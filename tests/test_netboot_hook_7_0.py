#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Focused checks for the worktree-local port-10000 netboot hook override."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "device/testing/7.0/postmarketos-mkinitfs-hook-netboot"
APKBUILD = PKG / "APKBUILD"
NETBOOT = PKG / "netboot.sh"
MODULES = PKG / "netboot.modules"
PMAPORTS_HOOK = ROOT / ".work/7.0/pmaports/main/postmarketos-mkinitfs-hook-netboot"
PMBOOTSTRAP = ROOT / ".work/7.0/pmbootstrap/pmbootstrap.py"


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksums() -> dict[str, str]:
    block = re.search(r'^sha512sums="\n(.*?)^"$', APKBUILD.read_text(), re.M | re.S)
    if not block:
        raise AssertionError("sha512sums block not found")
    return dict(
        (name, digest)
        for digest, name in re.findall(r"^([0-9a-f]{128})  (\S+)$", block.group(1), re.M)
    )


class NetbootHookTests(unittest.TestCase):
    def test_package_identity_is_the_local_override(self) -> None:
        text = APKBUILD.read_text()
        self.assertEqual(PKG.name, "postmarketos-mkinitfs-hook-netboot")
        self.assertIn("pkgname=postmarketos-mkinitfs-hook-netboot", text)
        self.assertIn("pkgver=4.2.1", text)
        self.assertIn("pkgrel=3", text)
        self.assertIn("Nura port 10000 override", text)

    def test_hook_uses_port_10000_only(self) -> None:
        text = NETBOOT.read_text()
        self.assertIn("NBD_PORT=10000", text)
        self.assertNotIn("NBD_PORT=9999", text)
        self.assertNotIn("9999", text)

    def test_module_list_and_checksums(self) -> None:
        self.assertEqual(MODULES.read_text().strip(), "nbd")
        sums = checksums()
        for name, path in (("netboot.sh", NETBOOT), ("netboot.modules", MODULES)):
            with self.subTest(name=name):
                self.assertEqual(sums.get(name), sha512(path))

    def test_prepared_pmbootstrap_parses_port_option(self) -> None:
        if not PMBOOTSTRAP.is_file():
            self.skipTest("worktree-local pmbootstrap clone is not prepared")
        help_text = subprocess.run(
            [sys.executable, str(PMBOOTSTRAP), "netboot", "serve", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_text.returncode, 0, help_text.stderr)
        self.assertIn("--port PORT", help_text.stdout)
        self.assertIn("--replace", help_text.stdout)

    def test_prepared_pmbootstrap_parses_documented_install_options(self) -> None:
        if not PMBOOTSTRAP.is_file():
            self.skipTest("worktree-local pmbootstrap clone is not prepared")

        def help_for(*arguments: str) -> str:
            result = subprocess.run(
                [sys.executable, str(PMBOOTSTRAP), *arguments, "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout

        top = help_for()
        self.assertIn("--boot-size", top)
        self.assertIn("--extra-space", top)
        install = help_for("install")
        self.assertIn("--add", install)
        self.assertIn("--no-split", install)
        self.assertIn("--filesystem", install)
        export = help_for("export")
        self.assertIn("--no-install", export)

    def test_pmaports_uses_the_local_override_when_prepared(self) -> None:
        if not PMAPORTS_HOOK.exists():
            self.skipTest("worktree-local pmaports clone is not prepared")
        self.assertTrue(PMAPORTS_HOOK.is_symlink())
        self.assertEqual(PMAPORTS_HOOK.resolve(), PKG.resolve())
        self.assertIn("NBD_PORT=10000", (PMAPORTS_HOOK / "netboot.sh").read_text())


if __name__ == "__main__":
    unittest.main()
