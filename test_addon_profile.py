#!/usr/bin/env python3
"""Home Assistant add-on profile of the local bridge (HA_DISCOVERY_PROFILE=addon).

Checks that the local bridge offers the units with the add-on's discovery topics,
unique_ids, state keys and value names, and that per-attribute commands reach the
unit as one complete mode frame.
Run:  python3 -m pytest test_addon_profile.py -v
"""
import asyncio
import importlib
import json

import pytest

m = importlib.import_module("ambientika_local_bridge")

SER = "3030F9CD6C80"


class FakeWriter:
    def __init__(self):
        self.frames = []

    def write(self, b):
        self.frames.append(bytes(b))

    async def drain(self):
        pass

    def get_extra_info(self, k):
        return ("test", 0)

    def close(self):
        pass

    async def wait_closed(self):
        pass


class FakeMqtt:
    def __init__(self):
        self.pubs = []
        self.subs = []

    def publish(self, topic, payload=None, retain=False, **k):
        self.pubs.append((topic, payload, retain))

    def subscribe(self, topic, *a, **k):
        self.subs.append(topic)

    def topics(self):
        return [t for t, _, _ in self.pubs]

    def last(self, topic):
        for t, p, _ in reversed(self.pubs):
            if t == topic:
                return p
        return None


class Msg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode()


def frame(serial=SER, mode=0, speed=0, hum_lvl=1, temp=20, hum=50, aq_raw=2,
          hum_alarm=0, filt=0, night=0, role=0, last_mode=0, light=2, rssi=200,
          legacy=False):
    tail = [mode, speed, hum_lvl, temp & 0xFF, hum, aq_raw, hum_alarm, filt, night,
            role, last_mode]
    if not legacy:
        tail += [light, rssi]
    return bytes([0x01, 0x00]) + bytes.fromhex(serial) + bytes(tail)


def make_bridge(profile="addon", **cfgkw):
    cfg = m.Config()
    cfg.ha_discovery_profile = profile
    cfg.state_file = ""
    for k, v in cfgkw.items():
        setattr(cfg, k, v)
    b = m.LocalBridge(cfg)
    b.mqtt = FakeMqtt()
    return b


def add_device(b, serial=SER, mode=0, speed=0, humidity=1, light=2):
    w = FakeWriter()
    dev = m.Device(serial=serial, writer=w)
    dev.raw_codes = {"mode": mode, "speed": speed, "humidity": humidity, "light": light}
    dev.last_status = {"dewPoint": 10.0}
    b.devices[serial] = dev
    return dev, w


def mode_frames(w):
    return [f for f in w.frames if len(f) == 13 and f[8] == 0x01]


@pytest.fixture(autouse=True)
def fast_coalesce(monkeypatch):
    monkeypatch.setattr(m, "ADDON_COALESCE_S", 0.01)


# ------------------------------------------------------------ vocabulary
def test_fields_add_on_vocabulary():
    d = m.addon_fields_from_frame(frame(mode=4, speed=1, hum_lvl=2, temp=21, hum=58,
                                        aq_raw=4, hum_alarm=1, filt=1, night=1, role=2,
                                        last_mode=8, light=3))
    assert d["operating_mode"] == "AwayHome" and d["operating_mode_raw"] == "AwayHome"
    assert d["fan_speed"] == "Medium"
    assert d["humidity_level"] == "Moist"
    assert d["light_sensor_level"] == "Medium"
    assert d["temperature"] == 21 and d["humidity"] == 58
    assert d["air_quality"] == "Poor" and d["air_quality_num"] == 1
    assert d["humidity_alarm"] is True and d["night_alarm"] is True
    assert d["filters_status"] == "Medium" and d["filter_status_num"] == 1
    assert d["device_role"] == "SlaveOppositeMaster"
    assert d["last_operating_mode"] == "Intake" and d["last_operating_mode_num"] == 8
    assert d["operating_mode_num"] == 4
    assert d["fan_speed_num"] == 2               # add-on: Low/Medium/High -> 1/2/3
    assert d["humidity_level_num"] == 2 and d["light_sensor_level_num"] == 3


@pytest.mark.parametrize("code,name", list(m.ADDON_MODE_NAMES.items()))
def test_every_mode_name(code, name):
    assert m.addon_fields_from_frame(frame(mode=code))["operating_mode"] == name


def test_night_fan_and_air_quality_scale():
    d = m.addon_fields_from_frame(frame(speed=3, aq_raw=1))
    assert d["fan_speed"] == "Night" and d["fan_speed_num"] == 4
    assert d["air_quality"] == "VeryGood" and d["air_quality_num"] == 4
    d = m.addon_fields_from_frame(frame(aq_raw=5))
    assert d["air_quality"] == "Bad" and d["air_quality_num"] == 0
    d = m.addon_fields_from_frame(frame(aq_raw=0))        # sensor not ready
    assert d["air_quality"] is None and d["air_quality_num"] is None


def test_negative_temperature_and_legacy_frame():
    d = m.addon_fields_from_frame(frame(temp=-4, legacy=True))
    assert d["temperature"] == -4
    assert d["light_sensor_level"] == "NotAvailable" and d["light_sensor_level_num"] == 0


def test_unknown_codes_do_not_crash():
    d = m.addon_fields_from_frame(frame(mode=42, filt=9, role=17))
    assert d["operating_mode"] == "Unknown_42" and d["operating_mode_num"] is None
    assert d["filters_status"] == "Unknown_9" and d["filter_status_num"] is None
    assert d["device_role"] == "Unknown_17"


def test_short_frame_raises():
    with pytest.raises(ValueError):
        m.addon_fields_from_frame(frame()[:15])


# ------------------------------------------------------------ commands
@pytest.mark.parametrize("attr,raw,expect", [
    ("operating_mode", "AwayHome", {"mode": 4}),
    ("operating_mode", "ManualHeatRecovery", {"mode": 2}),
    ("operating_mode", "intake", {"mode": 8}),
    ("operating_mode", "Off", {"mode": 11}),
    ("operating_mode", '"Smart"', {"mode": 0}),
    ("operating_mode", "AWAY_HOME", {"mode": 4}),
    ("fan_speed", "High", {"fanSpeed": "HIGH"}),
    ("fan_speed", "Low", {"fanSpeed": "LOW"}),
    ("fan_speed", "Night", {"fanSpeed": "LOW"}),
    ("fan_speed", "Turbo", {"fanSpeed": "HIGH"}),
    ("humidity_level", "Moist", {"humidityLevel": "MOIST"}),
    ("light_sensor_level", "Off", {"lightSensor": "OFF"}),
    ("light_sensor_level", "NotAvailable", {"lightSensor": "NOT_AVAILABLE"}),
    ("reset_filter", "PRESS", {"resetFilter": True}),
])
def test_parse_command(attr, raw, expect):
    assert m.parse_addon_command(attr, raw) == expect


@pytest.mark.parametrize("attr,raw", [
    ("operating_mode", "Party"), ("operating_mode", "99"), ("operating_mode", ""),
    ("fan_speed", "75"), ("humidity_level", "Wet"), ("bogus", "x"),
])
def test_parse_command_refuses(attr, raw):
    assert m.parse_addon_command(attr, raw) is None


def test_mode_then_fan_is_one_frame():
    b = make_bridge()
    _, w = add_device(b, mode=0, speed=0)

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "AwayHome")
        await b._handle_addon_command(SER, "fan_speed", "High")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    f = mode_frames(w)
    assert len(f) == 1
    assert f[0][9] == 4 and f[0][10] == 2          # mode AwayHome, speed High
    assert f[0][11] == 1 and f[0][12] == 2         # humidity / light echoed


def test_second_command_does_not_undo_first():
    """The unit has not reported the first command yet - the second must build on it."""
    b = make_bridge()
    _, w = add_device(b, mode=0, speed=1)

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Off")
        await asyncio.sleep(0.05)
        await b._handle_addon_command(SER, "fan_speed", "High")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    f = mode_frames(w)
    assert [(x[9], x[10]) for x in f] == [(11, 1), (11, 2)]


def test_overlay_ends_when_unit_reports():
    b = make_bridge()
    dev, w = add_device(b, mode=0, speed=1)

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Off")
        await asyncio.sleep(0.05)
        st = frame(mode=11, speed=1)
        dev.raw_codes = m.status_raw_codes(st)
        b._publish_addon_state(dev, st)              # unit confirms Off
        assert SER not in b._addon_sent
        dev.raw_codes = m.status_raw_codes(frame(mode=0, speed=1))  # remote: back to Smart
        await b._handle_addon_command(SER, "fan_speed", "Low")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert [(x[9], x[10]) for x in mode_frames(w)] == [(11, 1), (0, 0)]


def test_overlay_expires(monkeypatch):
    t = [1000.0]
    b = make_bridge()
    b.mono_fn = lambda: t[0]
    _, w = add_device(b, mode=0, speed=1)

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Off")
        await asyncio.sleep(0.05)
        t[0] += m.ADDON_OVERLAY_S + 1
        await b._handle_addon_command(SER, "fan_speed", "High")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert [(x[9], x[10]) for x in mode_frames(w)] == [(11, 1), (0, 2)]


def test_mode_change_from_night_speed_uses_last_regular_level():
    b = make_bridge()
    dev, w = add_device(b, mode=0, speed=2)
    b._publish_addon_state(dev, frame(mode=0, speed=2))      # remembers High
    dev.raw_codes = m.status_raw_codes(frame(mode=3, speed=3))

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Smart")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert [(x[9], x[10]) for x in mode_frames(w)] == [(0, 2)]


def test_switching_on_from_off_keeps_light_setting():
    b = make_bridge()
    dev, w = add_device(b)
    st = frame(mode=0, light=3)
    dev.raw_codes = m.status_raw_codes(st)
    b._publish_addon_state(dev, st)                        # light Medium while running
    st = frame(mode=11, light=1)                           # Off: unit reports a default
    dev.raw_codes = m.status_raw_codes(st)
    b._publish_addon_state(dev, st)

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Smart")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    f = mode_frames(w)
    assert f[-1][9] == 0 and f[-1][12] == 3


def test_command_suppressed_during_protection():
    b = make_bridge()
    _, w = add_device(b)
    b.protection = "RADON"

    async def run():
        await b._handle_addon_command(SER, "operating_mode", "Off")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert mode_frames(w) == []
    assert SER not in b._addon_sent


def test_reset_filter_button():
    b = make_bridge()
    _, w = add_device(b)
    asyncio.run(b._handle_addon_command(SER, "reset_filter", "PRESS"))
    assert w.frames == [m.encode_filter_reset(SER)]


def test_unknown_unit_is_ignored():
    b = make_bridge()

    async def run():
        await b._handle_addon_command("AABBCCDDEEFF", "operating_mode", "Off")
        await asyncio.sleep(0.05)
    asyncio.run(run())                                     # no exception


# ------------------------------------------------------------ MQTT dispatch
def test_dispatch_add_on_topic():
    b = make_bridge()
    _, w = add_device(b)

    async def run():
        b.loop = asyncio.get_running_loop()
        b._on_mqtt_message(None, None, Msg(f"ambientika/{SER}/set/operating_mode", "Intake"))
        await asyncio.sleep(0.1)
    asyncio.run(run())
    assert [(x[9], x[10]) for x in mode_frames(w)] == [(8, 0)]


def test_dispatch_ignored_with_local_profile():
    b = make_bridge(profile="local")
    _, w = add_device(b)

    async def run():
        b.loop = asyncio.get_running_loop()
        b._on_mqtt_message(None, None, Msg(f"ambientika/{SER}/set/operating_mode", "Intake"))
        await asyncio.sleep(0.1)
    asyncio.run(run())
    assert w.frames == []


def test_local_topics_unchanged_with_add_on_profile():
    b = make_bridge()
    _, w = add_device(b)

    async def run():
        b.loop = asyncio.get_running_loop()
        b._on_mqtt_message(None, None, Msg(f"ambientika/{SER}/set", '{"mode": "OFF"}'))
        b._on_mqtt_message(None, None, Msg(
            f"ambientika/{SER}/schedule/set", '{"monday": []}'))
        await asyncio.sleep(0.1)
    asyncio.run(run())
    assert [(x[9]) for x in mode_frames(w)] == [11]
    assert SER in b.schedules


def test_subscription_and_static_discovery():
    b = make_bridge()
    client = FakeMqtt()
    b.mqtt = client
    b._on_mqtt_connect(client, None, None, 0)
    assert "ambientika/+/set/+" in client.subs
    assert client.last("ambientika/bridge/availability") == "online"
    topics = client.topics()
    assert "homeassistant/binary_sensor/ambientika_bridge/config" in topics
    assert "homeassistant/binary_sensor/neuracell_radon_protection/config" in topics
    nc = json.loads(client.last("homeassistant/sensor/neuracell_dewpoint_indoor/config"))
    assert nc["state_topic"] == "ambientika/neuracell/addon_state"
    assert nc["unique_id"] == "neuracell_dewpoint_indoor"


def test_local_profile_subscribes_as_before():
    b = make_bridge(profile="local")
    client = FakeMqtt()
    b._on_mqtt_connect(client, None, None, 0)
    assert "ambientika/+/set/+" not in client.subs


def test_unknown_profile_falls_back_to_local():
    b = make_bridge(profile="cloud")
    assert b.addon is False and b.cfg.ha_discovery_profile == "local"


def test_profile_switches_discovery_on():
    b = make_bridge(ha_discovery=False)
    assert b.cfg.ha_discovery is True


# ------------------------------------------------------------ state / discovery
def test_state_discovery_and_diagnostics():
    b = make_bridge(addon_device_names={SER: "Büro"})
    dev, _ = add_device(b)
    st = frame(mode=0, speed=1, temp=22, hum=55)
    dev.raw_codes = m.status_raw_codes(st)
    b._publish_addon_state(dev, st)
    mq = b.mqtt
    state = json.loads(mq.last(f"ambientika/{SER}/state"))
    assert state["operating_mode"] == "Smart" and state["fan_speed"] == "Medium"
    assert state["temperature_avg"] == 22.0 and state["humidity_avg"] == 55.0
    assert state["zone_index"] is None
    cfg = json.loads(mq.last(f"homeassistant/select/{SER}_operating_mode/config"))
    assert cfg["unique_id"] == f"ambientika_{SER}_operating_mode_select"
    assert cfg["command_topic"] == f"ambientika/{SER}/set/operating_mode"
    assert cfg["device"]["name"] == "Büro" and cfg["device"]["identifiers"] == [SER]
    assert "AwayHome" in cfg["options"] and "ManualHeatRecovery" in cfg["options"]
    diag = json.loads(mq.last(f"ambientika/{SER}/diagnostics"))
    assert diag["last_status"] and diag["poll_failures_total"] == 0
    n_disc = mq.topics().count(f"homeassistant/select/{SER}_operating_mode/config")
    b._publish_addon_state(dev, st)                       # second status: no new discovery
    assert mq.topics().count(f"homeassistant/select/{SER}_operating_mode/config") == n_disc
    for t, _, retain in mq.pubs:
        assert retain, t


def test_averages_over_window():
    t = [0.0]
    b = make_bridge(average_window_min=1)
    b.mono_fn = lambda: t[0]
    dev, _ = add_device(b)
    for temp, at in ((20, 0), (22, 30), (24, 59)):
        t[0] = at
        b._publish_addon_state(dev, frame(temp=temp, hum=50))
    s = json.loads(b.mqtt.last(f"ambientika/{SER}/state"))
    assert s["temperature_avg"] == 22.0
    t[0] = 125                                            # older samples leave the window
    b._publish_addon_state(dev, frame(temp=30, hum=40))
    s = json.loads(b.mqtt.last(f"ambientika/{SER}/state"))
    assert s["temperature_avg"] == 30.0 and s["humidity_avg"] == 40.0


def test_zone_from_setup_devices():
    b = make_bridge(setup_devices=m.parse_setup_devices(
        json.dumps({SER: {"role": 0, "zone": 3, "house": 7}})))
    dev, _ = add_device(b)
    b._publish_addon_state(dev, frame())
    assert json.loads(b.mqtt.last(f"ambientika/{SER}/state"))["zone_index"] == 3


def test_disconnect_counts_as_failed_poll():
    b = make_bridge()
    dev, _ = add_device(b)
    b._publish_addon_state(dev, frame())
    b._addon_note_disconnect(SER)
    b._addon_note_disconnect(SER)
    d = json.loads(b.mqtt.last(f"ambientika/{SER}/diagnostics"))
    assert d["poll_failures_total"] == 2 and d["poll_failures_consecutive"] == 2
    assert d["last_outage_start"] and d["last_outage_end"] is None
    b._publish_addon_state(dev, frame())
    d = json.loads(b.mqtt.last(f"ambientika/{SER}/diagnostics"))
    assert d["poll_failures_consecutive"] == 0 and d["last_outage_end"]
    assert d["poll_failures_total"] == 2


def test_local_profile_publishes_no_add_on_state():
    b = make_bridge(profile="local", ha_discovery=True)
    dev, _ = add_device(b)

    async def run():
        r = asyncio.StreamReader()
        r.feed_data(frame())
        r.feed_eof()
        await b._handle_conn(r, FakeWriter())
    asyncio.run(run())
    topics = b.mqtt.topics()
    assert f"ambientika/{SER}/status" in topics
    assert f"ambientika/{SER}/state" not in topics


def test_tcp_status_publishes_add_on_state_and_offline_diag():
    b = make_bridge()

    async def run():
        r = asyncio.StreamReader()
        r.feed_data(frame(mode=8, speed=2))
        r.feed_eof()
        await b._handle_conn(r, FakeWriter())
    asyncio.run(run())
    mq = b.mqtt
    assert json.loads(mq.last(f"ambientika/{SER}/state"))["operating_mode"] == "Intake"
    assert json.loads(mq.last(f"ambientika/{SER}/status"))["mode"] is not None
    assert mq.last(f"ambientika/{SER}/availability") == "offline"
    assert json.loads(mq.last(f"ambientika/{SER}/diagnostics"))["poll_failures_total"] == 1


def test_neuracell_add_on_state():
    b = make_bridge()
    b._publish_addon_neuracell(True, True, 11.26, 9.04)
    s = json.loads(b.mqtt.last("ambientika/neuracell/addon_state"))
    assert s == {"radon_protection": True, "radon": None, "radon_meter_connected": None,
                 "dewpoint_controller_connected": None, "dewpoint_block": False,
                 "dewpoint_fallback": False, "indoor_dew_point": 11.3,
                 "outdoor_dew_point": 9.0}
    n = len(b.mqtt.pubs)
    b._publish_addon_neuracell(True, True, 11.26, 9.04)   # unchanged: not again
    assert len(b.mqtt.pubs) == n


def test_device_names_parser():
    assert m.parse_device_names('{"30:30:f9:cd:6c:80": "Büro"}') == {SER: "Büro"}
    assert m.parse_device_names("not json") == {}
    assert m.parse_device_names('["x"]') == {}
    assert m.parse_device_names("") == {}


def test_last_status_is_throttled():
    t = [0.0]
    b = make_bridge()
    b.mono_fn = lambda: t[0]
    dev, _ = add_device(b)
    topic = f"ambientika/{SER}/diagnostics"
    b._publish_addon_state(dev, frame())
    n = b.mqtt.topics().count(topic)
    t[0] = 10
    b._publish_addon_state(dev, frame())
    assert b.mqtt.topics().count(topic) == n            # within 30 s: not again
    t[0] = 31
    b._publish_addon_state(dev, frame())
    assert b.mqtt.topics().count(topic) == n + 1
    b._addon_note_disconnect(SER)                       # outage: published at once
    t[0] = 32
    b._publish_addon_state(dev, frame())                # end of outage: at once
    d = json.loads(b.mqtt.last(topic))
    assert d["poll_failures_consecutive"] == 0 and d["last_outage_end"]


def test_local_neuracell_state_waits_for_entity_move():
    """Add-on profile: the local NeuraCell state (other format) only appears after the
    NeuraCell-X entities have been moved to their own topic."""
    b = make_bridge()

    async def run():
        b.loop = asyncio.get_running_loop()
        b._publish_neuracell(False, False, 10.0, 8.0)          # before MQTT connect
        assert b.mqtt.last("ambientika/neuracell/state") is None
        b._publish_addon_static()                              # entities moved
        b._publish_neuracell(False, False, 10.0, 8.0)
        assert b.mqtt.last("ambientika/neuracell/state") is None
        await asyncio.sleep(m.ADDON_NC_HOLD_S + 0.3)
    asyncio.run(run())
    topics = b.mqtt.topics()
    assert topics.index("homeassistant/sensor/neuracell_radon_level/config") < \
        topics.index("ambientika/neuracell/state")
    assert json.loads(b.mqtt.last("ambientika/neuracell/state"))["dewpoint"]["indoor"] == 10.0


def test_local_profile_neuracell_state_immediate():
    b = make_bridge(profile="local")
    b._publish_neuracell(False, False, 10.0, 8.0)
    assert b.mqtt.last("ambientika/neuracell/state") is not None
