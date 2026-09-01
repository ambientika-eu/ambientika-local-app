#!/usr/bin/env python3
"""
Stream-framing tests — the part that actually failed in upstream issue #5.

The old handler sliced a fixed 21 bytes off every ``0x01`` frame. Against a
legacy unit sending 19 that walks two bytes further into the stream on each
frame, so the parser drifts across frame boundaries and never recovers. These
tests drive :class:`FrameReader` with realistically chopped TCP chunks and
assert that every frame comes back intact.

Run with:  python -m unittest test_framing -v
"""

from __future__ import annotations

import unittest

from ambientika_protocol import (
    FIRMWARE_LEN, FrameReader, STATUS_LEN_LEGACY, STATUS_LEN_MODERN,
    decode_firmware, decode_status,
)

SAMPLE_LEGACY = bytes.fromhex("01001c9dc2430444030101" + "1b" + "35" +
                              "00" + "00" + "00" + "01" + "00" + "03")
SAMPLE_MODERN = SAMPLE_LEGACY + bytes([0x01, 0xC6])

FW_LEGACY = (bytes([0x03, 0x00]) + bytes.fromhex("1C9DC2430444")
             + bytes([0, 0, 11, 0, 0, 11, 1, 1, 3, 0]))
FW_MODERN = (bytes([0x03, 0x00]) + bytes.fromhex("1C9DC2430444")
             + bytes([0, 0, 28, 0, 1, 22, 1, 1, 3, 0]))


def drive(reader: FrameReader, stream: bytes, chunk_size: int):
    """Feed a stream in fixed-size chunks, collecting every yielded frame."""
    out = []
    for i in range(0, len(stream), chunk_size):
        out.extend(reader.feed(stream[i:i + chunk_size]))
    return out


class TestLegacyStream(unittest.TestCase):
    """Ten consecutive 19-byte frames — the exact case that used to drift."""

    def test_all_frames_survive_every_chunk_size(self):
        stream = SAMPLE_LEGACY * 10
        for chunk in (1, 2, 3, 5, 7, 19, 20, 21, 64, 256, len(stream)):
            reader = FrameReader(radio_fw="0.0.11")
            frames = drive(reader, stream, chunk)
            self.assertEqual(len(frames), 10, f"chunk size {chunk}")
            for kind, frame in frames:
                self.assertEqual(kind, "status")
                self.assertEqual(len(frame), STATUS_LEN_LEGACY)
                self.assertEqual(decode_status(frame)["serial"], "1C9DC2430444")
            self.assertEqual(reader.resync_count, 0, f"chunk size {chunk}")
            self.assertEqual(len(reader.buf), 0, f"chunk size {chunk}")

    def test_firmware_frame_teaches_the_reader_the_length(self):
        # No firmware hint up front: the reader learns it from the 0x03 frame,
        # which is the sequence a real connection produces.
        reader = FrameReader()
        stream = FW_LEGACY + SAMPLE_LEGACY * 3
        frames = []
        for i in range(0, len(stream), 4):
            for kind, frame in reader.feed(stream[i:i + 4]):
                if kind == "firmware":
                    reader.set_radio_fw(decode_firmware(frame)["radioFw"])
                frames.append((kind, frame))
        kinds = [k for k, _ in frames]
        self.assertEqual(kinds, ["firmware", "status", "status", "status"])
        self.assertEqual(reader.known_status_len, STATUS_LEN_LEGACY)


class TestModernStream(unittest.TestCase):
    def test_all_frames_survive(self):
        stream = SAMPLE_MODERN * 8
        for chunk in (1, 3, 21, 22, 128):
            reader = FrameReader(radio_fw="0.0.28")
            frames = drive(reader, stream, chunk)
            self.assertEqual(len(frames), 8, f"chunk size {chunk}")
            self.assertTrue(all(len(f) == STATUS_LEN_MODERN for _, f in frames))
            self.assertEqual(decode_status(frames[0][1])["rssi"], -58)


class TestWithoutFirmwareHint(unittest.TestCase):
    """The probe has to carry the stream when no firmware frame arrived."""

    def test_legacy_stream_is_detected_by_the_probe(self):
        reader = FrameReader()
        frames = drive(reader, SAMPLE_LEGACY * 4, 5)
        # The final frame stays buffered: with nothing following it, the reader
        # cannot yet tell 19 from 21 — and holding it is the correct behaviour.
        self.assertGreaterEqual(len(frames), 3)
        self.assertTrue(all(len(f) == STATUS_LEN_LEGACY for _, f in frames))

    def test_modern_stream_is_detected_by_the_probe(self):
        reader = FrameReader()
        frames = drive(reader, SAMPLE_MODERN * 4, 5)
        self.assertEqual(len(frames), 4)
        self.assertTrue(all(len(f) == STATUS_LEN_MODERN for _, f in frames))

    def test_a_lone_legacy_frame_is_held_not_mis_decoded(self):
        # The failure mode we are protecting against is decoding it as 21 bytes.
        reader = FrameReader()
        frames = list(reader.feed(SAMPLE_LEGACY))
        self.assertEqual(frames, [])
        self.assertEqual(len(reader.buf), STATUS_LEN_LEGACY)

    def test_a_trailing_firmware_frame_releases_the_held_status(self):
        reader = FrameReader()
        frames = list(reader.feed(SAMPLE_LEGACY))
        self.assertEqual(frames, [])
        frames = list(reader.feed(FW_LEGACY))
        self.assertEqual([k for k, _ in frames], ["status", "firmware"])


class TestMixedAndDamagedStreams(unittest.TestCase):
    def test_interleaved_status_and_firmware(self):
        reader = FrameReader(radio_fw="0.0.11")
        stream = SAMPLE_LEGACY + FW_LEGACY + SAMPLE_LEGACY + SAMPLE_LEGACY
        frames = drive(reader, stream, 6)
        self.assertEqual([k for k, _ in frames],
                         ["status", "firmware", "status", "status"])

    def test_stray_byte_resyncs_without_losing_the_next_frame(self):
        reader = FrameReader(radio_fw="0.0.11")
        stream = b"\x99\x99" + SAMPLE_LEGACY * 2
        frames = drive(reader, stream, 4)
        self.assertEqual(len(frames), 2)
        self.assertEqual(reader.resync_count, 2)

    def test_buffer_cannot_grow_without_bound(self):
        reader = FrameReader(max_buffer=64)
        list(reader.feed(b"\x01" * 500))     # never completes a valid frame
        self.assertLessEqual(len(reader.buf), 64)

    def test_firmware_frame_length_is_respected(self):
        reader = FrameReader(radio_fw="0.0.11")
        frames = drive(reader, FW_LEGACY + FW_MODERN, 3)
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(len(f) == FIRMWARE_LEN for _, f in frames))
        self.assertEqual(decode_firmware(frames[1][1])["radioFw"], "0.0.28")


class TestRegressionAgainstTheOldBehaviour(unittest.TestCase):
    """Demonstrates the bug the patch fixes, using the old fixed-21 logic."""

    @staticmethod
    def old_demux(stream: bytes):
        """The previous handler, reduced to its framing decision."""
        buf = stream
        out = []
        while buf:
            if buf[0] == 0x01:
                if len(buf) < 21:
                    break
                out.append(("status", buf[:21]))
                buf = buf[21:]
            elif buf[0] == 0x03:
                if len(buf) < 18:
                    break
                out.append(("firmware", buf[:18]))
                buf = buf[18:]
            else:
                buf = buf[1:]
        return out

    def test_old_logic_loses_all_but_the_first_status_frame(self):
        old = self.old_demux(SAMPLE_LEGACY * 5)
        statuses = [f for kind, f in old if kind == "status"]
        self.assertEqual(len(statuses), 1,
                         "five status frames went in; the old demux keeps one")

    def test_old_logic_reads_garbage_into_the_tail_fields(self):
        # The one status frame it does emit has swallowed the first two bytes
        # of the following frame, so light sensor and RSSI are nonsense —
        # an RSSI of exactly 0 dBm is the tell.
        old = self.old_demux(SAMPLE_LEGACY * 5)
        frame = [f for kind, f in old if kind == "status"][0]
        self.assertEqual(frame[19], 0x01)     # actually the next frame's type byte
        self.assertEqual(frame[20], 0x00)     # actually the next frame's second byte

    def test_old_logic_invents_a_phantom_device(self):
        # Worse than dropping data: the drifted stream re-parses as firmware
        # frames for a device that does not exist, which is what fires the
        # bogus discovery reported in issue #5.
        old = self.old_demux(SAMPLE_LEGACY * 5)
        phantom = {decode_firmware(f)["serial"]
                   for kind, f in old if kind == "firmware"}
        self.assertEqual(phantom, {"011B35000000"})
        self.assertNotIn("1C9DC2430444", phantom)

    def test_new_logic_keeps_the_same_stream_in_sync(self):
        reader = FrameReader(radio_fw="0.0.11")
        frames = drive(reader, SAMPLE_LEGACY * 5, 7)
        serials = {decode_status(f)["serial"] for _, f in frames}
        self.assertEqual(len(frames), 5)
        self.assertEqual(serials, {"1C9DC2430444"})

    def test_new_logic_emits_no_phantom_firmware_frames(self):
        reader = FrameReader(radio_fw="0.0.11")
        frames = drive(reader, SAMPLE_LEGACY * 5, 7)
        self.assertEqual([k for k, _ in frames], ["status"] * 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
