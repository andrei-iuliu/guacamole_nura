#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Host-side tests for scripts/camera_verify.sh; no phone, SSH, sudo or hardware."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/camera_verify.sh"

RELEASE = "7.0.0-sm8150-rear1"
FIXED_SHA = "4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4"
STALE_SHA = "6f61b6083c8bade306c508fd907f5c667b8c8cd0a3628cc569afb6a0f1ee0378"
MODULE_PATH = f"/usr/lib/modules/{RELEASE}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
CMDLINE = "BOOT_IMAGE=/boot/vmlinuz pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1 console=tty0"
LSMOD = "\n".join(
    (
        "i2c_qcom_cci           20480  0",
        "qcom_camss            430080  0",
        "imx586                 36864  0",
        "imx481                 32768  0",
        "s5k3m5                 32768  0",
        "ak7375                  8192  0",
    )
)
DMESG = "i2c-qcom-cci 0c1c000.i2c: CCI HW version = 0x00030000"
MEDIA = "\n".join(
    (
        "== /dev/media0",
        "- entity 1: imx586 0-001a (1 pad, 1 link)",
        "- entity 2: imx481 4-001a (1 pad, 1 link)",
        "- entity 3: s5k3m5 2-0010 (1 pad, 1 link)",
        "- entity 4: ak7375 4-000c (0 pad, 0 link)",
        "- entity 5: qcom-csiphy0 (1 pad, 1 link)",
        "  pad0: Sink",
        '    <- "s5k3m5 2-0010":0 [ENABLED]',
        "- entity 6: qcom-csiphy1 (1 pad, 1 link)",
        "  pad0: Sink",
        '    <- "imx586 0-001a":0 [ENABLED]',
        "- entity 7: qcom-csiphy3 (1 pad, 1 link)",
        "  pad0: Sink",
        '    <- "imx481 4-001a":0 [ENABLED]',
    )
)
V4L2 = "Qualcomm Camera Subsystem (platform:qcom-camss):\n\t/dev/video0\n\t/dev/video1"
CAMREAR = "Available cameras:\n1: rear main\n2: rear wide\n3: rear tele"
POWER = "SKIPPED (rerun with --sudo for the read-only pm_genpd summary)"


def remote_output(
    *,
    release: str = RELEASE,
    module_sha: str = FIXED_SHA,
    module_size: int = 8950,
    module_state: str = "present",
    cmdline: str = CMDLINE,
    lsmod: str = LSMOD,
    sysfs: str = "loaded",
    dmesg: str = DMESG,
    media: str = MEDIA,
    v4l2: str = V4L2,
    camrear: str = CAMREAR,
    power: str = POWER,
) -> str:
    module_path = f"/usr/lib/modules/{release}/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
    if module_state == "missing":
        module_section = f"MISSING {module_path}"
    elif module_state == "no-sha256sum":
        module_section = f"NO_SHA256SUM {module_path}"
    elif module_state == "empty":
        module_section = ""
    else:
        module_section = f"{module_sha}  {module_path}\n{module_size}"
    sections = (
        ("UNAME", f"Linux (none) {release} #1 SMP PREEMPT"),
        ("RELEASE", release),
        ("CMDLINE", cmdline),
        ("MODULE", module_section),
        ("LSMOD", lsmod),
        ("MODULE_SYSFS", sysfs),
        ("DMESG", dmesg),
        ("MEDIA", media),
        ("V4L2", v4l2),
        ("CAMREAR", camrear),
        ("POWER", power),
        ("END", ""),
    )
    return "".join(f"@@{name}@@\n{text}\n" for name, text in sections)


class CameraVerifyTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.checkout = Path(self.temporary.name) / "checkout with spaces"
        (self.checkout / "scripts").mkdir(parents=True)
        shutil.copy2(SCRIPT, self.checkout / "scripts/camera_verify.sh")
        (self.checkout / "build/rear-camera").mkdir(parents=True)
        self.manifest = self.checkout / "build/rear-camera/DELIVERY.json"
        self.fake_bin = self.checkout / "fake-bin"
        self.fake_bin.mkdir()
        self.log = self.checkout / "tool-calls.log"
        self.remote_file = self.checkout / "remote-output.txt"
        self.out = self.checkout / "out"
        self.write_manifest()
        self.remote_file.write_text(remote_output())
        self.install_fake_ssh()
        self.install_fake_host_tools()

    # ------------------------------------------------------------ fixtures

    def write_manifest(self, **overrides) -> None:
        data = {
            "release": RELEASE,
            "worktree": str(self.checkout),
            "created_at": "2026-11-10T12:27:00Z",
            "boot_img": {"path": "build/rear-camera/boot.img", "sha256": "a" * 64, "size": 1},
            "image": {"path": "build/rear-camera/Image", "sha256": "b" * 64, "size": 1},
            "kernel_package": {"path": "vmlinuz", "sha256": "c" * 64, "size": 1},
            "dtb": {"path": "sm8150-oneplus-guacamole.dtb", "sha256": "d" * 64, "size": 1},
            "initramfs": {"path": "initramfs", "sha256": "e" * 64, "size": 1},
            "rootfs_image": {"path": "oneplus-guacamole.img", "sha256": "f" * 64, "size": 1},
            "i2c_qcom_cci": {
                "path": f"build/rear-camera/kernel-package{MODULE_PATH}",
                "sha256": FIXED_SHA,
                "size": 8950,
            },
        }
        data.update(overrides)
        self.manifest.write_text(json.dumps(data, indent=2) + "\n")

    def install_fake_ssh(self) -> None:
        ssh = self.fake_bin / "ssh"
        ssh.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'ssh %s\\n\' "$*" >> "$CAMERA_VERIFY_TEST_LOG"\n'
            'if [ "${CAMERA_VERIFY_TEST_SSH_MODE:-ok}" = fail ]; then exit 255; fi\n'
            'if [ "${CAMERA_VERIFY_TEST_SSH_MODE:-ok}" = hostkey ]; then\n'
            '  echo "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!" >&2\n'
            '  exit 255\n'
            'fi\n'
            'for arg in "$@"; do [ "$arg" = true ] && exit 0; done\n'
            'cat "$CAMERA_VERIFY_TEST_REMOTE_OUTPUT"\n'
            "exit 0\n"
        )
        ssh.chmod(0o755)

    def install_fake_host_tools(self) -> None:
        ip = self.fake_bin / "ip"
        ip.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'ip %s\\n\' "$*" >> "$CAMERA_VERIFY_TEST_LOG"\n'
            'case "$*" in\n'
            '  "-o -4 addr show") printf \'3: usb0    inet 172.16.42.2/24 brd 172.16.42.255 scope global usb0\\n\' ;;\n'
            '  "neigh show 172.16.42.1") printf \'172.16.42.1 dev usb0 lladdr 02:00:00:00:00:01 REACHABLE\\n\' ;;\n'
            "esac\n"
        )
        ping = self.fake_bin / "ping"
        ping.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'ping %s\\n\' "$*" >> "$CAMERA_VERIFY_TEST_LOG"\n'
            "exit 1\n"
        )
        nc = self.fake_bin / "nc"
        nc.write_text(
            "#!/usr/bin/env bash\n"
            'printf \'nc %s\\n\' "$*" >> "$CAMERA_VERIFY_TEST_LOG"\n'
            "exit 1\n"
        )
        for tool in (ip, ping, nc):
            tool.chmod(0o755)

    def run_script(
        self,
        *args: str,
        remote: str | None = None,
        ssh_mode: str = "ok",
        timeout: int = 60,
    ) -> subprocess.CompletedProcess[str]:
        if remote is not None:
            self.remote_file.write_text(remote)
        environment = {
            **os.environ,
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "CAMERA_VERIFY_TEST_LOG": str(self.log),
            "CAMERA_VERIFY_TEST_REMOTE_OUTPUT": str(self.remote_file),
            "CAMERA_VERIFY_TEST_SSH_MODE": ssh_mode,
        }
        return subprocess.run(
            ["bash", str(self.checkout / "scripts/camera_verify.sh"), *args],
            cwd="/",
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def tool_calls(self) -> str:
        return self.log.read_text() if self.log.exists() else ""

    def collected(self, name: str) -> str:
        return (self.out / name).read_text()

    # ------------------------------------------------------------ help/manifest

    def test_help_works_without_manifest(self) -> None:
        self.manifest.unlink()
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: bash scripts/camera_verify.sh", result.stdout)
        self.assertIn("--collect-dir", result.stdout)
        self.assertEqual(self.tool_calls(), "")

    def test_missing_manifest_fails_closed_before_ssh(self) -> None:
        self.manifest.unlink()
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing delivery manifest", result.stderr)
        self.assertIn("DELIVERY.json", result.stderr)
        self.assertIn("camera_build.sh manifest", result.stderr)
        self.assertNotIn("ssh ", self.tool_calls())

    def test_manifest_release_mismatch_fails_closed(self) -> None:
        self.write_manifest(release="6.17.0-sm8150")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("release", result.stderr)
        self.assertIn("6.17.0-sm8150", result.stderr)
        self.assertEqual(self.tool_calls(), "")

    def test_manifest_cci_hash_mismatch_fails_closed(self) -> None:
        self.write_manifest(
            i2c_qcom_cci={"path": MODULE_PATH, "sha256": STALE_SHA, "size": 8920}
        )
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("STALE MODULE", result.stdout)
        self.assertIn("expected:", result.stdout)
        self.assertIn(STALE_SHA, result.stdout)

    def test_manifest_cci_size_mismatch_fails_closed(self) -> None:
        self.write_manifest(i2c_qcom_cci={"path": MODULE_PATH, "sha256": FIXED_SHA, "size": 8920})
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("expected:", result.stdout)
        self.assertIn("8920", result.stdout)

    def test_future_build_cci_identity_is_accepted_from_the_manifest(self) -> None:
        future = "7" * 64
        self.write_manifest(i2c_qcom_cci={"path": MODULE_PATH, "sha256": future, "size": 9001})
        self.remote_file.write_text(remote_output(module_sha=future, module_size=9001))
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(future, result.stdout)
        self.assertNotIn(FIXED_SHA, result.stdout)

    def test_manifest_from_another_worktree_fails_closed(self) -> None:
        other = Path(self.temporary.name) / "nura-sm8150-7-0"
        other.mkdir()
        self.write_manifest(worktree=str(other))
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("another worktree", result.stderr)
        self.assertEqual(self.tool_calls(), "")

    def test_corrupt_manifest_fails_closed(self) -> None:
        self.manifest.write_text("{not json")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot read JSON", result.stderr)
        self.assertEqual(self.tool_calls(), "")

    # ------------------------------------------------------------ dry-run/local

    def test_dry_run_lists_remote_commands_without_connecting(self) -> None:
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        for token in (
            "@@UNAME@@",
            "@@MODULE@@",
            "@@DMESG@@",
            "@@MEDIA@@",
            "@@POWER@@",
            "sha256sum",
            "lsmod",
            "dmesg",
            "media-ctl -d",
            "v4l2-ctl --list-devices",
            "cam-rear -l",
            "pm_genpd_summary",
            "cat /proc/cmdline",
            "CAMERA_VERIFY_SUDO=0 sh -s",
        ):
            self.assertIn(token, result.stdout)
        self.assertEqual(self.tool_calls(), "")
        self.assertFalse((self.checkout / "build/rear-camera/diagnostics").exists())

    def test_local_mode_is_labelled_and_never_reports_a_pass(self) -> None:
        result = self.run_script("--local", "--collect-dir", str(self.out))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("LOCAL MODE", result.stdout)
        self.assertIn("not evidence about the phone", result.stdout)
        self.assertIn("LOCAL MODE", self.collected("summary.txt"))
        self.assertNotIn("ssh ", self.tool_calls())

    def test_dry_run_and_local_are_mutually_exclusive(self) -> None:
        result = self.run_script("--dry-run", "--local")
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)

    def test_invalid_timeout_is_rejected(self) -> None:
        result = self.run_script("--timeout", "abc")
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive integer", result.stderr)

    def test_unknown_argument_is_rejected(self) -> None:
        result = self.run_script("--nonsense")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)

    # ------------------------------------------------------------ primary verdicts

    def test_fixed_module_running_passes_and_writes_diagnostics(self) -> None:
        result = self.run_script("--collect-dir", str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS kernel release", result.stdout)
        self.assertIn("PASS cci module identity", result.stdout)
        self.assertIn("VERDICT: FIXED MODULE RUNNING", result.stdout)
        for name in (
            "uname.txt",
            "cmdline.txt",
            "module-sha256.txt",
            "lsmod.txt",
            "dmesg-grep.txt",
            "media-topology.txt",
            "power-summary.txt",
            "summary.txt",
        ):
            self.assertTrue((self.out / name).is_file(), name)
        module = self.collected("module-sha256.txt")
        self.assertIn(f"expected_sha256: {FIXED_SHA}", module)
        self.assertIn(f"observed_sha256: {FIXED_SHA}", module)
        self.assertIn("result: PASS", module)
        summary = self.collected("summary.txt")
        self.assertIn("VERDICT: FIXED MODULE RUNNING", summary)
        self.assertIn("mode: remote", summary)

    def test_stale_module_prints_the_exact_serve_fix(self) -> None:
        stale = remote_output(module_sha=STALE_SHA, module_size=8920)
        result = self.run_script("--collect-dir", str(self.out), remote=stale)
        self.assertEqual(result.returncode, 1)
        self.assertIn("STALE MODULE", result.stdout)
        self.assertIn(f"bash {self.checkout}/scripts/camera_serve.sh --replace", result.stdout)
        self.assertIn(f"bash {self.checkout}/scripts/camera_boot.sh", result.stdout)
        self.assertIn("images_netboot/oneplus-guacamole.img", result.stdout)
        self.assertIn("Do not boot build/rear-camera/boot.img", result.stdout)
        module = self.collected("module-sha256.txt")
        self.assertIn(f"observed_sha256: {STALE_SHA}", module)
        self.assertIn("observed_size: 8920", module)
        self.assertIn("result: FAIL", module)

    def test_missing_module_is_reported_as_stale(self) -> None:
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(module_state="missing")
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing at", result.stdout)
        self.assertIn("VERDICT: STALE MODULE", result.stdout)
        self.assertIn(f"bash {self.checkout}/scripts/camera_serve.sh --replace", result.stdout)

    def test_unverifiable_module_hash_fails_closed(self) -> None:
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(module_state="no-sha256sum")
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("cannot verify", result.stdout)
        self.assertIn("VERDICT: STALE MODULE", result.stdout)

    def test_wrong_kernel_release_is_stale(self) -> None:
        result = self.run_script(
            "--collect-dir",
            str(self.out),
            remote=remote_output(release="6.17.0-sm8150"),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("FAIL kernel release", result.stdout)
        self.assertIn("VERDICT: STALE MODULE", result.stdout)

    def test_cci_reset_timeout_is_probe_timeout(self) -> None:
        dmesg = (
            "i2c-qcom-cci 0c1c000.i2c: CCI HW version = 0x00030000\n"
            "i2c-qcom-cci 0c1c000.i2c: CCI reset timeout"
        )
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(dmesg=dmesg)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("CCI timeout", result.stdout)
        self.assertIn("VERDICT: PROBE TIMEOUT", result.stdout)

    def test_oops_is_reported_with_photo_and_pstore_guidance(self) -> None:
        dmesg = (
            "Unable to handle kernel NULL pointer dereference at virtual address 0000000000000000\n"
            "Oops: 0000000096000005 [#1] SMP\n"
            "i2c-qcom-cci 0c1c000.i2c: CCI reset timeout"
        )
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(dmesg=dmesg)
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("VERDICT: OOPS", result.stdout)
        self.assertIn("photograph", result.stdout)
        self.assertIn("scripts/ramoops.sh", result.stdout)
        self.assertIn("pstore", result.stdout)

    def test_dmesg_denied_is_a_warning_not_a_stale_module(self) -> None:
        result = self.run_script(
            "--collect-dir",
            str(self.out),
            remote=remote_output(dmesg="DMESG_DENIED: dmesg: klogctl: Operation not permitted"),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN dmesg camera lines", result.stdout)
        self.assertIn("--sudo", result.stdout)
        self.assertIn("VERDICT: FIXED MODULE RUNNING", result.stdout)

    # ------------------------------------------------------------ secondary checks

    def test_netboot_cmdline_missing_exits_2(self) -> None:
        result = self.run_script(
            "--collect-dir",
            str(self.out),
            remote=remote_output(cmdline="BOOT_IMAGE=/boot/vmlinuz pmos.root=/dev/sda19"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("FAIL netboot root cmdline", result.stdout)
        self.assertIn("VERDICT: FIXED MODULE RUNNING", result.stdout)

    def test_missing_sensor_entity_exits_2(self) -> None:
        media = "== /dev/media0\n- entity 1: imx586 0-001a (1 pad, 1 link)"
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(media=media)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing sensor entities", result.stdout)
        self.assertIn("imx481", result.stdout)

    def test_unlinked_sensor_entity_exits_2(self) -> None:
        media = "\n".join(
            (
                "== /dev/media0",
                "- entity 1: imx586 0-001a (1 pad, 1 link)",
                "- entity 2: imx481 4-001a (1 pad, 1 link)",
                "- entity 3: s5k3m5 2-0010 (1 pad, 1 link)",
                '    <- "imx586 0-001a":0 [ENABLED]',
                '    <- "s5k3m5 2-0010":0 [ENABLED]',
            )
        )
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(media=media)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not linked: imx481", result.stdout)

    def test_no_media_device_exits_2(self) -> None:
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(media="NO_MEDIA_DEVICES")
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("no /dev/media*", result.stdout)

    def test_media_ctl_missing_is_a_skip_not_a_failure(self) -> None:
        media = "== /dev/media0\nmedia-ctl not installed"
        result = self.run_script(
            "--collect-dir", str(self.out), remote=remote_output(media=media)
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SKIP media topology", result.stdout)
        self.assertIn("media-ctl is not installed on the phone", result.stdout)

    # ------------------------------------------------------------ ssh behaviour

    def test_no_ssh_reports_diagnostics_without_rebooting(self) -> None:
        result = self.run_script(
            "--timeout", "1", "--collect-dir", str(self.out), ssh_mode="fail", timeout=30
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("NO SSH", result.stdout)
        self.assertIn("VERDICT: NO SSH", result.stdout)
        self.assertIn(f"bash {self.checkout}/scripts/camera_boot.sh", result.stdout)
        self.assertIn("scripts/camera_serve.sh", result.stdout)
        self.assertIn("ip neigh", result.stdout)
        self.assertIn("nc -vz 172.16.42.1 22", result.stdout)
        calls = self.tool_calls()
        for command in ("ip -o -4 addr show", "ip neigh show 172.16.42.1",
                        "ping -c1 -W2 172.16.42.1", "nc -vz -w2 172.16.42.1 22"):
            self.assertIn(command, calls)
        diagnosis = (self.out / "ssh-diagnosis.txt").read_text()
        self.assertIn("camera_serve.sh", diagnosis)
        self.assertNotIn("reboot\n", result.stdout.lower())
        self.assertIn("never reboots anything", result.stdout)

    def test_ssh_host_key_change_is_diagnosed(self) -> None:
        result = self.run_script(
            "--timeout", "1", "--collect-dir", str(self.out), ssh_mode="hostkey", timeout=30
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ssh host key mismatch", result.stdout)
        self.assertIn(f"ssh-keygen -R 172.16.42.1 -f {self.checkout}/.work/delivery/known_hosts",
                      result.stdout)

    def test_documented_ssh_options_and_custom_target(self) -> None:
        result = self.run_script(
            "--host", "10.1.2.3", "--user", "root", "--collect-dir", str(self.out)
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.tool_calls()
        for option in (
            "-4",
            "ConnectTimeout=10",
            "StrictHostKeyChecking=accept-new",
            f"UserKnownHostsFile={self.checkout}/.work/delivery/known_hosts",
            "root@10.1.2.3",
        ):
            self.assertIn(option, calls)
        self.assertNotIn("user@172.16.42.1", calls)

    def test_sudo_flag_is_forwarded_to_the_read_only_probe(self) -> None:
        result = self.run_script("--sudo", "--collect-dir", str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CAMERA_VERIFY_SUDO=1 sh -s", self.tool_calls())

    def test_no_sudo_overrides_sudo_in_argument_order(self) -> None:
        result = self.run_script("--sudo", "--no-sudo", "--collect-dir", str(self.out))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CAMERA_VERIFY_SUDO=0 sh -s", self.tool_calls())
        self.assertNotIn("CAMERA_VERIFY_SUDO=1 sh -s", self.tool_calls())

    def test_default_collect_dir_is_under_the_resolved_checkout(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        diagnostics = list((self.checkout / "build/rear-camera/diagnostics").glob("phone-*"))
        self.assertEqual(len(diagnostics), 1)
        self.assertTrue((diagnostics[0] / "summary.txt").is_file())


if __name__ == "__main__":
    unittest.main()
