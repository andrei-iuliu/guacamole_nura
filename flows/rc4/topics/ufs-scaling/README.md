# UFS scaling timeout candidate

The rc4k phone boots with `clkscale_enable=0`, but enabling scaling has produced
gear 1 → 3 power-mode timeouts (`-110`), followed by a broken link and Hibern8
entry failures (`-67`). Filesystem expansion completed; a read-only fsck found
no structural errors. Disabling scaling still permits runtime suspend/Hibern8.
The battery cost of that workaround has not been measured.

`upstream/6475cfb81fc4.patch` is the unmodified upstream fix
[Avoid IRQ thread wakeup during active UIC command](https://github.com/torvalds/linux/commit/6475cfb81fc4f6175b6d15d1c205a5168dc10b46).
It handles interrupts directly while a UFS control command is active, avoiding
thread scheduling delays within the command timeout. Our 7.0 tree has the
threaded interrupt implementation but lacks this fix. This is a candidate,
not a confirmed explanation of the phone's failure.

Scope: one interrupt routing condition and its comment; no gear, frequency,
regulator, runtime-PM, filesystem or device-tree changes. The declaration and
threaded IRQ registration still use the same handler. UIC dispatch sets
`active_uic_cmd` before issuing the command; completion clears it. The existing
direct interrupt path already invokes `ufshcd_sl_intr()`.

An isolated source/output copy lives in `.work/ufs-scaling/`; the rc4k source,
boot images and default APKBUILD are unchanged. The patch applies with zero
fuzz. Compile logs are `compile.log` (UFS object) and `build.log` (kernel/modules).

Host verification passed: kernel and 839 modules built; exported `Module.symvers`
is byte-identical to rc4k. The diagnostic initramfs has the same file set and
contents as the keep-USB archive except its four rebuilt modules. Release,
module payload, DT wrapper and boot-envelope checks passed. Artifact hashes
are recorded in `.work/ufs-scaling/candidate.json`.

The RAM-boot image is `.work/ufs-scaling/boot/boot.img`. With the phone in
fastboot, run from the checkout (also valid in fish):

```sh
./platform-tools/fastboot boot .work/ufs-scaling/boot/boot.img
```

Leave the debug shell up with scaling enabled for inspection. Do not continue
to the rootfs until the diagnostic results have been reviewed; its installed
module package has not been replaced with this candidate's rebuilt package.

## First phone result — 2026-11-15

Diagnostic boot retained `clkscale_enable=1`. Four direct, read-only 256 MiB
reads from `/dev/sda19` to `/dev/null` succeeded at 505.9–514.9 MiB/s as reported
by BusyBox (1 GiB total). Devfreq transitions increased from 2 to 10: five in
each direction between 37.5 and 300 MHz including initial boot transitions.
Afterward, gear was HS_GEAR1, runtime state suspended and link HIBERN8; scaling
remained enabled. No UFS timeout, Hibern8 failure or I/O error appeared.
Raw output: `.work/ufs-scaling/read-test.log`.

The baseline and candidate kernel `.BTF` sections are byte-identical
(`24ddb4eb98b83b402e6c19aafd1804fc94a15c0cf76eed3cc939dba21492cf9c`),
as are exported symbols in `Module.symvers`. Together with the unchanged
configuration, release and type layouts, these checks support using the
installed rc4k modules for the next diagnostic full-OS boot. That next step
requires the user to run `pmos_continue_boot`; keep scaling enabled.

This demonstrates successful read-driven transitions in the initramfs, not
yet a stable full-OS fix. Write workloads, longer use, suspend/resume and
battery measurements remain untested. Default recipe remains unchanged.

Hardware validation must start from a diagnostic RAM boot, retaining access to
the known working scaling-disabled boot. Leave scaling enabled in that test;
capture kernel logs and devfreq transitions before continuing to normal boot.
If UFS errors recur, stop the test. Only promote this patch into the package
recipe after repeated transitions and ordinary filesystem use succeed. Power
and suspend/resume still need separate measurements. Do not reflash userdata
to test this kernel change.

## Outcome — 2026-11-15

The candidate `6475cfb81fc4` is rejected and must not be promoted.

- It did not identify the cause. The full-OS boot with this candidate
  produced IOMMU double-unmap warnings in `ufshcd_err_handler`
  (`io-pgtable-arm.c:639`, `dma-iommu.c`).
- Public upstream reports describe regressions on Qualcomm UFS (Samsung Galaxy
  Book2 sdm850 `1d84000.ufshc`, Qualcomm RB5). A revert has been proposed:
  [lkml 2603.2/04115](https://lkml.iu.edu/hypermail/linux/kernel/2603.2/04115.html)
  and [lkml 2603.2/06754](https://lkml.iu.edu/2603.2/06754.html).

The real cause was an NFC IRQ storm, not the UFS code path. Patch 0074 pulled
the NFC IRQ line (GPIO47) up. With the chip unpowered, that line stayed asserted
and flooded CPU0, which also services `ufshcd`, so UIC commands missed their
timeout during gear scaling. The fix is `bias-pull-down` on GPIO47 in rc4l
(rc4l kernel and boot images built and host-verified; the debug image passed the scaling load test on hardware).

Evidence and details:

- `.work/nfc-bisect/README.md` (A/B runs and IRQ counts)
- [RC4K NFC and sensor candidate](../../../../docs/RC4K_NFC_SENSORS.md#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15)

Scaling, suspend/resume and battery with the rc4l fix remain to be measured.

