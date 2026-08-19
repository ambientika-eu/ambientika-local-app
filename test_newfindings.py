#!/usr/bin/env python3
"""Verify the NEW findings from the adversarial review, so the report
distinguishes CONFIRMED (reproduced) from merely plausible."""
import asyncio
import importlib
import pytest

m = importlib.import_module("ambientika_local_bridge")
from test_bridge import FakeMqtt, FakeWriter, add_device, make_bridge


def bridge_no_loop():
    b = m.LocalBridge(m.Config())
    b.mqtt = FakeMqtt()
    return b


# --- Finding A: dict mutated during iteration in _apply_protection ----------
def test_A_devices_mutated_during_protection_is_safe():
    """Fixed: iterating a snapshot means a device disconnecting mid-apply no
    longer raises; protection still commits."""
    async def run():
        b = make_bridge()
        d1, w1 = add_device(b, "AABBCCDDEE01", mode=1, speed=1)
        d2, w2 = add_device(b, "AABBCCDDEE02", mode=1, speed=1)
        d3, w3 = add_device(b, "AABBCCDDEE03", mode=1, speed=1)
        orig = b._send
        async def send_then_drop(dev, frame):
            if dev.serial == "AABBCCDDEE01" and "AABBCCDDEE02" in b.devices:
                await asyncio.sleep(0)
                b.devices.pop("AABBCCDDEE02", None)   # disconnect mid-iteration
            await orig(dev, frame)
        b._send = send_then_drop
        b.nc_manual_radon = True
        try:
            await b._neuracell_evaluate()
            return "no-error", b.protection
        except RuntimeError:
            return "RuntimeError", b.protection
    result, protection = asyncio.run(run())
    assert result == "no-error"         # no dictionary-changed-size crash
    assert protection == "RADON"        # committed -> protection is in force


# --- Finding C: non-dict weather payload breaks NeuraCell -------------------
def test_C_nondict_weather_is_rejected_no_crash():
    """Fixed: non-object weather JSON is rejected to {} so NeuraCell keeps
    running instead of crashing on .get()."""
    async def run():
        b = make_bridge()
        add_device(b)
        # a valid JSON array must NOT wedge the evaluator
        await b._handle_control(["ambientika", "weather"], "[1,2,3]")
        # a following evaluation must also be clean
        await b._neuracell_evaluate()
        return b.weather
    weather = asyncio.run(run())
    assert weather == {}                 # coerced to empty dict, no exception


# --- Finding B: no release hysteresis -> flapping around the threshold ------
def test_B_dewpoint_hysteresis_no_flapping():
    """Fixed: two-threshold hysteresis (±margin). Block only when clearly
    moister outside; release only when clearly drier. Noise in the band does
    not toggle the state."""
    b = bridge_no_loop()
    b.cfg.dewpoint_margin = 1.0
    indoor = 12.0        # block at outdoor>=13, release at outdoor<11
    # start unblocked; small noise inside the band keeps it False
    assert b._dewpoint_block(indoor, 12.4) is False
    assert b._dewpoint_block(indoor, 12.6) is False
    # cross the upper threshold -> block
    assert b._dewpoint_block(indoor, 13.2) is True
    # noise back inside the band must NOT release (hysteresis holds)
    assert b._dewpoint_block(indoor, 12.0) is True
    assert b._dewpoint_block(indoor, 11.5) is True
    # only a clear drop below the lower threshold releases
    assert b._dewpoint_block(indoor, 10.5) is False


def test_B_manual_dewpoint_toggle_without_weather_releases():
    """Manual block is a separate OR term: turning it on then off must release
    even when there is NO outdoor data to drive the auto latch."""
    b = bridge_no_loop()
    b.nc_manual_dewpoint = True
    assert b._dewpoint_block(12.0, None) is True    # manual on, no weather
    b.nc_manual_dewpoint = False
    assert b._dewpoint_block(12.0, None) is False   # manual off -> releases
    assert b._dew_blocked is False                  # auto latch never got stuck


# --- Finding G: encode_setup overflow on large house_id ---------------------
def test_G_encode_setup_clamps_no_overflow():
    """Fixed: house_id is clamped into the u32 range instead of raising."""
    f_hi = m.encode_setup("AABBCCDDEEFF", role=0, zone=0, house_id=2**32)
    assert len(f_hi) == 16
    assert f_hi[12:16] == (0xFFFFFFFF).to_bytes(4, "little")   # clamped to max
    f_lo = m.encode_setup("AABBCCDDEEFF", role=0, zone=0, house_id=-1)
    assert len(f_lo) == 16
    assert f_lo[12:16] == (0).to_bytes(4, "little")            # clamped to 0
    # a normal value is unaffected
    assert m.encode_setup("AABBCCDDEEFF", 0, 0, 1)[12:16] == (1).to_bytes(4, "little")


# --- Finding F: temperature is unsigned; negative temp wraps ----------------
def test_F_temperature_decoded_signed():
    """Fixed: temperature/rssi decoded as signed bytes; -5 C (0xFB) reads -5."""
    from test_bridge import build_status
    f = build_status(temp=251, hum=80, rssi=200)   # -5 C, rssi 200->-56 dBm
    d = m.decode_status(f)
    assert d["temperature"] == -5                   # signed, not 251
    assert d["rssi"] == -56                          # signed dBm
    # dew point is now a sensible sub-zero value, not garbage
    assert d["dewPoint"] is not None and -12 < d["dewPoint"] < 0
    # positive temps are unchanged
    assert m.decode_status(build_status(temp=22))["temperature"] == 22


# --- Finding E: writer swap allows opt-in setup to be re-sent ----------------
def test_E_reconnect_new_writer_preserves_state():
    """Fixed: reconnect swaps the writer on the SAME Device, preserving runtime
    state (raw_codes/last_status/firmware); only setup_sent resets so an
    explicitly enabled setup can be re-pushed once on the new transport."""
    b = bridge_no_loop()
    w1 = FakeWriter()
    dev1 = b._register("AABBCCDDEE09", w1)
    dev1.setup_sent = True
    dev1.last_status = {"x": 1}
    dev1.raw_codes = {"mode": 2, "speed": 1, "humidity": 1, "light": 1}
    w2 = FakeWriter()
    dev2 = b._register("AABBCCDDEE09", w2)     # different writer -> swap
    assert dev2 is dev1                        # same object, not replaced
    assert dev2.writer is w2                   # transport swapped
    assert dev2.setup_sent is False            # opt-in setup may be re-sent once
    assert dev2.last_status == {"x": 1}        # state PRESERVED
    assert dev2.raw_codes["mode"] == 2         # raw_codes preserved


# --- Finding L9: staleness failsafe on radon/weather ------------------------
def test_L9_stale_radon_value_is_ignored():
    import datetime as dt
    fixed = {"t": dt.datetime(2025, 1, 1, 12, 0)}
    b = m.LocalBridge(m.Config(), now_fn=lambda: fixed["t"])
    b.mqtt = FakeMqtt()
    b.cfg.nc_input_ttl = 300
    b.nc_radon_value = 500          # well above threshold
    b.nc_radon_ts = dt.datetime(2025, 1, 1, 12, 0)
    assert b._radon_active() is True            # fresh -> trips
    fixed["t"] = dt.datetime(2025, 1, 1, 12, 10)  # +600s, older than TTL
    assert b._radon_active() is False           # stale -> ignored (fail to normal)
    b.nc_manual_radon = True
    assert b._radon_active() is True            # manual override still works


def test_L9_stale_weather_disables_auto_dewpoint():
    import datetime as dt
    fixed = {"t": dt.datetime(2025, 1, 1, 12, 0)}
    b = m.LocalBridge(m.Config(), now_fn=lambda: fixed["t"])
    b.mqtt = FakeMqtt()
    b.cfg.nc_input_ttl = 300
    b.weather = {"temperature": 25, "humidity": 90}
    b.weather_ts = dt.datetime(2025, 1, 1, 12, 0)
    assert b._outdoor_dewpoint() is not None     # fresh
    fixed["t"] = dt.datetime(2025, 1, 1, 12, 10)  # stale
    assert b._outdoor_dewpoint() is None          # ignored


# --- Finding M5: neuracell state published only on change -------------------
def test_M5_neuracell_publish_gated_on_change():
    async def run():
        b = make_bridge()
        add_device(b)
        await b._neuracell_evaluate()
        n1 = len([p for p in b.mqtt.pubs if "neuracell/state" in p[0][0]])
        await b._neuracell_evaluate()   # identical state
        n2 = len([p for p in b.mqtt.pubs if "neuracell/state" in p[0][0]])
        b.nc_manual_radon = True
        await b._neuracell_evaluate()   # state changes -> publish
        n3 = len([p for p in b.mqtt.pubs if "neuracell/state" in p[0][0]])
        return n1, n2, n3
    n1, n2, n3 = asyncio.run(run())
    assert n1 == 1            # first publish
    assert n2 == 1            # no duplicate publish for unchanged state
    assert n3 == 2            # publish again only when it changed


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
