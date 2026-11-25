#!/usr/bin/env python3
"""Build the deterministic Venus (video codec) firmware tarball.

The experimental venus optional kernel set (flows/rc4/optional/venus) ships it
in the kernel package as qcom/sm8150/oneplus7/venus.mdt and venus.b00..bNN, the
split format the other remoteprocs on this phone use.
There is no SM8150 video firmware in linux-firmware; the source is the
NON-HLOS `modem` partition (`::/image/venus.*`), either the phone's own
modem_b copied off by hand or the modem image rebuilt from a LineageOS OTA.

The firmware is checked before it is packed: venus.mdt must be a 32-bit ELF,
every segment with file data needs a venus.bNN of exactly that size, and the
load span must fit the 5 MiB venus_mem region of sm8150.dtsi.

The archive is reproducible: sorted entries, fixed ownership and mtime, and
gzip with mtime=0.

Usage:
    scripts/prepare_venus_bundle.py [--source DIR | --modem-img IMG] [--output TAR]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "guacamole-venus"
DEFAULT_SOURCE = REPO_ROOT / "local/firmware/venus"
DEFAULT_OUTPUT = REPO_ROOT / "flows/rc4/optional/venus" / f"{BUNDLE_NAME}.tar.gz"
VENUS_MEM_SIZE = 0x500000  # venus_mem in sm8150.dtsi
NAME_RE = re.compile(r"venus\.(mdt|b[0-9]{2})")
PT_LOAD = 1
SEG_TYPE_HASH = 2  # Qualcomm segment type in p_flags bits 24-26 (mdt_loader.c)


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def segments(mdt: bytes) -> list[tuple[int, int, int, int, int, int]]:
    """(index, p_type, p_paddr, p_filesz, p_memsz, p_flags) of a 32-bit ELF."""
    if mdt[:4] != b"\x7fELF":
        fail("venus.mdt is not an ELF file")
    if mdt[4] != 1 or mdt[5] != 1:
        fail("venus.mdt is not a 32-bit little-endian ELF")
    phoff = struct.unpack_from("<I", mdt, 0x1C)[0]
    phentsize, phnum = struct.unpack_from("<HH", mdt, 0x2A)
    if phentsize != 32 or phoff + phnum * 32 > len(mdt):
        fail("venus.mdt has a truncated program header table")
    out = []
    for i in range(phnum):
        p_type, _off, _va, paddr, filesz, memsz, flags, _al = struct.unpack_from(
            "<8I", mdt, phoff + i * 32
        )
        out.append((i, p_type, paddr, filesz, memsz, flags))
    return out


def validate(files: dict[str, bytes]) -> int:
    """Check the split firmware; return its load span in bytes."""
    if "venus.mdt" not in files:
        fail("venus.mdt is missing")
    lo = hi = None
    for i, p_type, paddr, filesz, memsz, flags in segments(files["venus.mdt"]):
        seg_type = (flags >> 24) & 7
        if filesz:
            name = "venus.b%02d" % i
            if name not in files:
                fail(f"{name} is missing (segment {i} has {filesz} bytes)")
            if len(files[name]) != filesz:
                fail(f"{name} is {len(files[name])} bytes, segment {i} needs {filesz}")
        # Same rule as mdt_phdr_valid()/qcom_mdt_get_size(): every PT_LOAD with
        # memory except the hash segment counts.
        if p_type == PT_LOAD and memsz and seg_type != SEG_TYPE_HASH:
            lo = paddr if lo is None else min(lo, paddr)
            hi = paddr + memsz if hi is None else max(hi, paddr + memsz)
    if lo is None:
        fail("venus.mdt has no loadable segment")
    span = hi - lo
    if span > VENUS_MEM_SIZE:
        fail(f"load span {span:#x} does not fit venus_mem ({VENUS_MEM_SIZE:#x})")
    return span


def read_source(source: Path) -> dict[str, bytes]:
    if not source.is_dir():
        fail(f"{source} is not a directory")
    files = {p.name: p.read_bytes() for p in source.iterdir()
             if p.is_file() and NAME_RE.fullmatch(p.name)}
    if not files:
        fail(f"no venus.* files in {source}")
    return files


def read_modem_img(image: Path, tmp: Path) -> dict[str, bytes]:
    """Copy ::/image/venus.* out of a FAT modem image with mtools."""
    listing = subprocess.run(["mdir", "-b", "-i", str(image), "::/image"],
                             check=True, capture_output=True, text=True).stdout
    names = [n.rsplit("/", 1)[-1] for n in listing.split()]
    names = [n for n in names if NAME_RE.fullmatch(n.lower())]
    if not names:
        fail(f"no venus.* in {image}::/image")
    for name in names:
        subprocess.run(["mcopy", "-n", "-i", str(image), f"::/image/{name}",
                        str(tmp / name.lower())], check=True)
    return read_source(tmp)


def add_entry(tar: tarfile.TarFile, name: str, mode: int, data: bytes | None) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    if data is None:
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    else:
        info.type = tarfile.REGTYPE
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def write_bundle(files: dict[str, bytes], output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                           compresslevel=9, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
                add_entry(tar, f"{BUNDLE_NAME}/", 0o755, None)
                for name in sorted(files):
                    add_entry(tar, f"{BUNDLE_NAME}/{name}", 0o644, files[name])
    return hashlib.sha512(output.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"directory with venus.mdt + venus.bNN (default: {DEFAULT_SOURCE})")
    parser.add_argument("--modem-img", type=Path,
                        help="FAT modem partition image (read with mtools)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"archive to write (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    if args.modem_img:
        with tempfile.TemporaryDirectory(prefix="guacamole-venus-") as tmp:
            files = read_modem_img(args.modem_img, Path(tmp))
    else:
        files = read_source(args.source)
    span = validate(files)
    digest = write_bundle(files, args.output)
    print(f"{args.output}: {len(files)} files, load span {span:#x} of {VENUS_MEM_SIZE:#x}")
    print(f"sha512: {digest}")


if __name__ == "__main__":
    main()
