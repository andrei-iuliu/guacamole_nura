# rc4j working checkpoint — 2026-11-14

The user built and successfully RAM-booted rc4j Phosh with a local userdata
rootfs. Git base: `4968102`, with the existing local source changes; this is
not a claim that the runtime was built from a pristine Git checkout.

## Verified

- User: speakers, Bluetooth and Wi-Fi work; Phosh desktop is usable.
- SSH: system running, no failed system/current-user units; Phosh and Stevia
  active with zero current-session restarts.
- Root: ext4 read-write, 218.8 GiB total and 205.4 GiB available after automatic
  expansion. No new storage checksum/I/O/OCS errors in the sampled boot log.
- Kernel: `7.0.0-sm8150-guacamole-rc4`; UFS logs the HS-G3 cap from patch 0010.
- Packages: device `4-r20`, Phoc `0.58.0-r100`, libssc `0.4.4-r100`,
  iio-sensor-proxy `3.9-r100`, kernel APK `7.0.0_p20261114153303-r0`;
  the installed world pins match.
- Sensor proxy: active, zero restarts, about 4.56 seconds CPU over roughly
  14 minutes uptime; accelerometer detected. Rotation under load is not tested.
- Phosh power modes reach TuneD via tuned-ppd. Performance selected
  `throughput-performance` and changed all CPU clusters to `performance`;
  Balanced/Power Saver prefer `schedutil`. GPU devfreq remains separate.

## Successful transfer and recovery sequence

An earlier transfer used three large sparse chunks. Its first boot reported
ext4 checksum errors during expansion; repair left essential directories in
`lost+found`, so a clean fsck alone did not make that installation bootable.
The host image subsequently passed `e2fsck -fn` on a separate root-partition copy.

Reflashing the same image with **`fastboot -S 64M flash userdata <image>`**,
then RAM-booting the matching debug image, allowed a full read-only filesystem
check on the phone **before** any mount/resize. Host and phone both reported
33557 files and 601146/1716224 blocks with no errors. After detaching the
read-only diagnostic loop and running `pmos_continue_boot`, automatic expansion
and normal startup succeeded. Retain the 64 MiB transfer cap; the original
corruption's precise cause is still unproven. Sparse chunk counts are not wipe
passes. This procedure replaces userdata and requires backing up needed data.

The normal boot image omits `pmos.debug-shell`; the separate debug image adds
it and pauses before mounting/resizing. Both were host-verified. No boot,
DTBO or vbmeta partition was flashed in this session. The Linux boot slot was b.

## Remaining issues and limits

- A USB serial/getty teardown kernel Oops in `gs_close` occurred when leaving
  the debug shell, before mounting root. The kernel continued, taint value 128.
  Physical USB removal or gadget reconfiguration may trigger this path; the
  exact trigger is not proven. See the saved call trace.
- Early display/SMMU, SLIMbus QMI and audio warnings remain. No later GPU hang
  was observed in this short sweep; the GPU fix is not stress-validated.
- Charge control was configured for 40–100%, not 40–80%. The supported user-run
  command is `sudo guacamole-chgctrl limit 40 80`; no setting change was verified.
- Cameras, microphone recording, cellular calls/data, suspend/resume and long
  GPU workloads were not retested on this version. Earlier camera success is
  historical evidence for a different build.

## Artifact identity and retention

The prepared runtime stays in `runtime-workspace`. Retain rc4j plus rc4i kernel
builds, and the normal/debug rc4j boot images; older rc4 build archives are
removed. Older rc2/rc3 baseline packages still required by the build flow are
inputs, not expendable rc4 generation archives.

| Artifact | SHA-256 |
| --- | --- |
| Normal boot | `bcffa1ec24b1d46be03fc32d39b3cf4bf246712dc810cadc57a5bab8ed3c7bc1` |
| Debug boot | `a761fe1455d6d68398df231fe9f642e07dff47e44b87f5368d4f79298931d917` |
| Source rootfs | `bcc343d25aff482a6e92f6d928be07b80f131117229441e0d32156eb799ccb1d` |

Full paths, sizes and kernel/module identities: [artifact-identity.json](../flows/rc4/evidence-rc4j-20261114/artifact-identity.json).
SSH evidence: [runtime](../flows/rc4/evidence-rc4j-20261114/runtime.txt),
[USB Oops](../flows/rc4/evidence-rc4j-20261114/kernel-oops.txt),
[storage/GPU log filter](../flows/rc4/evidence-rc4j-20261114/storage-gpu-errors.txt).
