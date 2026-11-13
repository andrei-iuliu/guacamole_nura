"""Rootless integration through the real copied rootfs/checker pipeline.

The source checkout is a temporary copy of the canonical test fixture; the
flow scripts and recipes are temporary copies of flows/local-storage.  A fake
pmbootstrap/admin layer supplies package/install/export outputs.  The actual
copied rootfs_prepare_local.sh and check_local_candidate.py execute.
"""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import struct
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

FLOW = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get(
    "NURA_SOURCE_ROOT",
    "runtime-workspace",
)).resolve()
sys.path.insert(0, str(SOURCE / "tests"))
import test_rootfs_prepare_plasma as canonical  # noqa: E402

RELEASE = "7.0.0-sm8150-rc3"


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_flow(tmp: Path) -> Path:
    flow = tmp / "flow"
    shutil.copytree(
        FLOW, flow,
        ignore=shutil.ignore_patterns(".work", "build", "__pycache__", "*.pyc"),
    )
    return flow


def guarded_initramfs(tmp: Path, hook: str) -> Path:
    root = tmp / "guarded-initramfs-root"
    write(root / "hooks/netboot.sh", hook)
    write(root / "init_functions.sh", "mount_subpartitions\nSUBPARTITION_LOOP\n"
          "--sector-size\npmOS_root\nresizepart\nresize2fs\n")
    write(root / "init_functions_2nd.sh", "mount_subpartitions\n")
    write(root / "usr/share/deviceinfo/device-oneplus-guacamole",
          'deviceinfo_rootfs_image_sector_size="4096"\n')
    write(root / f"usr/lib/modules/{RELEASE}/modules.dep", "")
    write(root / f"usr/lib/modules/{RELEASE}/kernel/drivers/block/nbd.ko.zst", b"nbd")
    for name in ("losetup", "parted", "resize2fs", "e2fsck", "blkid", "switch_root"):
        write(root / f"usr/bin/{name}", b"binary")
    cpio = subprocess.run(
        ["sh", "-c", "find . -print | cpio -o -H newc"],
        cwd=root, check=True, capture_output=True,
    ).stdout
    archive = tmp / "guarded-initramfs"
    archive.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=cpio, check=True,
                                       capture_output=True).stdout)
    return archive


FAKE_PREPARER = """#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument("--base-dtb", required=True)
parser.add_argument("--base-source-dts")
parser.add_argument("--patched-dts")
parser.add_argument("--out-dir", required=True)
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
base = Path(args.base_dtb)
out = Path(args.out_dir)
out.mkdir(parents=True, exist_ok=True)
wrapper = out / "custom_guacamole-abl-quarantine.dtb"
wrapper.write_bytes(base.read_bytes())
data = {
    "status": "PASS", "hardware": "UNTESTED",
    "checks": {"deep_validator": "PASS", "host_tool_hashes": True},
    "inputs": {"base_dtb": {"sha256": hashlib.sha256(base.read_bytes()).hexdigest()}},
    "artifacts": {"wrapper": {"sha256": hashlib.sha256(wrapper.read_bytes()).hexdigest()}},
}
(out / "manifest.json").write_text(json.dumps(data))
"""

PMB_SCRIPT = '''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path

args = sys.argv[1:]
def option(name):
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return Path(args[index + 1])
    return None

work = option("-w")
if work is None:
    raise SystemExit(2)
log = os.environ.get("FAKE_PMB_LOG")
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(" ".join(args) + "\\n")

versions = {
    "linux-postmarketos-qcom-sm8150": "7.0.0-sm8150-rc3-r0",
    "postmarketos-mkinitfs-hook-netboot": "4.2.1-r5",
    "postmarketos-ui-plasma-mobile": "6-r10",
    "modemmanager": "1.25.95_git20261005-r1",
    "libmm-glib": "1.25.95_git20261005-r1",
    "modemmanager-systemd": "1.25.95_git20261005-r1",
    "modemmanager-udev": "1.25.95_git20261005-r1",
    "modemmanager-lang": "1.25.95_git20261005-r1",
    "firmware-oneplus-guacamole": "1-r0",
    "device-oneplus-guacamole": "1-r0",
    "hexagonrpcd": "1-r0",
    "iio-sensor-proxy": "1-r0",
    "nura-clock": "0.1.0-r0",
}

if "chroot" in args and "abuild-sign" in args and args[-2] == "abuild-sign":
    # Model only the APK signature container; no private key/crypto/admin action.
    import io, tarfile, gzip
    control = work / ("chroot_native" + args[-1])
    member = io.BytesIO()
    with tarfile.open(fileobj=member, mode="w") as archive:
        info = tarfile.TarInfo(".SIGN.RSA.fixture.pub")
        info.size = 7
        archive.addfile(info, io.BytesIO(b"fixture"))
    control.write_bytes(gzip.compress(member.getvalue()) + control.read_bytes())
    raise SystemExit(0)

if "build" in args:
    package = args[-1]
    version = versions.get(package)
    if version:
        destination = work / "packages/edge/aarch64"
        destination.mkdir(parents=True, exist_ok=True)
        names = [package]
        if package == "modemmanager":
            names += ["libmm-glib", "modemmanager-systemd", "modemmanager-udev", "modemmanager-lang"]
        for name in names:
            item_version = versions[name]
            (destination / f"{name}-{item_version}.apk").write_bytes((name + item_version).encode())
    raise SystemExit(0)

if "install" in args:
    work.joinpath("chroot_native/home/pmos/rootfs").mkdir(parents=True, exist_ok=True)
    image = work / "chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
    shutil.copy2(os.environ["FAKE_ROOTFS_IMAGE"], image)
    tree = work / "chroot_rootfs_oneplus-guacamole"
    if tree.exists():
        shutil.rmtree(tree)
    shutil.copytree(os.environ["FAKE_ROOTFS_TREE"], tree, symlinks=True)
    shutil.copy2(os.environ["FAKE_INITRAMFS"], tree / "boot/initramfs")
    raise SystemExit(0)

if "export" in args:
    index = args.index("export")
    if index + 1 < len(args):
        destination = Path(args[index + 1])
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(os.environ["FAKE_INITRAMFS"], destination / "initramfs")
    raise SystemExit(0)

# chroot/sign/verify/shutdown are intentionally no-ops in this fake layer.
raise SystemExit(0)
'''


def make_gpt_image(tmp: Path) -> Path:
    """Small 4096-byte-sector GPT: p1 ext2 8 MiB, p2 ext4 32 MiB."""
    path = tmp / "rootfs.img"
    sector = 4096
    sectors = 64 * 1024 * 1024 // sector
    path.write_bytes(b"\0" * (sectors * sector))
    p1_first, p1_last = 2048, 4095
    p2_first, p2_last = 4096, 12287
    backup = sectors - 1
    last_usable = backup - 5
    mbr = bytearray(512)
    mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\0\0\0", 0xEE,
                               b"\xff\xff\xff", 1, min(sectors - 1, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xaa"

    def entry(first, last, guid, name):
        out = bytearray(128)
        out[0:16] = bytes([guid]) * 16
        out[16:32] = bytes([guid + 1]) * 16
        struct.pack_into("<QQQ", out, 32, first, last, 0)
        encoded = name.encode("utf-16le")
        out[56:56 + len(encoded)] = encoded
        return bytes(out)

    table = bytearray(128 * 128)
    table[0:128] = entry(p1_first, p1_last, 1, "primary")
    table[128:256] = entry(p2_first, p2_last, 3, "primary")

    def header(current, alternate, entries_lba):
        out = bytearray(92)
        out[0:8] = b"EFI PART"
        struct.pack_into("<IIIIQQQQ16sQIII", out, 8, 0x10000, 92, 0,
                         0, current, alternate, 6, last_usable,
                         b"\0" * 16, entries_lba, 128, 128, 0)
        return bytes(out)

    with path.open("r+b") as stream:
        stream.seek(0); stream.write(mbr)
        stream.seek(sector); stream.write(header(1, backup, 2))
        stream.seek(2 * sector); stream.write(table)
        stream.seek((backup - 4) * sector); stream.write(table)
        stream.seek(backup * sector); stream.write(header(backup, 1, backup - 4))
    subprocess.run(["mkfs.ext2", "-q", "-F", "-b", "4096", "-L", "pmOS_boot",
                    "-E", f"offset={p1_first * sector}", str(path), "2048"], check=True)
    subprocess.run(["mkfs.ext4", "-q", "-F", "-b", "4096", "-L", "pmOS_root",
                    "-E", f"offset={p2_first * sector}", str(path), "8192"], check=True)
    return path


class LocalRootfsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.tmp = Path(self.temporary.name)
        self.fixture = canonical.PlasmaPreparationTests("test_check_only_is_read_only")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.addCleanup(self.temporary.cleanup)
        self.source_root = self.fixture.root
        self.flow = copy_flow(self.tmp)
        self.log = self.tmp / "pmb.log"

        # The copied rootfs pipeline must execute the real copied checker and
        # recipes, with the canonical fixture acting as the read-only source.
        write(self.source_root / ".work/rear-camera/pmbootstrap/pmbootstrap.py", PMB_SCRIPT)
        write(self.fixture.bin / "python3", "#!/usr/bin/env bash\nexec /usr/bin/python3 \"$@\"\n")
        (self.fixture.bin / "python3").chmod(0o755)
        # Canonical fixture fakes zstd as failing; the real archive checks need it.
        (self.fixture.bin / "zstd").unlink(missing_ok=True)
        # The entrypoint's boot builder uses the source tools and the original
        # DTB preparer read-only; point them at the real canonical checkout.
        (self.source_root / "local").symlink_to(SOURCE / "local", target_is_directory=True)
        preparer = self.source_root / "scripts/prepare_temporary_dtb.py"
        if preparer.exists() or preparer.is_symlink():
            preparer.unlink()
        write(preparer, FAKE_PREPARER)
        hook_source = SOURCE / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
        hook_target = self.source_root / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
        for name in ("APKBUILD", "netboot.sh", "netboot.modules"):
            shutil.copy2(hook_source / name, hook_target / name)

        # Give the fake kernel a real gzip arm64 Image envelope so the actual
        # boot packer/envelope checks can run.
        image = bytearray(128)
        image[0x38:0x3C] = b"ARM\x64"
        struct.pack_into("<Q", image, 8, 0)
        struct.pack_into("<Q", image, 16, len(image))
        write(self.source_root / ".work/rc3-kernel/linux-v7.0.0-sm8150/"
              "arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts", "/dts-v1/;\n")
        write(self.source_root / ".work/rc3-kernel/output/.config", "\n".join([
            "CONFIG_EXT4_FS=y", "CONFIG_BLK_DEV_LOOP=y", "CONFIG_SCSI_UFSHCD=y",
            "CONFIG_SCSI_UFS_QCOM=y", "CONFIG_EFI_PARTITION=y", "CONFIG_EXT4_USE_FOR_EXT2=y",
            "CONFIG_DEVTMPFS=y", "CONFIG_PROC_FS=y", "CONFIG_SYSFS=y",
            "CONFIG_BLK_DEV_INITRD=y", "CONFIG_BLK_DEV_NBD=m", "",
        ]))
        kernel_pkg = self.source_root / "build/rc3-kernel/kernel-package"
        write(kernel_pkg / "boot/Image", bytes(image))
        write(kernel_pkg / "boot/vmlinuz", gzip.compress(bytes(image)))
        write(self.source_root / "build/rc3-kernel/Image", bytes(image))
        record = subprocess.run(
            [sys.executable, str(self.source_root / "scripts/check_plasma_candidate.py"), "kernel-record",
             "--build", str(self.source_root / "build/rc3-kernel"),
             "--state", str(self.source_root / ".work/rc3-kernel"),
             "--recipe", "device/testing/rc3/linux-postmarketos-qcom-sm8150"],
            cwd=self.source_root, capture_output=True, text=True,
        )
        self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
        # The real canonical output has absolute source/Makefile links; the
        # local copy must neutralise them and exclude source-local .output.
        state = self.source_root / ".work/rc3-kernel"
        (state / "output/source").symlink_to(state / "linux-v7.0.0-sm8150")
        write(state / "output/Makefile",
              f"include {state / 'linux-v7.0.0-sm8150'}/Makefile\n")
        write(state / "linux-v7.0.0-sm8150/.output/marker", "must not be copied\n")

        out, rootfs = self.fixture.make_candidate_fixture()
        installed_db = rootfs / "lib/apk/db/installed"
        installed_db.write_text(installed_db.read_text().replace(
            "P:powerdevil\nV:6.8.2-r0", "P:powerdevil\nV:6.8.3-r0") + "\nP:nura-clock\nV:0.1.0-r0\n\n")
        write(rootfs / "usr/lib/clock-epoch", "1794268800\n")
        os.utime(rootfs / "usr/lib/clock-epoch", (1794268800, 1794268800))
        write(self.source_root / "build/plasma-mobile/DELIVERY.json", "{}\n")
        synthetic_rootfs = make_gpt_image(self.tmp)
        p1_uuid = subprocess.run(["blkid", "-p", "-O", str(2048 * 4096), "-s", "UUID", "-o", "value",
                                  str(synthetic_rootfs)], capture_output=True, text=True, check=True).stdout.strip()
        p2_uuid = subprocess.run(["blkid", "-p", "-O", str(4096 * 4096), "-s", "UUID", "-o", "value",
                                  str(synthetic_rootfs)], capture_output=True, text=True, check=True).stdout.strip()
        write(rootfs / "etc/fstab",
              f"UUID={p2_uuid} / ext4 defaults 0 0\n"
              f"UUID={p1_uuid} /boot ext2 nodev,nosuid,noexec 0 0\n")
        db = rootfs / "lib/apk/db/installed"
        db.write_text(db.read_text().replace("P:powerdevil\nV:6.8.2-r0", "P:powerdevil\nV:6.8.3-r0"))
        hook = (self.flow / "device/testing/rear-camera/"
                "postmarketos-mkinitfs-hook-netboot/netboot.sh").read_text()
        archive = guarded_initramfs(self.tmp, hook)
        write(rootfs / "usr/share/mkinitfs/hooks/netboot.sh", hook)
        shutil.copy2(archive, rootfs / "boot/initramfs")
        self.fake_env = {
            "FAKE_PMB_LOG": str(self.log),
            "FAKE_ROOTFS_TREE": str(rootfs),
            "FAKE_ROOTFS_IMAGE": str(synthetic_rootfs),
            "FAKE_INITRAMFS": str(archive),
        }

    def run_flow(self, *arguments, local=True):
        env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}", **self.fake_env}
        env["BUILD_TEST_SUDO"] = str(self.fixture.sudo_calls)
        env["BUILD_TEST_CALLS"] = str(self.fixture.calls)
        env["BUILD_TEST_PYTHON"] = sys.executable
        env["NURA_SOURCE_ROOT"] = str(self.source_root)
        env["NURA_KERNEL_STATE"] = str(self.source_root / ".work/rc3-kernel")
        env["NURA_KERNEL_BUILD"] = str(self.source_root / "build/rc3-kernel")
        env["NURA_ROOTFS_STATE"] = str(self.flow / ".work/local-userdata/rootfs-state")
        env["NURA_ROOTFS_OUT"] = str(self.flow / "build/local-userdata")
        env["NURA_HELPER_SCRIPTS"] = str(self.flow / "scripts")
        env["NURA_LOCAL_TOOL"] = str(self.flow / "scripts/local_storage_tool.py")
        if local:
            env["NURA_LOCAL_USERDATA"] = "1"
            env["NURA_LOCAL_HOOK_RECIPE"] = str(
                self.flow / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot")
            env["NURA_NETBOOT_HOOK_VERSION"] = "4.2.1-r5"
        else:
            env.pop("NURA_LOCAL_USERDATA", None)
            env.pop("NURA_LOCAL_HOOK_RECIPE", None)
            env.pop("NURA_NETBOOT_HOOK_VERSION", None)
        return subprocess.run(
            ["bash", str(self.flow / "scripts/rootfs_prepare_local.sh"), *arguments],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=120,
        )

    def test_actual_patched_rootfs_reaches_local_intermediate(self):
        result = self.run_flow("--build")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        intermediate = self.flow / "build/local-userdata/ROOTFS-INTERMEDIATE.txt"
        self.assertTrue(intermediate.is_file(), result.stdout + result.stderr)
        self.assertIn("status=INTERMEDIATE-ROOTFS-ONLY", intermediate.read_text())
        self.assertFalse((self.flow / "build/local-userdata/boot.img").exists())
        self.assertFalse((self.flow / "build/local-userdata/DELIVERY.json").exists())
        calls = self.log.read_text().splitlines()
        hook_build = next(i for i, line in enumerate(calls) if "build" in line and
                          "postmarketos-mkinitfs-hook-netboot" in line)
        install = next(i for i, line in enumerate(calls) if "install" in line)
        self.assertLess(hook_build, install)
        clock_build = next(i for i, line in enumerate(calls) if "build" in line and "nura-clock" in line)
        self.assertLess(clock_build, install)
        self.assertIn("powerdevil=6.8.3-r0", calls[install])
        self.assertIn("nura-clock=0.1.0-r0", calls[install])
        kernel_build = next(i for i, line in enumerate(calls) if "build --envkernel" in line)
        signed = [i for i, line in enumerate(calls) if "chroot --user -- abuild-sign /home/pmos/nura-apk-signing/" in line]
        verified = [i for i, line in enumerate(calls) if "chroot -- apk verify " in line]
        self.assertEqual(len(signed), 2)
        self.assertEqual(len(verified), 2)
        self.assertLess(max(signed + verified), kernel_build)

    def test_r4_candidate_is_rejected_while_baseline_r4_is_accepted(self):
        first = self.run_flow("--build")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        env_result = self.run_flow("--build")
        self.assertEqual(env_result.returncode, 0, env_result.stdout + env_result.stderr)
        # Re-run with a candidate-policy r4 override; the guarded r5 recipe
        # must not be silently accepted as r4.
        env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}", **self.fake_env}
        env["BUILD_TEST_SUDO"] = str(self.fixture.sudo_calls)
        env["BUILD_TEST_CALLS"] = str(self.fixture.calls)
        env["BUILD_TEST_PYTHON"] = sys.executable
        env.update({
            "NURA_SOURCE_ROOT": str(self.source_root),
            "NURA_KERNEL_STATE": str(self.source_root / ".work/rc3-kernel"),
            "NURA_KERNEL_BUILD": str(self.source_root / "build/rc3-kernel"),
            "NURA_ROOTFS_STATE": str(self.flow / ".work/local-userdata/r4-state"),
            "NURA_ROOTFS_OUT": str(self.flow / "build/local-userdata-r4"),
            "NURA_HELPER_SCRIPTS": str(self.flow / "scripts"),
            "NURA_LOCAL_TOOL": str(self.flow / "scripts/local_storage_tool.py"),
            "NURA_LOCAL_USERDATA": "1",
            "NURA_LOCAL_HOOK_RECIPE": str(
                self.flow / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"),
            "NURA_NETBOOT_HOOK_VERSION": "4.2.1-r4",
        })
        rejected = subprocess.run(
            ["bash", str(self.flow / "scripts/rootfs_prepare_local.sh"), "--build"],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=120,
        )
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        self.assertIn("expected 4.2.1-r4", rejected.stderr + rejected.stdout)

    def test_entrypoint_actual_rootfs_to_final_manifest(self):
        env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}", **self.fake_env}
        env.update({
            "BUILD_TEST_SUDO": str(self.fixture.sudo_calls),
            "BUILD_TEST_CALLS": str(self.fixture.calls),
            "BUILD_TEST_PYTHON": sys.executable,
            "NURA_SOURCE_ROOT": str(self.source_root),
            "NURA_SERVED_RECORD": str(self.tmp / "no-served-record.json"),
        })
        result = subprocess.run(
            ["bash", str(self.flow / "scripts/local_storage_build.sh"), "--build"],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((self.flow / "build/local-userdata/DELIVERY.local.json").read_text())
        self.assertEqual(manifest["status"], "HOST-VERIFIED")
        self.assertNotIn("pmos.debug-shell", manifest["boot_img"]["cmdline"])
        original_boot = sha256(self.flow / "build/local-userdata/boot/boot.img")
        debug = subprocess.run(
            ["bash", str(self.flow / "scripts/local_storage_debug_bootimg.sh")],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(debug.returncode, 0, debug.stdout + debug.stderr)
        debug_manifest = json.loads((self.flow / "build/local-debug/DELIVERY.local.json").read_text())
        self.assertIn("pmos.debug-shell", debug_manifest["boot_img"]["cmdline"])
        self.assertNotIn("pmos.root=", debug_manifest["boot_img"]["cmdline"])
        self.assertEqual(sha256(self.flow / "build/local-userdata/boot/boot.img"), original_boot)
        self.assertTrue((self.flow / "build/local-userdata/boot/boot.img").is_file())
        self.assertTrue((self.flow / "build/local-userdata/ROOTFS-INTERMEDIATE.txt").is_file())
        self.assertIn("source rc3 inputs unchanged", result.stdout)
        local_kernel = self.flow / ".work/local-userdata/kernel-state"
        self.assertFalse((local_kernel / "linux-v7.0.0-sm8150/.output/marker").exists())
        self.assertFalse(os.path.isabs(os.readlink(local_kernel / "output/source")))
        self.assertNotIn(str(self.source_root), (local_kernel / "output/Makefile").read_text())

    def test_entrypoint_refuses_active_mount_and_lock_before_copy(self):
        state = self.flow / ".work/local-userdata"
        state.mkdir(parents=True)
        lock_path = state / ".build.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}",
                   "NURA_SOURCE_ROOT": str(self.source_root)}
            locked = subprocess.run(
                ["bash", str(self.flow / "scripts/local_storage_build.sh"), "--build"],
                cwd=self.flow, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertNotEqual(locked.returncode, 0)
            self.assertIn("already running", locked.stderr)
            self.assertFalse((state / "kernel-state").exists())
        mounted = self.tmp / "mounted-bin"
        mounted.mkdir()
        write(mounted / "findmnt", f"#!/bin/sh\nprintf '%s\\n' \"{state}\"\n")
        (mounted / "findmnt").chmod(0o755)
        env = {**os.environ, "PATH": f"{mounted}:{self.fixture.bin}:{os.environ['PATH']}",
               "NURA_SOURCE_ROOT": str(self.source_root)}
        result = subprocess.run(
            ["bash", str(self.flow / "scripts/local_storage_build.sh"), "--build"],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active mount", result.stderr)
        self.assertFalse((state / "kernel-state").exists())

    def test_entrypoint_kernel_copy_retry_reuses_and_refreshes(self):
        env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}", **self.fake_env}
        env.update({
            "BUILD_TEST_SUDO": str(self.fixture.sudo_calls),
            "BUILD_TEST_CALLS": str(self.fixture.calls),
            "BUILD_TEST_PYTHON": sys.executable,
            "NURA_SOURCE_ROOT": str(self.source_root),
            "NURA_SERVED_RECORD": str(self.tmp / "no-served-record.json"),
        })
        command = ["bash", str(self.flow / "scripts/local_storage_build.sh"), "--build"]
        first = subprocess.run(command, cwd=self.flow, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = subprocess.run(command, cwd=self.flow, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("reusing verified isolated kernel", second.stdout)
        kernel_makefile = self.flow / ".work/local-userdata/kernel-state/linux-v7.0.0-sm8150/Makefile"
        kernel_makefile.write_text("tampered\n")
        third = subprocess.run(command, cwd=self.flow, env=env, capture_output=True, text=True, timeout=180)
        self.assertNotEqual(third.returncode, 0)
        self.assertIn("--refresh-kernel", third.stderr)
        fourth = subprocess.run(command + ["--refresh-kernel"], cwd=self.flow, env=env,
                                capture_output=True, text=True, timeout=180)
        self.assertEqual(fourth.returncode, 0, fourth.stdout + fourth.stderr)

    def test_entrypoint_missing_installed_hook_has_no_final_manifest(self):
        hook = (self.fixture.root / "build/plasma-mobile/installed-rootfs/"
                "usr/share/mkinitfs/hooks/netboot.sh")
        hook.unlink()
        env = {**os.environ, "PATH": f"{self.fixture.bin}:{os.environ['PATH']}", **self.fake_env}
        env.update({
            "BUILD_TEST_SUDO": str(self.fixture.sudo_calls),
            "BUILD_TEST_CALLS": str(self.fixture.calls),
            "BUILD_TEST_PYTHON": sys.executable,
            "NURA_SOURCE_ROOT": str(self.source_root),
            "NURA_SERVED_RECORD": str(self.tmp / "no-served-record.json"),
        })
        result = subprocess.run(
            ["bash", str(self.flow / "scripts/local_storage_build.sh"), "--build"],
            cwd=self.flow, env=env, capture_output=True, text=True, timeout=180,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.flow / "build/local-userdata/DELIVERY.local.json").exists())

    def test_entrypoint_uses_fixed_flow_subtree_and_no_path_overrides(self):
        text = (self.flow / "scripts/local_storage_build.sh").read_text()
        self.assertIn('LOCAL_STATE="$FLOW_DIR/.work/local-userdata"', text)
        self.assertIn('LOCAL_OUT="$FLOW_DIR/build/local-userdata"', text)
        self.assertNotIn("NURA_LOCAL_STATE", text)
        self.assertIn("overlap \"$real\" \"$protected\"", text)

    def test_default_netboot_check_only_still_uses_original_defaults(self):
        result = self.run_flow("--check-only", local=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("checks passed", result.stdout)
        self.assertFalse((self.flow / "build/local-userdata/ROOTFS-INTERMEDIATE.txt").exists())

    def test_origin_manifest_hashes_match_source_copies(self):
        manifest = json.loads((self.flow / "ORIGIN-SHA256.json").read_text())
        for original, entry in manifest.items():
            self.assertEqual(sha256(SOURCE / original), entry["sha256"],
                             f"original changed: {original}")
            self.assertTrue((self.flow / entry["copy"]).is_file())


if __name__ == "__main__":
    unittest.main()
