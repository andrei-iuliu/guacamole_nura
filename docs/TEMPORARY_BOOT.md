# Temporary boot: preserve the stock DTBO

Status: **host-side wrapper PASS / hardware UNTESTED**.  The original Nura
kernel, its 24 patches, `base_guacamole_working.dts`, and all hardware nodes
are unchanged.  No boot image was produced in this task because the initramfs
and rootfs are still missing.

## Why this wrapper exists

The OnePlus ABL applies the active slot's stock DTBO when Linux is booted from
a downloaded Android v2 boot image.  The Nura DTB is not a downstream Android
base, so the stock overlay's fixups cannot resolve directly and ABL can corrupt
the live tree.  The old Nura instructions avoided that by zeroing the active
DTBO partition; that is not acceptable for a temporary boot that must preserve
Android.

The wrapper is a copy of the compiled Nura DTB with three boot-only additions:

1. the exact stock entry-selection/root identity fields;
2. a generated `/__symbols__` node that redirects all 245 external stock
   fixups to one sink;
3. one disabled `/__abl_dtbo_quarantine__` node with that sink.

The stock overlay still applies, but entirely below the disabled quarantine.
No live Nura node or property is added, removed or changed outside the reviewed
root identity delta.  The common `qcom,sm8150` compatible remains present.

## Current artifacts

Built from `build/custom_guacamole.dtb` (SHA-256
`06b65ea06f1b575fb9f246e44da45e5b2850fca5b2abeb74d495a2512b9ab2f9`,
105871 bytes) and stock DTBO entry 0.

| Artifact | Size | SHA-256 |
|---|---:|---|
| `build/temporary-dtbo/custom_guacamole-abl-quarantine.dtb` | 120252 | `f273c9d701cd2afbd7ccc2e0846343d7b6a6e4434c8ef251a319c48629273a2f` |
| `build/temporary-dtbo/dtbo-entry-00.dtb` | 326288 | `5755b70bba844ae433a9c3fac84d6bd301540d27c8f7e1f020db415a21cde238` |
| `build/temporary-dtbo/post-stock-merged-libufdt.dtb` | 446540 | `341ded3f1071f3853e2c2f9b7e88f07bac32f379f7a50b89ee396a5c432877e5` |
| `build/temporary-dtbo/post-stock-merged-libfdt.dtb` | 374009 | `cc01ee0f49f31cedf1dfee454bbf4d97d80fb109ae58bdca6fc7dd7cf0447d18` |
| `build/temporary-dtbo/quarantine-report.json` | 9990 | `e84f9e69c2084b34a570c366c4f33b3909841898fbf68a77744e56f45e1dbc12` |
| `build/temporary-dtbo/manifest.json` | — | manifest is authoritative and pins all inputs/tools |

The wrapper hash is deterministic for this exact base and stock entry.  If the
base DTB changes, regenerate and use the new manifest; do not reuse these hashes.

## Exact stock selection

Both supplied Lineage 24.0 references contain the same 10-entry DTBO payload
and the same entry 0; only their AVB footer differs:

| Reference | DTBO SHA-256 |
|---|---|
| `ref/lineageos/lineage-24.0-20261102/dtbo.img` | `460f3ed5f1d7c1b6b6f5d0fdce6526e7d07ac12ad965b6f77f2ead80ffa414c6` |
| `ref/lineageos/lineage-24.0-20261011/dtbo.img` | `6c0fd54b06920f41e586ce2010503eb2cde40ba54a1993af53cb59ae221bf5a4` |

Entry 0 of both images is the selected entry: project `18821` (0x4985), PCB
range `0..55` (hardware 21 is inside), `qcom,board-id = <8 0>`, 128 fragments,
245 external fixups, SHA-256 `5755b70b...`.  The nine other entries belong to
other projects (18857, 18865, 19801, 19863) and are not selected.

The matching stock boot references were also parsed.  Both have a v2 header,
page size 4096, kernel address `0x8000`, ramdisk address `0x01000000`, tags
`0x100`, DTB address `0x01f00000`, one base DTB in the pack, and the selected
base is index 0 with `qcom,msm-id = <339 0x20000>` and
`qcom,board-id = <8 0>`.  That base is SHA-256
`ebbf899dd2ec51322df6a27473d80a12f8a3dc01ea3786e52274c27fc9068f9d`
(454498 bytes) in both references.

This is reference evidence only.  It is **not** proof that the phone's installed
`dtbo_b` equals either copied image, nor that the installed Android build is
exactly one of these Lineage builds.  The phone was last seen as GM1911,
Android 17, slot `_b`, orange AVB.  Do not erase or write the DTBO partition;
the remaining preflight below must compare it read-only first.

The coordinator's later rootless Android check reports
`ro.lineage.version=24.0-20261011-NIGHTLY-guacamole`, slot `_b`,
`ro.boot.dtbo_idx=0`, hardware version `21`, and orange AVB. These match the
October 11 reference's selection. A direct SHA-256 read of installed `dtbo_b`
was denied by Android permissions; the installed partition hash is still
unverified. No root or recovery action was attempted.

## Validation performed

`scripts/prepare_temporary_dtb.py` reuses the proven validator from the
neighbouring bring-up (`scripts/vendor/check-boot-dtb-quarantine.py`, copied
verbatim, SHA-256 `45ade635be2f8a93a1e7e3d5b5d6c49741a4af467353e1238fee828a3aee5357`).
The tool overrides the vendored validator's expected input hashes for the
Lineage 24.0 references; its historical report key `exact_20260824_hashes`
therefore means "the supplied reference hashes are exact", not that this
wrapper uses the older 20260824 build.

For the current base and stock entry it verified:

- deep stock-DTBO quarantine validator: **PASS**;
- all 245 external fixups and all 128 fragments resolve below the disabled
  quarantine; no target-path fragments;
- phandle namespace containment: no quarantine/live phandle overlap and no final
  reference cell escapes to a live phandle;
- post-overlay live tree outside quarantine is byte-for-byte unchanged
  (structural diff empty);
- a direct base-to-post-overlay structural diff contains only the reviewed root
  identity fields; no live node or other property changes;
- Qualcomm `libufdt` and host `fdtoverlay` post-overlay live trees agree after
  removing only `/__symbols__` metadata;
- libfdt application is a live-tree no-op outside quarantine;
- wrapper differs from the clean Nura DTB only by `/__symbols__`, the disabled
  quarantine subtree and the reviewed root identity fields;
- root selection/identity is exactly the stock entry 0 identity in the wrapper
  and in both post-overlay trees;
- the generated `/__symbols__` map contains exactly the 245 stock fixups, all
  pointing at the disabled sink; the quarantine has no `compatible`;
- no compiled C/H driver in `build/kernel-nura-r25/source` references
  `oneplus,guacamole`, `oneplus,oneplus7`, `qcom,sm8150-mtp` or `qcom,mtp`;
  the shared `qcom,sm8150` compatible remains in the wrapper;
- reserved-memory subtree is unchanged in the wrapper and after both overlays;
- the 10-entry stock DTBO table is bounds-checked and entry 0 is selected by
  project/PCB/board identity, not by position alone.

The stock boot pack and base selection were parsed independently for both boot
references.  No device command was run.

### Pre-existing reserved-memory finding

The unmodified Nura DTB already contains overlapping `no-map` reservations:

- `memory@85e00000` (0x85e00000, size 0x140000) overlaps
  `memory@85f00000` (0x85f00000, size 0x20000) and
  `memory@85f20000` (`qcom,cmd-db`, 0x85f20000, size 0x20000).

This comes from the flattened base plus the existing `patch_dtb.py` rename of
`memory@85d00000` to `memory@85e00000` with the upstream 0x140000 size.  The
wrapper preserves it exactly; it does not add or change a reservation.  Linux
memblock merges overlapping reservations, so this is not automatically a boot
failure, but it is worth checking against the live/stock DTB before a first
boot.  This task did not change it because hardware settings must remain
unchanged.

## Usage

```bash
# 1. Build/refresh the base DTB exactly as before.
python3 scripts/patch_dtb.py

# 2. Build and validate the temporary-boot wrapper.
python3 scripts/prepare_temporary_dtb.py

# 3. Verify the existing wrapper/manifest without rebuilding it.
python3 scripts/prepare_temporary_dtb.py --verify

# 4. Focused regression test (host-only).
python3 -m unittest discover -s tests -p 'test_prepare_temporary_dtb.py' -v
```

`scripts/build_bootimg.sh` now uses the project-local `mkbootimg.py` and
`avbtool.py`, verifies their copied hashes, prepends `local/bin`/`local/lib`,
and has a strict DTB mode:

```bash
# Preserve stock DTBO (first temporary boot).  Fails if the wrapper or
# manifest is missing/unverified, or if patch_dtb.py changed the base.
KERNEL_DIR=build/nura-kernel-package \
DTB_MODE=abl-quarantine \
INITRAMFS=/path/to/initramfs \
./scripts/build_bootimg.sh
```

In wrapper mode the script appends the netboot cmdline
`pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1` by default, matching the rootfs
worker's NBD subpartition selection and avoiding an internal pmOS root race.
`NETBOOT=0` disables that addition for a non-netboot wrapper boot.

`DTB_MODE=original` keeps the legacy, non-wrapper build path and prints a
warning that the active DTBO must be absent/zeroed.  The old
`/tmp/device_initramfs_r4` fallback was removed: an explicit `INITRAMFS` or the
chroot's `boot/initramfs` is required, so a stale ramdisk cannot be packaged
silently.

No `build/boot.img` was produced.  The initramfs/rootfs are still missing, and
this task intentionally did not manufacture a boot image with a dummy ramdisk.
The fail-closed wrapper-mode test (missing initramfs) ended before packing and
left no boot image; its transcript is `local/dtbo-build-script-failclosed.log`.

## Remaining exact-image preflight

1. **Installed DTBO evidence:** read the phone's `dtbo_b` (or live
   `androidboot.dtbo_idx` plus a read-only partition hash) and compare its
   entry 0 with `5755b70b...`.  The copied images are references, not proof of
   the installed partition.  Do not erase or flash DTBO.
2. **Slot/AVB state:** re-confirm slot `_b`, Android 17, GM1911/project 18821,
   unlocked, orange AVB and healthy A/B metadata immediately before any boot.
3. **Initramfs/rootfs:** produce the real Nura initramfs and rootfs first.  The
   coordinator's `docs/NURA_ROOTFS_BUILD.md` is the handoff; this task only owns
   the DTB wrapper and the boot-image interface.  The current artifact is a DTB
   wrapper only.
4. **Full boot image:** after initramfs exists, build with
   `DTB_MODE=abl-quarantine`, unpack it independently, and verify the v2 header
   fields, page size 4096, addresses `0x8000`/`0x01000000`/`0x100`/`0x01f00000`,
   the netboot cmdline tokens, wrapper hash, AVB footer, and no dummy/stale
   ramdisk.
5. **Entry/ABL compatibility:** the neighbouring bring-up separately flagged
   the ARM64 EFI/ABL entry-layout question.  This DTBO-only task did not change
   kernel entry code or the AVB envelope; treat that as a separate review item.
6. **Reserved-memory overlap:** compare the pre-existing 0x85e00000/0x85f00000
   overlap above with the installed/live DTB.  Do not change hardware settings
   as part of the wrapper task.

Hardware remains **UNTESTED** until a separately approved, non-persistent boot
actually reaches userspace.

## Provenance and licensing

- `scripts/vendor/check-boot-dtb-quarantine.py` is a verbatim copy of
  `bringup-workspace/scripts/check-boot-dtb-quarantine.py`
  (source SHA-256 `45ade635...`).  The source project's `dt_builder.py` declares
  SPDX `GPL-2.0-only`; this workspace is GPL-2.0 (`LICENSE`).  See
  `scripts/vendor/README.md`.
- `local/tools/ufdt/ufdt_overlay_test_app` is a host x86-64 binary copied from
  `bringup-workspace/.cache/first-custom-boot-candidate-004/tools/ufdt_overlay_test_app`
  (SHA-256 `c7d9db05...`, Build ID `70727df4605bb944ce1dbcec33f00f69aab785b8`).
  It dynamically links `libfdt.so.1`; this project uses
  `local/lib/libfdt.so.1` (SHA-256 `aff62061...`).  It is local-only and is not
  committed.  See `local/tools/ufdt/NOTICE` for upstream licensing notes.
- The copied Lineage references under `ref/lineageos/` remain read-only inputs;
  no neighbouring workspace file was modified.

## Safety

All work in this task was host-side: files, hashes, dtc/fdtget/fdtput/
fdtoverlay/libufdt, and tests.  No adb/fastboot command, device access, reboot,
flash, mount, partition read, root action, RF change, sudo, commit or push was
performed.
