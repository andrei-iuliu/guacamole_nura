# rc4 topic: hardware video decode (Venus) on the OnePlus 7 Pro

**Status (2026-11-25): rc4t, EXPERIMENTAL optional set, compile tested, not
run on the phone.** It is not part of the default build: enable it with
`./nura kernel --optional venus` (or `NURA_KERNEL_OPTIONAL=venus` in
`build-env.local.sh`). The set lives in `flows/rc4/optional/venus`
(49 patches, `0001`-`0049`, plus the firmware). Other developers can leave it
out, or use a different Venus implementation, without touching the default
recipe. Next step: the RAM boot test below.

## What the hardware is

The SM8150's video block is an **IRIS1** (downstream "VPU 5"), the generation
between the AR50 (sdm845) and the IRIS2 (sm8250). It runs **HFI 4.x**
firmware. This phone's firmware is `VIDEO.IR.1.2-00035`. One video core sits
on MVS0 and the CVP on MVS1; there are three GDSCs (mvsc/venus, mvs0/vcodec0,
mvs1/cvp) plus the MMCX rail, seven clocks, four resets, and SMMU stream
`0x2300` (non-secure) with secure banks `0x2301`/`0x2303`/`0x2304`.

Mainline 7.0 had none of it: no platform data in the venus driver, no node in
`sm8150.dtsi`, no binding. The `iris` driver doesn't cover the SM8150 either,
and neither the postmarketOS SM8150 tree (up to its 7.4-rc1 branch of
2026-11-12) nor linux-firmware has anything for it.

## Where the patches come from

`0001`-`0048` are the SM8150 Venus series from the AOSPA raphael tree
(Xiaomi Redmi K20 Pro, the same SoC): branch `venus` of
<https://github.com/Aospa-raphael-unofficial/linux>, mostly by snowf14k3,
September 2026, on 7.2. The branch's last work is encoder P010 input, so its
decoder had been working there. The patches are taken in order with their
authors and messages; each carries `Nura-Origin` with its source commit.
Left out: the commits that aren't Venus work (raphael audio, GPIO, DSI,
board enablement, and an IPA fix).

They apply to our 7.0 tree unchanged except `0012` (the SM8150 resources),
where 7.0's match table has no `CONFIG_VIDEO_QCOM_IRIS` block.

What the series does, in short:

* **Boot and power:** preserve the IRIS1 interrupt mask, initialize the DSP
  queue fallback, restore the CPU clock configuration on enable, vote an OPP
  before boot, skip the AR50 AXI halt, and keep the venus, vcodec0 and CVP
  domains powered in **software** mode for the whole firmware cycle with
  firmware low-power mode off. A session-init timeout prints bounded state.
* **Resources, binding and DT:** `qcom,sm8150-venus`, the `sm8150.dtsi` node
  (disabled by default) with an OPP table of 200/240/338/365/444/533 MHz on
  min_svs/low_svs/svs/svs_l1/nom/turbo, the `venus_iova` and secure
  non-pixel IOVA regions, and the DMA range covering them.
* **Decoder:** the IRIS1 work route and the legacy decoder buffer and
  sequence contracts.
* **Encoder:** buffer requirements, capture sizes, the CBR contract, VUI,
  VP8 low latency, P010 input, and persist memory from the secure
  CP_NON_PIXEL context. `0030` adds an opt-in arm-smmu hook that shares page
  tables with that VMID; only a node with `qcom,secure-vmid` uses it, and
  the encoder fails closed without it.

`0049` is ours: it enables the node on guacamole with
`firmware-name = "qcom/sm8150/oneplus7/venus.mdt"`. `venus_mem` stays where
`sm8150-oneplus-common.dtsi` puts it, `0x97200000` (stock Android has it at
`0x96e00000`; our modem region is larger).

An earlier draft of a 0080-0084 series (derived from sm7150 notes) was dropped. It
handed the domains to hardware control, returned `-EAGAIN` from suspend, and
set the 444/533 MHz OPPs one corner too low. It's kept on the local branch
`venus-decode-v1`.

## Firmware

There is no SM8150 Venus firmware in linux-firmware, and the LineageOS vendor
partition and the pinned M1DNYT3 firmware repo don't have it either. The
board's copy is in the NON-HLOS `modem` partition, `::/image`, as the split
set the other remoteprocs use:

| File | Bytes | ELF segment |
| --- | ---: | --- |
| `venus.mdt` | 7020 | headers + hash |
| `venus.b00` | 212 | ELF header copy |
| `venus.b01` | 6808 | hash segment (pa `0x0fa00000`) |
| `venus.b02` | 1056676 | code, pa `0x0f500000` |
| `venus.b03` | 40516 | data, memsz `0x3f2000` |
| `venus.b04` | 32 | pa `0x0f9ff000` |

The load segments are relocatable and span `0x4ff020` bytes, which fits the
5 MiB `venus_mem` region (`0x500000`) with `0xfe0` to spare.

The pinned bundle `guacamole-venus.tar.gz` (sha512 in
`flows/rc4/optional/venus/SHA512SUMS`; gitignored, it lives next to it) was
packed on 2026-11-25 from this phone's `modem_b`; the files are
dated 2023-03-13. To pack it again from the phone (read-only mount):

```sh
ssh -t user@172.16.42.1 'sudo sh -c "mkdir -p /tmp/nonhlos && mount -o ro /dev/disk/by-partlabel/modem_b /tmp/nonhlos && mkdir -p /home/user/venus-fw && cp /tmp/nonhlos/image/venus.* /home/user/venus-fw/ && chown -R user: /home/user/venus-fw; umount /tmp/nonhlos"'
mkdir -p local/firmware/venus && scp 'user@172.16.42.1:venus-fw/venus.*' local/firmware/venus/
scripts/prepare_venus_bundle.py          # --source local/firmware/venus by default
```

The script checks that every segment's `venus.bNN` has the right size and
that the load span fits `venus_mem` (with the kernel's rule: every load
segment but the hash one counts), and writes a reproducible tarball into
`flows/rc4/optional/venus/`. `./nura firmware` also packs one from the OTA's
modem image and installs it there only if it matches the pin; a mismatch or
a missing set there is only a note, because the pin is the
phone's own copy, which is what TrustZone has to accept. Whether a LineageOS
OTA carries the same build is unchecked.

With the set enabled, the kernel builder stages the files and the kernel APK
installs them as `/usr/lib/firmware/qcom/sm8150/oneplus7/venus.mdt` +
`venus.b00`-`b04`; the firmware package (1-r7) is unchanged. The venus
driver loads it with `qcom_mdt_load`, which picks up the `.bNN` files itself.

## Risks

* **SMMU faults.** A wrong stream ID or mask faults in `arm-smmu`
  ("Unhandled context fault") on the first firmware access. The SIDs match
  the downstream node and the raphael port.
* **Memory.** `venus_mem` sits between the 150 MiB modem region and the next
  carve-out with no gap. The firmware fits; a different blob that didn't would
  be refused by the bundle script and by `qcom_mdt_load`.
* **Power domains.** A hang while powering MVS0/MVS1 or the MMCX vote would
  stop the boot or the first decode. The raphael port keeps the domains in
  software mode for exactly this reason.
* **Built-in code.** `0030` changes the built-in arm-smmu driver, so this
  needs a new boot image, not just a module. It only acts on nodes with
  `qcom,secure-vmid`.
* **Secure encoder.** The encoder needs TrustZone to accept
  `qcom_scm_assign_mem()` to CP_NON_PIXEL. If OnePlus's TZ refuses, the
  encoder fails closed; decode doesn't depend on it.

Never unbind/rebind the block or reboot from a wedged state to "retry"; use a
fresh boot, as with Wi-Fi.

## What it would give, and what's known

Checked on 2026-11-25 (phone probes, a web search and a review of the
imported series; notes in the gitignored `notes/HANDOVER_2026-11-25_gpu-venus.md`):

* Nothing SM8150/IRIS1 is upstream or queued through 7.4-rc1. The raphael
  branch is the only IRIS1 port proven on hardware: H.264, HEVC 8/10-bit and
  VP8 encode + decode, VP9 decode, at 1080p/720p/480p. Alternatives:
  CFM880/nabu-iris (Xiaomi Pad 5, decode only, iris-based) and the enser.io
  Galaxy Tab S6 bring-up (core boots, no frames yet).
* This phone is SoC revision 2.2 (V2 silicon, which the encoder's secure
  context model assumes). Whether OnePlus's TrustZone accepts the
  CP_NON_PIXEL assignment is only known after a boot; the encoder fails
  closed if not, decode doesn't depend on it.
* Userspace: mpv (`--hwdec=v4l2m2m-copy`), ffmpeg (`*_v4l2m2m`) and
  GStreamer's `v4l2` plugin (stateful; not `v4l2codecs`) use it directly.
  Firefox, Chromium and Qt use VA-API, so they need a VA-API shim such as
  <https://github.com/snowf14k3/venus-vaapi-driver> (proven on raphael).
* The live DT keeps `venus_mem` free at 0x97200000 and no device uses SIDs
  0x23xx. Reserved-memory node names on this board are 0x400000 off their
  `reg`; trust `reg`.

## Phone test plan

RAM boot first, never `fastboot flash boot_b` until the block has been seen
working (see CLAUDE.md, "Phone writes"):

1. Make sure `flows/rc4/optional/venus/guacamole-venus.tar.gz` is there
   (see "Firmware"), then `./nura kernel --optional venus` and `./nura build`.
   The first build with the set extracts a fresh source tree (the builder
   asks you to remove the old one). On the installed phone, `apk add` the new
   kernel APK, `sync`, then boot the matching image.
   `cat /usr/share/kernel/postmarketos-qcom-sm8150/optional-sets` should
   list `venus experimental …`.
2. `scripts/fastboot_boot.sh <boot.img>`: RAM boot. If it hangs before
   userspace, power off and boot `boot_b` again.
3. Probe and firmware:

   ```sh
   ssh user@172.16.42.1 'dmesg | grep -i -e venus -e "video-codec" -e arm-smmu'
   ```

   Expect the firmware version (`VIDEO.IR.1.2-00035`) and no SMMU fault.
   `failed to reset venus core`, a session-init timeout, `-110`, or an SMMU
   fault mean stop and report.
4. Device nodes and formats:

   ```sh
   ssh user@172.16.42.1 'ls -l /dev/video*; v4l2-ctl --list-devices; \
       v4l2-ctl -d /dev/videoN --list-formats-out'
   ```

   The decoder node is `video-decoder`; expect `H264`, `HEVC`, `VP8`/`VP9`.
5. Decode:

   ```sh
   gst-launch-1.0 -v filesrc location=clip.h264 ! h264parse ! v4l2h264dec ! \
       videoconvert ! fakesink silent=false
   ffmpeg -c:v h264_v4l2m2m -i clip.mp4 -f null -
   ```

   Then check the pixels, not just the exit code: decode the same clip in
   software and compare with the `psnr` filter.
6. Power: `cat /sys/bus/platform/devices/aa00000.video-codec/power/runtime_status`
   should go back to `suspended` after the decoder closes; then
   `systemctl suspend`, wake, and decode again.
7. To back out: rebuild without `--optional venus` (plain kernel APK plus
   its boot image), or for a quick test
   `modprobe.blacklist=venus_core,venus_dec,venus_enc` on the command line.
   The test needs `sudo apk add gstreamer-tools v4l-utils ffmpeg mpv` on the
   phone; the boot already logs SMMU faults for SIDs 0x800/0xc20 (splash),
   so filter for 0x23xx.

## A newer kernel

The series doesn't need 7.2: it applies to 7.0 with one context fix. Our
kernel base is the postmarketOS SM8150 tree (now served from
`gitlab.nura.eco`), whose tags stop at `v7.0.0-sm8150`; it never tagged
7.1-7.3, and its next work is `sm8150/7.4-rc1-wip` (2026-11-12, no Venus).
The raphael tree has `sm8150/7.2.0` and `sm8150/7.3.0`, but it's a
Xiaomi-focused fork; basing on it would swap the shared base and mean
rebasing all of `0001`-`0079`. The sensible next bump is to the SM8150
tree's 7.4 once it's tagged, carrying this series along.

## Sources

* AOSPA raphael kernel, branch `venus`:
  <https://github.com/Aospa-raphael-unofficial/linux/tree/venus>
  (PRs #2 and #3, 2026-09-23/24).
* Downstream vidc node in the LineageOS 24 guacamole boot image
  (`qcom,vidc@aa00000`, `pil_video_region` at `0x96e00000`).
* `mdt_loader.c` (`mdt_phdr_valid()`, `qcom_mdt_get_size()`) for the load
  span rule.
