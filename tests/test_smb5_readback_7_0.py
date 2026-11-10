#!/usr/bin/env python3
"""Focused regression checks for the SMB5 readback correction (patch 0032).

The register selection is taken from the patch's post-image, not from a
hand-copied constant, and the ICL address arithmetic is evaluated by a small
host program. No phone, module, register, service or network access is used.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH = (ROOT / "device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
         / "0032-smb5-readback.patch")
SRC = ROOT / ".work/rear-camera/linux-v7.0.0-sm8150"
TARGET = "drivers/power/supply/qcom_smbx.c"


def hunks(text: str) -> list[tuple[list[str], list[str]]]:
    """Split a unified diff into (pre-image, post-image) line lists."""
    result: list[tuple[list[str], list[str]]] = []
    old: list[str] | None = None
    new: list[str] = []
    for line in text.splitlines():
        if line.startswith("@@ "):
            if old is not None:
                result.append((old, new))
            old, new = [], []
        elif old is None:
            continue
        elif line.startswith("\\"):
            continue
        elif line.startswith("-"):
            old.append(line[1:])
        elif line.startswith("+"):
            new.append(line[1:])
        elif line.startswith(" "):
            old.append(line[1:])
            new.append(line[1:])
        else:
            result.append((old, new))
            old = None
    if old is not None:
        result.append((old, new))
    return result


TEXT = PATCH.read_text()
HUNKS = hunks(TEXT)
POST = "\n".join("\n".join(new) for _, new in HUNKS)


class PatchScope(unittest.TestCase):
    def test_only_the_smbx_readback_file_is_touched(self):
        self.assertEqual(re.findall(r"^\+\+\+ b/(.+)$", TEXT, re.M), [TARGET])

    def test_no_unrelated_charger_feature_is_pulled_in(self):
        code = [line[1:] for line in TEXT.splitlines()
                if line.startswith("+") and not line.startswith("+++")]
        for needle in ("HVDCP", "OTG", "PDO", "smb5_charger_status",
                       "FAST_CHARGE", "FLOAT_VOLTAGE", "bq27",
                       "power_supply_battery_info"):
            self.assertFalse([line for line in code if needle in line], needle)


class OvervoltageRegister(unittest.TestCase):
    def test_smb5_overvoltage_moves_from_status_7_to_status_2(self):
        selected = [(old, new) for old, new in HUNKS
                    if any("BATTERY_CHARGER_STATUS" in line for line in old)]
        self.assertEqual(len(selected), 1)
        old, new = selected[0]
        self.assertIn("\t\treg = BATTERY_CHARGER_STATUS_7;", old)
        self.assertIn("\t\treg = BATTERY_CHARGER_STATUS_2;", new)

    def test_both_generations_read_overvoltage_from_status_2_after_patch(self):
        selected = [(old, new) for old, new in HUNKS
                    if any("BATTERY_CHARGER_STATUS" in line for line in old)]
        old, new = selected[0]
        # Only the SMB5 register line changes; the SMB2 case stays as it was.
        self.assertEqual([line for line in old if line not in new],
                         ["\t\treg = BATTERY_CHARGER_STATUS_7;"])
        self.assertIn("\t\tmask = SMB2_CHARGER_ERROR_STATUS_BAT_OV_BIT;", new)
        smb5 = "\n".join(new).split("case SMB5:")[1]
        self.assertIn("BATTERY_CHARGER_STATUS_2", smb5)
        self.assertNotIn("BATTERY_CHARGER_STATUS_7", smb5)


class EffectiveInputCurrent(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "host C compiler unavailable")
    def test_effective_icl_address_is_0x1108_on_smb5_and_0x1607_on_smb2(self):
        begin = "#define SMB2_ICL_STATUS"
        defines = begin + POST.split(begin, 1)[1].split(
            "#define INPUT_CURRENT_LIMIT_MASK", 1)[0]
        program = "\n".join([
            "enum smb_generation { SMB2, SMB5 };",
            "struct smb_chip { enum smb_generation gen; };",
            defines,
            "#include <stdio.h>",
            "int main(void)",
            "{",
            "\tstruct smb_chip s2 = { .gen = SMB2 }, s5 = { .gen = SMB5 };",
            "\tstruct smb_chip *smb2 = &s2, *smb5 = &s5;",
            '\tprintf("%#x %#x\\n", 0x1000u + ICL_STATUS(smb2),',
            '\t       0x1000u + ICL_STATUS(smb5));',
            "\treturn 0;",
            "}",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "icl_status.c"
            binary = Path(tmp) / "icl_status"
            source.write_text(program + "\n")
            build = subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra",
                                    "-Werror", "-o", str(binary), str(source)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            output = subprocess.run([str(binary)], capture_output=True,
                                    text=True, check=True).stdout.split()
        self.assertEqual(output, ["0x1607", "0x1108"])

    def test_readback_keeps_the_effective_status_not_the_programmed_limit(self):
        added = [line[1:] for line in TEXT.splitlines()
                 if line.startswith("+") and not line.startswith("+++")]
        code = [line for line in added
                if not line.lstrip().startswith(("*", "/*", "*/"))]
        self.assertIn("ICL_STATUS", "".join(code))
        self.assertNotIn("USBIN_CURRENT_LIMIT_CFG", "".join(code))


@unittest.skipUnless(SRC.is_dir(), "patched kernel source not staged")
class StagedSource(unittest.TestCase):
    def test_staged_source_carries_the_fix_zero_fuzz(self):
        staged = (SRC / TARGET).read_text()
        # A prepared build tree may already include the patch after a rebuild.
        already_patched = "#define SMB5_AICL_ICL_STATUS" in staged
        args = ["patch", "-p1", "--fuzz=0", "--dry-run", "-i", str(PATCH)]
        if already_patched:
            args.insert(1, "-R")
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            target = tree / TARGET
            target.parent.mkdir(parents=True)
            target.write_text(staged)
            checked = subprocess.run(args, cwd=tree, capture_output=True,
                                     text=True)
            self.assertEqual(checked.returncode, 0,
                             checked.stdout + checked.stderr)
            if already_patched:
                text = staged
            else:
                subprocess.run(["patch", "-p1", "--fuzz=0", "-i",
                                str(PATCH)], cwd=tree, check=True,
                               capture_output=True)
                text = target.read_text()

        self.assertIn("reg = BATTERY_CHARGER_STATUS_2;", text)
        self.assertIn("#define SMB5_AICL_ICL_STATUS", text)
        readback = text.split("static inline int smb_get_current_limit")[1]
        readback = readback.split("static int smb_rerun_aicl")[0]
        self.assertIn("ICL_STATUS(chip)", readback)
        self.assertNotIn("USBIN_CURRENT_LIMIT_CFG", readback)


if __name__ == "__main__":
    unittest.main()
