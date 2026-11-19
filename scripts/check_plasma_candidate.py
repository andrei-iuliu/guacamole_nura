#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Validate and record the isolated Plasma Mobile candidate.

This helper is host-only.  It never runs pmbootstrap, sudo, a phone command or
a mount.  It is called by ``scripts/rootfs_prepare_plasma.sh`` and
``scripts/rc3_build.sh`` and can also be used directly for read-only checks.

Identity has two independent halves:

* the rc3 kernel build (``build/rc3-kernel`` + ``.work/rc3-kernel``) records
  the Image, vmlinuz, DTB, CCI module, complete module payload and the packed
  boot image it produced.  Every value is computed from that build, never
  copied from an older release;
* the Plasma candidate records the exported rootfs initramfs and rootfs image
  and compares the installed rootfs byte-for-byte with the staged modules,
  DTB and vmlinuz.  The boot-image ramdisk must be the exported rc3 rootfs
  initramfs that lists release 7.0.0-sm8150-rc3 and sets the port-10001 hook.

The old rc2 rear1 baseline is read-only input for the two pinned APKs and the
system libcamera 0.8 libraries; it is never rewritten or relabeled.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from check_initramfs_release import InitramfsError
from check_rear_camera_package import (check_module_payload, module_payload,
                                       check_initramfs, digest as module_digest)

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "7.0.0-sm8150-rc3"
# The kernel uname for this candidate is 7.0.0-sm8150-rc3.  It must match the
# kernel recipe and the staged kernel.release exactly.  The previous rc2 boot
# chain (7.0.0-sm8150-rear1) remains valid only through an explicit rollback.
LEGACY_RELEASE = "7.0.0-sm8150-rear1"
RELEASE_PATTERN = re.compile(r"7\.0\.0-sm8150-(?:rc[0-9]+|rear[0-9]+)")
RELEASE_SERIES = "nura-plasma"
RELEASE_CANDIDATE = "nura-plasma-rc3"
ARTIFACT_NAME = f"{RELEASE_CANDIDATE}-oneplus-guacamole"
NBD_PORT = 10001
PROFILE = "plasma-mobile"
LOCK_ON_START = False
KERNEL_RECIPE = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
KERNEL_STATE = ROOT / ".work/rc3-kernel"
KERNEL_BUILD = ROOT / "build/rc3-kernel"
BASELINE_STATE = ROOT / ".work/rear-camera"
BASELINE_BUILD = ROOT / "build/rear-camera"
CLOCK_EPOCH = "usr/lib/clock-epoch"
LIBCAMERA_ABI = "99990.8.0-r1"
CAMERA_APP = "plasma-camera"
CAMERA_APP_LIBCAMERA_ABI = "so:libcamera.so.0.8"
LIBRARY_REAR = "0.7.2-r0"
HOOK_VERSION = "4.2.1-r4"
POWERDEVIL_VERSION = "6.8.2-r0"
SYSTEM_LIBCAMERA_APKS = {
    "libcamera": (
        "libcamera-99990.8.0-r1.01d92cee.apk",
        "b2bee232ba7c5a596a6da1834f97fc6a7f0db4282b3856295d2481cffbac7944",
        916130,
        "99990.8.0-r1",
    ),
    "libcamera-ipa": (
        "libcamera-ipa-99990.8.0-r1.00365e1b.apk",
        "3d747573bc84215274f0cc830e9e806946bf443b384814e1e9234307c234ed30",
        847344,
        "99990.8.0-r1",
    ),
}
SYSTEM_LIBCAMERA_LIBS = (
    "usr/lib/libcamera.so.0.8.0",
    "usr/lib/libcamera-base.so.0.8.0",
)
POWERDEVIL_CONFIG = (
    "[AC][SuspendAndShutdown]\n"
    "AutoSuspendAction=0\n"
    "[Battery][SuspendAndShutdown]\n"
    "AutoSuspendAction=0\n"
    "[LowBattery][SuspendAndShutdown]\n"
    "AutoSuspendAction=0\n"
)
OFFICIAL_PMAPORTS = (
    "https://gitlab.postmarketos.org/postmarketOS/pmaports.git",
    "https://gitlab.com/postmarketos/pmaports.git",
)


def die(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def note(message: str) -> None:
    print(message)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head() -> str:
    return git_rev(ROOT)


def git_rev(repository: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def resolve_path(value: str | os.PathLike[str], base: Path = ROOT) -> Path:
    if value is None:
        die("manifest entry has no path")
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def digest(path: Path) -> str:
    try:
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError as error:
        die(f"cannot hash {path}: {error}")


def sha512(path: Path) -> str:
    try:
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha512").hexdigest()
    except OSError as error:
        die(f"cannot hash {path}: {error}")


def size_of(path: Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError as error:
        die(f"cannot stat {path}: {error}")


def read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        die(f"cannot read JSON {path}: {error}")


def write_atomic(path: Path, text: str, mode: int | None = None) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_text(text, encoding="utf-8")
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        die(f"cannot write {path}: {error}")


def check_pin(path: Path, expected_sha: str, expected_size: int | None, label: str) -> Path:
    path = Path(path)
    if path.is_symlink():
        die(f"{label} must be a real file/directory, not a symlink: {path}")
    if not path.is_file():
        die(f"missing {label}: {path}")
    actual = digest(path)
    if actual != expected_sha:
        die(f"{label} SHA256 mismatch: {path} is {actual}, manifest says {expected_sha}")
    if expected_size is not None and size_of(path) != expected_size:
        die(f"{label} size mismatch: {path} is {size_of(path)}, manifest says {expected_size}")
    return path


def artifact(entry: dict, label: str) -> Path:
    if not isinstance(entry, dict):
        die(f"manifest has no object for {label}")
    return check_pin(
        resolve_path(entry.get("path")),
        str(entry.get("sha256", "")),
        entry.get("size"),
        label,
    )


def parse_apk(path: Path, name: str) -> str:
    path = Path(path)
    prefix = name + "-"
    if not path.name.startswith(prefix) or not path.name.endswith(".apk"):
        die(f"{name} APK has an unexpected name: {path.name}")
    version = path.name[len(prefix):-len(".apk")]
    if not version:
        die(f"{name} APK has no version: {path.name}")
    return version


def apk_members(data: bytes) -> list[tuple[bytes, bytes]]:
    """Split APKv2's signature, control and data gzip members without changing bytes."""
    members = []
    while data:
        decoder = zlib.decompressobj(31)
        content = decoder.decompress(data)
        if not decoder.eof:
            raise ValueError("truncated APK gzip member")
        consumed = len(data) - len(decoder.unused_data)
        members.append((data[:consumed], content))
        data = decoder.unused_data
        if len(members) > 3:
            raise ValueError("unexpected APK gzip member count")
    return members


def apk_payload(path: Path) -> tuple[bytes, bytes]:
    members = apk_members(path.read_bytes())
    if len(members) != 3:
        raise ValueError(f"{path}: expected signed APKv2 with three gzip members")
    with tarfile.open(fileobj=io.BytesIO(members[0][1])) as archive:
        names = archive.getnames()
        if len(names) != 1 or not names[0].startswith(".SIGN."):
            raise ValueError(f"{path}: invalid APK signature member")
    with tarfile.open(fileobj=io.BytesIO(members[1][1])) as archive:
        if ".PKGINFO" not in archive.getnames():
            raise ValueError(f"{path}: missing APK control metadata")
    return members[1][0], members[2][0]


def resign_apk(args: argparse.Namespace) -> None:
    source = check_pin(args.source, args.sha256, None, "original cached APK")
    control, data = apk_payload(source)
    if args.control_output:
        args.control_output.write_bytes(control)
    elif args.candidate:
        if apk_payload(args.candidate) != (control, data):
            raise ValueError("re-signed APK control/data differs from the pinned original")
    else:
        signed = args.signed_control.read_bytes()
        members = apk_members(signed)
        if len(members) != 2 or members[1][0] != control:
            raise ValueError("signing changed the APK control member")
        # Validate the signature container and exact original control/data before publishing.
        staging = args.output.with_name(args.output.name + ".new")
        staging.write_bytes(signed + data)
        try:
            if apk_payload(staging) != (control, data):
                raise ValueError("signing changed the APK payload")
            staging.replace(args.output)
        finally:
            staging.unlink(missing_ok=True)


def validate_release(value: object, label: str) -> str:
    if not isinstance(value, str) or not RELEASE_PATTERN.fullmatch(value):
        die(f"{label} release {value!r} is not an accepted 7.0.0-sm8150 candidate release")
    return value


def inside_root(path: Path) -> bool:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def relative_to_root(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        die(f"path is outside this checkout: {path}")


def pinned_file(entry: object, label: str, *, expected_path: Path | None = None) -> Path:
    """Resolve a manifest entry and prove its bytes are still the recorded ones."""
    if not isinstance(entry, dict):
        die(f"{label}: manifest entry is not an object")
    path = resolve_path(entry.get("path"))
    if not inside_root(path):
        die(f"{label}: {path} resolves outside this checkout")
    if expected_path is not None and path != Path(expected_path).resolve():
        die(f"{label}: {path} is not the expected path {expected_path}")
    check_pin(path, str(entry.get("sha256", "")), entry.get("size"), label)
    return path


def kernel_inputs_contract(build: Path, state: Path, recipe: str) -> dict:
    """Validate the isolated rc3 kernel staging and its recorded recipe identity."""
    build = Path(build).resolve()
    state = Path(state).resolve()
    if not inside_root(build) or not inside_root(state):
        die(f"kernel build/state must stay inside this checkout: {build}, {state}")
    recipe_path = (ROOT / recipe).resolve()
    if not inside_root(recipe_path) or not recipe_path.is_dir():
        die(f"missing rc3 kernel recipe: {recipe_path}")
    apkbuild = recipe_path / "APKBUILD"
    config = recipe_path / "config-postmarketos-qcom-sm8150.aarch64"
    for path in (apkbuild, config):
        if path.is_symlink() or not path.is_file():
            die(f"missing rc3 kernel recipe file: {path}")
    inputs_path = build / "kernel-inputs.json"
    inputs = read_json(inputs_path)
    if validate_release(inputs.get("release"), "kernel-inputs.json") != RELEASE:
        die(f"{inputs_path}: release {inputs.get('release')!r} is not {RELEASE}")
    if inputs.get("recipe") != recipe:
        die(f"{inputs_path}: recipe {inputs.get('recipe')!r} is not {recipe!r}")
    if inputs.get("recipe_apkbuild_sha256") != digest(apkbuild):
        die(f"{inputs_path}: recipe APKBUILD changed after this kernel was built; rebuild it")
    if inputs.get("recipe_config_sha256") != digest(config):
        die(f"{inputs_path}: recipe config changed after this kernel was built; rebuild it")
    recorded_worktree = inputs.get("worktree")
    if not isinstance(recorded_worktree, str) or Path(recorded_worktree).resolve() != ROOT.resolve():
        die(f"{inputs_path}: worktree {recorded_worktree!r} is not this checkout")
    image = pinned_file(inputs.get("image"), "kernel Image", expected_path=build / "Image")
    kernel_dir = (build / "kernel-package").resolve()
    if not kernel_dir.is_dir():
        die(f"missing kernel package staging: {kernel_dir}")
    vmlinuz = pinned_file(inputs.get("vmlinuz"), "kernel vmlinuz",
                          expected_path=kernel_dir / "boot/vmlinuz")
    dtb = pinned_file(inputs.get("dtb"), "kernel DTB",
                      expected_path=kernel_dir / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb")
    cci = pinned_file(inputs.get("cci_module"), "kernel CCI module",
                      expected_path=kernel_dir / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst")
    release_file = kernel_dir / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
    if not release_file.is_file() or release_file.read_text(encoding="utf-8").strip() != RELEASE:
        die(f"staged kernel release is not {RELEASE}: {release_file}")
    try:
        payload = module_payload(kernel_dir, RELEASE)
    except (ValueError, OSError) as error:
        die(f"cannot identify the staged module payload: {error}")
    if payload["sha256"] != inputs.get("module_payload_sha256"):
        die(f"{inputs_path}: module payload differs from the staged modules")
    if payload["count"] != inputs.get("module_count"):
        die(f"{inputs_path}: module count differs from the staged modules")
    if digest(kernel_dir / "boot/vmlinuz") != digest(vmlinuz):
        die("kernel vmlinuz changed during validation")
    return {
        "release": RELEASE,
        "recipe": recipe,
        "recipe_dir": str(recipe_path),
        "recipe_apkbuild_sha256": digest(apkbuild),
        "recipe_config_sha256": digest(config),
        "kernel_inputs": str(inputs_path),
        "kernel_inputs_sha256": digest(inputs_path),
        "kernel_build_dir": str(build),
        "kernel_state_dir": str(state),
        "kernel_package_dir": str(kernel_dir),
        "image": str(image),
        "image_sha256": digest(image),
        "vmlinuz": str(vmlinuz),
        "vmlinuz_sha256": digest(vmlinuz),
        "dtb": str(dtb),
        "dtb_sha256": digest(dtb),
        "cci_sha256": digest(cci),
        "module_payload_sha256": payload["sha256"],
        "module_count": payload["count"],
        "inputs": inputs,
    }


def boot_image_parts(path: Path) -> dict:
    """Parse a boot.img enough to prove what the packer embedded.

    Real mkbootimg v2 images are parsed from the header.  A synthetic fixture
    without the ANDROID! magic is accepted only by locating the exact payload
    bytes, which still proves inclusion but not the header layout.
    """
    data = Path(path).read_bytes()
    parts = {"parsed": False, "cmdline": "", "page": 4096}
    if data[:8] == b"ANDROID!":
        import struct
        (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
         second_addr, tags_addr, page_size, header_version) = struct.unpack_from(
            "<9I", data, 8)
        if page_size not in (2048, 4096, 8192, 16384):
            die(f"{path}: implausible boot image page size {page_size}")
        extra_cmdline = data[608:1632].split(b"\0", 1)[0]
        cmdline = data[64:576].split(b"\0", 1)[0]
        if len(data) < 1660:
            die(f"{path}: truncated Android v2 boot header")
        recovery_dtbo_size = struct.unpack_from("<I", data, 1632)[0]
        header_size = struct.unpack_from("<I", data, 1644)[0]
        dtb_size = struct.unpack_from("<I", data, 1648)[0]
        if header_version < 2 or dtb_size == 0:
            die(f"{path}: boot image header is not v2 with a DTB")
        pages = lambda size: (size + page_size - 1) // page_size
        offset = page_size
        parts["kernel"] = data[offset:offset + kernel_size]
        offset += pages(kernel_size) * page_size
        parts["ramdisk"] = data[offset:offset + ramdisk_size]
        offset += pages(ramdisk_size) * page_size
        offset += pages(second_size) * page_size
        offset += pages(recovery_dtbo_size) * page_size
        parts["dtb"] = data[offset:offset + dtb_size]
        parts["parsed"] = True
        parts["cmdline"] = (cmdline + b" " + extra_cmdline).decode(errors="replace")
        parts["page"] = page_size
        parts["header_version"] = header_version
        parts["header_size"] = header_size
    return parts


def prove_boot_image(path: Path, image: Path, initramfs: Path, dtb: Path, *, label: str) -> dict:
    if not path.is_file() or path.is_symlink():
        die(f"{label}: missing real boot image: {path}")
    parts = boot_image_parts(path)
    payloads = {
        "kernel": Path(image).read_bytes(),
        "ramdisk": Path(initramfs).read_bytes(),
        "dtb": Path(dtb).read_bytes(),
    }
    data = Path(path).read_bytes()
    if parts["parsed"]:
        for name, want in payloads.items():
            if parts.get(name) != want:
                die(f"{label}: packed {name} bytes differ from the expected payload")
    else:
        for name, want in payloads.items():
            if not want or want not in data:
                die(f"{label}: boot image does not contain the expected {name} bytes")
    return parts


def kernel_delivery_contract(build: Path, state: Path, recipe: str) -> dict:
    """Validate the packed boot image and the kernel delivery manifest."""
    base = kernel_inputs_contract(build, state, recipe)
    build = Path(build).resolve()
    delivery_path = build / "DELIVERY.json"
    delivery = read_json(delivery_path)
    if validate_release(delivery.get("release"), "rc3 kernel DELIVERY.json") != RELEASE:
        die(f"{delivery_path}: release {delivery.get('release')!r} is not {RELEASE}")
    if delivery.get("worktree") and Path(delivery["worktree"]).resolve() != ROOT.resolve():
        die(f"{delivery_path}: worktree {delivery.get('worktree')!r} is not this checkout")
    if delivery.get("kernel_inputs_sha256") != base["kernel_inputs_sha256"]:
        die(f"{delivery_path}: kernel-inputs.json changed after the boot image was packed")
    boot = pinned_file(delivery.get("boot_img"), "rc3 boot image", expected_path=build / "boot.img")
    initramfs = pinned_file(delivery.get("initramfs"), "rc3 boot initramfs")
    rootfs_image = pinned_file(delivery.get("rootfs_image"), "rc3 rootfs image")
    pinned_file(delivery.get("kernel_package"), "rc3 kernel package", expected_path=Path(base["vmlinuz"]))
    pinned_file(delivery.get("dtb"), "rc3 DTB", expected_path=Path(base["dtb"]))
    pinned_file(delivery.get("i2c_qcom_cci"), "rc3 CCI module")
    if digest(Path(base["image"])) != digest(Path(delivery["image"]["path"])):
        die(f"{delivery_path}: Image entry differs from the staged Image")
    wrapper_dir = build / "temporary-dtbo"
    wrapper = wrapper_dir / "custom_guacamole-abl-quarantine.dtb"
    wrapper_manifest = read_json(wrapper_dir / "manifest.json")
    try:
        wrapper_base = wrapper_manifest["inputs"]["base_dtb"]["sha256"]
        wrapper_sha = wrapper_manifest["artifacts"]["wrapper"]["sha256"]
    except (KeyError, TypeError):
        die(f"{wrapper_dir}/manifest.json: missing base_dtb/wrapper identity")
    if wrapper_base != base["dtb_sha256"]:
        die("rc3 temporary-dtbo wrapper was built from a different DTB")
    if wrapper_sha != digest(wrapper):
        die("rc3 temporary-dtbo wrapper hash differs from its manifest")
    parts = prove_boot_image(boot, Path(base["image"]), initramfs, wrapper, label="rc3 boot image")
    try:
        check_initramfs(initramfs, RELEASE)
    except (ValueError, InitramfsError, OSError, subprocess.CalledProcessError) as error:
        die(f"rc3 boot initramfs is not a usable {RELEASE} port-{NBD_PORT} archive: {error}")
    if parts["parsed"]:
        cmdline = parts["cmdline"]
        for required in ("pmos.root=/dev/nbd0p2", "pmos.boot=/dev/nbd0p1"):
            if required not in cmdline:
                die(f"rc3 boot image cmdline lacks {required}: {cmdline!r}")
    base.update({
        "boot_img": str(boot),
        "boot_img_sha256": digest(boot),
        "initramfs": str(initramfs),
        "initramfs_sha256": digest(initramfs),
        "initramfs_size": size_of(initramfs),
        "rootfs_image": str(rootfs_image),
        "rootfs_image_sha256": digest(rootfs_image),
        "temporary_dtbo_dir": str(wrapper_dir),
        "temporary_dtbo_sha256": digest(wrapper),
        "delivery": str(delivery_path),
        "delivery_sha256": digest(delivery_path),
    })
    return base


def baseline_pins(build: Path, state: Path) -> dict:
    """Read-only rc2/rear1 pins: hook, private libcamera, system libcamera 0.8."""
    build = Path(build).resolve()
    state = Path(state).resolve()
    delivery_path = build / "DELIVERY.json"
    inputs_path = build / "packaging-inputs.json"
    delivery = read_json(delivery_path)
    inputs = read_json(inputs_path)
    if delivery.get("release") != LEGACY_RELEASE or inputs.get("release") != LEGACY_RELEASE:
        die(f"baseline manifests are not the {LEGACY_RELEASE} rollback input: {build}")
    if inputs.get("nbd_port") != NBD_PORT:
        die(f"{inputs_path}: nbd_port is not {NBD_PORT}")
    rootfs_tree = resolve_path(inputs.get("rootfs"))
    if not rootfs_tree.is_dir() or rootfs_tree.is_symlink():
        die(f"baseline packaging-inputs.json has no real rootfs tree: {rootfs_tree}")
    apks: dict[str, dict] = {}
    for name in ("postmarketos-mkinitfs-hook-netboot", "libcamera-rear"):
        entry = inputs.get("apks", {}).get(name)
        if not isinstance(entry, dict):
            die(f"{inputs_path}: no {name} APK pin")
        path = check_pin(resolve_path(entry.get("path")), str(entry.get("sha256", "")),
                         entry.get("size"), f"baseline {name} APK")
        apks[name] = {"path": str(path), "sha256": digest(path), "size": size_of(path),
                      "version": parse_apk(path, name)}
    if apks["postmarketos-mkinitfs-hook-netboot"]["version"] != HOOK_VERSION:
        die(f"baseline netboot hook is not {HOOK_VERSION}")
    if apks["libcamera-rear"]["version"] != LIBRARY_REAR:
        die(f"baseline libcamera-rear is not {LIBRARY_REAR}")
    system_libcamera: dict[str, dict] = {}
    cache = state / "pmbootstrap-work/cache_apk_aarch64"
    for name, (filename, expected_sha, expected_size, version) in SYSTEM_LIBCAMERA_APKS.items():
        path = cache / filename
        check_pin(path, expected_sha, expected_size, f"known-good {name} APK")
        system_libcamera[name] = {"path": str(path), "filename": filename,
                                  "sha256": digest(path), "size": size_of(path),
                                  "version": version}
    library_hashes: dict[str, str] = {}
    for relative in SYSTEM_LIBCAMERA_LIBS:
        known = rootfs_tree / relative
        if not known.is_file() or known.is_symlink():
            die(f"known-good rootfs is missing {relative}: {known}")
        library_hashes[relative] = digest(known)
    return {
        "baseline_build": str(build),
        "baseline_state": str(state),
        "rootfs": str(rootfs_tree),
        "system_libcamera": system_libcamera,
        "system_libcamera_libs": library_hashes,
        "apks": apks,
    }


def source_contract(source_build: Path, source_state: Path, recipe: str,
                    baseline_build: Path, baseline_state: Path,
                    require_boot: bool = False) -> dict:
    """Combined identity consumed by the user-run Plasma rootfs preparation."""
    source_build = Path(source_build).resolve()
    if require_boot or (source_build / "DELIVERY.json").is_file():
        kernel = kernel_delivery_contract(source_build, source_state, recipe)
    else:
        kernel = kernel_inputs_contract(source_build, source_state, recipe)
    baseline = baseline_pins(baseline_build, baseline_state)
    kernel["rootfs"] = baseline["rootfs"]
    kernel["system_libcamera"] = baseline["system_libcamera"]
    kernel["system_libcamera_libs"] = baseline["system_libcamera_libs"]
    kernel["apks"] = baseline["apks"]
    kernel["baseline_build"] = baseline["baseline_build"]
    kernel["baseline_state"] = baseline["baseline_state"]
    kernel["nbd_port"] = NBD_PORT
    return kernel


def official_remote(pmaports: Path) -> list[str]:
    try:
        output = subprocess.check_output(
            ["git", "-C", str(pmaports), "remote", "-v"], text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        die(f"cannot inspect pmaports remotes: {pmaports}")
    urls = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            urls.append(fields[1])
    official = tuple(url.lower() for url in OFFICIAL_PMAPORTS)
    if not any(url.lower() in official for url in urls):
        die(
            "pmaports has no official upstream remote; pmbootstrap needs origin/main. "
            f"Set it to {OFFICIAL_PMAPORTS[0]}"
        )
    return urls


def prepare_state(args: argparse.Namespace) -> dict:
    raw_state = Path(args.state)
    raw_candidate = Path(args.candidate_pmaports)
    if raw_state.is_symlink():
        die(f"candidate state must not be a symlink: {raw_state}")
    if raw_candidate.is_symlink():
        die(f"candidate pmaports must not be a symlink: {raw_candidate}")
    state_root = raw_state.resolve()
    candidate = raw_candidate.resolve()
    source = Path(args.source_pmaports).resolve()
    if not candidate.is_dir():
        die(f"candidate pmaports is not a real directory: {candidate}")
    if candidate == source:
        die("candidate pmaports must not be the read-only source tree")
    cfg = candidate / "pmaports.cfg"
    if not cfg.is_file():
        die(f"candidate pmaports is missing pmaports.cfg: {cfg}")
    text = cfg.read_text(encoding="utf-8")
    if not re.search(r"^version=7$", text, re.M):
        die(f"{cfg}: expected pmaports version=7")
    channel_match = re.search(r"^channel=(.+)$", text, re.M)
    if not channel_match:
        die(f"{cfg}: cannot read channel")
    channel = channel_match.group(1).strip()
    if not channel:
        die(f"{cfg}: empty channel")
    official_remote(candidate)

    links = {
        "device/testing/device-oneplus-guacamole": "device/testing/device-oneplus-guacamole",
        "device/testing/firmware-oneplus-guacamole": "device/testing/firmware-oneplus-guacamole",
        "device/testing/linux-postmarketos-qcom-sm8150": KERNEL_RECIPE,
        "main/hexagonrpcd": "device/testing/hexagonrpcd",
        "main/iio-sensor-proxy": "device/testing/iio-sensor-proxy",
        "main/postmarketos-mkinitfs-hook-netboot":
            "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot",
        "temp/libcamera-rear": "device/testing/rear-camera/libcamera-rear",
        "main/modemmanager": "device/testing/rc3/modemmanager",
    }
    optional_links = {
        "main/nura-clock": "device/testing/nura-clock",
    }
    for relative, target in optional_links.items():
        if (ROOT / target / "APKBUILD").is_file():
            links[relative] = target
    resolved_links: dict[str, str] = {}
    for relative, target in links.items():
        link = candidate / relative
        target_path = (ROOT / target).resolve()
        if not target_path.is_dir() or not (target_path / "APKBUILD").is_file():
            die(f"missing rc3 recipe target: {target_path}/APKBUILD")
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            die(f"{link} is a real path, not the expected local recipe symlink")
        relative_target = os.path.relpath(target_path, link.parent)
        link.symlink_to(relative_target, target_is_directory=True)
        if link.resolve() != target_path:
            die(f"{link} does not resolve to {target_path}")
        resolved_links[relative] = relative_target

    # The system libcamera 0.8 patches live in pmaports/temp/libcamera.  They
    # must be present in the isolated tree; the private 0.7.2 recipe is
    # separate and must never replace this ABI.
    libcamera = candidate / "temp/libcamera"
    if libcamera.is_symlink() or not libcamera.is_dir():
        die(f"candidate pmaports is missing the system libcamera 0.8 recipe: {libcamera}")
    libcamera_apkbuild = libcamera / "APKBUILD"
    if libcamera_apkbuild.is_symlink():
        die(f"system libcamera APKBUILD must not be a symlink: {libcamera_apkbuild}")
    if not libcamera_apkbuild.is_file():
        die(f"missing system libcamera APKBUILD: {libcamera_apkbuild}")
    libcamera_text = libcamera_apkbuild.read_text(encoding="utf-8")
    if "_pkgver=0.8.0" not in libcamera_text or "pkgver=9999$_pkgver" not in libcamera_text:
        die("system libcamera recipe is not the pinned 99990.8.0 source")
    for patch in (
        "0001-libcamera-software_isp-Allow-limiting-output-sizes.patch",
        "0002-libcamera-Add-quad-Bayer-CFA-layout-support.patch",
    ):
        if not (libcamera / patch).is_file():
            die(f"system libcamera recipe is missing {patch}")

    ui = candidate / "main/postmarketos-ui-plasma-mobile"
    ui_apkbuild = ui / "APKBUILD"
    lockfile = ui / "kscreenlockerrc"
    if not ui_apkbuild.is_file() or not lockfile.is_file():
        die(f"candidate pmaports is missing the Plasma UI recipe: {ui}")
    if ui_apkbuild.is_symlink() or lockfile.is_symlink():
        die(f"candidate Plasma UI recipe files must not be symlinks: {ui}")
    ui_text = ui_apkbuild.read_text(encoding="utf-8")
    if "kscreenlockerrc" not in ui_text or 'etc/xdg' not in ui_text:
        die(f"{ui_apkbuild} does not install kscreenlockerrc to /etc/xdg")
    logind_conf = ui / "50-nura-plasma-nbd.conf"
    logind_text = (
        "# Candidate-only NBD trial guard; persists until the candidate state is rebuilt.\n"
        "[Login]\n"
        "IdleAction=ignore\n"
        "IdleActionSec=0\n"
        "HandleSuspendKey=ignore\n"
        "HandleHibernateKey=ignore\n"
    )
    if logind_conf.is_symlink():
        die(f"candidate logind drop-in must not be a symlink: {logind_conf}")
    write_atomic(logind_conf, logind_text)
    if not re.search(r"^[ \t]*50-nura-plasma-nbd\.conf[ \t]*$", ui_text, re.M):
        ui_text, replaced = re.subn(
            r"^([ \t]*kscreenlockerrc[ \t]*)$",
            r"\1\n\t50-nura-plasma-nbd.conf",
            ui_text, count=1, flags=re.M,
        )
        if replaced != 1:
            die(f"{ui_apkbuild}: cannot add the logind drop-in to source=")
    if 'install -Dm644 "$srcdir"/50-nura-plasma-nbd.conf' not in ui_text:
        ui_text, replaced = re.subn(
            r'^([ \t]*-t "\$pkgdir"/etc/xdg[ \t]*)$',
            '\\1\n\tinstall -Dm644 "$srcdir"/50-nura-plasma-nbd.conf '
            '-t "$pkgdir"/etc/systemd/logind.conf.d',
            ui_text, count=1, flags=re.M,
        )
        if replaced != 1:
            die(f"{ui_apkbuild}: cannot add the logind drop-in to package()")
    powerdevil_conf = ui / "powerdevilrc"
    if powerdevil_conf.is_symlink():
        die(f"candidate PowerDevil policy must not be a symlink: {powerdevil_conf}")
    write_atomic(powerdevil_conf, POWERDEVIL_CONFIG)
    if not re.search(r"^[ \t]*powerdevilrc[ \t]*$", ui_text, re.M):
        ui_text, replaced = re.subn(
            r"^([ \t]*kscreenlockerrc[ \t]*)$",
            r"\1\n\tpowerdevilrc",
            ui_text, count=1, flags=re.M,
        )
        if replaced != 1:
            die(f"{ui_apkbuild}: cannot add powerdevilrc to source=")
    if 'install -Dm644 "$srcdir"/powerdevilrc' not in ui_text:
        ui_text, replaced = re.subn(
            r'^([ \t]*-t "\$pkgdir"/etc/xdg[ \t]*)$',
            '\\1\n\tinstall -Dm644 "$srcdir"/powerdevilrc -t "$pkgdir"/etc/xdg',
            ui_text, count=1, flags=re.M,
        )
        if replaced != 1:
            die(f"{ui_apkbuild}: cannot add powerdevilrc to package()")
    lock_text = lockfile.read_text(encoding="utf-8")
    if re.search(r"^LockOnStart=false$", lock_text, re.M):
        pass
    elif re.search(r"^LockOnStart=true$", lock_text, re.M):
        lock_text = re.sub(r"^LockOnStart=true$", "LockOnStart=false", lock_text, flags=re.M)
    else:
        die(f"{lockfile}: cannot find a LockOnStart setting")
    write_atomic(lockfile, lock_text)
    lock_sha = sha512(lockfile)
    logind_sha = sha512(logind_conf)
    powerdevil_sha = sha512(powerdevil_conf)
    lines = ui_text.splitlines(keepends=True)
    lock_lines = [
        index for index, line in enumerate(lines)
        if re.match(r"^[0-9a-f]{128}  kscreenlockerrc$", line.rstrip("\n"))
    ]
    if len(lock_lines) != 1:
        die(f"{ui_apkbuild}: expected exactly one kscreenlockerrc sha512sum entry")
    lines[lock_lines[0]] = f"{lock_sha}  kscreenlockerrc\n"
    for filename, checksum in (
        ("50-nura-plasma-nbd.conf", logind_sha),
        ("powerdevilrc", powerdevil_sha),
    ):
        indices = [
            index for index, line in enumerate(lines)
            if line.rstrip("\n").endswith("  " + filename)
        ]
        if len(indices) > 1:
            die(f"{ui_apkbuild}: duplicate {filename} sha512sum entries")
        if indices:
            lines[indices[0]] = f"{checksum}  {filename}\n"
        else:
            lines.insert(lock_lines[0] + 1, f"{checksum}  {filename}\n")
    write_atomic(ui_apkbuild, "".join(lines))

    source_cfg = Path(args.source_config)
    candidate_cfg = Path(args.candidate_config)
    receipt_path = Path(args.receipt)
    for path, label in ((candidate_cfg, "candidate config"), (receipt_path, "candidate receipt")):
        if path.is_symlink():
            die(f"{label} must not be a symlink: {path}")
        if not str(path.resolve()).startswith(str(state_root) + os.sep):
            die(f"{label} is outside the Plasma state: {path}")
    source_parser = configparser.ConfigParser(interpolation=None)
    try:
        source_parser.read(source_cfg, encoding="utf-8")
    except configparser.Error as error:
        die(f"cannot read {source_cfg}: {error}")
    if "pmbootstrap" not in source_parser:
        die(f"{source_cfg}: missing [pmbootstrap] section")
    previous = source_parser["pmbootstrap"]
    for key, expected in (
        ("device", "oneplus-guacamole"),
        ("service_manager", "systemd"),
        ("ui", "phosh"),
    ):
        if previous.get(key) != expected:
            die(f"{source_cfg}: {key} is {previous.get(key)!r}, expected {expected!r}")
    raw_work = Path(args.work)
    if raw_work.is_symlink():
        die(f"candidate work dir must not be a symlink: {raw_work}")
    work = raw_work.resolve()
    if not str(candidate).startswith(str(state_root) + os.sep):
        die(f"candidate pmaports is outside the Plasma state: {candidate}")
    if not str(work).startswith(str(state_root) + os.sep):
        die(f"candidate work dir is outside the Plasma state: {work}")
    config = configparser.ConfigParser(interpolation=None)
    config["pmbootstrap"] = {
        "aports": str(candidate),
        "build_pkgs_on_install": "false",
        "device": "oneplus-guacamole",
        "extra_packages": "none",
        "hostname": "guacamole",
        "keymap": "us",
        "locale": "en_US.UTF-8",
        "service_manager": "systemd",
        "ssh_keys": "false",
        "timezone": "UTC",
        "ui": "plasma-mobile",
        "user": "user",
        "work": str(work),
    }
    config["providers"] = {}
    config["mirrors"] = {}
    candidate_cfg.parent.mkdir(parents=True, exist_ok=True)
    with candidate_cfg.open("w", encoding="utf-8") as stream:
        config.write(stream, space_around_delimiters=True)
    os.chmod(candidate_cfg, 0o600)

    source_commit = git_rev(source)
    bootstrap_source = Path(args.source_pmbootstrap).resolve()
    bootstrap_candidate = Path(args.candidate_pmbootstrap).resolve()
    if bootstrap_candidate == bootstrap_source:
        die("candidate pmbootstrap must not be the read-only source tree")
    if not (bootstrap_candidate / "pmbootstrap.py").is_file():
        die(f"candidate pmbootstrap is incomplete: {bootstrap_candidate}")
    bootstrap_init = bootstrap_candidate / "pmb/config/__init__.py"
    if not bootstrap_init.is_file() or "work_version = 8" not in bootstrap_init.read_text(encoding="utf-8"):
        die(f"candidate pmbootstrap does not use work_version 8: {bootstrap_init}")
    # This pinned pmbootstrap parses -i but omits it when constructing Context.
    bootstrap_args = bootstrap_candidate / "pmb/helpers/args.py"
    args_text = bootstrap_args.read_text(encoding="utf-8")
    ignore_assignment = '    context.ignore_depends = getattr(args, "ignore_depends", False)\n'
    if ignore_assignment not in args_text:
        anchor = '    context.no_depends = getattr(args, "no_depends", False)\n'
        if args_text.count(anchor) != 1 or "context.ignore_depends =" in args_text:
            die("pmbootstrap argument handling changed; cannot safely repair --ignore-depends")
        bootstrap_args.write_text(args_text.replace(anchor, anchor + ignore_assignment), encoding="utf-8")
    bootstrap_commit = git_rev(bootstrap_source)
    if receipt_path.is_file():
        previous_receipt = read_json(receipt_path)
        if previous_receipt.get("source_pmaports") != str(source):
            die(f"candidate receipt belongs to another pmaports source: {previous_receipt.get('source_pmaports')}")
        if previous_receipt.get("source_commit") not in ("", None, source_commit):
            die("candidate pmaports is stale relative to the pinned source commit; move it aside")
        if previous_receipt.get("source_pmbootstrap") != str(bootstrap_source):
            die("candidate receipt belongs to another pmbootstrap source")
        if previous_receipt.get("source_pmbootstrap_commit") not in ("", None, bootstrap_commit):
            die("candidate pmbootstrap is stale relative to the pinned source commit; move it aside")

    receipt = {
        "profile": PROFILE,
        "release": RELEASE,
        "kernel_release": RELEASE,
        "kernel_recipe": KERNEL_RECIPE,
        "release_series": RELEASE_SERIES,
        "release_candidate": RELEASE_CANDIDATE,
        "artifact_name": ARTIFACT_NAME,
        "created_at": utc_now(),
        "source_pmaports": str(source),
        "source_commit": source_commit,
        "candidate_pmaports": str(candidate),
        "source_pmbootstrap": str(bootstrap_source),
        "source_pmbootstrap_commit": bootstrap_commit,
        "candidate_pmbootstrap": str(Path(args.candidate_pmbootstrap).resolve()),
        "source_config": str(source_cfg.resolve()),
        "candidate_config": str(candidate_cfg.resolve()),
        "work": str(work),
        "channel": channel,
        "lock_on_start": LOCK_ON_START,
        "kscreenlockerrc_sha512": lock_sha,
        "logind_conf_sha512": logind_sha,
        "powerdevil_conf_sha512": powerdevil_sha,
        "ui_apkbuild_sha256": digest(ui_apkbuild),
        "libcamera_apkbuild_sha256": digest(libcamera_apkbuild),
        "local_links": resolved_links,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(receipt_path, json.dumps(receipt, indent=2) + "\n")
    return receipt


def installed_packages(rootfs: Path) -> dict[str, dict]:
    database = rootfs / "lib/apk/db/installed"
    try:
        text = database.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        die(f"cannot read the installed APK database: {database}: {error}")
    packages: dict[str, dict] = {}
    for block in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if len(line) >= 2 and line[1] == ":":
                fields[line[0]] = line[2:]
        if "P" in fields:
            packages[fields["P"]] = fields
    return packages


def verify_installed(args: argparse.Namespace) -> dict:
    release = validate_release(args.release, "installed rootfs")
    kernel_dir = Path(args.kernel_dir).resolve()
    rootfs = Path(args.rootfs).resolve()
    initramfs = Path(args.initramfs).resolve()
    if not rootfs.is_dir():
        die(f"missing installed rootfs tree: {rootfs}")
    release_file = kernel_dir / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
    if not release_file.is_file() or release_file.read_text(encoding="utf-8").strip() != release:
        die(f"staged kernel release is not {release}: {release_file}")
    try:
        check_initramfs(initramfs, release)
    except (ValueError, InitramfsError, OSError, subprocess.CalledProcessError) as error:
        die(f"candidate initramfs is not a usable {release} port-{NBD_PORT} archive: {error}")
    rootfs_initramfs = rootfs / "boot/initramfs"
    if not rootfs_initramfs.is_file():
        die(f"installed rootfs has no boot initramfs: {rootfs_initramfs}")
    if digest(rootfs_initramfs) != digest(initramfs):
        die("installed rootfs boot initramfs differs from the exported candidate initramfs")
    known_good_rootfs = Path(args.known_good_rootfs).resolve()
    if not known_good_rootfs.is_dir():
        die(f"missing known-good rootfs tree: {known_good_rootfs}")
    for relative in SYSTEM_LIBCAMERA_LIBS:
        installed = rootfs / relative
        known = known_good_rootfs / relative
        if not installed.is_file() or not known.is_file():
            die(f"missing system libcamera library: {relative}")
        if digest(installed) != digest(known):
            die(f"installed {relative} differs from the known-good system libcamera")
    if digest(rootfs / "boot/vmlinuz") != args.expected_vmlinuz_sha:
        die("installed vmlinuz differs from the staged kernel package")
    if digest(rootfs / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb") != args.expected_dtb_sha:
        die("installed DTB differs from the staged kernel package")
    try:
        payload = check_module_payload(kernel_dir, rootfs, release)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        die(f"installed module payload differs from the staged kernel: {error}")
    if payload["sha256"] != args.expected_module_payload_sha:
        die("installed module payload identity differs from the rc3 kernel build")
    if payload["count"] != args.expected_module_count:
        die("installed module count differs from the rc3 kernel build")
    hook = rootfs / "usr/share/mkinitfs/hooks/netboot.sh"
    if not hook.is_file() or not re.search(r"^NBD_PORT=10001$", hook.read_text(encoding="utf-8"), re.M):
        die(f"installed netboot hook is not port {NBD_PORT}: {hook}")
    lockfile = rootfs / "etc/xdg/kscreenlockerrc"
    if not lockfile.is_file():
        die(f"installed rootfs has no {lockfile}")
    lock_text = lockfile.read_text(encoding="utf-8")
    if not re.search(r"^LockOnStart=false$", lock_text, re.M) or re.search(
        r"^LockOnStart=true$", lock_text, re.M
    ):
        die("installed kscreenlockerrc does not have the candidate-only LockOnStart=false")
    logind_conf = rootfs / "etc/systemd/logind.conf.d/50-nura-plasma-nbd.conf"
    if not logind_conf.is_file():
        die(f"installed rootfs is missing the candidate NBD logind guard: {logind_conf}")
    if "IdleAction=ignore" not in logind_conf.read_text(encoding="utf-8"):
        die("installed NBD logind guard does not disable the idle action")
    powerdevil_conf = rootfs / "etc/xdg/powerdevilrc"
    if not powerdevil_conf.is_file():
        die(f"installed rootfs is missing the candidate PowerDevil policy: {powerdevil_conf}")
    powerdevil_text = powerdevil_conf.read_text(encoding="utf-8")
    for profile in ("AC", "Battery", "LowBattery"):
        group = f"[{profile}][SuspendAndShutdown]"
        if group not in powerdevil_text or "AutoSuspendAction=0" not in powerdevil_text.split(group, 1)[1].split("[", 1)[0]:
            die(f"PowerDevil policy does not disable AutoSuspendAction for {profile}")
    packages = installed_packages(rootfs)
    required = {
        "postmarketos-ui-plasma-mobile": None,
        "plasma-mobile": None,
        CAMERA_APP: None,
        "libcamera": LIBCAMERA_ABI,
        "libcamera-ipa": LIBCAMERA_ABI,
        "libcamera-rear": LIBRARY_REAR,
        "pipewire-spa-libcamera": None,
        "pulseaudio": None,
        "xwayland": None,
        "postmarketos-base-ui-audio-backend-pulseaudio": None,
        "systemd": None,
        "powerdevil": POWERDEVIL_VERSION,
    }
    if args.modemmanager_version:
        required["modemmanager"] = args.modemmanager_version
        # The recipe builds aarch64 modemmanager/libmm-glib plus noarch
        # systemd/udev/lang subpackages at the same pkgver-rpkgrel.  Install
        # and verify them as one set.
        for name in ("modemmanager-systemd", "modemmanager-udev", "modemmanager-lang"):
            required[name] = args.modemmanager_version
    if args.libmm_glib_version:
        required["libmm-glib"] = args.libmm_glib_version
    if args.nura_clock_version:
        required["nura-clock"] = args.nura_clock_version
    for name, version in required.items():
        if name not in packages:
            die(f"installed rootfs is missing {name}")
        if version is not None and packages[name].get("V") != version:
            die(f"installed {name} is {packages[name].get('V')!r}, expected {version}")
    for forbidden in (
        "snapshot", "kamoso", "postmarketos-ui-phosh", "phosh", "greetd",
        "pipewire-pulse",
    ):
        if forbidden in packages:
            die(f"installed rootfs unexpectedly contains {forbidden}")
    # KWin starts Xwayland on demand; without it PulseAudio's X11 integration
    # can stall the server and Qt camera startup on KWin's listening sockets.
    xwayland = rootfs / "usr/bin/Xwayland"
    if not xwayland.is_file() or not xwayland.stat().st_mode & 0o111:
        die("installed rootfs has no executable /usr/bin/Xwayland")
    # Plasma Camera captures through libcamera directly, so its recorded
    # dependency must be the same pinned 0.8 ABI as the PipeWire SPA plugin.
    camera_depends = packages[CAMERA_APP].get("D", "").split()
    if CAMERA_APP_LIBCAMERA_ABI not in camera_depends:
        die(
            f"installed {CAMERA_APP} does not depend on the pinned system "
            f"libcamera ABI {CAMERA_APP_LIBCAMERA_ABI}"
        )
    camera_binary = rootfs / "usr/bin/plasma-camera"
    if not camera_binary.is_file():
        die(f"installed {CAMERA_APP} has no executable: {camera_binary}")
    camera_desktop = rootfs / "usr/share/applications/org.kde.plasma.camera.desktop"
    if not camera_desktop.is_file():
        die(f"installed {CAMERA_APP} has no KDE desktop file: {camera_desktop}")
    # Video recording hands libcamera frames to QMediaRecorder, which needs a
    # Qt Multimedia backend plugin.  Either the default FFmpeg one or the
    # GStreamer one is sufficient; both are installed by the candidate today.
    qt_backends = (
        rootfs / "usr/lib/qt6/plugins/multimedia/libffmpegmediaplugin.so",
        rootfs / "usr/lib/qt6/plugins/multimedia/libgstreamermediaplugin.so",
    )
    if not any(path.is_file() for path in qt_backends):
        die(
            "installed rootfs has no Qt Multimedia backend plugin for "
            f"{CAMERA_APP} video recording"
        )
    if packages["postmarketos-ui-plasma-mobile"].get("V") != args.ui_version:
        die(
            "installed postmarketos-ui-plasma-mobile is "
            f"{packages['postmarketos-ui-plasma-mobile'].get('V')!r}, expected {args.ui_version}"
        )
    display_alias = rootfs / "etc/systemd/system/display-manager.service"
    graphical_alias = rootfs / "etc/systemd/system/graphical.target.wants/display-manager.service"
    if display_alias.is_symlink() and os.readlink(display_alias).endswith("plasma-mobile.service"):
        pass
    elif graphical_alias.is_symlink() and os.readlink(graphical_alias).endswith("plasma-mobile.service"):
        pass
    else:
        die("display-manager.service is not a plasma-mobile.service alias (or graphical.target alias)")
    # The clock-epoch floor must be a real installed file with a reproducible
    # date derived from source mtime/SOURCE_DATE_EPOCH, never an RTC write.
    clock_epoch = rootfs / CLOCK_EPOCH
    if not clock_epoch.is_file() or clock_epoch.stat().st_size == 0:
        die(f"installed rootfs is missing the reproducible clock floor: {clock_epoch}")
    clock_text = clock_epoch.read_text(encoding="utf-8", errors="replace").strip()
    if not re.fullmatch(r"[0-9]{10}", clock_text):
        die(f"{clock_epoch} is not a Unix-seconds date: {clock_text!r}")
    # systemd advances CLOCK_REALTIME only to the file's *mtime*.  The content
    # is an inert source-date marker, so the installed mtime must not be older
    # than that lower bound (abuild stamps payload files from SOURCE_DATE_EPOCH).
    if int(clock_epoch.stat().st_mtime) < int(clock_text):
        die(
            f"{clock_epoch} mtime {int(clock_epoch.stat().st_mtime)} is older than "
            f"its declared lower bound {clock_text}; the systemd clock floor would be ineffective"
        )
    if args.modemmanager_version:
        modem_depends = packages["modemmanager"].get("D", "").split()
        if "libmm-glib" not in " ".join(modem_depends):
            die("installed modemmanager does not depend on the matching libmm-glib")
    return {
        "release": release,
        "release_series": RELEASE_SERIES,
        "release_candidate": RELEASE_CANDIDATE,
        "artifact_name": ARTIFACT_NAME,
        "rootfs": str(rootfs),
        "rootfs_initramfs": str(initramfs),
        "rootfs_initramfs_sha256": digest(initramfs),
        "module_payload_sha256": payload["sha256"],
        "module_count": payload["count"],
        "ui_version": packages["postmarketos-ui-plasma-mobile"].get("V"),
        "camera_app": CAMERA_APP,
        "camera_app_version": packages[CAMERA_APP].get("V"),
        "libcamera_version": packages["libcamera"].get("V"),
        "powerdevil_version": packages["powerdevil"].get("V"),
        "modemmanager_version": packages.get("modemmanager", {}).get("V"),
        "libmm_glib_version": packages.get("libmm-glib", {}).get("V"),
        "nura_clock_version": packages.get("nura-clock", {}).get("V"),
        "clock_epoch": clock_text,
        "lock_on_start": False,
    }


def manifest(args: argparse.Namespace) -> dict:
    source = source_contract(
        Path(args.source_build), Path(args.source_state), KERNEL_RECIPE,
        Path(args.baseline_build), Path(args.baseline_state), require_boot=True,
    )
    raw_paths = {
        "kernel-package": Path(args.kernel_dir),
        "Image": Path(args.image),
        "boot.img": Path(args.boot_img),
        "boot-initramfs": Path(args.boot_initramfs),
        "rootfs-initramfs": Path(args.rootfs_initramfs),
        "rootfs image": Path(args.rootfs_image),
    }
    for label, path in raw_paths.items():
        if path.is_symlink():
            die(f"candidate {label} must be a real path, not a symlink: {path}")
        if label == "kernel-package":
            if not path.is_dir():
                die(f"missing candidate kernel-package directory: {path}")
        elif not path.is_file():
            die(f"missing candidate {label}: {path}")
    raw_out = Path(args.out)
    if raw_out.is_symlink():
        die(f"candidate output must not be a symlink: {raw_out}")
    raw_temporary = Path(args.temporary_dtbo)
    if raw_temporary.is_symlink():
        die(f"candidate temporary-dtbo must not be a symlink: {raw_temporary}")
    raw_apk_dir = Path(args.apk_dir)
    if raw_apk_dir.is_symlink():
        die(f"candidate APK directory must not be a symlink: {raw_apk_dir}")
    kernel_dir = raw_paths["kernel-package"].resolve()
    image = raw_paths["Image"].resolve()
    boot_img = raw_paths["boot.img"].resolve()
    boot_initramfs = raw_paths["boot-initramfs"].resolve()
    rootfs_initramfs = raw_paths["rootfs-initramfs"].resolve()
    rootfs_image = raw_paths["rootfs image"].resolve()
    apk_dir = raw_apk_dir.resolve()
    out = raw_out.resolve()
    temporary_dtbo = raw_temporary.resolve()
    rootfs_tree = Path(args.rootfs_tree).resolve()
    if out.exists() and not out.is_dir():
        die(f"candidate output is not a directory: {out}")

    # The boot image must be the exact file the rc3 kernel build packed.
    if digest(boot_img) != source["boot_img_sha256"]:
        die("candidate boot.img is not the rc3 kernel build's packed boot image")
    if digest(image) != source["image_sha256"]:
        die("candidate Image differs from the rc3 kernel build")
    if digest(boot_initramfs) != source["initramfs_sha256"]:
        die("candidate boot initramfs is not the rc3 rootfs export initramfs")
    if digest(rootfs_initramfs) != digest(boot_initramfs):
        die("candidate rootfs initramfs and boot-initramfs must be the same exported archive")
    try:
        check_initramfs(boot_initramfs, RELEASE)
    except (ValueError, InitramfsError, OSError, subprocess.CalledProcessError) as error:
        die(f"candidate initramfs is not functionally usable: {error}")
    rootfs_boot_initramfs = rootfs_tree / "boot/initramfs"
    if not rootfs_boot_initramfs.is_file():
        die(f"candidate rootfs has no boot initramfs: {rootfs_boot_initramfs}")
    if digest(rootfs_boot_initramfs) != digest(rootfs_initramfs):
        die("candidate rootfs boot initramfs differs from the exported rootfs initramfs")
    vmlinuz = kernel_dir / "boot/vmlinuz"
    dtb = kernel_dir / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
    cci = kernel_dir / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
    if digest(vmlinuz) != source["vmlinuz_sha256"]:
        die("candidate kernel-package vmlinuz differs from the rc3 kernel build")
    if digest(dtb) != source["dtb_sha256"]:
        die("candidate kernel-package DTB differs from the rc3 kernel build")
    if not cci.is_file():
        die(f"candidate kernel-package is missing the CCI module: {cci}")
    if digest(cci) != source["cci_sha256"]:
        die("candidate kernel-package CCI module differs from the rc3 kernel build")
    payload = module_payload(kernel_dir, RELEASE)
    if payload["sha256"] != source["module_payload_sha256"] or payload["count"] != source["module_count"]:
        die("candidate kernel-package module payload differs from the rc3 kernel build")
    wrapper_manifest = read_json(temporary_dtbo / "manifest.json")
    try:
        wrapper_base = wrapper_manifest["inputs"]["base_dtb"]["sha256"]
        wrapper_sha = wrapper_manifest["artifacts"]["wrapper"]["sha256"]
    except (KeyError, TypeError):
        die(f"{args.temporary_dtbo}/manifest.json: missing inputs.base_dtb.sha256")
    if wrapper_base != source["dtb_sha256"]:
        die("candidate temporary-dtbo wrapper was not built from the candidate DTB")
    wrapper_path = temporary_dtbo / "custom_guacamole-abl-quarantine.dtb"
    if digest(wrapper_path) != source["temporary_dtbo_sha256"]:
        die("candidate temporary-dtbo wrapper differs from the rc3 kernel build")
    if digest(boot_img) == source["boot_img_sha256"]:
        prove_boot_image(boot_img, image, boot_initramfs, wrapper_path, label="candidate boot image")

    apks: dict[str, dict] = {}
    versions: dict[str, str] = {}
    required_apks = (
        "linux-postmarketos-qcom-sm8150",
        "postmarketos-mkinitfs-hook-netboot",
        "libcamera-rear",
        "postmarketos-ui-plasma-mobile",
        "libcamera",
        "libcamera-ipa",
        "modemmanager",
        "libmm-glib",
    )
    if args.nura_clock_version:
        required_apks += ("nura-clock",)
    for name in required_apks:
        matches = sorted(apk_dir.glob(f"{name}-[0-9]*.apk"))
        if len(matches) != 1:
            die(f"expected exactly one built {name} APK in {apk_dir}, found {len(matches)}")
        path = matches[0]
        if path.is_symlink():
            die(f"built APK must not be a symlink: {path}")
        version = parse_apk(path, name)
        apks[name] = {
            "path": str(path.resolve()),
            "sha256": digest(path),
            "size": size_of(path),
            "version": version,
        }
        versions[name] = version
    for name in ("postmarketos-mkinitfs-hook-netboot", "libcamera-rear"):
        if apks[name]["sha256"] != source["apks"][name]["sha256"]:
            die(f"candidate {name} APK differs from the pinned baseline APK")
    if versions["postmarketos-mkinitfs-hook-netboot"] != HOOK_VERSION:
        die(f"candidate netboot hook APK is not {HOOK_VERSION}")
    if versions["libcamera-rear"] != LIBRARY_REAR:
        die(f"candidate libcamera-rear APK is not {LIBRARY_REAR}")
    for name in ("libcamera", "libcamera-ipa"):
        original = source["system_libcamera"][name]
        if apk_payload(Path(apks[name]["path"])) != apk_payload(Path(original["path"])):
            die(f"candidate {name} APK payload differs from the known-good cached APK")
        apks[name]["original_sha256"] = original["sha256"]
        if versions[name] != LIBCAMERA_ABI:
            die(f"candidate {name} APK is not {LIBCAMERA_ABI}")
    if versions["modemmanager"] != args.modemmanager_version:
        die(f"built modemmanager is {versions['modemmanager']!r}, expected {args.modemmanager_version!r}")
    if versions["libmm-glib"] != args.libmm_glib_version:
        die(f"built libmm-glib is {versions['libmm-glib']!r}, expected {args.libmm_glib_version!r}")
    if args.nura_clock_version and versions["nura-clock"] != args.nura_clock_version:
        die(f"built nura-clock is {versions['nura-clock']!r}, expected {args.nura_clock_version!r}")
    kernel_apk_version = versions["linux-postmarketos-qcom-sm8150"]
    if not (kernel_apk_version.startswith("7.0.0") or kernel_apk_version.startswith("7.0.0_")):
        die(f"candidate kernel APK is not a 7.0.0 build: {kernel_apk_version}")

    created = utc_now()
    rootfs_sha = digest(rootfs_image)
    packages = installed_packages(rootfs_tree)
    packaging_inputs = {
        "release": RELEASE,
        "kernel_release": RELEASE,
        "release_series": RELEASE_SERIES,
        "release_candidate": RELEASE_CANDIDATE,
        "artifact_name": ARTIFACT_NAME,
        "profile": PROFILE,
        "ui": PROFILE,
        "nbd_port": NBD_PORT,
        "lock_on_start": LOCK_ON_START,
        "logind_idle_action": "ignore",
        "powerdevil_auto_suspend": "NoAction",
        "audio_backend": "pulseaudio",
        "created_at": created,
        "kernel_build": str(Path(args.source_build).resolve()),
        "kernel_inputs_sha256": source["kernel_inputs_sha256"],
        "kernel_recipe": KERNEL_RECIPE,
        "initramfs": str(boot_initramfs),
        "initramfs_sha256": digest(boot_initramfs),
        "rootfs_initramfs": str(rootfs_initramfs),
        "rootfs_initramfs_sha256": digest(rootfs_initramfs),
        "rootfs_initramfs_size": size_of(rootfs_initramfs),
        "vmlinuz_sha256": digest(vmlinuz),
        "module_payload_sha256": payload["sha256"],
        "module_count": payload["count"],
        "rootfs": str(rootfs_tree),
        "rootfs_image": str(rootfs_image),
        "rootfs_image_sha256": rootfs_sha,
        "apks": apks,
        "system_libcamera": source["system_libcamera"],
        "system_libcamera_libs": source["system_libcamera_libs"],
        "camera_app": CAMERA_APP,
        "camera_app_version": packages.get(CAMERA_APP, {}).get("V", ""),
        "libcamera_version": packages.get("libcamera", {}).get("V", ""),
        "powerdevil_version": packages.get("powerdevil", {}).get("V", ""),
        "modemmanager_version": args.modemmanager_version,
        "libmm_glib_version": args.libmm_glib_version,
        "nura_clock_version": args.nura_clock_version or None,
        "clock_epoch": (rootfs_tree / CLOCK_EPOCH).read_text(encoding="utf-8").strip()
        if (rootfs_tree / CLOCK_EPOCH).is_file() else "",
        "copied_from": relative_to_root(boot_img),
        "source_boot_sha256": source["boot_img_sha256"],
    }
    delivery = {
        "release": RELEASE,
        "kernel_release": RELEASE,
        "release_series": RELEASE_SERIES,
        "release_candidate": RELEASE_CANDIDATE,
        "artifact_name": ARTIFACT_NAME,
        "worktree": str(ROOT),
        "created_at": created,
        "git_head": git_head(),
        "profile": PROFILE,
        "ui": PROFILE,
        "nbd_port": NBD_PORT,
        "kernel_build": str(Path(args.source_build).resolve()),
        "kernel_inputs_sha256": source["kernel_inputs_sha256"],
        "kernel_recipe": KERNEL_RECIPE,
        "copied_from": relative_to_root(boot_img),
        "source_boot_sha256": source["boot_img_sha256"],
        "boot_img": entry_for(boot_img),
        "image": entry_for(image),
        "kernel_package": entry_for(vmlinuz),
        "dtb": entry_for(dtb),
        "initramfs": entry_for(boot_initramfs),
        "rootfs_initramfs": entry_for(rootfs_initramfs),
        "rootfs_image": {
            "path": str(rootfs_image),
            "sha256": rootfs_sha,
            "size": size_of(rootfs_image),
        },
        "i2c_qcom_cci": entry_for(cci),
        "system_libcamera": source["system_libcamera"],
        "system_libcamera_libs": source["system_libcamera_libs"],
    }
    out.mkdir(parents=True, exist_ok=True)
    write_atomic(out / "packaging-inputs.json", json.dumps(packaging_inputs, indent=2) + "\n")
    write_atomic(out / "DELIVERY.json", json.dumps(delivery, indent=2) + "\n")
    sums = []
    for path in sorted(apk_dir.glob("*.apk")):
        sums.append(f"{digest(path)}  {path.name}")
    write_atomic(out / "APK-SHA256SUMS", "\n".join(sums) + ("\n" if sums else ""))
    return delivery


def _kernel_staging_identity(build: Path, state: Path, recipe: str) -> tuple[dict, Path]:
    """Compute (do not read) the rc3 kernel identity from the staged build."""
    build = Path(build).resolve()
    state = Path(state).resolve()
    if not inside_root(build) or not inside_root(state):
        die(f"kernel build/state must stay inside this checkout: {build}, {state}")
    recipe_path = (ROOT / recipe).resolve()
    apkbuild = recipe_path / "APKBUILD"
    config = recipe_path / "config-postmarketos-qcom-sm8150.aarch64"
    for path in (apkbuild, config):
        if path.is_symlink() or not path.is_file():
            die(f"missing rc3 kernel recipe file: {path}")
    kernel_dir = build / "kernel-package"
    image = build / "Image"
    vmlinuz = kernel_dir / "boot/vmlinuz"
    dtb = kernel_dir / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
    release_file = kernel_dir / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
    if release_file.is_symlink() or not release_file.is_file():
        die(f"rootless kernel build did not produce {release_file}")
    if release_file.read_text(encoding="utf-8").strip() != RELEASE:
        die(f"rootless kernel build release is not {RELEASE}: {release_file}")
    cci = kernel_dir / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
    for path in (image, vmlinuz, dtb, cci):
        if path.is_symlink() or not path.is_file():
            die(f"rootless kernel build did not produce {path}")
    try:
        payload = module_payload(kernel_dir, RELEASE)
    except (ValueError, OSError) as error:
        die(f"cannot identify the staged module payload: {error}")
    identity = {
        "release": RELEASE,
        "recipe": recipe,
        "recipe_apkbuild_sha256": digest(apkbuild),
        "recipe_config_sha256": digest(config),
        "kernel_build": str(build),
        "kernel_state": str(state),
        "image": {"path": str(image), "sha256": digest(image), "size": size_of(image)},
        "vmlinuz": {"path": str(vmlinuz), "sha256": digest(vmlinuz), "size": size_of(vmlinuz)},
        "dtb": {"path": str(dtb), "sha256": digest(dtb), "size": size_of(dtb)},
        "cci_module": {"path": str(cci), "sha256": digest(cci), "size": size_of(cci)},
        "module_payload_sha256": payload["sha256"],
        "module_count": payload["count"],
    }
    return identity, kernel_dir


def _move_stale_kernel_delivery(build: Path, reason: str) -> Path | None:
    build = Path(build).resolve()
    names = ("boot.img", "DELIVERY.json", "packaging-inputs.json")
    existing = [name for name in names if (build / name).exists()]
    if not existing:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = build / f"previous-{stamp}"
    suffix = 1
    while target.exists():
        suffix += 1
        target = build / f"previous-{stamp}-{suffix}"
    target.mkdir()
    for name in existing:
        path = build / name
        if path.is_symlink() or not path.is_file():
            die(f"stale kernel delivery path is not a regular file: {path}")
        os.replace(path, target / name)
    (target / "STALE.txt").write_text(
        f"Moved aside because {reason}.\n"
        "These artifacts were not used for the new kernel build.\n",
        encoding="utf-8",
    )
    print(f"Preserved stale rc3 kernel delivery in {target} ({reason})")
    return target


def kernel_record(args: argparse.Namespace) -> dict:
    """Compute build/rc3-kernel/kernel-inputs.json from the staged kernel.

    Every hash comes from the files this rootless build produced.  If a
    previous delivery exists and the new identity differs, its boot image and
    manifests are moved aside so a stale boot cannot be mistaken for this one.
    """
    build = Path(args.build).resolve()
    state = Path(args.state).resolve()
    recipe = args.recipe
    identity, kernel_dir = _kernel_staging_identity(build, state, recipe)
    identity["worktree"] = str(ROOT)
    # Do not add created_at/git_head here: this file is hashed into the boot
    # delivery, so identical staging must produce identical bytes on a rerun.
    inputs_path = build / "kernel-inputs.json"
    text = json.dumps(identity, indent=2) + "\n"
    previous = read_json(inputs_path) if inputs_path.is_file() else None
    if previous is not None:
        previous_identity = {key: previous.get(key) for key in (
            "release", "recipe", "recipe_apkbuild_sha256", "recipe_config_sha256",
            "image", "vmlinuz", "dtb", "cci_module", "module_payload_sha256",
            "module_count")}
        current_identity = {key: identity.get(key) for key in previous_identity}
        if previous_identity != current_identity:
            _move_stale_kernel_delivery(build, "the staged kernel identity changed")
    elif (build / "DELIVERY.json").exists():
        _move_stale_kernel_delivery(build, "kernel-inputs.json was missing")
    write_atomic(inputs_path, text)
    delivered = build / "DELIVERY.json"
    if delivered.is_file():
        recorded = read_json(delivered).get("kernel_inputs_sha256")
        if recorded != digest(inputs_path):
            _move_stale_kernel_delivery(build, "the new kernel-inputs.json no longer matches the old delivery")
    return {**identity, "kernel_inputs": str(inputs_path),
            "kernel_inputs_sha256": digest(inputs_path)}


def kernel_manifest(args: argparse.Namespace) -> dict:
    """Write the rc3 kernel DELIVERY.json after the rootfs export and boot pack."""
    build = Path(args.build).resolve()
    state = Path(args.state).resolve()
    recipe = args.recipe
    base = kernel_inputs_contract(build, state, recipe)
    boot_img = Path(args.boot_img).resolve()
    initramfs = Path(args.initramfs).resolve()
    rootfs_image = Path(args.rootfs_image).resolve()
    rootfs_tree = Path(args.rootfs_tree).resolve()
    for label, path in (("boot image", boot_img), ("initramfs", initramfs),
                        ("rootfs image", rootfs_image)):
        if path.is_symlink() or not path.is_file():
            die(f"missing rc3 {label}: {path}")
    if not rootfs_tree.is_dir() or rootfs_tree.is_symlink():
        die(f"missing rc3 rootfs tree: {rootfs_tree}")
    try:
        check_initramfs(initramfs, RELEASE)
    except (ValueError, InitramfsError, OSError, subprocess.CalledProcessError) as error:
        die(f"rc3 initramfs is not a usable {RELEASE} port-{NBD_PORT} archive: {error}")
    wrapper = build / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb"
    wrapper_manifest = read_json(build / "temporary-dtbo/manifest.json")
    if wrapper_manifest.get("inputs", {}).get("base_dtb", {}).get("sha256") != base["dtb_sha256"]:
        die("rc3 boot wrapper was not built from the staged DTB")
    if wrapper_manifest.get("artifacts", {}).get("wrapper", {}).get("sha256") != digest(wrapper):
        die("rc3 boot wrapper hash differs from its manifest")
    prove_boot_image(boot_img, Path(base["image"]), initramfs, wrapper, label="rc3 boot image")
    created = utc_now()
    kernel_dir = Path(base["kernel_package_dir"])
    delivery = {
        "release": RELEASE,
        "worktree": str(ROOT),
        "created_at": created,
        "git_head": git_head(),
        "kernel_recipe": recipe,
        "kernel_inputs_sha256": base["kernel_inputs_sha256"],
        "boot_img": entry_for(boot_img),
        "image": entry_for(Path(base["image"])),
        "kernel_package": entry_for(kernel_dir / "boot/vmlinuz"),
        "dtb": entry_for(kernel_dir / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"),
        "initramfs": entry_for(initramfs),
        "rootfs_image": entry_for(rootfs_image),
        "i2c_qcom_cci": entry_for(Path(base["inputs"]["cci_module"]["path"])),
        "module_payload_sha256": base["module_payload_sha256"],
        "module_count": base["module_count"],
    }
    apks: dict[str, dict] = {}
    apk_dir = Path(args.apk_dir).resolve()
    for name in ("linux-postmarketos-qcom-sm8150",
                 "postmarketos-mkinitfs-hook-netboot",
                 "libcamera-rear", "modemmanager", "libmm-glib"):
        matches = sorted(apk_dir.glob(f"{name}-[0-9]*.apk"))
        if len(matches) != 1:
            die(f"expected exactly one built {name} APK in {apk_dir}, found {len(matches)}")
        path = matches[0]
        apks[name] = {"path": str(path), "sha256": digest(path), "size": size_of(path),
                      "version": parse_apk(path, name)}
    inputs = {
        "release": RELEASE,
        "kernel_release": RELEASE,
        "nbd_port": NBD_PORT,
        "kernel_recipe": recipe,
        "initramfs": str(initramfs),
        "initramfs_sha256": digest(initramfs),
        "vmlinuz_sha256": base["vmlinuz_sha256"],
        "module_payload_sha256": base["module_payload_sha256"],
        "module_count": base["module_count"],
        "rootfs": str(rootfs_tree),
        "rootfs_image": str(rootfs_image),
        "rootfs_image_sha256": digest(rootfs_image),
        "apks": apks,
    }
    write_atomic(build / "DELIVERY.json", json.dumps(delivery, indent=2) + "\n")
    write_atomic(build / "packaging-inputs.json", json.dumps(inputs, indent=2) + "\n")
    return delivery


def check_candidate(args: argparse.Namespace) -> dict:
    """Read-only consistency check for a completed Plasma candidate."""
    build = Path(args.build).resolve()
    state = Path(args.state).resolve()
    manifest_path = build / "DELIVERY.json"
    inputs_path = build / "packaging-inputs.json"
    delivery = read_json(manifest_path)
    inputs = read_json(inputs_path)
    release = validate_release(delivery.get("release"), "candidate manifest")
    if release != RELEASE:
        die(f"candidate manifest release {release} is not {RELEASE}")
    if delivery.get("release_candidate") != RELEASE_CANDIDATE:
        die(f"candidate manifest label {delivery.get('release_candidate')!r} is not {RELEASE_CANDIDATE}")
    if delivery.get("kernel_release") != RELEASE or inputs.get("kernel_release") != RELEASE:
        die("candidate manifests do not record the rc3 kernel release")
    if delivery.get("worktree") and Path(delivery["worktree"]).resolve() != ROOT.resolve():
        die("candidate manifest belongs to another worktree")
    if not delivery.get("kernel_build"):
        die("candidate manifest does not record the rc3 kernel build directory")
    source = source_contract(
        Path(delivery["kernel_build"]), Path(args.kernel_state), KERNEL_RECIPE,
        BASELINE_BUILD, BASELINE_STATE, require_boot=True,
    )
    if delivery.get("kernel_inputs_sha256") != source["kernel_inputs_sha256"]:
        die("candidate manifest kernel_inputs_sha256 differs from the kernel build")
    for key in ("boot_img", "image", "kernel_package", "dtb", "initramfs",
                "rootfs_initramfs", "i2c_qcom_cci"):
        pinned_file(delivery.get(key), f"candidate {key}")
    rootfs_entry = inputs.get("rootfs_image")
    if isinstance(rootfs_entry, dict):
        pinned_file(rootfs_entry, "candidate rootfs image")
    else:
        rootfs_path = resolve_path(rootfs_entry)
        if not inside_root(rootfs_path) or not rootfs_path.is_file():
            die(f"candidate rootfs image is missing: {rootfs_path}")
        if digest(rootfs_path) != inputs.get("rootfs_image_sha256"):
            die("candidate rootfs image differs from packaging-inputs.json")
    if inputs.get("release") != RELEASE or inputs.get("nbd_port") != NBD_PORT:
        die("candidate packaging-inputs.json does not record the rc3 release/port")
    if inputs.get("kernel_build") and Path(inputs["kernel_build"]).resolve() != Path(delivery.get("kernel_build")).resolve():
        die("candidate packaging inputs and manifest disagree about the kernel build")
    if inputs.get("vmlinuz_sha256") != source["vmlinuz_sha256"]:
        die("candidate vmlinuz differs from the rc3 kernel build")
    if inputs.get("module_payload_sha256") != source["module_payload_sha256"]:
        die("candidate module payload differs from the rc3 kernel build")
    if inputs.get("module_count") != source["module_count"]:
        die("candidate module count differs from the rc3 kernel build")
    if digest(Path(delivery["initramfs"]["path"])) != source["initramfs_sha256"]:
        die("candidate boot initramfs differs from the rc3 boot image")
    wrapper = build / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb"
    prove_boot_image(Path(delivery["boot_img"]["path"]), Path(delivery["image"]["path"]),
                     Path(delivery["initramfs"]["path"]), wrapper, label="candidate boot image")
    try:
        check_initramfs(Path(delivery["initramfs"]["path"]), RELEASE)
    except (ValueError, InitramfsError, OSError, subprocess.CalledProcessError) as error:
        die(f"candidate initramfs is not a usable {RELEASE} port-{NBD_PORT} archive: {error}")
    apks = inputs.get("apks", {})
    for name in ("linux-postmarketos-qcom-sm8150", "postmarketos-mkinitfs-hook-netboot",
                 "libcamera-rear", "postmarketos-ui-plasma-mobile", "libcamera",
                 "libcamera-ipa", "modemmanager", "libmm-glib"):
        entry = apks.get(name)
        if not isinstance(entry, dict):
            die(f"candidate packaging inputs have no {name} APK")
        pinned_file(entry, f"candidate {name} APK")
    if apks["libcamera"]["version"] != LIBCAMERA_ABI or apks["libcamera-ipa"]["version"] != LIBCAMERA_ABI:
        die("candidate system libcamera APKs are not the pinned 0.8 ABI")
    if apks["postmarketos-mkinitfs-hook-netboot"]["version"] != HOOK_VERSION:
        die(f"candidate netboot hook is not {HOOK_VERSION}")
    if apks["libcamera-rear"]["version"] != LIBRARY_REAR:
        die(f"candidate libcamera-rear is not {LIBRARY_REAR}")
    rootfs_tree = Path(inputs.get("rootfs", ""))
    if rootfs_tree.is_dir() and not rootfs_tree.is_symlink():
        verify_installed(argparse.Namespace(
            release=RELEASE,
            kernel_dir=Path(delivery["kernel_package"]["path"]).parent.parent,
            rootfs=str(rootfs_tree),
            initramfs=delivery["rootfs_initramfs"]["path"],
            known_good_rootfs=source["rootfs"],
            expected_vmlinuz_sha=source["vmlinuz_sha256"],
            expected_dtb_sha=source["dtb_sha256"],
            expected_module_payload_sha=source["module_payload_sha256"],
            expected_module_count=source["module_count"],
            ui_version=apks["postmarketos-ui-plasma-mobile"]["version"],
            modemmanager_version=apks["modemmanager"]["version"],
            libmm_glib_version=apks["libmm-glib"]["version"],
            nura_clock_version=apks.get("nura-clock", {}).get("version"),
        ))
    return {"release": release, "release_candidate": RELEASE_CANDIDATE,
            "kernel_build": delivery.get("kernel_build"),
            "boot_img_sha256": delivery["boot_img"]["sha256"],
            "rootfs_image_sha256": delivery["rootfs_image"]["sha256"],
            "installed_rootfs_checked": rootfs_tree.is_dir()}


def entry_for(path: Path) -> dict:
    return {"path": str(Path(path).resolve()), "sha256": digest(path), "size": size_of(path)}


# Explicit --authorized-key support.  The helper never reads, prints or writes
# private key material: an input line must be a plain OpenSSH public key.  The
# default build passes no key at all.
PUBLIC_KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ssh-dss",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)
PUBLIC_KEY_MAX_BYTES = 16384


def public_key_line(line: str, source: Path, number: int) -> str:
    """Validate one plain OpenSSH public key line and return it normalized."""
    fields = line.split()
    if len(fields) < 2:
        die(f"{source}:{number}: not an OpenSSH public key")
    key_type, encoded = fields[0], fields[1]
    if key_type not in PUBLIC_KEY_TYPES:
        die(
            f"{source}:{number}: unsupported public key type {key_type!r} "
            "(authorized_keys options are not accepted)"
        )
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        die(f"{source}:{number}: invalid base64 public key material")
    if len(blob) < 4:
        die(f"{source}:{number}: public key material is truncated")
    length = int.from_bytes(blob[:4], "big")
    if length <= 0 or length > 64 or blob[4:4 + length] != key_type.encode("ascii"):
        die(f"{source}:{number}: public key material does not match its type")
    # ed25519 has 32 key bytes; RSA/ECDSA blobs are longer.  A short tail means
    # a truncated or hand-made line that sshd would reject later.
    if len(blob) < 4 + length + 32:
        die(f"{source}:{number}: public key material is truncated")
    comment = " ".join(fields[2:])
    return " ".join(part for part in (key_type, encoded, comment) if part)


def read_public_keys(path: Path) -> tuple[list[str], int]:
    path = Path(path)
    if path.is_symlink():
        die(f"authorized-key file must not be a symlink: {path}")
    if not path.is_file():
        die(f"authorized-key file is not a regular file: {path}")
    try:
        if path.stat().st_size > PUBLIC_KEY_MAX_BYTES:
            die(f"authorized-key file is unexpectedly large: {path}")
        raw = path.read_bytes()
    except OSError as error:
        die(f"cannot read authorized-key file {path}: {error}")
    if b"PRIVATE KEY" in raw or b"OPENSSH PRIVATE" in raw:
        die(f"refusing a private key file: {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        die(f"authorized-key file is not UTF-8 text: {path}")
    kept: list[str] = []
    key_count = 0
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            kept.append(stripped)
            continue
        if "PRIVATE KEY" in stripped:
            die(f"refusing private key material at {path}:{number}")
        kept.append(public_key_line(stripped, path, number))
        key_count += 1
    if not key_count:
        die(f"authorized-key file contains no public keys: {path}")
    return kept, key_count


def authorized_key(args: argparse.Namespace) -> dict:
    lines, key_count = read_public_keys(Path(args.public_key))
    output = Path(args.output)
    if output.is_symlink():
        die(f"authorized-key output must not be a symlink: {output}")
    if output.is_dir():
        die(f"authorized-key output is a directory: {output}")
    write_atomic(output, "\n".join(lines) + "\n", mode=0o600)
    return {
        "key_type": next(line.split()[0] for line in lines if not line.startswith("#")),
        "key_count": key_count,
        "output": str(output),
    }


def authorized_key_config(args: argparse.Namespace) -> dict:
    source = Path(args.source_config)
    output = Path(args.output_config)
    keys = Path(args.authorized_keys)
    if source.is_symlink():
        die(f"source pmbootstrap config must not be a symlink: {source}")
    if output.is_symlink():
        die(f"authorized-key config must not be a symlink: {output}")
    if output.resolve() == source.resolve():
        die("authorized-key config must not replace the candidate config")
    if not source.is_file():
        die(f"candidate pmbootstrap config is missing: {source}")
    if keys.is_symlink() or not keys.is_file():
        die(f"validated authorized-key file is missing: {keys}")
    keys_text = str(keys)
    if not keys.is_absolute():
        die(f"authorized-key path must be absolute: {keys_text}")
    if any(character in keys_text for character in "%*?[]\n\r"):
        die(f"authorized-key path is not a safe glob: {keys_text}")
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        die(f"cannot read candidate pmbootstrap config {source}: {error}")
    header = None
    end = len(lines)
    for number, line in enumerate(lines):
        if line.strip() == "[pmbootstrap]":
            if header is not None:
                die(f"{source}: duplicate [pmbootstrap] section")
            header = number
        elif header is not None and line.strip().startswith("[") and line.strip().endswith("]"):
            end = number
            break
    if header is None:
        die(f"{source}: no [pmbootstrap] section")
    body = []
    user = None
    for line in lines[header + 1:end]:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith(("#", ";")) else None
        if key in ("ssh_keys", "ssh_key_glob"):
            continue
        if key == "user" and user is None:
            user = stripped.split("=", 1)[1].strip()
        body.append(line)
    if user is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", user):
        die(f"{source}: invalid build user {user!r}")
    updated = (
        lines[:header + 1]
        + ["ssh_keys = true", f"ssh_key_glob = {keys_text}"]
        + body
        + lines[end:]
    )
    content = "\n".join(updated) + "\n"
    parser = configparser.ConfigParser()
    try:
        parser.read_string(content)
    except configparser.Error as error:
        die(f"generated pmbootstrap config is invalid: {error}")
    if parser["pmbootstrap"].get("ssh_keys", "").lower() != "true":
        die("generated pmbootstrap config does not enable ssh_keys")
    if parser["pmbootstrap"].get("ssh_key_glob") != keys_text:
        die("generated pmbootstrap config does not point at the validated public keys")
    write_atomic(output, content, mode=0o600)
    return {
        "config": str(output),
        "user": user or "user",
        "ssh_key_glob": keys_text,
        "ssh_keys": True,
    }


def release_identity() -> None:
    print(RELEASE_CANDIDATE)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("release-candidate", help="print the release-candidate label")
    sub.add_parser("kernel-release", help="print this candidate's kernel uname")

    source = sub.add_parser("source", help="validate the rc3 kernel + baseline pins")
    source.add_argument("--source-build", type=Path, required=True)
    source.add_argument("--source-state", type=Path, required=True)
    source.add_argument("--baseline-build", type=Path, default=BASELINE_BUILD)
    source.add_argument("--baseline-state", type=Path, default=BASELINE_STATE)
    source.add_argument("--require-boot", action="store_true")

    record = sub.add_parser("kernel-record", help="record the rootless kernel build identity")
    record.add_argument("--build", type=Path, default=KERNEL_BUILD)
    record.add_argument("--state", type=Path, default=KERNEL_STATE)
    record.add_argument("--recipe", default=KERNEL_RECIPE)

    kernel_manifest_parser = sub.add_parser(
        "kernel-manifest", help="record the packed rc3 boot image and rootfs initramfs")
    kernel_manifest_parser.add_argument("--build", type=Path, default=KERNEL_BUILD)
    kernel_manifest_parser.add_argument("--state", type=Path, default=KERNEL_STATE)
    kernel_manifest_parser.add_argument("--recipe", default=KERNEL_RECIPE)
    kernel_manifest_parser.add_argument("--boot-img", type=Path, required=True)
    kernel_manifest_parser.add_argument("--initramfs", type=Path, required=True)
    kernel_manifest_parser.add_argument("--rootfs-image", type=Path, required=True)
    kernel_manifest_parser.add_argument("--rootfs-tree", type=Path, required=True)
    kernel_manifest_parser.add_argument("--apk-dir", type=Path, required=True)

    check = sub.add_parser("check", help="read-only candidate artifact consistency check")
    check.add_argument("--build", type=Path, default=ROOT / "build/plasma-mobile")
    check.add_argument("--state", type=Path, default=ROOT / ".work/plasma-mobile")
    check.add_argument("--kernel-state", type=Path, default=KERNEL_STATE)

    resign = sub.add_parser("apk-resign", help="preserve pinned APK control/data across local signing")
    resign.add_argument("--source", type=Path, required=True)
    resign.add_argument("--sha256", required=True)
    action = resign.add_mutually_exclusive_group(required=True)
    action.add_argument("--control-output", type=Path)
    action.add_argument("--candidate", type=Path)
    action.add_argument("--signed-control", type=Path)
    resign.add_argument("--output", type=Path)

    prepare = sub.add_parser("prepare", help="prepare the isolated pmaports/config state")
    prepare.add_argument("--source-pmaports", type=Path, required=True)
    prepare.add_argument("--candidate-pmaports", type=Path, required=True)
    prepare.add_argument("--source-pmbootstrap", type=Path, required=True)
    prepare.add_argument("--candidate-pmbootstrap", type=Path, required=True)
    prepare.add_argument("--source-config", type=Path, required=True)
    prepare.add_argument("--candidate-config", type=Path, required=True)
    prepare.add_argument("--work", type=Path, required=True)
    prepare.add_argument("--state", type=Path, required=True)
    prepare.add_argument("--receipt", type=Path, required=True)

    verify = sub.add_parser("verify", help="compare the installed rootfs with the staged kernel")
    verify.add_argument("--release", default=RELEASE)
    verify.add_argument("--kernel-dir", type=Path, required=True)
    verify.add_argument("--rootfs", type=Path, required=True)
    verify.add_argument("--initramfs", type=Path, required=True)
    verify.add_argument("--known-good-rootfs", type=Path, required=True)
    verify.add_argument("--expected-vmlinuz-sha", required=True)
    verify.add_argument("--expected-dtb-sha", required=True)
    verify.add_argument("--expected-module-payload-sha", required=True)
    verify.add_argument("--expected-module-count", type=int, required=True)
    verify.add_argument("--ui-version", required=True)
    verify.add_argument("--modemmanager-version")
    verify.add_argument("--libmm-glib-version")
    verify.add_argument("--nura-clock-version")

    key = sub.add_parser("authorized-key", help="validate explicit OpenSSH public keys")
    key.add_argument("--public-key", type=Path, required=True)
    key.add_argument("--output", type=Path, required=True)

    key_config = sub.add_parser(
        "authorized-key-config", help="enable pmbootstrap's public-key install for one build"
    )
    key_config.add_argument("--source-config", type=Path, required=True)
    key_config.add_argument("--authorized-keys", type=Path, required=True)
    key_config.add_argument("--output-config", type=Path, required=True)

    manifest_parser = sub.add_parser("manifest", help="write the Plasma DELIVERY manifest")
    manifest_parser.add_argument("--source-build", type=Path, required=True)
    manifest_parser.add_argument("--source-state", type=Path, required=True)
    manifest_parser.add_argument("--baseline-build", type=Path, default=BASELINE_BUILD)
    manifest_parser.add_argument("--baseline-state", type=Path, default=BASELINE_STATE)
    manifest_parser.add_argument("--kernel-dir", type=Path, required=True)
    manifest_parser.add_argument("--image", type=Path, required=True)
    manifest_parser.add_argument("--boot-img", type=Path, required=True)
    manifest_parser.add_argument("--boot-initramfs", type=Path, required=True)
    manifest_parser.add_argument("--rootfs-initramfs", type=Path, required=True)
    manifest_parser.add_argument("--rootfs-image", type=Path, required=True)
    manifest_parser.add_argument("--rootfs-tree", type=Path, required=True)
    manifest_parser.add_argument("--temporary-dtbo", type=Path, required=True)
    manifest_parser.add_argument("--apk-dir", type=Path, required=True)
    manifest_parser.add_argument("--modemmanager-version", required=True)
    manifest_parser.add_argument("--libmm-glib-version", required=True)
    manifest_parser.add_argument("--nura-clock-version")
    manifest_parser.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "release-candidate":
            release_identity()
        elif args.command == "kernel-release":
            print(RELEASE)
        elif args.command == "source":
            print(json.dumps(source_contract(
                args.source_build, args.source_state, KERNEL_RECIPE,
                args.baseline_build, args.baseline_state, args.require_boot), indent=2))
        elif args.command == "kernel-record":
            print(json.dumps(kernel_record(args), indent=2))
        elif args.command == "kernel-manifest":
            print(json.dumps(kernel_manifest(args), indent=2))
        elif args.command == "check":
            print(json.dumps(check_candidate(args), indent=2))
        elif args.command == "apk-resign":
            if args.signed_control and not args.output:
                parser.error("--signed-control requires --output")
            resign_apk(args)
        elif args.command == "prepare":
            print(json.dumps(prepare_state(args), indent=2))
        elif args.command == "verify":
            print(json.dumps(verify_installed(args), indent=2))
        elif args.command == "authorized-key":
            print(json.dumps(authorized_key(args), indent=2))
        elif args.command == "authorized-key-config":
            print(json.dumps(authorized_key_config(args), indent=2))
        elif args.command == "manifest":
            print(json.dumps(manifest(args), indent=2))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError, zlib.error, tarfile.TarError) as error:
        die(f"unexpected candidate data: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
