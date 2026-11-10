#!/usr/bin/env python3
"""Create a reproducible, read-only checkpoint of the working rear-camera build.

This tool inspects a checkout, the frozen source snapshot and the per-build
manifests.  It never runs sudo, mount, pmbootstrap, adb, fastboot, a phone
command or a network service.  It writes only below ``--out`` (plus the
explicitly requested archive file).

Identity policy:
  * The source is pinned by commit, patch and snapshot hashes.
  * Artifact identity comes from build/rear-camera/DELIVERY.json, which is
    generated for each build.  No artifact hash from a particular day is
    hardcoded here.
  * The packaged rootfs image is treated as immutable.  The netboot
    images_netboot copy is treated as a writable served cache: a difference
    is recorded, not failed.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

DEFAULT_HEAD = "5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24"
DEFAULT_RELEASE = "7.0.0-sm8150-rear1"
SNAPSHOT_PATCH = "working-source.patch"
SNAPSHOT_SUMS = "SHA256SUMS.json"
SNAPSHOT_UNTRACKED = "untracked"

ARTIFACT_FALLBACKS = {
    "boot_img": "build/rear-camera/boot.img",
    "image": "build/rear-camera/Image",
    "kernel_package": "build/rear-camera/kernel-package/boot/vmlinuz",
    "dtb": "build/rear-camera/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb",
    "initramfs": ".work/rear-camera/rootfs/export/initramfs",
    "rootfs_image": ".work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img",
}

HARDWARE_OBSERVED = [
    {
        "date": "2026-11-10",
        "claim": "After an explicit --replace refreshed the port-10001 netboot cache, "
                 "all three rear cameras worked in the camera app.",
        "evidence": "user report recorded in the frozen handoff and in "
                    "docs/REAR_CAMERA_7_0_BRINGUP.md (Hardware confirmation section)",
        "verified_by_this_tool": False,
    },
    {
        "date": "2026-11-10",
        "claim": "Video records, but recording/preview is choppy.  Capture, encoding, "
                 "preview and playback were not separated.",
        "evidence": "user report in the handoff brief",
        "verified_by_this_tool": False,
    },
    {
        "date": "2026-11-09",
        "claim": "Wi-Fi and Bluetooth worked on the temporary Nura boot; they were not "
                 "explicitly rechecked in the rear-camera confirmation.",
        "evidence": "docs/LIVE_BASELINE.md",
        "verified_by_this_tool": False,
    },
    {
        "date": "2026-11-10",
        "claim": "The kernel includes speaker patch 0027; audio was not explicitly "
                 "rechecked in the rear-camera confirmation.",
        "evidence": "recipe source list and the handoff brief",
        "verified_by_this_tool": False,
    },
    {
        "date": "2026-11-10",
        "claim": "The ultra-wide AK7374 is bound.  Main and tele actuator suppliers "
                 "remain unresolved, so those lenses are intentionally unbound.",
        "evidence": "handoff brief; docs/REAR_CAMERA_7_0_BRINGUP.md",
        "verified_by_this_tool": False,
    },
]

HARDWARE_UNVERIFIED = [
    "Which libcamera stack the camera app loaded: system libcamera 0.8.0-r1 plus "
    "PipeWire SPA 1.6.11, or the isolated 0.7.2 under /opt/nura-rear-camera.",
    "Autofocus correctness, calibration, focus accuracy and main/tele lens identity.",
    "Video FPS, dropped frames, encode vs capture vs preview vs playback contributions.",
    "Suspend/resume, long-term stability, thermal behaviour.",
    "Front camera (not implemented and out of scope).",
    "Cellular, charging, sensors and the other non-camera functions.",
    "The source-level CCI fix was re-confirmed here; the running phone was not touched.",
]


class CheckpointError(RuntimeError):
    """A required checkpoint input is missing or inconsistent."""


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path, algorithm: str = "sha256") -> str:
    hasher = hashlib.new(algorithm)
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def stat_signature(stat_result: os.stat_result) -> list[int]:
    return [stat_result.st_size, stat_result.st_mtime_ns, stat_result.st_ino]


def hash_file(path: Path, cache: dict, rehash: bool = False) -> tuple[str, bool]:
    """SHA-256 with a (size, mtime, inode) cache, so repeats are cheap."""
    path = Path(path)
    stat_result = path.stat()
    key = str(path.resolve())
    signature = stat_signature(stat_result)
    cached = cache.get(key)
    if not rehash and cached and cached.get("signature") == signature:
        return cached["sha256"], True
    digest = digest_file(path)
    cache[key] = {"signature": signature, "sha256": digest}
    return digest, False


def file_entry(path: Path, role: str, root: Path, cache: dict, rehash: bool) -> dict:
    path = Path(path)
    before = path.stat()
    digest, cached = hash_file(path, cache, rehash)
    after = path.stat()
    try:
        relative = str(path.resolve().relative_to(Path(root).resolve()))
    except ValueError:
        relative = str(path)
    entry = {
        "path": str(path),
        "relative": relative,
        "role": role,
        "sha256": digest,
        "size": after.st_size,
        "mtime_ns": after.st_mtime_ns,
        "inode": after.st_ino,
        "hash_cached": cached,
    }
    if stat_signature(before) != stat_signature(after):
        entry["changed_while_hashing"] = True
    return entry


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError as error:
        raise CheckpointError(f"missing JSON input: {path}") from error
    except json.JSONDecodeError as error:
        raise CheckpointError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(data, dict):
        raise CheckpointError(f"{path}: expected a JSON object")
    return data


def run(command: list[str], cwd: Path | None = None, input_data: bytes | None = None,
        timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        input=input_data,
        capture_output=True,
        text=False,
        timeout=timeout,
    )


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args])


def text_of(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode(errors="replace")


def tree_digest(entries: list[dict]) -> str:
    payload = "".join(f'{entry["sha256"]}  {entry["relative"]}\n'
                      for entry in sorted(entries, key=lambda item: item["relative"]))
    return digest_bytes(payload.encode())


# --------------------------------------------------------------------- snapshot
def snapshot_files(snapshot: Path) -> set[str]:
    files = {SNAPSHOT_PATCH}
    untracked = snapshot / SNAPSHOT_UNTRACKED
    if untracked.is_dir():
        for path in untracked.rglob("*"):
            if path.is_file():
                files.add(str(path.relative_to(snapshot)))
    return files


def verify_snapshot(snapshot: Path, cache: dict, rehash: bool) -> dict:
    snapshot = Path(snapshot)
    sums_path = snapshot / SNAPSHOT_SUMS
    sums = read_json(sums_path)
    actual_files = snapshot_files(snapshot)
    expected_files = set(sums)
    extras = sorted(actual_files - expected_files)
    missing = sorted(expected_files - actual_files)
    entries = []
    bad = []
    for relative, expected in sorted(sums.items()):
        path = snapshot / relative
        if not path.is_file():
            bad.append({"relative": relative, "error": "missing"})
            continue
        actual, cached = hash_file(path, cache, rehash)
        entry = {
            "relative": relative,
            "sha256": actual,
            "expected_sha256": expected,
            "size": path.stat().st_size,
            "match": actual == expected,
            "hash_cached": cached,
        }
        entries.append(entry)
        if actual != expected:
            bad.append({"relative": relative, "expected": expected, "actual": actual})
    ok = not bad and not extras and not missing
    return {
        "path": str(snapshot),
        "sums_sha256": digest_file(sums_path),
        "entries": len(sums),
        "verified": ok,
        "mismatches": bad,
        "missing_from_disk": missing,
        "extra_on_disk": extras,
        "tree_sha256": tree_digest(entries),
        "files": entries,
    }


def patch_paths(patch_text: str) -> list[str]:
    paths = []
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+?)$", patch_text, re.MULTILINE):
        paths.append(match.group(2))
    return paths


def verify_patch(snapshot: Path, root: Path, temporary: Path) -> dict:
    patch_path = Path(snapshot) / SNAPSHOT_PATCH
    text = patch_path.read_text()
    paths = patch_paths(text)
    archive = git(root, "archive", "HEAD")
    if archive.returncode != 0:
        return {
            "applies": False,
            "error": text_of(archive).strip() or "git archive HEAD failed",
            "files": paths,
        }
    temporary.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
        tar.extractall(temporary, filter="data")
    check = run(["git", "apply", "--check", "-p1", str(patch_path)], cwd=temporary)
    if check.returncode != 0:
        return {
            "applies": False,
            "error": text_of(check).strip(),
            "files": paths,
        }
    apply = run(["git", "apply", "-p1", str(patch_path)], cwd=temporary)
    if apply.returncode != 0:
        return {
            "applies": False,
            "error": text_of(apply).strip(),
            "files": paths,
        }
    files = []
    mismatches = []
    for relative in paths:
        built = temporary / relative
        live = Path(root) / relative
        if not built.is_file():
            mismatches.append({"relative": relative, "error": "patch output missing"})
            continue
        if not live.is_file():
            mismatches.append({"relative": relative, "error": "live file missing"})
            continue
        built_sha = digest_file(built)
        live_sha = digest_file(live)
        files.append({"relative": relative, "sha256": built_sha,
                      "matches_worktree": built_sha == live_sha})
        if built_sha != live_sha:
            mismatches.append({"relative": relative, "patched": built_sha,
                               "worktree": live_sha})
    return {
        "applies": True,
        "files": files,
        "mismatches": mismatches,
        "matches_worktree": not mismatches,
    }


def verify_untracked(snapshot: Path, root: Path, cache: dict, rehash: bool) -> dict:
    mismatches = []
    checked = 0
    untracked = Path(snapshot) / SNAPSHOT_UNTRACKED
    if not untracked.is_dir():
        return {"checked": 0, "mismatches": [{"error": "snapshot untracked/ missing"}],
                "matches_worktree": False}
    for path in sorted(untracked.rglob("*")):
        if not path.is_file():
            continue
        relative = str(path.relative_to(untracked))
        live = Path(root) / relative
        checked += 1
        if not live.is_file():
            mismatches.append({"relative": relative, "error": "missing in worktree"})
            continue
        snapshot_sha, _ = hash_file(path, cache, rehash)
        live_sha, _ = hash_file(live, cache, rehash)
        if snapshot_sha != live_sha:
            mismatches.append({"relative": relative, "snapshot": snapshot_sha,
                               "worktree": live_sha})
    return {"checked": checked, "mismatches": mismatches,
            "matches_worktree": not mismatches}


def verify_source(root: Path, snapshot: Path | None, out: Path, cache: dict,
                  rehash: bool, expected_head: str) -> tuple[dict, list[str], list[str]]:
    root = Path(root)
    failures: list[str] = []
    warnings: list[str] = []
    head_result = git(root, "rev-parse", "HEAD")
    head = text_of(head_result).strip() if head_result.returncode == 0 else None
    if head is None:
        failures.append(f"not a git worktree: {root}")
    elif expected_head and head != expected_head:
        failures.append(f"source HEAD is {head}, expected confirmed commit {expected_head}")
    status_result = git(root, "status", "--porcelain")
    dirty = text_of(status_result).splitlines() if status_result.returncode == 0 else []
    info = {
        "worktree": str(root),
        "head": head,
        "expected_head": expected_head,
        "head_ok": bool(head and (not expected_head or head == expected_head)),
        "dirty_entries": len(dirty),
        "dirty_files": dirty,
        "snapshot": None,
        "patch": None,
        "untracked": None,
    }
    if snapshot is None:
        warnings.append("no --snapshot supplied; frozen source snapshot was not verified")
        return info, failures, warnings
    snapshot = Path(snapshot)
    try:
        snapshot_info = verify_snapshot(snapshot, cache, rehash)
    except CheckpointError as error:
        failures.append(str(error))
        return info, failures, warnings
    info["snapshot"] = snapshot_info
    if not snapshot_info["verified"]:
        failures.append("frozen source snapshot does not match SHA256SUMS.json")
    temporary = Path(out) / ".tmp" / "source"
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    patch_info = verify_patch(snapshot, root, temporary)
    info["patch"] = patch_info
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        (Path(out) / ".tmp").rmdir()
    except OSError:
        pass
    if not patch_info.get("applies"):
        failures.append("working-source.patch does not apply cleanly to HEAD")
    elif not patch_info.get("matches_worktree"):
        warnings.append("working-source.patch applies, but tracked files differ from "
                        "the live worktree")
    untracked_info = verify_untracked(snapshot, root, cache, rehash)
    info["untracked"] = untracked_info
    if not untracked_info.get("matches_worktree"):
        warnings.append("frozen untracked/ snapshot differs from the live worktree")
    return info, failures, warnings


# ---------------------------------------------------------------------- recipes
def parse_assignment(text: str, name: str) -> str | None:
    match = re.search(rf"^{re.escape(name)}=(\S*)$", text, re.MULTILINE)
    return match.group(1) if match else None


def parse_block(text: str, name: str) -> str | None:
    match = re.search(rf'^{re.escape(name)}="(.*?)"\s*$', text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def recipe_info(recipe: Path, root: Path, cache: dict, rehash: bool,
                expected_pkgver: str) -> tuple[dict, list[str], list[str]]:
    recipe = Path(recipe)
    failures: list[str] = []
    warnings: list[str] = []
    apkbuild = recipe / "APKBUILD"
    if not apkbuild.is_file():
        return {"path": str(recipe)}, [f"missing recipe: {apkbuild}"], []
    text = apkbuild.read_text()
    pkgver = parse_assignment(text, "pkgver")
    pkgrel = parse_assignment(text, "pkgrel")
    source_block = parse_block(text, "source") or ""
    source_names = []
    shell = run(["sh", "-c", '. "$1"; printf "%s\\n" $source', "sh", str(apkbuild)])
    if shell.returncode == 0:
        words = text_of(shell).splitlines()
    else:
        words = [line.strip() for line in source_block.splitlines() if line.strip()]
    for line in words:
        line = line.strip()
        if not line:
            continue
        source_names.append(line.split("::")[0].split("/")[-1])
    sums_text = parse_block(text, "sha512sums") or ""
    sums = {}
    for line in sums_text.splitlines():
        match = re.match(r"^([a-f0-9]{128})\s{2}(\S+)$", line.strip())
        if match:
            sums[match.group(2)] = match.group(1)
    info = {
        "path": str(recipe),
        "apkbuild": str(apkbuild),
        "apkbuild_sha256": digest_file(apkbuild),
        "pkgver": pkgver,
        "pkgrel": pkgrel,
        "source_names": source_names,
        "checksum_names": sorted(sums),
    }
    if pkgver != expected_pkgver:
        failures.append(f"{recipe.name}: pkgver {pkgver!r}, expected {expected_pkgver!r}")
    if sorted(source_names) != sorted(sums):
        failures.append(f"{recipe.name}: source list and sha512sums names differ")
    files = {}
    for name in source_names:
        if name.endswith(".tar.gz"):
            path = Path(root) / ".work/rear-camera" / name
            if not path.is_file():
                warnings.append(f"{recipe.name}: source tarball not staged: {path}")
                continue
        else:
            path = recipe / name
            if not path.is_file():
                failures.append(f"{recipe.name}: source file missing: {path}")
                continue
        expected_sha512 = sums.get(name)
        actual_sha512 = digest_file(path, "sha512")
        sha256, cached = hash_file(path, cache, rehash)
        matches = actual_sha512 == expected_sha512
        files[name] = {
            "path": str(path),
            "sha256": sha256,
            "sha512": actual_sha512,
            "expected_sha512": expected_sha512,
            "size": path.stat().st_size,
            "recipe_checksum_match": matches,
            "hash_cached": cached,
        }
        if not matches:
            failures.append(f"{recipe.name}: sha512 mismatch for {name}")
    info["files"] = files
    return info, failures, warnings


def verify_recipes(root: Path, cache: dict, rehash: bool) -> tuple[dict, list[str], list[str]]:
    base = Path(root) / "device/testing/rear-camera"
    failures: list[str] = []
    warnings: list[str] = []
    info = {"base": str(base), "kernel": None, "libcamera": None, "pins": None,
            "fixes": {}}
    if not base.is_dir():
        return info, [f"missing rear-camera recipe directory: {base}"], warnings
    kernel_recipe = base / "linux-postmarketos-qcom-sm8150"
    libcamera_recipe = base / "libcamera-rear"
    kernel_info, kernel_failures, kernel_warnings = recipe_info(
        kernel_recipe, root, cache, rehash, "7.0.0")
    info["kernel"] = kernel_info
    failures.extend(kernel_failures)
    warnings.extend(kernel_warnings)
    lib_info, lib_failures, lib_warnings = recipe_info(
        libcamera_recipe, root, cache, rehash, "0.7.2")
    info["libcamera"] = lib_info
    failures.extend(lib_failures)
    warnings.extend(lib_warnings)
    pins_path = base / "SOURCE_PINS.json"
    if pins_path.is_file():
        try:
            pins = read_json(pins_path)
            info["pins"] = {"path": str(pins_path),
                            "sha256": digest_file(pins_path),
                            "values": pins}
        except CheckpointError as error:
            failures.append(str(error))
    else:
        warnings.append(f"missing source pins: {pins_path}")
    patch_0031 = kernel_recipe / "0031-i2c-qcom-cci-initialize-all-master-completions.patch"
    if not patch_0031.is_file():
        failures.append("patch 0031 is missing from the kernel recipe")
    else:
        text = patch_0031.read_text()
        before = text.split("for_each_available_child_of_node")[0]
        initialized = ("init_completion(&cci->master[i].irq_complete)" in before
                       and "i < cci->data->num_masters" in before)
        info["fixes"]["0031_initializes_all_master_completions"] = {
            "present": initialized,
            "evidence": str(patch_0031),
        }
        if not initialized:
            failures.append("patch 0031 does not initialize all master completions "
                            "before parsing available children")
    patch_0030 = kernel_recipe / "0030-arm64-dts-guacamole-three-rear-cameras.patch"
    if patch_0030.is_file():
        text = patch_0030.read_text()
        block = text.split("cci1: cci@ac4b000", 1)[-1].split("#endif", 1)[0]
        info["fixes"]["cci1_rear_graph_only_master1"] = {
            "present": "i2c-bus@1" in block and "i2c-bus@0" not in block,
            "evidence": str(patch_0030),
        }
        if not info["fixes"]["cci1_rear_graph_only_master1"]["present"]:
            warnings.append("CCI1 graph no longer looks like the rear-only master-1 graph")
    else:
        warnings.append("patch 0030 (rear camera graph) is missing")
    kernel_sources = set(kernel_info.get("source_names") or [])
    for required in ("0027-arm64-dts-enable-guacamole-speaker-bus.patch",
                     "0028-media-camss-sm8150-rebase.patch",
                     "0029-media-guacamole-rear-sensors-and-lenses.patch",
                     "0030-arm64-dts-guacamole-three-rear-cameras.patch",
                     "0031-i2c-qcom-cci-initialize-all-master-completions.patch"):
        if required not in kernel_sources:
            failures.append(f"kernel recipe source list is missing {required}")
    info["fixes"]["speaker_0027_in_recipe"] = {
        "present": "0027-arm64-dts-enable-guacamole-speaker-bus.patch" in kernel_sources,
        "evidence": str(kernel_recipe / "APKBUILD"),
    }
    return info, failures, warnings


# -------------------------------------------------------------------- artifacts
def resolve_manifest_path(root: Path, delivery: dict, key: str) -> Path:
    entry = delivery.get(key)
    fallback = ARTIFACT_FALLBACKS.get(key)
    if isinstance(entry, dict):
        raw = Path(str(entry.get("path") or ""))
        declared_root = delivery.get("worktree")
        candidates = []
        if raw.is_absolute():
            if declared_root:
                try:
                    candidates.append(Path(root) / raw.relative_to(Path(declared_root)))
                except ValueError:
                    pass
            candidates.append(raw)
        else:
            candidates.append(Path(root) / raw)
        for candidate in candidates:
            if candidate.exists():
                return candidate
    if fallback:
        return Path(root) / fallback
    raise CheckpointError(f"cannot resolve artifact path for {key}")


def manifest_match(actual: dict, expected: dict) -> bool:
    return (actual.get("sha256") == expected.get("sha256")
            and actual.get("size") == expected.get("size"))


def verify_artifacts(root: Path, cache: dict, rehash: bool) -> tuple[dict, list[str], list[str]]:
    root = Path(root)
    failures: list[str] = []
    warnings: list[str] = []
    delivery_path = root / "build/rear-camera/DELIVERY.json"
    if not delivery_path.is_file():
        return {"manifest": str(delivery_path)}, [
            f"missing per-build manifest: {delivery_path} "
            "(run: bash scripts/camera_build.sh manifest)"
        ], warnings
    delivery = read_json(delivery_path)
    release = delivery.get("release")
    info = {
        "manifest": {
            "path": str(delivery_path),
            "sha256": digest_file(delivery_path),
            "created_at": delivery.get("created_at"),
            "worktree": delivery.get("worktree"),
            "git_head": delivery.get("git_head"),
            "release": release,
        },
        "release": release,
        "files": {},
        "rootfs": {},
        "served_cache": {},
        "boot_img_contents": {},
        "kernel_package": {},
        "cross_checks": {},
    }
    if release != DEFAULT_RELEASE:
        warnings.append(f"manifest release {release!r} is not the confirmed "
                        f"{DEFAULT_RELEASE!r}")
    for key in ("boot_img", "image", "kernel_package", "dtb", "initramfs",
                "rootfs_image"):
        expected = delivery.get(key)
        if not isinstance(expected, dict):
            failures.append(f"DELIVERY.json has no {key} entry")
            continue
        path = resolve_manifest_path(root, delivery, key)
        if not path.is_file():
            failures.append(f"{key}: missing artifact {path}")
            info["files"][key] = {"path": str(path), "missing": True,
                                  "manifest": expected}
            continue
        role = "immutable_packaged_rootfs" if key == "rootfs_image" else "immutable_build_artifact"
        actual = file_entry(path, role, root, cache, rehash)
        actual["manifest"] = {"sha256": expected.get("sha256"),
                              "size": expected.get("size")}
        actual["match"] = manifest_match(actual, expected)
        info["files"][key] = actual
        if key == "rootfs_image":
            info["rootfs"] = actual
        if not actual["match"]:
            failures.append(f"{key}: {path} sha256/size does not match DELIVERY.json")
    # CCI module path follows the release, so a future build is not tied to a
    # hardcoded module name.
    cci_entry = delivery.get("i2c_qcom_cci")
    if isinstance(cci_entry, dict) and release:
        cci_path = (root / "build/rear-camera/kernel-package/usr/lib/modules" / release
                    / "kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst")
        if cci_path.is_file():
            actual = file_entry(cci_path, "staged_kernel_module", root, cache, rehash)
            actual["manifest"] = {"sha256": cci_entry.get("sha256"),
                                  "size": cci_entry.get("size")}
            actual["match"] = manifest_match(actual, cci_entry)
            info["files"]["i2c_qcom_cci"] = actual
            if not actual["match"]:
                failures.append("i2c_qcom_cci: staged module does not match DELIVERY.json")
        else:
            failures.append(f"i2c_qcom_cci: missing staged module {cci_path}")
    else:
        failures.append("DELIVERY.json has no i2c_qcom_cci entry")
    # Packaging inputs is a second per-build statement; verify the overlap.
    packaging_path = root / "build/rear-camera/packaging-inputs.json"
    if packaging_path.is_file():
        try:
            packaging = read_json(packaging_path)
            info["packaging_inputs"] = {
                "path": str(packaging_path),
                "sha256": digest_file(packaging_path),
                "release": packaging.get("release"),
                "nbd_port": packaging.get("nbd_port"),
            }
            for manifest_key, input_key in (("initramfs", "initramfs_sha256"),
                                            ("kernel_package", "vmlinuz_sha256"),
                                            ("rootfs_image", "rootfs_image_sha256")):
                actual = info["files"].get(manifest_key, {})
                expected = packaging.get(input_key)
                matches = bool(expected and actual.get("sha256") == expected)
                info["cross_checks"][input_key] = {
                    "expected": expected,
                    "actual": actual.get("sha256"),
                    "match": matches,
                }
                if not matches:
                    failures.append(f"packaging-inputs {input_key} does not match "
                                    f"{manifest_key}")
            if packaging.get("release") != release:
                failures.append("packaging-inputs release differs from DELIVERY.json")
        except CheckpointError as error:
            failures.append(str(error))
    else:
        warnings.append(f"missing packaging inputs: {packaging_path}")
    # Kernel package module count and release.
    module_root = root / "build/rear-camera/kernel-package/usr/lib/modules"
    if module_root.is_dir():
        releases = sorted(path.name for path in module_root.iterdir() if path.is_dir())
        modules = sorted(path for path in module_root.rglob("*.ko*") if path.is_file())
        info["kernel_package"] = {
            "module_root": str(module_root),
            "module_releases": releases,
            "module_count": len(modules),
            "release_file": None,
        }
        release_file = (root / "build/rear-camera/kernel-package/usr/share/kernel/"
                        "postmarketos-qcom-sm8150/kernel.release")
        if release_file.is_file():
            info["kernel_package"]["release_file"] = release_file.read_text().strip()
            if info["kernel_package"]["release_file"] != release:
                failures.append("kernel-package kernel.release differs from manifest")
        else:
            failures.append(f"missing kernel release file: {release_file}")
        if releases != [release]:
            failures.append(f"kernel-package module releases {releases} are not [{release}]")
    else:
        failures.append(f"missing kernel module tree: {module_root}")
    # DTB wrapper manifest, if present.  It must be recorded before the boot
    # image contents are compared.
    wrapper_manifest = root / "build/rear-camera/temporary-dtbo/manifest.json"
    if wrapper_manifest.is_file():
        try:
            wrapper = read_json(wrapper_manifest)
            wrapper_entry = (wrapper.get("artifacts") or {}).get("wrapper") or {}
            wrapper_path = root / str(wrapper_entry.get("path") or "")
            if wrapper_path.is_file():
                actual = file_entry(wrapper_path, "boot_image_dtb", root, cache, rehash)
                actual["manifest"] = {"sha256": wrapper_entry.get("sha256"),
                                      "size": wrapper_entry.get("size")}
                actual["match"] = manifest_match(actual, wrapper_entry)
                info["files"]["dtb_wrapper"] = actual
                if not actual["match"]:
                    warnings.append("DTB wrapper differs from temporary-dtbo/manifest.json")
            else:
                warnings.append(f"DTB wrapper missing: {wrapper_path}")
        except CheckpointError as error:
            warnings.append(str(error))
    else:
        warnings.append("temporary-dtbo/manifest.json is missing; wrapper identity is "
                        "recorded from the boot image only")
    # Boot image contents are derived from the boot image itself, not from a
    # fixed hash from a previous day.
    info["boot_img_contents"] = verify_boot_image(root, info, cache, rehash, failures,
                                                  warnings)
    # Served cache: mutable, and must never be treated as source integrity.
    served_path = (root / ".work/rear-camera/pmbootstrap-work/images_netboot/"
                   "oneplus-guacamole.img")
    if served_path.is_file():
        served = file_entry(served_path, "writable_served_cache", root, cache, rehash)
        packaged = info["files"].get("rootfs_image", {})
        served["same_as_packaged_rootfs"] = bool(
            packaged.get("sha256") and served.get("sha256") == packaged.get("sha256"))
        served["classification"] = ("identical to packaged rootfs at check time"
                                    if served["same_as_packaged_rootfs"]
                                    else "differs from packaged rootfs (guest writeback "
                                         "and/or stale cache)")
        served["integrity_claim"] = ("not a source of truth; the delivery path must "
                                     "verify the served cache before boot")
        info["served_cache"] = served
        if not served["same_as_packaged_rootfs"]:
            warnings.append("served netboot cache differs from the packaged rootfs; "
                            "this is expected after a guest write but is not proof of "
                            "fixed content")
    else:
        info["served_cache"] = {"path": str(served_path), "missing": True,
                                "role": "writable_served_cache"}
        warnings.append(f"served netboot cache not present: {served_path}")
    return info, failures, warnings


def verify_boot_image(root: Path, info: dict, cache: dict, rehash: bool,
                      failures: list[str], warnings: list[str]) -> dict:
    root = Path(root)
    boot_path = info["files"].get("boot_img", {}).get("path")
    result = {"path": boot_path, "unpack": "not-run"}
    if not boot_path or not Path(boot_path).is_file():
        return result
    unpacker = root / "local/tools/mkbootimg/unpack_bootimg.py"
    if not unpacker.is_file():
        result["unpack"] = "skipped: unpack_bootimg.py not found"
        warnings.append("boot image unpack skipped: bundled unpack_bootimg.py missing")
        return result
    temporary = Path(tempfile.mkdtemp(prefix="checkpoint-unpack-"))
    try:
        run_result = run([sys.executable, str(unpacker), "--boot_img", boot_path,
                          "--out", str(temporary)])
        result["returncode"] = run_result.returncode
        result["stdout"] = text_of(run_result)
        if run_result.returncode != 0:
            result["unpack"] = "failed"
            failures.append("unpack_bootimg.py failed on boot.img")
            return result
        result["unpack"] = "ok"
        fields = {}
        for line in text_of(run_result).splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        result["fields"] = {key: fields.get(key) for key in (
            "boot image header version", "page size", "kernel_size", "ramdisk size",
            "dtb size", "dtb address", "command line args")}
        expected = {
            "kernel": info["files"].get("image", {}).get("sha256"),
            "ramdisk": info["files"].get("initramfs", {}).get("sha256"),
            "dtb": info["files"].get("dtb_wrapper", {}).get("sha256"),
        }
        matches = {}
        for name, expected_sha in expected.items():
            extracted = temporary / name
            if not extracted.is_file():
                matches[name] = {"match": False, "error": "not extracted"}
                failures.append(f"boot image did not unpack {name}")
                continue
            actual_sha = digest_file(extracted)
            matches[name] = {"match": actual_sha == expected_sha,
                             "actual": actual_sha, "expected": expected_sha,
                             "size": extracted.stat().st_size}
            if expected_sha and actual_sha != expected_sha:
                failures.append(f"boot image {name} hash does not match the manifest input")
        result["contents"] = matches
        if not expected.get("dtb"):
            warnings.append("boot image DTB was not compared: wrapper identity missing")
        return result
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def verify_packages(root: Path, cache: dict, rehash: bool) -> tuple[dict, list[str]]:
    """Record APK provenance if the packaging checksum file is still present."""
    warnings: list[str] = []
    path = Path(root) / "build/rear-camera/APK-SHA256SUMS"
    if not path.is_file():
        return {"path": str(path), "present": False}, warnings
    expected = []
    for line in path.read_text().splitlines():
        match = re.match(r"^([a-f0-9]{64})\s{2}(\S+)$", line.strip())
        if match:
            expected.append((match.group(2), match.group(1)))
    entries = []
    missing = []
    mismatches = []
    for raw_path, expected_sha in expected:
        candidate = Path(raw_path)
        candidates = [candidate]
        marker = "/.work/rear-camera/"
        if marker in raw_path:
            candidates.append(Path(root) / (".work/rear-camera/" + raw_path.split(marker, 1)[1]))
        if not candidate.is_absolute():
            candidates.append(Path(root) / candidate)
        found = next((item for item in candidates if item.is_file()), None)
        if found is None:
            missing.append(raw_path)
            continue
        actual, cached = hash_file(found, cache, rehash)
        entry = {
            "path": str(found),
            "sha256": actual,
            "expected_sha256": expected_sha,
            "size": found.stat().st_size,
            "match": actual == expected_sha,
            "hash_cached": cached,
        }
        entries.append(entry)
        if actual != expected_sha:
            mismatches.append({"path": str(found), "expected": expected_sha,
                               "actual": actual})
    if mismatches:
        warnings.append("APK-SHA256SUMS does not match the staged packages")
    if missing:
        warnings.append("some packaged APKs are no longer staged; their hashes could "
                        "not be rechecked")
    return {
        "path": str(path),
        "sha256": digest_file(path),
        "present": True,
        "entries": len(entries),
        "total_entries": len(expected),
        "mismatches": mismatches,
        "missing": missing[:20],
        "authoritative": not mismatches and not missing,
        "files": entries,
    }, warnings


def verify_auxiliary_manifests(root: Path, cache: dict, rehash: bool) -> dict:
    """Record stale auxiliary manifests without treating them as truth."""
    root = Path(root)
    build = root / "build/rear-camera"
    result = {}
    sha_sums = build / "SHA256SUMS"
    if sha_sums.is_file():
        expected = []
        for line in sha_sums.read_text().splitlines():
            match = re.match(r"^([a-f0-9]{64})\s{2}(\S+)$", line.strip())
            if match:
                expected.append((match.group(2), match.group(1)))
        result["SHA256SUMS"] = audit_expected_paths(root, expected, cache, rehash)
        result["SHA256SUMS"]["path"] = str(sha_sums)
        result["SHA256SUMS"]["sha256"] = digest_file(sha_sums)
    artifacts = build / "ARTIFACTS.json"
    if artifacts.is_file():
        try:
            data = read_json(artifacts)
            expected = []
            for relative, entry in data.items():
                if isinstance(entry, dict) and entry.get("sha256"):
                    expected.append((relative, entry["sha256"]))
            result["ARTIFACTS.json"] = audit_expected_paths(root, expected, cache, rehash)
            result["ARTIFACTS.json"]["path"] = str(artifacts)
            result["ARTIFACTS.json"]["sha256"] = digest_file(artifacts)
        except CheckpointError as error:
            result["ARTIFACTS.json"] = {"error": str(error)}
    return result


def audit_expected_paths(root: Path, expected: list[tuple[str, str]], cache: dict,
                         rehash: bool) -> dict:
    mismatches = []
    missing = []
    checked = 0
    for relative, expected_sha in expected:
        path = Path(root) / relative
        if not path.is_file():
            missing.append(relative)
            continue
        actual_sha, _ = hash_file(path, cache, rehash)
        checked += 1
        if actual_sha != expected_sha:
            mismatches.append({"path": relative, "expected": expected_sha,
                               "actual": actual_sha})
    return {
        "checked": checked,
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "mismatches": mismatches[:20],
        "missing": missing[:20],
        "authoritative": not mismatches and not missing,
        "note": "pre-fix auxiliary manifest; use DELIVERY.json for this build's identity"
                if (mismatches or missing) else "matches the current build tree",
    }


# -------------------------------------------------------------------- toolchain
def first_match(pattern: str, text: str, group: int = 1) -> str | None:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(group) if match else None


def collect_toolchain(root: Path, build: Path) -> dict:
    root = Path(root)
    build = Path(build)
    lib_configure = build / "libcamera-configure.log"
    configure_text = lib_configure.read_text(errors="replace") if lib_configure.is_file() else ""
    kernel_config = root / ".work/rear-camera/kernel.config"
    config_text = kernel_config.read_text(errors="replace") if kernel_config.is_file() else ""
    pmb_log = root / ".work/rear-camera/pmbootstrap-work/log.txt"
    pmb_text = ""
    if pmb_log.is_file():
        with pmb_log.open(errors="replace") as handle:
            pmb_text = handle.read(2_000_000)
    python_log = build / "python-tools.log"
    python_text = python_log.read_text(errors="replace") if python_log.is_file() else ""
    clang_version = first_match(r"\bclang (\d+\.\d+\.\d+)", configure_text)
    lld_version = first_match(r"\bld\.lld (\d+\.\d+\.\d+)", configure_text)
    meson_version = first_match(r"^Version: (\S+)$", configure_text, 1)
    build_gcc = first_match(r"cc \(gcc (\d+\.\d+\.\d+)", configure_text)
    pmbootstrap = first_match(r"pmbootstrap v([\d.]+) \(Python ([\d.]+)", pmb_text)
    pmbootstrap_python = None
    if pmbootstrap:
        pmbootstrap_python = first_match(
            r"pmbootstrap v([\d.]+) \(Python ([\d.]+)", pmb_text, 2)
    config_clang = first_match(r"^CONFIG_CLANG_VERSION=(\d+)$", config_text)
    config_lld = first_match(r"^CONFIG_LLD_VERSION=(\d+)$", config_text)
    config_localversion = first_match(r'^CONFIG_LOCALVERSION="(.*)"$', config_text)

    def decode_llvm(value: str | None) -> str | None:
        if not value:
            return None
        return f"{int(value[:-4])}.{int(value[-4:-2])}.{int(value[-2:])}"

    return {
        "evidence": {
            "libcamera_configure_log": str(lib_configure) if lib_configure.is_file() else None,
            "kernel_config": str(kernel_config) if kernel_config.is_file() else None,
            "pmbootstrap_log": str(pmb_log) if pmb_log.is_file() else None,
            "python_tools_log": str(python_log) if python_log.is_file() else None,
        },
        "host_python": platform.python_version(),
        "clang": clang_version,
        "lld": lld_version,
        "meson": meson_version,
        "build_machine_gcc": build_gcc,
        "kernel_config_clang": {"raw": config_clang, "decoded": decode_llvm(config_clang)},
        "kernel_config_lld": {"raw": config_lld, "decoded": decode_llvm(config_lld)},
        "kernel_config_localversion": config_localversion,
        "pmbootstrap": pmbootstrap,
        "pmbootstrap_python": pmbootstrap_python,
        "build_venv": {
            "cpython": first_match(r"Using CPython (\S+)", python_text),
            "python": first_match(r"Using Python (\S+)", python_text),
        },
    }


# -------------------------------------------------------------------- checks
def run_existing_checks(root: Path, artifacts: dict) -> tuple[dict, list[str]]:
    root = Path(root)
    failures: list[str] = []
    checks = {}
    initramfs = artifacts["files"].get("initramfs", {}).get("path")
    kernel_dir = root / "build/rear-camera/kernel-package"
    package_script = root / "scripts/check_rear_camera_package.py"
    if package_script.is_file() and initramfs:
        result = run([sys.executable, str(package_script), "--initramfs", initramfs,
                      "--kernel-dir", str(kernel_dir)])
        checks["check_rear_camera_package"] = {
            "returncode": result.returncode,
            "stdout": text_of(result),
            "stderr": result.stderr.decode(errors="replace"),
        }
        if result.returncode != 0:
            failures.append("check_rear_camera_package.py failed")
    else:
        checks["check_rear_camera_package"] = {"skipped": True}
    image = artifacts["files"].get("image", {}).get("path")
    initramfs_size = artifacts["files"].get("initramfs", {}).get("size")
    envelope_script = root / "scripts/check_boot_envelope.py"
    if envelope_script.is_file() and image and initramfs_size:
        result = run([sys.executable, str(envelope_script), "--image", image,
                      "--kernel-offset", "0x8000", "--dtb-offset", "0x03000000",
                      "--ramdisk-offset", "0x01000000",
                      "--ramdisk-size", str(initramfs_size)])
        checks["check_boot_envelope"] = {
            "returncode": result.returncode,
            "stdout": text_of(result),
            "stderr": result.stderr.decode(errors="replace"),
        }
        if result.returncode != 0:
            failures.append("check_boot_envelope.py failed")
    else:
        checks["check_boot_envelope"] = {"skipped": True}
    return checks, failures


# --------------------------------------------------------------------- output
def checkpoint(args: argparse.Namespace) -> tuple[dict, int]:
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / "hash-cache.json"
    cache = {}
    if cache_path.is_file() and not args.rehash:
        try:
            cache = read_json(cache_path)
        except CheckpointError:
            cache = {}
    failures: list[str] = []
    warnings: list[str] = []

    source, source_failures, source_warnings = verify_source(
        root, Path(args.snapshot).resolve() if args.snapshot else None, out,
        cache, args.rehash, args.expected_head)
    failures.extend(source_failures)
    warnings.extend(source_warnings)

    recipes, recipe_failures, recipe_warnings = verify_recipes(root, cache, args.rehash)
    failures.extend(recipe_failures)
    warnings.extend(recipe_warnings)

    artifacts: dict = {}
    if args.source_only:
        artifacts = {"skipped": "source-only"}
    else:
        artifacts, artifact_failures, artifact_warnings = verify_artifacts(
            root, cache, args.rehash)
        failures.extend(artifact_failures)
        warnings.extend(artifact_warnings)

    auxiliary = verify_auxiliary_manifests(root, cache, args.rehash) \
        if not args.source_only else {}
    for name, entry in auxiliary.items():
        if entry.get("mismatch_count") or entry.get("missing_count"):
            warnings.append(f"{name} is stale/incomplete; DELIVERY.json is authoritative "
                            f"for this build")

    packages = verify_packages(root, cache, args.rehash) \
        if not args.source_only else {"skipped": "source-only"}
    package_warnings = []
    if isinstance(packages, tuple):
        packages, package_warnings = packages
    warnings.extend(package_warnings)

    checks: dict = {}
    if not args.source_only and artifacts.get("files"):
        checks, check_failures = run_existing_checks(root, artifacts)
        failures.extend(check_failures)

    toolchain = collect_toolchain(root, root / "build/rear-camera")
    document = {
        "schema": 1,
        "created_at_utc": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tool": {
            "path": str(Path(__file__).resolve()),
            "sha256": digest_file(Path(__file__).resolve()),
        },
        "release": DEFAULT_RELEASE,
        "source": source,
        "recipes": recipes,
        "toolchain": toolchain,
        "artifacts": artifacts,
        "packages": packages,
        "auxiliary_manifests": auxiliary,
        "existing_checks": checks,
        "hardware": {
            "observed_by_user_not_reverified_here": HARDWARE_OBSERVED,
            "unverified": HARDWARE_UNVERIFIED,
            "statement": "No phone, server, module, sensor, I2C, capture or flash "
                         "operation is performed by this checkpoint.",
        },
        "status": {
            "ok": not failures,
            "failures": failures,
            "warnings": warnings,
        },
    }
    checkpoint_json = out / "CHECKPOINT.json"
    checkpoint_json.write_text(json.dumps(document, indent=2, sort_keys=False) + "\n")
    checkpoint_sha = digest_file(checkpoint_json)
    (out / "CHECKPOINT.sha256").write_text(
        f"{checkpoint_sha}  CHECKPOINT.json\n")
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n")
    document["checkpoint_sha256"] = checkpoint_sha
    document["checkpoint_path"] = str(checkpoint_json)
    return document, 0 if not failures else 1


def archive_sources(root: Path, snapshot: Path | None, out: Path) -> list[tuple[Path, str]]:
    root = Path(root).resolve()
    out = Path(out).resolve()
    checkpoint_root = Path(__file__).resolve().parent.parent
    entries: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def default_arcname(path: Path) -> str:
        path = Path(path).resolve()
        for base, prefix in ((root, ""), (checkpoint_root, "checkpoint"),
                             (out, "checkpoint/out"), (snapshot, "snapshot")):
            if base is not None and path.is_relative_to(base):
                relative = str(path.relative_to(base))
                return f"{prefix}/{relative}" if prefix else relative
        return path.name

    def add(path: Path, arcname: str | None = None, recursive: bool = False) -> None:
        path = Path(path)
        if not path.exists():
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        entries.append((path, arcname or default_arcname(path)))
        if recursive and path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    add(child)

    if snapshot:
        snapshot = Path(snapshot).resolve()
        for name in (SNAPSHOT_SUMS, SNAPSHOT_PATCH):
            add(snapshot / name)
        add(snapshot / SNAPSHOT_UNTRACKED, recursive=True)
    add(root / "device/testing/rear-camera", recursive=True)
    for relative in (
        "docs/CAMERA_DELIVERY.md",
        "docs/CCI_PROBE_FAULT.md",
        "docs/LIVE_BASELINE.md",
        "docs/REAR_CAMERA_7_0_BRINGUP.md",
        "docs/SM8150_7_0_BRINGUP.md",
        "build/rear-camera/DELIVERY.json",
        "build/rear-camera/packaging-inputs.json",
        "build/rear-camera/ARTIFACTS.json",
        "build/rear-camera/APK-SHA256SUMS",
        "build/rear-camera/SHA256SUMS",
        "build/rear-camera/boot-envelope.txt",
        "build/rear-camera/imported-inputs.json",
        "build/rear-camera/sysroot-inputs.json",
        "build/rear-camera/libcamera-configure.log",
        "build/rear-camera/python-tools.log",
        "build/rear-camera/all-tests-final.log",
        "build/rear-camera/kernel-build-final.log",
        "build/rear-camera/cci-fix-build.log",
        "build/rear-camera/temporary-dtbo/manifest.json",
        "local/tool-hashes.txt",
    ):
        add(root / relative)
    for name in ("CHECKPOINT.json", "CHECKPOINT.sha256"):
        add(out / name)
    tool = Path(__file__).resolve()
    add(tool)
    for sibling in (tool.parent.parent / "docs/WORKING_CAMERA_CHECKPOINT.md",
                    tool.parent.parent / "REPORT.md",
                    tool.parent.parent / "TEST_RESULTS.txt",
                    tool.parent.parent / "tests/test_checkpoint_working_camera.py"):
        add(sibling)
    return entries


def make_archive(root: Path, snapshot: Path | None, out: Path, archive: Path) -> dict:
    archive = Path(archive)
    archive.parent.mkdir(parents=True, exist_ok=True)
    entries = archive_sources(root, snapshot, out)
    with tarfile.open(archive, "w:zst") as tar:
        for path, arcname in entries:
            tar.add(path, arcname=f"nura-working-camera-checkpoint/{arcname}",
                    recursive=False)
    archive_sha = digest_file(archive)
    archive.with_name(archive.name + ".sha256").write_text(
        f"{archive_sha}  {archive.name}\n")
    return {
        "path": str(archive),
        "sha256": archive_sha,
        "size": archive.stat().st_size,
        "members": len(entries),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True,
                        help="candidate checkout (source + built artifacts)")
    parser.add_argument("--snapshot", type=Path,
                        help="frozen snapshot with working-source.patch, untracked/ and "
                             "SHA256SUMS.json")
    parser.add_argument("--out", type=Path, default=Path("working-camera-checkpoint"),
                        help="output directory (created; default: ./working-camera-checkpoint)")
    parser.add_argument("--expected-head", default=DEFAULT_HEAD,
                        help="confirmed source commit (empty string disables the check)")
    parser.add_argument("--source-only", action="store_true",
                        help="verify source/recipes only; no artifacts or phone-side checks")
    parser.add_argument("--rehash", action="store_true",
                        help="ignore the hash cache and hash every input again")
    parser.add_argument("--archive", type=Path,
                        help="also write a small .tar.zst checkpoint archive (no large "
                             "artifacts or .work/rootfs images)")
    parser.add_argument("--json", action="store_true", help="print the document as JSON")
    args = parser.parse_args(argv)
    try:
        document, status = checkpoint(args)
    except CheckpointError as error:
        print(f"checkpoint: FAIL: {error}", file=sys.stderr)
        return 1
    if args.archive:
        try:
            archive = make_archive(Path(args.root).resolve(),
                                   Path(args.snapshot).resolve() if args.snapshot else None,
                                   Path(args.out).resolve(), args.archive.resolve())
            document["archive"] = archive
            (Path(args.out) / "ARCHIVE.json").write_text(
                json.dumps(archive, indent=2) + "\n")
            print(f"archive: {archive['path']} "
                  f"({archive['size']} bytes, sha256 {archive['sha256']})")
        except OSError as error:
            print(f"checkpoint: FAIL: archive: {error}", file=sys.stderr)
            status = 1
    if args.json:
        print(json.dumps(document, indent=2))
    else:
        print(f"checkpoint: {document['checkpoint_path']}")
        print(f"  release: {document.get('release')}")
        print(f"  source:  {document['source'].get('head')}")
        print(f"  status:  {'PASS' if document['status']['ok'] else 'FAIL'}")
        for failure in document["status"]["failures"]:
            print(f"  FAIL: {failure}")
        for warning in document["status"]["warnings"]:
            print(f"  WARN: {warning}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
