#!/usr/bin/env python3
"""Focused tests for the deterministic sensor bundle builder."""

from __future__ import annotations

import os
import subprocess
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts/prepare_sensor_bundle.py"


class SensorBundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="guacamole-bundle-test-")
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        (self.source / "sensors/config").mkdir(parents=True)
        (self.source / "sensors/config/a.json").write_text('{"a":1}\n')
        (self.source / "sensors/config/b.json").write_text('{"b":2}\n')
        (self.source / "sensors/sns_reg.conf").write_text("version=9\n")
        (self.source / "sdsp").mkdir()
        (self.source / "sdsp/libtest.so").write_text("library\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, args: list[str], env: dict[str, str] | None = None):
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            env=env or os.environ.copy(),
            text=True,
            capture_output=True,
        )

    def test_bundle_is_reproducible_and_normalised(self) -> None:
        first = self.root / "first.tar.gz"
        second = self.root / "second.tar.gz"
        for output in (first, second):
            result = self._run(
                ["--source", str(self.source), "--output", str(output)]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("4 files", result.stdout)
        self.assertEqual(first.read_bytes(), second.read_bytes())

        with tarfile.open(first, "r:gz") as tar:
            members = {m.name: m for m in tar.getmembers()}
            self.assertIn("guacamole-sensors-20261102", members)
            self.assertIn(
                "guacamole-sensors-20261102/sensors/config/a.json", members
            )
            self.assertIn("guacamole-sensors-20261102/sdsp/libtest.so", members)
            self.assertNotIn(
                "guacamole-sensors-20261102/sensors/registry", members
            )
            for member in members.values():
                self.assertEqual(member.uid, 0)
                self.assertEqual(member.gid, 0)
                self.assertEqual(member.mtime, 0)
                self.assertEqual(member.uname, "")
                self.assertEqual(member.gname, "")
                if member.isdir():
                    self.assertEqual(member.mode, 0o755)
                else:
                    self.assertEqual(member.mode, 0o644)
            extracted = tar.extractfile(
                "guacamole-sensors-20261102/sensors/sns_reg.conf"
            )
            assert extracted is not None
            self.assertEqual(extracted.read(), b"version=9\n")

    def test_missing_config_fails(self) -> None:
        for child in (self.source / "sensors/config").iterdir():
            child.unlink()
        (self.source / "sensors/config").rmdir()
        result = self._run(
            [
                "--source",
                str(self.source),
                "--output",
                str(self.root / "out.tar.gz"),
            ]
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sensors/config is missing", result.stderr)

    def test_extracts_from_ext4_images_with_debugfs(self) -> None:
        bindir = self.root / "bin"
        bindir.mkdir()
        stub = bindir / "debugfs"
        stub.write_text(
            textwrap.dedent(
                """\
                #!/bin/sh
                request=$2
                dest=${request##* }
                case "$request" in
                    "rdump /etc/sensors/config "*)
                        mkdir -p "$dest/config"
                        printf '{}\\n' > "$dest/config/from-image.json"
                        ;;
                    "dump /etc/sensors/sns_reg_config "*)
                        mkdir -p "$(dirname "$dest")"
                        printf 'version=9\\n' > "$dest"
                        ;;
                    "rdump /sdsp "*)
                        mkdir -p "$dest/sdsp"
                        printf 'lib\\n' > "$dest/sdsp/from-image.so"
                        ;;
                    *) exit 2 ;;
                esac
                """
            )
        )
        stub.chmod(0o755)
        vendor = self.root / "vendor.img"
        dsp = self.root / "dsp.img"
        vendor.write_bytes(b"vendor")
        dsp.write_bytes(b"dsp")
        output = self.root / "from-images.tar.gz"
        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        result = self._run(
            [
                "--vendor-img",
                str(vendor),
                "--dsp-img",
                str(dsp),
                "--output",
                str(output),
            ],
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(output, "r:gz") as tar:
            names = set(tar.getnames())
        self.assertIn(
            "guacamole-sensors-20261102/sensors/config/from-image.json", names
        )
        self.assertIn("guacamole-sensors-20261102/sdsp/from-image.so", names)


if __name__ == "__main__":
    unittest.main()
