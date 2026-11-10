#!/usr/bin/env python3
"""Focused tests for the per-unit sensor registry import (no phone, no root)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "device/testing/device-oneplus-guacamole/guacamole-sensors-registry"


class RegistryImportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="guacamole-registry-test-")
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fixture = self.root / "fixture"
        (self.fixture / "registry").mkdir(parents=True)
        (self.fixture / "registry/file.bin").write_text("calibration")
        self.state = self.root / "state"
        self.env = os.environ.copy()
        self.env.update(
            {
                "PATH": f"{self.bin}:{os.environ['PATH']}",
                "GUACAMOLE_SENSORS_STATE": str(self.state),
                "GUACAMOLE_SENSORS_PERSIST": str(self.root / "persist"),
                "GUACAMOLE_SENSORS_OWNER": f"{os.getuid()}:{os.getgid()}",
                "STUB_FIXTURE": str(self.fixture),
            }
        )
        self._write_debugfs()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_debugfs(self) -> None:
        stub = self.bin / "debugfs"
        stub.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                if [ "${STUB_MODE:-ok}" = fail ]; then
                    exit 1
                fi
                request=$2
                dest=${request##* }
                mkdir -p "$dest/registry/registry"
                cp "$STUB_FIXTURE/registry/file.bin" "$dest/registry/registry/file.bin"
                printf 'version=9\\n' > "$dest/registry/sns_reg_version"
                printf 'one' > "$dest/registry/file1"
                printf 'two' > "$dest/registry/file2"
                """
            )
        )
        stub.chmod(0o755)

    def _run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["/bin/sh", str(SCRIPT)],
            env=self.env,
            text=True,
            capture_output=True,
        )

    def test_imports_only_the_units_own_registry(self) -> None:
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("importing the factory registry", result.stdout)
        self.assertEqual(
            (self.state / "registry/file.bin").read_text(), "calibration"
        )
        self.assertEqual(
            (self.state / "sns_reg_version").read_text(), "version=9\n"
        )
        self.assertEqual((self.state / "file1").read_text(), "one")
        self.assertEqual((self.state / "file2").read_text(), "two")
        self.assertTrue((self.state / "project_info/project_name").is_file())

    def test_missing_calibration_fails_closed(self) -> None:
        self.env["STUB_MODE"] = "fail"
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("factory sensor", result.stderr)
        self.assertIn("sensors stay disabled", result.stderr)
        self.assertFalse((self.state / "registry").exists())

    def test_empty_registry_is_not_accepted(self) -> None:
        stub = self.bin / "debugfs"
        stub.write_text(
            "#!/bin/sh\nrequest=$2\ndest=${request##* }\nmkdir -p \"$dest/registry/registry\"\n"
        )
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("factory sensor", result.stderr)
        self.assertFalse((self.state / "registry").exists())

    def test_existing_import_is_reused_without_persist(self) -> None:
        (self.state / "registry").mkdir(parents=True)
        (self.state / "registry/already-imported").write_text("keep")
        self.env["STUB_MODE"] = "fail"
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.state / "registry/already-imported").read_text(), "keep"
        )


if __name__ == "__main__":
    unittest.main()
