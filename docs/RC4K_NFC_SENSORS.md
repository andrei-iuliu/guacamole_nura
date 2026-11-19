# rc4k NFC and sensor candidate — 2026-11-15

**Status: rootfs and boot image built; final manifest HOST-VERIFIED.**
The user ran the privileged rootfs build. After restoring the pinned host tools
and reference images, the host-only boot packer completed and the manifest
checker passed. NFC hardware operation remains untested.

For future pulls, use [the environment template and local-input checklist](BUILD_ENVIRONMENT.md).

| Output | Location / SHA-256 |
| --- | --- |
| Boot image | `flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img` |
| Boot SHA-256 | `f8b9aa863e53261a6781a51a9b61985d6678664c6c98c324b123325c9fa78e64` |
| Rootfs | `flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img` |
| Rootfs SHA-256 | `a4db07fbb4b04b1beec9fd9135ff177e9f3c97cd7575dec4e10648a1d01335fa` |
| Final manifest | `flows/rc4/local-storage/build/local-userdata-phosh/DELIVERY.local.json` |

Rootfs packages include `neard-0.20-r0` (with `nfctool`),
`iio-sensor-proxy-3.9-r100` and `libssc-0.4.4-r100`. Local logs:
`.work/rc4k-build/bootimg.log` and `.work/rc4k-build/manifest-check.log`.

## First hardware boot: storage failure, diagnosis pending

The user reports dense red UFS messages, including `hibern8 enter failed`
with return -67 and a further -110 error. The exact first failure has not
been captured. USB networking enumerates but the phone does not answer ARP,
telnet or SSH at 172.16.42.1. These reports established neither NFC causation
nor filesystem corruption at the time; do not treat this candidate as
hardware-accepted.

2026-11-15 update: NFC causation is now explained. The UFS failure tracks the
NFC IRQ pull-up in patch 0074; see
[the NFC IRQ section](#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15).

A separate host-built debug image is ready at
`.work/rc4k-build/debug-boot/boot.img`, SHA-256
`c1d504b3590445865ebfd79a43ca28d9acc5e98dcb5ad2062d42f5b459584276`.
Payload checks pass and `pmos.debug-shell` is present. It uses the same
kernel/DTB/initramfs, pausing initramfs before normal userdata mounting and
resizing. Next: user-run temporary boot, collect `/pmOS_init.log` and `dmesg`
over debug telnet/serial, then decide whether a read-only filesystem check
is possible. Do not continue normal startup or run filesystem repair as part
of log collection.

The debug boot reached telnet successfully: HS-G3 cap is active, all UFS LUNs
enumerated, userdata is unmounted, and no Hibern8/timeout/I/O errors were
present in the captured debug-boot log. Pstore is empty. Logs are saved as
`.work/rc4k-build/debug-telnet-{initial,boot}.txt`.

The user explicitly authorized inspecting relevant fish history. Its latest
flash entry (2026-11-15 09:02 local time) uses `-S 64M flash userdata`, followed
by normal RAM boot. The successful 2026-11-14 retry also used `-S 64M` but
booted the debug image and checked the filesystem before proceeding. History
does not retain command output and cannot prove transfer integrity.

Live userdata is `/dev/sda19`. Its nested GPT root partition starts at sector
250112 with 4096-byte logical sectors (byte offset 1024458752) and now ends at
59463277 (length 242537127936 bytes): the first normal boot already expanded
the partition table. Filesystem integrity remains unchecked. The next action
is a user-run read-only loop mapping plus `e2fsck -fn`, with no repair or
normal-startup continuation until results are reviewed.

The user's read-only `e2fsck -fn` completed all five passes: 33568 files,
2405019 used blocks, 59213166 total 4096-byte blocks. The filesystem therefore
already fills the expanded root partition (242537127936 bytes, 225.88 GiB).
It skipped journal recovery and reported `orphan_present` with a clean orphan
file; repair was declined. No directory/inode/checksum error was reported.
This is not a fully recovered clean-filesystem result. After the check the
loop was detached, userdata remained unmounted, no UFS/I/O errors appeared,
and the UFS controller reported runtime-suspended successfully. Missing
expansion is ruled out; the first boot's UFS failure remained unexplained at
the time. 2026-11-15: it is now explained by the NFC IRQ pull-up (see the
[NFC IRQ section](#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15)).

After the user ran `pmos_continue_boot`, telnet stopped responding and the USB
network/serial interfaces disappeared. No SSH or fastboot endpoint had returned
at the subsequent host checks. The helper deliberately loops after signaling
continuation, so its lack of a returned prompt is not proof of a hang. The USB
handoff is an unresolved suspect: rc4j previously recorded a `gs_close` Oops
during debug-shell teardown. Current screen/log evidence is needed before
attributing this run to that issue or to UFS.

The user returned to fastboot (slot b). A diagnostic initramfs variant under
`.work/rc4k-build/keep-usb/` changes only `init_functions.sh`: the generated
`pmos_continue_boot` helper leaves telnet alive, and `debug_shell()` skips
ACM/mass-storage removal and USB UDC rebinding on return. This is a temporary
diagnostic change, not a shipped recipe/default. Archive entry-set comparison,
per-file content comparison (only that function file differs) and shell syntax
checks passed. The normal/debug rc4k artifacts are unchanged. Next boot should
pause normally; attach log capture before asking the user to continue.

The keep-USB capture confirms journal recovery, a clean filesystem, successful
root mount and `Switching root` without a UFS error. The logger's shell is
then killed by initramfs's explicit `pidof sh` cleanup; use `exec cat /dev/kmsg`
on the next capture to remove that shell wrapper.

The user's subsequent readable photo shows `ufshcd_change_power_mode` timeout
(-110), `ufshcd_scale_gear` failing from tx/rx G1 to G3, the error handler's
`link is broken`, Hibern8 enter failure -67 and devfreq `dvfs failed` -110.
These identify a failed UFS scaling transition, not its initiating cause.
The existing driver control is
`/sys/bus/platform/devices/1d84000.ufshc/clkscale_enable`. Writing 0 suspends
devfreq scaling and requests maximum clocks/gear; it can itself fail if the
link is already broken. The next discriminating test must start from a fresh
debug boot, set that control before rootfs startup, verify success and logs,
then continue. This is a temporary user-run diagnostic setting; no permanent
kernel/DT/package workaround has been applied.

## Successful startup with UFS scaling disabled

On the next fresh keep-USB debug boot, the user wrote 0 to `clkscale_enable`
and confirmed its readback. No UFS errors followed that write. After the user
continued boot, journal recovery and root mount succeeded, and SSH/Phosh came
up. SSH confirms `clkscale_enable=0`, a 218.7 GiB mounted ext4 root with about
205.3 GiB available, active sshd/sensor-proxy and no failed system units.
The current kernel journal contains no Hibern8, UFS failure, devfreq failure,
I/O or ext4 error matching the diagnostic filter. This is one successful
startup test, not proof of the underlying cause or long-term stability.

The override is temporary and must be applied again after reboot until a
reviewed persistent boot workaround is integrated. Do not treat the original
normal boot image as fixed. Captures:
`.work/rc4k-build/keep-usb/logs/scaling-test.log` and
`.work/rc4k-build/runtime-scaling-disabled.txt`. SSH uses a separate
`.work/rc4k-build/known_hosts` because the fresh rootfs generated a new host key;
the global known-hosts file was not changed.

NFC now registers `nfc0`; `nfctool --list` advertises Felica/MIFARE/Jewel/
ISO-DEP/NFC-DEP, Powered No, RF Mode None, no targets. The journal has one
`nxp-nci_i2c 4-0028: NFC: Read failed with error -121` entry. Reader/RF function
is unverified; do not count successful registration as a tag-read result.
The `-121` read failure is the symptom of the NFC IRQ storm described below.

### Cross-check of Omarchy Mobile and Nura storage notes

The public [Nura README](https://github.com/emilyastranova/guacamole_nura#bootloader-dtbo-overlay)
describes clearing the active DTBO to prevent vendor overlays corrupting the
mainline device tree. Its [storage notes](https://github.com/emilyastranova/guacamole_nura/blob/main/docs/PLAN.md#ufs-storage-rootfs--usb-gadget-networking)
separately document HS-G4 write corruption and kernel patch 0010's G3 cap.
That patch is already present, and its cap is confirmed in this boot's log.

Omarchy Mobile's historical [bring-up log](https://github.com/Cube-Oakley/omarchy-mobile/blob/main/devices/oneplus7pro/docs/bringup.md)
records upgrading very old boot firmware and bridging the mainline DTB to a
filtered stock DTBO. Our current boot instead uses the verified quarantine
wrapper to preserve stock DTBO while containing its overlays. The inspected
notes do not establish an additional UFS-firmware partition flash as the fix
for this incident. Do not apply another project's DTBO/bootloader flashing
procedure on the basis of the present G1-to-G3 failure.

Kernel recipe `flows/rc4/device/testing/linux-postmarketos-qcom-sm8150`,
`pkgrel=14`, adds patch 0074 for the NQ330 NFC controller on I2C9. The bus
runs at 100 kHz, IRQ47 is level-high, VEN is GPIO41 and DWL is GPIO48.
GPIO42 (secure element power) remains unclaimed. The first experiment uses
the controller's existing firmware; no firmware download is automatic. Its GPIO47 pull-up caused the UFS
scaling failure; rc4l changes it to pull-down (see the NFC IRQ section).

The rootfs package list now includes `neard` for both UIs, and the installed
rootfs checker requires it. Phone repository metadata confirms that its main
package supplies `nfctool`; no separate tools package is needed. Package
installation is verified; tag reading is still untested.

The front sensor remains absent and the pop-up motor disabled. The existing
sensor packages already stream accelerometer, gyro, magnetic data and compass.
See [live evidence](SENSOR_READINESS_20261114.md) and
[gyro investigation](GYROSCOPE_FEASIBILITY.md). No new sensor service is needed.

## NFC IRQ pull-up and the UFS scaling failure (2026-11-15)

**Status: cause identified and fixed in source as rc4l. The rc4l kernel and
boot images are built and host-verified (`.work/rc4l-build/README.md`);
rc4l is installed as a fresh userdata image and passed the scaling load test with both the debug and normal boot images (NFC IRQ count 0); see `.work/rc4l-build/README.md`.** The v4
result below used an rc4k DTB with one edit, not an rc4l build.

Mainline `nxp-nci` holds VEN (GPIO41) low from probe and requests the
level-high IRQ on GPIO47 at once. With the chip unpowered, patch 0074's
`bias-pull-up` on GPIO47 keeps that line asserted. The result was an interrupt
storm of about 226k IRQ/s on CPU0 (`msmgpio 47 Level nxp-nci_i2c`,
15,738,244 → 16,870,612 in 5 s). Every I2C read NACKed
(`NFC: Read failed with error -121`). CPU0 also services `ufshcd`, so UFS UIC
commands missed their 500 ms timeout during gear scaling, giving `-110`. The
vendor nq-nci driver enabled its IRQ only after powering the chip, so the same
pull-up was harmless on Android.

| Test | Change from unmodified rc4k | Clock scaling | Result |
| --- | --- | --- | --- |
| Unmodified rc4k, keep-USB | none (cmdline `module_blacklist=qcomtee`) | on | `-110` after switch_root; three failures in total |
| rc4k, NFC driver blocked (v3) | cmdline adds `nxp_nci_i2c,nxp_nci` to the blacklist | on | clean boot; three rounds of 2 GiB O_DIRECT write/read, 313–338 / 538–549 MB/s, G1↔G3 each round |
| rc4k DTB, GPIO47 `bias-pull-down` only (v4) | DTB edit only | on | NFC driver bound, GPIO47 IRQ count 0; clean boot; three rounds 318–336 / 539–561 MB/s, G1↔G3 each round |

The A/B pair uses the same kernel, initramfs and rootfs; only the cmdline or
the one DTB property differs. The full run log is `.work/nfc-bisect/README.md`.

Patch 0074 now uses `bias-pull-down` on GPIO47 in the rc4l source, with the
reason in its description. `check_nfc_dtb.py` requires that pull-down and
rejects the rc4k DTB. The kernel release string stays
`7.0.0-sm8150-guacamole-rc4`, so identify rc4l builds by manifest and hashes.
Once rc4l is installed, `clkscale_enable=0` should no longer be needed. This
is expected, not yet verified on an rc4l build.

The upstream candidate `6475cfb81fc4` was tested and rejected. It is not the
fix, and it caused IOMMU double-unmap warnings in `ufshcd_err_handler` on the
full-OS boot. Upstream reports also describe regressions on Qualcomm UFS, and
a revert has been proposed. See the
[UFS scaling outcome](../flows/rc4/topics/ufs-scaling/README.md).

Open items:

- Suspend/resume and battery behaviour with scaling enabled are untested.
- NFC RF and tag reading remain untested.
- The v1/v2 images (I2C9 disabled in the DTB) failed to boot before the display
  or USB came up. This is still unexplained. They are not used for the NFC
  question; v4 shows that a DTB edit with `prepare_temporary_dtb.py` does boot.
- On one v3 boot, `iio-sensor-proxy` exited "No sensors" twice while SLPI and
  `hexagonrpcd` were up. It worked on the next two boots.

## Build and checks

The rootless kernel build passed in a separate directory, preserving the rc4j
build outputs:

```sh
NURA_SOURCE_ROOT="$PWD" RC4_KERNEL_ROOT="$PWD/.work/rc4k-build" JOBS=16 \
  bash flows/rc4/scripts/rc4.sh kernel
python3 flows/rc4/scripts/check_nfc_dtb.py \
  .work/rc4k-build/kernel-output/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole*.dtb
python3 flows/rc4/scripts/dt_facts.py \
  .work/rc4k-build/kernel-output/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb \
  --require-rc4-gates
```

Results: Image, four DTBs and 839 staged modules; build identity/contract and
boot envelope passed. NFC checks passed on all four DTBs. Default DTB retains
three bindable rear-camera endpoints, 11 audio links and the disabled feature
gates. Logs and identity are under `.work/rc4k-build/`.

| Artifact | SHA-256 |
| --- | --- |
| Image | `c0a08bd5399433f2430ae14e570517d1291ddecc4660c3c5208bc6a26026012b` |
| Default DTB | `1bbd1c01d30df2366d4fefd98d68d4fe52a2f80d3b67bd18dfdd3199c07c7d5b` |

The sensor wiring suite passed 21 tests. UI checks passed 35 tests with one
skip. The integration suite passed 26/27; its existing rc3 byte-identity
assertion fails on previously added patch metadata. The source checker retains
six documented stale historical assertions (old patch count/order, pkgrel,
byte-identity and UI default); local source checksums pass. These failures
have not been waived or represented as passing.

## User-run rootfs step

The dry run accepted the new kernel. A subsequent `--check-only` preflight
also validated its staging/modules and the pinned baseline APK inputs.
The fork has no baseline artifacts of its own, so `NURA_BASELINE_ROOT` selects
the preserved runtime's read-only inputs while recipes remain in this fork.
Rootfs creation invokes privileged pmbootstrap operations, so the user must
run it under this workspace's system-command instructions:

```sh
NURA_SOURCE_ROOT="$PWD" \
NURA_BASELINE_ROOT=runtime-workspace \
NURA_KERNEL_BUILD="$PWD/.work/rc4k-build/kernel-output" \
NURA_KERNEL_SOURCE_STATE="$PWD/.work/rc4k-build/kernel-state" \
  bash flows/rc4/local-storage/scripts/local_storage_build.sh \
    --ui phosh --build --authorized-key ~/.ssh/id_ed25519.pub
```

The public-key file was found and validated with `ssh-keygen -lf`.
This is the existing local-userdata flow and
uses its existing Phosh state/output directories; preserve any earlier image
you need before rebuilding. The preflight validates the pinned inputs but
does not establish that all network packages can be fetched or built.
This command rebuilds both artifacts and verifies the final
`DELIVERY.local.json` manifest. The completed build is recorded above.
The kernel release remains `7.0.0-sm8150-guacamole-rc4`; distinguish candidates
by their manifest/hashes and retain the matching module tree with each Image.

The first rootfs attempt (2026-11-15 08:33 local time) built the kernel APK
and guarded hook, then stopped at the firmware package because its local
`firmware-oneplus-guacamole-24.0-20261011.tar.gz` was absent. That archive and
`guacamole-sensors-20261102.tar.gz` have since been copied from the preserved
runtime into their respective recipe directories, with both SHA-512 hashes
verified against the fork's APKBUILDs. They remain ignored local inputs.
Before retrying after this failure, clear its active chroots with:

```sh
bash flows/rc4/scripts/rc4.sh shutdown --ui phosh
```

Then rerun the complete rootfs/boot-image command above. Shutdown and retry
remain user-run privileged operations.

## Hardware validation still required

After building and booting the candidate with its matching modules, inspect
`/sys/class/nfc` and kernel probe logs. Use packaged `nfctool --help` to confirm
options, then a bounded `nfctool -d nfc0 -1 -p` reader trial with a known tag.
Controller discovery alone does not prove RF operation. If NCI responds but
tags remain invisible, investigate the reference-clock and board configuration
work in [the NFC proposal](NFC_FEASIBILITY.md) before downloading firmware.
Do not run a raw I2C bus scan against a bound NFC device.

On the current SSH-connected phone, ordinary-user tests can use:

```sh
timeout -k 2 20 ssccli --sensor=proximity --timeout=15
timeout -k 2 20 ssccli --sensor=compass --timeout=15
timeout -k 2 20 ssccli --sensor=gyroscope --timeout=15
timeout -k 2 20 ssccli --sensor=light --timeout=15
```

Run sequentially: cover/uncover for proximity; quarter-turns away from magnets
for compass; rotate around each axis for gyro; change illumination for light.
Confirm normal sensor disable at the end. Gyro output's `m/s` label is wrong;
the SSC datatype uses rad/s. Application tests still need heading delivery,
call-screen blanking and automatic brightness. No controlled physical tests,
call tests, simultaneous-client stress or suspend checks have been completed.
