#!/usr/bin/env python3
"""
Integration tests for the patched call sites in ``ambientika_local_bridge``.

The unit tests in ``test_protocol`` / ``test_framing`` / ``test_safety`` prove
the two new modules in isolation. These tests prove the *wiring*: that the
bridge actually asks the policy before writing, actually hands the frame length
to the reader, and actually applies the calibration — the mistakes that unit
tests on the modules cannot catch, because the modules are correct and the call
site is what would be wrong.

No broker, no hardware: MQTT is replaced by a recorder and the TCP streams are
in-memory fakes.

Run with:  python3 -m unittest test_bridge -v
"""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

import ambientika_local_bridge as bridge
from ambientika_policy import parse_serial_list, parse_setup_devices
from ambientika_protocol import (
    STATUS_LEN_LEGACY, STATUS_LEN_MODERN, dew_point, parse_calibration,
)

SN = "1C9DC2430444"
MAC = bytes.fromhex(SN)


# ---------------------------------------------------------------------------
# Frame builders — device -> server
# ---------------------------------------------------------------------------
def status_frame(mode=2, speed=1, hum_level=1, temp=27, rh=45, aq=2,
                 hum_alarm=0, filt=0, night=0, role=0, last_mode=2,
                 light=1, rssi=-60, legacy=False) -> bytes:
    body = [0x01, 0x00] + list(MAC) + [
        mode & 0xFF, speed & 0xFF, hum_level & 0xFF, temp & 0xFF, rh & 0xFF,
        aq & 0xFF, hum_alarm & 0xFF, filt & 0xFF, night & 0xFF, role & 0xFF,
        last_mode & 0xFF,
    ]
    if not legacy:
        body += [light & 0xFF, rssi & 0xFF]
    frame = bytes(body)
    assert len(frame) == (STATUS_LEN_LEGACY if legacy else STATUS_LEN_MODERN)
    return frame


def firmware_frame(radio=(0, 0, 11), micro=(0, 0, 11)) -> bytes:
    frame = bytes([0x03, 0x00] + list(MAC) + list(radio) + list(micro) + [0, 0, 0, 0])
    assert len(frame) == 18
    return frame


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class RecordingMqtt:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload=None, retain=False):
        self.published.append((topic, payload))

    def username_pw_set(self, *a, **kw):
        pass

    def will_set(self, *a, **kw):
        pass

    def topic_payload(self, needle):
        for topic, payload in self.published:
            if needle in topic:
                return json.loads(payload)
        return None


class FakeReader:
    """Serves a fixed list of chunks, then EOF."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class FakeWriter:
    def __init__(self):
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(bytes(data))

    async def drain(self):
        pass

    def get_extra_info(self, _key):
        return ("192.0.2.10", 51233)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def make_bridge(**cfg_kwargs) -> bridge.LocalBridge:
    """A bridge with a recording MQTT client and an explicit config.

    The settings are passed as ``Config`` arguments rather than environment
    variables on purpose: the plain ``os.getenv`` defaults on the dataclass are
    read once when the module is imported, which is right for a process that
    starts fresh and wrong for a test that wants two different settings in the
    same interpreter. The env plumbing itself is covered by
    :class:`TestConfigReadsTheEnvironment`.
    """
    cfg = bridge.Config(**cfg_kwargs)
    with mock.patch.object(bridge.LocalBridge, "_make_mqtt_client",
                           return_value=RecordingMqtt()):
        b = bridge.LocalBridge(cfg)
    b.cfg.neuracell_enabled = False        # keep the tests to one subject
    return b


def run_conn(b: bridge.LocalBridge, chunks) -> FakeWriter:
    writer = FakeWriter()
    asyncio.run(b._handle_conn(FakeReader(chunks), writer))
    return writer


# ---------------------------------------------------------------------------
class TestFrameLengthReachesTheReader(unittest.TestCase):
    """The whole point of #5: a 19-byte unit must decode correctly."""

    def test_legacy_unit_decodes_without_phantom_devices(self):
        b = make_bridge()
        frames = firmware_frame(radio=(0, 0, 11)) + status_frame(legacy=True) * 5
        run_conn(b, [frames])
        # One device, not a phantom herd.
        self.assertEqual(list(b.devices) if b.devices else [], [])   # gone after EOF
        published = [t for t, _ in b.mqtt.published if t.endswith("/status")]
        self.assertEqual(len(published), 5)
        self.assertTrue(all(SN in t for t in published))

    def test_legacy_unit_reports_absent_fields_as_absent(self):
        b = make_bridge()
        run_conn(b, [firmware_frame(radio=(0, 0, 11)) + status_frame(legacy=True)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertEqual(data["lightSensor"], "NOT_AVAILABLE")
        self.assertIsNone(data["rssi"])

    def test_modern_unit_still_decodes_light_and_rssi(self):
        b = make_bridge()
        run_conn(b, [firmware_frame(radio=(0, 0, 28)) + status_frame(rssi=-60)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertEqual(data["lightSensor"], "OFF")
        self.assertEqual(data["rssi"], -60)

    def test_a_chopped_stream_is_reassembled(self):
        b = make_bridge()
        blob = firmware_frame(radio=(0, 0, 28)) + status_frame()
        chunks = [blob[i:i + 7] for i in range(0, len(blob), 7)]
        run_conn(b, chunks)
        self.assertEqual(len([t for t, _ in b.mqtt.published if t.endswith("/status")]), 1)

    def test_override_wins_over_firmware(self):
        b = make_bridge(status_len_override=19)
        self.assertEqual(b.cfg.status_len_override, 19)
        run_conn(b, [status_frame(legacy=True)])
        self.assertIsNotNone(b.mqtt.topic_payload(f"{SN}/status"))


class TestSetupIsOptIn(unittest.TestCase):
    """The failure that would hit every multi-unit installation."""

    def test_nothing_is_sent_by_default(self):
        b = make_bridge()
        w = run_conn(b, [status_frame()])
        self.assertEqual(w.written, [], "the bridge wrote to a unit unbidden")

    def test_not_sent_to_a_unit_that_is_not_listed(self):
        b = make_bridge(send_setup=True)
        w = run_conn(b, [status_frame()])
        self.assertEqual(w.written, [])

    def test_sent_to_a_listed_unit_whose_role_matches(self):
        b = make_bridge(send_setup=True, setup_devices=parse_setup_devices(
            {SN: {"role": 0, "zone": 2, "house": 7}}))
        w = run_conn(b, [status_frame(role=0)])
        self.assertEqual(len(w.written), 1)
        frame = w.written[0]
        self.assertEqual(frame[0:2], b"\x02\x00")
        self.assertEqual(frame[8], 0x00)          # setup opcode
        self.assertEqual(frame[9], 0)             # role
        self.assertEqual(frame[10], 2)            # zone

    def test_refused_when_it_would_change_a_reported_role(self):
        b = make_bridge(send_setup=True, setup_devices=parse_setup_devices(
            {SN: {"role": 0, "zone": 0, "house": 1}}))
        w = run_conn(b, [status_frame(role=2)])   # unit says: slave, opposite master
        self.assertEqual(w.written, [],
                         "a slave was about to be promoted to master")

    def test_role_change_happens_only_when_explicitly_allowed(self):
        b = make_bridge(send_setup=True, setup_allow_role_change=True,
                        setup_devices=parse_setup_devices(
                            {SN: {"role": 0, "zone": 0, "house": 1}}))
        w = run_conn(b, [status_frame(role=2)])
        self.assertEqual(len(w.written), 1)

    def test_the_reported_role_is_read_from_the_frame(self):
        """The role must come from the unit, not from configuration.

        The device object is dropped when the connection closes, so the value
        is captured at publish time — the moment it is actually used.
        """
        b = make_bridge()
        gesehen = []
        with mock.patch.object(bridge.LocalBridge, "_publish_status",
                               side_effect=lambda dev: gesehen.append(dev.role_code)):
            run_conn(b, [status_frame(role=2)])
        self.assertEqual(gesehen, [2])

    def test_a_setup_frame_is_sent_once_per_connection(self):
        b = make_bridge(send_setup=True, setup_devices=parse_setup_devices(
            {SN: {"role": 0, "zone": 0, "house": 1}}))
        w = run_conn(b, [status_frame(role=0) * 4])
        self.assertEqual(len(w.written), 1, "one setup frame, not one per status")


class TestObservationMode(unittest.TestCase):
    """One gate, not a flag checked in five places."""

    def test_no_write_path_survives_observe_only(self):
        b = make_bridge(observe_only=True, send_setup=True,
                        setup_devices=parse_setup_devices(
                            {SN: {"role": 0, "zone": 0, "house": 1}}))
        writer = FakeWriter()
        asyncio.run(b._handle_conn(FakeReader([status_frame(role=0)]), writer))
        self.assertEqual(writer.written, [])

    def test_commands_are_refused_too(self):
        b = make_bridge(observe_only=True)
        writer = FakeWriter()
        dev = bridge.Device(serial=SN, writer=writer)
        b.devices[SN] = dev
        asyncio.run(b._handle_command(SN, {"mode": "NIGHT", "fanSpeed": 100}))
        self.assertEqual(writer.written, [])

    def test_filter_reset_is_refused_too(self):
        b = make_bridge(observe_only=True)
        writer = FakeWriter()
        b.devices[SN] = bridge.Device(serial=SN, writer=writer)
        asyncio.run(b._handle_command(SN, {"resetFilter": True}))
        self.assertEqual(writer.written, [])

    def test_reading_still_works(self):
        b = make_bridge(observe_only=True)
        run_conn(b, [status_frame(temp=21, rh=55)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertEqual(data["temperature"], 21)
        self.assertEqual(data["humidity"], 55)


class TestNoopSuppression(unittest.TestCase):
    """Every accepted command makes the unit beep."""

    def _device(self, b, **codes):
        writer = FakeWriter()
        dev = bridge.Device(serial=SN, writer=writer)
        dev.raw_codes = {"mode": 2, "speed": 1, "humidity": 1, "light": 1}
        dev.raw_codes.update(codes)
        b.devices[SN] = dev
        return dev, writer

    def test_a_repeat_of_the_current_state_is_dropped(self):
        b = make_bridge()
        _, writer = self._device(b)
        asyncio.run(b._handle_command(SN, {"mode": "HRV", "fanSpeed": 70}))
        self.assertEqual(writer.written, [])

    def test_a_real_change_still_goes_through(self):
        b = make_bridge()
        _, writer = self._device(b)
        asyncio.run(b._handle_command(SN, {"mode": "NIGHT", "fanSpeed": 70}))
        self.assertEqual(len(writer.written), 1)

    def test_suppression_can_be_switched_off(self):
        b = make_bridge(suppress_noop=False)
        _, writer = self._device(b)
        asyncio.run(b._handle_command(SN, {"mode": "HRV", "fanSpeed": 70}))
        self.assertEqual(len(writer.written), 1)

    def test_a_dropped_command_is_still_remembered_as_the_normal_target(self):
        # Otherwise a NeuraCell restore would fall back to a stale baseline.
        b = make_bridge()
        self._device(b)
        asyncio.run(b._handle_command(SN, {"mode": "HRV", "fanSpeed": 70}))
        self.assertEqual(b.normal_codes[SN], (2, 1))


class TestSerialAllowlist(unittest.TestCase):
    def test_empty_list_accepts_everything(self):
        b = make_bridge()
        run_conn(b, [status_frame()])
        self.assertIsNotNone(b.mqtt.topic_payload(f"{SN}/status"))

    def test_an_unlisted_unit_is_dropped_without_a_single_write(self):
        b = make_bridge(allowed_serials=parse_serial_list("AABBCCDDEEFF"))
        w = run_conn(b, [status_frame()])
        self.assertEqual(w.written, [])
        self.assertEqual([t for t, _ in b.mqtt.published if t.endswith("/status")], [])
        self.assertTrue(w.closed)

    def test_a_listed_unit_passes(self):
        b = make_bridge(allowed_serials=parse_serial_list(f"aabbccddeeff, {SN.lower()}"))
        run_conn(b, [status_frame()])
        self.assertIsNotNone(b.mqtt.topic_payload(f"{SN}/status"))


class TestCalibrationReachesTheDecoder(unittest.TestCase):
    def test_offset_is_applied_and_the_raw_value_kept(self):
        b = make_bridge(calibration=parse_calibration({SN: {"temp": -3.05, "rh": 12.5}}))
        run_conn(b, [status_frame(temp=27, rh=45)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertAlmostEqual(data["temperature"], 23.9, places=2)
        self.assertAlmostEqual(data["humidity"], 57.5, places=2)
        self.assertEqual(data["temperatureRaw"], 27)
        self.assertEqual(data["humidityRaw"], 45)

    def test_without_calibration_the_payload_is_unchanged(self):
        b = make_bridge()
        run_conn(b, [status_frame(temp=27, rh=45)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertEqual(data["temperature"], 27)
        self.assertNotIn("temperatureRaw", data)

    def test_the_dew_point_is_computed_from_the_corrected_values(self):
        b = make_bridge(calibration=parse_calibration({SN: {"temp": -3.05, "rh": 12.5}}))
        run_conn(b, [status_frame(temp=27, rh=45)])
        data = b.mqtt.topic_payload(f"{SN}/status")
        self.assertEqual(data["dewPoint"], dew_point(23.9, 57.5))


class TestImplausibleReadings(unittest.TestCase):
    def test_an_impossible_reading_is_reported_once(self):
        b = make_bridge()
        with self.assertLogs("ambientika.local", level="WARNING") as protokoll:
            run_conn(b, [status_frame(temp=200) * 4])
        treffer = [z for z in protokoll.output if "implausible" in z]
        self.assertEqual(len(treffer), 1, "one warning per device and field, not per frame")

    def test_a_normal_reading_is_silent(self):
        b = make_bridge()
        with mock.patch.object(bridge.log, "warning") as warn:
            run_conn(b, [status_frame(temp=21, rh=55)])
        self.assertEqual(
            [c for c in warn.call_args_list if "implausible" in str(c)], [])


class TestConfigReadsTheEnvironment(unittest.TestCase):
    """The three settings that are parsed per instance, not at import.

    ``setup_devices``, ``calibration`` and ``allowed_serials`` use a
    ``default_factory``, so they read the environment every time a ``Config`` is
    built. That is what makes a typo in one of them a startup-time problem
    rather than a silent one.
    """

    def test_setup_devices_comes_from_the_environment(self):
        with mock.patch.dict("os.environ", {
                "SETUP_DEVICES": json.dumps({SN: {"role": 1, "zone": 3, "house": 9}})}):
            cfg = bridge.Config()
        self.assertIn(SN, cfg.setup_devices)
        self.assertEqual(cfg.setup_devices[SN].zone, 3)
        self.assertEqual(cfg.setup_devices[SN].house, 9)

    def test_calibration_comes_from_the_environment(self):
        with mock.patch.dict("os.environ", {
                "CALIBRATION": json.dumps({SN: {"temp": -3.05, "rh": 12.5}})}):
            cfg = bridge.Config()
        self.assertAlmostEqual(cfg.calibration[SN].temp, -3.05)

    def test_allowed_serials_comes_from_the_environment(self):
        with mock.patch.dict("os.environ", {"ALLOWED_SERIALS": f"{SN.lower()} aabbccddeeff"}):
            cfg = bridge.Config()
        self.assertEqual(cfg.allowed_serials, {SN, "AABBCCDDEEFF"})

    def test_a_broken_entry_does_not_stop_the_bridge_from_starting(self):
        with mock.patch.dict("os.environ", {"SETUP_DEVICES": "{not json"}):
            cfg = bridge.Config()
        self.assertEqual(cfg.setup_devices, {})

    def test_the_shipped_defaults_write_to_nothing(self):
        """Whatever else changes, these three must stay as they are."""
        cfg = bridge.Config()
        self.assertFalse(cfg.send_setup, "setup frames must be opt-in")
        self.assertFalse(cfg.setup_allow_role_change, "role changes must be opt-in")
        self.assertEqual(cfg.setup_devices, {}, "no unit is configured by default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
