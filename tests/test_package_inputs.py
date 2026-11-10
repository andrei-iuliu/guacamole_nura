#!/usr/bin/env python3
"""Checks for the package payloads used by the rootfs build.

These tests intentionally do not need a phone, root or network access. They
validate the local recipe inputs that are present in this workspace and skip
remote-only blobs that were not cached.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEVICE_DIR = REPO_ROOT / "device/testing/device-oneplus-guacamole"
FIRMWARE_DIR = REPO_ROOT / "device/testing/firmware-oneplus-guacamole"
SENSOR_TARBALL = DEVICE_DIR / "guacamole-sensors-20261102.tar.gz"
FIRMWARE_TARBALL = FIRMWARE_DIR / "firmware-oneplus-guacamole-24.0-20261011.tar.gz"
REMOTE_DIRS = [
    REPO_ROOT / "ref/firmware/source-files",
    REPO_ROOT / ".work/pmbootstrap/cache_distfiles",
]


def sums(apkbuild: Path) -> dict[str, str]:
    text = apkbuild.read_text()
    match = re.search(r'sha512sums="(.*?)"\n', text, re.S)
    if not match:
        raise AssertionError(f"no sha512sums in {apkbuild}")
    result: dict[str, str] = {}
    for line in match.group(1).strip().splitlines():
        digest, name = line.split()
        result[name] = digest
    return result


def find(name: str, apkbuild_dir: Path) -> Path | None:
    for directory in [apkbuild_dir, *REMOTE_DIRS]:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


class PackageInputTest(unittest.TestCase):
    def test_checksums_follow_abuild_source_order(self) -> None:
        for recipe in (REPO_ROOT / "device/testing").glob("*/APKBUILD"):
            with self.subTest(package=recipe.parent.name):
                result = subprocess.run(
                    ["sh", "-c", '. "$1"; printf "%s\\n" "$source"', "sh", str(recipe)],
                    check=True, capture_output=True, text=True,
                )
                sources = [
                    uri.split("::", 1)[0].rsplit("/", 1)[-1]
                    for uri in result.stdout.split()
                ]
                self.assertEqual(sources, list(sums(recipe)))

    def test_device_recipe_dependencies(self) -> None:
        text = (DEVICE_DIR / "APKBUILD").read_text()
        depends = re.search(r'depends="(.*?)"', text, re.S)
        assert depends is not None
        self.assertIn("python3", depends.group(1).split())

    def test_adsp_firmware_is_packaged(self) -> None:
        text = (FIRMWARE_DIR / "APKBUILD").read_text()
        self.assertIn('_adsp="adsp.mdt adsp.b00', text)
        self.assertIn("for f in $_adsp; do", text)
        checksums = sums(FIRMWARE_DIR / "APKBUILD")
        adsp = [f"adsp.{part}" for part in ["mdt", *[f"b{i:02d}" for i in range(15)]]]
        for name in adsp:
            self.assertIn(name, checksums)
            path = find(name, FIRMWARE_DIR)
            if path is None:
                continue
            self.assertEqual(
                hashlib.sha512(path.read_bytes()).hexdigest(), checksums[name], name
            )

    def test_no_foreign_registry_is_packaged(self) -> None:
        recipe = (DEVICE_DIR / "APKBUILD").read_text()
        script = (DEVICE_DIR / "guacamole-sensors-registry").read_text()
        self.assertNotIn("registry-default", recipe)
        self.assertNotIn("registry-default", script)
        self.assertIn('_sensors="guacamole-sensors-20261102"', recipe)

    def test_local_recipe_inputs_match_their_checksums(self) -> None:
        checked = 0
        for directory in (DEVICE_DIR, FIRMWARE_DIR):
            checksums = sums(directory / "APKBUILD")
            for name, expected in checksums.items():
                path = find(name, directory)
                if path is None:
                    continue
                self.assertEqual(
                    hashlib.sha512(path.read_bytes()).hexdigest(),
                    expected,
                    f"{directory.name}/{name}",
                )
                checked += 1
        self.assertGreater(checked, 0)

    def test_sensor_tarball_is_self_contained(self) -> None:
        if not SENSOR_TARBALL.is_file():
            self.skipTest("sensor tarball has not been generated")
        checksums = sums(DEVICE_DIR / "APKBUILD")
        self.assertEqual(
            hashlib.sha512(SENSOR_TARBALL.read_bytes()).hexdigest(),
            checksums[SENSOR_TARBALL.name],
        )
        import tarfile

        with tarfile.open(SENSOR_TARBALL, "r:gz") as tar:
            names = tar.getnames()
        self.assertTrue(
            all(
                name == "guacamole-sensors-20261102"
                or name.startswith("guacamole-sensors-20261102/")
                for name in names
            )
        )
        self.assertIn("guacamole-sensors-20261102/sensors/sns_reg.conf", names)
        self.assertFalse(any("registry" in name for name in names))

    def test_firmware_tarball_identity(self) -> None:
        if not FIRMWARE_TARBALL.is_file():
            self.skipTest("firmware tarball is not present")
        checksums = sums(FIRMWARE_DIR / "APKBUILD")
        self.assertEqual(
            hashlib.sha512(FIRMWARE_TARBALL.read_bytes()).hexdigest(),
            checksums[FIRMWARE_TARBALL.name],
        )


if __name__ == "__main__":
    unittest.main()
