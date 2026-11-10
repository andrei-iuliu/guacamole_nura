#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Focused tests for scripts/check_initramfs_release.py."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_initramfs_release as check  # noqa: E402

RELEASE = "7.0.0-sm8150"


def make_archive(
    directory: Path,
    releases: list[str],
    *,
    compression: str | None = "gzip",
) -> Path:
    source = directory / "root"
    source.mkdir(parents=True, exist_ok=True)
    for release in releases:
        member = source / "lib/modules" / release
        member.mkdir(parents=True, exist_ok=True)
        (member / "marker.txt").write_text(f"{release}\n")
    tag = "-".join(releases) if releases else "none"
    archive = directory / f"initramfs-{tag}.{compression or 'cpio'}"
    command = "find . -print0 | cpio --null -o -H newc"
    if compression == "gzip":
        command += " | gzip -9"
    elif compression == "zstd":
        command += " | zstd -q -9"
    with archive.open("wb") as output:
        subprocess.run(command, shell=True, cwd=source, check=True, stdout=output, stderr=subprocess.DEVNULL)
    return archive


class InitramfsReleaseTests(unittest.TestCase):
    def test_valid_gzip_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE])
            report = check.check_release(archive, RELEASE)
            self.assertEqual(report["module_releases"], [RELEASE])

    def test_valid_raw_cpio_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE], compression=None)
            self.assertEqual(check.check_release(archive, RELEASE)["module_releases"], [RELEASE])

    def test_exported_archive_symlink(self) -> None:
        for compression in ("gzip", "zstd"):
            if shutil.which(compression) is None:
                continue
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                archive = make_archive(directory, [RELEASE], compression=compression)
                exported = directory / "export/initramfs"
                exported.parent.mkdir()
                exported.symlink_to(archive)
                self.assertEqual(check.check_release(exported, RELEASE)["module_releases"], [RELEASE])

    def test_wrong_release_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), ["6.17.0-sm8150"])
            with self.assertRaisesRegex(check.InitramfsError, "do not match exactly"):
                check.check_release(archive, RELEASE)

    def test_mixed_releases_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE, "6.17.0-sm8150"])
            with self.assertRaisesRegex(check.InitramfsError, "do not match exactly"):
                check.check_release(archive, RELEASE)

    def test_release_matching_is_literal(self) -> None:
        entries = ["lib/modules/7x0x0-sm8150/marker"]
        self.assertEqual(check.module_releases(entries), {"7x0x0-sm8150"})
        self.assertNotIn(RELEASE, check.module_releases(entries))

    def test_invalid_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "initramfs"
            archive.write_bytes(b"this is not a cpio archive\n")
            with self.assertRaisesRegex(check.InitramfsError, "cpio could not list"):
                check.check_release(archive, RELEASE)

    def test_truncated_gzip_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE])
            archive.write_bytes(archive.read_bytes()[:-12])
            with self.assertRaisesRegex(check.InitramfsError, "could not decompress"):
                check.check_release(archive, RELEASE)

    def test_unreadable_archive_is_rejected(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read chmod-000 files")
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE])
            archive.chmod(0o000)
            try:
                with self.assertRaisesRegex(
                    check.InitramfsError, "cannot read archive|could not decompress"
                ):
                    check.check_release(archive, RELEASE)
            finally:
                archive.chmod(0o600)

    def test_no_module_entries_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [])
            with self.assertRaisesRegex(check.InitramfsError, "no lib/modules"):
                check.check_release(archive, RELEASE)

    def test_zstd_archive_when_available(self) -> None:
        if shutil.which("zstd") is None:
            self.skipTest("zstd not installed")
        with tempfile.TemporaryDirectory() as temporary:
            archive = make_archive(Path(temporary), [RELEASE], compression="zstd")
            self.assertEqual(check.check_release(archive, RELEASE)["module_releases"], [RELEASE])

    def test_cli_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            good = make_archive(Path(temporary), [RELEASE])
            bad = make_archive(Path(temporary), ["6.17.0-sm8150"])
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(check.main(["--archive", str(good), "--release", RELEASE]), 0)
                self.assertIn("listing-only check", out.getvalue())
                self.assertEqual(check.main(["--archive", str(bad), "--release", RELEASE]), 1)
                self.assertIn("ERROR:", err.getvalue())


class BootScriptFailClosedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernel = ROOT / "build/7.0/kernel-package"
        self.script = ROOT / "scripts/build_bootimg_7_0.sh"
        if not (self.kernel / "boot/vmlinuz").is_file():
            self.skipTest("7.0 kernel staging is not present")
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="test-bootimg-")
        self.addCleanup(self.temporary.cleanup)

    def run_script(self, archive: Path) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(
            OUTPUT_DIR=self.temporary.name,
            KERNEL_DIR=str(self.kernel),
            DTB_MODE="original",
            INITRAMFS=str(archive),
        )
        return subprocess.run(
            [str(self.script)], capture_output=True, text=True, check=False, env=environment
        )

    def test_invalid_truncated_and_wrong_release_fail_closed(self) -> None:
        directory = Path(self.temporary.name)
        invalid = directory / "invalid.bin"
        invalid.write_bytes(b"not a cpio archive\n")
        truncated = directory / "truncated.gz"
        good = make_archive(directory, [RELEASE])
        truncated.write_bytes(good.read_bytes()[:-12])
        wrong = make_archive(directory, ["6.17.0-sm8150"])
        for archive in (invalid, truncated, wrong):
            with self.subTest(archive=archive.name):
                result = self.run_script(archive)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("ERROR:", result.stderr)
                self.assertFalse((directory / "boot.img").exists())

class BuildScriptContractTests(unittest.TestCase):
    def test_boot_script_calls_the_helper_and_has_no_warning_fallback(self) -> None:
        text = (ROOT / "scripts/build_bootimg_7_0.sh").read_text()
        self.assertIn("check_initramfs_release.py", text)
        self.assertIn('--release "${KERNEL_RELEASE}"', text)
        self.assertNotIn("could not list the initramfs as a cpio archive", text)


if __name__ == "__main__":
    unittest.main()
