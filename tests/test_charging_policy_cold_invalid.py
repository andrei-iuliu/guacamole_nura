#!/usr/bin/env python3
"""Mocked tests for cold and invalid-gauge handling in guacamole-chgctrl.

Every path the daemon reads is redirected into a temporary directory and the
writes to the charger are recorded, so no /sys/class/power_supply entry, real
charger or phone is touched. Run with:

    python3 -m unittest tests.test_charging_policy_cold_invalid -v
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DAEMON = ROOT / "device/testing/device-oneplus-guacamole/guacamole-chgctrl.daemon"


def load_daemon():
    loader = importlib.machinery.SourceFileLoader("guacamole_chgctrl_daemon", str(DAEMON))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class StopLoop(Exception):
    """Raised from the injected sleep to end one policy run."""


class ChargingPolicyTest(unittest.TestCase):
    CONF = {"CHG_MIN": 40, "CHG_MAX": 100, "KEEP_AWAKE": "yes", "CHG_SDP_UA": 0}

    def setUp(self):
        self.daemon = load_daemon()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.psy = root / "power_supply"
        self.charger = self.psy / "pm8150b-charger"
        self.battery = self.psy / "bq27411-0"
        self.charger.mkdir(parents=True)
        self.battery.mkdir(parents=True)
        self.state_file = root / "run/guacamole/charger"

        self.daemon.PSY = str(self.psy)
        self.daemon.CHARGER = str(self.charger)
        self.daemon.BATTERY = str(self.battery)
        self.daemon.STATE_FILE = str(self.state_file)
        self.daemon.CONF_FILE = str(root / "absent.conf")

        self.set_charger(online=1, usb_type="DCP")

    # -- fake sysfs helpers ------------------------------------------------
    def set_charger(self, **props):
        for name, value in props.items():
            (self.charger / name).write_text("%s\n" % value)

    def apply(self, step):
        values = {"temp": step.get("temp", 300),
                  "capacity": step.get("cap", 50)}
        for name, value in values.items():
            path = self.battery / name
            if value is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text("%s\n" % value)

    def drive(self, steps, conf=None):
        """Run one policy iteration per step; return (writes, state, journal).

        sleep is replaced with a callback that installs the next step and ends
        the loop after the last one, so run() polls a fake sysfs tree only.
        """
        merged = dict(self.CONF)
        merged.update(conf or {})
        writes = []

        def fake_write(path, value):
            writes.append((os.path.basename(path), value))
            return True

        index = 0

        def fake_sleep(_):
            nonlocal index
            index += 1
            if index >= len(steps):
                raise StopLoop
            self.apply(steps[index])

        self.apply(steps[0])
        self.daemon.write = fake_write
        journal = io.StringIO()
        with contextlib.redirect_stdout(journal):
            with self.assertRaises(StopLoop):
                self.daemon.run(merged, sleep=fake_sleep)
        return writes, self.state_file.read_text().strip(), journal.getvalue()

    @staticmethod
    def statuses(writes):
        return [value for name, value in writes if name == "status"]

    @staticmethod
    def icls(writes):
        return [value for name, value in writes if name == "input_current_limit"]

    # -- tests -------------------------------------------------------------
    def test_normal_dcp_keeps_existing_policy(self):
        writes, state, _ = self.drive([{"temp": 300, "cap": 50}])
        self.assertEqual(self.statuses(writes), [1])
        self.assertEqual(self.icls(writes), [1800000])
        self.assertIn("state=ok", state)

    def test_cold_suspends_and_resumes_only_after_hysteresis(self):
        writes, state, journal = self.drive([
            {"temp": 40, "cap": 50},    # 4.0 C: cold cutoff
            {"temp": 60, "cap": 50},    # 6.0 C: still latched
            {"temp": 79, "cap": 50},    # 7.9 C: still latched
            {"temp": 90, "cap": 50},    # 9.0 C: resume
        ])
        self.assertEqual(self.statuses(writes), [0, 0, 0, 1])
        self.assertEqual(self.icls(writes), [1800000])
        self.assertIn("state start -> cold", journal)
        self.assertIn("input suspended: battery 4.0 C (cold cutoff)", journal)
        self.assertIn("state cold -> ok", journal)
        self.assertIn("state=ok", state)

    def test_missing_temperature_cannot_clear_a_latched_hot_state(self):
        writes, state, journal = self.drive([
            {"temp": 470, "cap": 50},   # 47.0 C: hot, input suspended
            {"temp": None, "cap": 50},  # gauge read disappears
            {"temp": 440, "cap": 50},   # 44.0 C: still inside hot hysteresis
            {"temp": 420, "cap": 50},   # 42.0 C: hot clears, warm throttles
        ])
        self.assertEqual(self.statuses(writes), [0, 0, 0, 1])
        self.assertEqual(self.icls(writes), [1000000])
        self.assertIn("state start -> hot (battery 47.0 C)", journal)
        self.assertIn("state hot -> invalid (temperature unusable)", journal)
        self.assertIn("state invalid -> hot (battery 44.0 C)", journal)
        self.assertIn("state hot -> warm (battery 42.0 C)", journal)
        self.assertIn("state=warm: throttled to 1 A", state)

    def test_malformed_missing_and_out_of_range_data_fail_closed(self):
        writes, _, journal = self.drive([
            {"temp": None, "cap": 50},
            {"temp": "not-a-number", "cap": 50},
            {"temp": 300, "cap": None},
            {"temp": 300, "cap": 101},
            {"temp": 300, "cap": -1},
            {"temp": 300, "cap": "n/a"},
            {"temp": 300, "cap": 50},   # usable again
        ])
        self.assertEqual(self.statuses(writes), [0, 0, 0, 0, 0, 0, 1])
        self.assertEqual(self.icls(writes), [1800000])
        self.assertIn("gauge temperature unusable", journal)
        self.assertIn("gauge capacity unusable", journal)
        self.assertIn("state=invalid: input suspended", journal)

    def test_charge_window_still_suspends_and_recovers(self):
        writes, _, journal = self.drive([
            {"temp": 300, "cap": 85},
            {"temp": 300, "cap": 60},
            {"temp": 300, "cap": 40},
            {"temp": 300, "cap": 75},
        ], conf={"CHG_MAX": 80})
        self.assertEqual(self.statuses(writes), [0, 0, 1, 1])
        self.assertEqual(self.icls(writes), [1800000])
        self.assertIn("input suspended: charge window 40-80%", journal)

    def test_plausibility_boundaries_fail_closed(self):
        writes, state, journal = self.drive([
            {"temp": -400, "cap": 50},   # -40.0 C: plausible, cold
            {"temp": -401, "cap": 50},   # below the window: invalid
            {"temp": 1250, "cap": 50},   # 125.0 C: plausible, hot
            {"temp": 1251, "cap": 50},   # above the window: invalid
            {"temp": 300, "cap": 50},    # normal again
            {"temp": 300, "cap": 0},     # capacity 0 % is usable
            {"temp": 300, "cap": 100},   # capacity 100 % is usable
            {"temp": 300, "cap": 101},   # out of range: invalid
            {"temp": 300, "cap": -1},    # unusable: invalid
        ])
        self.assertEqual(self.statuses(writes), [0, 0, 0, 0, 1, 1, 1, 0, 0])
        self.assertEqual(self.icls(writes), [1800000])
        self.assertIn("state start -> cold (battery -40.0 C)", journal)
        self.assertIn("state cold -> invalid (temperature unusable)", journal)
        self.assertIn("state invalid -> hot (battery 125.0 C)", journal)
        self.assertIn("state hot -> invalid (temperature unusable)", journal)
        self.assertIn("state invalid -> ok", journal)
        self.assertIn("state=invalid: input suspended: gauge capacity unusable", journal)

    def test_unplugged_invalid_gauge_still_enables_the_input(self):
        self.set_charger(online=0, usb_type="Unknown")
        writes, state, _ = self.drive([{"temp": None, "cap": None}])
        self.assertEqual(self.statuses(writes), [1])
        self.assertEqual(self.icls(writes), [])
        self.assertNotIn("state=", state)

    def test_status_reports_unusable_gauge_instead_of_zero(self):
        self.apply({"temp": None, "cap": None})
        self.set_charger(status="Discharging", voltage_now=3800000,
                         current_now=-500000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.daemon.cmd_status()
        text = out.getvalue()
        self.assertIn("capacity invalid", text)
        self.assertIn("temperature invalid", text)


if __name__ == "__main__":
    unittest.main()
