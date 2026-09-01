#!/usr/bin/env python3
"""
Regression tests for the Ambientika wire codec and the write policies.

The centrepiece is ``SAMPLE_LEGACY``: the real 19-byte status frame reported in
upstream issue #5 by a unit running radio firmware 0.0.11. Every field assertion
against it is a claim about the layout that a hardware test can confirm or
refute — which is the point, since the layout is reverse-engineered.

Run with:  python -m unittest -v
"""

from __future__ import annotations

import unittest

from ambientika_protocol import (
    Calibration, NO_CALIBRATION, STATUS_LEN_LEGACY, STATUS_LEN_MODERN,
    decode_firmware, decode_status, dew_point, parse_calibration,
    probe_status_len, resolve_status_len, status_len_for_firmware,
    status_raw_codes, status_role_code, encode_mode_command, encode_setup,
)
from ambientika_policy import (
    SETUP_SEND, SETUP_SKIP_DISABLED, SETUP_SKIP_NOT_LISTED,
    SETUP_SKIP_ROLE_CONFLICT, command_is_noop, parse_serial_list,
    parse_setup_devices, serial_allowed, setup_decision,
)

# The exact payload from issue #5, radio firmware 0.0.11.
#   01 00 | 1C 9D C2 43 04 44 | 03 01 01 1B 35 00 00 00 01 00 03
SAMPLE_LEGACY = bytes.fromhex("01001c9dc243044403010 11b3500000001 0003".replace(" ", ""))

# The same reading as a 21-byte frame: light sensor "OFF" (1) and RSSI -58 dBm.
SAMPLE_MODERN = SAMPLE_LEGACY + bytes([0x01, 0xC6])


class TestSampleIntegrity(unittest.TestCase):
    def test_lengths(self):
        self.assertEqual(len(SAMPLE_LEGACY), STATUS_LEN_LEGACY)
        self.assertEqual(len(SAMPLE_MODERN), STATUS_LEN_MODERN)


class TestLegacyFrameDecoding(unittest.TestCase):
    """The 19-byte layout is the 21-byte layout without light sensor and RSSI."""

    def setUp(self):
        self.d = decode_status(SAMPLE_LEGACY)

    def test_serial(self):
        self.assertEqual(self.d["serial"], "1C9DC2430444")

    def test_readings_are_plausible(self):
        self.assertEqual(self.d["temperature"], 27)
        self.assertEqual(self.d["humidity"], 53)

    def test_mode_block_is_self_consistent(self):
        # Mode NIGHT, night alarm raised, last mode NIGHT — a coherent picture,
        # which is the main evidence that the offsets are right.
        self.assertEqual(self.d["modeProtocol"], "NIGHT")
        self.assertTrue(self.d["nightAlarm"])
        self.assertEqual(self.d["lastMode"], "NIGHT")

    def test_role_and_alarms(self):
        self.assertEqual(self.d["deviceRole"], "MASTER")
        self.assertEqual(self.d["fanLevel"], "MEDIUM")
        self.assertEqual(self.d["humidityLevel"], "NORMAL")
        self.assertFalse(self.d["humidityAlarm"])
        self.assertEqual(self.d["filterStatus"], "GOOD")
        self.assertFalse(self.d["filterAlarm"])

    def test_voc_sensor_not_ready_is_not_reported_as_very_good(self):
        # Raw 0 means "no reading yet". Publishing that as VERY_GOOD would let
        # an automation act on a measurement that does not exist.
        self.assertEqual(self.d["airQualityLabel"], "UNKNOWN_SENSOR")

    def test_absent_fields_are_absent_not_invented(self):
        self.assertEqual(self.d["lightSensor"], "NOT_AVAILABLE")
        self.assertIsNone(self.d["rssi"])

    def test_frame_length_is_reported(self):
        self.assertEqual(self.d["statusFrameLength"], STATUS_LEN_LEGACY)


class TestModernFrameDecoding(unittest.TestCase):
    def test_shared_fields_match_the_legacy_frame(self):
        legacy = decode_status(SAMPLE_LEGACY)
        modern = decode_status(SAMPLE_MODERN)
        for key in ("serial", "mode", "fanSpeed", "temperature", "humidity",
                    "humidityLevel", "filterStatus", "nightAlarm",
                    "deviceRole", "lastMode", "dewPoint"):
            self.assertEqual(legacy[key], modern[key], f"field {key} diverged")

    def test_tail_fields_are_decoded(self):
        d = decode_status(SAMPLE_MODERN)
        self.assertEqual(d["lightSensor"], "OFF")
        self.assertEqual(d["rssi"], -58)      # signed decoding

    def test_unsupported_length_raises(self):
        with self.assertRaises(ValueError):
            decode_status(SAMPLE_LEGACY[:17])


class TestFrameLengthResolution(unittest.TestCase):
    def test_firmware_decides_legacy(self):
        self.assertEqual(status_len_for_firmware("0.0.11"), STATUS_LEN_LEGACY)

    def test_firmware_decides_modern(self):
        self.assertEqual(status_len_for_firmware("0.0.28"), STATUS_LEN_MODERN)
        self.assertEqual(status_len_for_firmware("0.1.22"), STATUS_LEN_MODERN)

    def test_firmware_in_the_unconfirmed_gap_stays_undecided(self):
        # 0.0.20 is neither known-legacy nor known-modern: refuse to guess.
        self.assertIsNone(status_len_for_firmware("0.0.20"))

    def test_unparseable_firmware_stays_undecided(self):
        self.assertIsNone(status_len_for_firmware("weird"))
        self.assertIsNone(status_len_for_firmware(None))

    def test_probe_detects_a_following_frame(self):
        stream = SAMPLE_LEGACY + SAMPLE_LEGACY
        self.assertEqual(probe_status_len(stream), STATUS_LEN_LEGACY)

    def test_probe_detects_a_following_firmware_frame(self):
        stream = SAMPLE_LEGACY + bytes([0x03, 0x00]) + b"\x00" * 16
        self.assertEqual(probe_status_len(stream), STATUS_LEN_LEGACY)

    def test_probe_accepts_a_modern_tail(self):
        self.assertEqual(probe_status_len(SAMPLE_MODERN), STATUS_LEN_MODERN)

    def test_probe_waits_when_there_is_not_enough_data(self):
        self.assertIsNone(probe_status_len(SAMPLE_LEGACY))

    def test_resolve_prefers_firmware_over_the_probe(self):
        # A legacy device whose stream happens to look modern must still be
        # read as legacy, because the firmware is the stronger evidence.
        stream = SAMPLE_LEGACY + bytes([0x02, 0xC6])
        self.assertEqual(resolve_status_len(stream, radio_fw="0.0.11"),
                         STATUS_LEN_LEGACY)

    def test_resolve_waits_rather_than_guessing(self):
        self.assertIsNone(resolve_status_len(SAMPLE_LEGACY[:12]))

    def test_forced_override_wins(self):
        self.assertEqual(
            resolve_status_len(SAMPLE_MODERN, radio_fw="0.0.28",
                               forced=STATUS_LEN_LEGACY),
            STATUS_LEN_LEGACY)


class TestRawCodes(unittest.TestCase):
    def test_legacy_light_code_falls_back_without_inventing_a_sensor(self):
        codes = status_raw_codes(SAMPLE_LEGACY)
        self.assertEqual(codes, {"mode": 3, "speed": 1, "humidity": 1, "light": 1})

    def test_modern_light_code_comes_from_the_frame(self):
        self.assertEqual(status_raw_codes(SAMPLE_MODERN)["light"], 1)

    def test_role_code(self):
        self.assertEqual(status_role_code(SAMPLE_LEGACY), 0)


class TestCalibration(unittest.TestCase):
    def test_offsets_are_applied_and_raw_values_kept(self):
        cal = Calibration(temp=-3.5, rh=6.0)
        d = decode_status(SAMPLE_LEGACY, cal)
        self.assertEqual(d["temperature"], 23.5)
        self.assertEqual(d["humidity"], 59.0)
        self.assertEqual(d["temperatureRaw"], 27)
        self.assertEqual(d["humidityRaw"], 53)

    def test_dew_point_uses_the_corrected_values(self):
        cal = Calibration(temp=-3.5, rh=6.0)
        d = decode_status(SAMPLE_LEGACY, cal)
        self.assertAlmostEqual(d["dewPoint"], dew_point(23.5, 59.0), places=2)

    def test_humidity_is_clamped_to_a_possible_reading(self):
        d = decode_status(SAMPLE_LEGACY, Calibration(rh=90.0))
        self.assertEqual(d["humidity"], 100.0)

    def test_no_calibration_leaves_the_payload_clean(self):
        d = decode_status(SAMPLE_LEGACY, NO_CALIBRATION)
        self.assertNotIn("temperatureRaw", d)
        self.assertNotIn("calibration", d)

    def test_no_calibration_preserves_the_integer_type(self):
        # Publishing 27.0 where the bridge previously published 27 would be a
        # silent payload change for every existing installation.
        d = decode_status(SAMPLE_LEGACY, NO_CALIBRATION)
        self.assertIsInstance(d["temperature"], int)
        self.assertIsInstance(d["humidity"], int)

    def test_calibrated_values_may_be_fractional(self):
        d = decode_status(SAMPLE_LEGACY, Calibration(temp=-3.5))
        self.assertIsInstance(d["temperature"], float)
        self.assertEqual(d["temperature"], 23.5)

    def test_parsing_from_json(self):
        cals = parse_calibration('{"1c9dc2430444": {"temp": -3.2, "rh": 6}}')
        self.assertIn("1C9DC2430444", cals)
        self.assertEqual(cals["1C9DC2430444"].temp, -3.2)

    def test_bad_json_does_not_raise(self):
        self.assertEqual(parse_calibration("{not json"), {})
        self.assertEqual(parse_calibration(None), {})


class TestSetupGating(unittest.TestCase):
    """The regression that matters most for multi-unit installations."""

    def setUp(self):
        self.targets = parse_setup_devices(
            '{"1C9DC2430444": {"role": 0, "zone": 2, "house": 7}}')

    def test_disabled_by_default_means_nothing_is_written(self):
        action, target, _ = setup_decision(
            "1C9DC2430444", enabled=False, targets=self.targets,
            reported_role=0, already_sent=False)
        self.assertEqual(action, SETUP_SKIP_DISABLED)
        self.assertIsNone(target)

    def test_a_unit_not_listed_is_left_alone(self):
        action, _, _ = setup_decision(
            "AABBCCDDEEFF", enabled=True, targets=self.targets,
            reported_role=1, already_sent=False)
        self.assertEqual(action, SETUP_SKIP_NOT_LISTED)

    def test_a_slave_is_not_silently_promoted_to_master(self):
        action, _, reason = setup_decision(
            "1C9DC2430444", enabled=True, targets=self.targets,
            reported_role=2, already_sent=False)
        self.assertEqual(action, SETUP_SKIP_ROLE_CONFLICT)
        self.assertIn("refusing", reason)

    def test_an_explicit_role_change_is_allowed(self):
        action, target, _ = setup_decision(
            "1C9DC2430444", enabled=True, targets=self.targets,
            reported_role=2, already_sent=False, allow_role_change=True)
        self.assertEqual(action, SETUP_SEND)
        self.assertEqual(target.role, 0)

    def test_matching_role_is_sent_with_the_configured_zone_and_house(self):
        action, target, _ = setup_decision(
            "1C9DC2430444", enabled=True, targets=self.targets,
            reported_role=0, already_sent=False)
        self.assertEqual(action, SETUP_SEND)
        self.assertEqual((target.role, target.zone, target.house), (0, 2, 7))

    def test_serial_matching_is_case_insensitive(self):
        action, _, _ = setup_decision(
            "1c9dc2430444", enabled=True, targets=self.targets,
            reported_role=0, already_sent=False)
        self.assertEqual(action, SETUP_SEND)

    def test_encoded_setup_carries_the_configured_house_id(self):
        frame = encode_setup("1C9DC2430444", role=0, zone=2, house_id=7)
        self.assertEqual(frame[:2], bytes([0x02, 0x00]))
        self.assertEqual(frame[2:8].hex().upper(), "1C9DC2430444")
        self.assertEqual(frame[9], 0)          # role
        self.assertEqual(frame[10], 2)         # zone
        self.assertEqual(int.from_bytes(frame[12:16], "little"), 7)


class TestNoOpSuppression(unittest.TestCase):
    """Each accepted command makes the unit beep — so do not send pointless ones."""

    def setUp(self):
        self.current = status_raw_codes(SAMPLE_LEGACY)

    def test_identical_command_is_a_noop(self):
        self.assertTrue(command_is_noop(self.current, 3, 1, 1, 1))

    def test_a_changed_mode_is_not_a_noop(self):
        self.assertFalse(command_is_noop(self.current, 8, 1, 1, 1))

    def test_a_changed_fan_level_is_not_a_noop(self):
        self.assertFalse(command_is_noop(self.current, 3, 2, 1, 1))

    def test_a_changed_humidity_level_is_not_a_noop(self):
        self.assertFalse(command_is_noop(self.current, 3, 1, 2, 1))

    def test_unknown_current_state_never_suppresses(self):
        self.assertFalse(command_is_noop({}, 3, 1, 1, 1))

    def test_command_encoding_is_unchanged(self):
        frame = encode_mode_command("1C9DC2430444", 3, 1, 1, 1)
        self.assertEqual(frame,
                         bytes.fromhex("02001c9dc2430444010301 0101".replace(" ", "")))


class TestSerialAllowlist(unittest.TestCase):
    def test_empty_allowlist_accepts_everything(self):
        self.assertTrue(serial_allowed("1C9DC2430444", set()))

    def test_listed_serial_is_accepted(self):
        allow = parse_serial_list("1c9dc2430444, AABBCCDDEEFF")
        self.assertTrue(serial_allowed("1C9DC2430444", allow))

    def test_unlisted_serial_is_rejected(self):
        allow = parse_serial_list("AABBCCDDEEFF")
        self.assertFalse(serial_allowed("1C9DC2430444", allow))


class TestFirmwareFrame(unittest.TestCase):
    def test_decode(self):
        frame = bytes([0x03, 0x00]) + bytes.fromhex("1C9DC2430444") + bytes(
            [0, 0, 11, 0, 0, 11, 1, 1, 3, 0])
        d = decode_firmware(frame)
        self.assertEqual(d["radioFw"], "0.0.11")
        self.assertEqual(d["microFw"], "0.0.11")
        self.assertEqual(d["radioAtFw"], "1.1.3.0")
        # And the firmware alone must be enough to pick the layout.
        self.assertEqual(status_len_for_firmware(d["radioFw"]), STATUS_LEN_LEGACY)


if __name__ == "__main__":
    unittest.main(verbosity=2)
