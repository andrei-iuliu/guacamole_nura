#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Checks for the rc4j SSC sensor fix (device/testing recipes + flow wiring).

Two local recipes, iio-sensor-proxy 3.9-r100 and libssc 0.4.4-r100, fix the
100 %-CPU / stalled-claim bug in iio-sensor-proxy's SSC drivers when a client
claims several sensors at once (Phosh Mobile Settings -> Sensors).  They are
wired into the image the way the patched `phoc` is: recipes in
`flows/rc4/device/testing/`, links from the flow's recipe farm, pins in
`/etc/apk/world`, a build-order rule (libssc before the daemon that
makedepends on it) and an installed-rootfs marker check.

What is checked:
  * source= and sha512sums= are paired positionally (abuild's rule) and every
    local file in source= hashes to the sum listed for it;
  * the patches only touch the files they claim, the iio-sensor-proxy one
    removes every *_open_sync call from the four SSC drivers and adds the
    async call plus a completion callback that cannot dereference a NULL
    GError, and the libssc one changes exactly one call (may_block);
  * the flow wiring: recipe links (before the daemon, which makedepends on
    libssc), the version constants, the shared install list for both UIs (the
    daemon and its -systemd/-udev subpackages at the same r100), the world
    pins and the `verify_sensor_drivers` binary check;
  * the verification script, the backtrace and the README the analysis rests
    on are still there.

Run: PYTHONPATH=flows/rc4/local-storage/scripts \\
     python3 -m unittest discover -s flows/rc4/tests -p 'test_*.py'
"""

import hashlib
import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
TOPIC = RC4 / "topics/sensors"
DEVICE = RC4 / "device/testing"
IIO = DEVICE / "iio-sensor-proxy"
LIBSSC = DEVICE / "libssc"
IIO_PATCH = IIO / "0001-drv-ssc-enable-and-disable-sensors-asynchronously.patch"
LIBSSC_PATCH = LIBSSC / "0001-libssc-common-block-in-the-sync-wait.patch"
VERIFY = TOPIC / "checks/verify.sh"
EVIDENCE = TOPIC / "evidence"
FLOW = RC4 / "local-storage"
CHECKER = FLOW / "scripts/check_local_candidate.py"
PREPARE = FLOW / "scripts/rootfs_prepare_local.sh"

SSC_DRIVERS = (
    "src/drv-ssc-accel.c",
    "src/drv-ssc-compass.c",
    "src/drv-ssc-light.c",
    "src/drv-ssc-proximity.c",
)
# patch file -> (lower-case sensor name, GObject macro)
SSC_TYPES = {
    "src/drv-ssc-accel.c": ("accelerometer", "SSC_SENSOR_ACCELEROMETER"),
    "src/drv-ssc-compass.c": ("compass", "SSC_SENSOR_COMPASS"),
    "src/drv-ssc-light.c": ("light", "SSC_SENSOR_LIGHT"),
    "src/drv-ssc-proximity.c": ("proximity", "SSC_SENSOR_PROXIMITY"),
}


def sha512(path):
    return hashlib.sha512(pathlib.Path(path).read_bytes()).hexdigest()


def load_checker(ui="plasma"):
    """Import check_local_candidate.py with NURA_UI=ui (its module constants)."""
    old = os.environ.get("NURA_UI")
    os.environ["NURA_UI"] = ui
    try:
        if str(CHECKER.parent) not in sys.path:      # its sibling helper modules
            sys.path.insert(0, str(CHECKER.parent))
        spec = importlib.util.spec_from_file_location(f"clc_{ui}_{id(ui)}", CHECKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if old is None:
            os.environ.pop("NURA_UI", None)
        else:
            os.environ["NURA_UI"] = old


def shell_function(text, name):
    """Return the body of a top-level bash function from a script's text."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start)
    return text[start:end]


def apkbuild_block(path, name):
    text = pathlib.Path(path).read_text()
    match = re.search(r'(?m)^%s="(.*?)"\s*$' % re.escape(name), text, re.S)
    assert match, "%s has no %s= block" % (path, name)
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def apkbuild_var(path, name):
    match = re.search(r"(?m)^%s=(.+)$" % re.escape(name),
                      pathlib.Path(path).read_text())
    return match.group(1).strip() if match else None


def expand(name, variables):
    for key, value in variables.items():
        name = name.replace("$" + key, value)
    return name


def source_names(path):
    """File names of source= in order.  'name::url' keeps name, a bare URL
    keeps its last path component with the recipe's variables expanded, which
    is how abuild names the downloaded file."""
    variables = {"pkgname": apkbuild_var(path, "pkgname"),
                 "pkgver": apkbuild_var(path, "pkgver")}
    names = []
    for entry in apkbuild_block(path, "source"):
        if "::" in entry:
            names.append(expand(entry.split("::", 1)[0], variables))
        elif "://" in entry:
            names.append(expand(entry.rstrip("/").rsplit("/", 1)[-1], variables))
        else:
            names.append(entry)
    return names


def sum_entries(path):
    """[(digest, file name), ...] in the order sha512sums= lists them."""
    return [tuple(line.split("  ", 1)) for line in apkbuild_block(path, "sha512sums")]


def patch_lines(path):
    """{file: [(sign, text), ...]} for a git-style unified diff."""
    files, current = {}, None
    for line in pathlib.Path(path).read_text().splitlines():
        if line.startswith("diff --git "):
            current = line.split()[-1][2:]        # b/<path>
            files[current] = []
        elif current and line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
            files[current].append((line[0], line[1:]))
    return files


class RecipeTest(unittest.TestCase):
    def test_sources_and_checksums_pair_up_in_order(self):
        for recipe in (IIO, LIBSSC):
            apkbuild = recipe / "APKBUILD"
            with self.subTest(recipe=recipe.name):
                sources = source_names(apkbuild)
                entries = sum_entries(apkbuild)
                self.assertEqual(sources, [name for _, name in entries],
                                 "source= and sha512sums= must list the same "
                                 "names in the same order")
                for digest, name in entries:
                    local = recipe / name
                    if not local.is_file():
                        continue        # remote tarball, only its sum is kept
                    self.assertEqual(sha512(local), digest,
                                     "%s does not match its sha512sum" % name)

    def test_patches_are_listed_as_sources(self):
        self.assertIn(IIO_PATCH.name, source_names(IIO / "APKBUILD"))
        self.assertIn(LIBSSC_PATCH.name, source_names(LIBSSC / "APKBUILD"))

    def test_recipe_versions_are_local_bumps(self):
        iio = (IIO / "APKBUILD").read_text()
        ssc = (LIBSSC / "APKBUILD").read_text()
        self.assertRegex(iio, r"(?m)^pkgver=3\.9$")
        self.assertRegex(iio, r"(?m)^pkgrel=100$")
        self.assertIn("-Dssc-support=enabled", iio)
        self.assertRegex(ssc, r"(?m)^pkgver=0\.4\.4$")
        self.assertRegex(ssc, r"(?m)^pkgrel=100$")
        self.assertIn('builddir="$srcdir/$pkgname"', ssc)


class IioSensorProxyPatchTest(unittest.TestCase):
    def setUp(self):
        self.lines = patch_lines(IIO_PATCH)

    def test_touches_only_the_ssc_drivers(self):
        self.assertEqual(sorted(self.lines), sorted(SSC_DRIVERS))

    def test_no_synchronous_enable_or_disable_is_added(self):
        for name, lines in self.lines.items():
            added = [text for sign, text in lines if sign == "+"]
            removed = [text for sign, text in lines if sign == "-"]
            with self.subTest(file=name):
                for text in added:
                    self.assertNotRegex(text, r"_open_sync\s*\(|_close_sync\s*\(")
                self.assertEqual(
                    len([t for t in removed
                         if "_open_sync (" in t or "_close_sync (" in t]), 2,
                    "both synchronous calls of set_polling() must go")

    def test_asynchronous_open_and_close_replace_them(self):
        for name, lines in self.lines.items():
            kind = SSC_TYPES[name][0]
            added = "\n".join(text for sign, text in lines if sign == "+")
            with self.subTest(file=name):
                self.assertIn("ssc_sensor_%s_open (drv_data->sensor, NULL, "
                              "%s_open_done, NULL);" % (kind, kind), added)
                self.assertIn("ssc_sensor_%s_close (drv_data->sensor, NULL, "
                              "%s_close_done, NULL);" % (kind, kind), added)

    def test_completion_callbacks_cannot_dereference_a_null_error(self):
        # libssc 0.4.4 can return FALSE with error == NULL (fixed upstream
        # only after 0.4.4, f54187c); the old inline warning segfaulted.
        for name, lines in self.lines.items():
            added = "\n".join(text for sign, text in lines if sign == "+")
            with self.subTest(file=name):
                self.assertEqual(added.count('error ? error->message : "UNKNOWN"'), 2)

    def test_callbacks_finish_the_async_operation(self):
        for name, lines in self.lines.items():
            added = "\n".join(text for sign, text in lines if sign == "+")
            kind, macro = SSC_TYPES[name]
            with self.subTest(file=name):
                self.assertIn("ssc_sensor_%s_open_finish (sensor, result, &error)"
                              % kind, added)
                self.assertIn("ssc_sensor_%s_close_finish (sensor, result, &error)"
                              % kind, added)
                self.assertIn("%s (source)" % macro, added)


class LibsscPatchTest(unittest.TestCase):
    def setUp(self):
        self.lines = patch_lines(LIBSSC_PATCH)

    def test_touches_only_the_sync_wait(self):
        self.assertEqual(list(self.lines), ["src/libssc-common.c"])

    def test_the_one_call_now_blocks(self):
        changed = [(sign, text) for sign, text in self.lines["src/libssc-common.c"]
                   if "g_main_context_iteration" in text]
        self.assertEqual([sign for sign, _ in changed], ["-", "+"], changed)
        self.assertTrue(changed[0][1].rstrip().endswith("FALSE);"), changed[0])
        self.assertTrue(changed[1][1].rstrip().endswith("TRUE);"), changed[1])

    def test_explains_why(self):
        added = "\n".join(text for sign, text in self.lines["src/libssc-common.c"]
                          if sign == "+")
        self.assertIn("busy wait", added)


class WiringTest(unittest.TestCase):
    """rc4j: both recipes are wired into the image like the patched phoc."""

    def test_recipes_and_link_farm(self):
        for name, recipe in (("iio-sensor-proxy", IIO), ("libssc", LIBSSC)):
            with self.subTest(recipe=name):
                self.assertTrue((recipe / "APKBUILD").is_file(), recipe)
                self.assertTrue((FLOW / "device/testing" / name / "APKBUILD").is_file(),
                                "the flow link farm must expose the %s recipe" % name)
                link = RC4 / "local-storage/device/testing" / name
                self.assertTrue(link.is_symlink(), link)
                self.assertEqual(link.resolve(), recipe.resolve(),
                                 "the link must resolve to the rc4j recipe, "
                                 "not the rc3-era tree")

    def test_checker_links_and_pins(self):
        checker = CHECKER.read_text()
        links = checker.split("def prepare_state(", 1)[1].split(
            "for relative, target in", 1)[0]
        self.assertIn('"main/libssc": "device/testing/libssc"', links)
        self.assertIn('"main/iio-sensor-proxy": "device/testing/iio-sensor-proxy"', links)
        # libssc is the daemon's makedepend, so its recipe is linked first.
        self.assertLess(links.index('"main/libssc"'),
                        links.index('"main/iio-sensor-proxy"'))
        clc = load_checker()
        self.assertEqual(clc.IIO_SENSOR_PROXY_VERSION, "3.9-r100")
        self.assertEqual(clc.LIBSSC_VERSION, "0.4.4-r100")
        self.assertIn("iio-sensor-proxy", clc.WORLD_PINNED)
        self.assertIn("libssc", clc.WORLD_PINNED)
        # The SSC symbol sets are the marker pair the rootfs check uses: the
        # claim-path marker is *_open_sync only, because discover() keeps its
        # own *_close_sync() call in the patched build.
        self.assertIn("ssc_sensor_light_open_sync", clc.IIO_SENSOR_PROXY_SYNC_SYMBOLS)
        self.assertNotIn("ssc_sensor_light_close_sync", clc.IIO_SENSOR_PROXY_SYNC_SYMBOLS)
        self.assertIn("ssc_sensor_compass_close_finish", clc.IIO_SENSOR_PROXY_ASYNC_SYMBOLS)

    def test_required_for_both_uis_including_subpackages(self):
        checker = CHECKER.read_text()
        required_block = checker.split("    required = {", 1)[1].split(
            'if UI == "plasma":', 1)[0]
        self.assertIn("required[name] = iio_version", required_block)
        self.assertIn('required["libssc"]', required_block)
        self.assertIn('"iio-sensor-proxy-systemd"', checker)
        self.assertIn('"iio-sensor-proxy-udev"', checker)
        self.assertIn("verify_sensor_drivers(rootfs)", checker)
        # mixed r2/r100 sets are rejected even when both mains check out
        self.assertIn("the rootfs mixes repository and local sensor packages", checker)
        self.assertIn('verify.add_argument("--iio-sensor-proxy-version")', checker)
        self.assertIn('verify.add_argument("--libssc-version")', checker)

    def test_verify_rejects_a_missing_or_unpatched_daemon(self):
        clc = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            with self.assertRaises(SystemExit):
                clc.verify_sensor_drivers(root)            # no daemon at all
            binary = root / "usr/libexec/iio-sensor-proxy"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\0ssc_sensor_light_open_sync\0")
            binary.chmod(0o644)
            with self.assertRaises(SystemExit):
                clc.verify_sensor_drivers(root)            # not executable
            binary.chmod(0o755)
            with self.assertRaises(SystemExit):
                clc.verify_sensor_drivers(root)            # r2 claim path
            patched = b"\0".join(symbol.encode()
                                 for symbol in clc.IIO_SENSOR_PROXY_ASYNC_SYMBOLS)
            binary.write_bytes(b"\0" + patched + b"\0")
            clc.verify_sensor_drivers(root)                # local r100 build
            binary.write_bytes(b"\0" + patched.replace(b"compass_open_finish",
                                                       b"nope") + b"\0")
            with self.assertRaises(SystemExit):
                clc.verify_sensor_drivers(root)            # async symbol missing

    def test_flow_builds_libssc_first_and_pins_both_uis(self):
        prepare = PREPARE.read_text()
        loop = prepare.split("for package in ", 1)[1].split("; do", 1)[0]
        self.assertIn("libssc", loop)
        self.assertIn("iio-sensor-proxy", loop)
        self.assertLess(loop.index("libssc"), loop.index("iio-sensor-proxy"),
                        "libssc is the daemon's makedepend and must build first")
        self.assertIn('"$iio_sensor_proxy_version" = 3.9-r100', prepare)
        self.assertIn('"$libssc_version" = 0.4.4-r100', prepare)
        adds = shell_function(prepare, "install_add_string")
        shared = adds.split('case "$NURA_UI" in', 1)[0]
        self.assertIn('"neard"', shared, "both UIs need nfctool for NFC trials")
        for entry in ('"libssc=$libssc_version"',
                      '"iio-sensor-proxy=$iio_sensor_proxy_version"',
                      '"iio-sensor-proxy-systemd=$iio_sensor_proxy_version"',
                      '"iio-sensor-proxy-udev=$iio_sensor_proxy_version"'):
            self.assertIn(entry, shared, "both UIs need " + entry)
        plasma = adds.split("plasma)", 1)[1].split(";;", 1)[0]
        phosh = adds.split("phosh)", 1)[1].split(";;", 1)[0]
        self.assertNotIn("iio-sensor-proxy", plasma, "already in the shared list")
        self.assertNotIn("libssc", phosh, "already in the shared list")
        self.assertIn('sensor_args=(--iio-sensor-proxy-version '
                      '"$(package_version iio-sensor-proxy)"', prepare)
        self.assertIn('--libssc-version "$(package_version libssc)")', prepare)
        self.assertIn('"${sensor_args[@]}"', prepare)

    def test_flow_checker_knows_the_recipes(self):
        text = (RC4 / "scripts/check_recipe.py").read_text()
        self.assertIn('farm / "libssc/APKBUILD"', text)
        self.assertIn('farm / "iio-sensor-proxy/APKBUILD"', text)


class TopicFilesTest(unittest.TestCase):
    def test_verify_script_is_shell_clean(self):
        self.assertTrue(VERIFY.is_file(), "checks/verify.sh is missing")
        self.assertTrue(VERIFY.stat().st_mode & 0o111, "verify.sh is not executable")
        for shell in ("sh", "busybox", "dash"):
            binary = shutil.which(shell)
            if binary is None:
                continue
            with self.subTest(shell=shell):
                result = subprocess.run([binary, "-n", str(VERIFY)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_evidence_backtrace_still_shows_the_nesting(self):
        text = (EVIDENCE / "iio-bt-20261114.txt").read_text()
        self.assertIn("ClaimProximity", text)
        self.assertIn("ClaimLight", text)
        self.assertIn("ssc_sensor_light_open_sync", text)
        # the spinning frame: libssc's wait helper (file offset 0x4808)
        self.assertIn("0x0000ffff8b01c808", text)

    def test_wait_loop_evidence_pins_the_shipped_instruction(self):
        text = (EVIDENCE / "libssc-0.4.4-r0-wait-loop.txt").read_text()
        self.assertIn("mov  w1, #0x0", text)
        self.assertIn("g_main_context_iteration", text)
        self.assertIn("0x4808", text)

    def test_readme_documents_root_cause_and_wiring(self):
        text = (TOPIC / "README.md").read_text()
        for heading in ("## Root cause", "### What is *not* proven", "## Fix",
                        "## How it is wired"):
            self.assertIn(heading, text)
        self.assertIn("systemctl restart iio-sensor-proxy", text)


if __name__ == "__main__":
    unittest.main()
