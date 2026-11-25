#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Unit tests for nura-upgrade (device-oneplus-guacamole).

`apk`, `id` and `uname` are stubs in a temporary PATH; nothing here touches
the host package database, root, systemd, a phone or the network.  The fake
apk keeps a call log so the tests can prove that a refusal, a simulation or
an aborted prompt never reaches `apk upgrade`.

Covered:
  * before.txt identity (uname -r, /etc/apk/world, sorted apk info -v);
  * pinned/unsatisfiable simulate refusal names the blocking packages and
    exits non-zero without running `apk upgrade`;
  * sensitive-change report (mesa, phoc, ...) and non-sensitive filtering;
  * --simulate stops after the report; --yes and the y/N prompt paths;
  * open GPU soak warning;
  * after.txt/changed.txt and the reboot reminder;
  * device package pkgrel 21, source/sha512 order and install path;
  * `sh -n` and BusyBox `sh -n` when available.

Run: PYTHONPATH=flows/rc4/local-storage/scripts \\
     python3 -m unittest discover -s flows/rc4/tests -p 'test_*.py'
"""

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
DEVICE = TESTS.parent / "device/testing/device-oneplus-guacamole"
UPGRADE = DEVICE / "nura-upgrade"
APKBUILD = DEVICE / "APKBUILD"

INFO_BEFORE = """\
curl-8.10.0-r0
device-oneplus-guacamole-4-r20
linux-postmarketos-qcom-sm8150-7.0.0-r0
mesa-26.2.6-r0
phoc-0.58.0-r100
"""

INFO_AFTER = """\
curl-8.11.0-r0
device-oneplus-guacamole-4-r20
linux-postmarketos-qcom-sm8150-7.0.0-r0
mesa-26.3.0-r0
phoc-0.58.0-r101
"""

SIMULATE = """\
(1/3) Upgrading mesa (26.2.6-r0 -> 26.3.0-r0)
(2/3) Upgrading phoc (0.58.0-r100 -> 0.58.0-r101)
(3/3) Upgrading curl (8.10.0-r0 -> 8.11.0-r0)
"""

CONFLICT = """\
ERROR: unable to select packages:
  phoc-0.58.0-r100 (no such package):
    required by: world[phoc=0.58.0-r100]
  linux-postmarketos-qcom-sm8150-7.0.0-r0 (no such package):
    required by: world[linux-postmarketos-qcom-sm8150=7.0.0-r0]
"""

WORLD = """\
linux-postmarketos-qcom-sm8150=7.0.0-r0
device-oneplus-guacamole=4-r20
firmware-oneplus-guacamole=1-r7
phoc=0.58.0-r100
gmobile=0.7.4-r100
postmarketos-ui-phosh=34-r0
"""

FAKE_APK = """\
#!/bin/sh
printf '%s\\n' "$*" >> "$APK_CALLS"
case "$*" in
  "info -v")
    if [ -f "$APK_UPGRADED" ]; then
      cat "$APK_INFO_AFTER"
    else
      cat "$APK_INFO_BEFORE"
    fi
    ;;
  update)
    if [ -n "${APK_UPDATE_OUT:-}" ]; then cat "$APK_UPDATE_OUT"; fi
    exit "${APK_UPDATE_RC:-0}"
    ;;
  "upgrade --simulate")
    if [ -n "${APK_SIMULATE_OUT:-}" ]; then cat "$APK_SIMULATE_OUT"; fi
    exit "${APK_SIMULATE_RC:-0}"
    ;;
  upgrade)
    if [ -n "${APK_UPGRADE_OUT:-}" ]; then cat "$APK_UPGRADE_OUT"; fi
    rc=${APK_UPGRADE_RC:-0}
    if [ "$rc" -eq 0 ]; then : > "$APK_UPGRADED"; fi
    exit "$rc"
    ;;
  *)
    exit 99
    ;;
esac
"""


def sha512(path):
    return hashlib.sha512(pathlib.Path(path).read_bytes()).hexdigest()


def run(cmd, env, input_text=None):
    base = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    base.update(env)
    return subprocess.run(cmd, env=base, capture_output=True, text=True,
                          timeout=120, input=input_text)


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nura-upgrade-")
        self.root = pathlib.Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.root / "etc/apk").mkdir(parents=True)
        (self.root / "proc/sys/kernel/random").mkdir(parents=True)
        (self.root / "etc/apk/world").write_text(WORLD)
        (self.root / "proc/sys/kernel/random/boot_id").write_text("boot-test\n")
        self.info_before = self.root / "info-before.txt"
        self.info_after = self.root / "info-after.txt"
        self.simulate = self.root / "simulate.txt"
        self.upgrade_out = self.root / "upgrade.out"
        self.calls = self.root / "calls.txt"
        self.upgraded = self.root / "upgraded"
        self.info_before.write_text(INFO_BEFORE)
        self.info_after.write_text(INFO_AFTER)
        self.simulate.write_text(SIMULATE)
        self.upgrade_out.write_text("")

        self.fake("id", "#!/bin/sh\n"
                        'if [ "${1:-}" = "-u" ]; then\n'
                        '    printf "0\\n"\n'
                        "else\n"
                        '    exec /usr/bin/id "$@"\n'
                        "fi\n")
        self.fake("uname", "#!/bin/sh\necho 7.0.0-test\n")
        self.fake("apk", FAKE_APK)

    def tearDown(self):
        self._tmp.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def env(self, **overrides):
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "NURA_UPGRADE_ROOT": str(self.root),
            "NURA_UPGRADE_STATE": str(self.root / "var/lib/nura/upgrade"),
            "NURA_APK_WORLD": str(self.root / "etc/apk/world"),
            "NURA_GPU_SOAK_STATE": str(self.root / "var/lib/nura/gpu-soak"),
            "NURA_UPGRADE_BOOT_ID": str(self.root / "proc/sys/kernel/random/boot_id"),
            "APK_CALLS": str(self.calls),
            "APK_INFO_BEFORE": str(self.info_before),
            "APK_INFO_AFTER": str(self.info_after),
            "APK_SIMULATE_OUT": str(self.simulate),
            "APK_UPGRADE_OUT": str(self.upgrade_out),
            "APK_UPGRADED": str(self.upgraded),
        }
        env.update({key: str(value) for key, value in overrides.items()})
        return env

    def run_upgrade(self, *args, input_text=None, **overrides):
        return run([str(UPGRADE), *args], self.env(**overrides), input_text)

    def snapshots(self):
        state = self.root / "var/lib/nura/upgrade"
        return sorted(path for path in state.iterdir() if path.is_dir()) \
            if state.is_dir() else []

    def one_snapshot(self):
        snaps = self.snapshots()
        self.assertEqual(len(snaps), 1, snaps)
        return snaps[0]

    def call_log(self):
        return self.calls.read_text().splitlines() if self.calls.is_file() else []

    def test_simulate_reports_sensitive_changes_and_writes_before(self):
        result = self.run_upgrade("--simulate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mesa: 26.2.6-r0 -> 26.3.0-r0", result.stdout)
        self.assertIn("phoc: 0.58.0-r100 -> 0.58.0-r101", result.stdout)
        self.assertNotIn("curl:", result.stdout)
        self.assertIn("simulated package changes: 3", result.stdout)
        self.assertIn("--simulate", result.stdout)
        self.assertEqual(self.call_log(), ["info -v", "update", "upgrade --simulate"])

        snap = self.one_snapshot()
        before = (snap / "before.txt").read_text()
        self.assertIn("uname_r=7.0.0-test", before)
        self.assertIn("## /etc/apk/world", before)
        self.assertIn("phoc=0.58.0-r100", before)
        self.assertIn("## apk info -v (sorted)", before)
        self.assertIn("mesa-26.2.6-r0", before)
        self.assertTrue((snap / "before-versions.txt").is_file())
        sim = (snap / "simulate.txt").read_text()
        self.assertIn("### apk update", sim)
        self.assertIn("### apk upgrade --simulate", sim)
        self.assertFalse((snap / "after.txt").exists())

    def test_pinned_conflict_is_refused_without_upgrade(self):
        self.simulate.write_text(CONFLICT)
        result = self.run_upgrade("--yes", APK_SIMULATE_RC=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocking packages", result.stdout)
        self.assertIn("phoc", result.stdout)
        self.assertIn("linux-postmarketos-qcom-sm8150", result.stdout)
        self.assertIn("Rebuild", result.stdout)
        self.assertEqual(self.call_log(), ["info -v", "update", "upgrade --simulate"])
        snap = self.one_snapshot()
        self.assertTrue((snap / "before.txt").is_file())
        self.assertTrue((snap / "simulate.txt").is_file())
        self.assertFalse((snap / "upgrade.txt").exists())

    def test_yes_path_upgrades_and_writes_after_and_changed(self):
        result = self.run_upgrade("--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("apply these changes?", result.stdout)
        self.assertIn("Reboot", result.stdout)
        self.assertEqual(self.call_log(),
                         ["info -v", "update", "upgrade --simulate",
                          "upgrade", "info -v"])
        snap = self.one_snapshot()
        self.assertTrue((snap / "upgrade.txt").is_file())
        self.assertTrue((snap / "after.txt").is_file())
        changed = (snap / "changed.txt").read_text()
        self.assertIn("mesa 26.2.6-r0 26.3.0-r0", changed)
        self.assertIn("phoc 0.58.0-r100 0.58.0-r101", changed)
        self.assertIn("mesa-26.3.0-r0", (snap / "after.txt").read_text())

    def test_prompt_yes_runs_upgrade_and_prompt_no_aborts(self):
        yes = self.run_upgrade(input_text="y\n")
        self.assertEqual(yes.returncode, 0, yes.stderr)
        self.assertIn("upgrade", self.call_log())

        # A second run in the same root: reset the marker so the fake apk
        # reports the before list again, and abort at the prompt.
        self.upgraded.unlink()
        self.calls.unlink()
        no = self.run_upgrade(input_text="n\n")
        self.assertEqual(no.returncode, 0, no.stderr)
        self.assertIn("aborted", no.stdout)
        self.assertNotIn("upgrade", self.call_log())

    def test_open_soak_snapshot_warns(self):
        soak = self.root / "var/lib/nura/gpu-soak/20261114T000000Z"
        soak.mkdir(parents=True)
        (soak / "start.txt").write_text("started=now\n")
        result = self.run_upgrade("--simulate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARNING", result.stdout)
        self.assertIn("GPU soak snapshot is still open", result.stdout)
        self.assertIn("invalidates", result.stdout)

        (soak / "stop.txt").write_text("stopped=now\n")
        self.root.joinpath("var/lib/nura/upgrade").rename(
            self.root / "var/lib/nura/upgrade-first")
        result = self.run_upgrade("--simulate")
        self.assertNotIn("WARNING", result.stdout)

    def test_root_is_required(self):
        self.fake("id", "#!/bin/sh\necho 10000\n")
        result = self.run_upgrade("--simulate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be run as root", result.stderr)
        self.assertFalse((self.root / "var/lib/nura/upgrade").exists())

    def test_update_failure_stops_before_simulate(self):
        result = self.run_upgrade("--yes", APK_UPDATE_RC=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("apk update failed", result.stdout)
        self.assertEqual(self.call_log(), ["info -v", "update"])
        snap = self.one_snapshot()
        self.assertTrue((snap / "before.txt").is_file())
        self.assertTrue((snap / "simulate.txt").is_file())

    def test_no_changes_stops_after_report(self):
        self.simulate.write_text("")
        result = self.run_upgrade()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nothing to upgrade", result.stdout)
        self.assertEqual(self.call_log(), ["info -v", "update", "upgrade --simulate"])

    def test_unknown_option_is_refused(self):
        result = self.run_upgrade("--dry-run")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)


class PackagingTest(unittest.TestCase):
    def test_device_package_installs_nura_upgrade(self):
        text = APKBUILD.read_text()
        self.assertRegex(text, r"(?m)^pkgrel=23$")
        self.assertIn("nura-upgrade", text)
        self.assertIn('"$pkgdir/usr/bin/nura-upgrade"', text)
        self.assertTrue(os.access(UPGRADE, os.X_OK))

    def test_source_and_checksum_order(self):
        text = APKBUILD.read_text()
        src = re.search(r'^source="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        names = [line.strip().split("::")[-1].split("/")[-1]
                 for line in src.splitlines() if line.strip()]
        block = re.search(r'^sha512sums="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        pairs = [line.split() for line in block.splitlines() if line.strip()]
        sums = [pair[1] for pair in pairs if len(pair) == 2]
        self.assertIn("nura-upgrade", names)
        self.assertIn("nura-upgrade", sums)
        index = names.index("nura-upgrade")
        self.assertEqual(pairs[index][0], sha512(UPGRADE))
        self.assertLess(index, names.index("nura-gpu.sh"))

    def test_shell_syntax(self):
        busybox = next((p for p in (shutil.which("busybox"),
                                     "/usr/lib/initcpio/busybox",
                                     "/bin/busybox")
                        if p and os.access(p, os.X_OK)), None)
        subprocess.run(["sh", "-n", str(UPGRADE)], check=True)
        if busybox:
            subprocess.run([busybox, "sh", "-n", str(UPGRADE)], check=True)
        if shutil.which("dash"):
            subprocess.run(["dash", "-n", str(UPGRADE)], check=True)


if __name__ == "__main__":
    unittest.main()
