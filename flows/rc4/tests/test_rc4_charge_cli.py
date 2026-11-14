"""Charge CLI checks; systemd and privilege are mocked, no hardware writes."""
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch

DEVICE = Path(__file__).resolve().parents[1] / "device/testing/device-oneplus-guacamole"
DAEMON = DEVICE / "guacamole-chgctrl.daemon"


class ChargeCliTest(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("rc4_charge_cli", str(DAEMON))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.daemon = importlib.util.module_from_spec(spec)
        loader.exec_module(self.daemon)

    def test_valid_window_is_persisted_before_restart(self):
        with patch.object(self.daemon.os, "geteuid", return_value=0), \
             patch.object(self.daemon.subprocess, "run") as run, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.daemon.set_limits(60, 85)
        self.assertEqual(run.call_args_list, [
            call(["systemctl", "edit", "--stdin", "--drop-in=80-nura-charge-window.conf",
                  "guacamole-chgctrl.service"],
                 input="[Service]\nEnvironment=CHG_MIN=60 CHG_MAX=85\n", text=True, check=True),
            call(["systemctl", "restart", "guacamole-chgctrl.service"], check=True),
            call(["systemctl", "is-active", "--quiet", "guacamole-chgctrl.service"], check=True),
        ])
        self.assertIn("stop at 85%; resume at 60%", output.getvalue())

    def test_invalid_windows_do_not_touch_systemd(self):
        with patch.object(self.daemon.subprocess, "run") as run:
            for lo, hi in [(-1, 80), (80, 80), (90, 80), (75, 101), (0, 0)]:
                with self.subTest(lo=lo, hi=hi), self.assertRaises(ValueError):
                    self.daemon.set_limits(lo, hi)
            run.assert_not_called()

    def test_non_root_cannot_change_limits(self):
        with patch.object(self.daemon.os, "geteuid", return_value=10000), \
             patch.object(self.daemon.subprocess, "run") as run:
            with self.assertRaises(PermissionError):
                self.daemon.set_limits(75, 80)
            run.assert_not_called()

    def test_systemd_failure_stops_sequence_without_success_message(self):
        for step in range(3):
            with self.subTest(step=step), \
                 patch.object(self.daemon.os, "geteuid", return_value=0), \
                 patch.object(self.daemon.subprocess, "run",
                              side_effect=[None] * step + [subprocess.CalledProcessError(1, "systemctl")]) as run, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(subprocess.CalledProcessError):
                    self.daemon.set_limits(75, 80)
                self.assertEqual(run.call_count, step + 1)
                self.assertEqual(output.getvalue(), "")

    def test_full_charge_mode_is_explained(self):
        with patch.object(self.daemon.os, "geteuid", return_value=0), \
             patch.object(self.daemon.subprocess, "run"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.daemon.set_limits(0, 100)
        self.assertIn("Charge window disabled", output.getvalue())

    def test_main_parses_custom_window_and_preserves_status(self):
        with patch.object(self.daemon.sys, "argv", ["guacamole-chgctrl", "limit", "60", "85"]), \
             patch.object(self.daemon, "set_limits") as set_limits:
            self.daemon.main()
            set_limits.assert_called_once_with(60, 85)
        with patch.object(self.daemon.sys, "argv", ["guacamole-chgctrl", "status"]), \
             patch.object(self.daemon, "cmd_status") as status:
            self.daemon.main()
            status.assert_called_once_with()

    def test_service_without_arguments_still_runs_existing_policy(self):
        with patch.object(self.daemon.sys, "argv", ["guacamole-chgctrl"]), \
             patch.object(self.daemon.signal, "signal"), \
             patch.object(self.daemon, "load_conf", return_value={"CHG_MAX": 80}), \
             patch.object(self.daemon, "run") as run:
            self.daemon.main()
            run.assert_called_once_with({"CHG_MAX": 80})

    def test_malformed_arguments_cannot_start_daemon(self):
        for args in [["limit", "75"], ["limit", "bad", "80"], ["typo"]]:
            with self.subTest(args=args), \
                 patch.object(self.daemon.sys, "argv", ["guacamole-chgctrl", *args]), \
                 patch.object(self.daemon, "run") as run, \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.daemon.main()
                self.assertEqual(error.exception.code, 2)
                run.assert_not_called()

    def test_recipe_exports_cli_and_matches_source_checksum(self):
        recipe = (DEVICE / "APKBUILD").read_text()
        self.assertIn('ln -s ../libexec/guacamole-chgctrl "$pkgdir/usr/bin/guacamole-chgctrl"', recipe)
        self.assertIn(hashlib.sha512(DAEMON.read_bytes()).hexdigest() +
                      "  guacamole-chgctrl.daemon", recipe)


if __name__ == "__main__":
    unittest.main()
