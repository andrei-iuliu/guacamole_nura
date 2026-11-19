"""Rootless tests for scripts/rc3_build.sh and the rc3 kernel identity guards.

Every fixture is a synthetic checkout with fake df/ss/findmnt/sudo tools.  No
kernel build, pmbootstrap, sudo, mount, server, phone command or real artifact
is touched.  The tests prove the preflight refusals (missing recipes, missing
archives, low disk, live port, active mounts) and that a stale kernel release
or module payload is rejected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "7.0.0-sm8150-rc3"
LEGACY_RELEASE = "7.0.0-sm8150-rear1"


def write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data)
    return path


class Rc3BuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "checkout"
        self.bin = self.root / "bin"
        self.make_checkout()

    def tearDown(self):
        self.directory.cleanup()

    # -- fixture -----------------------------------------------------------
    def make_checkout(self):
        root = self.root
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        for name in (
            "rc3_build.sh",
            "check_plasma_candidate.py",
            "check_rear_camera_package.py",
            "check_initramfs_release.py",
            "build_rear_camera_kernel.sh",
            "rootfs_prepare_plasma.sh",
        ):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)

        recipe = root / "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        write(recipe / "APKBUILD", f"""pkgname=linux-postmarketos-qcom-sm8150
pkgver=7.0.0
pkgrel=4
source="
\tlinux-v7.0.0-sm8150.tar.gz
\tconfig-postmarketos-qcom-sm8150.aarch64
\t0031-i2c-qcom-cci-initialize-all-master-completions.patch
\tsha512sums
"
sha512sums="
{0:0128d}  linux-v7.0.0-sm8150.tar.gz
{0:0128d}  config-postmarketos-qcom-sm8150.aarch64
{0:0128d}  0031-i2c-qcom-cci-initialize-all-master-completions.patch
"
""".replace("{0:0128d}", "0" * 128))
        write(recipe / "config-postmarketos-qcom-sm8150.aarch64",
              'CONFIG_LOCALVERSION="-sm8150-rc3"\n')
        write(recipe / "0031-i2c-qcom-cci-initialize-all-master-completions.patch",
              "init_completion(&cci->master[i].irq_complete);\n")

        write(root / "device/testing/rc3/modemmanager/APKBUILD",
              "pkgname=modemmanager\npkgver=1.25.95_git20261005\npkgrel=1\n")
        write(root / "device/testing/nura-clock/APKBUILD",
              "pkgname=nura-clock\npkgver=0.1.0\npkgrel=0\nsource=\"clock-epoch\"\n")
        write(root / "device/testing/nura-clock/clock-epoch", "1794268800\n")
        write(root / "device/testing/device-oneplus-guacamole/APKBUILD",
              "pkgname=device-oneplus-guacamole\npkgver=4\npkgrel=13\n")
        write(root / "device/testing/device-oneplus-guacamole/deviceinfo",
              'deviceinfo_codename="oneplus-guacamole"\n')
        write(root / "device/testing/firmware-oneplus-guacamole/"
              "firmware-oneplus-guacamole-24.0-20261011.tar.gz", b"firmware\n")
        write(root / "device/testing/device-oneplus-guacamole/"
              "guacamole-sensors-20261102.tar.gz", b"sensors\n")
        write(root / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot/APKBUILD",
              "pkgname=postmarketos-mkinitfs-hook-netboot\n")
        write(root / "device/testing/rear-camera/libcamera-rear/APKBUILD",
              "pkgname=libcamera-rear\n")

        baseline = root / ".work/rear-camera"
        write(baseline / "pmaports/pmaports.cfg", "channel=edge\nversion=7\n")
        write(baseline / "pmbootstrap/pmbootstrap.py", "")
        write(baseline / "pmbootstrap/pmb/config/__init__.py", "work_version = 8\n")
        write(baseline / "rootfs/pmbootstrap.cfg",
              "[pmbootstrap]\nwork = /work\naports = /aports\ndevice = oneplus-guacamole\n"
              "ui = phosh\n")
        write(root / "build/rear-camera/DELIVERY.json",
              json.dumps({"release": LEGACY_RELEASE,
                          "worktree": str(root)}) + "\n")
        write(root / "build/rear-camera/packaging-inputs.json",
              json.dumps({"release": LEGACY_RELEASE, "nbd_port": 10001}) + "\n")

        self.make_fake_tools()

    def make_fake_tools(self):
        self.bin.mkdir(parents=True, exist_ok=True)
        df = self.bin / "df"
        df.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ -n "${RC3_TEST_DF_KIB:-}" ]]; then\n'
            '  printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\n"\n'
            '  printf "fake 100000 0 %s 0%% /\\n" "$RC3_TEST_DF_KIB"\n'
            '  exit 0\n'
            'fi\n'
            f'exec {shutil.which("df")} "$@"\n')
        df.chmod(0o755)

        ss = self.bin / "ss"
        ss.write_text(
            '#!/usr/bin/env bash\n'
            '[[ -n "${RC3_TEST_LISTENER:-}" ]] && printf "%s\\n" "$RC3_TEST_LISTENER"\n'
            'exit 0\n')
        ss.chmod(0o755)

        findmnt = self.bin / "findmnt"
        findmnt.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ -n "${RC3_TEST_FINDMNT_FAIL:-}" ]]; then\n'
            '  echo "findmnt: simulated failure" >&2\n'
            '  exit 2\n'
            'fi\n'
            '[[ -n "${RC3_TEST_MOUNT:-}" ]] && printf "%s\\n" "$RC3_TEST_MOUNT"\n'
            'exit 0\n')
        findmnt.chmod(0o755)

        sudo = self.bin / "sudo"
        sudo.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$*" >> "${RC3_TEST_SUDO:-/dev/null}"\n'
            'exit 97\n')
        sudo.chmod(0o755)

    def run_rc3(self, *arguments, env=None):
        base = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
        }
        if env:
            base.update(env)
        return subprocess.run(
            ["bash", str(self.root / "scripts/rc3_build.sh"), *arguments],
            cwd=self.directory.name,
            env=base,
            capture_output=True,
            text=True,
            timeout=120,
        )

    # -- preflight refusals ------------------------------------------------
    def test_check_reports_the_rc3_identity(self):
        result = self.run_rc3("check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("rc3 kernel recipe", result.stdout)
        self.assertIn("rc3 modem recipe", result.stdout)
        self.assertIn("no rc3 kernel build yet", result.stdout)
        self.assertIn("read-only", result.stdout + result.stderr)
        self.assertFalse((self.root / "script").exists())

    def test_check_refuses_a_missing_rc3_modem_recipe(self):
        (self.root / "device/testing/rc3/modemmanager/APKBUILD").unlink()
        result = self.run_rc3("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("device/testing/rc3/modemmanager/APKBUILD", result.stdout + result.stderr)
        self.assertIn("modem worker", result.stdout + result.stderr)

    def test_check_refuses_a_missing_rc3_kernel_recipe(self):
        (self.root / "device/testing/rc3/linux-postmarketos-qcom-sm8150/APKBUILD").unlink()
        result = self.run_rc3("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("device/testing/rc3/linux-postmarketos-qcom-sm8150/APKBUILD",
                      result.stdout + result.stderr)
        self.assertIn("kernel worker", result.stdout + result.stderr)

    def test_check_refuses_a_missing_firmware_archive(self):
        (self.root / "device/testing/firmware-oneplus-guacamole/"
              "firmware-oneplus-guacamole-24.0-20261011.tar.gz").unlink()
        result = self.run_rc3("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("generate it from a LineageOS OTA", result.stdout + result.stderr)

    def test_check_refuses_low_disk_space(self):
        result = self.run_rc3("check", env={"RC3_TEST_DF_KIB": "1024"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not enough free space", result.stdout + result.stderr)
        self.assertIn("25165824 KiB required", result.stdout + result.stderr)

    def test_build_refuses_a_live_candidate_port(self):
        result = self.run_rc3("build", env={"RC3_TEST_LISTENER": "LISTEN 0 4096 *:10001"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TCP port 10001 is in use", result.stdout + result.stderr)
        self.assertIn("boot the phone out of Linux", result.stdout + result.stderr)

    def test_build_refuses_active_candidate_mounts(self):
        mount = str(self.root / ".work/plasma-mobile/chroot_native")
        result = self.run_rc3("build", env={"RC3_TEST_MOUNT": mount})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active mounts below .work/plasma-mobile", result.stdout + result.stderr)
        self.assertIn("stop pmbootstrap", result.stdout + result.stderr)

    def write_unreadable_candidate(self):
        # A manifest that cannot even be parsed.  If preflight touches it while
        # the candidate may be live, the run would fail with "unreadable".
        write(self.root / "build/plasma-mobile/DELIVERY.json",
              "{ this is not json\n")

    def test_check_skips_candidate_validation_while_the_port_is_live(self):
        self.write_unreadable_candidate()
        result = self.run_rc3("check", env={"RC3_TEST_LISTENER": "LISTEN 0 4096 *:10001"})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("candidate artifact validation is skipped", result.stdout + result.stderr)
        self.assertIn("skipped current-candidate artifact validation", result.stdout)
        self.assertNotIn("cannot read candidate manifest", result.stdout + result.stderr)

    def test_check_skips_candidate_validation_while_mounts_are_active(self):
        self.write_unreadable_candidate()
        mount = str(self.root / ".work/plasma-mobile/chroot_native")
        result = self.run_rc3("check", env={"RC3_TEST_MOUNT": mount})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("candidate artifact validation is skipped", result.stdout + result.stderr)
        self.assertIn("skipped current-candidate artifact validation", result.stdout)
        self.assertNotIn("cannot read candidate manifest", result.stdout + result.stderr)

    def test_build_refuses_the_live_port_before_candidate_validation(self):
        self.write_unreadable_candidate()
        result = self.run_rc3("build", env={"RC3_TEST_LISTENER": "LISTEN 0 4096 *:10001"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TCP port 10001 is in use", result.stdout + result.stderr)
        self.assertNotIn("cannot read candidate manifest", result.stdout + result.stderr)
        self.assertLess(
            (result.stdout + result.stderr).index("TCP port 10001 is in use"),
            (result.stdout + result.stderr).index("preflight: rc3 recipes") if "preflight: rc3 recipes" in result.stdout + result.stderr else len(result.stdout + result.stderr),
        )

    def test_build_refuses_active_mounts_before_candidate_validation(self):
        self.write_unreadable_candidate()
        mount = str(self.root / ".work/plasma-mobile/chroot_native")
        result = self.run_rc3("build", env={"RC3_TEST_MOUNT": mount})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("active mounts below .work/plasma-mobile", result.stdout + result.stderr)
        self.assertNotIn("cannot read candidate manifest", result.stdout + result.stderr)

    def test_live_guard_runs_before_recipe_existence_checks(self):
        (self.root / "device/testing/rc3/modemmanager/APKBUILD").unlink()
        result = self.run_rc3("build", env={"RC3_TEST_LISTENER": "LISTEN 0 4096 *:10001"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TCP port 10001 is in use", result.stdout + result.stderr)
        self.assertNotIn("modem worker", result.stdout + result.stderr)

    def test_failed_findmnt_is_not_treated_as_no_mounts(self):
        result = self.run_rc3("check", env={"RC3_TEST_FINDMNT_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("findmnt failed; cannot prove", result.stdout + result.stderr)
        self.assertNotIn("checks passed", result.stdout)

    def test_failed_findmnt_refuses_build_before_candidate_validation(self):
        self.write_unreadable_candidate()
        result = self.run_rc3("build", env={"RC3_TEST_FINDMNT_FAIL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("findmnt failed; cannot prove", result.stdout + result.stderr)
        self.assertNotIn("cannot read candidate manifest", result.stdout + result.stderr)

    def test_build_refuses_a_symlinked_kernel_state(self):
        target = self.root / "outside-state"
        target.mkdir()
        (self.root / ".work/rc3-kernel").symlink_to(target)
        result = self.run_rc3("build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinked state/output path", result.stdout + result.stderr)

    # -- stale kernel rejection -------------------------------------------
    def make_kernel_staging(self, release=RELEASE):
        kernel = self.root / "build/rc3-kernel"
        package = kernel / "kernel-package"
        write(package / "boot/vmlinuz", b"vmlinuz\n")
        write(package / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb", b"dtb\n")
        write(package / f"usr/lib/modules/{release}/kernel/drivers/i2c/busses/"
              "i2c-qcom-cci.ko.zst", b"cci\n")
        write(package / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release",
              release + "\n")
        write(kernel / "Image", b"image\n")
        write(self.root / ".work/rc3-kernel/linux-v7.0.0-sm8150/Makefile", "all:\n")
        write(self.root / ".work/rc3-kernel/output/include/config/kernel.release",
              release + "\n")
        return kernel

    def run_helper(self, *arguments):
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/check_plasma_candidate.py"),
             *map(str, arguments)],
            cwd=self.directory.name,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_kernel_record_rejects_the_old_release(self):
        kernel = self.make_kernel_staging(LEGACY_RELEASE)
        result = self.run_helper(
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel",
            "--recipe", "device/testing/rc3/linux-postmarketos-qcom-sm8150")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release is not 7.0.0-sm8150-rc3", result.stdout + result.stderr)
        self.assertFalse((kernel / "kernel-inputs.json").exists())

    def test_kernel_record_writes_only_computed_identities(self):
        kernel = self.make_kernel_staging()
        result = self.run_helper(
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel",
            "--recipe", "device/testing/rc3/linux-postmarketos-qcom-sm8150")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        recorded = json.loads((kernel / "kernel-inputs.json").read_text())
        import hashlib
        for key, relative in (
            ("image", "Image"),
            ("vmlinuz", "kernel-package/boot/vmlinuz"),
            ("dtb", "kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"),
        ):
            data = (kernel / relative).read_bytes()
            self.assertEqual(recorded[key]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(recorded[key]["size"], len(data))
        self.assertEqual(recorded["release"], RELEASE)
        self.assertNotIn("boot.img", recorded)

    def test_kernel_record_is_deterministic_for_identical_staging(self):
        kernel = self.make_kernel_staging()
        recipe = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        arguments = (
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel", "--recipe", recipe)
        first = self.run_helper(*arguments)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = (kernel / "kernel-inputs.json").read_bytes()
        second = self.run_helper(*arguments)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual((kernel / "kernel-inputs.json").read_bytes(), before,
                         "identical staging must not change the recorded identity")

    def test_kernel_record_moves_a_delivery_from_a_different_build_aside(self):
        kernel = self.make_kernel_staging()
        recipe = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        arguments = (
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel", "--recipe", recipe)
        first = self.run_helper(*arguments)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        write(kernel / "DELIVERY.json", json.dumps({
            "release": RELEASE, "kernel_inputs_sha256": "0" * 64}))
        write(kernel / "boot.img", b"stale boot\n")
        second = self.run_helper(*arguments)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        previous = sorted(kernel.glob("previous-*"))
        self.assertEqual(len(previous), 1, second.stdout + second.stderr)
        self.assertTrue((previous[0] / "boot.img").is_file())
        self.assertFalse((kernel / "boot.img").exists())
        self.assertTrue((previous[0] / "STALE.txt").is_file())

    def test_a_changed_module_payload_is_rejected(self):
        kernel = self.make_kernel_staging()
        recipe = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        record = self.run_helper(
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel", "--recipe", recipe)
        self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
        write(kernel / "kernel-package" / f"usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/"
              "i2c-qcom-cci.ko.zst", b"tampered module\n")
        result = self.run_helper(
            "source", "--source-build", kernel,
            "--source-state", self.root / ".work/rc3-kernel",
            "--baseline-build", self.root / "build/rear-camera",
            "--baseline-state", self.root / ".work/rear-camera")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kernel CCI module SHA256 mismatch", result.stdout + result.stderr)

    def test_an_added_module_changes_the_payload_identity(self):
        kernel = self.make_kernel_staging()
        recipe = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        record = self.run_helper(
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel", "--recipe", recipe)
        self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
        write(kernel / "kernel-package" / f"usr/lib/modules/{RELEASE}/kernel/drivers/extra/x.ko",
              b"unexpected module\n")
        result = self.run_helper(
            "source", "--source-build", kernel,
            "--source-state", self.root / ".work/rc3-kernel",
            "--baseline-build", self.root / "build/rear-camera",
            "--baseline-state", self.root / ".work/rear-camera")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("module payload differs", result.stdout + result.stderr)

    def test_source_rejects_a_recipe_changed_after_the_build(self):
        kernel = self.make_kernel_staging()
        recipe = "device/testing/rc3/linux-postmarketos-qcom-sm8150"
        record = self.run_helper(
            "kernel-record", "--build", kernel,
            "--state", self.root / ".work/rc3-kernel", "--recipe", recipe)
        self.assertEqual(record.returncode, 0, record.stdout + record.stderr)
        write(self.root / recipe / "config-postmarketos-qcom-sm8150.aarch64",
              'CONFIG_LOCALVERSION="-something-else"\n')
        result = self.run_helper(
            "source", "--source-build", kernel,
            "--source-state", self.root / ".work/rc3-kernel",
            "--baseline-build", self.root / "build/rear-camera",
            "--baseline-state", self.root / ".work/rear-camera")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("recipe config changed", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
