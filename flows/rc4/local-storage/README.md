# rc4 local-storage packaging flow (user-run)

A copy of the reviewed `flows/local-storage` flow, adapted for the rc4 kernel
candidate (`7.0.0-sm8150-guacamole-rc4`). The heavy pipeline is the same code;
the constants, the kernel inputs and every state/output path are rc4-specific,
and all state stays inside `flows/rc4`.

**Nothing here was run end-to-end by the integration work.** The read-only
guards and the dry-run refusals are tested; the rootfs/boot steps are the user's
first run. There is no flash, boot, mount or repartition action anywhere in this
flow, and no automatic cleanup.

## 1. What it builds

| Step | Script | Writes |
| --- | --- | --- |
| kernel package (rootless, separate) | `flows/rc4/scripts/rc4.sh kernel` → `scripts/build_rear_camera_kernel.sh` with `NURA_*` | `flows/rc4/build-source/kernel-state/` (`linux-*/`, `output/`) and `flows/rc4/build-source/kernel-output/` (`kernel-package/`, `Image`, `kernel-inputs.json`) |
| rootfs + initramfs (pmbootstrap, user-run) | `scripts/local_storage_build.sh --build` → `rootfs_prepare_local.sh` | `flows/rc4/local-storage/.work/local-userdata/` |
| boot image | `local_bootimg_wrapper.sh --build` → `build_local_bootimg.sh` | `flows/rc4/local-storage/build/local-userdata/` |
| final manifest check | `local_storage_tool.py check` | — (read-only) |

The kernel is **not** built by this flow: `local_storage_build.sh` copies the
already-built `flows/rc4/build-source/kernel-state` (the builder's `output/`
lives inside it, as in rc3) into its isolated state
(excluding the source-local `.output`, rewriting `output/Makefile`, repointing
`output/source` resolving to that tree — absolute or relative — while still
refusing a dangling link, a link to another tree inside the state, or a link
that leaves the state), and requires
`flows/rc4/build-source/kernel-output/kernel-inputs.json` (written by
`kernel-record`, not by the builder) plus `kernel-output/kernel-package` whose
`kernel.release` is exactly `7.0.0-sm8150-guacamole-rc4`. Before copying it
calls `check_local_candidate.py kernel-copy-check`, which validates the source
tree, `output/`, `.config` and an `output/source` link whose resolved target is
exactly that tree (kbuild creates it absolute; the copy rewrites it relative).

The kernel `DELIVERY.json` is **not** required by this local profile: the local
rootfs phase stops after the intermediate export and the boot packer writes
`DELIVERY.local.json` in `flows/rc4/local-storage/build/local-userdata`. The
kernel `DELIVERY.json` belongs to the non-local (`kernel-manifest`) pipeline.

## 2. Entry points

```bash
# dry-run plan; writes nothing
bash flows/rc4/local-storage/scripts/local_storage_build.sh

# user-run build (needs the rc4 kernel build first)
bash flows/rc4/local-storage/scripts/local_storage_build.sh --build \
    --authorized-key /absolute/path/to/key.pub

# repack only the boot image from an existing rootfs export
bash flows/rc4/local-storage/scripts/local_bootimg_wrapper.sh --build \
    --initramfs <exported initramfs> --rootfs-image <image> --rootfs-tree <tree>

# or use the wrapper, which carries the right environment
bash flows/rc4/scripts/rc4.sh rootfs --authorized-key /absolute/path/to/key.pub
```

`--authorized-key` is optional but must be an absolute path to a **public** key;
private key material is refused by `check_local_candidate.py`. Without it the
build adds no key.

## 3. Constant map (rc3 → rc4)

Every change is a deliberate constant, not a global replacement. The files that
still mention rc3 do so for the rollback baseline and are listed at the bottom.

| Constant | rc3 flow | rc4 flow |
| --- | --- | --- |
| release | `7.0.0-sm8150-rc3` | `7.0.0-sm8150-guacamole-rc4` |
| release pattern | `7.0.0-sm8150-(rc\|rear)N` | `7.0.0-sm8150-(guacamole-rc\|rc\|rear)N` |
| candidate label | `nura-plasma-rc3` | `nura-plasma-rc4` |
| kernel recipe | `device/testing/rc3/linux-postmarketos-qcom-sm8150` | `flows/rc4/device/testing/linux-postmarketos-qcom-sm8150` |
| kernel state | `$SOURCE_ROOT/.work/rc3-kernel` | `$SOURCE_ROOT/flows/rc4/build-source/kernel-state` (contains `output/`) |
| kernel output | `$SOURCE_ROOT/build/rc3-kernel` | `$SOURCE_ROOT/flows/rc4/build-source/kernel-output` (`kernel-package/`, `Image`, `kernel-inputs.json`) |
| recipe root | `$ROOT/device/testing/...` copies | `$ROOT/device/testing` link farm: rc4 kernel/device recipes + read-only rc3-era recipes |
| distfiles | `.work/rc3-distfiles` | `.work/rc4-distfiles` (under this flow) |
| unchanged recipes | `$ROOT/device/testing/...` copies | read-only links to `flows/local-storage/device/testing/...` |
| MSS unit | — | added to the rc4 device package, not enabled |
| qcomtee gate | — | `module_blacklist=qcomtee` in `build_local_bootimg.sh` |

## 4. Layout

```
flows/rc4/local-storage/
  scripts/                      adapted copies (12 files)
  tests/                        copied public-key + storage-rehearsal tests
  ORIGIN-SHA256.json            hashes of the inputs the copies came from
  device/testing/               links only:
      device-oneplus-guacamole            -> flows/rc4/device/testing/... (rc4, pkgrel 15)
      linux-postmarketos-qcom-sm8150      -> flows/rc4/device/testing/... (rc4 recipe)
      firmware-oneplus-guacamole, hexagonrpcd, iio-sensor-proxy, nura-clock,
      rc3, rear-camera                    -> flows/local-storage/device/testing/... (read-only)
```

Unchanged recipes are linked, not copied: the firmware recipe alone is 39 MB and
must stay byte-identical to the reviewed one. The integration tests assert that
the links resolve and that the reviewed copies are unchanged
(`flows/rc4/tests/rc3-baseline.sha256`).

## 5. Guards kept from the reviewed flow

* local root, never NBD: the hook recipe's `netboot.sh` exits early when
  `pmos.root=/dev/nbd` is absent, and `rootfs_prepare_local.sh` verifies that
  guard (`grep -Fq 'pmos\.root=/dev/nbd'`) plus the `netboot.sh` checksum before
  staging it;
* `refuse_active_mounts` on the kernel state/output and on the flow state
  before anything is copied;
* `flock` on `$LOCAL_STATE/.build.lock` so two builds cannot run at once;
* `canonical_snapshot` before/after the build: the read-only rc4 inputs must be
  unchanged or the run fails;
* no local path may be a symlink, the flow root, or overlap the checkout's
  `.work`/`build`/`scripts`/`device`/`tests`/`ref`/`local`.

## 6. Deliberate rc3 references that stay

| Reference | Why it stays |
| --- | --- |
| `SRC_STATE=$SOURCE_ROOT/.work/rear-camera`, `SRC_OUT=$SOURCE_ROOT/build/rear-camera` | read-only rc2 APK baseline used for the libcamera ABI comparison and the rollback |
| `LEGACY_RELEASE=7.0.0-sm8150-rear1` (in `check_local_candidate.py`) | the old netboot chain remains valid only as an explicit rollback |
| `device/testing/rc3/modemmanager` (via `RECIPE_ROOT`) | unchanged pinned ModemManager recipe; rc4 does not modify ModemManager |
| `device/testing/rear-camera/...` (via `RECIPE_ROOT`) | the guarded mkinitfs hook and private libcamera recipe are unchanged |
| mentioned "rc3 netboot/boot image" in refusal messages | the guard forbids writing into the rc3 delivery directories; that is the point |

`flows/rc4/tests/test_rc4_integration.py` asserts that no *kernel* input points
at an rc3 path, and `flows/rc4/tests/test_rc4_flow_guards.py` asserts the guard
order. A global `rc3 → rc4` replacement would have broken the rollback baseline,
which is why it was not done.
