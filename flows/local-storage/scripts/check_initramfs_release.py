#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check that an initramfs lists exactly one kernel module release.

This is a path-level consistency check, not an integrity check: it lists the
cpio members and compares the ``lib/modules/<release>`` directories.  It does
not verify file contents, signatures, module loadability or that the ramdisk
matches the booted Image beyond the release string.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
XZ_MAGIC = b"\xfd7zXZ\x00"
MODULES_MARKER = "lib/modules/"


class InitramfsError(Exception):
    """The archive could not be listed or has the wrong module release."""


def _run(command: list[str], stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(command, input=stdin, capture_output=True)


def _decompress(path: Path, tool: str, args: list[str]) -> bytes:
    if shutil.which(tool) is None:
        raise InitramfsError(f"{path}: {tool} is required to decompress this archive")
    check = _run([tool, *args], stdin=path.read_bytes())
    if check.returncode != 0:
        raise InitramfsError(
            f"{path}: {tool} could not decompress the archive: "
            f"{check.stderr.decode(errors='replace').strip()}"
        )
    return check.stdout


def list_archive(path: Path) -> list[str]:
    """Return cpio member names, failing closed on decompression/listing errors."""
    if not path.is_file():
        raise InitramfsError(f"missing initramfs: {path}")
    with path.open("rb") as handle:
        magic = handle.read(6)
    if magic.startswith(GZIP_MAGIC):
        data = _decompress(path, "gzip", ["-dc"])
    elif magic.startswith(ZSTD_MAGIC):
        data = _decompress(path, "zstd", ["-dc"])
    elif magic.startswith(XZ_MAGIC):
        data = _decompress(path, "xz", ["-dc"])
    else:
        data = path.read_bytes()

    if shutil.which("cpio") is None:
        raise InitramfsError("cpio is required to list the initramfs")
    listing = _run(["cpio", "-t"], stdin=data)
    if listing.returncode != 0:
        raise InitramfsError(
            f"{path}: cpio could not list the archive: "
            f"{listing.stderr.decode(errors='replace').strip()}"
        )
    return [line for line in listing.stdout.decode(errors="replace").splitlines() if line.strip()]


def module_releases(entries: list[str]) -> set[str]:
    """Extract every literal lib/modules/<release> directory name."""
    releases: set[str] = set()
    for entry in entries:
        entry = entry.strip()
        if MODULES_MARKER not in entry:
            continue
        rest = entry.split(MODULES_MARKER, 1)[1]
        release = rest.split("/", 1)[0].strip()
        if release:
            releases.add(release)
    return releases


def check_release(path: Path, expected: str) -> dict[str, object]:
    try:
        entries = list_archive(path)
    except OSError as error:
        raise InitramfsError(f"{path}: cannot read archive: {error}") from error
    releases = module_releases(entries)
    if not entries:
        raise InitramfsError(f"{path}: cpio archive is empty")
    if not releases:
        raise InitramfsError(f"{path}: no {MODULES_MARKER}<release> entries found")
    if releases != {expected}:
        found = ", ".join(sorted(releases))
        raise InitramfsError(
            f"{path}: module releases {{{found}}} do not match exactly {{{expected}}}"
        )
    return {
        "archive": str(path),
        "expected_release": expected,
        "module_releases": sorted(releases),
        "entries": len(entries),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, help="initramfs archive")
    parser.add_argument("--release", required=True, help="expected kernel release")
    parser.add_argument("--json", action="store_true", help="print a JSON report")
    args = parser.parse_args(argv)

    try:
        report = check_release(Path(args.archive), args.release)
    except InitramfsError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"initramfs release: {report['expected_release']} "
            f"({report['entries']} cpio entries; listing-only check)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
