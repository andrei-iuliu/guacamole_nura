# Vendored DTBO quarantine validator

`check-boot-dtb-quarantine.py` is copied verbatim from the proven neighbouring
bring-up workspace:

- Source: `bringup-workspace/scripts/check-boot-dtb-quarantine.py`
- Source SHA-256: `45ade635be2f8a93a1e7e3d5b5d6c49741a4af467353e1238fee828a3aee5357`
- Copied file SHA-256: `45ade635be2f8a93a1e7e3d5b5d6c49741a4af467353e1238fee828a3aee5357`
- Source project's related `dt_builder.py` declares SPDX `GPL-2.0-only`.
  The copied validator itself has no separate SPDX header.
- This workspace is GPL-2.0; see the top-level `LICENSE`.

The copy is intentionally unchanged.  `scripts/prepare_temporary_dtb.py`
loads it as a module and overrides only the expected input hashes and the
board-specific clean-to-wrapper delta policy for this Nura DTB.  Do not edit
the vendored copy in place; if the upstream validator changes, copy the new
version, update the hash pin in `prepare_temporary_dtb.py`, and rerun
`tests/test_prepare_temporary_dtb.py`.

# Local Qualcomm libufdt test app

The wrapper/validator also needs the sourced Qualcomm `libufdt` host test app.
It is a binary, so it is kept out of git under `local/`:

- Expected path: `local/tools/ufdt/ufdt_overlay_test_app`
- Source: `bringup-workspace/.cache/first-custom-boot-candidate-004/tools/ufdt/ufdt_overlay_test_app`
- Size: 38024 bytes
- SHA-256: `c7d9db055d5ecfb7771200ea176dd459e2164114f58f107e3c72754cced999fa`
- ELF GNU Build ID: `70727df4605bb944ce1dbcec33f00f69aab785b8`
- Runtime: dynamic `libfdt.so.1` from `local/lib/libfdt.so.1`
  (SHA-256 `aff62061c4c2ea9d248304b87f12ee323d079b89e5a632e6f5ed1614adb3a5e8`).

Upstream licensing notes are in `local/tools/ufdt/NOTICE` when the local copy
is present.  The tool exercises AOSP `libufdt` (Apache-2.0) and `libfdt`
(GPL-2.0-or-later / BSD-2-Clause); preserve those notices on redistribution.
