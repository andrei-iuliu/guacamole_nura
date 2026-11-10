#!/usr/bin/env python3
"""Host-side tests for collect_profile.sh; fake ssh only, no phone or hardware."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
COLLECT = SCRIPTS / "collect_profile.sh"

SNAPSHOT = """@@META@@
probe_version=1
collected_utc=2026-11-10T12:00:00Z
kernel_release=7.0.0-fake
kernel_version=#1 SMP PREEMPT
boot_cmdline=console=tty0
@@CAMERA_PROCESSES@@
[NOT_OBSERVED] no camera process
@@LIBCAMERA_LIBS@@
[NOT_OBSERVED] no libcamera mapped
@@DMESG@@
[NO_MATCH]
@@END@@
"""


class CollectProfileTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkout = self.tmp / "checkout with spaces"
        (self.checkout / "scripts").mkdir(parents=True)
        for name in ("collect_profile.sh", "nura-probe.sh", "analyze_profile.py"):
            shutil.copy2(SCRIPTS / name, self.checkout / "scripts" / name)
        self.snapshot = self.tmp / "snapshot.txt"
        self.snapshot.write_text(SNAPSHOT)
        self.out = self.checkout / "out with spaces"
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()
        self.ssh_log = self.tmp / "ssh.log"
        self.ssh_fixture = self.tmp / "ssh-fixture.txt"
        self.ssh_fixture.write_text(SNAPSHOT)
        self.install_fake_ssh()

    def install_fake_ssh(self) -> None:
        ssh = self.fakebin / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >> "$COLLECT_SSH_LOG"\n'
            'if [ "${COLLECT_SSH_MODE:-ok}" = fail ]; then exit 255; fi\n'
            'cat "$COLLECT_SSH_FIXTURE"\n'
        )
        ssh.chmod(0o755)

    def run_collect(self, *args: str, mode: str = "ok") -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.fakebin}:{os.environ['PATH']}",
            "COLLECT_SSH_LOG": str(self.ssh_log),
            "COLLECT_SSH_FIXTURE": str(self.ssh_fixture),
            "COLLECT_SSH_MODE": mode,
        }
        return subprocess.run(
            ["bash", str(self.checkout / "scripts/collect_profile.sh"), *args],
            capture_output=True, text=True, timeout=60, env=env,
        )

    def ssh_calls(self) -> list[str]:
        if not self.ssh_log.exists():
            return []
        return [line for line in self.ssh_log.read_text().splitlines() if line]

    def test_help_needs_no_input(self) -> None:
        result = self.run_collect("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: bash collect_profile.sh", result.stdout)
        self.assertFalse(self.ssh_log.exists())

    def test_requires_snapshot_or_ssh(self) -> None:
        result = self.run_collect("--out", str(self.out))
        self.assertEqual(result.returncode, 2)
        self.assertIn("provide --snapshot", result.stderr)
        self.assertFalse(self.out.exists())

    def test_snapshot_mode_does_not_call_ssh(self) -> None:
        result = self.run_collect("--snapshot", str(self.snapshot), "--out", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.ssh_calls(), [])
        self.assertTrue((self.out / "report.md").is_file())
        self.assertTrue((self.out / "analysis.json").is_file())
        self.assertTrue((self.out / "videos.tsv").is_file())
        self.assertTrue((self.out / "SHA256SUMS").is_file())
        self.assertIn("Verdict: `INSUFFICIENT_DATA`", (self.out / "report.md").read_text())

    def test_ssh_is_one_batchmode_attempt(self) -> None:
        result = self.run_collect("--ssh", "user@phone", "--out", str(self.out))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.ssh_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("BatchMode=yes", calls[0])
        self.assertIn("StrictHostKeyChecking=yes", calls[0])
        self.assertIn("NumberOfPasswordPrompts=0", calls[0])
        self.assertIn("user@phone", calls[0])
        self.assertTrue((self.out / "ssh.rc").is_file())
        self.assertEqual((self.out / "ssh.rc").read_text().strip(), "0")

    def test_ssh_failure_does_not_retry_or_analyze(self) -> None:
        result = self.run_collect("--ssh", "user@phone", "--out", str(self.out), mode="fail")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(self.ssh_calls()), 1)
        self.assertIn("no retry", result.stderr)
        self.assertFalse((self.out / "report.md").exists())

    def test_snapshot_and_ssh_are_mutually_exclusive(self) -> None:
        result = self.run_collect(
            "--snapshot", str(self.snapshot), "--ssh", "user@phone", "--out", str(self.out)
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutually exclusive", result.stderr)
        self.assertEqual(self.ssh_calls(), [])

    def test_missing_recordings_directory_fails_before_analysis(self) -> None:
        result = self.run_collect(
            "--snapshot", str(self.snapshot), "--out", str(self.out),
            "--recordings", str(self.tmp / "does-not-exist"),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("recordings directory", result.stderr)
        self.assertFalse(self.out.exists())

    def test_manifest_is_copied_into_bundle(self) -> None:
        manifest = self.tmp / "DELIVERY.json"
        manifest.write_text('{"release": "7.0.0-fake"}\n')
        result = self.run_collect(
            "--snapshot", str(self.snapshot), "--out", str(self.out),
            "--manifest", str(manifest),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "manifest.json").is_file())
        self.assertIn("manifest.json", (self.out / "SHA256SUMS").read_text())

    def test_user_dmesg_is_copied_and_used(self) -> None:
        dmesg = self.tmp / "dmesg.txt"
        dmesg.write_text("i2c-qcom-cci 0c1c000.i2c: cci reset timeout\n")
        result = self.run_collect(
            "--snapshot", str(self.snapshot), "--out", str(self.out),
            "--dmesg", str(dmesg),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.out / "dmesg.txt").is_file())
        self.assertIn("OBSERVED_ERRORS", (self.out / "report.md").read_text())
        self.assertIn("dmesg.txt", (self.out / "SHA256SUMS").read_text())


if __name__ == "__main__":
    unittest.main()
