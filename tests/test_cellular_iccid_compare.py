#!/usr/bin/env python3
"""Tests for the privacy-preserving ICCID source diagnostic."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import cellular_iccid_compare as compare  # noqa: E402

# Public synthetic values from ModemManager's own test suite and simple
# low-nibble-first BCD encodings. They are not read from any phone.
ICCID_A = "8944200053671052499"
ICCID_B = "89324102234690160476"


def bcd_low_first(digits: str) -> bytes:
    data = bytearray()
    for i in range(0, len(digits), 2):
        low = int(digits[i])
        high = int(digits[i + 1]) if i + 1 < len(digits) else 0xF
        data.append(low | (high << 4))
    return bytes(data)


class IccidNormalizationTest(unittest.TestCase):
    def test_modemmanager_public_vectors(self) -> None:
        self.assertEqual(
            compare.mm_parse_iccid("984402003576012594F9"),
            "8944200053671052499",
        )
        self.assertEqual(
            compare.mm_parse_iccid("98231420326409614067"),
            "89324102234690160476",
        )
        self.assertEqual(
            compare.mm_parse_iccid("8944200053671052499F"),
            "8944200053671052499",
        )
        self.assertEqual(
            compare.mm_parse_iccid("8944200053671052499"),
            "8944200053671052499",
        )
        self.assertEqual(
            compare.mm_parse_iccid('"984402003576012594F9"'),
            "8944200053671052499",
        )

    def test_modemmanager_rejects_bad_values(self) -> None:
        for value in (
            "982314203264096",
            "98231420326pl9614067",
            "0044200053671052499",
        ):
            with self.assertRaises(compare.IccidError):
                compare.mm_parse_iccid(value)

    def test_low_nibble_decode_matches_qmicli(self) -> None:
        raw = bcd_low_first(ICCID_A)
        self.assertEqual(compare.qmicli_decode_iccid(raw), ICCID_A)
        # qmicli maps A-F to NUL and g_print stops at the first one.
        self.assertEqual(compare.qmicli_decode_iccid(bytes.fromhex("9844020035A6")), "89442000536")

    def test_operator_hex_and_ascii_rules(self):
        raw = bytes.fromhex('984402A03576012594F9')
        self.assertEqual(compare.mm_parse_iccid(raw.hex()), '8944200A53671052499')
        self.assertEqual(compare.qmicli_decode_iccid(raw), '8944200')
        with self.assertRaises(compare.IccidError):
            compare.mm_parse_iccid('89' + '\u0661' * 18)


class ParsingTest(unittest.TestCase):
    def test_slot_status(self) -> None:
        output = """
[qrtr://0] 2 physical slots found:
  Physical slot 1:
     Card status: present
     Slot status: active
           ICCID: 8944200053671052499
  Physical slot 2:
     Card status: absent
     Slot status: inactive
           ICCID: unknown
"""
        self.assertEqual(compare.parse_slot_status(output), {1: ICCID_A, 2: None})

    def test_read_transparent(self) -> None:
        output = (
            "[qrtr://0] Successfully read information from the UIM:\n"
            "Card result:\n\tSW1: '0x00'\n\tSW2: '0x00'\n"
            "Read result:\n\t"
            + ":".join(f"{b:02X}" for b in bcd_low_first(ICCID_A))
            + "\n"
        )
        self.assertEqual(
            compare.parse_read_transparent(output), bcd_low_first(ICCID_A)
        )

    def test_slot_status_active_slot(self) -> None:
        output = """
  Physical slot 1:
     Card status: present
     Slot status: active
           ICCID: 8944200053671052499
  Physical slot 2:
     Card status: absent
     Slot status: inactive
"""
        self.assertEqual(compare.parse_slot_status_active_slot(output), 1)

    def test_card_status_primary_gw(self) -> None:
        present = "Provisioning applications:\n\tPrimary GW:   slot '1', application '1'\n"
        absent = "Provisioning applications:\n\tPrimary GW:   session doesn't exist\n"
        self.assertEqual(compare.parse_primary_gw_slot(present), 1)
        self.assertTrue(compare.primary_gw_session_exists(present))
        self.assertFalse(compare.primary_gw_session_exists(absent))

    def test_dms_iccid(self) -> None:
        output = "[qrtr://0] UIM ICCID retrieved:\n\tICCID: '" + ICCID_A + "'\n"
        self.assertEqual(compare.parse_dms_iccid(output), ICCID_A)

    def test_extract_sections(self) -> None:
        output = (
            "@@MODEM@@\r\nmodem.generic.state : disabled\r\n"
            "@@SIM@@\r\nsim.properties.iccid : " + ICCID_A + "\r\n"
            "@@SLOT@@\r\n           ICCID: " + ICCID_A + "\r\n"
            "@@READ@@\r\nRead result:\r\n\t98:44\r\n"
        )
        sections = compare.extract_sections(output)
        self.assertEqual(
            compare.parse_keyvalue(sections["@@SIM@@"], "sim.properties.iccid"),
            ICCID_A,
        )
        self.assertEqual(sections["@@SLOT@@"], "           ICCID: " + ICCID_A)


class ReportPrivacyTest(unittest.TestCase):
    def report(self, data, salt=None):
        data.modem += "\nmodem.dbus-path : /modem/0\nmodem.generic.sim : /sim/0\n"
        data.sim += "\nsim.dbus-path : /sim/0\n"
        data.modem_after = data.modem
        data.sim_after = data.sim
        return compare.build_report(data, salt=salt)

    def _data(self, cached: str = ICCID_A, slot: str = ICCID_A, raw: bytes | None = None):
        raw = bcd_low_first(ICCID_A) if raw is None else raw
        return compare.ReportData(
            modem="modem.generic.state : enabling\nmodem.generic.sim : /sim/0\n",
            sim=f"sim.properties.iccid : {cached}\n" if cached is not None else "",
            slot=f"Physical slot 1:\n Slot status: active\n ICCID: {slot}\n" if slot is not None else "",
            read="Read result:\n\t" + ":".join(f"{b:02X}" for b in raw) + "\n",
        )

    def test_report_never_leaks_raw_identifiers(self) -> None:
        report = self.report(self._data(), salt="fixed-test-salt")
        self.assertIn("len=19", report)
        self.assertIn("prefix=89", report)
        self.assertNotIn(ICCID_A, report)
        self.assertNotIn(ICCID_B, report)
        self.assertNotIn("8944200053671052499", report)
        self.assertIn("cached == active-slot ICCID: YES", report)
        self.assertIn("cached == EF2FE2(MM normalization): YES", report)

    def test_detects_slot_versus_ef2fe2_mismatch(self) -> None:
        # slot says A, EF2FE2 raw bytes are B; cached follows slot.
        data = compare.ReportData(
            sim=f"sim.properties.iccid : {ICCID_A}\n",
            slot=f"Physical slot 1:\n Slot status: active\n ICCID: {ICCID_A}\n",
            read="Read result:\n\t" + ":".join(f"{b:02X}" for b in bcd_low_first(ICCID_B)) + "\n",
        )
        report = self.report(data, salt="fixed-test-salt")
        self.assertIn("cached == active-slot ICCID: YES", report)
        self.assertIn("cached == EF2FE2(MM normalization): NO", report)
        self.assertIn("investigate slot-status", report)
        self.assertNotIn(ICCID_A, report)
        self.assertNotIn(ICCID_B, report)

    def test_reports_dms_path_hint(self) -> None:
        report = self.report(
            compare.ReportData(
                sim=f"sim.properties.iccid : {ICCID_A}\n",
                slot=f"Physical slot 1:\n Slot status: active\n ICCID: {ICCID_B}\n",
                dms="\tICCID: '" + ICCID_A + "'\n",
                read="Read result:\n\t" + ":".join(f"{b:02X}" for b in bcd_low_first(ICCID_B)) + "\n",
            ),
            salt="fixed-test-salt",
        )
        self.assertIn("cached == DMS Get ICCID: YES", report)
        self.assertIn("Cached matches DMS UIM Get ICCID", report)
        self.assertNotIn(ICCID_A, report)
        self.assertNotIn(ICCID_B, report)

    def test_reports_slot_mismatch_hint(self) -> None:
        report = self.report(
            compare.ReportData(
                sim=f"sim.properties.iccid : {ICCID_A}\n",
                slot=(
                    "  Physical slot 1:\n     Slot status: inactive\n"
                    f"           ICCID: {ICCID_A}\n"
                    "  Physical slot 2:\n     Slot status: active\n"
                    f"           ICCID: {ICCID_A}\n"
                ),
                card="Provisioning applications:\n\tPrimary GW:   slot '1', application '1'\n",
                read="Read result:\n\t" + ":".join(f"{b:02X}" for b in bcd_low_first(ICCID_A)) + "\n",
            ),
            salt="fixed-test-salt",
        )
        self.assertIn("slot-status active slot == primary-GW provisioning slot: NO", report)
        self.assertIn("active slot differs from the primary-GW", report)
        self.assertNotIn(ICCID_A, report)

    def test_reports_empty_cached_identifier(self) -> None:
        report = self.report(
            compare.ReportData(
                sim="sim.properties.iccid : \n",
                slot=f"Physical slot 1:\n Slot status: active\n ICCID: {ICCID_A}\n",
                read="Read result:\n\t98:44\n",
            ),
            salt="fixed-test-salt",
        )
        self.assertIn("EMPTY", report)
        self.assertIn("cached identifier is EMPTY", report)
        self.assertNotIn(ICCID_A, report)

    def test_cli_with_fake_ssh_does_not_leak(self) -> None:
        read_hex = ":".join(f"{b:02X}" for b in bcd_low_first(ICCID_A))
        with tempfile.TemporaryDirectory(prefix="fake-ssh-") as tmp:
            fake = Path(tmp) / "ssh"
            fake.write_text(
                "#!/bin/sh\n"
                "cat <<'EOF'\n"
                "@@MODEM@@\n"
                "modem.generic.state : enabling\n"
                "modem.dbus-path : /modem/0\nmodem.generic.sim : /sim/0\n"
                "@@SIM@@\n"
                f"sim.properties.iccid : {ICCID_A}\nsim.dbus-path : /sim/0\n"
                "@@SLOT@@\n"
                f"Physical slot 1:\n Slot status: active\n ICCID: {ICCID_A}\n"
                "@@DMS@@\n"
                f"\tICCID: '{ICCID_A}'\n"
                "@@READ@@\n"
                "Read result:\n"
                f"\t{read_hex}\n"
                "@@MODEM_AFTER@@\nmodem.dbus-path : /modem/0\nmodem.generic.sim : /sim/0\n"
                "@@SIM_AFTER@@\nsim.dbus-path : /sim/0\n"
                "EOF\n"
            )
            fake.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{tmp}:{env['PATH']}"
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts/cellular_iccid_compare.py"),
                    "user@example.invalid",
                    "--no-sudo",
                ],
                env=env,
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cached == active-slot ICCID: YES", result.stdout)
        self.assertIn("cached == EF2FE2(MM normalization): YES", result.stdout)
        self.assertIn("cached == DMS Get ICCID: YES", result.stdout)
        self.assertNotIn(ICCID_A, result.stdout)
        self.assertNotIn(ICCID_B, result.stdout)

    def test_remote_script_is_read_only(self) -> None:
        script = compare.remote_script(True)
        self.assertIn("--uim-get-slot-status", script)
        self.assertIn("--uim-get-card-status", script)
        self.assertIn("--uim-read-transparent=0x3F00,0x2FE2", script)
        self.assertIn("--dms-uim-get-iccid", script)
        for forbidden in (
            "uim-change-provisioning",
            "uim-reset",
            "uim-switch-slot",
            "uim-set",
            "mmcli --disable",
            "mmcli --enable",
            "mmcli --set",
            "systemctl",
            "reboot",
            "rm ",
        ):
            self.assertNotIn(forbidden, script)


class SourceRegressionTest(unittest.TestCase):
    def test_padded_exact_keys_and_value_states(self):
        # Pinned mmcli-output.c:1349 pads keys and :1351 uses -- for NULL.
        output = f"{'sim.properties.iccid.extra':45} : wrong\n{'sim.properties.iccid':45} : {ICCID_A}\n"
        self.assertEqual(compare.parse_keyvalue(output, compare.MM_ICCID_KEY), ICCID_A)
        self.assertIsNone(compare.parse_keyvalue('', compare.MM_ICCID_KEY))
        self.assertEqual(compare.parse_keyvalue('sim.properties.iccid : ', compare.MM_ICCID_KEY), '')
        self.assertEqual(compare.parse_keyvalue('sim.properties.iccid : --', compare.MM_ICCID_KEY), '--')
        self.assertNotEqual(compare.describe(None, 'salt'), compare.describe('', 'salt'))
        self.assertIn('sentinel --', compare.describe('--', 'salt'))

    def test_hashes_are_source_independent_and_run_specific(self):
        report = compare.build_report(compare.ReportData(
            sim=f'sim.properties.iccid : {ICCID_A}',
            slot=f'Physical slot 2:\n Slot status: active\n ICCID: {ICCID_A}',
            read='Read result:\n' + bcd_low_first(ICCID_A).hex(':')), salt='salt')
        import re
        hashes = re.findall(r'salted_sha256=([a-f0-9]{64})', report)
        self.assertGreaterEqual(len(hashes), 4)
        self.assertEqual(len(set(hashes)), 1)
        self.assertNotEqual(compare.make_hash('salt', ICCID_A), compare.make_hash('other', ICCID_A))
        self.assertNotEqual(compare.make_hash('salt', ICCID_A), compare.make_hash('salt', ICCID_B))

    def test_missing_sample_is_unknown_not_empty(self):
        report = compare.build_report(compare.ReportData(), salt='salt')
        self.assertIn('cached mmcli SIM identifier: UNAVAILABLE', report)
        self.assertNotIn('identifier is EMPTY', report)
        self.assertNotIn(': NO', report)

    def test_active_slot_two_and_absent_slot_one(self):
        for first in ('unknown', ICCID_B):
            report = compare.build_report(compare.ReportData(
                slot=f'Physical slot 1:\n Slot status: inactive\n ICCID: {first}\nPhysical slot 2:\n Slot status: active\n ICCID: {ICCID_A}',
                read='Read result:\n' + bcd_low_first(ICCID_A).hex(':')), salt='salt')
            self.assertIn('physical slot 2: len=19', report)
            self.assertIn('active-slot ICCID == EF2FE2(MM normalization): YES', report)
        self.assertEqual(compare.parse_slot_status('ICCID: ' + ICCID_A), {})

    def test_multiple_active_slots_are_ambiguous(self):
        output = 'Physical slot 1:\n Slot status: active\nPhysical slot 2:\n Slot status: active\n'
        self.assertIsNone(compare.parse_slot_status_active_slot(output))

    def test_identity_change_and_missing_identity_block_cached_comparison(self):
        data = compare.ReportData(
            modem='modem.dbus-path : /modem/1\nmodem.generic.sim : /sim/1',
            sim=f'sim.dbus-path : /sim/1\nsim.properties.iccid : {ICCID_A}',
            modem_after='modem.dbus-path : /modem/2\nmodem.generic.sim : /sim/2',
            sim_after='sim.dbus-path : /sim/2')
        self.assertIn('identity before/after: CHANGED/INCONSISTENT', compare.build_report(data))
        self.assertIn('cached == EF2FE2(MM normalization): UNKNOWN', compare.build_report(data))
        data.sim_after = ''
        self.assertIn('identity before/after: UNKNOWN', compare.build_report(data))

    def test_error_categories_do_not_echo_private_errors(self):
        for text, code, category in (
            (f"QMI protocol error (70): '{ICCID_A}'", 1, 'QMI-error code=70'),
            (f'Permission denied {ICCID_A}', 1, 'permission/authentication'),
            (f'operation timed out {ICCID_A}', 1, 'timeout'),
            (f'not found {ICCID_A}', 127, 'command unavailable'),
            (ICCID_A, 2, 'query failed'),
        ):
            body, status = compare.query_result(f'{text}\n@@EXIT@@ {code}\n')
            self.assertEqual(body, '')
            self.assertIn(category, status)
            self.assertNotIn(ICCID_A, status)

    def test_local_cli_failure_report_keeps_errors_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('mmcli', 'qmicli'):
                fake = Path(tmp) / name
                fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"QMI protocol error (70): {ICCID_A}\" >&2\nexit 1\n")
                fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}")
            result = subprocess.run([sys.executable, str(REPO_ROOT / 'scripts/cellular_iccid_compare.py'),
                                     '--local', '--no-sudo'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('query READ: exit=1 QMI-error code=70', result.stdout)
        self.assertIn('cached mmcli SIM identifier: UNAVAILABLE', result.stdout)
        self.assertNotIn(ICCID_A, result.stdout + result.stderr)

    def test_generated_commands_capture_real_exit_status_privately(self):
        # Execute only synthetic binaries; no SSH, phone, QMI or sudo.
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('mmcli', 'qmicli'):
                fake = Path(tmp) / name
                fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"QMI protocol error (70): {ICCID_A}\" >&2\nexit 3\n")
                fake.chmod(0o755)
            env = dict(os.environ, PATH=f"{tmp}:{os.environ['PATH']}")
            result = subprocess.run(['sh', '-c', compare.remote_script(False)], env=env, capture_output=True, text=True)
        sections = compare.extract_sections(result.stdout)
        self.assertEqual(len(sections), 8)
        for output in sections.values():
            body, status = compare.query_result(output)
            self.assertEqual(body, '')
            self.assertEqual(status, 'exit=3 QMI-error code=70')
        self.assertEqual(result.stderr, '')



if __name__ == "__main__":
    unittest.main()
