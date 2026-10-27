#!/usr/bin/env python3
"""Tests for the safety fixes of the local bridge (Oct. 2026):

  S. setup frames: opt-in, per serial, never change a reported role silently
  F. status frames: 19-byte legacy layout, 21-byte unchanged, firmware/probe
  V. only confirmed wire codes are written
  U. unknown frame types from a unit are logged, never answered

Run:  python3 -m pytest test_safety_fixes.py -v
"""
import asyncio
import logging

import pytest

import ambientika_local_bridge as m
from test_bridge import FakeMqtt, FakeWriter, build_status

MASTER, SLAVE_EQ, SLAVE_OPP = 0, 1, 2


class FakeReader:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        return self._chunks.pop(0) if self._chunks else b""


def make_bridge(**cfg_overrides):
    cfg = m.Config()
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    b = m.LocalBridge(cfg)
    b.mqtt = FakeMqtt()
    b.loop = None          # MQTT callbacks are not exercised here
    return b


def setups(frames):
    return [f for f in frames if len(f) == 15 and f[:2] == b"\x02\x00" and f[8] == 0x00]


def modes(frames):
    return [f for f in frames if len(f) == 13 and f[:2] == b"\x02\x00" and f[8] == 0x01]


def legacy_status(serial="AABBCCDDEEFF", mode=0, speed=1, temp=20, hum=50, role=0):
    """19-byte status of radio firmware 0.0.11 (no light sensor, no RSSI)."""
    return build_status(serial=serial, mode=mode, speed=speed, temp=temp,
                        hum=hum, role=role)[:19]


def firmware_frame(serial="AABBCCDDEEFF", radio=(0, 0, 11)):
    mac = bytes.fromhex(serial)
    return bytes([0x03, 0x00]) + mac + bytes(radio) + bytes([1, 2, 3]) + bytes([1, 0, 0, 0])


def run_conn(b, chunks, writer=None):
    w = writer or FakeWriter()
    asyncio.run(b._handle_conn(FakeReader(chunks), w))
    return w


def published_status(b, serial="AABBCCDDEEFF"):
    import json
    return [json.loads(p[0][1]) for p in b.mqtt.pubs if p[0][0].endswith(f"/{serial}/status")]


# ============================================================ S. SETUP
def test_S1_default_sends_no_setup():
    b = make_bridge()
    w = run_conn(b, [build_status(role=MASTER)])
    assert setups(w.frames) == []


def test_S2_listed_master_gets_its_own_target():
    b = make_bridge(setup_devices=m.parse_setup_devices(
        '{"aabbccddeeff": {"role": 0, "zone": 3, "house": 77}}'))
    w = run_conn(b, [build_status(role=MASTER)])
    (f,) = setups(w.frames)
    assert f == m.encode_setup("AABBCCDDEEFF", 0, 3, 77)


def test_S3_listed_target_would_flip_slave_to_master_is_refused():
    b = make_bridge(setup_devices=m.parse_setup_devices(
        '{"AABBCCDDEEFF": {"role": 0, "zone": 0, "house": 1}}'))
    w = run_conn(b, [build_status(role=SLAVE_OPP)])
    assert setups(w.frames) == []


def test_S4_role_change_only_with_explicit_permission():
    b = make_bridge(setup_allow_role_change=True, setup_devices=m.parse_setup_devices(
        '{"AABBCCDDEEFF": {"role": 0, "zone": 0, "house": 1}}'))
    w = run_conn(b, [build_status(role=SLAVE_OPP)])
    assert len(setups(w.frames)) == 1


def test_S5_legacy_global_setup_never_turns_slaves_into_masters():
    """The original defect: SEND_SETUP pushed role MASTER to every unit.
    (Other MASTERS would still get the global zone/house — hence the global
    setup is documented as single-unit only; see S9.)"""
    b = make_bridge(send_setup=True)              # explicit, old global default 0/0/1
    sent = {}
    for serial, role in (("111111111111", MASTER), ("222222222222", SLAVE_EQ),
                         ("333333333333", SLAVE_OPP)):
        w = run_conn(b, [build_status(serial=serial, role=role)])
        sent[serial] = len(setups(w.frames))
    assert sent == {"111111111111": 1, "222222222222": 0, "333333333333": 0}


def test_S6_reconnect_decides_again_and_stays_protected():
    b = make_bridge(send_setup=True)
    w1 = run_conn(b, [build_status(role=SLAVE_EQ)])
    w2 = run_conn(b, [build_status(role=SLAVE_EQ)])
    assert setups(w1.frames) == [] and setups(w2.frames) == []


@pytest.mark.parametrize("raw", [
    "not json",
    '{"AABBCCDDEEFF": {"role": 9, "zone": 0, "house": 1}}',      # invalid role
    '{"AABBCCDDEEFF": {"role": "0", "zone": 0, "house": 1}}',    # not an integer
    '{"AABBCCDDEEFF": {"role": true, "zone": 0, "house": 1}}',   # bool is not a role
    '{"AABBCCDDEEFF": {"role": 0.0, "zone": 0, "house": 1}}',    # float
    '{"AABBCCDDEEFF": {"zone": 1, "house": 1}}',                 # role is mandatory
    '{"AABBCCDDEEFF": {"role": 0, "house": 1}}',                 # zone is mandatory
    '{"AABBCCDDEEFF": {"role": 0, "zone": 0}}',                  # house is mandatory
    '{"AABBCCDDEEFF": {"role": 0, "zone": 16, "house": 1}}',     # zone is 4 bits
    '{"AABBCCDDEEFF": {"role": 0, "zone": 0, "house": -1}}',     # house out of range
    '{"AA:BB:CC:DD:EE:FF": {"role": 0, "zone": 0, "house": 1}}', # not 12 hex chars
    '{"AABBCCDDEEFF": null}', '{"AABBCCDDEEFF": []}',
])
def test_S7_setup_devices_parsing_is_defensive(raw):
    assert m.parse_setup_devices(raw) == {}


def test_S7b_setup_devices_valid_entry():
    got = m.parse_setup_devices('{" aabbccddeeff ": {"role": 2, "zone": 1, "house": 12048}}')
    assert got == {"AABBCCDDEEFF": m.SetupTarget(2, 1, 12048)}
    # same serial twice (case differs): the first entry wins, the second is logged
    dup = m.parse_setup_devices('{"AABBCCDDEEFF": {"role": 0, "zone": 1, "house": 5},'
                                ' "aabbccddeeff": {"role": 2, "zone": 2, "house": 6}}')
    assert dup == {"AABBCCDDEEFF": m.SetupTarget(0, 1, 5)}


@pytest.mark.parametrize("over", [{"setup_role": 7}, {"setup_zone": 256},
                                  {"setup_house": -1}])
def test_S8_invalid_global_target_never_sent(over):
    b = make_bridge(send_setup=True, **over)
    w = run_conn(b, [build_status(role=MASTER)])
    assert setups(w.frames) == []


# ============================================================ F. FRAMES
def test_F1_modern_frame_decodes_exactly_as_before():
    f = build_status(mode=3, speed=2, temp=256 - 5, hum=48, light=2, rssi=200)
    d = m.decode_status(f)
    assert d["temperature"] == -5 and d["lightSensor"] == "LOW" and d["rssi"] == -56
    assert m.status_raw_codes(f) == {"mode": 3, "speed": 2, "humidity": 1, "light": 2}


def test_F2_legacy_frames_with_firmware_frame_first():
    b = make_bridge()
    stream = (firmware_frame(radio=(0, 0, 11))
              + legacy_status(temp=18, hum=40) + legacy_status(temp=19, hum=41))
    run_conn(b, [stream])
    st = published_status(b)
    assert [(s["temperature"], s["humidity"]) for s in st] == [(18, 40), (19, 41)]
    assert st[-1]["rssi"] is None and st[-1]["lightSensor"] == "NOT_AVAILABLE"
    assert st[-1]["radioFw"] == "0.0.11"


def test_F3_legacy_frames_without_firmware_frame_are_probed():
    b = make_bridge()
    stream = legacy_status(temp=18, hum=40) + legacy_status(temp=22, hum=55) \
        + legacy_status(temp=23, hum=56)
    run_conn(b, [stream])
    st = published_status(b)
    # proven on the first frame, kept for the connection: all three decoded
    assert [(s["temperature"], s["humidity"]) for s in st] == [(18, 40), (22, 55), (23, 56)]


def test_F4_legacy_frames_split_across_reads():
    b = make_bridge()
    data = firmware_frame(radio=(0, 0, 11)) + legacy_status(temp=17) + legacy_status(temp=21)
    chunks = [data[i:i + 5] for i in range(0, len(data), 5)]
    run_conn(b, chunks)
    assert [s["temperature"] for s in published_status(b)] == [17, 21]


def test_F5_modern_firmware_uses_21_bytes():
    b = make_bridge()
    run_conn(b, [firmware_frame(radio=(0, 0, 28)) + build_status(temp=24) + build_status(temp=25)])
    st = published_status(b)
    assert [s["temperature"] for s in st] == [24, 25] and st[0]["rssi"] == -56


def test_F6_without_proof_the_previous_21_byte_reading_is_kept():
    f = build_status(light=7, rssi=5)            # no legacy proof
    assert m.probe_status_len(f) == 21
    assert m.resolve_status_len(f) == 21
    # '01 00' at offset 19 alone is not proof: wait for the MAC, then decide
    g = build_status(light=1, rssi=0)
    assert m.probe_status_len(g) is None
    assert m.probe_status_len(g + build_status()) == 21


def test_F7_intermediate_firmware_is_probed():
    assert m.status_len_for_firmware("0.0.20") is None
    assert m.resolve_status_len(build_status(), "0.0.20") == 21
    assert m.status_len_for_firmware("0.0.11") == 19
    assert m.status_len_for_firmware("garbage") is None


def test_F8_legacy_command_echoes_default_light_code():
    raw = m.status_raw_codes(legacy_status(mode=0, speed=1))
    assert raw["light"] == m.LEGACY_LIGHT_CODE
    b, w = _bridge_with_device(raw=raw)
    asyncio.run(b._handle_command("AABBCCDDEEFF", {"mode": "NIGHT"}))
    (cmd,) = modes(w.frames)
    assert cmd[-1] == m.LEGACY_LIGHT_CODE


# ============================================================ V. VALIDATION
def _bridge_with_device(raw=None):
    b = make_bridge()
    w = FakeWriter()
    dev = m.Device("AABBCCDDEEFF", w)
    dev.raw_codes = raw or {"mode": 0, "speed": 1, "humidity": 1, "light": 1}
    dev.last_status = {"serial": dev.serial}         # unit has reported at least once
    b.devices[dev.serial] = dev
    return b, w


@pytest.mark.parametrize("payload", [{"mode": 99}, {"mode": "12"}, {"mode": 255},
                                     {"fanSpeed": "NIGHT", "mode": 42},
                                     {"humidityLevel": 9}, {"lightSensor": 4}])
def test_V1_unconfirmed_codes_are_refused(payload):
    b, w = _bridge_with_device()
    asyncio.run(b._handle_command("AABBCCDDEEFF", payload))
    assert modes(w.frames) == []


@pytest.mark.parametrize("payload,mode", [({"mode": "SMART"}, 0), ({"mode": "OFF"}, 11),
                                          ({"mode": 9}, 9), ({"mode": "INTAKE"}, 8)])
def test_V2_confirmed_codes_still_sent(payload, mode):
    b, w = _bridge_with_device()
    asyncio.run(b._handle_command("AABBCCDDEEFF", payload))
    (f,) = modes(w.frames)
    assert f[9] == mode


def test_V3_garbage_echo_from_device_is_replaced_not_written_back():
    b, w = _bridge_with_device(raw={"mode": 0, "speed": 1, "humidity": 7, "light": 9})
    asyncio.run(b._handle_command("AABBCCDDEEFF", {"mode": "NIGHT"}))
    (f,) = modes(w.frames)
    assert (f[9], f[11], f[12]) == (3, 1, m.LEGACY_LIGHT_CODE)   # NIGHT, NORMAL, OFF


def test_V3b_explicit_invalid_humidity_is_still_refused():
    b, w = _bridge_with_device()
    asyncio.run(b._handle_command("AABBCCDDEEFF", {"mode": "NIGHT", "humidityLevel": 7}))
    assert modes(w.frames) == []


@pytest.mark.parametrize("payload", [{"fanSpeed": float("nan")}, {"fanSpeed": float("inf")},
                                     ["SMART"], "SMART", None])
def test_V6_unreadable_payload_is_refused_without_crash(payload):
    b, w = _bridge_with_device()
    asyncio.run(b._handle_command("AABBCCDDEEFF", payload))
    assert modes(w.frames) == []


def test_V4_schedule_slot_with_invalid_mode_writes_nothing():
    import datetime as dt
    b, w = _bridge_with_device()
    now = dt.datetime(2026, 10, 26, 10, 0)                    # Monday
    b.schedules["AABBCCDDEEFF"] = {"mon": [{"start": "09:00", "end": "11:00", "mode": 42}]}
    asyncio.run(b._scheduler_tick(now))
    assert modes(w.frames) == []


def test_V5_invalid_radon_protect_config_falls_back_to_intake_low():
    b = make_bridge(radon_protect_mode=77, radon_protect_fan=9)
    assert (b.cfg.radon_protect_mode, b.cfg.radon_protect_fan) == (m.MODE_INTAKE, m.FAN_LOW)


# ============================================================ U. UNKNOWN FRAMES
def test_U1_unknown_frame_is_logged_never_answered(caplog):
    caplog.set_level(logging.INFO, logger="ambientika.local")
    b = make_bridge()
    unknown = bytes.fromhex("0500aabbccddeeff0a2d0000")
    w = run_conn(b, [unknown + build_status(temp=21)])
    assert [s["temperature"] for s in published_status(b)] == [21]
    assert w.frames == []                                     # nothing written at all
    assert any("unknown frame type 0x05" in r.message for r in caplog.records)


def test_F9_modern_frame_with_light1_rssi0_is_not_mistaken_for_legacy():
    """Review finding: light=1 + RSSI byte 0 looked like '01 00' at offset 19."""
    b = make_bridge()
    stream = build_status(light=1, rssi=0, temp=20) + build_status(light=1, rssi=0, temp=21) \
        + build_status(light=1, rssi=200, temp=22)
    chunks = [stream[i:i + 7] for i in range(0, len(stream), 7)]
    run_conn(b, chunks)
    st = published_status(b)
    assert [s["temperature"] for s in st] == [20, 21, 22]
    assert set(b.mqtt_serials if hasattr(b, "mqtt_serials") else
               [p[0][0].split("/")[1] for p in b.mqtt.pubs if p[0][0].endswith("/status")]) \
        == {"AABBCCDDEEFF"}                                   # no phantom unit


def test_F10_status_frame_needs_zero_second_byte():
    b = make_bridge()
    bad = bytes([0x01, 0x07]) + build_status()[2:]
    run_conn(b, [build_status(temp=20) + bad + build_status(temp=23)])
    assert [s["temperature"] for s in published_status(b)] == [20, 23]


# ============================================================ P. PROTECTION
def test_P1_radon_write_survives_unexpected_echo():
    b, w = _bridge_with_device(raw={"mode": 0, "speed": 1, "humidity": 1, "light": 4})
    asyncio.run(b._handle_control(["ambientika", "radon", "alarm"], "ON"))
    (f,) = modes(w.frames)
    assert (f[9], f[10], f[12]) == (m.MODE_INTAKE, m.FAN_LOW, m.LEGACY_LIGHT_CODE)


def test_P2_dewpoint_off_survives_unexpected_speed_echo():
    b, w = _bridge_with_device(raw={"mode": 0, "speed": 4, "humidity": 1, "light": 1})
    asyncio.run(b._handle_control(["ambientika", "dewpoint", "block"], "ON"))
    (f,) = modes(w.frames)
    assert (f[9], f[10]) == (m.MODE_OFF, m.FAN_LOW)


def test_P3_restore_uses_last_confirmed_state_not_garbage_echo():
    b, w = _bridge_with_device(raw={"mode": 6, "speed": 2, "humidity": 1, "light": 1})
    b.normal_codes["AABBCCDDEEFF"] = (6, 2)                   # last confirmed (BOOST/HIGH)
    b.devices["AABBCCDDEEFF"].raw_codes = {"mode": 12, "speed": 1, "humidity": 1, "light": 1}
    asyncio.run(b._handle_control(["ambientika", "dewpoint", "block"], "ON"))
    asyncio.run(b._handle_control(["ambientika", "dewpoint", "block"], "OFF"))
    off, restore = modes(w.frames)
    assert off[9] == m.MODE_OFF and (restore[9], restore[10]) == (6, 2)


def test_P4_unconfirmed_echo_is_not_learned_as_normal_state():
    b = make_bridge()
    run_conn(b, [build_status(mode=12, speed=1)])
    assert "AABBCCDDEEFF" not in b.normal_codes


def test_U2_status_like_bytes_inside_unknown_frame_create_no_phantom():
    """Review finding: an unknown frame whose payload contains 01 00 ... must not
    be parsed as a status of a different unit once the connection's unit is known."""
    b = make_bridge()
    first = build_status(temp=20)
    unknown = bytes.fromhex("0500") + bytes.fromhex("AA0100CCDDEE") + bytes(13)
    run_conn(b, [first + unknown + build_status(temp=24) + build_status(temp=25)])
    serials = {p[0][0].split("/")[1] for p in b.mqtt.pubs if p[0][0].endswith("/status")}
    assert serials == {"AABBCCDDEEFF"}
    assert [s["temperature"] for s in published_status(b)] == [20, 24, 25]


def test_U3_junk_before_first_frame_cannot_lock_out_the_real_unit():
    """Review pass 2 (R1): a phantom found while resyncing must not become the
    serial against which all real frames are rejected."""
    b = make_bridge()
    junk = bytes.fromhex("0700112233445501000000")
    real = firmware_frame(radio=(0, 0, 28)) + b"".join(
        build_status(temp=t) for t in (20, 21, 22, 23))
    run_conn(b, [junk + real])
    temps = [s["temperature"] for s in published_status(b)]
    assert temps[-3:] == [21, 22, 23]                       # real unit recovers


def test_P5_normal_state_learned_from_mode_and_speed_only():
    b = make_bridge()
    run_conn(b, [build_status(mode=8, speed=2, light=4)])
    assert b.normal_codes["AABBCCDDEEFF"] == (8, 2)


# ============================================================ review pass 3
def test_S10_setup_frame_uses_15_byte_cloud_format():
    f = m.encode_setup("AABBCCDDEEFF", role=2, zone=3, house_id=12048)
    assert f == bytes.fromhex("0200AABBCCDDEEFF" "00" "02" "03") + (12048).to_bytes(4, "little")


def test_S11_undocumented_reported_role_gets_no_setup():
    b = make_bridge(setup_allow_role_change=True, setup_devices=m.parse_setup_devices(
        '{"AABBCCDDEEFF": {"role": 0, "zone": 0, "house": 1}}'))
    w = run_conn(b, [build_status(role=0xFF)])
    assert setups(w.frames) == []


def test_U3b_phantom_split_across_reads_cannot_lock_out_the_real_unit():
    """Review pass 3: junk with '01 00 <other MAC>' whose read ends inside it."""
    b = make_bridge()
    junk = bytes.fromhex("0700112233445501000000")
    real = firmware_frame(radio=(0, 0, 28)) + b"".join(
        build_status(temp=t) for t in (20, 21, 22, 23, 24))
    data = junk + real
    for cut in range(1, 40):
        b = make_bridge()
        run_conn(b, [data[:cut], data[cut:]])
        temps = [s["temperature"] for s in published_status(b)]
        assert temps[-4:] == [21, 22, 23, 24], cut


def test_F11_master_pings_are_consumed_silently():
    b = make_bridge()
    ping = bytes.fromhex("0400AABBCCDDEEFF")
    w = run_conn(b, [build_status(temp=20) + ping + build_status(temp=21) + ping])
    assert [s["temperature"] for s in published_status(b)] == [20, 21]
    assert w.frames == []


def test_F12_legacy_proof_also_accepts_a_following_ping():
    b = make_bridge()
    ping = bytes.fromhex("0400AABBCCDDEEFF")
    run_conn(b, [legacy_status(temp=18) + ping + legacy_status(temp=19) + ping])
    assert [s["temperature"] for s in published_status(b)] == [18, 19]


def test_P6_no_baseline_before_first_status():
    b = make_bridge()
    dev = m.Device("AABBCCDDEEFF", FakeWriter())
    assert dev.last_status == {}
    assert b._baseline_for(dev) is None


def test_P7_protected_devices_lists_every_unit_under_protection():
    import json
    b, w = _bridge_with_device()
    asyncio.run(b._handle_control(["ambientika", "radon", "alarm"], "ON"))
    st = [json.loads(p[0][1]) for p in b.mqtt.pubs if p[0][0].endswith("neuracell/state")]
    assert st[-1]["protectedDevices"] == ["AABBCCDDEEFF"]


# ============================================================ review pass 4
def test_P8_unit_without_status_is_not_switched_off_and_restored_correctly():
    """Bridge restart on a humid day: unit B has only sent its firmware frame when
    the dew-point block trips. It must be protected on its first status and
    restored to its REAL state afterwards — never left OFF."""
    async def go():
        b = make_bridge()
        wa, wb = FakeWriter(), FakeWriter()
        a = m.Device("111111111111", wa); a.last_status = {"x": 1}
        a.raw_codes = {"mode": 0, "speed": 1, "humidity": 1, "light": 1}
        bdev = m.Device("222222222222", wb)                # firmware only, no status
        b.devices.update({a.serial: a, bdev.serial: bdev})
        await b._handle_control(["ambientika", "dewpoint", "block"], "ON")
        assert modes(wb.frames) == []                       # nothing blind to B
        # B's first status arrives (its real state: NIGHT / MEDIUM)
        bdev.last_status = {"x": 1}
        bdev.raw_codes = {"mode": 3, "speed": 1, "humidity": 1, "light": 1}
        b.normal_codes[bdev.serial] = (3, 1)
        await b._protect_new_device(bdev)
        await b._handle_control(["ambientika", "dewpoint", "block"], "OFF")
        return [(f[9], f[10]) for f in modes(wb.frames)]
    assert asyncio.run(go()) == [(m.MODE_OFF, 1), (3, 1)]


def test_U4_phantom_chain_after_resync_cannot_lock_out_the_real_unit():
    """Review pass 4: unknown frame containing '04 00 xx*6' followed by
    '01 00 <fake MAC>' at connection start."""
    junk = bytes.fromhex("07") + bytes.fromhex("0400" + "11" * 6)
    fake = bytes([0x01, 0x00]) + bytes.fromhex("99" * 6) + bytes(13)
    real = firmware_frame(radio=(0, 0, 28)) + b"".join(
        build_status(temp=t) for t in (20, 21, 22, 23, 24))
    for cut in (None, 3, 10, 20):
        b = make_bridge()
        data = junk + fake + real
        chunks = [data] if cut is None else [data[:cut], data[cut:]]
        run_conn(b, chunks)
        temps = [s["temperature"] for s in published_status(b)]
        assert temps[-4:] == [21, 22, 23, 24], cut


@pytest.mark.parametrize("payload", [{"humidityLevel": "\u00b2"}, {"lightSensor": "\u00b2"},
                                     {"mode": "\u00b2"}, {"fanSpeed": "\u00b2"}])
def test_V7_non_ascii_digits_do_not_raise(payload):
    b, w = _bridge_with_device()
    asyncio.run(b._handle_command("AABBCCDDEEFF", payload))      # must not raise
    for f in modes(w.frames):
        assert m.codes_valid(f[9], f[10], f[11], f[12])


def test_V8_unreadable_schedule_slot_does_not_abort_the_tick():
    import datetime as dt
    b, w = _bridge_with_device()
    w2 = FakeWriter(); d2 = m.Device("222222222222", w2); d2.last_status = {"x": 1}
    d2.raw_codes = {"mode": 0, "speed": 1, "humidity": 1, "light": 1}
    b.devices[d2.serial] = d2
    now = dt.datetime(2026, 10, 26, 10, 0)
    b.schedules["AABBCCDDEEFF"] = {"mon": [{"start": "09:00", "end": "11:00", "fanSpeed": float("nan")}]}
    b.schedules["222222222222"] = {"mon": [{"start": "09:00", "end": "11:00", "mode": "NIGHT"}]}
    asyncio.run(b._scheduler_tick(now))
    assert modes(w.frames) == [] and [f[9] for f in modes(w2.frames)] == [3]


def test_U5_ping_inside_phantom_chain_does_not_end_the_resync():
    """Review pass 5: unknown frame, ping, fake status, trailing junk, then the real unit."""
    junk = bytes.fromhex("070009") + bytes.fromhex("0400" + "11" * 6)
    fake = bytes([0x01, 0x00]) + bytes.fromhex("99" * 6) + bytes(13) + bytes.fromhex("0705")
    real = b"".join(build_status(temp=t) for t in (20, 21, 22, 23, 24))
    b = make_bridge()
    run_conn(b, [junk + fake + real])
    temps = [s["temperature"] for s in published_status(b, "AABBCCDDEEFF")]
    assert temps[-4:] == [21, 22, 23, 24]


def test_V9_schedule_times_with_non_ascii_digits_do_not_raise():
    week = m.normalize_week({"mon": [{"start": "\u00b2:00", "end": "11:00", "mode": "SMART"}]})
    assert week["mon"][0]["start"] == "\u00b2:00"          # left untouched, no exception
