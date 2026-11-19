"""Rootless tests for the user-run Plasma Mobile rootfs/rootfs-preparation helper.

The tests build a tiny fake checkout with synthetic APKs, a real cpio initramfs,
fake ss/findmnt/sudo/python3 commands, and no pmbootstrap execution.  They assert
that the Plasma path is isolated from the Phosh state and stops at the first
mocked pmbootstrap signing setup without running privileged commands.
"""

from __future__ import annotations

import base64
import hashlib
import gzip
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import textwrap
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "7.0.0-sm8150-rc3"
LEGACY_RELEASE = "7.0.0-sm8150-rear1"
CANDIDATE = "nura-plasma-rc3"


def sha256(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sha512(path: Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha512").hexdigest()


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


def entry(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256(path), "size": path.stat().st_size}


def make_cpio(path: Path, extra: bool = False, release: str = RELEASE) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        write(root / "lib/modules" / release / "dummy.ko", b"")
        write(root / "usr/share/mkinitfs/hooks/netboot.sh", "NBD_PORT=10001\n")
        if extra:
            write(root / "usr/share/plasma-candidate-marker", "different but valid\n")
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        result = subprocess.run(
            ["cpio", "-o", "-H", "newc"],
            cwd=root,
            input=("\n".join(files) + "\n").encode(),
            capture_output=True,
            check=True,
        )
        path.write_bytes(result.stdout)


def make_public_key(fill: int = 0x11, comment: str = "fixture@test") -> str:
    key_type = b"ssh-ed25519"
    blob = struct.pack(">I", len(key_type)) + key_type + bytes([fill]) * 32
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} {comment}\n"


class PlasmaPreparationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "checkout with spaces"
        self.make_checkout()

    def tearDown(self):
        self.directory.cleanup()

    def make_checkout(self):
        root = self.root
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        for name in (
            "rootfs_prepare_plasma.sh",
            "plasma_shutdown.sh",
            "stop_plasma_server.py",
            "check_plasma_candidate.py",
            "check_rear_camera_package.py",
            "check_initramfs_release.py",
        ):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)

        # Local recipe targets.  The candidate symlinks must resolve here and
        # nowhere near the real Phosh state.
        targets = {
            "device/testing/device-oneplus-guacamole": "device APKBUILD\n",
            "device/testing/firmware-oneplus-guacamole": "firmware APKBUILD\n",
            "device/testing/rc3/linux-postmarketos-qcom-sm8150": "rc3 kernel APKBUILD\n",
            "device/testing/rc3/modemmanager": "rc3 modemmanager APKBUILD\n",
            "device/testing/hexagonrpcd": "hexagon\n",
            "device/testing/iio-sensor-proxy": "iio\n",
            "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot": "hook\n",
            "device/testing/rear-camera/libcamera-rear": "private libcamera\n",
        }
        for relative, contents in targets.items():
            write(root / relative / "APKBUILD", contents)
            write(root / relative / "marker", contents)
        write(root / "device/testing/rc3/linux-postmarketos-qcom-sm8150/"
              "config-postmarketos-qcom-sm8150.aarch64",
              'CONFIG_LOCALVERSION="-sm8150-rc3"\n')

        source_state = root / ".work/rear-camera"
        source_out = root / "build/rear-camera"
        pmaports = source_state / "pmaports"
        write(pmaports / "pmaports.cfg", "channel=edge\nversion=7\n")
        write(pmaports / "temp/libcamera/APKBUILD",
              "pkgname=libcamera\n_pkgver=0.8.0\npkgver=9999$_pkgver\n")
        write(pmaports / "temp/libcamera/0001-libcamera-software_isp-Allow-limiting-output-sizes.patch", "")
        write(pmaports / "temp/libcamera/0002-libcamera-Add-quad-Bayer-CFA-layout-support.patch", "")
        lock_sha = sha512(write(pmaports / "main/postmarketos-ui-plasma-mobile/kscreenlockerrc",
                                "[Daemon]\nLockOnStart=true\n"))
        write(pmaports / "main/postmarketos-ui-plasma-mobile/APKBUILD", f"""pkgname=postmarketos-ui-plasma-mobile
pkgver=6
pkgrel=10
source="
\tkscreenlockerrc
\t"
package() {{
    install -Dm644 \\
        "$srcdir"/kscreenlockerrc \\
        -t "$pkgdir"/etc/xdg
}}
sha512sums="
{lock_sha}  kscreenlockerrc
"
""")
        for relative, target in {
            "device/testing/device-oneplus-guacamole": "device/testing/device-oneplus-guacamole",
            "device/testing/firmware-oneplus-guacamole": "device/testing/firmware-oneplus-guacamole",
            "device/testing/linux-postmarketos-qcom-sm8150":
                "device/testing/rc3/linux-postmarketos-qcom-sm8150",
            "main/hexagonrpcd": "device/testing/hexagonrpcd",
            "main/iio-sensor-proxy": "device/testing/iio-sensor-proxy",
            "main/postmarketos-mkinitfs-hook-netboot":
                "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot",
            "temp/libcamera-rear": "device/testing/rear-camera/libcamera-rear",
            "main/modemmanager": "device/testing/rc3/modemmanager",
        }.items():
            link = pmaports / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(os.path.relpath(root / target, link.parent), target_is_directory=True)
        subprocess.run(["git", "init", "-q", str(pmaports)], check=True)
        subprocess.run(
            ["git", "-C", str(pmaports), "remote", "add", "origin",
             "https://gitlab.postmarketos.org/postMarketOS/pmaports.git"],
            check=True,
        )

        pmbootstrap = source_state / "pmbootstrap"
        write(pmbootstrap / "pmbootstrap.py", "")
        write(pmbootstrap / "pmb/config/__init__.py", "work_version = 8\n")
        write(pmbootstrap / "pmb/helpers/args.py",
              '    context.no_depends = getattr(args, "no_depends", False)\n')

        write(source_state / "rootfs/pmbootstrap.cfg", """[pmbootstrap]
aports = /home/someone/runtime/.work/rear-camera/pmaports
build_pkgs_on_install = false
device = oneplus-guacamole
service_manager = systemd
ui = phosh
work = /home/someone/runtime/.work/rear-camera/pmbootstrap-work

[providers]

[mirrors]
""")

        kernel = source_out / "kernel-package"
        write(kernel / "boot/vmlinuz", b"vmlinuz\n")
        write(kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb", b"dtb\n")
        write(kernel / f"usr/lib/modules/{LEGACY_RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst", b"cci\n")
        write(kernel / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release", LEGACY_RELEASE + "\n")
        write(source_out / "Image", b"Image\n")
        write(source_out / "boot.img", b"boot.img\n")
        write(source_out / "rootfs.img", b"rootfs\n")
        write(source_out / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb", b"wrapper\n")
        write(source_out / "temporary-dtbo/manifest.json", json.dumps({
            "inputs": {"base_dtb": {"sha256": sha256(kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb")}},
            "artifacts": {"wrapper": {"sha256": sha256(source_out / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb")}},
        }))
        make_cpio(source_out / "initramfs")

        known_good_rootfs = source_state / "pmbootstrap-work/chroot_rootfs_oneplus-guacamole"
        write(known_good_rootfs / "usr/lib/libcamera.so.0.8.0", b"known-good libcamera 0.8\n")
        write(known_good_rootfs / "usr/lib/libcamera-base.so.0.8.0", b"known-good libcamera base 0.8\n")
        cache = source_state / "pmbootstrap-work/cache_apk_aarch64"
        cache.mkdir(parents=True)
        write(source_state / "pmbootstrap-work/config_apk_keys/original.rsa.pub", "original public key\n")
        write(source_state / "pmbootstrap-work/config_apk_keys/ignored-not-public", "not copied\n")
        for filename in (
            "libcamera-99990.8.0-r1.01d92cee.apk",
            "libcamera-ipa-99990.8.0-r1.00365e1b.apk",
        ):
            shutil.copy2(ROOT / ".work/rear-camera/pmbootstrap-work/cache_apk_aarch64" / filename,
                         cache / filename)

        apk_dir = source_state / "pmbootstrap-work/packages/edge/aarch64"
        apks = {
            "linux-postmarketos-qcom-sm8150": "7.0.0_test-r0",
            "postmarketos-mkinitfs-hook-netboot": "4.2.1-r4",
            "libcamera-rear": "0.7.2-r0",
        }
        apk_entries = {}
        for name, version in apks.items():
            path = write(apk_dir / f"{name}-{version}.apk", (name + version).encode())
            apk_entries[name] = entry(path)

        sys.path.insert(0, str(scripts))
        from check_rear_camera_package import module_payload  # noqa: PLC0415
        payload = module_payload(kernel)
        inputs = {
            "release": LEGACY_RELEASE,
            "nbd_port": 10001,
            "initramfs_sha256": sha256(source_out / "initramfs"),
            "vmlinuz_sha256": sha256(kernel / "boot/vmlinuz"),
            "module_payload_sha256": payload["sha256"],
            "module_count": payload["count"],
            "rootfs": str(known_good_rootfs),
            "rootfs_image": str(source_out / "rootfs.img"),
            "rootfs_image_sha256": sha256(source_out / "rootfs.img"),
            "apks": apk_entries,
        }
        write(source_out / "packaging-inputs.json", json.dumps(inputs))
        write(source_out / "DELIVERY.json", json.dumps({
            "release": LEGACY_RELEASE,
            "worktree": str(root),
            "created_at": "2026-11-10T00:00:00Z",
            "git_head": "",
            "boot_img": entry(source_out / "boot.img"),
            "image": entry(source_out / "Image"),
            "kernel_package": entry(kernel / "boot/vmlinuz"),
            "dtb": entry(kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"),
            "initramfs": entry(source_out / "initramfs"),
            "rootfs_image": entry(source_out / "rootfs.img"),
            "i2c_qcom_cci": entry(kernel / f"usr/lib/modules/{LEGACY_RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"),
        }))

        # Isolated rc3 kernel state/output.  The recorded kernel-inputs.json is
        # written by the real checker so every hash matches the fixture bytes.
        kernel_state = root / ".work/rc3-kernel"
        kernel_build = root / "build/rc3-kernel"
        write(kernel_state / "linux-v7.0.0-sm8150/Makefile", "all:\n")
        write(kernel_state / "output/include/config/kernel.release", RELEASE + "\n")
        write(kernel_state / "output/.config", "CONFIG_LOCALVERSION=\"-sm8150-rc3\"\n")
        rc3_kernel = kernel_build / "kernel-package"
        write(rc3_kernel / "boot/vmlinuz", b"rc3-vmlinuz\n")
        write(rc3_kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb", b"rc3-dtb\n")
        write(rc3_kernel / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst",
              b"rc3-cci\n")
        write(rc3_kernel / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release", RELEASE + "\n")
        write(kernel_build / "Image", b"rc3-image\n")
        record = subprocess.run(
            [sys.executable, str(scripts / "check_plasma_candidate.py"), "kernel-record",
             "--build", str(kernel_build), "--state", str(kernel_state),
             "--recipe", "device/testing/rc3/linux-postmarketos-qcom-sm8150"],
            capture_output=True, text=True)
        if record.returncode:
            raise SystemExit(record.stdout + record.stderr)

        self.bin = root / "bin"
        self.bin.mkdir()
        self.calls = root / "calls.txt"
        self.sudo_calls = root / "sudo.txt"
        self.make_fake_tools()
        self.root_before = {
            path: sha256(path)
            for path in (
                pmaports / "main/postmarketos-ui-plasma-mobile/kscreenlockerrc",
                pmaports / "main/postmarketos-ui-plasma-mobile/APKBUILD",
                source_state / "rootfs/pmbootstrap.cfg",
                source_out / "boot.img",
            )
        }

    def make_fake_tools(self):
        python = self.bin / "python3"
        python.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "${1:-}" == *"/pmbootstrap/pmbootstrap.py" ]]; then\n'
            '  printf "cwd=%s\\n" "$PWD" >> "$BUILD_TEST_CALLS"\n'
            '  printf "%s\\n" "$@" >> "$BUILD_TEST_CALLS"\n'
            '  exit 42\n'
            'fi\n'
            'exec "$BUILD_TEST_PYTHON" "$@"\n'
        )
        python.chmod(0o755)

        sudo = self.bin / "sudo"
        sudo.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$*" >> "$BUILD_TEST_SUDO"\n'
            'command=${1:-}; shift || true\n'
            'protected=${BUILD_TEST_PRIVILEGED_PARENT:-}\n'
            'destination=${@: -1}\n'
            'restore_mode=\n'
            'if [[ -n "$protected" && "$destination" == "$protected"/* ]]; then\n'
            '  restore_mode=$(stat -c %a "$protected") || exit 96\n'
            '  chmod u+w "$protected" || exit 96\n'
            'fi\n'
            'args=()\n'
            'while (($#)); do\n'
            '  case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac\n'
            'done\n'
            'case "$command" in\n'
            '  cp) cp "${args[@]}" ;;\n'
            '  mv) mv "${args[@]}" ;;\n'
            '  rm) rm "${args[@]}" ;;\n'
            '  install) install "${args[@]}" ;;\n'
            '  *) echo "unexpected sudo: $command ${args[*]}" >&2; exit 97 ;;\n'
            'esac\n'
            'status=$?\n'
            'if [[ -n "$restore_mode" ]]; then chmod "$restore_mode" "$protected" || exit 96; fi\n'
            'exit "$status"\n'
        )
        sudo.chmod(0o755)

        ss = self.bin / "ss"
        ss.write_text(
            '#!/usr/bin/env bash\n'
            '[[ -n "${BUILD_TEST_LISTENER:-}" ]] && printf "%s\\n" "$BUILD_TEST_LISTENER"\n'
            'exit 0\n'
        )
        ss.chmod(0o755)

        findmnt = self.bin / "findmnt"
        findmnt.write_text(
            '#!/usr/bin/env bash\n'
            '[[ -n "${BUILD_TEST_MOUNT_TARGET:-}" ]] && printf "%s\\n" "$BUILD_TEST_MOUNT_TARGET"\n'
            'exit 0\n'
        )
        findmnt.chmod(0o755)

        for name, body in (
            ("modinfo", '#!/usr/bin/env bash\necho "vermagic: 7.0.0-sm8150-rear1"\n'),
            ("zstd", '#!/usr/bin/env bash\nexit 1\n'),
        ):
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o755)

    def run_builder(self, *arguments, env=None):
        base = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "BUILD_TEST_CALLS": str(self.calls),
            "BUILD_TEST_SUDO": str(self.sudo_calls),
            "BUILD_TEST_PYTHON": sys.executable,
        }
        if env:
            base.update(env)
        return subprocess.run(
            ["bash", str(self.root / "scripts/rootfs_prepare_plasma.sh"), *arguments],
            cwd=self.directory.name,
            env=base,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def state(self) -> Path:
        return self.root / ".work/plasma-mobile"

    def output(self) -> Path:
        return self.root / "build/plasma-mobile"

    def run_helper(self, *arguments):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/check_plasma_candidate.py"),
             *map(str, arguments)],
            cwd=self.directory.name,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def make_candidate_fixture(self):
        kernel_build = self.root / "build/rc3-kernel"
        kernel = kernel_build / "kernel-package"
        out = self.output()
        out.mkdir(parents=True, exist_ok=True)

        # One exported rc3 rootfs initramfs is both the rootfs archive and the
        # boot image ramdisk for this release.
        rootfs_initramfs = out / "rootfs-initramfs"
        make_cpio(rootfs_initramfs, extra=True)
        rootfs_image = write(out / "rootfs.img", b"candidate-rootfs\n")

        wrapper_dir = kernel_build / "temporary-dtbo"
        wrapper = write(wrapper_dir / "custom_guacamole-abl-quarantine.dtb", b"rc3-wrapper\n")
        dtb = kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
        write(wrapper_dir / "manifest.json", json.dumps({
            "inputs": {"base_dtb": {"sha256": sha256(dtb)}},
            "artifacts": {"wrapper": {"sha256": sha256(wrapper)}},
        }))
        image = kernel_build / "Image"
        boot_img = write(kernel_build / "boot.img",
                         b"BOOTFIX" + image.read_bytes() + rootfs_initramfs.read_bytes()
                         + wrapper.read_bytes())

        sys.path.insert(0, str(self.root / "scripts"))
        from check_rear_camera_package import module_payload  # noqa: PLC0415
        payload = module_payload(kernel, RELEASE)
        write(kernel_build / "DELIVERY.json", json.dumps({
            "release": RELEASE,
            "worktree": str(self.root),
            "created_at": "2026-11-12T00:00:00Z",
            "git_head": "",
            "kernel_recipe": "device/testing/rc3/linux-postmarketos-qcom-sm8150",
            "kernel_inputs_sha256": sha256(kernel_build / "kernel-inputs.json"),
            "boot_img": entry(boot_img),
            "image": entry(image),
            "kernel_package": entry(kernel / "boot/vmlinuz"),
            "dtb": entry(dtb),
            "initramfs": entry(rootfs_initramfs),
            "rootfs_image": entry(rootfs_image),
            "i2c_qcom_cci": entry(kernel / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"),
            "module_payload_sha256": payload["sha256"],
            "module_count": payload["count"],
        }) + "\n")
        write(kernel_build / "packaging-inputs.json", json.dumps({
            "release": RELEASE,
            "kernel_release": RELEASE,
            "nbd_port": 10001,
            "initramfs_sha256": sha256(rootfs_initramfs),
            "vmlinuz_sha256": sha256(kernel / "boot/vmlinuz"),
            "module_payload_sha256": payload["sha256"],
            "module_count": payload["count"],
            "rootfs": str((kernel_build / "installed-rootfs").resolve()),
            "rootfs_image": str(rootfs_image.resolve()),
            "rootfs_image_sha256": sha256(rootfs_image),
            "apks": {},
        }) + "\n")

        # Candidate copies: the rebuilt rc3 kernel package, Image and wrapper.
        if (out / "kernel-package").exists():
            shutil.rmtree(out / "kernel-package")
        shutil.copytree(kernel, out / "kernel-package")
        shutil.copy2(image, out / "Image")
        if (out / "temporary-dtbo").exists():
            shutil.rmtree(out / "temporary-dtbo")
        shutil.copytree(wrapper_dir, out / "temporary-dtbo")
        shutil.copy2(boot_img, out / "boot.img")
        shutil.copy2(rootfs_initramfs, out / "boot-initramfs")
        make_cpio(out / "altered-boot-initramfs", extra=True)
        with (out / "altered-boot-initramfs").open("ab") as altered:
            altered.write(b"different-but-valid")

        apk_dir = out / "apks"
        if apk_dir.exists():
            shutil.rmtree(apk_dir)
        apk_dir.mkdir()
        source_apks = self.root / ".work/rear-camera/pmbootstrap-work/packages/edge/aarch64"
        for name, version in (
            ("postmarketos-mkinitfs-hook-netboot", "4.2.1-r4"),
            ("libcamera-rear", "0.7.2-r0"),
        ):
            shutil.copy2(source_apks / f"{name}-{version}.apk", apk_dir / f"{name}-{version}.apk")
        write(apk_dir / "linux-postmarketos-qcom-sm8150-7.0.0_test-r4.apk", b"rc3 kernel apk\n")
        write(apk_dir / "postmarketos-ui-plasma-mobile-6-r10.apk", b"ui\n")
        write(apk_dir / f"modemmanager-{self.modem_version()}.apk", b"modemmanager\n")
        write(apk_dir / f"libmm-glib-{self.modem_version()}.apk", b"libmm-glib\n")
        cache = self.root / ".work/rear-camera/pmbootstrap-work/cache_apk_aarch64"
        shutil.copy2(cache / "libcamera-99990.8.0-r1.01d92cee.apk",
                     apk_dir / "libcamera-99990.8.0-r1.apk")
        shutil.copy2(cache / "libcamera-ipa-99990.8.0-r1.00365e1b.apk",
                     apk_dir / "libcamera-ipa-99990.8.0-r1.apk")

        rootfs = out / "installed-rootfs"
        if rootfs.exists():
            shutil.rmtree(rootfs)
        shutil.copytree(kernel / "boot", rootfs / "boot")
        shutil.copytree(kernel / f"usr/lib/modules/{RELEASE}", rootfs / f"usr/lib/modules/{RELEASE}")
        (rootfs / "usr/share/kernel/postmarketos-qcom-sm8150").mkdir(parents=True, exist_ok=True)
        shutil.copy2(kernel / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release",
                     rootfs / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release")
        shutil.copy2(rootfs_initramfs, rootfs / "boot/initramfs")
        write(rootfs / "usr/share/mkinitfs/hooks/netboot.sh", "NBD_PORT=10001\n")
        write(rootfs / "etc/xdg/kscreenlockerrc", "[Daemon]\nLockOnStart=false\n")
        write(rootfs / "etc/systemd/logind.conf.d/50-nura-plasma-nbd.conf", "[Login]\nIdleAction=ignore\n")
        write(rootfs / "etc/xdg/powerdevilrc",
              "[AC][SuspendAndShutdown]\nAutoSuspendAction=0\n"
              "[Battery][SuspendAndShutdown]\nAutoSuspendAction=0\n"
              "[LowBattery][SuspendAndShutdown]\nAutoSuspendAction=0\n")
        write(rootfs / "usr/lib/clock-epoch", "1762905600\n")
        (rootfs / "etc/systemd/system").mkdir(parents=True, exist_ok=True)
        (rootfs / "etc/systemd/system/display-manager.service").symlink_to(
            "/usr/lib/systemd/system/plasma-mobile.service")
        known_good = self.root / ".work/rear-camera/pmbootstrap-work/chroot_rootfs_oneplus-guacamole"
        for relative in ("usr/lib/libcamera.so.0.8.0", "usr/lib/libcamera-base.so.0.8.0"):
            shutil.copy2(known_good / relative, rootfs / relative)
        database = [
            ("postmarketos-ui-plasma-mobile", "6-r10"),
            ("plasma-mobile", "6.8.2-r0"),
            ("plasma-camera", "26.08.2-r1"),
            ("libcamera", "99990.8.0-r1"),
            ("libcamera-ipa", "99990.8.0-r1"),
            ("libcamera-rear", "0.7.2-r0"),
            ("pipewire-spa-libcamera", "1.6.11-r0"),
            ("pulseaudio", "17.0-r9"),
            ("xwayland", "24.1.15-r0"),
            ("postmarketos-base-ui-audio-backend-pulseaudio", "65-r0"),
            ("systemd", "262.2-r0"),
            ("powerdevil", "6.8.2-r0"),
            ("modemmanager", self.modem_version()),
            ("libmm-glib", self.modem_version()),
            ("modemmanager-systemd", self.modem_version()),
            ("modemmanager-udev", self.modem_version()),
            ("modemmanager-lang", self.modem_version()),
        ]
        extra = {
            "plasma-camera": "D:libcamera qt6-qtmultimedia so:libcamera.so.0.8\n",
            "modemmanager": "D:libmm-glib polkit\n",
        }
        write(rootfs / "lib/apk/db/installed",
              "\n".join(f"P:{name}\nV:{version}\n{extra.get(name, '')}"
                        for name, version in database))
        write(rootfs / "usr/bin/plasma-camera", "#!/bin/sh\nexit 0\n")
        write(rootfs / "usr/bin/Xwayland", "#!/bin/sh\nexit 0\n").chmod(0o755)
        write(rootfs / "usr/share/applications/org.kde.plasma.camera.desktop",
              "[Desktop Entry]\nExec=plasma-camera\n")
        write(rootfs / "usr/lib/qt6/plugins/multimedia/libffmpegmediaplugin.so", b"")
        return out, rootfs

    def test_build_refuses_symlinked_state_or_output_before_writing(self):
        for kind in ("state", "output"):
            with self.subTest(kind=kind):
                if kind == "state":
                    link = self.state()
                    target = self.root / "victim-state"
                    link.parent.mkdir(parents=True, exist_ok=True)
                else:
                    link = self.output()
                    target = self.root / "victim-output"
                    link.parent.mkdir(parents=True, exist_ok=True)
                target.mkdir()
                link.symlink_to(target)
                result = self.run_builder("--build")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("symlinked Plasma path", result.stdout + result.stderr)
                self.assertEqual(list(target.iterdir()), [])
                self.assertFalse(self.calls.exists())
                self.assertFalse(self.sudo_calls.exists())
                link.unlink()

    def make_verify_args(self, out, rootfs):
        sys.path.insert(0, str(self.root / "scripts"))
        from check_rear_camera_package import module_payload  # noqa: PLC0415
        kernel = out / "kernel-package"
        payload = module_payload(kernel, RELEASE)
        return [
            "--release", RELEASE,
            "--kernel-dir", str(kernel),
            "--rootfs", str(rootfs),
            "--initramfs", str(out / "rootfs-initramfs"),
            "--known-good-rootfs", str(self.root / ".work/rear-camera/pmbootstrap-work/chroot_rootfs_oneplus-guacamole"),
            "--expected-vmlinuz-sha", sha256(kernel / "boot/vmlinuz"),
            "--expected-dtb-sha", sha256(kernel / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"),
            "--expected-module-payload-sha", payload["sha256"],
            "--expected-module-count", str(payload["count"]),
            "--ui-version", "6-r10",
        ]

    def modem_version(self):
        return "1.25.95_git20261005-r1"

    def run_manifest(self, out, rootfs, boot_initramfs=None):
        return self.run_helper(
            "manifest",
            "--source-build", str(self.root / "build/rc3-kernel"),
            "--source-state", str(self.root / ".work/rc3-kernel"),
            "--baseline-build", str(self.root / "build/rear-camera"),
            "--baseline-state", str(self.root / ".work/rear-camera"),
            "--kernel-dir", str(out / "kernel-package"),
            "--image", str(out / "Image"),
            "--boot-img", str(out / "boot.img"),
            "--boot-initramfs", str(boot_initramfs or (out / "boot-initramfs")),
            "--rootfs-initramfs", str(out / "rootfs-initramfs"),
            "--rootfs-image", str(out / "rootfs.img"),
            "--rootfs-tree", str(rootfs),
            "--temporary-dtbo", str(out / "temporary-dtbo"),
            "--apk-dir", str(out / "apks"),
            "--modemmanager-version", self.modem_version(),
            "--libmm-glib-version", self.modem_version(),
            "--out", str(out),
        )

    def make_previous_candidate(self):
        """Create a servable previous Plasma candidate for preservation tests."""
        out = self.output()
        out.mkdir(parents=True, exist_ok=True)
        source_out = self.root / "build/rear-camera"
        shutil.copytree(source_out / "kernel-package", out / "kernel-package")
        shutil.copy2(source_out / "Image", out / "Image")
        shutil.copytree(source_out / "temporary-dtbo", out / "temporary-dtbo")
        write(out / "boot.img", b"old boot image\n")
        write(out / "boot-initramfs", b"old boot initramfs\n")
        write(out / "DELIVERY.json", json.dumps({
            "release": LEGACY_RELEASE,
            "release_candidate": "nura-plasma-rc1",
            "worktree": str(self.root),
        }) + "\n")
        write(out / "packaging-inputs.json", '{"old": "inputs"}\n')
        write(out / "APK-SHA256SUMS", "old sums\n")
        work = self.state() / "pmbootstrap-work"
        write(work / "version", "8\n")
        rootfs = work / "chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
        write(rootfs, b"old rootfs image\n")
        export = self.state() / "rootfs/export"
        write(export / "initramfs", b"old rootfs initramfs\n")
        (export / "boot.img").symlink_to(
            "../../pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img")
        write(self.state() / "rootfs/candidate-state.json", json.dumps({
            "release_candidate": "nura-plasma-rc1",
            "source_pmaports": str((self.root / ".work/rear-camera/pmaports").resolve()),
            "source_commit": "",
            "source_pmbootstrap": str((self.root / ".work/rear-camera/pmbootstrap").resolve()),
            "source_pmbootstrap_commit": "",
        }) + "\n")
        return out, rootfs, export

    def rollback_dirs(self):
        return sorted(self.output().glob("rollback-*"))

    def test_the_new_rc3_initramfs_is_required_for_boot_and_rootfs(self):
        out, rootfs = self.make_candidate_fixture()
        source_out = self.root / "build/rear-camera"
        candidate_initramfs = out / "rootfs-initramfs"
        # The rc3 boot ramdisk must be the exported rc3 rootfs archive, never
        # the pinned rear1 boot initramfs.
        self.assertNotEqual(sha256(candidate_initramfs), sha256(source_out / "initramfs"))

        verify = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)

        manifest = self.run_manifest(out, rootfs)
        self.assertEqual(manifest.returncode, 0, manifest.stdout + manifest.stderr)
        delivery = json.loads((out / "DELIVERY.json").read_text())
        inputs = json.loads((out / "packaging-inputs.json").read_text())
        self.assertEqual(delivery["initramfs"]["sha256"], sha256(candidate_initramfs))
        self.assertEqual(delivery["rootfs_initramfs"]["sha256"], sha256(candidate_initramfs))
        self.assertEqual(inputs["initramfs_sha256"], sha256(candidate_initramfs))
        self.assertEqual(inputs["rootfs_initramfs_sha256"], sha256(candidate_initramfs))
        self.assertEqual(delivery["kernel_release"], RELEASE)
        self.assertEqual(inputs["camera_app"], "plasma-camera")
        self.assertEqual(inputs["camera_app_version"], "26.08.2-r1")
        self.assertNotIn("snapshot_version", inputs)
        self.assertEqual(delivery["release"], RELEASE)
        self.assertEqual(delivery["release_candidate"], CANDIDATE)
        self.assertEqual(delivery["release_series"], "nura-plasma")
        self.assertEqual(delivery["artifact_name"], f"{CANDIDATE}-oneplus-guacamole")
        self.assertEqual(inputs["release"], RELEASE)
        self.assertEqual(inputs["release_candidate"], CANDIDATE)
        self.assertEqual(inputs["artifact_name"], f"{CANDIDATE}-oneplus-guacamole")
        self.assertEqual(self.run_helper("release-candidate").stdout.strip(), CANDIDATE)

        altered = self.run_manifest(out, rootfs, boot_initramfs=out / "altered-boot-initramfs")
        self.assertNotEqual(altered.returncode, 0, altered.stdout + altered.stderr)
        self.assertIn("boot initramfs", altered.stdout + altered.stderr)

    def test_wrong_powerdevil_policy_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        write(rootfs / "etc/xdg/powerdevilrc",
              "[AC][SuspendAndShutdown]\nAutoSuspendAction=1\n"
              "[Battery][SuspendAndShutdown]\nAutoSuspendAction=0\n"
              "[LowBattery][SuspendAndShutdown]\nAutoSuspendAction=0\n")
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PowerDevil", result.stdout + result.stderr)

    def rewrite_database(self, rootfs, mutate):
        database = rootfs / "lib/apk/db/installed"
        blocks = [block for block in database.read_text().split("\n\n") if block.strip()]
        database.write_text("\n\n".join(mutate(blocks)) + "\n")

    def test_plasma_camera_is_the_recorded_camera_app(self):
        out, rootfs = self.make_candidate_fixture()
        verify = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
        installed = json.loads(verify.stdout)
        self.assertEqual(installed["camera_app"], "plasma-camera")
        self.assertEqual(installed["camera_app_version"], "26.08.2-r1")
        self.assertEqual(installed["release"], RELEASE)
        self.assertEqual(installed["release_candidate"], CANDIDATE)
        self.assertNotIn("snapshot_version", installed)

    def test_snapshot_is_rejected_as_the_camera_app(self):
        out, rootfs = self.make_candidate_fixture()
        self.rewrite_database(rootfs, lambda blocks: blocks + ["P:snapshot\nV:51.0-r0"])
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unexpectedly contains snapshot", result.stdout + result.stderr)

    def test_kamoso_is_rejected_as_the_camera_app(self):
        out, rootfs = self.make_candidate_fixture()
        self.rewrite_database(rootfs, lambda blocks: blocks + ["P:kamoso\nV:1-r0"])
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unexpectedly contains kamoso", result.stdout + result.stderr)

    def test_missing_plasma_camera_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        self.rewrite_database(
            rootfs,
            lambda blocks: [b for b in blocks if not b.startswith("P:plasma-camera\n")],
        )
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("missing plasma-camera", result.stdout + result.stderr)

    def test_plasma_camera_requires_the_pinned_libcamera_abi(self):
        out, rootfs = self.make_candidate_fixture()
        self.rewrite_database(
            rootfs,
            lambda blocks: [
                b.replace("so:libcamera.so.0.8", "so:libcamera.so.0.9")
                if b.startswith("P:plasma-camera\n") else b
                for b in blocks
            ],
        )
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pinned system libcamera ABI", result.stdout + result.stderr)

    def test_install_list_selects_plasma_camera_and_not_snapshot(self):
        script = (ROOT / "scripts/rootfs_prepare_plasma.sh").read_text()
        match = re.search(r"^install_add_string\(\) \{.*?^\}", script, re.M | re.S)
        self.assertIsNotNone(match, "rootfs_prepare_plasma.sh has no install_add_string")
        body = match.group(0)
        self.assertIn('"plasma-camera"', body)
        self.assertIn('"xwayland"', body)
        self.assertNotIn('"snapshot"', body)
        # Reused pmbootstrap rootfs chroots only gain packages, so the removed
        # app must be deleted once before the new install list is applied.
        self.assertIn("apk del snapshot", script)
        self.assertIn("chroot --rootfs", script)
        self.assertLess(script.index("apk del snapshot"), script.index('--add "$add"'))
        sys.path.insert(0, str(ROOT / "scripts"))
        from check_plasma_candidate import CAMERA_APP  # noqa: PLC0415
        self.assertEqual(CAMERA_APP, "plasma-camera")
        self.assertIn(CAMERA_APP, body)

    def test_missing_xwayland_package_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        db = rootfs / "lib/apk/db/installed"
        db.write_text(db.read_text().replace("P:xwayland\nV:24.1.15-r0\n", ""))
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing xwayland", result.stdout + result.stderr)

    def test_missing_or_nonexecutable_xwayland_binary_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        binary = rootfs / "usr/bin/Xwayland"
        for missing in (False, True):
            with self.subTest(missing=missing):
                binary.unlink() if missing else binary.chmod(0o644)
                result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no executable /usr/bin/Xwayland", result.stdout + result.stderr)

    def test_missing_qt_multimedia_backend_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        (rootfs / "usr/lib/qt6/plugins/multimedia/libffmpegmediaplugin.so").unlink()
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Qt Multimedia backend plugin", result.stdout + result.stderr)

    def test_tampered_system_libcamera_fails_verify(self):
        out, rootfs = self.make_candidate_fixture()
        write(rootfs / "usr/lib/libcamera.so.0.8.0", b"tampered\n")
        result = self.run_helper("verify", *self.make_verify_args(out, rootfs))
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("system libcamera", result.stdout + result.stderr)

    def test_check_only_is_read_only(self):
        result = self.run_builder("--check-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("checks passed", result.stdout)
        self.assertIn(f"release candidate: {CANDIDATE}", result.stdout)
        self.assertFalse(self.state().exists(), "check mode must not create candidate state")
        self.assertFalse(self.output().exists(), "check mode must not create candidate output")
        self.assertFalse(self.calls.exists(), "check mode must not invoke pmbootstrap")
        self.assertFalse(self.sudo_calls.exists(), "check mode must not invoke sudo")

    def test_build_prepares_isolated_state_and_stops_before_install(self):
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn("Plasma packaging failed", result.stdout + result.stderr)

        config = (self.state() / "rootfs/pmbootstrap.cfg").read_text()
        self.assertIn("ui = plasma-mobile", config)
        self.assertIn("build_pkgs_on_install = false", config)
        self.assertIn(f"aports = {self.state()}/pmaports", config)
        self.assertIn(f"work = {self.state()}/pmbootstrap-work", config)
        self.assertNotIn("/home/someone/runtime", config)
        repaired_args = (self.state() / "pmbootstrap/pmb/helpers/args.py").read_text()
        from types import SimpleNamespace
        context = SimpleNamespace()
        exec(textwrap.dedent(repaired_args), {"context": context,
             "args": SimpleNamespace(no_depends=True, ignore_depends=True)})
        self.assertTrue(context.ignore_depends)

        ui = self.state() / "pmaports/main/postmarketos-ui-plasma-mobile"
        lock = (ui / "kscreenlockerrc").read_text()
        self.assertIn("LockOnStart=false", lock)
        self.assertIn(f"{sha512(ui / 'kscreenlockerrc')}  kscreenlockerrc", (ui / "APKBUILD").read_text())
        logind = (ui / "50-nura-plasma-nbd.conf").read_text()
        self.assertIn("IdleAction=ignore", logind)
        powerdevil = (ui / "powerdevilrc").read_text()
        self.assertIn("[AC][SuspendAndShutdown]\nAutoSuspendAction=0", powerdevil)
        self.assertIn("[Battery][SuspendAndShutdown]\nAutoSuspendAction=0", powerdevil)
        self.assertIn("[LowBattery][SuspendAndShutdown]\nAutoSuspendAction=0", powerdevil)
        apkbuild = (ui / "APKBUILD").read_text()
        for filename in ("50-nura-plasma-nbd.conf", "powerdevilrc"):
            self.assertIn(filename, apkbuild)
            self.assertIn(f"{sha512(ui / filename)}  {filename}", apkbuild)
        self.assertIn('etc/systemd/logind.conf.d', apkbuild)
        receipt = json.loads((self.state() / "rootfs/candidate-state.json").read_text())
        self.assertFalse(receipt["lock_on_start"])
        self.assertEqual(receipt["release"], RELEASE)
        self.assertEqual(receipt["release_candidate"], CANDIDATE)
        self.assertEqual(receipt["artifact_name"], f"{CANDIDATE}-oneplus-guacamole")
        self.assertEqual(receipt["logind_conf_sha512"], sha512(ui / "50-nura-plasma-nbd.conf"))
        self.assertEqual(receipt["powerdevil_conf_sha512"], sha512(ui / "powerdevilrc"))

        for relative, target in {
            "device/testing/device-oneplus-guacamole": "device/testing/device-oneplus-guacamole",
            "main/hexagonrpcd": "device/testing/hexagonrpcd",
            "temp/libcamera-rear": "device/testing/rear-camera/libcamera-rear",
        }.items():
            link = self.state() / "pmaports" / relative
            self.assertTrue(link.is_symlink(), link)
            self.assertFalse(os.path.isabs(os.readlink(link)), "candidate package links must stay relative")
            self.assertEqual(link.resolve(), (self.root / target).resolve())

        # The rc3 kernel package/Image/wrapper are copied only after the
        # successful installed-rootfs verification and boot pack, so a build
        # stopped at the first pmbootstrap call leaves the output unchanged.
        self.assertFalse((self.output() / "kernel-package").exists())
        self.assertFalse((self.output() / "boot.img").exists())
        self.assertFalse((self.output() / "DELIVERY.json").exists())

        calls = self.calls.read_text().splitlines()
        # The real sequence is seed -> system-libcamera signing -> rc3 kernel
        # APK -> other local packages -> install.  The fake stops at the first
        # pmbootstrap invocation (signing), so assert the ordering at source
        # level as well as the observed refusal.
        script = (self.root / "scripts/rootfs_prepare_plasma.sh").read_text()
        self.assertLess(script.index("build_kernel_apk"),
                        script.index("run_pmbootstrap_builds"))
        self.assertIn("--envkernel", script)
        self.assertIn("clear_stale_kernel_apks", script)
        self.assertNotIn("install", calls, "the fake must fail before install")
        self.assertTrue(calls[0].startswith("cwd="))
        sudo_calls = self.sudo_calls.read_text().splitlines()
        self.assertTrue(sudo_calls)
        self.assertTrue(all(line.startswith("install ") for line in sudo_calls), sudo_calls)
        pkgdir = self.state() / "pmbootstrap-work/packages/edge/aarch64"
        keydir = self.state() / "pmbootstrap-work/config_apk_keys"
        self.assertEqual((keydir / "original.rsa.pub").read_text(), "original public key\n")
        self.assertFalse((keydir / "ignored-not-public").exists())
        for filename in (
            "libcamera-99990.8.0-r1.apk",
            "libcamera-ipa-99990.8.0-r1.apk",
        ):
            path = pkgdir / filename
            self.assertTrue(path.is_file(), path)
            self.assertEqual(sha256(path), sha256(self.root / ".work/rear-camera/pmbootstrap-work/cache_apk_aarch64" /
                                                  ("libcamera-99990.8.0-r1.01d92cee.apk" if "ipa" not in filename
                                                   else "libcamera-ipa-99990.8.0-r1.00365e1b.apk")))

        for path, expected in self.root_before.items():
            self.assertEqual(sha256(path), expected, f"Phosh source changed: {path}")
        self.assertIn("LockOnStart=true", (self.root / ".work/rear-camera/pmaports/main/postmarketos-ui-plasma-mobile/kscreenlockerrc").read_text())

    def test_build_preserves_previous_rootfs_and_servable_export(self):
        out, rootfs, export = self.make_previous_candidate()
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn("Previous nura-plasma-rc1 candidate preserved", result.stdout)
        rollbacks = self.rollback_dirs()
        self.assertEqual(len(rollbacks), 1, result.stdout + result.stderr)
        rollback = rollbacks[0]
        # The rollback belongs to the displaced rc1, not to this rc2 build.
        self.assertTrue(rollback.name.startswith("rollback-nura-plasma-rc1-"), rollback.name)
        self.assertNotIn("rc2", rollback.name)
        self.assertEqual((rollback / "boot.img").read_bytes(), b"old boot image\n")
        self.assertEqual((rollback / "boot-initramfs").read_bytes(), b"old boot initramfs\n")
        self.assertEqual((rollback / "rootfs.img").read_bytes(), b"old rootfs image\n")
        self.assertEqual((rollback / "rootfs-initramfs").read_bytes(), b"old rootfs initramfs\n")
        self.assertEqual((rollback / "rootfs-export/initramfs").read_bytes(),
                         b"old rootfs initramfs\n")
        self.assertEqual((rollback / "kernel-package/boot/vmlinuz").read_bytes(), b"vmlinuz\n")
        self.assertEqual((rollback / "Image").read_bytes(), b"Image\n")
        self.assertTrue((rollback / "temporary-dtbo/manifest.json").is_file())
        self.assertEqual(json.loads((rollback / "candidate-state.json").read_text())["release_candidate"],
                         "nura-plasma-rc1")
        metadata = json.loads((rollback / "ROLLBACK.json").read_text())
        self.assertEqual(metadata["release"], LEGACY_RELEASE)
        self.assertEqual(metadata["release_candidate"], "nura-plasma-rc1")
        self.assertEqual(metadata["artifact_name"], "nura-plasma-rc1-oneplus-guacamole")
        self.assertEqual(metadata["replaced_by_release_candidate"], CANDIDATE)
        self.assertIn("Previous nura-plasma-rc1 candidate", (rollback / "RESTORE.txt").read_text())
        self.assertIn("--restore", (rollback / "RESTORE.txt").read_text())
        checked = subprocess.run(
            [shutil.which("sha256sum"), "-c", "ROLLBACK-SHA256SUMS"],
            cwd=rollback, capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        # The live rootfs was only read/copied, never hashed in place, and the
        # failed build did not remove the old artifact set.
        self.assertEqual(rootfs.read_bytes(), b"old rootfs image\n")
        self.assertEqual((out / "boot.img").read_bytes(), b"old boot image\n")

    def test_the_rc2_rollback_keeps_its_own_rear1_module_path(self):
        out, rootfs, export = self.make_previous_candidate()
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        rollback = self.rollback_dirs()[0]
        # The rc2 rollback was built for 7.0.0-sm8150-rear1.  Naming its module
        # path from the new rc3 RELEASE would drop the CCI module and make the
        # rollback unrestorable.
        self.assertTrue((rollback / f"kernel-package/usr/lib/modules/{LEGACY_RELEASE}/"
                         "kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst").is_file())
        self.assertFalse((rollback / f"kernel-package/usr/lib/modules/{RELEASE}").exists())
        self.assertEqual(json.loads((rollback / "ROLLBACK.json").read_text())["kernel_release"],
                         LEGACY_RELEASE)

    def test_rc2_rollback_restore_is_accepted_only_as_the_explicit_restore_path(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollback = self.rollback_dirs()[0]
        # A normal --check-only always validates the rc3 kernel source; the old
        # rc2 manifest is not treated as the current candidate.
        check = self.run_builder("--check-only")
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
        self.assertIn(RELEASE, check.stdout)
        self.assertNotIn("release candidate: nura-plasma-rc1", check.stdout)
        restore = self.run_builder("--restore", str(rollback))
        self.assertEqual(restore.returncode, 0, restore.stdout + restore.stderr)
        self.assertIn("Restored nura-plasma-rc1", restore.stdout)
        self.assertEqual(json.loads((out / "DELIVERY.json").read_text())["release"],
                         LEGACY_RELEASE)

    def test_a_reused_rc2_initramfs_is_rejected_as_the_rc3_boot_ramdisk(self):
        out, rootfs = self.make_candidate_fixture()
        rear1 = out / "rear1-initramfs"
        make_cpio(rear1, extra=True, release=LEGACY_RELEASE)
        kernel_build = self.root / "build/rc3-kernel"
        kernel = kernel_build / "kernel-package"
        delivery_path = kernel_build / "DELIVERY.json"
        delivery = json.loads(delivery_path.read_text())
        delivery["initramfs"] = entry(rear1)
        delivery_path.write_text(json.dumps(delivery) + "\n")
        wrapper = kernel_build / "temporary-dtbo/custom_guacamole-abl-quarantine.dtb"
        for path in (out / "rootfs-initramfs", out / "boot-initramfs", rootfs / "boot/initramfs"):
            path.write_bytes(rear1.read_bytes())
        rebuilt = (b"BOOTFIX" + (kernel_build / "Image").read_bytes()
                   + rear1.read_bytes() + wrapper.read_bytes())
        (kernel_build / "boot.img").write_bytes(rebuilt)
        (out / "boot.img").write_bytes(rebuilt)
        delivery["boot_img"] = entry(kernel_build / "boot.img")
        delivery_path.write_text(json.dumps(delivery) + "\n")
        result = self.run_helper(
            "source", "--source-build", kernel_build,
            "--source-state", self.root / ".work/rc3-kernel",
            "--baseline-build", self.root / "build/rear-camera",
            "--baseline-state", self.root / ".work/rear-camera",
            "--require-boot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("do not match exactly", result.stdout + result.stderr)
        self.assertIn(LEGACY_RELEASE, result.stdout + result.stderr)
        self.assertIn(RELEASE, result.stdout + result.stderr)

    def test_check_validates_a_built_rc3_candidate(self):
        out, rootfs = self.make_candidate_fixture()
        manifest = self.run_manifest(out, rootfs)
        self.assertEqual(manifest.returncode, 0, manifest.stdout + manifest.stderr)
        result = self.run_helper(
            "check", "--build", out, "--state", self.state(),
            "--kernel-state", self.root / ".work/rc3-kernel")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["release"], RELEASE)
        self.assertEqual(report["release_candidate"], CANDIDATE)
        self.assertTrue(report["installed_rootfs_checked"])

    def test_missing_rc3_kernel_inputs_prints_the_build_command(self):
        (self.root / "build/rc3-kernel/kernel-inputs.json").unlink()
        result = self.run_builder("--check-only")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kernel-inputs.json", result.stdout + result.stderr)
        self.assertIn("bash scripts/rc3_build.sh kernel", result.stdout + result.stderr)

    def test_preservation_does_not_hash_the_live_writable_rootfs(self):
        out, rootfs, export = self.make_previous_candidate()
        shacalls = self.root / "sha256-calls.txt"
        real = shutil.which("sha256sum")
        wrapper = self.bin / "sha256sum"
        wrapper.write_text(
            '#!/usr/bin/env bash\n'
            f'printf "%s\\n" "$*" >> {shlex.quote(str(shacalls))}\n'
            f'exec {shlex.quote(real)} "$@"\n')
        wrapper.chmod(0o755)
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertTrue(shacalls.is_file())
        self.assertNotIn(str(rootfs), shacalls.read_text())

    def test_build_refuses_to_overwrite_a_manifest_without_its_rootfs(self):
        out, rootfs, export = self.make_previous_candidate()
        rootfs.unlink()
        result = self.run_builder("--build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rootfs image is missing", result.stdout + result.stderr)
        self.assertEqual(self.rollback_dirs(), [])
        self.assertEqual((out / "boot.img").read_bytes(), b"old boot image\n")
        self.assertFalse(self.calls.exists())

    def test_retry_reuses_verified_rollback_after_export_is_removed(self):
        out, rootfs, export = self.make_previous_candidate()
        first = self.run_builder("--build")
        self.assertEqual(first.returncode, 42, first.stdout + first.stderr)
        rollback = self.rollback_dirs()[0]
        rootfs.unlink()
        retry = self.run_builder("--build")
        self.assertEqual(retry.returncode, 42, retry.stdout + retry.stderr)
        self.assertIn("Reusing verified previous", retry.stdout)
        self.assertEqual(self.rollback_dirs(), [rollback])

    def test_retry_rejects_corrupt_or_mismatched_saved_rollback(self):
        for mismatch in (False, True):
            with self.subTest(mismatch=mismatch):
                if mismatch:
                    self.tearDown()
                    self.setUp()
                out, rootfs, export = self.make_previous_candidate()
                first = self.run_builder("--build")
                self.assertEqual(first.returncode, 42, first.stdout + first.stderr)
                rollback = self.rollback_dirs()[0]
                if mismatch:
                    (out / "boot.img").write_bytes(b"different published boot")
                else:
                    (rollback / "rootfs.img").write_bytes(b"corrupt backup")
                rootfs.unlink()
                retry = self.run_builder("--build")
                self.assertNotEqual(retry.returncode, 42, retry.stdout + retry.stderr)
                self.assertIn("rootfs image is missing", retry.stdout + retry.stderr)

    def test_build_refuses_a_symlinked_previous_rootfs(self):
        out, rootfs, export = self.make_previous_candidate()
        victim = write(self.root / "outside-rootfs.img", b"outside\n")
        rootfs.unlink()
        rootfs.symlink_to(victim)
        result = self.run_builder("--build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be a symlink", result.stdout + result.stderr)
        self.assertEqual(victim.read_bytes(), b"outside\n")
        self.assertEqual(self.rollback_dirs(), [])
        self.assertFalse(self.calls.exists())

    def test_build_refuses_when_the_rollback_copy_does_not_fit(self):
        out, rootfs, export = self.make_previous_candidate()
        df = self.bin / "df"
        df.write_text(
            '#!/usr/bin/env bash\n'
            "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n"
            "fake 100000 99999 1 100%% /\\n'\n")
        df.chmod(0o755)
        result = self.run_builder("--build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not enough free space", result.stdout + result.stderr)
        self.assertEqual(self.rollback_dirs(), [])
        self.assertEqual((out / "boot.img").read_bytes(), b"old boot image\n")
        self.assertFalse(self.calls.exists())

    def test_partial_rollback_copy_failure_keeps_the_old_candidate(self):
        out, rootfs, export = self.make_previous_candidate()
        cp = self.bin / "cp"
        real = shutil.which("cp")
        cp.write_text(
            '#!/usr/bin/env bash\n'
            'case "$*" in *rootfs.img*) echo "simulated copy failure" >&2; exit 1;; esac\n'
            f'exec {shlex.quote(real)} "$@"\n')
        cp.chmod(0o755)
        result = self.run_builder("--build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not copy the previous candidate", result.stdout + result.stderr)
        self.assertEqual(self.rollback_dirs(), [])
        self.assertEqual(list(out.glob(".rollback-*.partial")), [])
        self.assertEqual((out / "boot.img").read_bytes(), b"old boot image\n")
        self.assertEqual(rootfs.read_bytes(), b"old rootfs image\n")
        self.assertFalse(self.calls.exists())

    def test_restore_reinstalls_the_verified_rootfs_and_export(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollbacks = self.rollback_dirs()
        self.assertEqual(len(rollbacks), 1)
        rollback = rollbacks[0]
        write(out / "boot.img", b"new boot image\n")
        write(out / "DELIVERY.json", '{"new": true}\n')
        write(rootfs, b"new rootfs image\n")
        export.mkdir(parents=True, exist_ok=True)
        write(export / "initramfs", b"new rootfs initramfs\n")
        write(self.state() / "rootfs/candidate-state.json", '{"new": true}\n')

        result = self.run_builder("--restore", str(rollback))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Restored nura-plasma-rc1", result.stdout)
        self.assertEqual((out / "boot.img").read_bytes(), b"old boot image\n")
        self.assertEqual((out / "DELIVERY.json").read_text(),
                         json.dumps({"release": LEGACY_RELEASE,
                                     "release_candidate": "nura-plasma-rc1",
                                     "worktree": str(self.root)}) + "\n")
        self.assertEqual(rootfs.read_bytes(), b"old rootfs image\n")
        self.assertEqual((export / "initramfs").read_bytes(), b"old rootfs initramfs\n")
        self.assertEqual(json.loads((self.state() / "rootfs/candidate-state.json").read_text())["release_candidate"],
                         "nura-plasma-rc1")
        asides = sorted((self.state() / "rootfs").glob("restore-before-*"))
        self.assertEqual(len(asides), 1)
        self.assertEqual((asides[0] / "out/boot.img").read_bytes(), b"new boot image\n")
        self.assertEqual((asides[0] / "oneplus-guacamole.img").read_bytes(), b"new rootfs image\n")
        self.assertEqual((asides[0] / "export/initramfs").read_bytes(), b"new rootfs initramfs\n")
        self.assertTrue(rollback.is_dir(), "restore must not delete the rollback")

    def test_restore_privilege_is_scoped_and_used_after_validation(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollback = self.rollback_dirs()[0]
        self.sudo_calls.unlink(missing_ok=True)
        parent = rootfs.parent
        parent.chmod(0o555)
        try:
            result = self.run_builder("--restore", str(rollback), env={
                "BUILD_TEST_PRIVILEGED_PARENT": str(parent),
            })
        finally:
            parent.chmod(0o755)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.sudo_calls.read_text().splitlines()
        self.assertEqual(len(calls), 3, calls)
        self.assertTrue(calls[0].startswith(f"cp -a -- {rootfs} "), calls)
        self.assertTrue(calls[1].startswith("install -m "), calls)
        self.assertIn(str(parent / ".oneplus-guacamole.img.restore-"), calls[1])
        self.assertTrue(calls[2].startswith("mv -f -- "), calls)
        self.assertTrue(calls[2].endswith(str(rootfs)), calls)
        self.assertEqual(rootfs.read_bytes(), b"old rootfs image\n")

    def test_tampered_rollback_is_rejected_before_any_sudo_call(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollback = self.rollback_dirs()[0]
        self.sudo_calls.unlink(missing_ok=True)
        write(rollback / "rootfs.img", b"tampered\n")
        result = self.run_builder("--restore", str(rollback))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sudo_calls.exists())

    def test_restore_rejects_a_tampered_rollback_before_writing(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollback = self.rollback_dirs()[0]
        write(rollback / "rootfs.img", b"tampered rootfs\n")
        write(rootfs, b"current rootfs\n")
        result = self.run_builder("--restore", str(rollback))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum verification failed", result.stdout + result.stderr)
        self.assertEqual(rootfs.read_bytes(), b"current rootfs\n")
        self.assertEqual(list((self.state() / "rootfs").glob("restore-before-*")), [])

    def test_restore_rejects_changed_export_symlink_before_writing(self):
        out, rootfs, export = self.make_previous_candidate()
        self.run_builder("--build")
        rollback = self.rollback_dirs()[0]
        link = rollback / "rootfs-export/boot.img"
        link.unlink()
        link.symlink_to("/tmp/unverified-boot.img")
        result = self.run_builder("--restore", str(rollback))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rootfs export differs from its inventory", result.stdout + result.stderr)
        self.assertEqual(rootfs.read_bytes(), b"old rootfs image\n")
        self.assertFalse((export / "boot.img").is_symlink())
        self.assertEqual(list((self.state() / "rootfs").glob("restore-before-*")), [])

    def test_unlabeled_previous_output_is_preserved_honestly(self):
        out, rootfs, export = self.make_previous_candidate()
        (out / "DELIVERY.json").unlink()
        (out / "packaging-inputs.json").unlink()
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        rollbacks = self.rollback_dirs()
        self.assertEqual(len(rollbacks), 1, result.stdout + result.stderr)
        self.assertTrue(rollbacks[0].name.startswith("rollback-unlabeled-"), rollbacks[0].name)
        metadata = json.loads((rollbacks[0] / "ROLLBACK.json").read_text())
        self.assertIsNone(metadata["release_candidate"])
        self.assertIsNone(metadata["artifact_name"])
        self.assertEqual(metadata["replaced_by_release_candidate"], CANDIDATE)

    def test_build_refuses_conflicting_public_key_before_pmbootstrap(self):
        key = write(self.state() / "pmbootstrap-work/config_apk_keys/original.rsa.pub", "different key\n")
        write(self.state() / "pmbootstrap-work/version", "8\n")
        result = self.run_builder("--build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public key conflicts", result.stdout + result.stderr)
        self.assertEqual(key.read_text(), "different key\n")
        self.assertFalse(self.calls.exists())

    def test_a_stale_kernel_apk_is_never_seeded_as_the_rc3_package(self):
        # A real build moves any existing kernel APK aside in
        # clear_stale_kernel_apks() before pmbootstrap --envkernel.  The fake
        # stops at system-libcamera signing, so this test asserts the source
        # order and that seeding no longer installs a kernel APK at all.
        script = (ROOT / "scripts/rootfs_prepare_plasma.sh").read_text()
        self.assertIn("clear_stale_kernel_apks()", script)
        self.assertLess(script.index("clear_stale_kernel_apks\n"),
                        script.index("build --envkernel"))
        self.assertNotIn("'linux-postmarketos-qcom-sm8150', 'postmarketos-mkinitfs-hook-netboot'",
                         script)

    def test_retry_reuses_proven_camera_and_hook_apks_preserving_rebuilds(self):
        work = self.state() / "pmbootstrap-work"
        write(work / "version", "8\n")
        for name, version in (("libcamera-rear", "0.7.2-r0"),
                              ("postmarketos-mkinitfs-hook-netboot", "4.2.1-r4")):
            filename = f"{name}-{version}.apk"
            write(work / "packages/edge/aarch64" / filename, b"previous rebuild")
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        for name, version in (("libcamera-rear", "0.7.2-r0"),
                              ("postmarketos-mkinitfs-hook-netboot", "4.2.1-r4")):
            filename = f"{name}-{version}.apk"
            source = self.root / ".work/rear-camera/pmbootstrap-work/packages/edge/aarch64" / filename
            self.assertEqual((work / "packages/edge/aarch64" / filename).read_bytes(), source.read_bytes())
            backups = list((work / "previous-apks").glob(f"*/{filename}"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"previous rebuild")
        calls = self.calls.read_text().splitlines()
        self.assertNotIn("libcamera-rear", calls)
        self.assertNotIn("postmarketos-mkinitfs-hook-netboot", calls)

    def test_resigning_preserves_exact_control_and_data_and_rejects_tampering(self):
        source = self.root / ".work/rear-camera/pmbootstrap-work/cache_apk_aarch64/libcamera-99990.8.0-r1.01d92cee.apk"
        control = self.root / "control.tar.gz"
        arguments = ("apk-resign", "--source", str(source), "--sha256", sha256(source))
        result = self.run_helper(*arguments, "--control-output", str(control))
        self.assertEqual(result.returncode, 0, result.stderr)
        signature_tar = io.BytesIO()
        with tarfile.open(fileobj=signature_tar, mode="w") as archive:
            member = tarfile.TarInfo(".SIGN.RSA.fixture.rsa.pub")
            member.size = 7
            archive.addfile(member, io.BytesIO(b"fixture"))
        signature = gzip.compress(signature_tar.getvalue())
        signed_control = write(self.root / "signed-control.tar.gz", signature + control.read_bytes())
        output = self.root / "resigned.apk"
        result = self.run_helper(*arguments, "--signed-control", str(signed_control), "--output", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(sha256(output), sha256(source))
        result = self.run_helper(*arguments, "--candidate", str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        before = output.read_bytes()
        signed_control.write_bytes(signature + gzip.compress(b"altered control"))
        result = self.run_helper(*arguments, "--signed-control", str(signed_control), "--output", str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed the APK control", result.stderr)
        self.assertEqual(output.read_bytes(), before)
        output.write_bytes(before[:-8])
        result = self.run_helper(*arguments, "--candidate", str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("truncated", result.stderr)
        result = self.run_helper("apk-resign", "--source", str(source), "--sha256", "0" * 64,
                                 "--control-output", str(control))
        self.assertNotEqual(result.returncode, 0)

    def test_check_only_rejects_authorized_key_option(self):
        key = write(self.root / "debug.pub", make_public_key())
        result = self.run_builder("--check-only", "--authorized-key", str(key))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("only valid with --build", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.sudo_calls.exists())

    def test_build_rejects_a_private_authorized_key_before_pmbootstrap(self):
        private = write(self.root / "id_ed25519",
                        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n"
                        "-----END OPENSSH PRIVATE KEY-----\n")
        result = self.run_builder("--build", "--authorized-key", str(private))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing a private key file", result.stderr)
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.sudo_calls.exists())

    def test_build_with_public_authorized_key_reaches_pmbootstrap(self):
        key = write(self.root / "debug.pub", make_public_key())
        result = self.run_builder("--build", "--authorized-key", str(key))
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn("authorized public key(s): 1 validated", result.stdout)
        # pmbootstrap's own install copies the public key; the candidate
        # config used by every non-install step stays at ssh_keys=false.
        script = (ROOT / "scripts/rootfs_prepare_plasma.sh").read_text()
        self.assertIn('"${INSTALL_CONFIG:-$STATE/rootfs/pmbootstrap.cfg}"', script)
        self.assertIn("authorized-key-config", script)
        self.assertIn("authorized_keys", script)

    def test_default_build_never_enables_ssh_keys(self):
        script = (ROOT / "scripts/rootfs_prepare_plasma.sh").read_text()
        self.assertIn('INSTALL_CONFIG=""', script)
        self.assertIn('[[ -n "$AUTHORIZED_KEY" ]] || return 0', script)
        result = self.run_builder("--build")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertNotIn("authorized public key", result.stdout)
        config = (self.state() / "rootfs/pmbootstrap.cfg").read_text()
        self.assertNotIn("ssh_keys = true", config)

    def test_build_refuses_a_live_candidate_port_before_writing(self):
        result = self.run_builder("--build", env={"BUILD_TEST_LISTENER": "LISTEN 0 1 0.0.0.0:10001"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("already listening", result.stderr)
        self.assertFalse(self.state().exists())
        self.assertFalse(self.output().exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.sudo_calls.exists())

    def test_build_refuses_mount_cleanup_without_candidate_pmbootstrap(self):
        result = self.run_builder(
            "--build",
            env={"BUILD_TEST_MOUNT_TARGET": str(self.state() / "pmaports/loop")},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("pmbootstrap installation is incomplete", result.stderr)
        self.assertFalse(self.state().exists())
        self.assertFalse(self.output().exists())
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.sudo_calls.exists())


class AuthorizedKeyHelperTests(unittest.TestCase):
    """Explicit public-key validation and build-only config, no sudo/pmbootstrap."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.script = ROOT / "scripts/check_plasma_candidate.py"

    def run_helper(self, *arguments):
        return subprocess.run(
            [sys.executable, str(self.script), *map(str, arguments)],
            capture_output=True, text=True, timeout=30)

    def test_valid_public_key_is_written_private_mode_without_printing_it(self):
        key = write(self.root / "debug.pub", make_public_key())
        output = self.root / "authorized_keys"
        result = self.run_helper("authorized-key", "--public-key", key, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), key.read_text())
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertNotIn(key.read_text().split()[1], result.stdout)

    def test_existing_authorized_keys_keeps_every_public_key(self):
        first = make_public_key(0x11, "first@fixture")
        second = make_public_key(0x22, "second@fixture")
        source = write(self.root / "existing_authorized_keys",
                       "# existing debugging keys\n\n" + first + second)
        output = self.root / "authorized_keys"
        result = self.run_helper("authorized-key", "--public-key", source, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(), "# existing debugging keys\n" + first + second)

    def test_private_key_material_is_rejected_before_any_output(self):
        for name, body in (
            ("id_ed25519", "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"),
            ("id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----\n"),
        ):
            with self.subTest(name=name):
                key = write(self.root / name, body)
                output = self.root / "authorized_keys"
                output.unlink(missing_ok=True)
                result = self.run_helper("authorized-key", "--public-key", key, "--output", output)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refusing a private key", result.stderr)
                self.assertFalse(output.exists())

    def test_invalid_public_key_lines_are_rejected(self):
        truncated = struct.pack(">I", len(b"ssh-ed25519")) + b"ssh-ed25519"
        cases = {
            "bad-base64": "ssh-ed25519 !!!not-base64!!! x\n",
            "truncated": "ssh-ed25519 " + base64.b64encode(truncated).decode() + "\n",
            "options": 'from="127.0.0.1" ' + make_public_key().strip() + "\n",
            "no-keys": "# only comments\n\n",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                key = write(self.root / f"{name}.pub", body)
                output = self.root / "authorized_keys"
                output.unlink(missing_ok=True)
                result = self.run_helper("authorized-key", "--public-key", key, "--output", output)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(output.exists())

    def test_symlinked_public_key_file_is_refused(self):
        key = write(self.root / "debug.pub", make_public_key())
        link = self.root / "debug-link.pub"
        link.symlink_to(key)
        output = self.root / "authorized_keys"
        result = self.run_helper("authorized-key", "--public-key", link, "--output", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not be a symlink", result.stderr)
        self.assertFalse(output.exists())

    def test_key_config_enables_install_without_changing_the_candidate_config(self):
        source = write(self.root / "pmbootstrap.cfg", """[pmbootstrap]
aports = /x/pmaports
build_pkgs_on_install = false
device = oneplus-guacamole
service_manager = systemd
ssh_keys = false
ui = plasma-mobile
user = user
work = /x/work

[providers]

[mirrors]
""")
        keys = write(self.root / "authorized_keys", make_public_key())
        output = self.root / "pmbootstrap-authorized-key.cfg"
        result = self.run_helper(
            "authorized-key-config",
            "--source-config", source, "--authorized-keys", keys, "--output-config", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["ssh_keys"])
        self.assertEqual(data["user"], "user")
        self.assertEqual(data["ssh_key_glob"], str(keys))
        self.assertIn("ssh_keys = false", source.read_text())
        generated = output.read_text()
        self.assertIn("ssh_keys = true", generated)
        self.assertIn(f"ssh_key_glob = {keys}", generated)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
