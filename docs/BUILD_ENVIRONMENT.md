# Recreating the local build environment

Git carries recipes, patches and validation scripts. Tools, source archives,
firmware, stock reference images and build outputs are ignored local inputs.
Pulling this repository does not restore them.

## Paths

From the checkout root, copy `build-env.example.sh` to `build-env.local.sh`.
Edit `NURA_BASELINE_ROOT` and `NURA_SSH_PUBLIC_KEY`, then load it in Bash:

```bash
source ./build-env.local.sh
cd "$NURA_SOURCE_ROOT"
```

For fish, start Bash first, then use the commands in this guide:

```fish
bash
```

Alternatively, run a complete block with `bash -c 'source ./build-env.local.sh;
<commands>'`. Do not source `build-env.local.sh` directly in fish: it uses Bash
syntax, and environment changes in a child Bash do not propagate back to fish.

`build-env.local.sh` is ignored and remains machine-specific. The baseline is a
separate, preserved runtime workspace (the one that produced the rc2/rc3
images), and the public key is usually `~/.ssh/id_ed25519.pub`. The baseline is
read-only; it supplies the pinned pmaports, pmbootstrap and older APK inputs,
while this checkout owns the current recipes and candidate outputs.

The rc4 entrypoints (`flows/rc4/scripts/rc4.sh` and
`flows/rc4/local-storage/scripts/*`) default `NURA_SOURCE_ROOT` to the
checkout that contains them, so it only needs exporting to point at a different
checkout. Older scripts outside `flows/rc4` still default to a placeholder path
and stop with `missing source checkout` unless it is exported (the environment
file does).

| Variable | Purpose |
| --- | --- |
| `NURA_SOURCE_ROOT` | Current checkout, derived from the environment file's location |
| `NURA_BASELINE_ROOT` | Preserved runtime containing `.work/rear-camera` and `build/rear-camera` |
| `RC4_KERNEL_ROOT` | Kernel build directory for one candidate; use a fresh one per candidate (for example `.work/rc4n-build`) and never reuse an old one |
| `NURA_KERNEL_BUILD` | Kernel output containing `Image`, `kernel-package`, `kernel-inputs.json` |
| `NURA_KERNEL_SOURCE_STATE` | Candidate kernel source and kbuild output for the local rootfs entrypoint |
| `NURA_KERNEL_DIR` | Staged kernel package for a boot-image-only build |
| `NURA_SSH_PUBLIC_KEY` | Public key passed explicitly to `--authorized-key` |
| `NURA_FASTBOOT` | Project-local Android platform-tools fastboot executable |
| `NURA_LOCAL_INITRAMFS` | Verified rootfs export initramfs for boot-image packaging |
| `NURA_LOCAL_ROOTFS_IMAGE` | Built userdata image for manifest recording |
| `NURA_LOCAL_ROOTFS_TREE` | Installed rootfs tree for hook validation |

The example targets Phosh. For Plasma use the flow's documented Plasma paths;
the three explicit retry paths above must also change. Do not export
`NURA_KERNEL_STATE` globally: the kernel builder, rootfs entrypoint and packer
use it at different stages, and the rootfs entrypoint manages an isolated copy.

## Required ignored files

Restore these from a trusted prepared workspace or regenerate them using the
referenced recipe/extraction instructions. Keep all checksum checks enabled.

On a fresh checkout, most of them can be restored with one command, which
first checks host prerequisites and prints the packages to install:

```bash
bash scripts/fetch_inputs.sh --lineage-ota /path/to/lineage-24.0-*-guacamole-signed.zip
bash scripts/fetch_inputs.sh --check      # later: verify only
```

It fetches platform-tools, mkbootimg and avbtool, each verified against the
pins below. It provides the DTB tools through `scripts/build_host_tools.sh`,
which links the system `dtc` (1.7 or newer) and builds `ufdt_overlay_test_app`
from AOSP libufdt, recording them in `local/TOOLS.json`. It also extracts the
stock `dtbo.img`/`boot.img` from the OTA. The DTBO is accepted by its entry 0
(`ENTRY_SHA256`), so any Lineage 24 build with the same entry works. The stock
`boot.img` is only an optional cross-check of the hard-coded root identity.
System `dtc` 1.8.1 produced a wrapper and merged trees byte-identical to the
pinned 1.7.2 binaries. The same OTA is the input for
`extract_lineage_firmware.py` and `prepare_sensor_bundle.py`.

| Destination in this checkout | Needed for | Source / validation |
| --- | --- | --- |
| `$RC4_KERNEL_ROOT/kernel-state/linux-v7.0.0-sm8150.tar.gz` | kernel | Source archive (about 250 MB); its SHA-512 is pinned in the kernel APKBUILD and checked by the builder |
| `local/bin/bc` | kernel | Host build tool used by kbuild; the builder puts `local/bin` first on `PATH` |
| `local/bin/{dtc,fdtget,fdtput,fdtoverlay}` | boot image | Exact binaries pinned in `scripts/prepare_temporary_dtb.py:TOOL_HASHES` |
| `local/lib/libfdt.so.1` | boot image | Shared library pinned in the same table |
| `local/tools/ufdt/ufdt_overlay_test_app` | boot image | Overlay validator pinned in the same table |
| `local/tools/mkbootimg/` | boot image | Complete tool directory, including `gki/` and `unpack_bootimg.py`; `mkbootimg.py` pinned by `build_local_bootimg.sh` |
| `local/tools/avbtool.py` | boot image | Pinned by `flows/rc4/local-storage/scripts/build_local_bootimg.sh` |
| `ref/lineageos/lineage-24.0-20261102/{boot.img,dtbo.img}` | boot image | Stock references pinned in `prepare_temporary_dtb.py` |
| `platform-tools/fastboot` | phone | Android SDK platform-tools; check `--version` and `devices` before phone commands |
| `flows/local-storage/device/testing/firmware-oneplus-guacamole/firmware-oneplus-guacamole-24.0-20261011.tar.gz` | rootfs | Local firmware bundle pinned in that directory's APKBUILD; regeneration: `scripts/extract_lineage_firmware.py` |
| `flows/rc4/device/testing/device-oneplus-guacamole/guacamole-sensors-20261102.tar.gz` | rootfs | Sensor files bundle pinned in that directory's APKBUILD; regeneration: `scripts/prepare_sensor_bundle.py` |

The kernel build needs host Clang/LLD 23.x and pahole. A newer Clang patch
release only changes the `CONFIG_*_VERSION` strings in `.config`; compare
`Module.symvers` and the BTF instead (see below).

`NURA_BASELINE_ROOT/.work/rear-camera` must contain the pinned `pmaports/`,
`pmbootstrap/` and `rootfs/pmbootstrap.cfg`; its `build/rear-camera` must contain
the baseline APKs and image identity expected by `check_local_candidate.py`.
An empty directory is insufficient. The read-only preflight validates these
inputs. Downloads used by recipes are verified separately during package builds.

### Without the preserved baseline (fresh baseline)

A fresh checkout can recreate the baseline from public sources instead:

```bash
bash scripts/bootstrap_baseline.sh          # .work/baseline, about 25 s
export NURA_BASELINE_ROOT="$PWD/.work/baseline"
```

This fetches pmaports and pmbootstrap at the commits in
`device/testing/rear-camera/SOURCE_PINS.json`, applies the netboot-port patch,
and writes the config and APK keys. It does not recreate `build/rear-camera`.
Build with `--fresh-baseline` so that the flow builds libcamera 0.8 (pinned
pmaports `temp/libcamera`) and `libcamera-rear` from their recipes instead of
reusing the rear1 APKs:

```bash
NURA_FRESH_BASELINE=1 NURA_KERNEL_STATE="$NURA_KERNEL_SOURCE_STATE" \
  bash flows/rc4/local-storage/scripts/rootfs_prepare_local.sh --check-only
bash flows/rc4/local-storage/scripts/local_storage_build.sh \
  --ui "$NURA_UI" --build --fresh-baseline --authorized-key "$NURA_SSH_PUBLIC_KEY"
```

Those libraries are checked by version (`99990.8.0-r1`, `0.7.2-r0`), and the
installed `libcamera.so` files must match the APKs just built. They are not
the rear1 binaries that were tested on hardware, so test the cameras after the
first fresh build. Fresh mode exists for the local-userdata profile only; the
netboot `manifest`/`check` commands refuse it.

## Build and retry commands

Build the kernel rootlessly:

```bash
JOBS=16 bash flows/rc4/scripts/rc4.sh kernel
```

Validate rootfs source inputs without starting chroots:

```bash
NURA_KERNEL_STATE="$NURA_KERNEL_SOURCE_STATE" \
  bash flows/rc4/local-storage/scripts/rootfs_prepare_local.sh --check-only
```

Build the rootfs and boot image (user-run: invokes privileged pmbootstrap):

```bash
bash flows/rc4/local-storage/scripts/local_storage_build.sh \
  --ui "$NURA_UI" --build --authorized-key "$NURA_SSH_PUBLIC_KEY"
```

If the rootfs passed and only boot-image packaging failed, restore the missing
host inputs and retry just the host-only packer:

```bash
bash flows/rc4/local-storage/scripts/local_bootimg_wrapper.sh --build
```

Verify the resulting manifest:

```bash
python3 flows/rc4/local-storage/scripts/local_storage_tool.py check \
  --manifest flows/rc4/local-storage/build/local-userdata-phosh/DELIVERY.local.json
```

After a failed rootfs build, active chroots must be cleared before restarting
the full build (user-run):

```bash
bash flows/rc4/scripts/rc4.sh shutdown --ui "$NURA_UI"
```

Outputs are in `flows/rc4/local-storage/build/local-userdata-phosh/` (boot image
and manifest) and the rootfs path specified by `NURA_LOCAL_ROOTFS_IMAGE`.
These commands do not flash the phone. Hardware readiness remains a separate
check.

## Installing a fresh rootfs onto the phone

This **replaces** the Linux rootfs on `userdata` (`/home/user` included).
Build the matching debug image first, then run the guarded installer:

```bash
bash flows/rc4/local-storage/scripts/local_storage_debug_bootimg.sh
python3 flows/rc4/local-storage/scripts/install_userdata.py --dry-run
python3 flows/rc4/local-storage/scripts/install_userdata.py
```

It re-verifies `DELIVERY.local.json`, backs up `/home/user` and
`/etc/apk/world` into `backups/userdata-install-<time>/` when the phone is
reachable over SSH, and checks the bootloader: one device, a guacamole
product, unlocked, slot `b`, and a userdata partition bigger than the image.
After you type `ERASE USERDATA` it runs `fastboot -S 64M flash userdata`. It
then RAM-boots the debug image and runs a read-only `e2fsck` of both
subpartitions in the initramfs shell. If that is clean it continues the boot,
waits for SSH and checks the kernel release. It only **prints** the commands
for RAM-booting the normal image and flashing `boot_b`. Its fastboot wrapper
can issue only `devices`, `getvar`, `-S 64M flash userdata` and `boot`.

If the bootloader's `product` is not recognised (for example `msmnile`), the
script stops. Rerun it with `--accept-product <exact value>` after checking
that the device really is the OnePlus 7 Pro.

## Boot image from a kernel build, without a rootfs build

This packs a local-boot image from `rc4.sh kernel` output and a known-good
initramfs. It is host-only and needs no pmbootstrap or sudo. The packer refuses
an initramfs whose module release differs from the kernel's, and `NETBOOT=0`
leaves `pmos.root`/`pmos.boot` off the cmdline.

```bash
NURA_SOURCE_ROOT="$PWD" \
KERNEL_SOURCE="$RC4_KERNEL_ROOT/kernel-state/linux-v7.0.0-sm8150" \
KERNEL_DIR="$NURA_KERNEL_DIR" \
OUTPUT_DIR="$RC4_KERNEL_ROOT/boot-local" \
INITRAMFS=/path/to/initramfs \
NETBOOT=0 \
  bash flows/rc4/local-storage/scripts/build_local_bootimg.sh
```

`KERNEL_SOURCE` is required whenever the kernel was built under
`RC4_KERNEL_ROOT` rather than the default `flows/rc4/build-source`.

Use an initramfs whose `hooks/netboot.sh` is the guarded hook from
`flows/local-storage/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot`
(it exits unless the cmdline contains `pmos.root=/dev/nbd`). An unguarded hook
makes a local boot hang at `Waiting for netboot`. The hardware-tested local
initramfs has SHA-256 `7414497f092aa790d87ff44e7e336a16089a7a72d5173c60ac29a2ec6ca41397`.
Since the guarded hook package (`postmarketos-mkinitfs-hook-netboot-4.2.1-r5`)
was installed on the phone, the phone's own `/boot/initramfs` is that same file;
copy it with `scp user@172.16.42.1:/boot/initramfs` and check its hash.

On 2026-11-19 an rc4n image built this way from a clean checkout of this
repository booted from `boot_b` with all checks passing.

## Upgrading the kernel while keeping userdata

The rootfs lives on `userdata`; the boot image lives on `boot_b`. A kernel
upgrade never needs a userdata flash. The release string stays
`7.0.0-sm8150-guacamole-rc4` across candidates, so what matters is whether the
module ABI changed.

First compare the new build with the running kernel:

```bash
sha256sum "$RC4_KERNEL_ROOT/kernel-state/output/Module.symvers"
llvm-objcopy --dump-section .BTF=/dev/stdout \
  "$RC4_KERNEL_ROOT/kernel-state/output/vmlinux" | sha256sum
ssh user@172.16.42.1 'sha256sum /sys/kernel/btf/vmlinux'
```

Compare `Module.symvers` with the previous candidate's build directory.

### Same ABI (device-tree or code changes that keep `Module.symvers` and BTF)

The installed modules keep working. Build the boot image as above, RAM-boot it
first, then flash it:

```bash
bash scripts/fastboot_boot.sh "$RC4_KERNEL_ROOT/boot-local/boot.img"
# check over SSH, then from the bootloader again:
"$NURA_FASTBOOT" flash boot_b "$RC4_KERNEL_ROOT/boot-local/boot.img"
"$NURA_FASTBOOT" reboot
```

rc4n (patch 0075, DT only) was delivered this way.

### Changed ABI (new modules, config changes)

The new modules must be installed on the phone together with the new kernel.

1. Build the kernel package (user-run; also builds a rootfs image, which you do
   not flash):

   ```bash
   bash flows/rc4/local-storage/scripts/local_storage_build.sh \
     --ui "$NURA_UI" --build --refresh-kernel --authorized-key "$NURA_SSH_PUBLIC_KEY"
   ```

   The APK is
   `flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/packages/edge/aarch64/linux-postmarketos-qcom-sm8150-*.apk`.
   It is signed with that pmbootstrap work directory's key. The phone trusts
   the key under `/etc/apk/keys/` that was installed with the rootfs; a new work
   directory has a new key, which must be copied to the phone first.
2. Keep the currently installed kernel APK as the rollback package.
3. Install it on the phone and sync:

   ```bash
   scp <kernel apk> user@172.16.42.1:/tmp/
   ssh -t user@172.16.42.1 'sudo apk add /tmp/<kernel apk> && sync'
   ```

   This replaces `/lib/modules/7.0.0-sm8150-guacamole-rc4`. Because the release
   string is unchanged, the kernel still in `boot_b` can no longer load modules
   with a different ABI, so boot the new kernel next. mkinitfs regenerates
   `/boot/initramfs` and `/boot/boot.img` with the guarded hook.
4. Build the boot image from the new kernel and the phone's regenerated
   `/boot/initramfs`, or use the flow's
   `flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img`. RAM-boot,
   check, then flash `boot_b` as above.

To roll back, reinstall the previous kernel APK with `apk add` and flash the
previous boot image to `boot_b`. Only flash a phone-generated
`/boot/boot.img` if its kernel package matches the kernel you want.
