#!/usr/bin/env python3
"""Build the deterministic sensor-data tarball used by device-oneplus-guacamole.

The payload is the public sensor configuration and SLPI userspace libraries
from the verified LineageOS 24.0 20261102 guacamole OTA. It deliberately does
NOT contain a sensor registry: the registry is this unit's factory calibration
and is imported from /persist on first boot. The previous private tarball from
the port author is not available, so this bundle is not byte-identical to it.

Inputs can either be an already extracted tree (default: the one prepared by
the coordinator) or the two ext4 images from the OTA, extracted read-only with
debugfs. The archive is reproducible: sorted entries, fixed ownership and
mtime, no file mode surprises, and gzip with mtime=0.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import NoReturn

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLE_NAME = "guacamole-sensors-20261102"
DEFAULT_SOURCE = REPO_ROOT / "ref/sensors/lineage-24.0-20261102"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "device/testing/device-oneplus-guacamole"
    / f"{BUNDLE_NAME}.tar.gz"
)


def fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def run_debugfs(image: Path, request: str) -> None:
    try:
        result = subprocess.run(
            ["debugfs", "-R", request, str(image)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        fail("debugfs is missing (install e2fsprogs on the host)")
    if result.returncode != 0:
        fail(f"debugfs failed for {image}: {result.stderr.strip()}")


def extract_from_images(vendor_img: Path, dsp_img: Path, staging: Path) -> Path:
    """Extract vendor_b/etc/sensors and dsp_b/sdsp into a staging tree."""
    for image in (vendor_img, dsp_img):
        if not image.is_file():
            fail(f"missing ext4 image: {image}")

    sensors = staging / "sensors"
    sensors.mkdir(parents=True)
    run_debugfs(vendor_img, f"rdump /etc/sensors/config {sensors}")
    run_debugfs(
        vendor_img,
        f"dump /etc/sensors/sns_reg_config {sensors / 'sns_reg.conf'}",
    )
    # rdump keeps the basename, so this creates staging/sdsp/.
    run_debugfs(dsp_img, f"rdump /sdsp {staging}")
    return staging


def validate_source(source: Path) -> None:
    config = source / "sensors/config"
    sns_reg = source / "sensors/sns_reg.conf"
    sdsp = source / "sdsp"

    if not config.is_dir():
        fail(f"{config} is missing; pass --vendor-img/--dsp-img or a valid --source")
    if not any(config.iterdir()):
        fail(f"{config} is empty")
    if not sns_reg.is_file() or sns_reg.stat().st_size == 0:
        fail(f"{sns_reg} is missing or empty")
    if not sdsp.is_dir():
        fail(f"{sdsp} is missing; pass --vendor-img/--dsp-img or a valid --source")
    if not any(sdsp.iterdir()):
        fail(f"{sdsp} is empty")

    for path in source.rglob("*"):
        if path.is_symlink():
            fail(f"refusing symlink in sensor payload: {path}")
        if not (path.is_dir() or path.is_file()):
            fail(f"refusing special file in sensor payload: {path}")


def add_entry(tar: tarfile.TarFile, name: str, mode: int, data: bytes | None) -> None:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    if data is None:
        info.type = tarfile.DIRTYPE
        info.size = 0
        tar.addfile(info)
    else:
        info.type = tarfile.REGTYPE
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))


def write_bundle(source: Path, output: Path) -> tuple[int, str]:
    validate_source(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in source.rglob("*") if p.is_file())
    dirs = sorted(p for p in source.rglob("*") if p.is_dir())
    if not files:
        fail(f"no files found under {source}")

    with output.open("wb") as raw:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0
        ) as gz:
            with tarfile.open(
                fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT
            ) as tar:
                add_entry(tar, f"{BUNDLE_NAME}/", 0o755, None)
                for path in dirs:
                    rel = path.relative_to(source).as_posix()
                    add_entry(tar, f"{BUNDLE_NAME}/{rel}/", 0o755, None)
                for path in files:
                    rel = path.relative_to(source).as_posix()
                    add_entry(tar, f"{BUNDLE_NAME}/{rel}", 0o644, path.read_bytes())

    data = output.read_bytes()
    return len(files), hashlib.sha512(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"extracted sensors/ + sdsp/ tree (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument("--vendor-img", type=Path, help="ext4 vendor_b image")
    parser.add_argument("--dsp-img", type=Path, help="ext4 dsp_b image")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"archive to write (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if args.vendor_img or args.dsp_img:
        if not (args.vendor_img and args.dsp_img):
            fail("--vendor-img and --dsp-img must be used together")
        with tempfile.TemporaryDirectory(prefix="guacamole-sensors-") as tmp:
            source = extract_from_images(args.vendor_img, args.dsp_img, Path(tmp))
            count, digest = write_bundle(source, args.output)
    else:
        count, digest = write_bundle(args.source, args.output)

    print(f"{args.output}: {count} files")
    print(f"sha512: {digest}")


if __name__ == "__main__":
    main()
