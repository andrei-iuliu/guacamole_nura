#!/usr/bin/env python3
"""Rootless tests for the simplified journal-only boot diagnostics snapshot.

Every test uses a temporary fake /proc+/sys tree, a fake systemctl and a
temporary working directory.  No real journal, unit, phone, sudo or admin tool
is used.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "collect_boot_diagnostics.sh"
UNIT_DIR = REPO / "device" / "testing" / "device-oneplus-guacamole"
DOC = REPO / "docs" / "NURA_BOOT_DIAGNOSTICS.md"

FAILED_TEXT = """serial-getty@ttyMSM0.service loaded failed failed Serial Getty
foo.service loaded failed failed Foo failure
"""


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name).resolve()
        self.root = self.tmp / "fakeroot"
        self.fakebin = self.tmp / "fakebin"
        self.run_dir = self.tmp / "run"
        self.fakebin.mkdir()
        self.run_dir.mkdir()
        self.failed_file = self.tmp / "failed.txt"
        self.failed_file.write_text(FAILED_TEXT)

        (self.root / "proc" / "asound").mkdir(parents=True)
        (self.root / "proc" / "asound" / "cards").write_text(
            " 0 [OnePlus7Pro]: sm8150 - OnePlus 7 Pro\n"
        )
        self._fake_file("sys/class/drm/card0/status", "connected\n")
        self._fake_file("sys/class/drm/card0-DP-1/status", "disconnected\n")
        self._fake_file("sys/class/udc/a600000.dwc3/state", "not attached\n")
        self._fake_file("sys/class/udc/a600000.dwc3/current_speed", "super-speed\n")
        self._fake_file("sys/class/remoteproc/remoteproc0/state", "running\n")
        self._fake_file("sys/class/remoteproc/remoteproc0/name", "modem\n")
        self._fake_file("sys/class/remoteproc/remoteproc0/firmware", "modem.mdt\n")
        self._fake_file("sys/class/typec/port0/data_role", "device\n")
        self._fake_file("sys/class/typec/port0/power_role", "sink\n")

        self._fake_exe(
            "systemctl",
            'if [ -n "${FAKE_SYSTEMCTL_SLEEP:-}" ]; then exec sleep "$FAKE_SYSTEMCTL_SLEEP"; fi\n'
            'if [ -n "${FAKE_SYSTEMCTL_FILE:-}" ]; then cat "$FAKE_SYSTEMCTL_FILE"; exit 0; fi\n'
            "printf 'serial-getty@ttyMSM0.service loaded failed failed Serial Getty\\n'\n"
            "printf 'foo.service loaded failed failed Foo failure\\n'\n",
        )

    def tearDown(self) -> None:
        self._td.cleanup()

    def _fake_file(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def _fake_exe(self, name: str, body: str) -> None:
        path = self.fakebin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def _minbin(self, names) -> Path:
        path = self.tmp / ("minbin-" + "-".join(names))
        path.mkdir()
        for name in names:
            target = shutil.which(name)
            self.assertIsNotNone(target, name)
            os.symlink(target, path / name)
        return path

    def run_collector(self, extra_env=None, path=None, timeout: int = 30):
        env = os.environ.copy()
        env.update(
            {
                "PATH": path or (str(self.fakebin) + ":" + env.get("PATH", "")),
                "NURA_BOOT_DIAGNOSTICS_ROOT": str(self.root),
                "FAKE_SYSTEMCTL_FILE": str(self.failed_file),
            }
        )
        if extra_env:
            env.update({key: str(value) for key, value in extra_env.items()})
        return subprocess.run(
            ["/bin/sh", str(SCRIPT)],
            env=env,
            cwd=self.run_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ happy
    def test_snapshot_is_verbatim_bounded_and_does_not_write(self) -> None:
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout

        self.assertIn("nura-boot-diagnostics: begin", out)
        self.assertIn("nura-boot-diagnostics: failed-units", out)
        self.assertIn("nura-boot-diagnostics: sysfs", out)
        self.assertIn("nura-boot-diagnostics: end", out)
        # This line contains "serial" and no kernel-interest keyword.  The old
        # kernel filter would have dropped it; failed-unit output must be verbatim.
        self.assertIn("serial-getty@ttyMSM0.service loaded failed failed Serial Getty", out)
        self.assertIn("foo.service loaded failed failed Foo failure", out)
        self.assertNotIn("[MISSING] systemctl", out)

        # Exact allowlist values.
        self.assertIn("card0/status=connected", out)
        self.assertIn("card0-DP-1/status=disconnected", out)
        self.assertIn("a600000.dwc3/state=not attached", out)
        self.assertIn("a600000.dwc3/current_speed=super-speed", out)
        self.assertIn("remoteproc0/state=running", out)
        self.assertIn("remoteproc0/name=modem", out)
        self.assertIn("remoteproc0/firmware=modem.mdt", out)
        self.assertIn("port0/data_role=device", out)
        self.assertIn("port0/power_role=sink", out)
        self.assertIn("/proc/asound/cards=", out)

        # Journal-only: no state directory, boot sequence or file output.
        self.assertEqual(list(self.run_dir.iterdir()), [])
        self.assertNotIn("boot_id=", out)
        self.assertNotIn("boot-seq", out)
        self.assertNotIn("snapshot-", out)

    def test_failed_units_are_not_kernel_filtered(self) -> None:
        self.failed_file.write_text(
            "imei-reader.service loaded failed failed IMEI Reader\n"
            "serial-getty@ttyMSM0.service loaded failed failed Serial Getty\n"
            "drm-helper.service loaded failed failed DRM helper\n"
        )
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("imei-reader.service", result.stdout)
        self.assertIn("serial-getty@ttyMSM0.service", result.stdout)
        self.assertIn("drm-helper.service", result.stdout)

    def test_total_output_is_capped(self) -> None:
        # Force both the failed-unit cap and the sysfs entry cap.
        self.failed_file.write_text(
            "".join(f"unit{i}.service loaded failed failed failure {i}\n" for i in range(1000))
        )
        for i in range(100):
            self._fake_file(f"sys/class/udc/udc{i}/state", "x" * 512 + "\n")
        result = self.run_collector()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLessEqual(len(result.stdout.splitlines()), 120)
        self.assertLessEqual(len(result.stdout.encode()), 16384)

    def test_systemctl_timeout_is_bounded(self) -> None:
        started = time.monotonic()
        result = self.run_collector(
            extra_env={"FAKE_SYSTEMCTL_SLEEP": "10"}, timeout=20
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 8.0)
        self.assertIn("[NONE-OR-UNAVAILABLE]", result.stdout)
        self.assertIn("card0/status=connected", result.stdout)

    def test_missing_systemctl_is_reported(self) -> None:
        minbin = self._minbin(("head", "tr"))
        result = self.run_collector(path=str(minbin))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[MISSING] systemctl", result.stdout)
        self.assertIn("card0/status=connected", result.stdout)

    def test_missing_head_is_reported(self) -> None:
        minbin = self._minbin(("tr",))
        result = self.run_collector(path=str(minbin))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[MISSING] head", result.stdout)

    # ------------------------------------------------------- source and units
    def test_source_is_journal_only_and_has_exact_allowlist(self) -> None:
        text = SCRIPT.read_text()
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        for forbidden in (
            "journalctl",
            "dmesg",
            "mkdir",
            "StateDirectory",
            "retention",
            "boot-sequence",
            "boot_id",
            "/proc/[0-9]+",
            "debugfs",
            "/sys/kernel/debug",
            "lsusb",
            "udevadm",
            "sudo",
            r"\bssh\b",
            r"\badb\b",
            "fastboot",
            "systemctl start",
            "systemctl stop",
            "systemctl restart",
            "systemctl enable",
            "systemctl disable",
            r"\bdate\b",
        ):
            self.assertIsNone(re.search(forbidden, code), f"forbidden in code: {forbidden}")
        for allowed in (
            "/proc/asound/cards",
            "/sys/class/drm/card[0-9]/status",
            "/sys/class/drm/card[0-9][0-9]/status",
            "/sys/class/drm/card*-*/status",
            "/sys/class/udc/*/state",
            "/sys/class/udc/*/current_speed",
            "/sys/class/remoteproc/remoteproc*/state",
            "/sys/class/remoteproc/remoteproc*/name",
            "/sys/class/remoteproc/remoteproc*/firmware",
            "/sys/class/typec/port*/data_role",
            "/sys/class/typec/port*/power_role",
            "systemctl --failed",
            "head -c",
            "head -n",
            "timeout",
            "[MISSING]",
        ):
            self.assertIn(allowed, code)
        self.assertLess(len(text.splitlines()), 150)

    def test_units_are_journal_only_without_state_directory(self) -> None:
        service = (UNIT_DIR / "nura-boot-diagnostics.service").read_text()
        timer = (UNIT_DIR / "nura-boot-diagnostics.timer").read_text()
        preset = (UNIT_DIR / "81-nura-boot-diagnostics.preset").read_text()

        for needle in (
            "Type=oneshot",
            "ExecStart=/usr/bin/nura-boot-diagnostics",
            "StandardOutput=journal",
            "TimeoutStartSec=15",
            "NoNewPrivileges=yes",
            "WantedBy=multi-user.target",
        ):
            self.assertIn(needle, service)
        for forbidden in ("StateDirectory=", "StateDirectoryMode=", "ReadWritePaths="):
            self.assertNotIn(forbidden, service)
        for needle in (
            "OnBootSec=3min",
            "Persistent=false",
            "Unit=nura-boot-diagnostics.service",
            "WantedBy=timers.target",
        ):
            self.assertIn(needle, timer)
        self.assertNotIn("OnCalendar=", timer)
        self.assertNotIn("Persistent=true", timer)
        self.assertIn("enable nura-boot-diagnostics.service", preset)
        self.assertIn("enable nura-boot-diagnostics.timer", preset)

    def test_docs_cover_freeze_and_evidence_limits(self) -> None:
        text = DOC.read_text().lower()
        for needle in (
            "freeze",
            "power+volumeup",
            "not a spontaneous reboot",
            "later freeze",
            "pstore",
            "nbd loss",
            "fresh serve",
            "writable cache",
            "journald",
            "systemmaxuse=1g",
            "no statedirectory",
        ):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
