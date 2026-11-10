"""Check that the user-run builder stops before install when packaging fails."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.geteuid() == 0, "builder intentionally refuses root")
class RootfsBuildScriptTests(unittest.TestCase):
    def test_new_work_directory_is_initialized_before_packaging(self):
        self.run_builder()

    def test_existing_work_directory_is_reused(self):
        self.run_builder(version="8\n")

    def test_incompatible_work_directory_is_rejected(self):
        self.run_builder(version="7\n", expected_status=1)

    def test_unversioned_nonempty_work_directory_is_rejected(self):
        self.run_builder(unversioned=True, expected_status=1)

    def run_builder(self, version=None, unversioned=False, expected_status=42):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "checkout with spaces"
            script = root / "scripts/rootfs_prepare_7_0.sh"
            script.parent.mkdir(parents=True)
            shutil.copy2(ROOT / "scripts/rootfs_prepare_7_0.sh", script)
            recipe = root / "device/testing/7.0/postmarketos-mkinitfs-hook-netboot"
            recipe.mkdir(parents=True)
            (recipe / "netboot.sh").write_text("NBD_PORT=10000\n")
            link = root / ".work/7.0/pmaports/main/postmarketos-mkinitfs-hook-netboot"
            link.parent.mkdir(parents=True)
            link.symlink_to(recipe, target_is_directory=True)
            (root / ".work/7.0/build-src").mkdir(parents=True)
            work = root / ".work/7.0/pmbootstrap-work"
            if version is not None or unversioned:
                work.mkdir()
                if version is not None:
                    (work / "version").write_text(version)
                else:
                    (work / "existing-data").write_text("preserve me")
            binaries = root / "bin"
            binaries.mkdir()
            calls = root / "calls.txt"
            mock = binaries / "python3"
            mock.write_text(
                '#!/usr/bin/env bash\n'
                'printf "%s\\n" "$PWD" "$@" >> "$BUILD_TEST_CALLS"\n'
                'exit 42\n'
            )
            mock.chmod(0o755)
            result = subprocess.run(
                ["bash", str(script)],
                cwd=directory,
                env={**os.environ, "PATH": f"{binaries}:{os.environ['PATH']}",
                     "BUILD_TEST_CALLS": str(calls)},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, expected_status, result.stdout + result.stderr)
            if expected_status == 1:
                self.assertFalse(calls.exists(), "must reject before invoking pmbootstrap")
                if version is not None:
                    self.assertEqual((work / "version").read_text(), version)
                else:
                    self.assertFalse((work / "version").exists())
                    self.assertEqual((work / "existing-data").read_text(), "preserve me")
                return
            self.assertEqual((work / "version").read_text(), "8\n")
            self.assertTrue((work / "cache_git").is_dir())
            arguments = calls.read_text().splitlines()
            self.assertEqual(arguments[0], str(root / ".work/7.0/build-src"))
            self.assertEqual(arguments[1], str(root / ".work/7.0/pmbootstrap/pmbootstrap.py"))
            self.assertEqual(arguments[-3:], ["build", "--envkernel", "linux-postmarketos-qcom-sm8150"])
            self.assertEqual(arguments.count("build"), 1)
            self.assertNotIn("install", arguments)
            logs = list((root / ".work/7.0/rootfs").glob("build-*.log"))
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].read_text().count("Build failed"), 1)
