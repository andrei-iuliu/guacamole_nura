#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Focused host-side tests for scripts/check_boot_envelope.py."""

from __future__ import annotations

import contextlib
import io
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_boot_envelope as envelope  # noqa: E402


def write_image(path: Path, *, image_size: int, file_size: int, magic: bytes = b"ARM\x64") -> None:
    data = bytearray(file_size)
    struct.pack_into("<Q", data, 8, 0)
    struct.pack_into("<Q", data, 16, image_size)
    data[0x38:0x3C] = magic
    path.write_bytes(data)


class EnvelopeRuleTests(unittest.TestCase):
    def test_known_overlapping_7_3_3_case_is_rejected(self) -> None:
        # The first 7.3.3 candidate: image_size 0x2060000, file 33,405,440,
        # stock DTB at 0x01f00000.  This is the black-screen envelope.
        image = envelope.Arm64Image(
            file_size=33_405_440,
            header_image_size=0x2060000,
            text_offset=0,
        )
        with self.assertRaises(envelope.EnvelopeError) as raised:
            envelope.check_envelope(image, kernel_offset=0x8000, dtb_offset=0x01F00000)
        self.assertIn("overlaps the DTB", str(raised.exception))

    def test_valid_7_0_envelope_has_margin(self) -> None:
        image = envelope.Arm64Image(
            file_size=0x2A56A00,
            header_image_size=0x2AE0000,
            text_offset=0,
        )
        report = envelope.check_envelope(
            image, kernel_offset=0x8000, dtb_offset=0x03000000
        )
        expected_end = 0x8000 + 0x2AE0000
        self.assertEqual(report["kernel_load_end"], expected_end)
        self.assertEqual(report["dtb_margin"], 0x03000000 - expected_end)
        self.assertGreater(report["dtb_margin"], 0x400000)

    def test_boundary_is_adjacent_not_overlapping(self) -> None:
        extent = 0x1000
        image = envelope.Arm64Image(file_size=extent, header_image_size=extent, text_offset=0)
        report = envelope.check_envelope(
            image, kernel_offset=0x8000, dtb_offset=0x8000 + extent
        )
        self.assertEqual(report["dtb_margin"], 0)
        with self.assertRaises(envelope.EnvelopeError):
            envelope.check_envelope(
                image, kernel_offset=0x8000, dtb_offset=0x8000 + extent - 1
            )

    def test_header_image_size_wins_when_larger(self) -> None:
        image = envelope.Arm64Image(file_size=0x1000, header_image_size=0x2000, text_offset=0)
        self.assertEqual(image.extent, 0x2000)
        report = envelope.check_envelope(
            image, kernel_offset=0x8000, dtb_offset=0x8000 + 0x2000
        )
        self.assertEqual(report["kernel_extent"], 0x2000)
        with self.assertRaises(envelope.EnvelopeError):
            envelope.check_envelope(
                image, kernel_offset=0x8000, dtb_offset=0x8000 + 0x1FFF
            )

    def test_file_size_wins_when_larger(self) -> None:
        image = envelope.Arm64Image(file_size=0x2000, header_image_size=0x1000, text_offset=0)
        self.assertEqual(image.extent, 0x2000)
        report = envelope.check_envelope(
            image, kernel_offset=0x8000, dtb_offset=0x8000 + 0x2000
        )
        self.assertEqual(report["kernel_extent"], 0x2000)

    def test_invalid_offsets_are_rejected(self) -> None:
        image = envelope.Arm64Image(file_size=0x1000, header_image_size=0x1000, text_offset=0)
        for kwargs in (
            {"kernel_offset": -1, "dtb_offset": 0x8000},
            {"kernel_offset": 0x8000, "dtb_offset": 0},
            {"kernel_offset": 0x8000, "dtb_offset": -1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(envelope.EnvelopeError):
                envelope.check_envelope(image, **kwargs)

    def test_ramdisk_literal_overlap_is_reported_not_fatal(self) -> None:
        image = envelope.Arm64Image(
            file_size=0x2A56A00, header_image_size=0x2AE0000, text_offset=0
        )
        report = envelope.check_envelope(
            image,
            kernel_offset=0x8000,
            dtb_offset=0x03000000,
            ramdisk_offset=0x01000000,
            ramdisk_size=12_361_587,
        )
        self.assertGreater(report["ramdisk_literal_overlap"], 0)
        self.assertEqual(report["dtb_margin"], 0x03000000 - (0x8000 + 0x2AE0000))


class HeaderParsingTests(unittest.TestCase):
    def test_reads_arm64_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            write_image(path, image_size=0x2000, file_size=0x1800)
            image = envelope.read_arm64_image(path)
            self.assertEqual(image.header_image_size, 0x2000)
            self.assertEqual(image.file_size, 0x1800)
            self.assertEqual(image.extent, 0x2000)

    def test_rejects_short_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            path.write_bytes(b"ARM\x64")
            with self.assertRaises(envelope.EnvelopeError):
                envelope.read_arm64_image(path)

    def test_rejects_bad_magic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            write_image(path, image_size=0x1000, file_size=0x1000, magic=b"XXXX")
            with self.assertRaises(envelope.EnvelopeError) as raised:
                envelope.read_arm64_image(path)
            self.assertIn("magic", str(raised.exception))

    def test_rejects_zero_image_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            write_image(path, image_size=0, file_size=0x1000)
            with self.assertRaises(envelope.EnvelopeError):
                envelope.read_arm64_image(path)


class CommandLineTests(unittest.TestCase):
    def test_cli_pass_and_fail_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            write_image(path, image_size=0x2000, file_size=0x2000)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(
                    envelope.main(["--image", str(path), "--dtb-offset", "0xa000"]), 0
                )
                self.assertIn("margin 0x0", out.getvalue())
                self.assertEqual(
                    envelope.main(["--image", str(path), "--dtb-offset", "0x9fff"]), 1
                )
                self.assertIn("overlaps the DTB", err.getvalue())

    def test_cli_json_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Image"
            write_image(path, image_size=0x2000, file_size=0x2000)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(
                    envelope.main(
                        ["--image", str(path), "--dtb-offset", "0xa000", "--json"]
                    ),
                    0,
                )
            self.assertIn('"dtb_margin": 0', out.getvalue())


if __name__ == "__main__":
    unittest.main()
