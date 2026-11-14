#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Unit tests for the rc4i GPU crash collector (device-oneplus-guacamole).

The collector is a plain `sh` script, so it is exercised directly, against a
fake sysfs tree: `<tmp>/sys/class/devcoredump/devcd0/data` is a regular file
whose content stands in for the dump drm/msm generates on a GPU hang, and
`<tmp>/sys/class/devcoredump/devcd0/failing_device` is a symlink like the one
devcoredump.c creates.  `dmesg` comes from a fake bin directory prepended to
PATH.  Nothing here needs root, a phone, sysfs, systemd or udev.

What is checked:
  * the dump is copied verbatim, the dmesg tail is bounded to 200 lines, INFO
    records device/bytes/released, and the fake data file receives the "1"
    that releases the dump (so the next hang is not discarded);
  * a second run with the dump already released collects nothing new;
  * an unreadable/empty dump is left armed instead of being released with no
    copy on disk;
  * retention keeps the newest 10 captures and enforces the total size cap,
    never deleting the capture it just wrote;
  * the shipped units agree with the script: udev rule -> oneshot service,
    timer backstop, preset enables the timer, and APKBUILD installs and
    checksums all five files.

Run: PYTHONPATH=flows/rc4/local-storage/scripts \\
     python3 -m unittest discover -s flows/rc4/tests -p 'test_*.py'
"""

import hashlib
import os
import pathlib
import re
import subprocess
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
RC4 = TESTS.parent
DEVICE = RC4 / "device/testing/device-oneplus-guacamole"
SCRIPT = DEVICE / "nura-gpu-crash-capture"
SERVICE = DEVICE / "nura-gpu-crash-capture.service"
TIMER = DEVICE / "nura-gpu-crash-capture.timer"
PRESET = DEVICE / "83-nura-gpu-crash.preset"
RULE = DEVICE / "90-nura-gpu-crash.rules"
APKBUILD = DEVICE / "APKBUILD"

DMESG_LINES = 250


def sha512(path):
    return hashlib.sha512(pathlib.Path(path).read_bytes()).hexdigest()


class CollectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nura-gpu-crash-")
        self.tmp = pathlib.Path(self._tmp.name)
        self.classdir = self.tmp / "sys/class/devcoredump"
        self.classdir.mkdir(parents=True)
        self.state = self.tmp / "state"
        self.fakebin = self.tmp / "bin"
        self.fakebin.mkdir()
        self._write_dmesg(DMESG_LINES)

    def tearDown(self):
        self._tmp.cleanup()

    # --- fixtures ---------------------------------------------------------
    def _write_dmesg(self, lines):
        path = self.fakebin / "dmesg"
        path.write_text(
            "#!/bin/sh\n"
            "i=0\n"
            "while [ \"$i\" -lt %d ]; do\n"
            "    echo \"dmesg line $i\"\n"
            "    i=$((i + 1))\n"
            "done\n" % lines
        )
        path.chmod(0o755)

    def add_dump(self, name="devcd0", content=b"register dump\n", device="ae94000.gpu"):
        devcd = self.classdir / name
        devcd.mkdir()
        (devcd / "data").write_bytes(content)
        os.symlink(device, devcd / "failing_device")
        return devcd

    def run_capture(self, **env_overrides):
        env = {
            "PATH": "%s:%s" % (self.fakebin, os.environ.get("PATH", "/usr/bin:/bin")),
            "NURA_GPU_CRASH_ROOT": str(self.tmp),
            "NURA_GPU_CRASH_STATE": str(self.state),
            "LC_ALL": "C",
        }
        for key, value in os.environ.items():
            if key.startswith("NURA_GPU_CRASH_"):
                continue
            env.setdefault(key, value)
        for key, value in env_overrides.items():
            env["NURA_GPU_CRASH_" + key] = str(value)
        return subprocess.run([str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=120)

    def captures(self):
        return sorted(p for p in self.state.iterdir() if p.is_dir())

    # --- tests ------------------------------------------------------------
    def test_capture_copies_dump_and_dmesg_and_releases(self):
        dump = b"registers\nring 0 fence\n" * 4
        devcd = self.add_dump(content=dump)

        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        dirs = self.captures()
        self.assertEqual(len(dirs), 1, dirs)
        capture = dirs[0]
        self.assertIn("devcd0", capture.name)

        self.assertEqual((capture / "devcoredump").read_bytes(), dump)

        dmesg = (capture / "dmesg.txt").read_text().splitlines()
        self.assertEqual(len(dmesg), 200, "dmesg tail must be bounded to 200 lines")
        self.assertEqual(dmesg[-1], "dmesg line %d" % (DMESG_LINES - 1))
        self.assertNotIn("dmesg line 0", dmesg)

        info = (capture / "INFO").read_text()
        self.assertIn("devcd=devcd0", info)
        self.assertIn("failing_device=ae94000.gpu", info)
        self.assertIn("devcoredump_bytes=%d" % len(dump), info)
        self.assertIn("truncated=no", info)
        self.assertIn("released=yes", info)

        # The release write is what lets the devcoredump framework delete the
        # sysfs device (and accept the next hang).  In the fake tree it shows
        # up as the new content of the data file.
        self.assertEqual((devcd / "data").read_bytes(), b"1")

    def test_released_dump_is_not_collected_twice(self):
        devcd = self.add_dump()
        self.assertEqual(self.run_capture().returncode, 0)
        self.assertEqual(len(self.captures()), 1)

        # The kernel deletes /sys/class/devcoredump/devcd<N>/ on release.
        (devcd / "data").unlink()
        (devcd / "failing_device").unlink()
        devcd.rmdir()

        result = self.run_capture()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(self.captures()), 1, "a released dump was recaptured")
        self.assertIn("no devcoredump present", result.stdout)

    def test_empty_dump_is_left_armed(self):
        devcd = self.add_dump(content=b"")

        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.captures(), [], "an empty read must not create a capture")
        self.assertEqual((devcd / "data").read_bytes(), b"",
                         "an empty dump must not be released")

    def test_missing_class_dir_is_a_noop(self):
        result = self.run_capture()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.captures(), [])
        self.assertIn("no devcoredump present", result.stdout)

    def test_retention_keeps_newest_ten(self):
        for i in range(12):
            old = self.state / ("20200101T0000%02dZ-devcd%d" % (i, i))
            old.mkdir(parents=True)
            (old / "devcoredump").write_bytes(b"old\n")
        self.add_dump()

        result = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        dirs = self.captures()
        self.assertEqual(len(dirs), 10, dirs)
        names = [p.name for p in dirs]
        self.assertTrue(any("devcd0" in n and not n.startswith("2020") for n in names),
                        "the new capture was removed: %s" % names)
        self.assertNotIn("20200101T000000Z-devcd0", names)
        self.assertNotIn("20200101T000001Z-devcd1", names)

    def test_total_size_cap(self):
        for i in range(3):
            old = self.state / ("20200101T0000%02dZ-devcd%d" % (i, i))
            old.mkdir(parents=True)
            (old / "devcoredump").write_bytes(b"x" * 4096)
        self.add_dump()

        result = self.run_capture(MAX_TOTAL_KIB=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        dirs = self.captures()
        # The cap (1 KiB) is below one capture, so every old capture goes and
        # the one just written stays.
        self.assertEqual(len(dirs), 1, dirs)
        self.assertIn("devcd0", dirs[0].name)
        self.assertNotIn("2020", dirs[0].name)

    def test_script_is_posix_sh_and_has_no_debugfs_dependency(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        self.assertNotIn("debugfs", re.sub(r"(?m)^\s*#.*$", "", text))
        self.assertTrue(os.access(SCRIPT, os.X_OK), "collector is not executable")

    def test_shipped_units_and_package_install(self):
        service = SERVICE.read_text()
        timer = TIMER.read_text()
        preset = PRESET.read_text()
        rule = RULE.read_text()
        apkbuild = APKBUILD.read_text()

        self.assertIn("ExecStart=/usr/libexec/nura-gpu-crash-capture", service)
        self.assertIn("StateDirectory=nura/gpu-crash", service)
        self.assertIn("ConditionPathExistsGlob=/sys/class/devcoredump/devcd*/data", service)
        # Releasing the dump is a write under /sys; systemd would break it.
        directives = "\n".join(l for l in service.splitlines()
                                if not l.lstrip().startswith("#"))
        self.assertNotIn("ProtectKernelTunables=yes", directives)
        self.assertNotIn("ProtectSystem=strict", directives)
        self.assertIn("ProtectSystem=full", directives)

        self.assertIn("Unit=nura-gpu-crash-capture.service", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertIn("enable nura-gpu-crash-capture.timer", preset)

        self.assertIn('SUBSYSTEM=="devcoredump"', rule)
        self.assertIn('ENV{SYSTEMD_WANTS}+="nura-gpu-crash-capture.service"', rule)

        for path in ("/usr/libexec/nura-gpu-crash-capture",
                     "/usr/lib/systemd/system/nura-gpu-crash-capture.service",
                     "/usr/lib/systemd/system/nura-gpu-crash-capture.timer",
                     "/usr/lib/systemd/system-preset/83-nura-gpu-crash.preset",
                     "/usr/lib/udev/rules.d/90-nura-gpu-crash.rules"):
            self.assertIn(path, apkbuild, path)

        sums = {}
        block = re.search(r'^sha512sums="\n(.*?)\n"\n', apkbuild, re.S | re.M).group(1)
        for line in block.splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1]] = parts[0]
        for name in ("nura-gpu-crash-capture", "nura-gpu-crash-capture.service",
                     "nura-gpu-crash-capture.timer", "83-nura-gpu-crash.preset",
                     "90-nura-gpu-crash.rules"):
            self.assertIn(name, sums, "%s is not in sha512sums" % name)
            self.assertEqual(sums[name], sha512(DEVICE / name), name)


if __name__ == "__main__":
    unittest.main()
