#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Local-userdata checks built on the canonical rc4 helpers.

The boot-image payload proof, initramfs release/netboot-hook check and kernel
module payload comparison remain in the canonical scripts.  This file only
adds the local-profile-specific pieces: the guarded-hook check, the installed
rootfs hook pair, the 4096-byte nested GPT/fstab check, userdata facts, and a
fail-closed final manifest.

Host-only: no build, flash, mount, sudo, server or phone command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

def _helper_dir() -> Path:
    local = Path(__file__).resolve().parent
    if (local / "check_local_candidate.py").is_file() or (local / "check_plasma_candidate.py").is_file():
        return local
    return Path("runtime-workspace/scripts")


HELPERS = Path(os.environ.get("NURA_HELPER_SCRIPTS", str(_helper_dir()))).resolve()
if not ((HELPERS / "check_local_candidate.py").is_file() or (HELPERS / "check_plasma_candidate.py").is_file()):
    raise SystemExit(f"missing canonical helper scripts: {HELPERS}")
sys.path.insert(0, str(HELPERS))

import check_initramfs_release as cir  # noqa: E402
try:
    import check_local_candidate as cpc  # noqa: E402
except ImportError:
    import check_plasma_candidate as cpc  # noqa: E402
import check_rear_camera_package as crcp  # noqa: E402

SECTOR_SIZE = 4096
AVB_MAGIC = b"AVBf"
REQUIRED_INITRAMFS_BINARIES = (
    "usr/bin/losetup",
    "usr/bin/parted",
    "usr/bin/resize2fs",
    "usr/bin/e2fsck",
    "usr/bin/blkid",
    "usr/bin/switch_root",
)
REQUIRED_INITRAMFS_FUNCTIONS = (
    "mount_subpartitions",
    "SUBPARTITION_LOOP",
    "--sector-size",
    "pmOS_root",
    "resizepart",
    "resize2fs",
)
REQUIRED_KERNEL_BUILTINS = {
    "CONFIG_EXT4_FS": "y",
    "CONFIG_BLK_DEV_LOOP": "y",
    "CONFIG_SCSI_UFSHCD": "y",
    "CONFIG_SCSI_UFS_QCOM": "y",
    "CONFIG_EFI_PARTITION": "y",
    "CONFIG_EXT4_USE_FOR_EXT2": "y",
    "CONFIG_DEVTMPFS": "y",
    "CONFIG_PROC_FS": "y",
    "CONFIG_SYSFS": "y",
    "CONFIG_BLK_DEV_INITRD": "y",
}
BOOT_REQUIRED_CMDLINE = (
    "console=ttyMSM0",
    "ramoops.mem_address=0xa9800000",
    "PMOS_NO_OUTPUT_REDIRECT",
    "scsi_mod.scan=sync",
)
BOOT_FORBIDDEN_CMDLINE = ("pmos.root=", "pmos.boot=")


class LocalError(RuntimeError):
    """A fail-closed local-profile check failed."""


def fail(message: str) -> None:
    raise LocalError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict:
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        fail(f"missing real file (symlinks are refused): {path}")
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256_file(path)}


def archive_data(path: Path) -> bytes:
    path = Path(path)
    data = path.read_bytes()
    for magic, tool in (
        (cir.GZIP_MAGIC, "gzip"),
        (cir.ZSTD_MAGIC, "zstd"),
        (cir.XZ_MAGIC, "xz"),
    ):
        if data.startswith(magic):
            return cir._decompress(path, tool, ["-dc"])
    return data


def archive_member(path: Path, member: str) -> bytes:
    data = archive_data(path)
    result = subprocess.run(
        ["cpio", "-i", "--to-stdout", "--quiet", member],
        input=data, capture_output=True,
    )
    if result.returncode != 0:
        fail(f"{path}: cpio cannot extract {member}: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout


def validate_netboot_hook(text: str) -> dict:
    """Require the guarded hook, not just a port-10001 hook."""
    if "NBD_PORT=10001" not in text:
        fail("hook no longer sets NBD_PORT=10001")
    marker = "pmos\\.root=/dev/nbd"
    pos = text.find(marker)
    if pos < 0:
        fail("hook has no local-root guard; an unconditional nbd-client loop is not local")
    body = text[pos:]
    if "exit 0" not in body:
        fail("hook guard does not exit 0 for a local root")
    for setup in ("setup_usb_network", "modprobe nbd", "nbd-client"):
        setup_pos = text.find(setup)
        if setup_pos >= 0 and setup_pos < pos:
            fail(f"{setup} appears before the local-root guard")
    return {"guarded": True, "guard_marker": marker, "nbd_port": 10001}


def initramfs_report(path: Path, release: str) -> dict:
    path = Path(path)
    try:
        crcp.check_initramfs(path, release)
    except (ValueError, cir.InitramfsError, OSError, subprocess.CalledProcessError) as error:
        fail(f"{path}: canonical initramfs check failed: {error}")
    entries = cir.list_archive(path)
    normalized = {entry.lstrip("./") for entry in entries}
    missing = [name for name in REQUIRED_INITRAMFS_BINARIES if name not in normalized]
    if missing:
        fail(f"{path}: missing local-rootfs binaries: {', '.join(missing)}")
    hook_entries = [entry for entry in entries if entry.lstrip("./").endswith("hooks/netboot.sh")]
    if len(hook_entries) != 1:
        fail(f"{path}: expected exactly one hooks/netboot.sh, found {len(hook_entries)}")
    hook_raw = archive_member(path, hook_entries[0])
    hook = validate_netboot_hook(hook_raw.decode(errors="replace"))
    hook["sha256"] = hashlib.sha256(hook_raw).hexdigest()
    hook["member"] = hook_entries[0]
    nbd = f"usr/lib/modules/{release}/kernel/drivers/block/nbd.ko.zst"
    if nbd not in normalized:
        fail(f"{path}: missing nbd module for the guarded netboot fallback")
    deviceinfo = archive_member(path, "usr/share/deviceinfo/device-oneplus-guacamole").decode(errors="replace")
    if not re.search(r'^deviceinfo_rootfs_image_sector_size="4096"$', deviceinfo, re.M):
        fail(f"{path}: deviceinfo does not set the 4096-byte sector size")
    functions = archive_member(path, "init_functions.sh").decode(errors="replace")
    functions += "\n" + archive_member(path, "init_functions_2nd.sh").decode(errors="replace")
    missing_tokens = [token for token in REQUIRED_INITRAMFS_FUNCTIONS if token not in functions]
    if missing_tokens:
        fail(f"{path}: initramfs lacks nested-GPT/resize code: {', '.join(missing_tokens)}")
    return {
        "archive": str(path.resolve()),
        "release": release,
        "entries": len(entries),
        "hook": hook,
        "deviceinfo_sector_size": 4096,
    }


def rootfs_hook_report(rootfs_tree: Path, initramfs: Path | None = None) -> dict:
    path = Path(rootfs_tree) / "usr/share/mkinitfs/hooks/netboot.sh"
    if not path.is_file() or path.is_symlink():
        fail(f"installed rootfs has no real netboot hook: {path}")
    text = path.read_text(errors="replace")
    guard = validate_netboot_hook(text)
    report = {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "guarded": guard["guarded"],
        "initramfs_sha256": None,
    }
    if initramfs is not None:
        archive = initramfs_report(Path(initramfs), _release_for(Path(initramfs)))
        if archive["hook"]["sha256"] != report["sha256"]:
            fail("installed rootfs hook differs from the initramfs hook; mkinitfs would not reproduce the guard")
        report["initramfs_sha256"] = archive["hook"]["sha256"]
    return report


def _release_for(initramfs: Path) -> str:
    releases = sorted(cir.module_releases(cir.list_archive(Path(initramfs))))
    if len(releases) != 1:
        fail(f"{initramfs}: cannot determine a single module release: {releases}")
    return releases[0]


def kernel_config_report(config: Path) -> dict:
    config = Path(config)
    if not config.is_file():
        fail(f"missing kernel config: {config}")
    text = config.read_text(errors="replace")
    missing = [f"{option}={value}" for option, value in REQUIRED_KERNEL_BUILTINS.items()
               if f"{option}={value}" not in text]
    if not re.search(r"^CONFIG_BLK_DEV_NBD=[my]$", text, re.M):
        missing.append("CONFIG_BLK_DEV_NBD=m|y")
    if missing:
        fail(f"{config}: local boot needs built-in storage/loop/GPT support: {', '.join(missing)}")
    return {"path": str(config.resolve()), "required_builtins": dict(REQUIRED_KERNEL_BUILTINS)}


def check_boot_image(boot: Path, image: Path, initramfs: Path, dtb: Path) -> dict:
    try:
        parts = cpc.prove_boot_image(Path(boot), Path(image), Path(initramfs), Path(dtb), label="local boot image")
    except SystemExit as error:
        fail(f"{boot}: canonical boot-image proof failed (exit {error.code})")
    data = Path(boot).read_bytes()
    if data[-64:-60] != AVB_MAGIC:
        fail(f"{boot}: missing AVB hash footer")
    if parts.get("header_version") != 2 or parts.get("page") != 4096:
        fail(f"{boot}: expected boot header v2 with 4096-byte pages")
    cmdline = parts["cmdline"]
    for token in BOOT_REQUIRED_CMDLINE:
        if token not in cmdline:
            fail(f"{boot}: cmdline lacks {token}: {cmdline!r}")
    for token in BOOT_FORBIDDEN_CMDLINE:
        if token in cmdline:
            fail(f"{boot}: local boot image still has {token}: {cmdline!r}")
    header = struct.unpack_from("<9I", data, 8)
    kernel_addr, ramdisk_addr, tags_addr = header[1], header[3], header[6]
    dtb_offset = struct.unpack_from("<Q", data, 1652)[0]
    for key, actual, expected in (
        ("kernel_offset", kernel_addr, 0x8000),
        ("ramdisk_offset", ramdisk_addr, 0x01000000),
        ("tags_offset", tags_addr, 0x100),
        ("dtb_offset", dtb_offset, 0x03000000),
    ):
        if actual != expected:
            fail(f"{boot}: {key} is {actual:#x}, expected {expected:#x}")
    return {
        "path": str(Path(boot).resolve()),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "cmdline": cmdline,
        "header_version": 2,
        "page_size": 4096,
        "kernel_sha256": hashlib.sha256(parts["kernel"]).hexdigest(),
        "ramdisk_sha256": hashlib.sha256(parts["ramdisk"]).hexdigest(),
        "dtb_sha256": hashlib.sha256(parts["dtb"]).hexdigest(),
        "dtb_offset": dtb_offset,
        "avb_footer": True,
    }


def parse_gpt(path: Path, sector_size: int = SECTOR_SIZE) -> dict:
    path = Path(path)
    size = path.stat().st_size
    if size % sector_size:
        fail(f"{path}: size {size} is not a multiple of {sector_size}")
    sectors = size // sector_size
    with path.open("rb") as stream:
        mbr = stream.read(512)
        if mbr[510:512] != b"\x55\xaa":
            fail(f"{path}: missing protective MBR signature")
        stream.seek(sector_size)
        header = stream.read(92)
        if header[:8] != b"EFI PART":
            fail(f"{path}: missing GPT header at LBA 1")
        (revision, header_size, _crc, _reserved, current_lba, backup_lba,
         first_usable, last_usable, _guid, entries_lba, entry_count,
         entry_size, _entry_crc) = struct.unpack_from("<IIIIQQQQ16sQIII", header, 8)
        if header_size < 92 or current_lba != 1:
            fail(f"{path}: invalid primary GPT header")
        if backup_lba != sectors - 1:
            fail(f"{path}: GPT backup LBA {backup_lba} is not the end of the image ({sectors - 1})")
        stream.seek(entries_lba * sector_size)
        table = stream.read(entry_count * entry_size)
        if len(table) < entry_count * entry_size:
            fail(f"{path}: truncated GPT entry array")
        partitions = []
        for index in range(entry_count):
            entry = table[index * entry_size:(index + 1) * entry_size]
            if len(entry) < 128 or entry[:16] == b"\0" * 16:
                continue
            first_lba, last_lba, attrs = struct.unpack_from("<QQQ", entry, 32)
            partitions.append({
                "index": index + 1,
                "first_lba": first_lba,
                "last_lba": last_lba,
                "offset": first_lba * sector_size,
                "size": (last_lba - first_lba + 1) * sector_size,
                "attrs": attrs,
                "name": entry[56:128].decode("utf-16le", errors="replace").rstrip("\0"),
            })
    return {
        "path": str(path.resolve()),
        "size": size,
        "sectors": sectors,
        "sector_size": sector_size,
        "backup_lba": backup_lba,
        "first_usable": first_usable,
        "last_usable": last_usable,
        "partitions": partitions,
    }


def blkid_at(path: Path, offset: int) -> dict:
    result = subprocess.run(["blkid", "-p", "-O", str(offset), str(path)], capture_output=True)
    if result.returncode != 0:
        fail(f"{path}: blkid cannot read a filesystem at offset {offset}")
    values = {}
    for match in re.finditer(r'([A-Z0-9_]+)="([^"]*)"', result.stdout.decode(errors="replace")):
        values[match.group(1)] = match.group(2)
    return values


def check_rootfs_image(path: Path, rootfs_tree: Path | None = None) -> dict:
    gpt = parse_gpt(path)
    if len(gpt["partitions"]) != 2:
        fail(f"{path}: expected exactly 2 nested partitions, found {len(gpt['partitions'])}")
    first, second = gpt["partitions"]
    boot_fs, root_fs = blkid_at(path, first["offset"]), blkid_at(path, second["offset"])
    if boot_fs.get("LABEL") != "pmOS_boot" or boot_fs.get("TYPE") != "ext2":
        fail(f"{path}: p1 is not LABEL=pmOS_boot TYPE=ext2")
    if root_fs.get("LABEL") != "pmOS_root" or root_fs.get("TYPE") != "ext4":
        fail(f"{path}: p2 is not LABEL=pmOS_root TYPE=ext4")
    if second["last_lba"] > gpt["last_usable"]:
        fail(f"{path}: p2 ends beyond the GPT last usable LBA")
    fstab_report = None
    if rootfs_tree is not None:
        fstab = Path(rootfs_tree) / "etc/fstab"
        text = fstab.read_text(errors="replace") if fstab.is_file() else ""
        root_uuid = re.search(r"^UUID=(\S+)\s+/\s+(\S+)", text, re.M)
        boot_uuid = re.search(r"^UUID=(\S+)\s+/boot\s+(\S+)", text, re.M)
        if not root_uuid or not boot_uuid:
            fail(f"{fstab}: does not pin / and /boot by UUID")
        if root_uuid.group(1) != root_fs.get("UUID") or root_uuid.group(2) != "ext4":
            fail(f"{fstab}: / UUID/type does not match the image root filesystem")
        if boot_uuid.group(1) != boot_fs.get("UUID") or boot_uuid.group(2) != "ext2":
            fail(f"{fstab}: /boot UUID/type does not match the image boot filesystem")
        fstab_report = {"path": str(fstab.resolve()), "root_uuid": root_uuid.group(1),
                        "boot_uuid": boot_uuid.group(1)}
    return {"gpt": gpt, "boot_fs": boot_fs, "root_fs": root_fs, "fstab": fstab_report}


def port_listening(port: int = 10001) -> bool:
    if shutil.which("ss") is None:
        fail("ss is required to prove that the netboot port is not live")
    result = subprocess.run(["ss", "-ltn"], capture_output=True)
    if result.returncode != 0:
        fail("ss could not list listeners; refusing to hash a possibly served image")
    return any(re.search(rf"(?:^|[\s:]){port}(?:\s|$)", line)
               for line in result.stdout.decode(errors="replace").splitlines())


def refuse_served_image(path: Path, served_record: Path | None = None) -> None:
    path = Path(path).resolve()
    if port_listening(10001):
        fail("port 10001 is listening; refusing to read/hash a possibly served rootfs image")
    if "images_netboot" in path.parts:
        fail(f"{path}: refusing the live netboot cache; use the source image")
    if served_record is not None and Path(served_record).is_file():
        record = json.loads(Path(served_record).read_text())
        if record.get("path") and Path(record["path"]).resolve() == path:
            fail(f"{path}: this path is recorded as the served image; stop the server first")


def check_facts(facts: dict, image_size: int, *, require_backups: bool, require_recovery: bool) -> dict:
    errors, warnings = [], []
    current_slot = facts.get("current_slot")
    if current_slot not in ("a", "b"):
        errors.append("current_slot must be explicitly 'a' or 'b' (never inferred)")
    for key in ("slot_successful", "slot_unbootable"):
        value = facts.get(key)
        if not isinstance(value, dict) or not all(slot in value for slot in ("a", "b")):
            errors.append(f"{key} must contain explicit a and b booleans")
    userdata = facts.get("userdata") or {}
    size = userdata.get("size_bytes")
    if not isinstance(size, int) or size <= 0:
        errors.append("userdata.size_bytes must be a positive integer")
    elif size <= image_size:
        errors.append(f"userdata ({size} B) is not larger than the image ({image_size} B)")
    elif size - image_size < 1024 ** 3:
        warnings.append(f"userdata exceeds the image by only {size - image_size} B")
    if current_slot == "b":
        warnings.append(
            "current Android slot is b; a later boot_b write would overwrite the active "
            "Android boot chain. This check does not select, zero or change a slot."
        )
    unbootable = facts.get("slot_unbootable")
    if isinstance(unbootable, dict) and unbootable.get(current_slot) is True:
        warnings.append(f"slot {current_slot} is recorded unbootable; recover before any userdata write")
    backups = facts.get("backups")
    if not isinstance(backups, dict):
        backups = {}
        if require_backups:
            errors.append("backups must be an object with boot_b/dtbo_b/vbmeta_b records")
    if require_backups:
        for name in ("boot_b", "dtbo_b", "vbmeta_b"):
            entry = backups.get(name)
            if not isinstance(entry, dict):
                errors.append(f"backups.{name} is missing")
                continue
            if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                errors.append(f"backups.{name}.sha256 is not a lowercase sha256")
            if not isinstance(entry.get("size"), int) or entry["size"] <= 0:
                errors.append(f"backups.{name}.size must be positive")
    recovery = facts.get("recovery")
    if not isinstance(recovery, dict):
        recovery = {}
        if require_recovery:
            errors.append("recovery must be an object with the LineageOS artifacts")
    if require_recovery:
        for name in ("lineage_zip", "boot_img", "dtbo_img", "vbmeta_img"):
            if not isinstance(recovery.get(name), dict):
                errors.append(f"recovery.{name} is missing")
    if errors:
        fail("; ".join(errors))
    return {"current_slot": current_slot, "warnings": warnings}


def record_manifest(args: argparse.Namespace) -> dict:
    boot_img, initramfs = Path(args.boot_img).resolve(), Path(args.initramfs).resolve()
    kernel_dir, image = Path(args.kernel_dir).resolve(), Path(args.image).resolve()
    kernel_state = Path(args.kernel_state).resolve()
    release_file = kernel_dir / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
    if not release_file.is_file() or release_file.read_text().strip() != args.release:
        fail(f"kernel package is not {args.release}: {release_file}")
    initramfs_facts = initramfs_report(initramfs, args.release)
    config = kernel_config_report(kernel_state / "output/.config")
    wrapper_manifest_path = Path(args.dtb_dir).resolve() / "manifest.json"
    if not wrapper_manifest_path.is_file():
        fail(f"missing DTB wrapper manifest: {wrapper_manifest_path}")
    wrapper_manifest = json.loads(wrapper_manifest_path.read_text())
    if wrapper_manifest.get("status") != "PASS" or not all(
        value is True or value == "PASS" for value in wrapper_manifest.get("checks", {}).values()
    ):
        fail(f"{wrapper_manifest_path}: DTB wrapper is not PASS")
    base_dtb = kernel_dir / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
    wrapper = Path(args.dtb_dir).resolve() / "custom_guacamole-abl-quarantine.dtb"
    if wrapper_manifest["inputs"]["base_dtb"]["sha256"] != sha256_file(base_dtb):
        fail("DTB wrapper base does not match the kernel package DTB")
    if wrapper_manifest["artifacts"]["wrapper"]["sha256"] != sha256_file(wrapper):
        fail("DTB wrapper bytes do not match the wrapper manifest")
    boot = check_boot_image(boot_img, image, initramfs, wrapper)

    rootfs = None
    installed_hook = None
    if not args.rootfs_image or not args.rootfs_tree:
        if not args.inspection_only:
            fail("readiness requires --rootfs-image and --rootfs-tree (installed hook included)")
    else:
        rootfs_path, tree = Path(args.rootfs_image).resolve(), Path(args.rootfs_tree).resolve()
        refuse_served_image(rootfs_path, Path(args.served_record) if args.served_record else None)
        installed_hook = rootfs_hook_report(tree, initramfs)
        rootfs = {
            "record": file_record(rootfs_path),
            "tree_path": str(tree.resolve()),
            "tree": file_record(tree / "etc/fstab"),
            "checks": check_rootfs_image(rootfs_path, tree),
        }
    status = "INSPECTION-ONLY" if rootfs is None else "HOST-VERIFIED"
    manifest = {
        "schema": 1,
        "status": status,
        "hardware": "UNTESTED",
        "profile": "local-userdata",
        "ui": getattr(cpc, "UI", "plasma"),
        "identity": {
            "kernel_release": args.release,
            "ui_release_candidate": getattr(cpc, "RELEASE_CANDIDATE", None),
            "release_candidate": "nura-local-storage-rc4" if args.release == "7.0.0-sm8150-guacamole-rc4" else args.release,
            "future_release_rule": "a rebuilt release is 7.0.0-sm8150-guacamole-rc4; never relabel rc3",
        },
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "worktree": str(Path(args.workspace).resolve()),
        "inputs": {
            "kernel_package": file_record(kernel_dir / "boot/vmlinuz"),
            "kernel_dtb": file_record(base_dtb),
            "kernel_image": file_record(image),
            "initramfs": file_record(initramfs),
            "temporary_dtb_manifest": file_record(wrapper_manifest_path),
            "wrapper_dtb": file_record(wrapper),
            "sources": [file_record(Path(path)) for path in (args.tool, args.builder, args.dtb_preparer) if path],
        },
        "boot_img": boot,
        "checks": {
            "initramfs": initramfs_facts,
            "installed_hook": installed_hook,
            "kernel_builtins": config,
            "boot_payloads_match": True,
            "boot_cmdline_local": True,
            "dtb_wrapper_verified": True,
        },
        "rootfs_image": rootfs,
        "blockers": [] if rootfs else ["inspection only: rootfs image / installed-hook identity is not recorded"],
    }
    if args.out:
        Path(args.out).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def check_manifest(args: argparse.Namespace) -> dict:
    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text())
    readiness = manifest.get("status") == "HOST-VERIFIED"
    if not readiness and not args.inspection_only:
        fail(f"{manifest_path}: status {manifest.get('status')!r} is not final HOST-VERIFIED readiness")
    if not manifest.get("rootfs_image") and not args.inspection_only:
        fail(f"{manifest_path}: final readiness has no rootfs image / installed hook")
    for key in ("boot_img", "initramfs", "kernel_package", "kernel_dtb", "kernel_image",
                "wrapper_dtb", "temporary_dtb_manifest"):
        entry = manifest["boot_img"] if key == "boot_img" else manifest["inputs"][key]
        actual = file_record(Path(entry["path"]))
        if actual["sha256"] != entry.get("sha256") or actual["size"] != entry.get("size"):
            fail(f"{key} changed since the manifest was recorded: {entry['path']}")
    boot = check_boot_image(
        Path(manifest["boot_img"]["path"]),
        Path(manifest["inputs"]["kernel_image"]["path"]),
        Path(manifest["inputs"]["initramfs"]["path"]),
        Path(manifest["inputs"]["wrapper_dtb"]["path"]),
    )
    if boot["sha256"] != manifest["boot_img"]["sha256"]:
        fail("boot image hash differs from the recorded manifest")
    kernel_config_report(Path(manifest["checks"]["kernel_builtins"]["path"]))
    initramfs_report(Path(manifest["inputs"]["initramfs"]["path"]), manifest["identity"]["kernel_release"])
    if manifest.get("rootfs_image"):
        rootfs = manifest["rootfs_image"]
        rootfs_path = Path(rootfs["record"]["path"])
        refuse_served_image(rootfs_path, Path(args.served_record) if args.served_record else None)
        actual = file_record(rootfs_path)
        if actual["sha256"] != rootfs["record"]["sha256"]:
            fail(f"rootfs image changed since the manifest was recorded: {rootfs_path}")
        if rootfs.get("checks", {}).get("fstab") is None:
            fail("recorded rootfs image has no fstab identity")
        tree_record = rootfs.get("tree") or {}
        if tree_record.get("path"):
            actual_tree = file_record(Path(tree_record["path"]))
            if actual_tree["sha256"] != tree_record.get("sha256"):
                fail("rootfs fstab changed since the manifest was recorded")
        check_rootfs_image(rootfs_path)
        hook = manifest["checks"].get("installed_hook") or {}
        if not hook.get("path") or not hook.get("sha256"):
            fail("recorded installed-hook identity is missing")
        tree = rootfs.get("tree_path")
        if not tree:
            fail("recorded rootfs tree path is missing")
        current = rootfs_hook_report(Path(tree), Path(manifest["inputs"]["initramfs"]["path"]))
        if current["sha256"] != hook["sha256"]:
            fail("installed rootfs hook changed since the manifest was recorded")
    elif not args.inspection_only:
        fail("final readiness has no rootfs image")
    if args.facts:
        facts = json.loads(Path(args.facts).read_text())
        check_facts(facts, (manifest["rootfs_image"]["record"]["size"] if manifest.get("rootfs_image")
                            else manifest["boot_img"]["size"]),
                    require_backups=args.require_backups, require_recovery=args.require_recovery)
    elif args.require_backups or args.require_recovery:
        fail("--require-backups/--require-recovery need --facts")
    print(f"PASS: {manifest_path} ({manifest['identity']['kernel_release']}, {manifest['status']})")
    return manifest


def probes() -> None:
    print("Read-only probes. This tool executes none and prints no flash/erase/slot command.")
    print()
    print("Host (no port-10001 server):")
    print("  ss -ltn | grep -E ':1000[01]' || true")
    print("  python3 local_storage_tool.py check --manifest <DELIVERY.local.json> --facts <facts.json> --require-backups")
    print("  python3 local_storage_tool.py check-rootfs-hook --rootfs-tree <rootfs-tree> --initramfs <initramfs>")
    print("  sha256sum <boot.img> <initramfs> <source.img>")
    print()
    print("Phone, from the netboot or pmos.debug-shell initramfs (read-only):")
    print("  cat /proc/cmdline")
    print("  blockdev --getsize64 /dev/disk/by-partlabel/userdata")
    print("  fdisk -l /dev/disk/by-partlabel/userdata")
    print("  lsblk -o NAME,SIZE,LOG-SEC,PARTLABEL")
    print("  dmesg | grep -i -e ufs -e OCS -e 'Resize'")
    print()
    print("Bootloader, read-only getvars (record exact output in facts.json):")
    for name in ("current-slot", "unlocked", "partition-size:userdata", "partition-size:boot_b",
                 "partition-size:dtbo_b", "slot-successful:a", "slot-successful:b",
                 "slot-unbootable:a", "slot-unbootable:b"):
        print(f"  fastboot getvar {name}")
    print("  fastboot oem device-info")
    print()
    print("Backups are reads from phone to host (boot_b/dtbo_b/vbmeta_b + sha256sum).")
    print("Exact userdata-write and boot_b/slot commands are deliberately withheld.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check-initramfs")
    p.add_argument("--archive", required=True)
    p.add_argument("--release", default="7.0.0-sm8150-guacamole-rc4")
    p.add_argument("--require-hook", action="store_true",
                   help="accepted for the canonical packer interface; guarded hooks are always required")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check-rootfs-hook")
    p.add_argument("--rootfs-tree", required=True)
    p.add_argument("--initramfs")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("record")
    p.add_argument("--boot-img", required=True)
    p.add_argument("--initramfs", required=True)
    p.add_argument("--kernel-dir", required=True)
    p.add_argument("--kernel-state", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--dtb-dir", required=True)
    p.add_argument("--release", default="7.0.0-sm8150-guacamole-rc4")
    p.add_argument("--rootfs-image")
    p.add_argument("--rootfs-tree")
    p.add_argument("--inspection-only", action="store_true")
    p.add_argument("--served-record")
    p.add_argument("--out")
    p.add_argument("--workspace", default=str(cpc.ROOT))
    p.add_argument("--tool", default=str(Path(__file__).resolve()))
    p.add_argument("--builder")
    p.add_argument("--dtb-preparer")
    p = sub.add_parser("check")
    p.add_argument("--manifest", required=True)
    p.add_argument("--facts")
    p.add_argument("--served-record")
    p.add_argument("--inspection-only", action="store_true")
    p.add_argument("--require-backups", action="store_true")
    p.add_argument("--require-recovery", action="store_true")
    sub.add_parser("probes")
    args = parser.parse_args(argv)
    try:
        if args.command == "check-initramfs":
            report = initramfs_report(Path(args.archive), args.release)
            print(json.dumps(report, indent=2, sort_keys=True) if args.json else
                  f"PASS: {args.archive} release={args.release} hook={report['hook']['sha256']}")
        elif args.command == "check-rootfs-hook":
            report = rootfs_hook_report(Path(args.rootfs_tree), Path(args.initramfs) if args.initramfs else None)
            print(json.dumps(report, indent=2, sort_keys=True) if args.json else
                  f"PASS: {report['path']} guarded={report['guarded']} sha256={report['sha256']}")
        elif args.command == "record":
            manifest = record_manifest(args)
            print(f"PASS: {manifest['boot_img']['path']} status={manifest['status']}")
            if args.out:
                print(f"manifest: {args.out}")
        elif args.command == "check":
            check_manifest(args)
        else:
            probes()
    except LocalError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
