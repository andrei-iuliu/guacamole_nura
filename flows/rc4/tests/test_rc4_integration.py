#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rootless integration tests for the rc4 source candidate.

Covers, without any build, phone, sudo, mount or pmbootstrap:
  * merged source checksums: the recipe's sha512sums match its files, the
    0001-0041 bytes still equal rc3's, and the reviewed 0044-0058 set is
    complete and ordered;
  * the release identity and the isolated paths (everything rc4 writes lives
    under flows/rc4; nothing points at the rc3 kernel state/output);
  * untouched rc3 flow hashes: the rc3 recipe, the reviewed local-storage
    scripts and the rc3 device recipe still match tests/rc3-baseline.sha256;
  * every deliberate feature gate (MSS unit installed-but-disabled, suspend
    policy off, DP audio link disabled while DP video is enabled, front sensor
    AND CAMSS endpoint absent together with the motor disabled, fingerprint DT
    disabled with allow_power defaulting to false, qcomtee blacklisted at the
    kernel cmdline);
  * on the compiled combined DTB: exactly the three bindable CAMSS async
    connections rc3 has, and exactly 11 available ALSA links;
  * the local-storage flow keeps its guards (no NBD local root, active-mount
    refusal, flock, explicit public key).

Run: python3 -m unittest discover -s flows/rc4/tests -p 'test_*.py' -v
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
REPO = RC4.parents[1]
sys.path.insert(0, str(RC4 / "scripts"))

import dt_facts  # noqa: E402  (same-directory helper)

RECIPE = RC4 / "device/testing/linux-postmarketos-qcom-sm8150"
DEVICE = RC4 / "device/testing/device-oneplus-guacamole"
FLOW = RC4 / "local-storage"
RC3_RECIPE = REPO / "device/testing/rc3/linux-postmarketos-qcom-sm8150"
KERNEL_OUT = RC4 / "build-source/kernel-output"
# `rc4.sh kernel` writes the full staging under kernel-output.  The review
# evidence build (scripts/ and objects only, no kernel package) lives under
# build-source/syntax-check and is accepted as a fallback so the DT gates can
# be re-checked without a full build.
DTB_DIRS = [KERNEL_OUT / "arch/arm64/boot/dts/qcom",
            RC4 / "build-source/artifacts/dtb"]
RELEASE = "7.0.0-sm8150-guacamole-rc4"

NEW_PATCHES = [
    "0044-soc-qcom-guacamole-mss-vote.patch",
    "0045-ath10k-snoc-wmi-key-ack.patch",
    "0046-asoc-q6afe-dai-displayport-rx-input-widget.patch",
    "0047-asoc-sm8150-accept-displayport-backend.patch",
    "0048-asoc-sm8150-prepare-dp-codec-dai-before-afe-start.patch",
    "0049-asoc-qdsp6-q6afe-split-port-config-and-start-errors.patch",
    "0050-asoc-qdsp6-q6afe-send-dp-stream-and-controller-index.patch",
    "0051-input-misc-add-goodix-fp.patch",
    "0052-iio-magnetometer-add-mxm1120.patch",
    "0053-media-i2c-add-imx471.patch",
    "0054-media-qcom-add-oneplus-hotdog-popup-motor.patch",
    "0055-arm64-dts-guacamole-fingerprint.patch",
    "0056-arm64-dts-guacamole-popup-camera.patch",
    "0057-arm64-dts-guacamole-displayport-video.patch",
    "0058-arm64-dts-guacamole-displayport-audio-link.patch",
    "0059-slimbus-qcom-ngd-wait-5s-for-qmi.patch",
    "0060-usb-typec-qcom-pmic-typec-pdphy-always-return-rx-buffer.patch",
    "0061-usb-typec-qcom-pmic-typec-vsafe0v-fallback-only-as-sink.patch",
    "0062-usb-typec-tcpm-reset-hard-reset-budget-on-unattach.patch",
    "0063-usb-typec-qcom-pmic-typec-reread-vbus-leaving-sink.patch",
    "0064-drm-msm-dp-use-the-real-pixel-rate-for-link-bandwidth.patch",
    "0065-usb-typec-qcom-pmic-typec-hide-reattach-during-power-role-swap.patch",
    "0066-usb-typec-tcpm-tolerate-one-data-role-mismatch-per-attach.patch",
    "0067-drm-msm-dp-reapply-and-reread-edid-in-get-modes.patch",
    "0068-drm-msm-forbid-mem-reclaim-from-reset.patch",
    "0069-drm-msm-always-recover-the-gpu.patch",
    "0070-drm-msm-recover-hw-before-retire-hung-submit.patch",
    "0071-drm-msm-fix-task-struct-reference-leak-in-recover-worker.patch",
    "0072-arm64-dts-guacamole-pon-reboot-modes.patch",
]


def read(path):
    return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")


def sha512(path):
    h = hashlib.sha512()
    h.update(pathlib.Path(path).read_bytes())
    return h.hexdigest()


def code_lines(text):
    """Drop full-line comments so prose cannot trip a command scan."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def sha256(path):
    h = hashlib.sha256()
    h.update(pathlib.Path(path).read_bytes())
    return h.hexdigest()


def apkbuild(text):
    src = re.search(r'^source="\n(.*?)\n"\n', text, re.S | re.M).group(1)
    names = [l.strip().split("::")[-1].split("/")[-1]
             for l in src.splitlines() if l.strip()]
    sums = {}
    block = re.search(r'^sha512sums="\n(.*?)\n"\n', text, re.S | re.M).group(1)
    for line in block.splitlines():
        parts = line.split()
        if len(parts) == 2:
            sums[parts[1]] = parts[0]
    return names, sums


class TestRecipe(unittest.TestCase):
    def test_checksums_match_sources(self):
        names, sums = apkbuild(read(RECIPE / "APKBUILD"))
        self.assertEqual(len(names), len(sums), "one sha512 per source entry")
        for name in names:
            if name.startswith("linux-") or name == "config-$_flavor.$arch":
                continue
            self.assertIn(name, sums, "%s has no sha512sum" % name)
            self.assertEqual(sha512(RECIPE / name), sums[name],
                             "%s checksum mismatch" % name)

    def test_rc3_patch_bytes_retained(self):
        for p in sorted(RC3_RECIPE.glob("0*.patch")):
            if p.name >= "0042":
                continue
            self.assertEqual((RECIPE / p.name).read_bytes(), p.read_bytes(),
                             "%s was rewritten" % p.name)

    def test_reviewed_new_patch_set_and_order(self):
        present = sorted(p.name for p in RECIPE.glob("0*.patch") if p.name >= "0044")
        self.assertEqual(present, NEW_PATCHES)
        names, _ = apkbuild(read(RECIPE / "APKBUILD"))
        listed = [n for n in names if n >= "0044" and n.endswith(".patch")]
        self.assertEqual(listed, NEW_PATCHES, "source= order must be 0044-0058")

    def test_release_identity(self):
        apk = read(RECIPE / "APKBUILD")
        self.assertIn("pkgver=7.0.0", apk)
        self.assertIn('_tag="v$pkgver-sm8150"', apk)
        config = read(RECIPE / "config-postmarketos-qcom-sm8150.aarch64")
        self.assertIn('CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"', config)
        self.assertNotIn("-sm8150-rc3", config)
        # pkgver + LOCALVERSION is what uname -r reports
        self.assertEqual("7.0.0" + "-sm8150-guacamole-rc4", RELEASE)

    def test_config_gates(self):
        config = read(RECIPE / "config-postmarketos-qcom-sm8150.aarch64")
        for line in ('CONFIG_PM_DEBUG=y', 'CONFIG_PM_SLEEP_DEBUG=y',
                     'CONFIG_QCOM_GUACAMOLE_MSS_VOTE=m', 'CONFIG_QCOMTEE=m',
                     'CONFIG_INPUT_GOODIX_FP=m', 'CONFIG_MXM1120=m',
                     'CONFIG_VIDEO_IMX471=m',
                     'CONFIG_VIDEO_QCOM_HOTDOG_POPUP=m'):
            self.assertIn(line, config)
        # no module_blacklist in config: it is a cmdline gate, not a symbol
        self.assertNotIn("MODULE_BLACKLIST", config)


class TestIsolation(unittest.TestCase):
    def test_no_rc3_kernel_state_or_output_is_used(self):
        for name in ("local_storage_build.sh", "rootfs_prepare_local.sh",
                     "build_local_bootimg.sh", "local_bootimg_wrapper.sh",
                     "check_local_candidate.py"):
            text = code_lines(read(FLOW / "scripts" / name))
            self.assertNotIn("device/testing/rc3/linux-postmarketos-qcom-sm8150", text)
            self.assertNotIn("build/rc3-kernel", text)
            self.assertNotIn(".work/rc3-kernel", text)
            self.assertNotIn("7.0.0-sm8150-rc3", text)
        for name in ("local_storage_build.sh", "rootfs_prepare_local.sh",
                     "build_local_bootimg.sh", "local_bootimg_wrapper.sh",
                     "check_local_candidate.py"):
            text = read(FLOW / "scripts" / name)
            self.assertIn("flows/rc4/build-source", text,
                          "%s must read the rc4 kernel build" % name)

    def test_state_and_output_stay_inside_rc4(self):
        entry = read(FLOW / "scripts/local_storage_build.sh")
        # Plasma keeps the original paths (UI_SUFFIX=""); Phosh gets its own
        # -phosh state/output so the Plasma state is never overwritten.
        self.assertIn('LOCAL_STATE="$FLOW_DIR/.work/local-userdata$UI_SUFFIX"', entry)
        self.assertIn('LOCAL_OUT="$FLOW_DIR/build/local-userdata$UI_SUFFIX"', entry)
        self.assertIn('plasma) UI_SUFFIX="" ;;', entry)
        self.assertIn('phosh) UI_SUFFIX="-phosh" ;;', entry)
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn('STATE="${NURA_ROOTFS_STATE:-$ROOT/.work/local-userdata$UI_SUFFIX/rootfs-state}"',
                      prepare)
        self.assertIn('OUT="${NURA_ROOTFS_OUT:-$ROOT/build/local-userdata$UI_SUFFIX}"', prepare)

    def test_flow_uses_rc4_recipes(self):
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn("flows/rc4/device/testing/linux-postmarketos-qcom-sm8150", prepare)
        self.assertIn('RECIPE_ROOT="${NURA_RECIPE_ROOT:-$ROOT/device/testing}"', prepare)
        entry = read(FLOW / "scripts/local_storage_build.sh")
        self.assertIn("flows/rc4/device/testing/", entry + prepare)

    def test_checker_links_resolve_to_the_rc4_device_package(self):
        # prepare_state() resolves pmaports recipe links through ROOT
        # (flows/rc4/local-storage), not SOURCE_ROOT: the device package it
        # installs must be the rc4i pkgrel 17 copy.
        checker = read(FLOW / "scripts/check_local_candidate.py")
        self.assertIn('"device/testing/device-oneplus-guacamole": '
                      '"device/testing/device-oneplus-guacamole"', checker)
        target = (FLOW / "device/testing/device-oneplus-guacamole").resolve()
        self.assertEqual(target, (RC4 / "device/testing/device-oneplus-guacamole").resolve())
        self.assertRegex(read(target / "APKBUILD"), r"(?m)^pkgrel=17$")
        self.assertIn("guacamole-mss-release", read(target / "APKBUILD"))

    def test_recipe_root_selects_the_rc4_device_package(self):
        # RECIPE_ROOT is the flow's link farm: it must resolve to pkgrel 17 (rc4i)
        # and never to the rc3-era flows/local-storage copy (pkgrel 14).
        farm = FLOW / "device/testing/device-oneplus-guacamole"
        self.assertTrue(farm.is_dir())
        self.assertRegex(read(farm / "APKBUILD"), r"(?m)^pkgrel=17$")
        original = REPO / "flows/local-storage/device/testing/device-oneplus-guacamole/APKBUILD"
        if original.is_file():
            self.assertRegex(read(original), r"(?m)^pkgrel=14$")
            self.assertNotEqual(farm.resolve(), original.resolve())

    def test_unchanged_recipe_links(self):
        links = RC4 / "local-storage/device/testing"
        for name in ("firmware-oneplus-guacamole", "hexagonrpcd",
                     "iio-sensor-proxy", "nura-clock", "rc3", "rear-camera"):
            link = links / name
            self.assertTrue(link.is_symlink(), "%s must be a read-only link" % name)
            self.assertTrue(link.is_dir(), "%s does not resolve" % name)
        self.assertTrue((links / "device-oneplus-guacamole/APKBUILD").is_file())
        self.assertTrue((links / "linux-postmarketos-qcom-sm8150/APKBUILD").is_file())
        # the unchanged ones must still point at the reviewed local-storage copies
        self.assertIn("local-storage/device/testing",
                      str((links / "firmware-oneplus-guacamole").resolve()))


class TestBaselineUntouched(unittest.TestCase):
    def test_rc3_baseline_hashes(self):
        manifest = TESTS / "rc3-baseline.sha256"
        entries = [l.split(None, 1) for l in read(manifest).splitlines()
                   if l and not l.startswith("#")]
        self.assertGreater(len(entries), 40)
        for digest, path in entries:
            target = REPO / path.strip()
            self.assertTrue(target.is_file(), "%s disappeared" % path)
            self.assertEqual(sha256(target), digest, "%s was modified" % path)


class TestGates(unittest.TestCase):
    def test_mss_unit_installed_but_disabled(self):
        dev = read(DEVICE / "APKBUILD")
        self.assertIn("guacamole-mss-release", dev)
        self.assertIn('$pkgdir/usr/libexec/guacamole-mss-release', dev)
        self.assertIn('$pkgdir/usr/lib/systemd/system/guacamole-mss-release.service', dev)
        preset = read(DEVICE / "80-guacamole.preset")
        self.assertNotIn("mss-release", preset)
        self.assertEqual(preset, read(REPO / "device/testing/device-oneplus-guacamole/80-guacamole.preset"),
                         "the rc4 preset must be identical to rc3's")
        # no automatic release from the shared modem starter
        starter = read(DEVICE / "guacamole-mpss-start")
        self.assertNotIn("mss-release", starter)
        self.assertNotIn("guacamole_mss_vote", starter)

    def test_suspend_policy_stays_disabled(self):
        checker = read(FLOW / "scripts/check_local_candidate.py")
        self.assertIn("HandleSuspendKey=ignore", checker)
        self.assertIn("AutoSuspendAction=0", checker)
        self.assertIn("PowerDevil policy does not disable AutoSuspendAction", checker)
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn("Automatic suspend is disabled", prepare)

    def test_qcomtee_blacklisted_on_cmdline(self):
        packer = read(FLOW / "scripts/build_local_bootimg.sh")
        self.assertIn("module_blacklist=qcomtee", packer)
        # it must be on the kernel cmdline, not only a modprobe.d file
        self.assertNotIn("blacklist.conf", packer)

    def test_fingerprint_gate(self):
        fp = read(RECIPE / "0055-arm64-dts-guacamole-fingerprint.patch")
        self.assertIn('status = "disabled"', fp)
        # allow_power must be a bool module parameter that defaults to false
        patch = read(RECIPE / "0051-input-misc-add-goodix-fp.patch")
        self.assertIn("static bool allow_power;", patch)
        self.assertIn("module_param(allow_power, bool, 0", patch)
        self.assertIn("MODULE_PARM_DESC(allow_power", patch)

    def test_popup_gate(self):
        popup = read(RECIPE / "0056-arm64-dts-guacamole-popup-camera.patch")
        # the dtsi content is added by the patch; both nodes must be gated
        self.assertIn('#ifdef CAMERA_POPUP_SENSOR', popup)
        self.assertIn('#ifdef CAMERA_POPUP_MOTOR', popup)
        self.assertIn('status = "disabled"', popup)
        self.assertIn("#include \"sm8150-oneplus-guacamole-popup.dtsi\"", popup)

    def test_dp_audio_link_disabled(self):
        dp = read(RECIPE / "0058-arm64-dts-guacamole-displayport-audio-link.patch")
        self.assertIn('status = "disabled"', dp)
        self.assertIn("dp-dai-link", dp)

    def test_popup_motor_never_enabled_by_default(self):
        popup = read(RECIPE / "0056-arm64-dts-guacamole-popup-camera.patch")
        # default branch of the motor status must be disabled
        block = popup.split("#ifdef CAMERA_POPUP_MOTOR")[1].split("#else")[1]
        self.assertIn('status = "disabled";', block.split("#endif")[0])


class TestCompiledDtb(unittest.TestCase):
    def setUp(self):
        candidates = [d / "sm8150-oneplus-guacamole.dtb" for d in DTB_DIRS]
        self.dtb = next((c for c in candidates if c.is_file()), None)
        if self.dtb is None:
            self.skipTest("no combined DTB yet; run flows/rc4/scripts/rc4.sh kernel")

    def test_three_bindable_camss_connections(self):
        f = dt_facts.facts(self.dtb)
        self.assertEqual(len(f["camss_connections"]), 3)
        for conn in f["camss_connections"]:
            self.assertTrue(conn["bindable"], conn)
        self.assertEqual([c["port"] for c in f["camss_connections"]],
                         ["port@0", "port@1", "port@3"])

    def test_eleven_available_alsa_links(self):
        f = dt_facts.facts(self.dtb)
        self.assertEqual(f["sound_available_links"], 11)
        self.assertEqual(f["sound_child_count"], 12)
        disabled = [l["name"] for l in f["sound_links"] if not l["available"]]
        self.assertEqual(disabled, ["dp-dai-link"])

    def test_rc4_gates_on_dtb(self):
        f = dt_facts.facts(self.dtb)
        bad = dt_facts.require_gates(f)
        self.assertEqual(bad, [])

    def test_popup_absent_together(self):
        f = dt_facts.facts(self.dtb)
        self.assertFalse(f["popup"]["front_sensor"]["present"])
        self.assertFalse(f["popup"]["camss_port2"]["present"])
        self.assertEqual(f["popup"]["motor"]["status"], "disabled")


class TestFlowGuards(unittest.TestCase):
    def test_local_root_guard(self):
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn("/dev/nbd", prepare)
        hook = read(FLOW / "device/testing/rear-camera/"
                    "postmarketos-mkinitfs-hook-netboot/netboot.sh")
        self.assertIn(r"pmos\.root=/dev/nbd", hook)
        # the guard is an early exit, not just a mention
        self.assertRegex(hook, r"if ! grep -q 'pmos\\.root=/dev/nbd' /proc/cmdline; then")

    def test_mount_and_flock_guards(self):
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn("refuse_active_mounts", prepare)
        entry = read(FLOW / "scripts/local_storage_build.sh")
        self.assertIn("flock", entry)
        self.assertIn("overlap", entry)

    def test_public_key_argument(self):
        entry = read(FLOW / "scripts/local_storage_build.sh")
        self.assertIn("--authorized-key", entry)
        prepare = read(FLOW / "scripts/rootfs_prepare_local.sh")
        self.assertIn("AUTHORIZED_KEY", prepare)
        self.assertIn("authorized-key", prepare)
        # private key material is refused, public keys are validated
        checker = read(FLOW / "scripts/check_local_candidate.py")
        self.assertIn("refusing a private key file", checker)
        self.assertIn("PRIVATE KEY", checker)

    def test_no_automatic_phone_actions_in_flow(self):
        # Comments may mention these words; only executable lines count.
        for name in ("local_storage_build.sh", "build_local_bootimg.sh",
                     "local_bootimg_wrapper.sh", "rootfs_prepare_local.sh"):
            text = code_lines(read(FLOW / "scripts" / name))
            for forbidden in ("fastboot flash", "fastboot boot", " adb ",
                              " ssh ", "mkfs.", "sgdisk", "parted "):
                self.assertNotIn(forbidden, text,
                                 "%s contains %r" % (name, forbidden))


if __name__ == "__main__":
    unittest.main(verbosity=2)
