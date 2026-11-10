#!/usr/bin/env python3
"""Mocked, read-only tests for scripts/nura-probe.sh; no phone or hardware."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "nura-probe.sh"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def tree_state(root: Path) -> dict[str, tuple]:
    state: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        for name in dirnames:
            path = base / name
            state[str(path.relative_to(root))] = ("dir",)
        for name in filenames:
            path = base / name
            info = path.lstat()
            state[str(path.relative_to(root))] = (
                info.st_mode,
                info.st_size,
                info.st_mtime_ns,
                os.readlink(path) if path.is_symlink() else None,
            )
    return state


class ProbeTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "phone"
        self.mockbin = self.tmp / "mockbin"
        self.build_fixture()

    # --------------------------------------------------------------- fixtures

    def build_fixture(self) -> None:
        root = self.root
        write(root / "etc/os-release", 'PRETTY_NAME="postmarketOS test"\n')
        write(root / "proc/cmdline", "console=tty0 pmos.root=/dev/nbd0p2\n")
        write(root / "proc/uptime", "123.45 100.00\n")
        write(root / "proc/version", "Linux version 7.0.0-fake\n")
        write(root / "proc/loadavg", "1.00 0.50 0.25 1/100 1234\n")
        write(root / "proc/pressure/cpu", "some avg10=1.23 avg60=0.50 avg300=0.20 total=10\n")
        write(root / "proc/pressure/io", "some avg10=0.10 avg60=0.05 avg300=0.01 total=1\n")
        write(root / "proc/modules", "i2c_qcom_cci 20480 0 - Live 0x0000000000000000\nqcom_camss 430080 0 - Live 0x0000000000000000\n")
        write(root / "proc/1234/comm", "nura-camera\n")
        write_bytes(root / "proc/1234/cmdline", b"nura-camera\0--preview\0")
        write(root / "proc/1234/maps", "\n".join(
            (
                "7f0000000000-7f0000001000 r-xp 00000000 fe:00 1234 /opt/nura-rear-camera/lib/libcamera.so.0.7.2",
                "7f0000001000-7f0000002000 r-xp 00000000 fe:00 1234 /usr/lib/libpipewire-0.3.so.0",
            )
        ) + "\n")
        write_bytes(
            root / "proc/1234/environ",
            b"LD_LIBRARY_PATH=/opt/nura-rear-camera/lib\0SECRET=do-not-print\0XDG_SESSION_TYPE=wayland\0",
        )
        (root / "proc/1234/fd").mkdir(parents=True, exist_ok=True)
        os.symlink("/dev/video0", root / "proc/1234/fd/5")
        os.symlink("/usr/bin/nura-camera", root / "proc/1234/exe")
        write(root / "dev/video0", "")
        write(root / "dev/media0", "")
        write(root / "sys/class/video4linux/video0/name", "imx586\n")

        cci = "/usr/lib/modules/7.0.0-fake/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
        write_bytes(root / cci.lstrip("/"), b"fake-cci-module")
        write_bytes(root / "opt/nura-rear-camera/lib/libcamera.so.0.7.2", b"fake-private-libcamera")

        write(root / "sys/class/thermal/thermal_zone0/type", "cpu-0\n")
        write(root / "sys/class/thermal/thermal_zone0/temp", "42000\n")
        write(root / "sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq", "1800000\n")
        write(root / "sys/devices/system/cpu/cpufreq/policy0/scaling_min_freq", "300000\n")
        write(root / "sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq", "2800000\n")
        write(root / "sys/devices/system/cpu/cpufreq/policy0/scaling_governor", "schedutil\n")
        write(root / "sys/class/devfreq/kgsl-3d0/name", "kgsl-3d0\n")
        write(root / "sys/class/devfreq/kgsl-3d0/cur_freq", "500000000\n")
        write(root / "sys/class/devfreq/kgsl-3d0/min_freq", "200000000\n")
        write(root / "sys/class/devfreq/kgsl-3d0/max_freq", "600000000\n")
        write(root / "sys/class/devfreq/kgsl-3d0/governor", "msm-v2\n")

        write(root / "sys/class/power_supply/battery/type", "Battery\n")
        write(root / "sys/class/power_supply/battery/status", "Charging\n")
        write(root / "sys/class/power_supply/battery/capacity", "55\n")
        write(root / "sys/class/power_supply/battery/voltage_now", "4000000\n")
        write(root / "sys/class/power_supply/usb/type", "USB\n")
        write(root / "sys/class/power_supply/usb/online", "1\n")
        write(root / "sys/class/power_supply/usb/input_current_limit", "1800000\n")
        write(root / "run/guacamole/charger", "DCP\n")

        write(root / "sys/power/state", "freeze mem\n")
        write(root / "sys/power/mem_sleep", "s2idle [deep]\n")
        write(root / "sys/power/wakeup_count", "0\n")
        write(root / "sys/power/autosleep", "off\n")
        write(root / "sys/power/suspend_stats/success", "3\n")
        write(root / "sys/power/suspend_stats/fail", "0\n")
        write(root / "sys/power/suspend_stats/last_failed_dev", "\n")
        write(root / "sys/class/net/wlan0/operstate", "up\n")
        write(root / "sys/class/bluetooth/hci0/address", "00:11:22:33:44:55\n")
        write(root / "home/user/Videos/main-scene.mp4", "not a real video\n")

        self.mockbin.mkdir()
        command = self.mockbin / "mockcmd"
        command.write_text(
            "#!/bin/sh\n"
            "name=${0##*/}\n"
            "case \"$name\" in\n"
            "  uname)\n"
            "    case \"${1:-}\" in\n"
            "      -r) printf '%s\\n' \"${FAKE_RELEASE:-7.0.0-fake}\" ;;\n"
            "      -v) printf '%s\\n' '#1 SMP PREEMPT' ;;\n"
            "      -a) printf 'Linux (none) %s #1 SMP PREEMPT\\n' \"${FAKE_RELEASE:-7.0.0-fake}\" ;;\n"
            "    esac ;;\n"
            "  apk)\n"
            "    if [ \"${1:-}\" = info ] && [ \"${2:-}\" = -v ]; then\n"
            "      printf 'libcamera-99990.8.0-r1\\npipewire-spa-libcamera-1.6.11-r0\\n'\n"
            "    elif [ \"${1:-}\" = info ] && [ \"${2:-}\" = -W ]; then\n"
            "      printf '%s is owned by libcamera-rear-0.7.2-r0\\n' \"${3:-}\"\n"
            "    fi ;;\n"
            "  dmesg) printf '[    0.000000] i2c-qcom-cci 0c1c000.i2c: CCI HW version = 0x00030000\\n[    0.100000] camss: probe ok\\n' ;;\n"
            "  ps) printf '  PID  PPID %%CPU %%MEM ELAPSED COMMAND\\n  1234     1  2.0  1.0 00:10:00 nura-camera\\n' ;;\n"
            "  modinfo) printf '%s\\n' \"${FAKE_CCI_PATH:-/usr/lib/modules/7.0.0-fake/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst}\" ;;\n"
            "  journalctl) printf 'Nov 10 12:00:00 nura kernel: PM: suspend entry (deep)\\n' ;;\n"
            "  media-ctl|v4l2-ctl|top) printf 'mock %s\\n' \"$name\" ;;\n"
            "  *) exit 127 ;;\n"
            "esac\n"
        )
        command.chmod(0o755)
        for name in ("uname", "apk", "dmesg", "ps", "modinfo", "journalctl", "media-ctl", "v4l2-ctl", "top"):
            os.symlink("mockcmd", self.mockbin / name)

    def run_probe(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "PATH": f"{self.mockbin}:/usr/bin:/bin",
            "NURA_ROOT": str(self.root),
            "HOME": "/home/user",
            "FAKE_RELEASE": "7.0.0-fake",
            "FAKE_CCI_PATH": "/usr/lib/modules/7.0.0-fake/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst",
        }
        return subprocess.run(
            ["sh", str(PROBE), *args],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

    # ------------------------------------------------------------------ tests

    def test_syntax_and_help(self) -> None:
        subprocess.run(["sh", "-n", str(PROBE)], check=True)
        result = self.run_probe("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage: sh nura-probe.sh", result.stdout)
        self.assertIn("--watch", result.stdout)

    def test_fixture_snapshot(self) -> None:
        before = tree_state(self.root)
        result = self.run_probe()
        after = tree_state(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("@@META@@", out)
        self.assertIn("kernel_release=7.0.0-fake", out)
        self.assertIn("@@CAMERA_PROCESSES@@", out)
        self.assertIn("PROCESS pid=1234 comm=nura-camera", out)
        self.assertIn("MAP /opt/nura-rear-camera/lib/libcamera.so.0.7.2", out)
        self.assertIn("LOADED /opt/nura-rear-camera/lib/libcamera.so.0.7.2", out)
        self.assertIn("@@CHARGING@@", out)
        self.assertIn("status=Charging", out)
        self.assertIn("@@SUSPEND@@", out)
        self.assertIn("fail=0", out)
        self.assertIn("@@END@@", out)
        self.assertNotIn("SECRET", out)
        self.assertNotIn("ENV LD_LIBRARY_PATH=", out)
        self.assertNotIn('$_p/environ', PROBE.read_text())
        self.assertNotIn("PASS", out)
        self.assertEqual(before, after, "probe changed the fixture tree")

    def test_watch_samples_without_starting_capture(self) -> None:
        result = self.run_probe("--watch", "1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("@@WATCH@@", result.stdout)
        self.assertIn("@@WATCH_END@@", result.stdout)
        rows = [
            line for line in result.stdout.splitlines()
            if line and line[0].isdigit() and len(line.split()) == 14
        ]
        self.assertTrue(rows, result.stdout)
        self.assertIn("schedutil", rows[0])
        self.assertIn("Charging", rows[0])

    def test_missing_file_is_marked_missing(self) -> None:
        (self.root / "sys/power/state").unlink()
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[MISSING] /sys/power/state", result.stdout)

    @unittest.skipIf(os.geteuid() == 0, "chmod 000 does not deny root")
    def test_denied_file_is_marked_denied(self) -> None:
        fail = self.root / "sys/power/suspend_stats/fail"
        os.chmod(fail, 0o000)
        self.addCleanup(os.chmod, fail, 0o644)
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fail=[DENIED]", result.stdout)

    def test_no_camera_process_is_not_observed(self) -> None:
        shutil.rmtree(self.root / "proc/1234")
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[NOT_OBSERVED]", result.stdout)
        self.assertNotIn("PROCESS pid=1234", result.stdout)

    def test_invalid_watch_value_fails(self) -> None:
        result = self.run_probe("--watch", "soon")
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-negative integer", result.stderr)


if __name__ == "__main__":
    unittest.main()
