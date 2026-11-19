#!/usr/bin/env python3
"""Rootless source checks for the rc3 kernel/device integration.

These tests only read files in the checkout and write to a temporary
directory.  They do not build a kernel, package, image or service, and they do
not touch the phone.  Passing them means the recipe, config, device-tree
patches and package wiring are internally consistent; it does not mean the
kernel compiles or the hardware works.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import shutil
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
RC3 = REPO / "device" / "testing" / "rc3" / "linux-postmarketos-qcom-sm8150"
REAR = REPO / "device" / "testing" / "rear-camera" / "linux-postmarketos-qcom-sm8150"
DEVICE = REPO / "device" / "testing" / "device-oneplus-guacamole"
CLOCK = REPO / "device" / "testing" / "nura-clock"
BOOT_HELPER = REPO / "scripts" / "collect_boot_diagnostics.sh"

TAR_SHA512 = (
    "c1bdb11a9ad8be826ec5781e239c60ad92d6bb3a2d6b0adff8ef1f1e334bc688"
    "9ab91683257691ba3155f976011cd37969be379d509c9f928f4f85753e95635e"
)
# 2026-11-10T00:00:00Z: at or before the rc3 source checkout commit.
CLOCK_FLOOR = 1_794_268_800
CLOCK_CEILING = 1_798_761_600  # 2027-01-01T00:00:00Z, a sanity upper bound.


def _raw_block(text: str, key: str) -> str:
    start = text.index(key + '="') + len(key) + 2
    end = text.index('"', start)
    return text[start:end]


def _block(text: str, key: str) -> list[str]:
    """Return the whitespace-separated entries of an APKBUILD list."""
    return _raw_block(text, key).split()


def _sums(text: str) -> dict[str, str]:
    return {
        line.split()[1]: line.split()[0]
        for line in _raw_block(text, "sha512sums").strip().splitlines()
    }


def _expand(name: str) -> str:
    return (
        name.replace("$pkgname", "device-oneplus-guacamole")
        .replace("$_sensors", "guacamole-sensors-20261102")
        .replace("$_tag", "v7.0.0-sm8150")
        .replace("$_flavor", "postmarketos-qcom-sm8150")
        .replace("$arch", "aarch64")
    )


def _check_sums(test: unittest.TestCase, recipe: pathlib.Path) -> None:
    text = (recipe / "APKBUILD").read_text()
    sums = _sums(text)
    for raw in _block(text, "source"):
        if "::" in raw:
            # Remote archive with an explicit URL; abuild fetches it.
            continue
        name = _expand(raw.split("::", 1)[0])
        path = recipe / name
        test.assertTrue(path.is_file(), f"{path} is listed in source= but missing")
        test.assertIn(name, sums, f"{name} has no sha512 entry")
        actual = hashlib.sha512(path.read_bytes()).hexdigest()
        test.assertEqual(sums[name], actual, f"sha512 mismatch for {name}")


class KernelRecipeTests(unittest.TestCase):
    def test_release_and_rc3_config(self) -> None:
        text = (RC3 / "APKBUILD").read_text()
        config = (RC3 / "config-postmarketos-qcom-sm8150.aarch64").read_text()

        self.assertIn("pkgver=7.0.0", text)
        self.assertRegex(text, r"(?m)^pkgrel=4$")
        self.assertIn('CONFIG_LOCALVERSION="-sm8150-rc3"', config)
        self.assertNotIn("rear1", config.split("CONFIG_LOCALVERSION_AUTO", 1)[0])

        # AW8697 haptics: in-tree module, selected by the recipe.
        self.assertIn("CONFIG_INPUT_AW8697=m", config)
        self.assertNotIn("CONFIG_INPUT_AW8697=y", config)

        # The vendor RTC counter has no Unix offset: HCTOSYS must stay off.
        self.assertIn("# CONFIG_RTC_HCTOSYS is not set", config)
        self.assertNotRegex(config, r"(?m)^CONFIG_RTC_HCTOSYS=y$")
        self.assertNotRegex(config, r"(?m)^CONFIG_RTC_HCTOSYS_DEVICE=")

        # Alarms stay available: driver, class, alarm-capable /dev/rtc and the
        # NTP->RTC path (which fails with -ENODEV on this DT) are retained.
        for retained in (
            "CONFIG_RTC_CLASS=y",
            "CONFIG_RTC_INTF_DEV=y",
            "CONFIG_RTC_SYSTOHC=y",
            'CONFIG_RTC_SYSTOHC_DEVICE="rtc0"',
            "CONFIG_RTC_DRV_PM8XXX=m",
        ):
            self.assertIn(retained, config)

    def test_sources_checksums_and_patch_series(self) -> None:
        _check_sums(self, RC3)
        text = (RC3 / "APKBUILD").read_text()
        sums = _sums(text)
        self.assertEqual(TAR_SHA512, sums["linux-v7.0.0-sm8150.tar.gz"])

        patches = [n for n in _block(text, "source") if n.endswith(".patch")]
        numbers = [int(p.split("-", 1)[0]) for p in patches]
        self.assertEqual(len(numbers), len(set(numbers)), "duplicate patch number")
        self.assertEqual(numbers, sorted(numbers), "patch source order is not ascending")
        self.assertEqual(
            patches[-4:],
            [
                "0038-arm64-dts-guacamole-rear-flash-bringup.patch",
                "0039-input-aw8697-kconfig-makefile.patch",
                "0040-input-aw8697-haptics.patch",
                "0041-arm64-dts-guacamole-haptics-alert-slider.patch",
            ],
        )
        # rc3 must build on the reviewed rc2 series, byte for byte.
        for name in patches:
            if int(name.split("-", 1)[0]) <= 37:
                rear = REAR / name
                self.assertTrue(rear.is_file(), f"missing rc2 baseline patch {name}")
                self.assertEqual(
                    rear.read_bytes(), (RC3 / name).read_bytes(),
                    f"{name} differs from the rc2 baseline",
                )

    def test_torch_patch_keeps_the_conservative_70ma_bringup(self) -> None:
        patch_name = "0038-arm64-dts-guacamole-rear-flash-bringup.patch"
        patch = (RC3 / patch_name).read_text()
        props = re.findall(
            r"(?m)^\+\s*(led-max-microamp|flash-max-microamp|flash-max-timeout-us|led-sources)\s*=\s*(<[^>]+>);",
            patch,
        )
        self.assertIn(("led-max-microamp", "<70000>"), props)
        self.assertIn(("led-sources", "<1 2>"), props)
        self.assertFalse([p for p in props if p[0].startswith("flash-")])
        self.assertNotIn(("led-max-microamp", "<600000>"), props)

    def test_haptics_and_slider_patches(self) -> None:
        kconfig = (RC3 / "0039-input-aw8697-kconfig-makefile.patch").read_text()
        self.assertIn("+config INPUT_AW8697", kconfig)
        self.assertIn("+obj-$(CONFIG_INPUT_AW8697)", kconfig)

        driver = (RC3 / "0040-input-aw8697-haptics.patch").read_text()
        self.assertIn("+++ b/drivers/input/misc/aw8697_haptics.c", driver)
        self.assertIn('{ .compatible = "awinic,aw8697" },', driver)
        self.assertIn('input->name = "Awinic AW8697 haptics";', driver)
        self.assertIn("input_set_capability(haptics->input, EV_FF, FF_RUMBLE);", driver)

        dts = (RC3 / "0041-arm64-dts-guacamole-haptics-alert-slider.patch").read_text()
        for needle in (
            "&i2c7 {",
            "clock-frequency = <400000>;",
            'compatible = "awinic,aw8697";',
            "reg = <0x5a>;",
            "reset-gpios = <&tlmm 116 GPIO_ACTIVE_LOW>;",
            'label = "Alert slider";',
            "linux,code = <ABS_SND_PROFILE>;",
            "linux,input-value = <SND_PROFILE_SILENT>;",
            "linux,input-value = <SND_PROFILE_VIBRATE>;",
            "linux,input-value = <SND_PROFILE_RING>;",
            "bias-disable;",
        ):
            self.assertIn(needle, dts)

    def test_rear_camera_baseline_is_untouched(self) -> None:
        rear_config = (REAR / "config-postmarketos-qcom-sm8150.aarch64").read_text()
        self.assertIn('CONFIG_LOCALVERSION="-sm8150-rear1"', rear_config)
        self.assertIn("CONFIG_RTC_HCTOSYS=y", rear_config)
        self.assertNotRegex(rear_config, r"(?m)^CONFIG_INPUT_AW8697=")
        self.assertFalse((REAR / "0038-arm64-dts-guacamole-rear-flash-bringup.patch").exists())
        self.assertFalse((REAR / "0041-arm64-dts-guacamole-haptics-alert-slider.patch").exists())


class ClockMaintainerTests(unittest.TestCase):
    def test_local_recipe_has_valid_maintainer_metadata(self) -> None:
        text = (CLOCK / "APKBUILD").read_text()
        self.assertRegex(text, r'(?m)^maintainer="[^"<>]+ <[^ <>@]+@[^ <>@]+\.[^ <>@]+>"$')


class DevicePackageTests(unittest.TestCase):
    def test_sources_and_checksums(self) -> None:
        _check_sums(self, DEVICE)

    def test_apkbuild_installs_haptics_and_boot_diagnostics(self) -> None:
        text = (DEVICE / "APKBUILD").read_text()
        source = _block(text, "source")
        self.assertIn("72-guacamole-haptics.rules", source)
        for name in (
            "nura-boot-diagnostics",
            "nura-boot-diagnostics.service",
            "nura-boot-diagnostics.timer",
            "81-nura-boot-diagnostics.preset",
        ):
            self.assertIn(name, source)
        for needle in (
            '/usr/lib/udev/rules.d/72-guacamole-haptics.rules',
            '/usr/bin/nura-boot-diagnostics',
            '/usr/lib/systemd/system/nura-boot-diagnostics.service',
            '/usr/lib/systemd/system/nura-boot-diagnostics.timer',
            '/usr/lib/systemd/system-preset/81-nura-boot-diagnostics.preset',
        ):
            self.assertIn(needle, text)
        self.assertRegex(text, r"(?m)^pkgrel=(?:1[4-9]|[2-9][0-9])$")
        self.assertIn("\tnura-clock\n", text)

    def test_boot_helper_is_a_byte_identical_copy(self) -> None:
        helper = DEVICE / "nura-boot-diagnostics"
        self.assertEqual(BOOT_HELPER.read_bytes(), helper.read_bytes())
        self.assertIn('ExecStart=/usr/bin/nura-boot-diagnostics',
                      (DEVICE / "nura-boot-diagnostics.service").read_text())

    def test_udev_rule_matches_reviewed_input_names(self) -> None:
        rule = (DEVICE / "72-guacamole-haptics.rules").read_text()
        self.assertIn('ATTRS{name}=="Awinic AW8697 haptics"', rule)
        self.assertIn('ENV{FEEDBACKD_TYPE}="vibra"', rule)
        self.assertIn('ATTRS{name}=="Alert slider"', rule)
        self.assertIn('ENV{FEEDBACKD_TYPE}="alert-slider"', rule)
        self.assertIn('TAG+="uaccess"', rule)
        # feedbackd's uaccess ACL is applied by 73-seat-late.rules.
        self.assertTrue((DEVICE / "72-guacamole-haptics.rules").name < "73-seat-late.rules")


class ClockPackageTests(unittest.TestCase):
    def test_ships_only_the_epoch_floor(self) -> None:
        _check_sums(self, CLOCK)
        text = (CLOCK / "APKBUILD").read_text()
        self.assertIn("_rtc_guard=0", text)
        self.assertIn('source="clock-epoch"', text)
        self.assertIn('"$pkgdir/usr/lib/clock-epoch"', text)
        self.assertNotIn('install -Dm644 "$srcdir/nura-rtc.conf"', text)
        self.assertNotIn('"$pkgdir/etc/modprobe.d', text)
        marker = (CLOCK / "clock-epoch").read_text().strip()
        self.assertRegex(marker, r"^[0-9]{10}$")
        self.assertEqual(str(CLOCK_FLOOR), marker)

    def test_clock_floor_is_an_explicit_build_lower_bound(self) -> None:
        self.assertEqual(1_794_268_800, CLOCK_FLOOR)
        payload = CLOCK / "clock-epoch"
        recipe = CLOCK / "APKBUILD"
        self.assertEqual(str(CLOCK_FLOOR), payload.read_text().strip())
        self.assertGreaterEqual(int(payload.stat().st_mtime), CLOCK_FLOOR)
        self.assertGreaterEqual(int(recipe.stat().st_mtime), CLOCK_FLOOR)

        def pid1_epoch(clock_epoch_mtime: int, timesync_mtime: int = 0) -> int:
            """systemd(1) SYSTEM CLOCK EPOCH: max of the known stamps."""
            return max(0, clock_epoch_mtime, timesync_mtime)

        # abuild stamps payload mtimes from SOURCE_DATE_EPOCH; model both the
        # source mtime and the APKBUILD-derived fallback.
        for stamp in (int(payload.stat().st_mtime), int(recipe.stat().st_mtime)):
            epoch = pid1_epoch(stamp)
            self.assertGreaterEqual(epoch, CLOCK_FLOOR)
            self.assertLessEqual(epoch, CLOCK_CEILING)
        self.assertEqual(pid1_epoch(0), 0, "a 1970/zero stamp must stay rejected")
        self.assertLess(pid1_epoch(0), CLOCK_FLOOR)

        # A fake install must preserve the stamp and never write a clock or RTC.
        with tempfile.TemporaryDirectory() as tmp:
            installed = pathlib.Path(tmp) / "usr/lib/clock-epoch"
            installed.parent.mkdir(parents=True)
            shutil.copyfile(payload, installed)
            installed.touch()
            stamp = int(installed.stat().st_mtime)
            self.assertGreaterEqual(stamp, CLOCK_FLOOR)
            self.assertLessEqual(stamp, CLOCK_CEILING)
        for forbidden in ("hwclock", "date -s", "settimeofday", "clock_settime",
                          "rtcwake", "/dev/rtc"):
            self.assertNotIn(forbidden, (CLOCK / "APKBUILD").read_text())


if __name__ == "__main__":
    unittest.main()
