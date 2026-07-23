#!/usr/bin/env python3
"""Comprehensive error-hunting test suite for ambientika_local_bridge.py.

Goal is NOT to prove it works, but to find where it breaks. Tests are grouped:
  A. wire codec (byte-exactness, round-trips, malformed input)
  B. mappings / dew point / pct<->level
  C. schedule helpers + scheduler edge-trigger
  D. NeuraCell-X priority / restore / suppression
  E. concurrency & framing defects (the interesting ones)
Run:  python3 -m pytest test_bridge.py -v
"""
import asyncio
import datetime as dt
import importlib
import math

import pytest

m = importlib.import_module("ambientika_local_bridge")


# ---------------------------------------------------------------- fakes
class FakeWriter:
    def __init__(self):
        self.frames = []
        self.closed = False
    def write(self, b):
        self.frames.append(bytes(b))
    async def drain(self):
        pass
    def get_extra_info(self, k):
        return ("test", 0)
    def close(self):
        self.closed = True
    async def wait_closed(self):
        pass


class FakeMqtt:
    def __init__(self):
        self.pubs = []
    def publish(self, *a, **k):
        self.pubs.append((a, k))
    def username_pw_set(self, *a):
        pass
    def connect(self, *a, **k):
        pass
    def loop_start(self):
        pass
    def subscribe(self, *a, **k):
        pass


def make_bridge(**envless):
    cfg = m.Config()
    for k, v in envless.items():
        setattr(cfg, k, v)
    b = m.LocalBridge(cfg)
    b.mqtt = FakeMqtt()
    b.loop = asyncio.get_event_loop()
    return b


def build_status(serial="AABBCCDDEEFF", mode=0, speed=0, hum_lvl=1, temp=20,
                 hum=50, aq_raw=1, hum_alarm=0, filt=0, night=0, role=0,
                 last_mode=0, light=1, rssi=200):
    mac = bytes.fromhex(serial)
    return bytes([0x01, 0x00]) + mac + bytes([
        mode, speed, hum_lvl, temp, hum, aq_raw, hum_alarm, filt, night,
        role, last_mode, light, rssi])


def add_device(b, serial="AABBCCDDEEFF", mode=0, speed=0):
    w = FakeWriter()
    dev = m.Device(serial=serial, writer=w)
    dev.raw_codes = {"mode": mode, "speed": speed, "humidity": 1, "light": 1}
    dev.last_status = {"dewPoint": 10.0}
    b.devices[serial] = dev
    return dev, w


# ================================================================ A. CODEC
def test_status_frame_len_and_decode():
    f = build_status(mode=3, speed=2, temp=21, hum=48, filt=2)
    assert len(f) == 21
    d = m.decode_status(f)
    assert d["serial"] == "AABBCCDDEEFF"
    assert d["mode"] == "NIGHT"          # proto 3 -> app NIGHT
    assert d["fanSpeed"] == 100          # level 2 -> 100%
    assert d["temperature"] == 21
    assert d["humidity"] == 48
    assert d["filterAlarm"] is True      # filt==2


def test_mode_command_bytes_exact():
    f = m.encode_mode_command("AABBCCDDEEFF", 8, 0, 1, 1)
    assert f == bytes([0x02, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF,
                       0x01, 0x08, 0x00, 0x01, 0x01])
    assert len(f) == 13


def test_filter_reset_bytes_exact():
    f = m.encode_filter_reset("AABBCCDDEEFF")
    assert f == bytes([0x02, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x03])
    assert len(f) == 9


def test_setup_bytes_exact():
    f = m.encode_setup("AABBCCDDEEFF", role=0, zone=0, house_id=1)
    assert len(f) == 16
    assert f[:9] == bytes([0x02, 0x00, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00])
    assert f[12:16] == (1).to_bytes(4, "little")


def test_serial_roundtrip():
    f = build_status(serial="0011223344FF")
    assert m.decode_status(f)["serial"] == "0011223344FF"


def test_decode_status_short_buffer_raises():
    # A truncated status frame should not silently produce garbage.
    with pytest.raises(IndexError):
        m.decode_status(bytes([0x01, 0x00, 0xAA]))


# =========================================================== B. MAPPINGS
def test_mode_mapping_bijective():
    for name, code in m.APP_MODE_TO_PROTO.items():
        assert m.PROTO_TO_APP_MODE[code] == name


def test_level_pct_roundtrip_low_med_high():
    for lvl in (0, 1, 2):
        pct = m.LEVEL_TO_PCT[lvl]
        assert m.pct_to_level(pct) == lvl, f"level {lvl} pct {pct}"


def test_night_level_is_one_way_by_design():
    # NIGHT (level 3) is mode-linked, not a slider position. By design a user
    # %-command never yields NIGHT (only LOW/MED/HIGH), and NIGHT is never lost:
    # decode_status still exposes it verbatim in the 'fanLevel' string.
    for pct in range(0, 101):
        assert m.pct_to_level(pct) in (0, 1, 2)      # never 3
    f = build_status(speed=3)                        # a NIGHT-speed status
    assert m.decode_status(f)["fanLevel"] == "NIGHT"  # info preserved


def test_dew_point_reference_values():
    assert abs(m.dew_point(20, 50) - 9.26) < 0.05
    assert abs(m.dew_point(30, 80) - 26.16) < 0.1
    assert abs(m.dew_point(0, 100) - 0.0) < 0.05


def test_dew_point_invalid():
    assert m.dew_point(20, 0) is None      # rh<=0
    assert m.dew_point(20, 150) is None    # rh>100
    assert m.dew_point("x", 50) is None
    assert m.dew_point(20, None) is None


def test_mode_to_code_never_none_with_int_default():
    assert m.mode_to_code("BOGUS", 5) == 5
    assert m.mode_to_code(None, 5) == 5
    assert m.mode_to_code("BOOST", 5) == 6


def test_mode_to_code_none_default_can_return_none():
    # latent: if default is None and value unknown -> None (would crash encoder)
    assert m.mode_to_code("BOGUS", None) is None
    assert m.speed_to_code("BOGUS", None) is None


# =========================================================== C. SCHEDULE
def test_active_slot_basic():
    slots = [{"start": "08:00", "end": "12:00", "mode": "ECO"}]
    assert m.active_slot(slots, "09:00")["mode"] == "ECO"
    assert m.active_slot(slots, "12:00") is None       # end exclusive
    assert m.active_slot(slots, "07:59") is None


def test_unpadded_time_is_normalized_and_matches():
    # normalize_week now zero-pads times so lexicographic compare is correct.
    week = m.normalize_week({"mon": [{"start": "9:00", "end": "17:00", "mode": "ECO"}]})
    slot = week["mon"][0]
    assert slot["start"] == "09:00" and slot["end"] == "17:00"
    assert m.active_slot(week["mon"], "10:30")["mode"] == "ECO"   # now matches
    assert m.active_slot(week["mon"], "08:59") is None


def test_normalize_week_filters_bad_slots():
    payload = {"mon": [{"start": "08:00", "end": "12:00"}, {"start": "x"}],
               "xxx": [{"start": "1", "end": "2"}]}
    w = m.normalize_week(payload)
    assert len(w["mon"]) == 1
    assert "xxx" not in w


def test_scheduler_edge_trigger_applies_once():
    async def run():
        b = make_bridge()
        dev, w = add_device(b)
        b.schedules["AABBCCDDEEFF"] = {
            "mon": [{"start": "08:00", "end": "12:00", "mode": "ECO", "fanSpeed": 70}]}
        monday_9 = dt.datetime(2025, 6, 2, 9, 0)   # a Monday
        await b._scheduler_tick(monday_9)
        assert len(w.frames) == 1                  # applied once
        await b._scheduler_tick(dt.datetime(2025, 6, 2, 9, 30))
        assert len(w.frames) == 1                  # NOT re-applied inside slot
        # move to a time outside the slot then back -> new edge
        await b._scheduler_tick(dt.datetime(2025, 6, 2, 13, 0))
        await b._scheduler_tick(dt.datetime(2025, 6, 2, 9, 0))
        assert len(w.frames) == 2
    asyncio.run(run())


def test_scheduler_suspended_during_protection():
    async def run():
        b = make_bridge()
        dev, w = add_device(b)
        b.protection = "RADON"
        b.schedules["AABBCCDDEEFF"] = {
            "mon": [{"start": "08:00", "end": "12:00", "mode": "ECO"}]}
        await b._scheduler_tick(dt.datetime(2025, 6, 2, 9, 0))
        assert len(w.frames) == 0                  # suspended
    asyncio.run(run())


# ========================================================= D. NEURACELL
def test_radon_priority_over_dewpoint():
    async def run():
        b = make_bridge()
        dev, w = add_device(b, mode=1, speed=1)
        b.nc_manual_radon = True
        b.nc_manual_dewpoint = True
        await b._neuracell_evaluate()
        assert b.protection == "RADON"
        # radon target = INTAKE(8)/LOW(0)
        last = w.frames[-1]
        assert last[9] == b.cfg.radon_protect_mode
        assert last[10] == b.cfg.radon_protect_fan
    asyncio.run(run())


def test_exact_restore_after_clear():
    async def run():
        b = make_bridge()
        dev, w = add_device(b, mode=2, speed=1)   # HRV / MEDIUM baseline
        b.nc_manual_radon = True
        await b._neuracell_evaluate()             # -> RADON
        assert b.protection == "RADON"
        b.nc_manual_radon = False
        await b._neuracell_evaluate()             # -> NONE, restore
        assert b.protection == "NONE"
        last = w.frames[-1]
        assert last[9] == 2 and last[10] == 1     # restored HRV/MEDIUM
        assert b.baseline == {}
    asyncio.run(run())


def test_command_suppressed_during_protection():
    async def run():
        b = make_bridge()
        dev, w = add_device(b)
        b.protection = "RADON"
        await b._handle_command("AABBCCDDEEFF", {"mode": "BOOST"})
        assert len(w.frames) == 0                  # suppressed
        # but filter reset is allowed through
        await b._handle_command("AABBCCDDEEFF", {"resetFilter": True})
        assert len(w.frames) == 1
    asyncio.run(run())


def test_baseline_uses_normal_target_not_protective_echo():
    """Fixed: the restore baseline comes from the last *normal* target
    (self.normal_codes), so a fast radon re-trip while the device still echoes
    the protective codes restores the correct pre-protection mode."""
    async def run():
        b = make_bridge()
        dev, w = add_device(b, mode=2, speed=1)   # true normal HRV/MEDIUM
        b.normal_codes["AABBCCDDEEFF"] = (2, 1)   # as set from an unprotected status
        b.nc_manual_radon = True
        await b._neuracell_evaluate()             # RADON, baseline=(2,1)
        b.nc_manual_radon = False
        await b._neuracell_evaluate()             # restore to (2,1)
        # device has NOT yet reported back -> raw_codes still protective:
        dev.raw_codes = {"mode": b.cfg.radon_protect_mode,
                         "speed": b.cfg.radon_protect_fan, "humidity": 1, "light": 1}
        b.nc_manual_radon = True
        await b._neuracell_evaluate()             # RADON again, baseline still (2,1)
        b.nc_manual_radon = False
        await b._neuracell_evaluate()             # restore -> CORRECT (2,1)
        last = w.frames[-1]
        return last[9], last[10]
    assert asyncio.run(run()) == (2, 1)            # correct restore, race fixed


# ============================================ E. CONCURRENCY / FRAMING
def test_protection_committed_before_writes():
    """Fixed: self.protection is committed BEFORE any protective write, so
    every send happens with suppression already in force."""
    async def run():
        b = make_bridge()
        d1, w1 = add_device(b, "AABBCCDDEE01", mode=1, speed=1)
        d2, w2 = add_device(b, "AABBCCDDEE02", mode=1, speed=1)
        seen = []
        orig = b._send
        async def spy(dev, frame):
            seen.append(b.protection)   # protection value at send time
            await orig(dev, frame)
        b._send = spy
        b.nc_manual_radon = True
        await b._neuracell_evaluate()   # NONE -> RADON
        return seen
    seen = asyncio.run(run())
    assert seen and all(s == "RADON" for s in seen)   # suppression active throughout


def test_reentrant_evaluate_single_apply_under_backpressure():
    """Fixed: the global lock serialises evaluates, so even with a suspending
    write two concurrent evaluations apply the protection exactly once."""
    async def run():
        b = make_bridge()
        d1, w1 = add_device(b, "AABBCCDDEE01", mode=1, speed=1)
        orig = b._send
        async def slow_send(dev, frame):
            await asyncio.sleep(0)     # emulate writer.drain() suspending
            await orig(dev, frame)
        b._send = slow_send
        b.nc_manual_radon = True
        await asyncio.gather(b._neuracell_evaluate(), b._neuracell_evaluate())
        return len(w1.frames)
    n = asyncio.run(run())
    assert n == 1   # exactly one protective command -> no re-entrant double-apply


def _frame_loop(buf):
    """Mirror of the FIXED inner framing loop in _handle_conn (drop-1-and-resync)."""
    decoded = 0
    while buf:
        b0 = buf[0]
        if b0 == 0x01:
            if len(buf) < 21:
                break
            buf = buf[21:]; decoded += 1
        elif b0 == 0x03:
            if len(buf) < 18:
                break
            buf = buf[18:]
        else:
            buf = buf[1:]      # resync: drop the stray byte
    return decoded, len(buf)


def test_frame_parser_resyncs_after_unknown_byte():
    decoded, remaining = _frame_loop(bytes([0x7F]) + build_status())
    assert decoded == 1        # the valid status behind the junk IS recovered
    assert remaining == 0


def test_frame_parser_ok_without_junk():
    decoded, remaining = _frame_loop(build_status() + build_status())
    assert decoded == 2 and remaining == 0


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
