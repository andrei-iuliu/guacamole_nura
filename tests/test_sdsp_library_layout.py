#!/usr/bin/env python3
"""Packaging check for the SDSP library layout served by hexagonrpcd."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APKBUILD = ROOT / "device/testing/device-oneplus-guacamole/APKBUILD"


class SdspLayoutTest(unittest.TestCase):
    def test_libraries_are_installed_under_dsp_sdsp(self) -> None:
        recipe = APKBUILD.read_text()
        # hexagonrpcd maps <root>/dsp/<dsp> to the DSP's /usr/lib/qcom/adsp.
        self.assertIn('mkdir -p "$q/sensors" "$q/socinfo" "$q/dsp"', recipe)
        self.assertIn('cp -r "$sd/sdsp" "$q/dsp/sdsp"', recipe)
        self.assertIn('ln -s dsp/sdsp "$q/sdsp"', recipe)
        self.assertNotIn('cp -r "$sd/sdsp" "$q/sdsp"', recipe)


if __name__ == "__main__":
    unittest.main()
