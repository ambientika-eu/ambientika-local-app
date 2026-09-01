#!/usr/bin/env python3
"""
Safety tests for the pilot rollout.

The field mapping is going to be validated on a customer's installation rather
than in a lab. Two properties have to hold for that to be defensible:

* in observation mode the bridge cannot write to a unit, whatever the caller
  asks for;
* a frame that decoded into something physically impossible is flagged rather
  than published as a reading.

These tests exist to keep both properties from quietly regressing.

Run with:  python -m unittest test_safety -v
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from ambientika_protocol import (
    PLAUSIBLE_RSSI_DBM, decode_status, implausible_fields,
)
from ambientika_policy import write_refusal

SAMPLE_LEGACY = bytes.fromhex("01001c9dc2430444030101" + "1b" + "35" +
                              "00" + "00" + "00" + "01" + "00" + "03")
SAMPLE_MODERN = SAMPLE_LEGACY + bytes([0x01, 0xC6])


class TestObserveOnly(unittest.TestCase):
    def test_observation_mode_refuses_every_write(self):
        for what in ("command", "setup frame", "filter reset",
                     "radon protection", "schedule slot"):
            reason = write_refusal(True, what)
            self.assertIsNotNone(reason, f"{what} was not refused")
            self.assertIn("OBSERVE_ONLY", reason)
            self.assertIn(what, reason)

    def test_normal_mode_permits_writes(self):
        self.assertIsNone(write_refusal(False, "command"))


class TestPlausibility(unittest.TestCase):
    def test_a_good_frame_raises_nothing(self):
        self.assertEqual(implausible_fields(decode_status(SAMPLE_MODERN)), [])
        self.assertEqual(implausible_fields(decode_status(SAMPLE_LEGACY)), [])

    def test_the_old_misparse_signature_is_caught(self):
        # The fixed-21 bug produced light sensor 1 with an RSSI of exactly
        # 0 dBm, by reading the next frame's first two bytes. That is the
        # single most recognisable symptom, so it must not pass silently.
        bad = decode_status(SAMPLE_LEGACY + bytes([0x01, 0x00]))
        problems = implausible_fields(bad)
        self.assertTrue(any("rssi" in p for p in problems), problems)

    def test_impossible_temperature_is_caught(self):
        frame = bytearray(SAMPLE_MODERN)
        frame[11] = 0x64                      # +100 °C
        problems = implausible_fields(decode_status(bytes(frame)))
        self.assertTrue(any("temperature" in p for p in problems), problems)

    def test_impossible_humidity_is_caught(self):
        frame = bytearray(SAMPLE_MODERN)
        frame[12] = 0xC8                      # 200 %
        problems = implausible_fields(decode_status(bytes(frame)))
        self.assertTrue(any("humidity" in p for p in problems), problems)

    def test_unknown_mode_code_is_caught(self):
        frame = bytearray(SAMPLE_MODERN)
        frame[8] = 0x5A                       # not an operating mode
        problems = implausible_fields(decode_status(bytes(frame)))
        self.assertTrue(any("operating mode" in p for p in problems), problems)

    def test_unknown_role_is_caught(self):
        frame = bytearray(SAMPLE_MODERN)
        frame[17] = 0x77
        problems = implausible_fields(decode_status(bytes(frame)))
        self.assertTrue(any("role" in p for p in problems), problems)

    def test_a_legitimately_negative_temperature_is_accepted(self):
        # -12 °C is a normal winter reading and must not be flagged; this is
        # the case the signed decoding exists for.
        frame = bytearray(SAMPLE_MODERN)
        frame[11] = 0xF4
        decoded = decode_status(bytes(frame))
        self.assertEqual(decoded["temperature"], -12)
        self.assertEqual(implausible_fields(decoded), [])

    def test_a_weak_but_real_signal_is_accepted(self):
        frame = bytearray(SAMPLE_MODERN)
        frame[20] = 0xA6                      # -90 dBm, poor but real
        decoded = decode_status(bytes(frame))
        self.assertEqual(decoded["rssi"], -90)
        self.assertGreaterEqual(decoded["rssi"], PLAUSIBLE_RSSI_DBM[0])
        self.assertEqual(implausible_fields(decoded), [])

    def test_legacy_frames_are_not_penalised_for_a_missing_rssi(self):
        decoded = decode_status(SAMPLE_LEGACY)
        self.assertIsNone(decoded["rssi"])
        self.assertEqual(implausible_fields(decoded), [])


class TestCaptureToolCannotWrite(unittest.TestCase):
    """The pilot tool's safety property, asserted against its source."""

    SOURCE = Path(__file__).with_name("verify_capture.py")

    def test_the_tool_exists_and_imports(self):
        result = subprocess.run(
            [sys.executable, str(self.SOURCE), "--help"],
            capture_output=True, text=True, cwd=str(self.SOURCE.parent))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("never writes", result.stdout)

    def test_no_send_path_in_the_source(self):
        src = self.SOURCE.read_text(encoding="utf-8")
        for forbidden in ("sendall(", ".send(", "encode_mode_command",
                          "encode_setup", "encode_filter_reset"):
            self.assertNotIn(forbidden, src,
                             f"capture tool must not contain {forbidden}")

    def test_write_direction_is_shut_down(self):
        src = self.SOURCE.read_text(encoding="utf-8")
        self.assertIn("SHUT_WR", src,
                      "the tool should close its write direction explicitly")


if __name__ == "__main__":
    unittest.main(verbosity=2)
