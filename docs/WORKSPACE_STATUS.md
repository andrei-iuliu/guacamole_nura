# Authoritative workspace and runtime status

## Current: rc4t (rc4 + EXPERIMENTAL venus optional set) on `boot_b`, Plasma rootfs kept (2026-11-25)

`boot_b` holds the rc4t Plasma image (`--optional venus`, pmaports `82eac8fd`,
kernel package `7.0.0_p20261125122042-r0`, installed with `apk add` on the
existing Plasma `-nura` rootfs; userdata kept). Flashed directly (RAM boots
are unreliable on this ABL); copies of both boot images and both kernel APKs
are in `.work/rollback/`, and the rc4r APK is also on the phone in
`~/rollback/`. `/usr/share/kernel/postmarketos-qcom-sm8150/optional-sets`
reads `venus experimental 7a2aaf80…`.

Venus works: firmware `VIDEO.IR.1.2-00035` boots, `/dev/video8` decoder
(H.264, VP8, VP9, HEVC, MPEG-2 → NV12) and `/dev/video9` encoder (H.264, VP8,
HEVC). GStreamer `v4l2h264dec`/`v4l2h265dec` decoded 1080p test clips
bit-exactly (300/300 frames, sha256 equal to a software decode); 1080p H.264
real-time decode used ~31% of one core. `v4l2h264enc` encoded 720p and the
secure persist buffer was assigned to and reclaimed from VMID 11 (TrustZone
accepts it). Runtime PM returns to `suspended`. No SMMU faults for SIDs 0x23xx.
Health: no failed units, Wi-Fi up, KDE running, modem/ADSP/SLPI running.
Not yet tested: VP8/VP9 decode, long playback, suspend/resume with a
session, a player app. Alpine's ffmpeg has no V4L2 M2M (and on the phone
its CLI is 9.1 against 9.0.2 libs); use GStreamer. See
[the venus topic](../flows/rc4/topics/venus/README.md).

### Before that: rc4r on `boot_b`, kernel pkgrel 20 and device 4-r22 on the Plasma rootfs (2026-11-23)

`boot_b` holds the rc4r Plasma image (kernel build ID `c1f7fd5f…`, 0001-0079),
flashed after a RAM boot failed (RAM boots on this ABL often end black with no
USB, even for good images). The rootfs has
`linux-postmarketos-qcom-sm8150-7.0.0_p20261123095143-r0` (rc4r, pkgrel 20) and
`device-oneplus-guacamole-4-r22`. `Module.symvers` and BTF are unchanged since
rc4p. New since rc4q: patch 0079 (panel prefers 60 Hz; boot logs
`initializing panel at 60 Hz (control-display 0x20)`), the qcom_wdt watchdog
armed by systemd, and the reboot-hang workaround (no fbdev restore when KWin
exits, panel turned off before KWin stops). See
[the reboot hang topic](../flows/rc4/topics/reboot-hang/README.md). Also merged:
`wip/sensor-startup-claim` (iio-sensor-proxy 3.9-r101, already on the phone).
Copy of the flashed image: `.work/rollback/boot-rc4r-local-userdata-nura-20261123.img`
(the rc4p rollback image was removed in the 2026-11-23 cleanup).
Health check after boot: no failed units, Wi-Fi up, iio-sensor-proxy active.

GPU (checked 2026-11-25): KWin composites with OpenGL ES 3.2 on freedreno
(FD640, Mesa 26.3.1). Turnip (`mesa-vulkan-freedreno`) was added by hand with
`apk add` and works (`vulkaninfo`: Turnip Adreno (TM) 640, Vulkan 1.3); device
4-r23 depends on it, so new rootfs builds include it. No hardware video decode
yet: the 7.0 tree has no SM8150 Venus support (see the venus topic once it lands).

### Before that: rc4q kernel package on the rc4p image (2026-11-23)

The rc4q kernel package `linux-postmarketos-qcom-sm8150-7.0.0_p20261123081257-r0`
is installed with `apk add`, and the phone runs it. It adds kernel patch
[0078](../flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/0078-wifi-ath10k-offload-gtk-rekey-during-wow.patch),
which offloads the GTK rekey to the firmware during WoWLAN suspend, so the AP's
hourly group rekey no longer wakes the phone. `boot_b` was not reflashed: it
holds the rc4p Plasma image (the running kernel's build ID `813222fc…` is the
rc4p `vmlinux`), and `Module.symvers` and BTF match rc4p, so it stays. A full night
with Wi-Fi on is still to be checked. See
[the Wi-Fi WoWLAN topic](../flows/rc4/topics/wifi-wowlan/README.md).

## Previous: rc4o packages on the rc4l rootfs, rc4n image on `boot_b` (2026-11-19)

The rc4o kernel package (fixed 0044) and `device-oneplus-guacamole-4-r21`
are installed. The modem-rail vote is released at every boot, and manual
suspend works with RTC and Power-button wake; automatic suspend stays off.
See [the suspend results](../flows/rc4/topics/suspend-fixes/README.md). One
warm reboot hung before the rootfs (black screen); a cold boot recovered.

### Before that: rc4n on `boot_b`, rc4l rootfs

`boot_b` holds the rc4n local boot image (0075 autofocus, DT only), rebuilt
from this repository: DTB `6552338d…` and initramfs `7414497f…` identical to
the fork's rc4n image; the kernel has identical config options,
`Module.symvers` and BTF. The phone boots it unaided (SSH in about 23 s,
no failed units, NFC IRQ 0, UFS scaling on), and both LC898217XC
actuators bind. Focus itself is not yet verified. The rootfs on
`userdata` is the rc4l install from 2026-11-15 with its rc4l kernel
package; the module ABI is unchanged. The guarded netboot hook
(`postmarketos-mkinitfs-hook-netboot-4.2.1-r5`) is now installed, so the
phone's own `/boot/initramfs` is the local one (`7414497f…`). Kernel
upgrades keep userdata; see [BUILD_ENVIRONMENT.md](BUILD_ENVIRONMENT.md).

## Previous: rc4l installed on local userdata (2026-11-15)

The rc4k UFS clock-scaling failure (`-110`, broken link) is explained. Patch
0074 pulled the NFC IRQ line (GPIO47) up, which caused an IRQ storm on CPU0 that
starved UFS. The rc4l source changes the pull to pull-down. Its kernel and boot
images are built and host-verified (`.work/rc4l-build/README.md`); rc4l
is installed as a fresh userdata image and passed the scaling load test
with the debug and normal boot images. Suspend/battery with scaling on and
NFC RF remain untested. The upstream UFS
candidate `6475cfb81fc4` is rejected. See the
[NFC IRQ root cause](RC4K_NFC_SENSORS.md#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15),
the [UFS scaling outcome](../flows/rc4/topics/ufs-scaling/README.md) and the
[rc4 status](../flows/rc4/README.md).

## Earlier: rc4j on local userdata (2026-11-14)

The rc4j Phosh image is built and running on the phone, with speakers,
Bluetooth and Wi-Fi confirmed by the user. Rootfs expansion, Phosh/Stevia,
the sensor packages and exact world pins were verified over SSH. See
[the success record](RC4J_SUCCESS_20261114.md) for artifact identity,
`fastboot -S 64M` transfer evidence, and remaining issues. No boot partition
was flashed in this session; the matching boot image was RAM-booted.

`runtime-workspace` remains the prepared runtime/build workspace. The independent
local source fork is `guacamole_nura-workspace/fork`.
Build
artifacts and privileged chroots are not moved into the fork. Retention is the
rc4j and rc4i kernel generations plus the current normal/debug rc4j boot images;
historical archive paths below may have been pruned.

## Historical checkpoints

2026-11-12: rc3 sources are prepared in this checkout.  `nura-plasma-rc3`
rebuilds the kernel (`7.0.0-sm8150-rc3`) and boot chain in
`.work/rc3-kernel` / `build/rc3-kernel`, keeps the candidate state/output in
`.work/plasma-mobile` / `build/plasma-mobile` so rc2 is preserved as rollback,
and keeps `.work/rear-camera` / `build/rear-camera` read-only.  The single
user-run entrypoint is `bash scripts/rc3_build.sh build`; see
`docs/NURA_RC3.md`.  This is source-ready only: no rc3 kernel/APK/rootfs/boot
image was built and no hardware result exists.  The actual build, serve, boot,
modem/clock/haptics/camera checks and any rollback boot remain user-run.

2026-11-10: `runtime-workspace` is the consolidated source workspace.
It includes the latest tested-source delta from `nura-rear-cameras-7-0`,
reviewed delivery lifecycle fixes, checkpoint tooling and profiling tooling.
The user requested a local Git checkpoint of these consolidated fixes.
No push, worktree deletion, runtime migration or phone action is included.

The hardware-tested runtime artifacts, privileged chroots and original boot/serve
commands remain in `nura-rear-cameras-7-0`. Do not run packaging in this checkout
expecting those runtime paths to migrate automatically. Recreate/verify local
build inputs, pmbootstrap config, packaging receipts and DELIVERY.json before
using this checkout for another build. Do not copy privileged live chroots.
The original runtime checkout is retained as rollback evidence, not the source
location for new development. The frozen source checkpoint remains outside both.

User confirmed all three rear cameras and recording; recording is choppy.
No measured FPS or regression/suspend result was collected by this swarm.
The dual-boot feasibility report is an investigation, not deployment approval.
No partition write/resize/slot change is prepared or authorized.
The user has deferred local installation: continue fixing usability with netboot,
then revisit direct installation once the system is sufficiently usable.

Future entrypoints: camera_build.sh, camera_serve.sh, camera_boot.sh,
camera_verify.sh; see docs/DELIVERY_LIFECYCLE.md. The smaller rear_camera
launchers are retained as historical fallback. Never refresh a live rootfs.
Checkpoint: docs/WORKING_CAMERA_CHECKPOINT.md. Profiling:
docs/DIAGNOSTIC_PROTOCOL.md. All newly integrated tooling still needs an actual
end-to-end user-run test; source/mock tests alone do not establish hardware health.
