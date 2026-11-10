#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate an arm64 Image against a v2 boot-image DTB load address.

The arm64 Image header's image_size field includes BSS, so the kernel can
touch more memory than the file occupies.  The Android boot-image DTB address
must be at or beyond the end of that range, otherwise the bootloader and the
early kernel can overwrite the device tree.

This is a host-side check only.  It does not claim to validate the ramdisk or
the bootloader's relocation rules; those are reported separately, because the
known-good 6.17 baseline already has a literal kernel/ramdisk overlap.
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys
from dataclasses import dataclass

ARM64_MAGIC = b"ARM\x64"
ARM64_HEADER_SIZE = 64
IMAGE_SIZE_OFFSET = 16
TEXT_OFFSET_OFFSET = 8


class EnvelopeError(Exception):
    """The image or the requested load envelope is invalid."""


@dataclass(frozen=True)
class Arm64Image:
    file_size: int
    header_image_size: int
    text_offset: int

    @property
    def extent(self) -> int:
        return max(self.file_size, self.header_image_size)


def read_arm64_image(path: str | os.PathLike[str]) -> Arm64Image:
    file_size = os.path.getsize(path)
    if file_size < ARM64_HEADER_SIZE:
        raise EnvelopeError(
            f"{path}: only {file_size} bytes, shorter than the 64-byte arm64 header"
        )
    with open(path, "rb") as handle:
        header = handle.read(ARM64_HEADER_SIZE)
    if header[0x38:0x3C] != ARM64_MAGIC:
        raise EnvelopeError(
            f"{path}: missing arm64 Image magic at 0x38 (got {header[0x38:0x3C]!r})"
        )
    text_offset = struct.unpack_from("<Q", header, TEXT_OFFSET_OFFSET)[0]
    image_size = struct.unpack_from("<Q", header, IMAGE_SIZE_OFFSET)[0]
    if image_size == 0:
        raise EnvelopeError(f"{path}: arm64 header image_size is zero")
    return Arm64Image(
        file_size=file_size,
        header_image_size=image_size,
        text_offset=text_offset,
    )


def check_envelope(
    image: Arm64Image,
    *,
    kernel_offset: int,
    dtb_offset: int,
    ramdisk_offset: int = 0x01000000,
    ramdisk_size: int = 0,
) -> dict[str, int]:
    """Return a report, or raise EnvelopeError if the DTB range is clobbered."""
    if kernel_offset < 0:
        raise EnvelopeError(f"negative kernel offset: {kernel_offset:#x}")
    if dtb_offset <= 0:
        raise EnvelopeError(f"non-positive DTB offset: {dtb_offset:#x}")
    if ramdisk_offset < 0 or ramdisk_size < 0:
        raise EnvelopeError("negative ramdisk offset or size")

    kernel_load_end = kernel_offset + image.extent
    if kernel_load_end > dtb_offset:
        raise EnvelopeError(
            "kernel image overlaps the DTB load address: "
            f"kernel [{kernel_offset:#x}, {kernel_load_end:#x}) "
            f"DTB at {dtb_offset:#x}; need at least {kernel_load_end - dtb_offset:#x} "
            "more bytes of separation or a smaller Image"
        )

    # Report the literal header ranges.  The known-good 6.17 baseline overlaps
    # here too, so this is diagnostic and must not make the check fail.
    ramdisk_end = ramdisk_offset + ramdisk_size
    overlap_start = max(kernel_offset, ramdisk_offset)
    overlap_end = min(kernel_load_end, ramdisk_end)
    ramdisk_overlap = max(0, overlap_end - overlap_start) if ramdisk_size else 0

    return {
        "file_size": image.file_size,
        "header_image_size": image.header_image_size,
        "kernel_extent": image.extent,
        "kernel_offset": kernel_offset,
        "kernel_load_end": kernel_load_end,
        "dtb_offset": dtb_offset,
        "dtb_margin": dtb_offset - kernel_load_end,
        "ramdisk_offset": ramdisk_offset,
        "ramdisk_size": ramdisk_size,
        "ramdisk_end": ramdisk_end,
        "ramdisk_literal_overlap": ramdisk_overlap,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="uncompressed arm64 Image")
    parser.add_argument("--kernel-offset", type=lambda text: int(text, 0), default=0x8000)
    parser.add_argument("--dtb-offset", type=lambda text: int(text, 0), required=True)
    parser.add_argument("--ramdisk-offset", type=lambda text: int(text, 0), default=0x01000000)
    parser.add_argument("--ramdisk-size", type=lambda text: int(text, 0), default=0)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        image = read_arm64_image(args.image)
        report = check_envelope(
            image,
            kernel_offset=args.kernel_offset,
            dtb_offset=args.dtb_offset,
            ramdisk_offset=args.ramdisk_offset,
            ramdisk_size=args.ramdisk_size,
        )
    except EnvelopeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "kernel: file={file_size:#x} header_image_size={header_image_size:#x} "
            "extent={kernel_extent:#x}".format(**report)
        )
        print(
            "kernel load [{kernel_offset:#x}, {kernel_load_end:#x}) "
            "DTB {dtb_offset:#x} margin {dtb_margin:#x}".format(**report)
        )
        if report["ramdisk_literal_overlap"]:
            print(
                "WARNING: literal kernel/ramdisk ranges overlap by "
                f"{report['ramdisk_literal_overlap']:#x} bytes; "
                "the known 6.17 baseline has the same literal overlap, so this "
                "check does not establish the bootloader's relocation rules."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
