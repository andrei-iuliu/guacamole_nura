# RC4N Camera Autofocus

## Status

The rc4n full kernel/modules build is complete (839 modules). The previously prepared rc4n `boot_b` image, the earlier DT-only test image, and the saved phone-current rollback snapshot are **UNSUITABLE FOR LOCAL BOOT** and must not be flashed or booted. All three inherit an unconditional `hooks/netboot.sh` from the reused `/boot/boot.img` snapshot (`ca456378...`), which causes the phone to hang at `Waiting for netboot` instead of starting the local system.

A phone user flashed the rc4n image and observed that hang. Userdata was not modified. A corrected rc4n local image is now built at `.work/rc4n-build/boot-local/boot.img` using the canonical `flows/rc4/local-storage/scripts/build_local_bootimg.sh NETBOOT=0`; it is host-verified and **READY FOR USER-RUN FLASH**. The known hardware-tested recovery image is the rc4l local boot image described below. Autofocus hardware remains **UNTESTED** until the user boots the corrected image, and the coordinator has not flashed or rebooted any device.

**Update 2026-11-19:** the corrected image was rebuilt from the published
repository and is now in `boot_b`. Its DTB (`6552338d…`) and initramfs
(`7414497f…`) match the image above byte for byte; the kernel was rebuilt
with Clang 23.1.5 and has identical config options, `Module.symvers` and
BTF. It was RAM-booted first, then flashed; the phone boots it unaided and
both actuators bind. Focus behaviour (the hardware check below) is still
open. The rootfs now has the guarded netboot hook, so images generated on
the phone no longer wait for netboot.

## What rc4n changes

- Adds `0075`: main IMX586 LC898217XC lens at 72 and tele S5K3M5 lens at 74.
- Enables and links autofocus for both cameras; max/default is 400.
- Package release is `pkgrel16`. The kernel `uname` remains `7.0.0-sm8150-guacamole-rc4`.
- Ultra-wide camera and supplies are unchanged.
- Hardware focus range is still unvalidated.
- Optional configfs work and separate libcamera/omacam quality or crash work are out of scope.

## Current phone state and evidence

The phone `/boot/boot.img` snapshot that was reused for these images was regenerated on 2026-11-16 with SHA-256:

```text
ca456378ff4f405616944f1ff65f90691e1d1357347bb6a4988b01519707ffdb
```

It was copied to `.work/camera-af-rc/phone-current.boot.img` and unpacked in `.work/camera-af-rc/phone-current-unpacked/`.

- The compressed kernel SHA-256 is `c5d2bf98947f433699d423979f28d1bde4db8cd80477d39d1f82d72a8603cbeb`, matching `.work/rc4l-build/kernel-output/kernel-package/boot/vmlinuz`.
- The DTB SHA-256 is `bec708f5501c8689405eec2f09ecca1dfb52e09b320ab98c61349007f319e3e9`, exactly matching a recompilation of the preserved rc4l source.

These hashes remain valid payload evidence, but the snapshot is **UNSUITABLE FOR LOCAL BOOT** and for local-boot rollback. It contains the unconditional `hooks/netboot.sh` that causes the `Waiting for netboot` hang. Do not flash it.

Before the rc4n flash, the running slot was `b`, confirmed over SSH. The rc4n image was flashed to `boot_b`; userdata was not modified.

`.work/camera-af-rc/phone-current.boot.img` is a saved `/boot/boot.img` file snapshot of the current boot image. A raw backup of the boot partition itself was not taken.

The original byte-preservation validation confirmed that the kernel, ramdisk, boot arguments, and root UUIDs were carried over from this snapshot. It did not inspect or exercise the inherited initramfs boot hooks, so it missed `hooks/netboot.sh`. Preserving bytes therefore preserved the netboot behavior; it did not establish local-boot suitability.

Do not flash userdata or rootfs.

## DTB validation

- The default and all three isolated camera DTBs compile.
- Default existing RC4 gates pass.
- NFC check passes for all four DTBs.
- The wide-only DTB is byte-identical to rc4l.
- The autofocus checker passes the new full DTB and the unpacked test-image DTB, and rejects the baseline missing lens-focus.

Exact host validation command:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 flows/rc4/scripts/check_camera_af_dtb.py .work/camera-af-rc/reviewer-dt/sm8150-oneplus-guacamole.dtb .work/camera-af-rc/boot-af-source-unpacked/dtb
```

Integration tests: 26/27 pass. The sole failure, `test_rc3_patch_bytes_retained`, is a pre-existing `0001-enable-ufs.patch` metadata mismatch: the current `0001` patch equals HEAD and differs from rc3.

## Full build evidence

The full kernel/modules build completed with 839 modules.

- `.config` and `Module.symvers` are byte-identical to rc4l.
- `vmlinux.BTF` SHA-256 `24ddb4eb98b83b402e6c19aafd1804fc94a15c0cf76eed3cc939dba21492cf9c` matches both rc4l and the live `/sys/kernel/btf/vmlinux`.
- The installed `lc898` module matches rc4l.
- The source change is DT-only, and the existing module ABI is retained. Compressed module hashes differ after the rebuild, so the modules are not claimed byte-identical.

## Invalidated rc4n boot images (DO NOT FLASH)

These artifacts are retained only as invalid evidence. All three are **UNSUITABLE FOR LOCAL BOOT** because each inherits the unconditional `hooks/netboot.sh` from the reused `ca456378...` `/boot/boot.img` snapshot.

| Image | SHA-256 | Status |
| --- | --- | --- |
| `.work/rc4n-build/boot/boot.img` | `69df4214d92f24ffe64f93f20c77216c0a0cfc2e852e50dd17241cc538a746b0` | Unsuitable for local boot; do not flash |
| `.work/camera-af-rc/phone-current.boot.img` | `ca456378ff4f405616944f1ff65f90691e1d1357347bb6a4988b01519707ffdb` | Unsuitable for local-boot rollback; do not flash |
| `.work/camera-af-rc/boot-af-source.img` | `da4b776678f500b53246cf5890cc460348bc529bd17f28303dd9e58b3b7a10f0` | Unsuitable for local boot; do not flash or boot |

The rc4n image (previously `69df4214...`) contains the new rc4n compressed kernel and the validated autofocus DTB wrapper. Its ramdisk was byte-identical to the phone's then-current ramdisk, which is exactly why the netboot hook was carried over. The old review record `.work/rc4n-build/boot/review.json` did not catch this behavior and is not a bootability endorsement.

The DT-only image (previously `da4b7766...`) also inherits the netboot hook. Its DTB load-address change from `0x01f00000` to `0x03000000`, ABL wrapper, and preserved kernel/ramdisk remain payload evidence only; the image is not a fallback and must not be booted with `fastboot boot`.

### Why the original validation missed the netboot hook

The original validation was byte-preservation-focused. It verified the kernel, DTB, ramdisk, boot arguments, and root UUIDs against the reused snapshot, but it did not inspect or execute the ramdisk's `hooks/netboot.sh` and did not perform a local boot. Byte identity preserved the netboot behavior along with the payload. This is why the image passed the earlier checks yet the phone hung at `Waiting for netboot`.

## Known hardware-tested local boot image

`flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img`

```text
SHA-256: 2a5fb7246eff4c161387bc64e32e04a8c3a8b09802d03e91ac40899ff13f49a8
```

The rc4l source README records a hardware boot on 2026-11-15. This is the known local-boot recovery image.

## Recovery (USER-RUN ONLY)

After the rc4n flash, the phone hung at `Waiting for netboot`. Manually return it to the bootloader, confirm the target slot is `b`, and run these commands from the repository root one line at a time:

```bash
./platform-tools/fastboot flash boot_b flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img
./platform-tools/fastboot reboot
```

This flashes only `boot_b` and does not write userdata or rootfs.

## Corrected rc4n local image (READY FOR USER-RUN FLASH)

`.work/rc4n-build/boot-local/boot.img`

```text
SHA-256: 7b312a07d90da940c48edfebb2304b3ebf16ec3075a7b10dde6c7c43dcd2131c
Size: 57450496 bytes
```

Built with `NETBOOT=0` using the canonical `flows/rc4/local-storage/scripts/build_local_bootimg.sh`. It contains the new rc4n **uncompressed** `Image`, the guarded local-storage initramfs from the hardware-tested rc4l boot file, and the validated autofocus DTB wrapper.

Host validation passes: the canonical `local_storage_tool.initramfs_report`, `check_boot_image`, and `kernel_config_report`, plus the AF/NFC checks on the unpacked final DTB. Review record: `.work/rc4n-build/boot-local/review.json`.

The local hook exits before network setup unless `pmos.root=/dev/nbd` is requested; the final cmdline has no `pmos.root` or `pmos.boot`. The AVB footer is verified, and the image fits `boot_b` (96 MiB).

User-run persistent flash, from the repository root one line at a time, after manually entering the bootloader and confirming the current slot is `b`:

```bash
./platform-tools/fastboot flash boot_b .work/rc4n-build/boot-local/boot.img
./platform-tools/fastboot reboot
```

This writes only `boot_b`; it does not write userdata or rootfs. The three images above remain invalid, and the known rc4l recovery command above remains the documented recovery.

## Build rc4n from the repository root

These commands were used for the completed full build. Run them in Bash from the repository root.

```bash
source ./build-env.local.sh
mkdir -p .work/rc4n-build/kernel-state
cp --reflink=auto .work/rc4k-build/kernel-state/linux-v7.0.0-sm8150.tar.gz .work/rc4n-build/kernel-state/
RC4_KERNEL_ROOT="$PWD/.work/rc4n-build" JOBS=16 bash flows/rc4/scripts/rc4.sh kernel
```

Do not reuse previous `.work` build directories.

This command builds the kernel and package tree only. It does not itself build `boot.img` and does not flash anything.

The unchanged uname release does not prove ABI compatibility. For this build, `.config` and `Module.symvers` match rc4l, the `vmlinux.BTF` hash matches the live kernel, and the installed `lc898` module matches rc4l. The invalidated rc4n boot image retained the phone's then-current initramfs, which included the unconditional netboot hook. The corrected local image uses the known local initramfs instead.

## Expected outputs

Under `.work/rc4n-build/kernel-output/`:

- `Image`
- `kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole*.dtb`
- `kernel-package/usr/lib/modules/7.0.0-sm8150-guacamole-rc4/`
- `kernel-inputs.json`

## Hardware final-review check

After the corrected rc4n local image is booted, use the full libcamera camera IDs from the handoff:

- Main: `/base/soc@0/cci@ac4a000/i2c-bus@1/camera@1a`
- Tele: `/base/soc@0/cci@ac4a000/i2c-bus@0/camera@10`

Verify `focus_absolute` from 0 to 400, sharpness, infinity/macro focus behavior, and 1x/3x stills. Focus is not yet validated; do not claim autofocus works until this hardware check passes.

## Operational boundaries

- The user runs all administration and device operations, including recovery.
- The coordinator has not flashed or rebooted any device.
- The rc4n image, the DT-only image, and the saved phone-current snapshot are invalid local-boot artifacts; do not flash or boot any of them.
- Stale instructions to flash the old rc4n image or roll back to `phone-current.boot.img` are withdrawn.
- The only documented recovery flash is the user-run `boot_b` flash of the known hardware-tested rc4l image above.
- The corrected rc4n local image at `.work/rc4n-build/boot-local/boot.img` is built and host-verified with SHA-256 `7b312a07d90da940c48edfebb2304b3ebf16ec3075a7b10dde6c7c43dcd2131c`; it is ready for the documented user-run `boot_b` flash.
- Do not flash userdata or rootfs.
- Autofocus hardware remains **UNTESTED** until the user boots the corrected image.
- Use the commands documented here; do not invent repacking scripts or extra flags.
- The full kernel/modules build is complete (839 modules). The documented kernel build command does not itself build or flash `boot.img`.
