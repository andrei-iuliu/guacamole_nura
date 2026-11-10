#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Regression tests for the temporary-boot DTBO quarantine wrapper.

These tests are host-only.  They compile a small synthetic overlay, read the
copied stock Lineage references, and exercise the real wrapper/validator path.
"""
from __future__ import annotations

import argparse
import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_temporary_dtb", ROOT / "scripts/prepare_temporary_dtb.py")
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare
SPEC.loader.exec_module(prepare)


class TemporaryDtbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prepare.configure_tools()
        prepare.verify_tools()

    def test_real_reference_selects_exact_entry_zero(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="test-dtbo-") as temporary:
            selected, inspected = prepare.select_stock_entry(
                prepare.DTBO_DEFAULT, Path(temporary))
        self.assertEqual(selected["entry"]["index"], 0)
        self.assertEqual(selected["sha256"], prepare.ENTRY_SHA256)
        self.assertEqual(len(selected["fixups"]), prepare.EXPECTED_FIXUPS)
        self.assertEqual(selected["fragments"], prepare.EXPECTED_FRAGMENTS)
        self.assertEqual(len(inspected), 10)
        self.assertEqual(selected["identity"]["oplus,dtsi_no"], [prepare.PROJECT])
        self.assertEqual(selected["identity"]["oplus,pcb_range"], list(prepare.PCB_RANGE))

    def test_rejects_incompatible_overlay_entry(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="test-dtbo-") as temporary:
            work = Path(temporary)
            dts = work / "incompatible.dts"
            dtb = work / "incompatible.dtb"
            dts.write_text(
                "/dts-v1/;\n"
                "/ {\n"
                '  model = "Qualcomm Technologies, Inc. SM8150 MTP 19863";\n'
                '  compatible = "qcom,sm8150-mtp", "qcom,sm8150", "qcom,mtp";\n'
                "  qcom,board-id = <0x08 0x00>;\n"
                "  oplus,dtsi_no = <0x4d97>;\n"
                "  oplus,pcb_range = <0x00 0x37>;\n"
                "  fragment@0 { target = <0xffffffff>; __overlay__ { status = \"okay\"; }; };\n"
                "};\n"
            )
            prepare.run([prepare.DTC, "-I", "dts", "-O", "dtb", dts, "-o", dtb])
            overlay = dtb.read_bytes()
            header_size, entry_size = 32, 32
            payload_offset = header_size + entry_size
            total = payload_offset + len(overlay)
            image = struct.pack(
                ">8I", 0xD7B7AB1E, total, header_size, entry_size, 1, header_size, 4096, 0)
            image += struct.pack(">4I", len(overlay), payload_offset, 0, 0)
            image += b"\0" * (entry_size - 16)
            image += overlay
            dtbo = work / "incompatible-dtbo.img"
            dtbo.write_bytes(image)
            with self.assertRaisesRegex(prepare.PrepareError, "exactly one compatible"):
                prepare.select_stock_entry(dtbo, work)

    def test_prepare_verify_and_tamper_rejection(self) -> None:
        base_dtb = prepare.BASE_DTB_DEFAULT
        base_source = prepare.BASE_DTS_SOURCE
        if not base_dtb.is_file():
            # A fresh 7.0 worktree has no 6.17 flattened DTB.  Use the 7.0
            # staged DTB when present so this still exercises the wrapper.
            candidate = ROOT / "build/7.0/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
            candidate_source = (
                ROOT / ".work/7.0/build-src/arch/arm64/boot/dts/qcom/"
                "sm8150-oneplus-guacamole.dts"
            )
            if not candidate.is_file() or not candidate_source.is_file():
                self.skipTest("no compiled Nura DTB staged")
            base_dtb = candidate
            base_source = candidate_source
        with tempfile.TemporaryDirectory(dir=ROOT / "build", prefix="test-temporary-dtb-") as temporary:
            out = Path(temporary) / "artifacts"
            args = argparse.Namespace(
                base_dtb=str(base_dtb),
                base_source_dts=str(base_source),
                patched_dts=str(out / "patched.dts"),
                dtbo=str(prepare.DTBO_DEFAULT),
                stock_boot=str(prepare.STOCK_BOOT_DEFAULT),
                out_dir=str(out),
                verify=False,
                manifest=str(out / "manifest.json"),
            )
            self.assertEqual(prepare.prepare(args), 0)
            manifest_path = out / "manifest.json"
            self.assertTrue(manifest_path.is_file())

            import json
            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["status"], "PASS")
            self.assertEqual(manifest["hardware"], "UNTESTED")
            self.assertEqual(manifest["checks"]["deep_validator"], "PASS")
            self.assertEqual(manifest["checks"]["reserved_memory_no_new_conflicts"], True)
            self.assertEqual(
                manifest["reserved_memory"]["pre_existing_conflicts"],
                manifest["reserved_memory"]["post_overlay_conflicts"],
            )
            self.assertEqual(
                manifest["artifacts"]["wrapper"]["sha256"],
                prepare.sha256(out / "custom_guacamole-abl-quarantine.dtb"),
            )

            wrapper = out / "custom_guacamole-abl-quarantine.dtb"
            payload = bytearray(wrapper.read_bytes())
            payload[len(payload) // 2] ^= 0xFF
            wrapper.write_bytes(payload)
            with self.assertRaisesRegex(prepare.PrepareError, "hash/size mismatch"):
                prepare.verify_manifest(argparse.Namespace(manifest=str(manifest_path)))


if __name__ == "__main__":
    unittest.main()
