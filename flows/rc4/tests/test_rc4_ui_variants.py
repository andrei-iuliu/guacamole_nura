#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rootless tests for the Plasma/Phosh UI selector and the shared device fixes.

Nothing here runs pmbootstrap, sudo, a mount or a phone command.  Fixtures
live in temporary directories; the repository is only read.

  * NURA_UI selects the release label (nura-plasma-rc4 / nura-phosh-rc4) and
    the per-UI state/output directories; plasma stays the default;
  * the Phosh UI recipe gets the same candidate-only no-suspend policy as
    Plasma, idempotently and with correct sha512sums;
  * /etc/apk/world must pin kernel, device and firmware to exact versions;
  * the Phosh rootfs check requires the guacamole panel inside libgmobile;
  * the device package wires the A/B slot marker and the Plasma preset, and
    the patched gmobile recipe carries both DT compatibles;
  * the patched phoc recipe keeps its source=/sha512sums= order, carries the
    null-texture guard before the render call, and is built/installed/checked
    by the Phosh flow only;
  * guacamole-boot-slot (ported from omarchy-mobile) changes exactly one
    attribute bit in both GPT copies.
"""

import binascii
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
FLOW = RC4 / "local-storage"
SCRIPTS = FLOW / "scripts"
ENTRY = SCRIPTS / "local_storage_build.sh"
PREPARE = SCRIPTS / "rootfs_prepare_local.sh"
CHECKER = SCRIPTS / "check_local_candidate.py"
DEVICE = RC4 / "device/testing/device-oneplus-guacamole"
GMOBILE = RC4 / "device/testing/gmobile"
PHOC = RC4 / "device/testing/phoc"
RELEASE = "7.0.0-sm8150-guacamole-rc4"

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True


def run(cmd, env=None, **kw):
    full = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
    full.update(env or {})
    return subprocess.run(cmd, capture_output=True, text=True, env=full, **kw)


def load_checker(ui):
    """Import check_local_candidate.py with NURA_UI=ui (module constants)."""
    old = os.environ.get("NURA_UI")
    os.environ["NURA_UI"] = ui
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"clc_{ui}", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))
        if old is None:
            os.environ.pop("NURA_UI", None)
        else:
            os.environ["NURA_UI"] = old


def apkbuild_sums(path):
    text = path.read_text()
    block = re.search(r'sha512sums="(.*?)"', text, re.S).group(1)
    return dict(reversed(line.split("  ", 1)) for line in block.strip().splitlines())


class TestSelector(unittest.TestCase):
    def test_release_candidate_per_ui(self):
        for ui, label in (("plasma", "nura-plasma-rc4"), ("phosh", "nura-phosh-rc4")):
            result = run([sys.executable, str(CHECKER), "release-candidate"], {"NURA_UI": ui})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), label)
        default = run([sys.executable, str(CHECKER), "release-candidate"])
        self.assertEqual(default.stdout.strip(), "nura-phosh-rc4")

    def test_unknown_ui_is_refused(self):
        result = run([sys.executable, str(CHECKER), "release-candidate"], {"NURA_UI": "gnome"})
        self.assertEqual(result.returncode, 2)
        self.assertIn("NURA_UI", result.stderr)
        result = run(["bash", str(ENTRY), "--ui", "gnome"],
                     {"NURA_SOURCE_ROOT": str(RC4.parents[1])})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be plasma or phosh", result.stderr)
        result = run(["bash", str(PREPARE), "--check-only"], {"NURA_UI": "gnome"})
        self.assertEqual(result.returncode, 2)

    def test_phosh_is_local_userdata_only(self):
        text = PREPARE.read_text()
        self.assertIn('supported in the local-userdata profile only', text)

    def test_dry_run_uses_separate_phosh_paths(self):
        # Run a copy of the real entrypoint in a throw-away flow directory with
        # a fake kernel build and hook recipe; the dry run writes nothing.
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            flow = tmp / "flow"
            shutil.copytree(SCRIPTS, flow / "scripts")
            shutil.copy(FLOW / "ORIGIN-SHA256.json", flow / "ORIGIN-SHA256.json")
            hook = flow / "device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
            hook.mkdir(parents=True)
            (hook / "APKBUILD").write_text("pkgrel=5\n")
            (hook / "netboot.sh").write_text("#!/bin/sh\n")
            source = tmp / "source"
            source.mkdir()
            build = tmp / "kernel-output"
            release_file = build / "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
            release_file.parent.mkdir(parents=True)
            release_file.write_text(RELEASE + "\n")
            (build / "kernel-inputs.json").write_text("{}\n")
            state = tmp / "kernel-state"
            (state / "output/include/config").mkdir(parents=True)
            (state / "output/include/config/kernel.release").write_text(RELEASE + "\n")
            env = {"NURA_SOURCE_ROOT": str(source), "NURA_KERNEL_BUILD": str(build),
                   "NURA_KERNEL_SOURCE_STATE": str(state)}
            plans = {}
            for args in (["--ui", "phosh"], ["--ui=plasma"], []):
                result = run(["bash", str(flow / "scripts/local_storage_build.sh"), *args], env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("DRY RUN: no files written", result.stdout)
                plans[" ".join(args) or "default"] = result.stdout
            # Phosh is the default since 2026-11-14; plasma stays selectable.
            for key in ("--ui phosh", "default"):
                self.assertIn("ui:                 phosh", plans[key])
                self.assertIn(str(flow / ".work/local-userdata-phosh") + "\n", plans[key])
                self.assertIn(str(flow / "build/local-userdata-phosh") + "\n", plans[key])
            plasma = plans["--ui=plasma"]
            self.assertIn("ui:                 plasma", plasma)
            self.assertIn(str(flow / ".work/local-userdata") + "\n", plasma)
            self.assertNotIn("local-userdata-phosh", plasma)
            self.assertFalse((flow / ".work").exists())
            self.assertFalse((flow / "build").exists())


MINI_PHOSH_APKBUILD = '''pkgname=postmarketos-ui-phosh
pkgver=34
pkgrel=0
source="
\t01_postmarketos-ui-phosh.gschema.override
\tmimeapps.list
\t"
package() {
\tinstall -Dm644 "$srcdir"/01_postmarketos-ui-phosh.gschema.override \\
\t\t-t "$pkgdir"/usr/share/glib-2.0/schemas/
\tinstall -Dm644 "$srcdir"/mimeapps.list \\
\t\t"$pkgdir"/usr/share/applications/mimeapps.list
}
sha512sums="
%s  01_postmarketos-ui-phosh.gschema.override
%s  mimeapps.list
"
'''


class TestPhoshPolicy(unittest.TestCase):
    def test_phosh_ui_recipe_policy_is_idempotent(self):
        clc = load_checker("phosh")
        with tempfile.TemporaryDirectory() as tmp:
            ui = pathlib.Path(tmp) / "main/postmarketos-ui-phosh"
            ui.mkdir(parents=True)
            (ui / "01_postmarketos-ui-phosh.gschema.override").write_text("[x]\n")
            (ui / "mimeapps.list").write_text("[y]\n")
            sums = [hashlib.sha512(b"[x]\n").hexdigest(), hashlib.sha512(b"[y]\n").hexdigest()]
            (ui / "APKBUILD").write_text(MINI_PHOSH_APKBUILD % tuple(sums))
            _, first = clc.prepare_phosh_ui(pathlib.Path(tmp))
            text_once = (ui / "APKBUILD").read_text()
            _, second = clc.prepare_phosh_ui(pathlib.Path(tmp))
            self.assertEqual(first, second)
            self.assertEqual(text_once, (ui / "APKBUILD").read_text())
            for name, digest in apkbuild_sums(ui / "APKBUILD").items():
                self.assertEqual(hashlib.sha512((ui / name).read_bytes()).hexdigest(), digest, name)
            self.assertIn("IdleAction=ignore", (ui / "50-nura-phosh-nbd.conf").read_text())
            self.assertIn("sleep-inactive-battery-type='nothing'",
                          (ui / clc.PHOSH_GSETTINGS_OVERRIDE_NAME).read_text())
            self.assertEqual(text_once.count("50-nura-phosh-nbd.conf"), 3)

    def test_plasma_constants_unchanged(self):
        clc = load_checker("plasma")
        self.assertEqual(clc.RELEASE_CANDIDATE, "nura-plasma-rc4")
        self.assertEqual(clc.PROFILE, "plasma-mobile")
        self.assertEqual(clc.UI_PACKAGE, "postmarketos-ui-plasma-mobile")
        self.assertEqual(clc.CAMERA_APP, "plasma-camera")
        self.assertIn("discover-backend-apk", clc.UI_PROFILE["app_store"])
        phosh = load_checker("phosh")
        self.assertEqual(phosh.UI_PACKAGE, "postmarketos-ui-phosh")
        self.assertIn("gnome-software-plugin-apk2", phosh.UI_PROFILE["app_store"])


def rootfs_with(tmp, world, versions):
    root = pathlib.Path(tmp)
    (root / "etc/apk").mkdir(parents=True)
    (root / "etc/apk/world").write_text("\n".join(world) + "\n")
    (root / "lib/apk/db").mkdir(parents=True)
    (root / "lib/apk/db/installed").write_text(
        "".join(f"P:{name}\nV:{version}\n\n" for name, version in versions.items()))
    return root


class TestWorldPins(unittest.TestCase):
    # The rc4j images pin the two SSC sensor recipes next to the kernel, the
    # device and the firmware package: the sensor fix lives in those builds.
    VERSIONS = {"linux-postmarketos-qcom-sm8150": "7.0.0_p1-r0",
                "device-oneplus-guacamole": "4-r16",
                "firmware-oneplus-guacamole": "1-r7",
                "iio-sensor-proxy": "3.9-r100",
                "libssc": "0.4.4-r100"}

    def test_exact_pins_pass(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            root = rootfs_with(tmp, [f"{n}={v}" for n, v in self.VERSIONS.items()] + ["discover"],
                               self.VERSIONS)
            pins = clc.verify_world_pins(root, clc.installed_packages(root),
                                         {"device-oneplus-guacamole": "4-r16"})
            self.assertEqual(pins, self.VERSIONS)

    def test_unpinned_device_fails(self):
        clc = load_checker("phosh")
        with tempfile.TemporaryDirectory() as tmp:
            world = ["linux-postmarketos-qcom-sm8150=7.0.0_p1-r0", "device-oneplus-guacamole",
                     "firmware-oneplus-guacamole=1-r7",
                     "iio-sensor-proxy=3.9-r100", "libssc=0.4.4-r100"]
            root = rootfs_with(tmp, world, self.VERSIONS)
            with self.assertRaises(SystemExit):
                clc.verify_world_pins(root, clc.installed_packages(root), {})


class TestPhoshPanelCheck(unittest.TestCase):
    def test_libgmobile_must_carry_the_guacamole_panel(self):
        clc = load_checker("phosh")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "usr/bin").mkdir(parents=True)
            (root / "usr/bin/snapshot").write_text("")
            lib = root / "usr/lib/libgmobile.so.0.0.0"
            lib.parent.mkdir(parents=True)
            lib.write_bytes(b"\0stock gmobile\0")
            with self.assertRaises(SystemExit):
                clc.verify_phosh_camera_and_panel(root)
            lib.write_bytes(b"\0".join(clc.GMOBILE_PANEL_MARKERS))
            clc.verify_phosh_camera_and_panel(root)


class TestUiExclusions(unittest.TestCase):
    """Per-UI default apps the build must not install.

    pmbootstrap 3.12.1 refuses a "!pkgname" entry in the install package list
    (pmbootstrap pmb/chroot/apk.py:220-222), so the exclusion is applied to the
    rootfs chroot and then re-checked against the installed APK database.
    """

    PHOSH = ("phosh-tour", "lpa-gtk", "vvmplayer", "decibels")

    def test_profile_lists(self):
        plasma = load_checker("plasma")
        phosh = load_checker("phosh")
        self.assertEqual(plasma.EXCLUDED_PACKAGES, ())
        self.assertEqual(plasma.UI_PROFILE["excluded_packages"], ())
        self.assertEqual(phosh.EXCLUDED_PACKAGES, self.PHOSH)
        self.assertEqual(phosh.UI_PROFILE["excluded_packages"], self.PHOSH)
        # Gapless (g4music) is the wanted audio player and must not be listed.
        self.assertNotIn("g4music", phosh.EXCLUDED_PACKAGES)

    def test_cli_lists_one_line_per_ui(self):
        cases = (("plasma", ""), ("phosh", ",".join(self.PHOSH)))
        for ui, expected in cases:
            result = run([sys.executable, str(CHECKER), "exclude-list"], {"NURA_UI": ui})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
        # Phosh is the default since 2026-11-14.
        default = run([sys.executable, str(CHECKER), "exclude-list"])
        self.assertEqual(default.stdout.strip(), ",".join(self.PHOSH))

    def test_post_install_check_fails_on_an_excluded_package(self):
        clc = load_checker("phosh")
        with tempfile.TemporaryDirectory() as tmp:
            root = rootfs_with(tmp, ["postmarketos-ui-phosh"],
                               {"phosh": "34-r0", "g4music": "47.0-r0"})
            clc.verify_excluded_packages(clc.installed_packages(root))
            database = root / "lib/apk/db/installed"
            for name in self.PHOSH:
                with self.subTest(package=name):
                    database.write_text(
                        "P:phosh\nV:34-r0\n\n" + f"P:{name}\nV:1-r0\n\n")
                    with self.assertRaises(SystemExit):
                        clc.verify_excluded_packages(clc.installed_packages(root))

    def test_plasma_never_excludes_anything(self):
        clc = load_checker("plasma")
        with tempfile.TemporaryDirectory() as tmp:
            root = rootfs_with(tmp, ["postmarketos-ui-plasma-mobile"],
                               {name: "1-r0" for name in self.PHOSH})
            clc.verify_excluded_packages(clc.installed_packages(root))

    def test_build_applies_the_list_before_writing_the_image(self):
        prepare = PREPARE.read_text()
        self.assertIn('check_local_candidate.py" exclude-list', prepare)
        # Phase 1 populates the chroot without an image, then the excluded
        # apps are deleted from that chroot, then the image is written with
        # --no-recommends so the deletion survives.
        self.assertIn("install --no-image", prepare)
        self.assertIn("apk del $to_del", prepare)
        populate = prepare.index("install --no-image")
        remove = prepare.index('remove_excluded_packages "${PMB[@]}"')
        write_image = prepare.index("--no-split --filesystem ext4 --no-recommends --add")
        self.assertLess(populate, remove)
        self.assertLess(remove, write_image)
        self.assertIn("verify_excluded_packages", CHECKER.read_text())


class TestShutdownCommand(unittest.TestCase):
    """rc4.sh shutdown: per-UI, refuses an incomplete state, never force-unmounts.

    ``findmnt`` and ``python3`` are fake scripts on PATH; no real pmbootstrap,
    mount or sudo runs here.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name) / "flow with spaces"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy2(ENTRY, self.root / "scripts")
        self.bin = pathlib.Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.calls = pathlib.Path(self.tmp.name) / "calls"
        self.marker = pathlib.Path(self.tmp.name) / "mounted"
        self.phosh_state = self.root / ".work/local-userdata-phosh/rootfs-state"
        # findmnt raw output escapes spaces as \x20; the script unescapes it.
        self.mount_target = str(self.phosh_state / "pmbootstrap-work/chroot_native/proc")
        self.tool("findmnt", '[[ ! -f "$TEST_MOUNT_MARKER" ]] || printf "%s\\n" "$TEST_MOUNT_TARGET"\n')
        self.tool("python3", '''printf "%s\\n" "$@" > "$TEST_CALLS"
[[ ${TEST_REMAIN:-0} == 0 ]] && rm -f "$TEST_MOUNT_MARKER"
exit 0
''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\nset -e\n" + body)
        path.chmod(0o755)

    def install_state(self):
        for directory in ("pmbootstrap", "rootfs", "pmaports", "pmbootstrap-work"):
            (self.phosh_state / directory).mkdir(parents=True, exist_ok=True)
        (self.phosh_state / "pmbootstrap/pmbootstrap.py").touch()
        (self.phosh_state / "rootfs/pmbootstrap.cfg").touch()

    def shutdown(self, *args, **extra):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin",
               "TEST_MOUNT_MARKER": str(self.marker),
               "TEST_MOUNT_TARGET": self.mount_target.replace(" ", r"\x20"),
               "TEST_CALLS": str(self.calls)}
        env.update(extra)
        return run(["bash", str(self.root / "scripts/local_storage_build.sh"),
                    "--shutdown", *args], env, timeout=30)

    def test_no_mounts_is_safe(self):
        result = self.shutdown("--ui", "phosh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no active mounts under", result.stdout)
        self.assertIn(".work/local-userdata-phosh", result.stdout)
        self.assertFalse(self.calls.exists(), "pmbootstrap must not be called")

    def test_state_directory_follows_the_ui(self):
        phosh = self.shutdown("--ui", "phosh")
        self.assertIn(".work/local-userdata-phosh", phosh.stdout)
        plasma = self.shutdown("--ui", "plasma")
        self.assertEqual(plasma.returncode, 0, plasma.stderr)
        self.assertIn(".work/local-userdata\n", plasma.stdout)
        self.assertNotIn("local-userdata-phosh", plasma.stdout)

    def test_dry_run_prints_the_command_and_never_calls_pmbootstrap(self):
        self.install_state()
        self.marker.touch()
        result = self.shutdown("--ui", "phosh", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = str(self.phosh_state)
        self.assertIn(f"shutdown command: python3 {state}/pmbootstrap/pmbootstrap.py "
                      f"-c {state}/rootfs/pmbootstrap.cfg -p {state}/pmaports "
                      f"-w {state}/pmbootstrap-work shutdown", result.stdout)
        self.assertIn("pmbootstrap-work/chroot_native/proc", result.stdout)
        self.assertIn("pmbootstrap was not called", result.stdout)
        self.assertFalse(self.calls.exists(), "the dry run must not call pmbootstrap")

    def test_incomplete_state_is_refused(self):
        self.marker.touch()
        result = self.shutdown("--ui", "phosh")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("incomplete", result.stderr)
        self.assertFalse(self.calls.exists(), "pmbootstrap must not be called")

    def test_runs_pmbootstrap_shutdown_then_reports_cleared_mounts(self):
        self.install_state()
        self.marker.touch()
        result = self.shutdown("--ui", "phosh")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = str(self.phosh_state)
        self.assertEqual(self.calls.read_text().splitlines(), [
            f"{state}/pmbootstrap/pmbootstrap.py",
            "-c", f"{state}/rootfs/pmbootstrap.cfg",
            "-p", f"{state}/pmaports",
            "-w", f"{state}/pmbootstrap-work",
            "shutdown",
        ])
        self.assertIn("mounts cleared", result.stdout)

    def test_remaining_mounts_fail_with_the_list(self):
        self.install_state()
        self.marker.touch()
        result = self.shutdown("--ui", "phosh", TEST_REMAIN="1")
        self.assertEqual(result.returncode, 1)
        self.assertIn("mounts remain after the phosh shutdown", result.stderr)
        self.assertIn("pmbootstrap-work/chroot_native/proc", result.stderr)

    def test_rc4_wrapper_offers_the_action(self):
        wrapper = (RC4 / "scripts/rc4.sh").read_text()
        self.assertIn("shutdown)", wrapper)
        self.assertIn('bash "$LOCAL_FLOW" --shutdown "$@"', wrapper)
        self.assertIn("shutdown [--ui plasma|phosh] [--dry-run]", wrapper)

    def test_active_mount_error_prints_the_shutdown_command(self):
        text = ENTRY.read_text()
        self.assertIn('"- clear the leftover $NURA_UI chroots with: '
                      'bash $RC4_WRAPPER shutdown --ui $NURA_UI"', text)
        # fish is the login shell: the hint must not assign a variable first.
        hint = [line for line in text.splitlines() if "clear the leftover" in line][0]
        self.assertNotIn("=$STATE", hint)
        self.assertNotIn("$STATE/", hint)


class TestDevicePackage(unittest.TestCase):
    def test_sources_and_checksums(self):
        apkbuild = DEVICE / "APKBUILD"
        text = apkbuild.read_text()
        self.assertRegex(text, r"(?m)^pkgrel=21$")
        sums = apkbuild_sums(apkbuild)
        for name in ("plasma-mobile-preset-oneplus-guacamole.conf", "guacamole-boot-slot",
                     "guacamole-boot-slot.service", "guacamole-boot-slot.timer",
                     "82-guacamole-boot-slot.preset"):
            self.assertIn(name, sums)
            self.assertEqual(hashlib.sha512((DEVICE / name).read_bytes()).hexdigest(), sums[name], name)
        for path in ("/usr/libexec/guacamole-boot-slot",
                     "/usr/lib/systemd/system/guacamole-boot-slot.timer",
                     "/usr/lib/systemd/system-preset/82-guacamole-boot-slot.preset",
                     "/usr/share/plasma-mobile-device-presets/$preset.conf"):
            self.assertIn(path, text)
        self.assertIn("for preset in oneplus,guacamole qcom,sm8150-mtp", text)

    def test_unit_wiring(self):
        service = (DEVICE / "guacamole-boot-slot.service").read_text()
        timer = (DEVICE / "guacamole-boot-slot.timer").read_text()
        preset = (DEVICE / "82-guacamole-boot-slot.preset").read_text()
        self.assertIn("ExecStart=/usr/libexec/guacamole-boot-slot mark-successful", service)
        self.assertIn("ExecCondition=/usr/bin/systemctl is-active --quiet display-manager.service",
                      service)
        self.assertIn("Unit=guacamole-boot-slot.service", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("enable guacamole-boot-slot.timer", preset)
        self.assertTrue(os.access(DEVICE / "guacamole-boot-slot", os.X_OK))

    def test_plasma_preset_matches_plasma_corners_branch(self):
        top = RC4.parents[1] / "device/testing/device-oneplus-guacamole/plasma-mobile-preset-oneplus-guacamole.conf"
        if not top.is_file():
            # rc4b carries the preset only in the rc4 device package; the
            # top-level copy lives on the plasma-corners branch.
            self.skipTest("top-level preset not on this branch")
        self.assertEqual((DEVICE / top.name).read_bytes(), top.read_bytes())


class TestGmobileRecipe(unittest.TestCase):
    PATCH = "0001-data-add-OnePlus-7-Pro-guacamole-display-panel.patch"

    def test_recipe_and_patch(self):
        apkbuild = GMOBILE / "APKBUILD"
        text = apkbuild.read_text()
        version = "%s-r%s" % (re.search(r"(?m)^pkgver=(.+)$", text).group(1),
                              re.search(r"(?m)^pkgrel=(.+)$", text).group(1))
        self.assertEqual(version, load_checker("phosh").GMOBILE_VERSION)
        sums = apkbuild_sums(apkbuild)
        self.assertEqual(hashlib.sha512((GMOBILE / self.PATCH).read_bytes()).hexdigest(),
                         sums[self.PATCH])
        self.assertTrue((FLOW / "device/testing/gmobile/APKBUILD").is_file(),
                        "the flow link farm must expose the gmobile recipe")
        self.assertIn('links["main/gmobile"] = "device/testing/gmobile"', CHECKER.read_text())

    def test_patch_adds_both_compatibles(self):
        patch = (GMOBILE / self.PATCH).read_text()
        for compatible in ("oneplus,guacamole", "qcom,sm8150-mtp"):
            self.assertIn(f"+++ b/data/devices/display-panels/{compatible}.json", patch)
            self.assertIn(f'devices/display-panels/{compatible}.json</file>', patch)
        bodies = re.findall(r"@@ -0,0 \+1,8 @@\n((?:\+.*\n){8})", patch)
        self.assertEqual(len(bodies), 2)
        for body in bodies:
            panel = json.loads("".join(line[1:] + "\n" for line in body.splitlines()))
            self.assertEqual(panel, {"name": "OnePlus 7 Pro", "x-res": 1440, "y-res": 3120,
                                     "corner-radii": [145, 145, 145, 145],
                                     "width": 71, "height": 154})


# --- phoc: the local 0.58.0-r100 recipe with the thumbnail null-texture guard


def shell_function(text, name):
    """Return the body of a top-level bash function from a script's text."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start)
    return text[start:end]


class TestPhocRecipe(unittest.TestCase):
    """rc4j: a local phoc that survives the thumbnail crash path."""

    PATCH = "0001-render-skip-surfaces-that-have-no-texture.patch"
    # sha512 of phoc-0.58.0.tar.xz, downloaded from the recipe's source= URL
    # and identical to Alpine community/phoc's 0.58.0-r0 APKBUILD.
    TARBALL_SHA512 = (
        "8abdf88f5e00c86ce3da54b24ff691cfe1a5cc7cb55bf725714d269473d43748"
        "15dfb857028abf0a0ecab6a33ca5c350598ae872eb1f220071493f743fa15dcc"
    )

    def test_recipe_and_patch(self):
        apkbuild = PHOC / "APKBUILD"
        text = apkbuild.read_text()
        self.assertIn("pkgname=phoc", text)
        self.assertEqual(re.search(r"(?m)^pkgver=(.+)$", text).group(1), "0.58.0")
        self.assertEqual(re.search(r"(?m)^pkgrel=(.+)$", text).group(1), "100")
        self.assertEqual("0.58.0-r100", load_checker("phosh").PHOC_VERSION)
        # The upstream subpackages must stay exactly as Alpine ships them.
        self.assertIn("\t$pkgname-dbg\n\t$pkgname-doc\n\t$pkgname-lang\n"
                      "\t$pkgname-schemas::noarch\n", text)
        self.assertIn("options=\"!check\" # Needs fullblown EGL", text)
        sums = apkbuild_sums(apkbuild)
        self.assertIn(self.PATCH, sums)
        self.assertEqual(hashlib.sha512((PHOC / self.PATCH).read_bytes()).hexdigest(),
                         sums[self.PATCH])
        self.assertEqual(sums["phoc-0.58.0.tar.xz"], self.TARBALL_SHA512)
        self.assertTrue((FLOW / "device/testing/phoc/APKBUILD").is_file(),
                        "the flow link farm must expose the phoc recipe")
        self.assertIn('links["main/phoc"] = "device/testing/phoc"', CHECKER.read_text())

    def test_source_and_checksums_are_in_the_same_order(self):
        text = (PHOC / "APKBUILD").read_text()
        source = re.search(r'(?m)^source="(.*?)"$', text, re.S).group(1)
        names = [pathlib.PurePosixPath(line.strip()).name
                 for line in source.replace("${pkgver/_/.}", "0.58.0").splitlines()
                 if line.strip()]
        block = re.search(r'sha512sums="(.*?)"', text, re.S).group(1)
        entries = [line.split("  ") for line in block.strip().splitlines()]
        self.assertEqual(names, [name for _, name in entries],
                         "abuild pairs sha512sums= with source= positionally")
        for digest, name in entries:
            self.assertRegex(digest, r"^[0-9a-f]{128}$", name)

    def test_patch_guards_the_render_call(self):
        patch = (PHOC / self.PATCH).read_text()
        # Original author attribution is kept in the header.
        self.assertIn("From: Alexandr Mironov", patch)
        self.assertIn("phoc 0.56.0", patch)
        # Refreshed for 0.58.0: -512,6 is the hunk's real position, so the
        # patch applies with fuzz 0 and no offset.
        self.assertIn("@@ -512,6 +512,9 @@", patch)
        body = patch.split("@@", 2)[-1]
        guard = body.index("+  if (!texture)")
        self.assertLess(body.index("texture = wlr_surface_get_texture (surface);"), guard)
        self.assertLess(guard, body.index("phoc_view_get_geometry"))
        self.assertIn("+    return;", body)

    def test_verify_rejects_a_missing_or_unpatched_compositor(self):
        clc = load_checker("phosh")
        # The recorded hash of the unpatched Alpine 0.58.0-r0 binary.
        self.assertEqual(clc.PHOC_UPSTREAM_SHA256,
                         "1645e7abf59ca98adf4a0a5652d4044dc7a2bb4f67014e74d918532b18b48245")
        self.assertIn(clc.PHOC_UPSTREAM_SHA256, (RC4.parents[1] / "docs/NURA_GPU_FAULTS.md").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self.assertRaises(SystemExit):
                clc.verify_phoc_guard(root)
            binary = root / "usr/bin/phoc"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\0local phoc 0.58.0-r100\0")
            binary.chmod(0o644)
            with self.assertRaises(SystemExit):
                clc.verify_phoc_guard(root)
            binary.chmod(0o755)
            clc.verify_phoc_guard(root)
            # A rootfs that still carries the repository build must fail: pin the
            # recorded hash onto this fixture to exercise that branch.
            recorded = clc.PHOC_UPSTREAM_SHA256
            clc.PHOC_UPSTREAM_SHA256 = hashlib.sha256(binary.read_bytes()).hexdigest()
            try:
                with self.assertRaises(SystemExit):
                    clc.verify_phoc_guard(root)
            finally:
                clc.PHOC_UPSTREAM_SHA256 = recorded

    def test_flow_builds_and_pins_phoc_for_phosh_only(self):
        prepare = PREPARE.read_text()
        self.assertIn('[[ "$NURA_UI" != phosh ]] || ui_builds+=(gmobile phoc)', prepare)
        self.assertIn('[[ "$phoc_version" = 0.58.0-r100 ]]', prepare)
        self.assertIn('"phoc (null-texture guard): $phoc_version"', prepare)
        self.assertIn('phoc_args=(--phoc-version "$(package_version phoc)")', prepare)
        self.assertIn('"${phoc_args[@]}"', prepare)
        adds = shell_function(prepare, "install_add_string")
        plasma = adds.split("plasma)", 1)[1].split(";;", 1)[0]
        phosh = adds.split("phosh)", 1)[1].split(";;", 1)[0]
        self.assertNotIn("phoc", plasma, "Plasma must not install the local phoc")
        self.assertIn('"phoc=$phoc_version"', phosh)
        checker = CHECKER.read_text()
        # verify only requires the local phoc for phosh (right after gmobile).
        self.assertIn('required["phoc"] = args.phoc_version or PHOC_VERSION', checker)
        self.assertGreater(checker.index('required["phoc"]'),
                           checker.index('required["gmobile"]'))
        self.assertIn("verify_phoc_guard(rootfs)", checker)
        self.assertIn("verify.add_argument(\"--phoc-version\")", checker)
        # prepare_state shadows Alpine's community/phoc for the Phosh UI only.
        prepare_state = checker.split("def prepare_state(", 1)[1]
        phosh_links = prepare_state.split('if UI == "phosh":', 1)[1].split(
            "for relative, target in optional_links", 1)[0]
        self.assertIn('links["main/phoc"] = "device/testing/phoc"', phosh_links)


# --- guacamole-boot-slot: GPT fixture tests ported from omarchy-mobile
# devices/oneplus7pro/tests/test_boot_slot.py ---
loader = importlib.machinery.SourceFileLoader("guacamole_boot_slot", str(DEVICE / "guacamole-boot-slot"))
spec = importlib.util.spec_from_loader(loader.name, loader)
boot_slot = importlib.util.module_from_spec(spec)
loader.exec_module(boot_slot)

BLOCK = 4096
BLOCKS = 64
COUNT = 128
LABELS = ["xbl_a", "abl_a", "boot_a", "dtbo_a", "abl_b", "boot_b", "dtbo_b"]


def crc(data):
    return binascii.crc32(data) & 0xffffffff


def flags(retry, active=False, successful=False, unbootable=False, priority=3):
    return priority | (0x04 if active else 0) | (retry << 3) | (0x40 if successful else 0) | \
        (0x80 if unbootable else 0)


def build_image(path, slot_flags):
    entries = bytearray(COUNT * 128)
    for i, label in enumerate(LABELS):
        entry = bytearray(128)
        entry[:16] = uuid.uuid4().bytes
        entry[16:32] = uuid.uuid4().bytes
        struct.pack_into("<QQ", entry, 32, 10 + i, 10 + i)
        struct.pack_into("<Q", entry, 48, 0x1000000000000001)
        entry[56:56 + 2 * len(label)] = label.encode("utf-16-le")
        if label.startswith("boot_"):
            entry[54] = slot_flags[label[-1]]
        entries[i * 128:(i + 1) * 128] = entry
    guid = uuid.uuid4().bytes
    image = bytearray(BLOCK * BLOCKS)

    def header(my_lba, alternate, entries_lba):
        raw = bytearray(boot_slot.HEADER.pack(
            b"EFI PART", 0x00010000, 92, 0, 0, my_lba, alternate, 6, BLOCKS - 6,
            guid, entries_lba, COUNT, 128, crc(entries)))
        struct.pack_into("<I", raw, 16, crc(raw))
        return raw

    image[BLOCK:BLOCK + 92] = header(1, BLOCKS - 1, 2)
    image[2 * BLOCK:2 * BLOCK + len(entries)] = entries
    image[(BLOCKS - 1) * BLOCK:(BLOCKS - 1) * BLOCK + 92] = header(BLOCKS - 1, 1, BLOCKS - 5)
    image[(BLOCKS - 5) * BLOCK:(BLOCKS - 5) * BLOCK + len(entries)] = entries
    path.write_bytes(image)


class TestBootSlot(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.dev = root / "dev"
        self.dev.mkdir()
        queue = root / "sys/block/sde/queue"
        queue.mkdir(parents=True)
        (queue / "logical_block_size").write_text(f"{BLOCK}\n")
        (root / "sys/block/sde/size").write_text(f"{BLOCKS * BLOCK // 512}\n")
        self.image = self.dev / "sde"
        boot_slot.SYS_BLOCK = root / "sys/block"
        boot_slot.DEV = self.dev
        os.environ["BOOT_SLOT_LOCK"] = str(root / "lock")

    def tearDown(self):
        os.environ.pop("BOOT_SLOT_LOCK", None)
        self.tmp.cleanup()

    def test_marks_only_the_active_boot_entry_in_both_copies(self):
        build_image(self.image, {"a": flags(7), "b": flags(6, active=True)})
        before = self.image.read_bytes()
        boot_slot.mark_successful(boot_slot.find_disk(), dry_run=False)
        after = self.image.read_bytes()
        changed = {i for i in range(len(before)) if before[i] != after[i]}
        boot_b = 2 * BLOCK + LABELS.index("boot_b") * 128 + 54
        backup_b = (BLOCKS - 5) * BLOCK + LABELS.index("boot_b") * 128 + 54
        self.assertEqual(after[boot_b], flags(6, active=True, successful=True))
        self.assertEqual(after[backup_b], after[boot_b])
        allowed = {boot_b, backup_b}
        for lba in (1, BLOCKS - 1):
            allowed |= set(range(lba * BLOCK + 16, lba * BLOCK + 20))
            allowed |= set(range(lba * BLOCK + 88, lba * BLOCK + 92))
        self.assertTrue(changed <= allowed)
        self.assertEqual(boot_slot.find_disk().flags("a"), flags(7))

    def test_repeat_and_dry_run_write_nothing(self):
        build_image(self.image, {"a": flags(7), "b": flags(6, active=True, successful=True)})
        before = self.image.read_bytes()
        boot_slot.mark_successful(boot_slot.find_disk(), dry_run=False)
        self.assertEqual(self.image.read_bytes(), before)
        build_image(self.image, {"a": flags(7), "b": flags(6, active=True)})
        before = self.image.read_bytes()
        boot_slot.mark_successful(boot_slot.find_disk(), dry_run=True)
        self.assertEqual(self.image.read_bytes(), before)

    def test_refusals(self):
        build_image(self.image, {"a": flags(7), "b": flags(0, active=True, unbootable=True)})
        with self.assertRaises(boot_slot.GptError):
            boot_slot.mark_successful(boot_slot.find_disk(), dry_run=False)
        build_image(self.image, {"a": flags(7, active=True), "b": flags(6, active=True)})
        with self.assertRaises(boot_slot.GptError):
            boot_slot.mark_successful(boot_slot.find_disk(), dry_run=False)
        build_image(self.image, {"a": flags(7), "b": flags(6, active=True)})
        image = bytearray(self.image.read_bytes())
        image[(BLOCKS - 5) * BLOCK + 1] ^= 0xff
        self.image.write_bytes(image)
        with self.assertRaises(boot_slot.GptError):
            boot_slot.find_disk()


if __name__ == "__main__":
    unittest.main()
