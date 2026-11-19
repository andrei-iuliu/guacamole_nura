# archive/

Superseded build and boot scripts, kept for reference and history. Nothing
in the current flow (`./nura`, `flows/rc4/`, the device packages) uses them;
this was checked by following every script reference from those roots before
moving them (2026-11-19).

They are **not runnable from here as-is**: they compute paths relative to
their own location, which was `scripts/`. To run one, use it from the commit
before the move, for example
`git show 5368975:scripts/build_bootimg.sh > /tmp/build_bootimg.sh`.
Their tests still run from `tests/` against these files.

Dated documents under `docs/` and `flows/rc4/topics/` keep the original
`scripts/...` paths; this table maps them.

| Old path | Now | Era | Replaced by |
|---|---|---|---|
| `scripts/rootfs_prepare.sh` | `archive/scripts/rootfs_prepare.sh` | 6.17 netboot | `./nura build` |
| `scripts/envsetup.sh` | `archive/scripts/envsetup.sh` | 6.17 | `./nura` + `build-env.local.sh` |
| `scripts/build_kernel.sh` | `archive/scripts/build_kernel.sh` | 6.17 envkernel | `./nura kernel` |
| `scripts/build_bootimg.sh` | `archive/scripts/build_bootimg.sh` | 6.17 boot image | `./nura build` (boot image step) |
| `scripts/flash_boot.sh` | `archive/scripts/flash_boot.sh` | 6.17 | `scripts/fastboot_boot.sh`, `./nura install` |
| `scripts/rootfs_prepare_7_0.sh` | `archive/scripts/rootfs_prepare_7_0.sh` | 7.0 netboot | `./nura build` |
| `scripts/build_bootimg_7_0.sh` | `archive/scripts/build_bootimg_7_0.sh` | 7.0 boot image | `./nura build` |
| `scripts/boot_7_0.sh` | `archive/scripts/boot_7_0.sh` | 7.0 netboot launch | `./nura install` |
| `scripts/serve_7_0.sh` | `archive/scripts/serve_7_0.sh` | 7.0 NBD server | local userdata install |
| `scripts/boot_rear_camera.sh` | `archive/scripts/boot_rear_camera.sh` | rear-camera netboot launch | `./nura install` |

`flows/local-storage/` (the rc3-era local flow) stays in place: its
`device/testing/` recipes are linked by `flows/rc4/local-storage/device/testing`,
and its scripts are provenance inputs recorded in
`flows/rc4/local-storage/ORIGIN-SHA256.json` and `flows/rc4/tests/rc3-baseline.sha256`.
Its own test suite is not maintained (it predates the rc4 layout).
