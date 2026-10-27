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
    return [f for f in frames if len(f) == 16 and f[:2] == b"\x02\x00" and f[8] == 0x00]


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
    '{"AABBCCDDEEFF": {"role": 9}}',                 # invalid role
    '{"AABBCCDDEEFF": {"role": "0"}}',               # not an integer
    '{"AABBCCDDEEFF": {"role": true}}',              # bool is not a role
    '{"AABBCCDDEEFF": {"role": 0.0}}',               # float
    '{"AABBCCDDEEFF": {"zone": 1}}',                 # role is mandatory
    '{"AABBCCDDEEFF": {"role": 0, "zone": 300}}',    # zone out of range
    '{"AABBCCDDEEFF": {"role": 0, "house": -1}}',    # house out of range
    '{"AA:BB:CC:DD:EE:FF": {"role": 0}}',            # not 12 hex chars
])
def test_S7_setup_devices_parsing_is_defensive(raw):
    assert m.parse_setup_devices(raw) == {}


def test_S7b_setup_devices_valid_entry():
    got = m.parse_setup_devices('{" aabbccddeeff ": {"role": 2, "zone": 1}}')
    assert got == {"AABBCCDDEEFF": m.SetupTarget(2, 1, 1)}


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


def test_V3_garbage_echo_from_device_is_not_written_back():
    b, w = _bridge_with_device(raw={"mode": 0, "speed": 1, "humidity": 7, "light": 1})
    asyncio.run(b._handle_command("AABBCCDDEEFF", {"mode": "NIGHT"}))
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
