# Reviewed7.0 reliability batch (2026-11-10)

Based on working rear-camera checkpoint8a2c557. Local integration includes cold/invalid-gauge charging policy, SMB5 fault/current readback, SLIMbus startup ordering, FastRPC freezer wait, touchscreen resume, DSI PHY clock lifetime and DSI command/frame serialization. Kernel patches0032-0037 are registered in recipe order; kernel package revision3, release remains7.0.0-sm8150-rear1. Package revision/hash distinguishes this build; never combine the new kernel with old rootfs modules.

Systemd MPSS startup is packaged but remains opt-in. SDSP libraries are installed under /usr/share/qcom/dsp/sdsp, with the former path as a compatibility symlink. This does not prove cellular or the sensor watchdog fixed.

Clean rootless build of Image.gz, modules and all four guacamole DTBs passed, including modules_install and boot-envelope validation. Image SHA256:ceb3a2603e0f6c702ea55d629f937048a17c116d96d149ae9d46a7072c635e92. DTB placement margin:5,275,648bytes. Hardware validation and privileged rootfs packaging have not been performed.

Post-build host suite:385 tests run,21 skipped, PASS. Display patch tests pass on both the unpatched backup source and freshly patched source. Fixture identity checks use actual module content rather than one historical kernel build hash.

The canonical workspace's previous source/output/stage/Image are saved in .work/rear-camera/before-fix-batch-20261110. Working runtime sibling and baseline remain untouched. The existing boot.img is an old candidate; matching rootfs/initramfs/boot image must be packaged before launch. The copied partial pmbootstrap-work is not a valid packaging workspace and must be preserved/replaced with a fresh initialized work directory by the user; never bypass its missing-version guard.

Limits: charging cold threshold is a conservative software choice; stopping the daemon retains inherited input-resume behavior. SLIMbus IRQ auto-enable and failed-child-probe callback ownership need broader upstream lifecycle work. Display serialization assumes one command-mode publisher and sends after a50ms timeout. Test ordinary charging, audio enumeration, touch/display resume, brightness during redraw and rear-camera recording before declaring the candidate usable. Video choppiness, power-button UX, sensor startup watchdog and suspend residency are not resolved by this batch.

Detailed reports, worker receipts, logs and artifact identities are in bringup-notes/nura-fix-batch-20261110.
