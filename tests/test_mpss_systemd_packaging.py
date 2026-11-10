#!/usr/bin/env python3
"""Focused tests for the opt-in MPSS systemd starter, using fake sysfs only."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEVICE = ROOT / "device/testing/device-oneplus-guacamole"
HELPER = DEVICE / "guacamole-mpss-start"
UNIT = DEVICE / "guacamole-mpss.service"
PRESET = DEVICE / "80-guacamole.preset"
APKBUILD = DEVICE / "APKBUILD"


def checksums() -> dict[str, str]:
    text = APKBUILD.read_text()
    match = re.search(r'sha512sums="(.*?)"\n', text, re.S)
    if match is None:
        raise AssertionError("no sha512sums in APKBUILD")
    result: dict[str, str] = {}
    for line in match.group(1).strip().splitlines():
        digest, name = line.split()
        result[name] = digest
    return result


class FakeRemoteproc:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.class_dir = root / "class/remoteproc"
        self.class_dir.mkdir(parents=True)
        self.ipa_module = root / "module/ipa"
        self.ipa_module.mkdir(parents=True)
        self.ipa_driver = root / "bus/platform/drivers/ipa"
        self.ipa_driver.mkdir(parents=True)
        (self.ipa_driver / "bind").write_text("")
        (self.ipa_driver / "1e40000.ipa").mkdir()

    def add(
        self,
        index: int,
        name: str,
        state: str,
        compatible: str | None = None,
    ) -> Path:
        entry = self.class_dir / f"remoteproc{index}"
        entry.mkdir()
        (entry / "name").write_text(name + "\n")
        (entry / "state").write_text(state + "\n")
        if compatible is not None:
            of_node = entry / "device/of_node"
            of_node.mkdir(parents=True)
            (of_node / "compatible").write_bytes(compatible.encode() + b"\0")
        return entry

    def env(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GUACAMOLE_REMOTEPROC_CLASS": str(self.class_dir),
                "GUACAMOLE_MPSS_IPA_MODULE": str(self.ipa_module),
                "GUACAMOLE_MPSS_IPA_DRIVER": str(self.ipa_driver),
                "GUACAMOLE_MPSS_IPA_TIMEOUT": "0",
                "GUACAMOLE_MPSS_START_TIMEOUT": "1",
                "GUACAMOLE_MPSS_POLL_INTERVAL": "0.02",
            }
        )
        env.update(overrides)
        return env

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(HELPER)],
            env=self.env(**overrides),
            text=True,
            capture_output=True,
            timeout=10,
        )


class HelperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="guacamole-mpss-test-")
        self.fake = FakeRemoteproc(Path(self.tmp.name))
        self.modem = self.fake.add(
            3,
            "modem",
            "offline",
            compatible="qcom,sm8150-mpss-pas",
        )
        self.adsp = self.fake.add(
            0,
            "adsp",
            "running",
            compatible="qcom,sm8150-adsp-pas",
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _simulate_start(self, entry: Path) -> tuple[bool, int, str, str]:
        proc = subprocess.Popen(
            ["/bin/sh", str(HELPER)],
            env=self.fake.env(GUACAMOLE_MPSS_START_TIMEOUT="2"),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        observed = False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            try:
                if (entry / "state").read_text().strip() == "start":
                    (entry / "state").write_text("running\n")
                    observed = True
                    break
            except OSError:
                pass
            time.sleep(0.01)
        stdout, stderr = proc.communicate(timeout=5)
        return observed, proc.returncode, stdout, stderr

    def test_resolves_mpss_by_compatible_not_class_index(self) -> None:
        observed, code, stdout, stderr = self._simulate_start(self.modem)
        self.assertTrue(observed, stderr)
        self.assertEqual(code, 0, stdout + stderr)
        self.assertIn("MPSS is running", stdout)
        self.assertEqual((self.modem / "state").read_text().strip(), "running")
        self.assertEqual((self.adsp / "state").read_text().strip(), "running")

    def test_running_mpss_is_not_written(self) -> None:
        (self.modem / "state").write_text("running\n")
        (self.modem / "state").chmod(0o444)
        result = self.fake.run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already running", result.stdout)
        self.assertEqual((self.modem / "state").read_text().strip(), "running")

    def test_crashed_mpss_is_not_restarted(self) -> None:
        (self.modem / "state").write_text("crashed\n")
        result = self.fake.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crashed", result.stderr)
        self.assertEqual((self.modem / "state").read_text().strip(), "crashed")

    def test_missing_mpss_fails(self) -> None:
        (self.modem / "name").unlink()
        (self.modem / "device/of_node/compatible").unlink()
        result = self.fake.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no remoteproc", result.stderr)
        self.assertEqual((self.adsp / "state").read_text().strip(), "running")

    def test_ambiguous_mpss_entries_fail_without_writing(self) -> None:
        (self.modem / "device/of_node/compatible").unlink()
        self.fake.add(4, "modem", "offline")
        result = self.fake.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("multiple remoteprocs", result.stderr)
        self.assertEqual((self.modem / "state").read_text().strip(), "offline")

    def test_ipa_readiness_fails_before_start(self) -> None:
        (self.fake.ipa_driver / "1e40000.ipa").rmdir()
        self.fake.ipa_module.rmdir()
        result = self.fake.run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("IPA is not ready", result.stderr)
        self.assertEqual((self.modem / "state").read_text().strip(), "offline")

    def test_start_wait_is_bounded(self) -> None:
        started = time.monotonic()
        result = self.fake.run(GUACAMOLE_MPSS_START_TIMEOUT="0")
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not reach running", result.stderr)
        self.assertLess(elapsed, 5)
        self.assertEqual((self.modem / "state").read_text().strip(), "start")

    def test_compatible_wins_when_sysmon_name_differs(self) -> None:
        (self.modem / "name").write_text("mpss\n")
        observed, code, stdout, stderr = self._simulate_start(self.modem)
        self.assertTrue(observed, stderr)
        self.assertEqual(code, 0, stdout + stderr)

    def test_resolves_by_sysmon_name_when_compatible_is_missing(self) -> None:
        (self.modem / "device/of_node/compatible").unlink()
        observed, code, stdout, stderr = self._simulate_start(self.modem)
        self.assertTrue(observed, stderr)
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual((self.adsp / "state").read_text().strip(), "running")


class PackagingTest(unittest.TestCase):
    def test_unit_is_opt_in_and_ordered_without_cycles(self) -> None:
        unit = UNIT.read_text()
        self.assertIn("After=rmtfs.service tqftpserv.service", unit)
        self.assertIn(
            "Before=msm-modem-uim-selection.service ModemManager.service", unit
        )
        self.assertIn("ExecStart=/usr/libexec/guacamole-mpss-start", unit)
        self.assertIn("WantedBy=multi-user.target", unit)
        self.assertIn("systemctl enable --now guacamole-mpss.service", unit)
        preset = PRESET.read_text()
        self.assertNotRegex(
            preset, r"(?m)^enable\s+guacamole-mpss\.service\s*$"
        )
        self.assertRegex(preset, r"(?m)^enable\s+guacamole-chgctrl\.service\s*$")

    def test_recipe_installs_the_helper_and_unit(self) -> None:
        recipe = APKBUILD.read_text()
        for name in ("guacamole-mpss-start", "guacamole-mpss.service"):
            self.assertIn(f"\t{name}\n", recipe)
        self.assertIn(
            'install -Dm755 "$srcdir/guacamole-mpss-start"', recipe
        )
        self.assertIn(
            '"$pkgdir/usr/libexec/guacamole-mpss-start"', recipe
        )
        self.assertIn(
            '"$pkgdir/usr/lib/systemd/system/guacamole-mpss.service"', recipe
        )
        initd = (DEVICE / "guacamole-mpss.initd").read_text()
        self.assertIn("/usr/libexec/guacamole-mpss-start", initd)
        self.assertNotIn("remoteproc0", initd)

    def test_sources_match_their_checksums(self) -> None:
        sums = checksums()
        for name in (
            "guacamole-mpss.initd",
            "guacamole-mpss-start",
            "guacamole-mpss.service",
            "80-guacamole.preset",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    hashlib.sha512((DEVICE / name).read_bytes()).hexdigest(),
                    sums[name],
                )


if __name__ == "__main__":
    unittest.main()
