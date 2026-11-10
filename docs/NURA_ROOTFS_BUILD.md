# Nura oneplus-guacamole rootfs preparation

> This document describes the older 6.17 baseline. For the current
> `7.0.0-sm8150-rear1` rear-camera candidate, use
> `bash scripts/rootfs_prepare_rear_camera.sh` as documented in
> `docs/REAR_CAMERA_7_0_BRINGUP.md`.

Status: **package inputs, sensor payload and host checks ready; rootfs image,
initramfs and boot image are still missing**. No rootfs build, mount, chroot,
netboot or hardware action was performed while preparing this document. The
only blocking step is a user-run privileged `pmbootstrap` build; the exact
entrypoint is below.

This keeps the original Nura driver stack (kernel `v6.17.0-sm8150`, all 24
patches, custom DTB) and the Phosh + systemd baseline. Nothing here replaces
the kernel, its config, its patches or `base_guacamole_working.dts`.

## What is ready

| Input | Identity |
|---|---|
| pmaports | `b2b857484723945088cdbe5f5122c72fec4dc834`, channel `edge` |
| pmbootstrap | `e0dfe2cda7f3a0b70cdcc96f3060100839322aa4` (3.12.1) |
| kernel source | `gitlab.com/sm8150-mainline/linux` tag `v6.17.0-sm8150`, commit `379d8fe35c7ca685a650bd82fd023af0ea3f0de0`, 24 patches |
| kernel build | `build/kernel-nura-r25`, release `6.17.0-sm8150`, 828 modules |
| kernel image | `build/nura-kernel-package/boot/vmlinuz`, SHA-256 `f2198a45b94db92f1a110b70153ae11ad2bbae9bc61e7490c915731b1d30d088` |
| firmware bundle | `device/testing/firmware-oneplus-guacamole/firmware-oneplus-guacamole-24.0-20261011.tar.gz`, SHA-512 `bb1b5157b8c505b35b2bfe5bff2b6661828949dd1b0cdb30b183ca51e4ffa98a284342e73f807bf6e7752c64a82a4832d44b53ba3736a971100b17fc07b20ed2` (matches the original APKBUILD) |
| sensor bundle | `device/testing/device-oneplus-guacamole/guacamole-sensors-20261102.tar.gz`, SHA-512 `9b1c4fde9e47335631c7ab90eaf47f1425b3130126f80eadd523abb5cb42db273c179928db877299be1760a8b6ca5fdc723b1f4a330bed0d22e67ba209213ebd`, 83 files |
| rootfs builder | `scripts/rootfs_prepare.sh` (check-only by default; `--build` is user-run) |

Run the read-only preflight at any time:

```sh
./scripts/rootfs_prepare.sh --check-only
```

It verifies host commands, the five local recipe symlinks in `.work/pmaports`,
the pmaports and pmbootstrap pins, both local tarballs, all 16 ADSP sources,
the kernel image/modules identity and the project-local pmbootstrap config.
It does not invoke `sudo`, mount anything or touch the phone.

## Changes made for the rootfs

### ADSP audio firmware is now packaged

The flattened DTB requests `qcom/sm8150/oneplus7/adsp.mdt`, but the original
recipe only installed GPU, modem, IPA, SLPI and WLAN/BT files. Without ADSP the
speakers and WCD9340 microphone cannot work even though the TFA9874/WCD9340
patches and UCM files are present.

`firmware-oneplus-guacamole` r7 adds `adsp.mdt` and `adsp.b00`..`adsp.b14`
from the pinned M1DNYT3 repository
(`f071a51faf6f6250c35662e8091deb2cb484a025`, `qcom/sm8150/oneplus7/`), with
explicit SHA-512 entries in the APKBUILD. The public blobs are also cached in
`.work/pmbootstrap/cache_distfiles` for offline inspection; pmbootstrap
re-verifies them before building. `adsp.mdt` is a Qualcomm DSP6 ELF and the
whole set is 16 files. This is the same provenance as the already used SLPI
firmware. It has not been booted on this phone.

### Dependencies that are actually required

- `python3` is now a `device-oneplus-guacamole` dependency. The charging policy
  daemon is `#!/usr/bin/env python3` and imports only the standard library, but
  no other package was guaranteeing it.
- `iio-sensor-proxy-systemd` is deliberately **not** added to the device
  `depends`. abuild's default `-systemd` split function sets
  `install_if="systemd iio-sensor-proxy=$pkgver-r$pkgrel"`, so the unit package
  is selected automatically whenever `iio-sensor-proxy` and `systemd` are
  installed. pmaports additionally ships
  `postmarketos-base-ui-iio-sensor-proxy-systemd` (`install_if` includes
  `postmarketos-base-ui iio-sensor-proxy systemd`) for its shutdown drop-in.
  Phosh pulls `postmarketos-base-ui-gnome` → `postmarketos-base-ui`, so the
  target has both paths. The device drop-in is a separate file and is harmless
  if the unit package is installed first or second.
- `hexagonrpcd.pre-install` does **not** need to be added to `source=`.
  abuild reads `install=` scripts from `$startdir`, not `$srcdir`; pmaports
  itself relies on this (for example
  `main/postmarketos-boot-hwtest/postmarketos-boot-hwtest.post-install` is not
  in `source=` or `sha512sums`). The earlier audit's suggestion was incorrect.
- OpenRC-only fixes were not made. The baseline is Phosh + systemd; adding
  `pd-mapper`/`rmtfs` OpenRC dependencies would be untested dead weight here.

### The per-unit sensor registry is now fail-closed

The old package built a `guacamole-sensors-20261014.tar.gz` that contained
`registry-default/registry`, a snapshot of the author's phone. The registry is
unit-specific factory calibration and the DSP also rewrites parts of it.

The new flow:

1. `scripts/prepare_sensor_bundle.py` builds
   `guacamole-sensors-20261102.tar.gz` from the public LineageOS 24.0
   20261102 OTA payload: 65 `vendor_b/etc/sensors/config/*.json`, the 553-byte
   `sns_reg_config` renamed to `sns_reg.conf`, and 17 `dsp_b/sdsp/*` files.
   Extraction is read-only `debugfs`; it never mounts the images.
2. The tarball is reproducible (sorted entries, uid/gid 0, mtime 0, modes
   0644/0755, gzip mtime 0). Extracting the real
   `.work/sensor-partitions/{vendor.img,dsp.img}` and packaging produces the
   exact same SHA-512 as the checked-in local bundle. `tests/test_prepare_sensor_bundle.py`
   covers reproducibility, missing-input failure and the debugfs extraction path.
3. `device-oneplus-guacamole` no longer installs any registry snapshot.
4. On first boot `guacamole-sensors-registry` still imports this unit's own
   `/persist` → `/sensors/registry` with `debugfs` (read-only). If that fails,
   it prints a clear error and exits non-zero. It does **not** copy a foreign,
   default, zero or fake registry, and it does not claim calibration succeeded.
5. A new drop-in,
   `hexagonrpcd-sdsp.service.d/guacamole-registry.conf`, makes
   `hexagonrpcd-sdsp.service` require the registry import. A failed import
   therefore leaves the sensor DSP file server and sensors off instead of
   serving an uncalibrated/foreign tree. `tests/test_sensors_registry.py` covers
   successful import, missing calibration, empty calibration and reuse of an
   already imported registry without root or phone access.

**Payload identity warning:** the original private
`guacamole-sensors-20261014.tar.gz` is still unavailable. The new bundle was
not compared byte-for-byte with it. The original archive is dated 20261014, while this
payload comes from the 20261102 OTA. Even if the firmware/version strings look
the same, this document does not claim the two archives are identical. The old
archive's origin and exact file set are unknown. The per-unit calibration is
intentionally absent.

### Host helpers and ignored evidence

- `scripts/prepare_sensor_bundle.py` is the deterministic sensor
  extraction/packaging helper. It supports `--source` (already extracted tree)
  or `--vendor-img` + `--dsp-img` (real ext4 images, unprivileged `debugfs`).
- `scripts/rootfs_prepare.sh` writes the project-local pmbootstrap config and
  work folder under `.work/`, packages the existing kernel output with the
  documented `pmbootstrap build --envkernel` path, builds the local recipes and
  calls `pmbootstrap install` / `export`. It never calls `flasher`, `adb`,
  `fastboot`, `--disk` or anything that writes the phone.
- Focused tests live under `tests/`:
  ```sh
  python3 -m unittest tests.test_sensors_registry \
      tests.test_prepare_sensor_bundle tests.test_package_inputs -v
  ```
  All 13 tests for this change pass as of 2026-11-09. A full
  `python3 -m unittest discover -s tests` additionally runs the separately
  maintained `test_prepare_temporary_dtb` suite; it is not part of this change.

## Kernel reuse instead of a second full compile

`scripts/rootfs_prepare.sh` reuses `build/kernel-nura-r25` when it is present:

1. It creates `.work/linux-v6.17.0-sm8150/.output` as a hardlink clone of the
   existing O= build output (no 4.4 GiB copy), verifies
   `include/config/kernel.release` is `6.17.0-sm8150` and that
   `.output/arch/arm64/boot/Image.gz` is byte-identical to
   `build/nura-kernel-package/boot/vmlinuz`.
2. It replaces the generated O= `Makefile` (which contains absolute host paths)
   with the relative stub `include ../Makefile`, exactly what pmbootstrap's
   `--envkernel` path overmounts inside the chroot.
3. It runs `pmbootstrap build --envkernel linux-postmarketos-qcom-sm8150`,
   which runs `abuild rootpkg` over the existing output. This produces a normal
   signed APK with pmbootstrap/abuild metadata; no APK is hand-assembled.
   Host validation already ran the same `make modules_install dtbs_install`
   from the relocated `.output` (828 `.ko` files, 346 DTBs, rc 0).

If the build output is absent, the script falls back to the canonical
`pmbootstrap build --force linux-postmarketos-qcom-sm8150` (recipe r25), which
compiles the pinned source. In both cases it reads the produced APK version,
refuses anything that is not `6.17.0*`, and pins it in the `install --add`
argument together with `postmarketos-mkinitfs-hook-netboot`. This is what
prevents the distro's `7.0.0-r2` kernel in the binary mirror from being selected
silently.

The envkernel APK gets pmbootstrap's normal `_pYYYYMMDDHHMMSS` version suffix
(pkgrel 0). The generated rootfs therefore has 6.17.0 modules that match the
6.17.0 kernel in the boot image. Do not drop the exact `--add` pin: the mirror
does have a 7.x kernel and the device dependency alone is unversioned.

## Netboot feasibility (resolved as far as source audit can)

The built-in hook `postmarketos-mkinitfs-hook-netboot` is compatible with the
Phosh baseline, despite the scary-looking dependency:

- The hook's `depends` is `devicepkg-utils postmarketos-mkinitfs !usb-moded`.
  `!usb-moded` conflicts with the old package named `usb-moded`, not with
  `postmarketos-usb-moded`. Phosh depends on `postmarketos-usb-moded`; apk will
  not install the obsolete `usb-moded` name, so there is no actual conflict.
- The gadget is managed by Alpine's `usb-signaller` (pulled by
  `postmarketos-usb-moded`), not by the legacy daemon. In upstream
  `usb-signaller` 0.4.3, `src/udc.rs` explicitly implements a read-only
  `Netboot` mode: it detects `/dev/nbd0` (created by the hook), classifies an
  active NCM gadget as `Netboot`, and refuses mode changes while netboot is
  active (`set_mode` returns early). The default `developer_mode` request is
  therefore rejected and the initramfs NCM gadget is not torn down. This was
  checked against the Alpine edge 0.4.3 source and package on 2026-11-09; a
  future usb-signaller release is a runtime re-check item.
- The Nura deviceinfo does not set `deviceinfo_create_initfs_extra`, so
  mkinitfs produces one merged initramfs that contains `/init_2nd.sh`; stage 1
  jumps straight to stage 2 before scanning subpartitions. The netboot hook
  runs before `mount_subpartitions`, connects the NBD device and then scans
  partitions. In the normal case the NBD image's `pmOS_root`/`pmOS_boot` labels
  are visible first; an existing pmOS install nested inside `userdata` is not
  visible until a loop device is made. There is still a small udev/label race
  with that internal install. Make it deterministic by adding
  `pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1` to the temporary boot image
  cmdline: the initramfs then uses the NBD subpartitions directly and never
  scans `userdata`. That cmdline belongs to `scripts/build_bootimg.sh` /
  `docs/TEMPORARY_BOOT.md`, which another worker owns; this task did not edit
  it. If `nbd-client` fails, the hook loops before the subpartition scan and
  does not fall back to the internal rootfs.
- The kernel has `CONFIG_BLK_DEV_NBD=m`; the hook's `netboot.modules` adds the
  module to the initramfs. `deviceinfo_rootfs_image_sector_size="4096"` is used
  for `nbd-client -b` and matches the image created by pmbootstrap. Configfs
  and NCM gadget support are built in (`CONFIG_USB_CONFIGFS=y`,
  `CONFIG_USB_CONFIGFS_NCM=y`, `CONFIG_USB_F_NCM=y`); the hook's
  USB network setup is available in the initramfs.

Residual runtime questions, not build blockers:

- NetworkManager in the booted rootfs can still try to manage `usb0` while the
  netboot NBD session is live. The gadget is preserved by usb-signaller, but
  the interface/DHCP behaviour has not been tested on this phone. If the NBD
  session drops after the rootfs starts, the boot freezes; retry with the
  netboot server already listening and the host USB interface dedicated to the
  phone, or use an external USB Ethernet path. Do not "fix" this by writing a
  device-specific NetworkManager policy without hardware evidence.
- The hook requires the host side to receive `172.16.42.2` from the initramfs
  `unudhcpd`; a conflicting host IP or a NetworkManager profile on the USB
  interface must be removed. The netboot server binds `172.16.42.2:9999`.
- Phosh, systemd, the kernel modules and the sensor DSP are still untested on
  this phone as a booted rootfs. Compilation and source checks are not hardware
  validation.

## User-run build entrypoint

The agent cannot run these: pmbootstrap invokes `sudo`, creates chroots, binds
mounts and uses loop devices. Run them yourself from the repo root.

### 1. Host prerequisites (Arch/CachyOS example)

```sh
sudo pacman -S --needed git openssl tar gzip util-linux procps-ng \
    e2fsprogs multipath-tools python sudo
```

`kpartx` is in `multipath-tools`; `losetup`/`chroot` are in `util-linux`;
`debugfs` is in `e2fsprogs`. If the commands are already present, skip this.

### 2. Read-only preflight

```sh
./scripts/rootfs_prepare.sh --check-only
```

This does not require sudo. It must end with `checks passed`.

### 3. Build the rootfs, initramfs and exported image

```sh
./scripts/rootfs_prepare.sh --build
```

What it does, in order:

1. Creates `.work/rootfs/pmbootstrap.cfg` (Phosh, systemd, project-local
   `.work/pmbootstrap` work dir, `build_pkgs_on_install = false`). No host
   config outside this project is touched.
2. Reuses `build/kernel-nura-r25` through `pmbootstrap build --envkernel`, or
   compiles the pinned 6.17.0 source if that output is missing.
3. Builds the local recipes with `-i` (ignore kernel dependency):
   `firmware-oneplus-guacamole`, `device-oneplus-guacamole`, `hexagonrpcd`,
   `iio-sensor-proxy`, and `postmarketos-mkinitfs-hook-netboot`.
4. Runs `pmbootstrap --boot-size 1024 --extra-space 4096 install --no-split --filesystem ext4 --add
   "postmarketos-mkinitfs-hook-netboot,linux-postmarketos-qcom-sm8150=<exact
   built version>"`. It will ask for the user password and for sudo. This
   creates the image under `.work/pmbootstrap`; it does not flash anything.
   The boot partition is 1024 MiB and the root filesystem gets 4096 MiB of
   additional free space. The original 2588 MiB root filesystem reached zero
   space available to non-root users during the greeter restart loop: 119 MiB
   free was below its 129 MiB ext4 reservation. Enlarging only `/boot` would
   not address that cache-write failure.
   It then reads back the installed rootfs's
   `/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release` and module
   directory and refuses to continue unless both are `6.17.0-sm8150`.
5. Exports symlinks to `.work/rootfs/export/`:
   `oneplus-guacamole.img`, `initramfs`, `boot.img`, `vmlinuz`.
6. Prints the exact netboot and boot-image commands.

The expected final artifacts are:

```
.work/rootfs/export/oneplus-guacamole.img   # partitioned ext4 image with /boot + /
.work/rootfs/export/initramfs               # same 6.17.0-sm8150 build + netboot hook
```

The script deliberately does not call `pmbootstrap flasher`, `adb`, `fastboot`,
`pmbootstrap install --disk`, or any partition write. If the install asks for
anything other than the user password, stop and inspect it.

### 4. Serve the rootfs over NBD (keep this terminal open)

When replacing an existing served image, first shut down Linux on the phone
and stop the old server with Ctrl+C. Do not replace or resize an image while
the phone is using it. Rebuild with the command above while the server is
stopped, then use `netboot serve --replace` to copy the new image into the
separate netboot directory. Without `--replace`, pmbootstrap reuses the old
small image. The replacement discards changes made only inside the previous
netboot copy, such as its saved Wi-Fi profile; it does not write Android storage.

```sh
python3 pmbootstrap/pmbootstrap.py \
    -c .work/rootfs/pmbootstrap.cfg \
    -p .work/pmaports \
    -w .work/pmbootstrap \
    netboot serve --replace
```

The server prints the NBD address and waits for the phone. It runs
`nbd-server` in the native chroot; the image is written only in `.work/`.

### 5. Build the temporary boot image with the preserved-DTBO wrapper

Use the other worker's wrapper; do not use `DTB_MODE=original` for a temporary
boot while the stock DTBO is still on the phone.

```sh
source scripts/envsetup.sh
KERNEL_DIR="$PWD/build/nura-kernel-package" \
INITRAMFS="$PWD/.work/rootfs/export/initramfs" \
DTB_MODE=abl-quarantine \
./scripts/build_bootimg.sh
```

In wrapper mode, `build_bootimg.sh` adds
`pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1` by default; these select the NBD
image explicitly instead of allowing an internal pmOS root to win a label race.
This produces `build/boot.img`. See
`docs/TEMPORARY_BOOT.md` for the read-only DTBO preflight and why the quarantine
wrapper is required. The initramfs used here is the freshly exported one, not
the stale `/tmp` default.

### 6. Boot temporarily

With the netboot server already running and the phone in fastboot:

```sh
fastboot boot build/boot.img
```

Use `scripts/fastboot_boot.sh build/boot.img` if the ABL stalls. **Never**
`fastboot flash boot`, `fastboot flash dtbo`, or install the image to
`userdata` for this first test.

## Android and data preservation limits

- Netboot only reads the host image through NBD. It does not write Android,
  `userdata`, `persist`, `dtbo`, or any UFS partition. The existing pmOS
  install (if present) is not selected while netboot succeeds; a failed NBD
  connection loops before the internal subpartition scan.
- The boot image is `fastboot boot`-ed, not flashed. The stock DTBO stays on
  the phone. The quarantine wrapper is what makes the temporary downloaded DTB
  safe while ABL applies that stock DTBO.
- The factory sensor calibration stays on `/persist` and is read with debugfs;
  it is not extracted, modified or shipped. No phone partition, RF or
  calibration read was performed by this work.
- The following remain unverified until the user boots it: actual panel/touch,
  audio with the newly packaged ADSP, Wi-Fi/BT/modem, charging, sensors, Phosh
  start, and the netboot NetworkManager interaction. A build is not hardware
  validation.

## Remaining work after the first netboot

1. Boot the image via netboot (with the `pmos.root=/dev/nbd0p2
   pmos.boot=/dev/nbd0p1` cmdline) and capture the initramfs/netboot console
   and `dmesg`. Confirm the NBD session survives the switch to the rootfs and
   SSH works at `user@172.16.42.1`.
2. Check the sensor registry service result:
   `systemctl status guacamole-sensors-registry hexagonrpcd-sdsp`.
   A missing `/persist` registry must leave the DSP off, not fall back.
3. Check the sensor bundle identity against the old private archive if it ever
   becomes available; it is currently unverified.
4. Re-check usb-signaller's Netboot mode on the actual Alpine edge version
   installed in the rootfs.
5. Only after a successful temporary boot should a non-netboot installation
   path (and any Android-preservation plan) be discussed. This document does
   not authorize installing to `userdata`.
