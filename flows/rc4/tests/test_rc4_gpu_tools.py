#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Unit tests for nura-gpu-mode and nura-gpu-soak (device-oneplus-guacamole).

Both scripts are plain `sh`, so they are exercised directly against a fake
root.  No root, phone, systemd, journald or dmesg access is needed:

  * nura-gpu-mode status/default/sysmem/inorder, the single state file, the
    systemd user environment.d drop-in, the /etc/profile.d bridge, and the
    refusal to change the mode without root;
  * nura-gpu-soak start/stop with fake dmesg/journalctl, apk, coredumpctl and
    uname: identity and counters in start.txt, the delta and one-line SUMMARY
    in stop.txt, the dmesg->journalctl fallback, and the no-start error;
  * the device package installs the scripts, the session bridge and both
    log-level drop-ins, with matching sha512sums;
  * `sh -n` (and `busybox sh -n`/`dash -n` when installed) accepts both.

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
MODE = DEVICE / "nura-gpu-mode"
SOAK = DEVICE / "nura-gpu-soak"
PROFILE = DEVICE / "nura-gpu.sh"
LOGCONF = DEVICE / "90-nura-loglevel.conf"
APKBUILD = DEVICE / "APKBUILD"

KLOG_START = """\
[    1.000000] panel-samsung-oneplus-dsc ae94000.dsi.0: initializing panel at 90 Hz (control-display 0x30)
[    2.000000] guacamole kernel: *** gpu fault: ttbr0=0 iova=1 dir=READ type=TRANSLATION source=CCU (0,0,0,1)
[    3.000000] guacamole kernel: *** gpu fault: ttbr0=0 iova=2 dir=READ type=TRANSLATION source=CCU (0,0,0,1)
[    4.000000] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.4.0.1: hangcheck recover!
"""

KLOG_STOP_EXTRA = """\
[    5.000000] guacamole kernel: *** gpu fault: ttbr0=0 iova=3 dir=READ type=TRANSLATION source=CCU (0,0,0,1)
[    6.000000] guacamole kernel: *** gpu fault: ttbr0=0 iova=4 dir=READ type=TRANSLATION source=CCU (0,0,0,1)
[    7.000000] guacamole kernel: *** gpu fault: ttbr0=0 iova=5 dir=READ type=TRANSLATION source=CCU (0,0,0,1)
[    8.000000] msm_dpu ae01000.display-controller: [drm:recover_worker] *ERROR* 6.4.0.1: hangcheck recover!
"""

VERSIONS = """\
device-oneplus-guacamole-4-r20
linux-postmarketos-qcom-sm8150-7.0.0-r0
mesa-26.2.6-r0
mesa-dri-gallium-26.2.6-r0
phoc-0.58.0-r0
phosh-99990.58.0-r0
"""

COREDUMPS_EMPTY = "TIME PID UID GID SIG COREFILE EXE\n"
COREDUMPS_ONE = (COREDUMPS_EMPTY +
                 "Nov 14 12:00:00 123 10000 10000 11 present /usr/bin/phoc\n")


def sha512(path):
    return hashlib.sha512(pathlib.Path(path).read_bytes()).hexdigest()


def sh_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def run(cmd, env):
    base = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    base.update(env)
    return subprocess.run(cmd, env=base, capture_output=True, text=True,
                          timeout=120)


class FakeRootMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nura-gpu-tools-")
        self.root = pathlib.Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def fake_id(self, uid):
        self.fake("id", "#!/bin/sh\n"
                        'if [ "${1:-}" = "-u" ]; then\n'
                        '    printf "%%s\\n" %s\n'
                        "else\n"
                        '    exec /usr/bin/id "$@"\n'
                        "fi\n" % sh_quote(uid))


class GpuModeTest(FakeRootMixin, unittest.TestCase):
    def mode_env(self, uid):
        self.fake_id(uid)
        return {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "NURA_GPU_MODE_ROOT": str(self.root),
        }

    def run_mode(self, *args, uid=0):
        return run([str(MODE), *args], self.mode_env(uid))

    def state(self):
        return self.root / "var/lib/nura/gpu-mode"

    def dropin(self):
        return self.root / "etc/environment.d/90-nura-gpu.conf"

    def profile(self, state=None):
        state = state or self.state()
        return run(["sh", "-c", '. "$1"; printf "%s" "${FD_MESA_DEBUG-}"',
                    "sh", str(PROFILE)],
                   {"PATH": "/usr/bin:/bin",
                    "NURA_GPU_MODE_STATE": str(state)})

    def test_status_default_and_no_root_needed(self):
        result = self.run_mode("status", uid=10000)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("configured=default", result.stdout)
        self.assertIn("rendering=default", result.stdout)
        self.assertIn("re-login or reboot", result.stdout)
        self.assertFalse(self.state().exists())

    def test_sysmem_writes_one_state_and_user_dropin(self):
        result = self.run_mode("sysmem")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state().read_text(), "sysmem\n")
        self.assertEqual(self.dropin().read_text(), "FD_MESA_DEBUG=sysmem\n")
        self.assertIn("re-login or reboot", result.stdout)
        self.assertEqual(self.profile().stdout, "sysmem")
        status = self.run_mode("status", uid=10000)
        self.assertIn("configured=sysmem", status.stdout)

    def test_inorder_replaces_sysmem_and_never_combines(self):
        self.assertEqual(self.run_mode("sysmem").returncode, 0)
        self.assertEqual(self.run_mode("inorder").returncode, 0)
        self.assertEqual(self.state().read_text(), "inorder\n")
        self.assertEqual(self.dropin().read_text(), "FD_MESA_DEBUG=inorder\n")
        self.assertNotIn("sysmem", self.state().read_text())
        self.assertNotIn("sysmem", self.dropin().read_text())
        self.assertEqual(self.profile().stdout, "inorder")

    def test_default_removes_state_and_dropin(self):
        self.assertEqual(self.run_mode("sysmem").returncode, 0)
        self.assertTrue(self.state().exists())
        self.assertTrue(self.dropin().exists())
        result = self.run_mode("default")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.state().exists())
        self.assertFalse(self.dropin().exists())
        self.assertEqual(self.profile().stdout, "")
        self.assertIn("configured=default", self.run_mode("status").stdout)

    def test_non_root_cannot_change_mode(self):
        result = self.run_mode("sysmem", uid=10000)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be run as root", result.stderr)
        self.assertFalse(self.state().exists())
        self.assertFalse(self.dropin().exists())

    def test_unknown_mode_is_refused(self):
        result = self.run_mode("nogmem")
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)

    def test_profile_bridge_preserves_an_existing_value_when_default(self):
        result = self.profile()
        self.assertEqual(result.stdout, "")
        # No state file is the normal-rendering default.  If the user set the
        # variable elsewhere, the bridge must not unset it.
        child = run(["sh", "-c", '. "$1"; printf "%s" "${FD_MESA_DEBUG-}"',
                     "sh", str(PROFILE)],
                    {"PATH": "/usr/bin:/bin",
                     "NURA_GPU_MODE_STATE": str(self.state()),
                     "FD_MESA_DEBUG": "keep-me"})
        self.assertEqual(child.stdout, "keep-me")


class GpuSoakTest(FakeRootMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.root / "proc/sys/kernel/random").mkdir(parents=True)
        (self.root / "proc/sys/kernel/random/boot_id").write_text("boot-1234\n")
        self.state = self.root / "var/lib/nura/gpu-soak"
        self.crash = self.root / "var/lib/nura/gpu-crash"
        self.crash.mkdir(parents=True)
        self.mode_state = self.root / "var/lib/nura/gpu-mode"
        self.klog = self.root / "klog.txt"
        self.coredumps = self.root / "coredumps.txt"
        self.klog.write_text(KLOG_START)
        self.coredumps.write_text(COREDUMPS_EMPTY)

        self.fake("dmesg", "#!/bin/sh\ncat %s\n" % sh_quote(self.klog))
        self.fake("journalctl",
                  "#!/bin/sh\n"
                  'case "${1:-}" in\n'
                  "  -k) cat %s ;;\n"
                  "  *) cat %s ;;\n"
                  "esac\n" % (sh_quote(self.klog), sh_quote(self.coredumps)))
        self.fake("apk", "#!/bin/sh\ncat <<'EOF'\n%sEOF\n" % VERSIONS)
        self.fake("coredumpctl", "#!/bin/sh\ncat %s\n" % sh_quote(self.coredumps))
        self.fake("uname", "#!/bin/sh\n"
                           "echo 'Linux guacamole 7.0.0-sm8150-guacamole-rc4 aarch64'\n")

    def soak_env(self):
        return {
            "PATH": "%s:/usr/bin:/bin" % self.bin,
            "NURA_GPU_SOAK_ROOT": str(self.root),
            "NURA_GPU_SOAK_STATE": str(self.state),
            "NURA_GPU_MODE_STATE": str(self.mode_state),
            "NURA_GPU_CRASH_STATE": str(self.crash),
        }

    def run_soak(self, *args):
        return run([str(SOAK), *args], self.soak_env())

    def snapshots(self):
        return sorted(p for p in self.state.iterdir() if p.is_dir()) \
            if self.state.is_dir() else []

    def start_snapshot(self):
        result = self.run_soak("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        dirs = self.snapshots()
        self.assertEqual(len(dirs), 1, dirs)
        return dirs[0]

    def test_start_records_identity_mode_and_counters(self):
        self.mode_state.parent.mkdir(parents=True, exist_ok=True)
        self.mode_state.write_text("sysmem\n")
        snap = self.start_snapshot()
        start = (snap / "start.txt").read_text()
        for line in ("boot_id=boot-1234", "kernel=7.0.0-r0",
                     "mesa=26.2.6-r0", "phoc=0.58.0-r0",
                     "phosh=99990.58.0-r0", "device=4-r20",
                     "gpu_mode=sysmem", "refresh_hz=90",
                     "kernel_log=dmesg", "gpu_faults=2",
                     "hangcheck_recovers=1", "devcoredump_captures=0"):
            self.assertIn(line, start)
        versions = (snap / "versions.txt").read_text()
        self.assertIn("linux-postmarketos-qcom-sm8150-7.0.0-r0", versions)
        self.assertNotIn("mesa-dri-gallium", versions,
                         "only the main mesa package is recorded")

    def test_stop_reports_delta_and_one_line_summary(self):
        snap = self.start_snapshot()
        self.klog.write_text(KLOG_START + KLOG_STOP_EXTRA)
        (self.crash / "20261114T120000Z-devcd0").mkdir()
        self.coredumps.write_text(COREDUMPS_ONE)

        result = self.run_soak("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = (snap / "stop.txt").read_text()
        for line in ("gpu_faults=5", "hangcheck_recovers=2",
                     "delta_gpu_faults=3", "delta_hangcheck_recovers=1",
                     "delta_coredumps=1", "delta_devcoredump_captures=1",
                     "same_boot=yes", "log_rotated=no", "captures_pruned=no"):
            self.assertIn(line, stop)
        summary = [l for l in (snap / "stop.txt").read_text().splitlines()
                   if l.startswith("summary=")][0]
        self.assertIn("gpu_faults=+3", summary)
        self.assertIn("hangcheck=+1", summary)
        self.assertIn("coredumps=+1", summary)
        self.assertIn("devcoredumps=+1", summary)
        self.assertIn("mode=default", summary)
        self.assertIn("refresh_hz=90", summary)
        # The human output carries the same single pasteable line.
        self.assertIn("SUMMARY ", result.stdout)
        self.assertIn("gpu_faults=+3", result.stdout)

    def test_stop_without_start_is_an_error(self):
        self.state.mkdir(parents=True)
        result = self.run_soak("stop")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no open snapshot", result.stderr)

    def test_empty_dmesg_falls_back_to_journalctl(self):
        self.fake("dmesg", "#!/bin/sh\nexit 0\n")
        snap = self.start_snapshot()
        start = (snap / "start.txt").read_text()
        self.assertIn("kernel_log=journalctl", start)
        self.assertIn("gpu_faults=2", start)
        self.assertIn("hangcheck_recovers=1", start)

    def test_coredump_journal_fallback_when_coredumpctl_is_absent(self):
        (self.bin / "coredumpctl").unlink()
        self.coredumps.write_text(
            "Nov 14 12:00:00 guacamole systemd-coredump[1]: "
            "Process 123 (phoc) of user 10000 dumped core.\n")
        snap = self.start_snapshot()
        result = self.run_soak("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("delta_coredumps=1", (snap / "stop.txt").read_text())

    def test_unknown_gpu_mode_is_reported_as_default(self):
        snap = self.start_snapshot()
        self.assertIn("gpu_mode=default", (snap / "start.txt").read_text())


class PackagingTest(unittest.TestCase):
    def test_device_package_installs_the_tools(self):
        text = APKBUILD.read_text()
        self.assertRegex(text, r"(?m)^pkgrel=22$")
        for name in (str(MODE), str(SOAK), str(PROFILE), str(LOGCONF)):
            self.assertTrue(os.access(name, os.R_OK), name)
        self.assertTrue(os.access(MODE, os.X_OK), "nura-gpu-mode is not executable")
        self.assertTrue(os.access(SOAK, os.X_OK), "nura-gpu-soak is not executable")

        for path in ("/usr/bin/nura-gpu-mode", "/usr/bin/nura-gpu-soak",
                     "/etc/profile.d/nura-gpu.sh",
                     "/etc/environment.d",
                     "/var/lib/nura",
                     "/etc/systemd/system.conf.d/90-nura-loglevel.conf",
                     "/etc/systemd/user.conf.d/90-nura-loglevel.conf"):
            self.assertIn(path, text, path)

    def test_source_and_checksum_order_matches(self):
        text = APKBUILD.read_text()
        src = re.search(r'^source="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        names = [l.strip().split("::")[-1].split("/")[-1]
                 for l in src.splitlines() if l.strip()]
        block = re.search(r'^sha512sums="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        pairs = [l.split() for l in block.splitlines() if l.strip()]
        sums = [p[1] for p in pairs if len(p) == 2]
        # The variable-expanded entries are resolved by abuild; for the files
        # this package ships the order and the digest must match exactly.
        wanted = ["nura-gpu-mode", "nura-gpu-soak", "nura-gpu.sh",
                  "90-nura-loglevel.conf"]
        self.assertEqual([n for n in names if n in wanted], wanted)
        self.assertEqual([n for n in sums if n in wanted], wanted)
        sums_by_name = {p[1]: p[0] for p in pairs if len(p) == 2}
        for name in wanted:
            self.assertEqual(sums_by_name[name], sha512(DEVICE / name), name)

    def test_all_device_sources_have_matching_sha512(self):
        # Same shape as the kernel recipe check in docs/KERNEL_PATCHES.md:
        # one source entry per sha512 line, in the same order.
        text = APKBUILD.read_text()
        src = re.search(r'^source="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        names = []
        for line in src.splitlines():
            if not line.strip():
                continue
            name = line.strip().split("::")[-1].split("/")[-1]
            name = name.replace("$pkgname", "device-oneplus-guacamole")
            name = name.replace("$_sensors", "guacamole-sensors-20261102")
            names.append(name)
        block = re.search(r'^sha512sums="\n(.*?)\n"\n', text, re.S | re.M).group(1)
        sums = [line.split() for line in block.splitlines() if line.strip()]
        self.assertEqual(len(names), len(sums), "one sha512 per source entry")
        self.assertEqual([s[1] for s in sums], names,
                         "source= and sha512sums= must stay in the same order")
        for digest, name in sums:
            path = DEVICE / name
            if not path.is_file():
                # The sensor bundle is generated by scripts/prepare_sensor_bundle.py
                # and is deliberately not in git.
                self.assertTrue(name.endswith(".tar.gz"), "%s is missing" % name)
                continue
            self.assertEqual(digest, sha512(path), name)

    def test_log_level_dropin_is_info_in_both_managers(self):
        text = LOGCONF.read_text()
        self.assertIn("[Manager]", text)
        self.assertIn("ManagerEnvironment=SYSTEMD_LOG_LEVEL=info", text)
        self.assertIn("DefaultEnvironment=SYSTEMD_LOG_LEVEL=info", text)
        self.assertNotIn("SYSTEMD_LOG_LEVEL=debug", text)

    def test_scripts_pass_shell_syntax_checks(self):
        # Some distributions keep the recovery busybox outside PATH (Arch:
        # /usr/lib/initcpio/busybox); test it when it is present.
        busybox = next((p for p in (shutil.which("busybox"),
                                     "/usr/lib/initcpio/busybox",
                                     "/bin/busybox")
                        if p and os.access(p, os.X_OK)), None)
        for script in (MODE, SOAK, PROFILE):
            subprocess.run(["sh", "-n", str(script)], check=True)
            if busybox:
                subprocess.run([busybox, "sh", "-n", str(script)], check=True)
            if shutil.which("dash"):
                subprocess.run(["dash", "-n", str(script)], check=True)


if __name__ == "__main__":
    unittest.main()
