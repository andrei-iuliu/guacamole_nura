# Authoritative workspace and runtime status

## Current: rc4n on `boot_b`, rc4l rootfs (2026-11-19)

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
