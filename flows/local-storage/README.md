# Local storage boot flow

Status: **source-ready proposal only. Nothing here was built, flashed, mounted,
served or probed on a phone.** The actual rootfs/install/build steps remain
user-run. The working netboot flow under the source checkout's `scripts/` and
its rc3 artifacts are read-only and untouched.

This folder is the independent local-storage flow. The previous patch series in
`bringup-notes/nura-local-storage-20261112` is
report-only; no patch is applied to the original scripts. The first trial
reuses the actual rc3 kernel (`7.0.0-sm8150-rc3`). A future rebuilt kernel is
`7.0.0-sm8150-guacamole-rc4`; no binary is relabelled.

## Layout and source/flow split

```
flows/local-storage/
  ORIGIN-SHA256.json              hashes of the three originals before copying
  README.md
  scripts/
    local_storage_build.sh        the single user-run entrypoint
    rootfs_prepare_local.sh       adapted copy of rootfs_prepare_plasma.sh
    check_local_candidate.py      adapted copy of check_plasma_candidate.py
    build_local_bootimg.sh        adapted copy of build_bootimg_rear_camera.sh
    local_bootimg_wrapper.sh      local-only wrapper around the copied packer
    local_storage_tool.py         local guard/GPT/facts/manifest gate
    check_initramfs_release.py    copied unmodified helper
    check_rear_camera_package.py  copied unmodified helper
    check_boot_envelope.py        copied unmodified helper
  device/testing/...              copied recipes used by the local flow
  tests/                          rootless integration tests
  .work/local-userdata/           created only by a user-run --build
  build/local-userdata/           created only by a user-run --build
```

`SOURCE_ROOT="${NURA_SOURCE_ROOT:-...runtime-workspace}"` is explicit read-only
input. `FLOW_DIR` is the parent of `scripts/`, so the scripts are
working-directory independent. Local state/output are fixed under
`$FLOW_DIR/.work/local-userdata` and `$FLOW_DIR/build/local-userdata`; there are
no state/output path overrides to point at an ancestor of the source
checkout. The entrypoint also rejects a flow root equal to the source root.

## Originals read/copied

Read-only originals and copied counterparts (hashes in `ORIGIN-SHA256.json`):

| Original | Flow copy |
|---|---|
| `scripts/rootfs_prepare_plasma.sh` | `scripts/rootfs_prepare_local.sh` |
| `scripts/check_plasma_candidate.py` | `scripts/check_local_candidate.py` |
| `scripts/build_bootimg_rear_camera.sh` | `scripts/build_local_bootimg.sh` |

The copied recipes are under `device/testing/...` (device, firmware, rc3
kernel, modemmanager, hexagonrpcd, iio-sensor-proxy, the guarded hook, and
private libcamera). The boot DTB preparer and the local source tools are used
read-only from `SOURCE_ROOT`; no local mode was added to them. The original
scripts/recipes/tests/rc3 state/artifacts were not edited.

## One entrypoint

```bash
bash scripts/local_storage_build.sh          # dry-run plan
bash scripts/local_storage_build.sh --build  # user-run pmbootstrap flow
# Optional public-key seeding for later SSH diagnostics:
bash scripts/local_storage_build.sh --build --authorized-key /absolute/path/to/key.pub
```

The entrypoint:

1. verifies `ORIGIN-SHA256.json` against the source originals (the tests do
   this too);
2. checks its fixed local paths for symlinks, active mounts and a concurrent
   local build (`flock`), **before** any copy or cleanup;
3. copies only `linux-*` and `output` from the source `.work/rc3-kernel` into
   the flow kernel-state directory. It uses `rsync` to exclude the source-local
   `.output`, rejects source symlinks into `SOURCE_ROOT`, rewrites the copied
   `output/Makefile`, and repoints `output/source` at the local source tree;
4. stages the guarded flow hook recipe (pkgrel 5) and runs the real copied
   rootfs/checker pipeline with `NURA_LOCAL_USERDATA=1`;
5. runs the local boot wrapper and writes the final local
   `DELIVERY.local.json` only after the rootfs/installed-hook pair is present;
6. re-hashes the small source rc3 inputs and fails if any changed.

`--refresh-kernel` replaces the local kernel copy. A verified local copy is
otherwise reused on retry, so a failed rootfs/install attempt does not require
a new kernel copy. If the copy is incomplete/corrupt (for example a copy
interrupted before its stamp was written), the exact retry is:

```bash
bash scripts/local_storage_build.sh --build --refresh-kernel
```

The entrypoint does not silently delete or reuse an unverified copy. The final success message is truthful: the
rootfs step runs pmbootstrap/chroot and may mount/chown its isolated local work
directory; no phone mount/flash/slot/server action occurs.

## Rootfs/package ordering

- The immutable rc2 baseline hook remains pinned at `4.2.1-r4`
  (`BASELINE_HOOK_VERSION`); the candidate policy is r5
  (`CANDIDATE_HOOK_VERSION` / `NURA_NETBOOT_HOOK_VERSION`). The r4 baseline is
  therefore accepted while an r4 candidate policy is rejected.
- The copied rootfs pipeline seeds the pinned baseline APKs but **does not seed
  the r4 hook** for the local flow. It installs the real guarded flow recipe
  into local pmaports, then builds `postmarketos-mkinitfs-hook-netboot=4.2.1-r5`
  **before** install.
- The local flow preserves the hash-pinned system-libcamera control/data,
  re-signs those APKs with its own workspace key, and verifies their signatures
  before the first kernel build indexes the repository. Baseline public keys
  are also copied for the other unchanged APKs. No untrusted-install bypass is used.
- After install/export, the copied checker verifies the installed rootfs
  against the staged kernel/DTB/modules/UI/modem, the guarded installed hook,
  and the identical guarded initramfs hook.
- Local mode stops after the rootfs/initramfs verification and writes only
  `build/local-userdata/ROOTFS-INTERMEDIATE.txt`
  (`status=INTERMEDIATE-ROOTFS-ONLY`, `final_boot_image=NOT-BUILT`). It skips
  `pack_boot_image`, `write_kernel_manifest`, the netboot boot copy and
  `write_manifest`. The normal netboot defaults are not changed in the source
  scripts.
- The final boot image and final manifest are produced only by
  `local_bootimg_wrapper.sh` + `build_local_bootimg.sh`, with `NETBOOT=0` and
  `DTB_MODE=abl-quarantine`. The wrapper refuses a build without the rootfs
  image/tree, and the final tool requires the installed hook to match the
  initramfs hook. The final manifest cannot pass with a missing rootfs/hook.

## Tests (rootless)

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The nine rootless tests import the canonical
`tests/test_rootfs_prepare_plasma.py` fixture, copy this flow to a temporary
directory, and run the **real copied `rootfs_prepare_local.sh` and
`check_local_candidate.py`** with a fake pmbootstrap/admin layer (package builds, install tree/image, export) and real
cpio/zstd/checker paths. It asserts:

- the actual local rootfs path reaches `ROOTFS-INTERMEDIATE.txt` and does not
  write a netboot boot image/manifest;
- the guarded hook r5 is built before install and the baseline r4 is accepted;
- an r4 candidate policy is rejected;
- the full entrypoint reaches a final `HOST-VERIFIED` local manifest through
  the copied boot packer and local tool;
- a missing installed hook produces no final manifest;
- active mounts and a concurrent lock are refused before copy;
- a verified kernel copy is reused on retry, corruption requires
  `--refresh-kernel`, and a refresh recovers;
- source `output/source`/`output/Makefile` absolute links are neutralised and
  the source-local `.output` is not copied;
- `ORIGIN-SHA256.json` originals are unchanged and the copies exist.

The full-entrypoint test uses the fake pmbootstrap/admin layer and a minimal
DTB wrapper fixture; it runs the real copied rootfs/checker and real mkbootimg
packer, but not the real prepare_temporary_dtb wrapper. The fake layer does not
prove a real pmbootstrap build, real kernel/rootfs bytes, hardware, or the user
data/partition path. No test runs sudo, a mount,
a phone command, fastboot, or a write outside the temporary fixture.

## Remaining user-run prerequisites

- Parent review/integration of this flow (not the original patch series).
- Real user-run `local_storage_build.sh --build` with the actual pmbootstrap
  cache/chroot and real rootfs image.
- Real kernel/firmware/package sources and disk space; no source artifacts are
  modified by the flow.
- Independent verification of the final manifest and rootfs image before any
  userdata write.
- Userdata/slot facts, backups, recovery set and 4096-sector loop rehearsal
  remain prerequisites for any later commit; no destructive command exists in
  this folder.

No userdata write, boot-slot change, flash, firmware/DTBO/vbmeta change or
phone action is performed or proposed as an automatic step.

## Pre-install checks (user-run only)

`python3 scripts/rehearse_storage.py` lists the rehearsal requirements without
creating a loop device. `sudo python3 scripts/rehearse_storage.py --run` creates
and grows a disposable 4096-sector GPT/ext4 image under this flow's `.work`.
It accepts no disk/partition argument and checks backing-file ownership before
detaching its loop. It never addresses phone storage. A host pass does not
establish the target BusyBox scanner or phone UFS behaviour.

`scripts/backup_boot_chain.sh` is USER RUN ON THE PHONE with `sudo sh` while
booted in the known rc3 netboot environment. It reads only `boot_b`, `dtbo_b`
and `vbmeta_b`, verifies sizes and before/copy/after hashes, and writes temporary
backup files under `/tmp/nura-boot-chain.*`. Copy that exact directory to the host
and verify `SHA256SUMS` there before rebooting. No partition writes occur.
These prerequisite checks are not an instruction to flash userdata or boot_b.

## Separate non-installing debug image

After saving backups on the host, reboot the phone out of Linux and stop the
NBD server. As the normal host user, run:

```bash
bash flows/local-storage/scripts/local_storage_debug_bootimg.sh
```

This repacks the existing local initramfs/kernel into
`build/local-debug/boot/boot.img` with `pmos.debug-shell`, leaving the normal
local boot image and original rc3 images unchanged. It does not rebuild packages
or run fastboot. The explicit debug stop is before root discovery/resize in the
pinned second-stage init script. This is a temporary USB debug environment,
not an internal installation or a completed Plasma boot. Do not run
`pmos_continue_boot` or any formatting/resize command as part of this probe.
