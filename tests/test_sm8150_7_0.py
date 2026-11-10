#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Focused checks for the isolated Nura v7.0.0-sm8150 kernel package.

These checks are host-only.  Build-artifact checks skip cleanly when the
worktree has not produced the 7.0 kernel yet; recipe and source checks always
run.  They are intentionally about 7.0 paths, not the discarded 7.3.3
experiment.
"""

from __future__ import annotations

import gzip
import hashlib
import re
import struct
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = ROOT / "device/testing/7.0/linux-postmarketos-qcom-sm8150"
APKBUILD = RECIPE / "APKBUILD"
CONFIG = RECIPE / "config-postmarketos-qcom-sm8150.aarch64"
SOURCE_TARBALL = ROOT / ".work/7.0/linux-v7.0.0-sm8150.tar.gz"
KERNEL_PACKAGE = ROOT / "build/7.0/kernel-package"
KERNEL_RELEASE = "7.0.0-sm8150"
DTB = KERNEL_PACKAGE / "boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
DTC = ROOT / "local/bin/dtc"
FDTGET = ROOT / "local/bin/fdtget"
BOOT_SCRIPT = ROOT / "scripts/build_bootimg_7_0.sh"


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
        elif line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def recipe_sources() -> list[str]:
    # APKBUILD source entries use $_tag/$_flavor/$arch; let the shell expand
    # them exactly as abuild does.
    result = subprocess.run(
        ["sh", "-c", '. "$1"; printf "%s\\n" $source', "sh", str(APKBUILD)],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def recipe_checksums() -> dict[str, str]:
    text = APKBUILD.read_text()
    block = re.search(r'^sha512sums="\n(.*?)^"$', text, re.M | re.S)
    if not block:
        raise AssertionError("sha512sums block not found")
    return dict(
        (name, digest)
        for digest, name in re.findall(r"^([0-9a-f]{128})  (\S+)$", block.group(1), re.M)
    )


class RecipeTests(unittest.TestCase):
    def test_isolated_7_0_recipe(self) -> None:
        text = APKBUILD.read_text()
        self.assertIn("pkgver=7.0.0", text)
        self.assertIn("pkgrel=1", text)
        self.assertIn("Nura-local v7.0.0-sm8150 port", text)
        self.assertNotIn("7.3.3", text)
        self.assertNotIn("linux-v6.17.0-sm8150.tar.gz", text)
        self.assertNotIn("0001-lid-switch-fix.patch", text)

    def test_source_list_has_no_dropped_or_duplicate_patches(self) -> None:
        sources = recipe_sources()
        names = {Path(source).name for source in sources if source.endswith(".patch")}
        for dropped in (
            "0001-lid-switch-fix.patch",
            "0003-fix-llvm-build.patch",
            "0013-net-ipa-add-ipa-v4.1-platform-data.patch",
            "0014-net-ipa-add-ipa-v4.1-register-definitions.patch",
            "0016-net-ipa-v4.1-endp-init-ctrl.patch",
            "0023-usb-typec-qcom-pmic-typec-log-port-status-on-vbus-irq.patch",
        ):
            self.assertNotIn(dropped, names)
        self.assertEqual(len(names), len([s for s in sources if s.endswith(".patch")]))

    def test_checksums_match_files(self) -> None:
        sums = recipe_checksums()
        for source in recipe_sources():
            name = source.split("::", 1)[0]
            path = SOURCE_TARBALL if name.startswith("linux-v7.0.0") else RECIPE / name
            with self.subTest(source=name):
                self.assertIn(name, sums)
                if not path.is_file():
                    self.skipTest(f"input not staged: {path}")
                self.assertEqual(sums[name], sha512(path))


class ConfigTests(unittest.TestCase):
    REQUIRED = {
        "CONFIG_ARM64": "y",
        "CONFIG_LOCALVERSION": '"-sm8150"',
        "CONFIG_LOCALVERSION_AUTO": "n",
        "CONFIG_DRM_MSM": "y",
        "CONFIG_DRM_PANEL_SAMSUNG_ONEPLUS_DSC": "y",
        "CONFIG_TOUCHSCREEN_S6SY761": "m",
        "CONFIG_SND_SOC_SM8150": "m",
        "CONFIG_SND_SOC_TFA9874": "m",
        "CONFIG_SND_SOC_WCD934X": "m",
        "CONFIG_SLIMBUS": "y",
        "CONFIG_CHARGER_QCOM_SMB2": "y",
        "CONFIG_BATTERY_BQ27XXX": "y",
        "CONFIG_BATTERY_BQ27XXX_I2C": "y",
        "CONFIG_BATTERY_BQ27XXX_DT_UPDATES_NVM": "n",
        "CONFIG_TYPEC_QCOM_PMIC": "y",
        "CONFIG_TYPEC_TCPM": "y",
        "CONFIG_REGULATOR_QCOM_USB_VBUS": "y",
        "CONFIG_USB_ROLE_SWITCH": "y",
        "CONFIG_QCOM_IPA": "m",
        "CONFIG_RMNET": "m",
        "CONFIG_QCOM_FASTRPC": "m",
        "CONFIG_QCOM_PD_MAPPER": "m",
        "CONFIG_QCOM_Q6V5_PAS": "m",
        "CONFIG_ATH10K_SNOC": "m",
        "CONFIG_BT_HCIUART_QCA": "y",
        "CONFIG_SCSI_UFSHCD": "y",
        "CONFIG_BLK_DEV_NBD": "m",
        "CONFIG_USB_CONFIGFS_NCM": "y",
        "CONFIG_USB_F_NCM": "y",
        "CONFIG_QCOM_SCM": "y",
        "CONFIG_PSTORE_RAM": "y",
        "CONFIG_KALLSYMS": "y",
        "CONFIG_KALLSYMS_ALL": "y",
    }

    def test_required_options(self) -> None:
        values = config_values(CONFIG)
        for key, expected in self.REQUIRED.items():
            with self.subTest(key=key):
                self.assertEqual(values.get(key), expected)

    def test_built_config_matches_recipe_if_present(self) -> None:
        built = ROOT / ".work/7.0/build-7.0-clean/.config"
        if not built.is_file():
            self.skipTest("7.0 build config not present")
        self.assertEqual(config_values(built), config_values(CONFIG))

    def test_module_compression_matches_staging(self) -> None:
        values = config_values(CONFIG)
        self.assertEqual(values.get("CONFIG_MODULE_COMPRESS_ZSTD"), "y")
        self.assertEqual(values.get("CONFIG_MODULE_COMPRESS_XZ"), "n")


class BootScriptTests(unittest.TestCase):
    def test_script_uses_measured_offset_and_envelope_guard(self) -> None:
        text = BOOT_SCRIPT.read_text()
        self.assertIn('DTB_OFFSET="${DTB_OFFSET:-0x03000000}"', text)
        self.assertIn("check_boot_envelope.py", text)
        self.assertIn("INITRAMFS is not set", text)
        self.assertIn("check_initramfs_release.py", text)
        self.assertIn('--release "${KERNEL_RELEASE}"', text)
        self.assertNotIn("/tmp/device_initramfs_r4", text)
        self.assertNotIn("/home/emily", text)
        self.assertNotIn("sudo", text)

    def test_script_has_no_phone_actions(self) -> None:
        text = BOOT_SCRIPT.read_text()
        for forbidden in ("fastboot", "adb ", "ssh ", "reboot"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


class KernelArtifactTests(unittest.TestCase):
    def test_release_is_exact(self) -> None:
        release_file = KERNEL_PACKAGE / "usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
        if not release_file.is_file():
            self.skipTest("7.0 kernel package not staged")
        self.assertEqual(release_file.read_text().strip(), KERNEL_RELEASE)

    def test_image_is_arm64_and_large_enough_to_need_relocation(self) -> None:
        if not (KERNEL_PACKAGE / "boot/vmlinuz").is_file():
            self.skipTest("7.0 kernel package not staged")
        image = gzip.decompress((KERNEL_PACKAGE / "boot/vmlinuz").read_bytes())
        self.assertEqual(image[0x38:0x3C], b"ARMd")
        header_size = struct.unpack_from("<Q", image, 16)[0]
        extent = max(header_size, len(image))
        self.assertGreater(0x8000 + extent, 0x01F00000)
        self.assertGreater(0x8000 + extent, 0x02400000)
        self.assertLessEqual(0x8000 + extent, 0x03000000)

    def test_module_tree_is_7_0_only(self) -> None:
        modules = list((KERNEL_PACKAGE / "usr/lib/modules").rglob("*.ko*"))
        if not modules:
            self.skipTest("7.0 modules not staged")
        self.assertGreater(len(modules), 800)
        self.assertFalse(list((KERNEL_PACKAGE / "usr/lib/modules").rglob("*6.17*")))
        for path in modules:
            with self.subTest(path=str(path)):
                self.assertIn(KERNEL_RELEASE, str(path))
        sample = (
            KERNEL_PACKAGE
            / "usr/lib/modules/7.0.0-sm8150/kernel/sound/soc/codecs/snd-soc-tfa9874.ko.zst"
        )
        self.assertTrue(sample.is_file())
        info = subprocess.run(
            ["modinfo", "-F", "vermagic", str(sample)], capture_output=True, text=True
        )
        self.assertEqual(info.returncode, 0, info.stderr)
        self.assertTrue(info.stdout.strip().startswith(KERNEL_RELEASE))
        self.assertIn("aarch64", info.stdout)

    def test_staging_has_no_build_symlinks(self) -> None:
        modules = KERNEL_PACKAGE / "usr/lib/modules/7.0.0-sm8150"
        if not modules.is_dir():
            self.skipTest("7.0 modules not staged")
        self.assertFalse((modules / "build").exists())
        self.assertFalse((modules / "source").exists())


class DeviceTreeTests(unittest.TestCase):
    REQUIRED_COMPATIBLES = (
        "samsung,oneplus-dsc",
        "samsung,s6sy761",
        "nxp,tfa9874",
        "qcom,pm8150b-charger",
        "qcom,sm8150-ipa",
        "qcom,sm8150-sndcard",
        "qcom,sm8150-adsp-pas",
        "qcom,adreno-640.1",
        "ti,bq27411",
        "qcom,pm8150b-typec",
        "qcom,sm8150-ufshc",
        "qcom,wcn3990-wifi",
        "qcom,wcn3990-bt",
    )

    def setUp(self) -> None:
        if not DTB.is_file() or not DTC.is_file():
            self.skipTest("7.0 DTB or local dtc not staged")
        env = {"LD_LIBRARY_PATH": str(ROOT / "local/lib")}
        self.dts = subprocess.run(
            [str(DTC), "-I", "dtb", "-O", "dts", str(DTB)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout

    def test_required_nodes_and_ids(self) -> None:
        for compatible in self.REQUIRED_COMPATIBLES:
            with self.subTest(compatible=compatible):
                self.assertIn(f'compatible = "{compatible}"', self.dts)
        self.assertIn("qcom,msm-id = <0x153 0x20000>", self.dts)
        self.assertIn("qcom,board-id = <0x08 0x00 0x00 0x00>", self.dts)
        self.assertIn("qcom,dload-mode", self.dts)
        self.assertIn("ramoops", self.dts)
        self.assertIn("fastrpc-shared-pool", self.dts)

    def test_gpu_adsp_and_wifi_are_enabled(self) -> None:
        for pattern in (
            r"gpu@2c00000 \{(.*?)\n\t\t\};",
            r"gmu@2c6a000 \{(.*?)\n\t\t\};",
            r"remoteproc@17300000 \{(.*?)\n\t\t\};",
            r"wifi@18800000 \{(.*?)\n\t\t\};",
        ):
            with self.subTest(pattern=pattern):
                match = re.search(pattern, self.dts, re.S)
                self.assertIsNotNone(match)
                self.assertIn('status = "okay"', match.group(1))
        self.assertIn('firmware-name = "qcom/sm8150/oneplus7/adsp.mdt"', self.dts)
        self.assertIn('firmware-name = "qcom/a640_zap.mdt"', self.dts)
        for supply in (
            "vdd-0.8-cx-mx-supply",
            "vdd-1.8-xo-supply",
            "vdd-1.3-rfa-supply",
            "vdd-3.3-ch0-supply",
        ):
            with self.subTest(supply=supply):
                self.assertIn(supply, self.dts)

    def test_wcd9340_supplies_and_reset(self) -> None:
        block = re.search(r"codec@1,0 \{(.*?)swm@", self.dts, re.S)
        self.assertIsNotNone(block)
        body = block.group(1)
        self.assertIn("reset-gpios", body)
        for supply in (
            "vdd-buck-supply",
            "vdd-buck-sido-supply",
            "vdd-tx-supply",
            "vdd-rx-supply",
            "vdd-io-supply",
        ):
            with self.subTest(supply=supply):
                self.assertIn(supply, body)

    def test_stock_reserved_memory_fixes(self) -> None:
        self.assertIn("memory@85e00000", self.dts)
        self.assertIn("reg = <0x00 0x86200000 0x00 0x5500000>", self.dts)
        self.assertIn("reg = <0x00 0x9c000000 0x00 0x2400000>", self.dts)
        self.assertIn("bootloader_log_mem@9fff7000", self.dts)
        self.assertIn("memory@a4c00000", self.dts)
        self.assertIn("memory@ac200000", self.dts)
        self.assertIn("memory@ac300000", self.dts)

    def test_fastrpc_pool_is_below_4_gib(self) -> None:
        block = re.search(
            r"fastrpc-shared-pool \{(.*?)\n\t\t\};", self.dts, re.S
        )
        self.assertIsNotNone(block)
        self.assertIn("alloc-ranges = <0x00 0x00 0x00 0xffffffff>", block.group(1))
        self.assertIn("size = <0x00 0x1000000>", block.group(1))

    def test_gauge_has_no_monitored_battery(self) -> None:
        gauge = re.search(
            r"fuel-gauge@55 \{(.*?)\n\t\t\t\};", self.dts, re.S
        )
        self.assertIsNotNone(gauge)
        self.assertIn('compatible = "ti,bq27411"', gauge.group(1))
        self.assertNotIn("monitored-battery", gauge.group(1))
        self.assertIn("power-supplies", gauge.group(1))
        charger = re.search(
            r"charger@1000 \{(.*?)\n\t\t\t\t\};", self.dts, re.S
        )
        self.assertIsNotNone(charger)
        self.assertIn("monitored-battery", charger.group(1))

    def test_usb_connector_is_5v_only(self) -> None:
        connector = re.search(r"connector \{(.*?)\n\t\t\t\t\t\};", self.dts, re.S)
        self.assertIsNotNone(connector)
        block = connector.group(1)
        self.assertIn("source-pdos = <0x26019096>", block)
        self.assertIn("sink-pdos = <0x2601912c>", block)
        self.assertNotIn("0x260190c8", block)  # 9 V PDO
        self.assertNotIn("0x26019190", block)  # 12 V PDO
        self.assertIn("usb-role-switch", self.dts)
        self.assertIn("orientation-switch", self.dts)
        # The connector has both the data-role and orientation ports.
        self.assertGreaterEqual(block.count("remote-endpoint"), 2)


if __name__ == "__main__":
    unittest.main()
