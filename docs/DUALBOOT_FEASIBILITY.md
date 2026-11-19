> **Current state (2026-11-19): this study is historical.** The phone took
> option 4.7, replacing Android with Linux on `userdata`. `userdata` was
> overwritten with the Linux rootfs image (rc4j on 2026-11-14, a fresh rc4l
> image on 2026-11-15), and `boot_b` holds the Linux boot image (rc4l from
> 2026-11-15, rc4n since 2026-11-19). Android's `/data` no longer exists, so
> the `userdata` and `/data` figures below describe the phone as probed on
> 2026-11-10. LineageOS system images remain on slot `_a` (see
> [NURA_PHOSH.md](NURA_PHOSH.md)) but have no data partition; using them
> again would require reformatting `userdata`, which destroys the Linux
> install. That path is untested.
>
> Review status: investigation only; no deployment commands below are approved
> or validated. USB local-rootfs boot remains untested on this setup. Exact
> inactive partition capacities and GPT free space are unverified. Normal
> Android boot and absence of TWRP properties do not prove recovery is absent.
> Any slot-A reuse destroys that Android slot and conflicts with OTA updates.
> Preserve the working DTBO quarantine approach; do not zero DTBO partitions
> based on historical procedures in this report. Confirm current layout and
> a recoverable backup before preparing any write. The ADB probe transcript is
> [`evidence/dualboot/adb-probe.txt`](evidence/dualboot/adb-probe.txt).

# DUALBOOT_FEASIBILITY — Android + a local Linux installation on OnePlus 7 Pro (guacamole)

**Status:** read-only investigation complete. No phone or Android state was changed.
**Date:** 2026-11-10
**Device:** OnePlus 7 Pro `GM1911` / `guacamole` / SM8150
**Current Android:** LineageOS `24.0-20261011-NIGHTLY-guacamole`, Android 17 / SDK 37, `userdebug`, release-keys. Vendor blobs report an OxygenOS 12 base.
**Current boot state:** active slot `_b`, `ro.boot.verifiedbootstate=orange` (reported unlocked), normal boot, `dtbo_idx=0`.
**Evidence:** `adb-probe.txt` (redacted commands/results; kept locally, not published), plus the host-side rootfs image inspection reproduced in Appendix B.
**Scope:** ADB read-only probing only. No root, `su`, remount, reboot, fastboot, recovery, mount, settings write, package install, or partition write was performed. Root-required read-only checks are proposed for the user, not executed.

---

## 0. TL;DR

1. **Android is currently on slot `_b`; slot `_a` is an inactive Android slot, not a second OS.** The device has the usual OnePlus static A/B layout (`system_a/b`, `vendor_a/b`, `boot_a/b`, `dtbo_a/b`, `vbmeta_a/b`) with **one shared `userdata`**, one `metadata`, one `misc` and one `persist`. A/B is an OTA mechanism, not dual boot.
2. **`/data` is not usable as shared storage for Linux.** It is ext4 on `dm-39` (backed by `sda19`) with **file-based encryption plus metadata encryption** (`ro.crypto.type=file`, `ro.crypto.metadata.enabled=true`, `inlinecrypt`). The metadata key is protected by KeyMint/Keymaster in the TEE. Linux cannot mount or read it without Android's `vold` and keys.
3. **No verified unallocated 8 GB region exists.** `dumpsys mount` reports 256,000,000,000 B internal storage; `userdata` alone is 234.86 GB, and the two `system` + two `vendor` partitions add ~9.1 GB. GPT/sysfs are unreadable from the unprivileged shell, so a small gap cannot be ruled out, but it must not be relied on. **Treat free space as none.**
4. **The only practical on-device space is the inactive slot `_a`** (`system_a` ≈ 3.28 GiB, inferred from the active `system_b`; `vendor_a` ≈ 992 MiB, inferred). Using it destroys the dormant Android copy on slot A. Active Android on slot B and all of `userdata` remain intact. This exact pattern was used successfully earlier in this project (`docs/PLAN.md`: rootfs on `/dev/sda13` = `system_a`, Android preserved on `_b`).
5. **The current initramfs is NBD-only and will not boot a local rootfs as-is.** It contains `hooks/netboot.sh`, which unconditionally retries `nbd-client` before any local root scan. `NETBOOT=0` in `build_bootimg.sh` only removes the NBD cmdline; it does **not** remove the hook. A new initramfs without (or with a guarded) netboot hook is required.
6. **Once that initramfs exists, no custom mount hook is needed for a nested pmOS image on `system_a`.** pmOS `mount_subpartitions()` already probes `/dev/disk/by-partlabel/system*`, loop-mounts a partition that contains exactly two subpartitions, and finds the `pmOS_root` label (provided BusyBox `fdisk` parses the nested GPT; a one-line `losetup` fallback is in §5.2). The image already uses a 2-partition GPT with 4096-byte sectors.
7. **Boot selection still needs a computer.** There is no `recovery` partition and no TWRP installed; the bootloader has no boot menu. `fastboot boot` is the non-destructive selector. A persistent Linux boot on slot A is possible, but switching back to Android still needs `fastboot --set-active=b`.
8. **Recommended path:** first test a local rootfs on a USB-C SSD/thumb drive
   (zero phone writes, full 7.5 GiB image, reversible), to learn whether local
   storage actually fixes the camera choppiness. If it does and the user accepts
   losing the dormant slot A Android, install an image **sized to the confirmed
   `system_a` partition (≈3.28 GiB)** and boot it with `fastboot boot`. **Do not
   shrink `userdata`** and **do not put a Linux image inside `/data`**.

**GO / NO-GO in one line:** USB rootfs **GO** (test); `system_a` rootfs **CONDITIONAL GO** (slot A sacrifice); `userdata` resize and `/data`-file rootfs **NO-GO**; replacing Android is a separate decision.

---

## 1. Verified current state

All values below are from the 2026-11-10 read-only probe; see `adb-probe.txt` for commands and raw output.

| Item | Observed value |
|---|---|
| Model / codename | `GM1911` / `guacamole` (SM8150) |
| Android build | LineageOS `24.0-20261011-NIGHTLY-guacamole`, Android 17 / SDK 37 |
| Build type | `userdebug`, `release-keys`, `ro.debuggable=0` |
| Running kernel | Android `4.14.357-openela-perf-...` (not the Nura 7.0 kernel) |
| Active slot | `_b` (`ro.boot.slot_suffix=_b`) |
| A/B support | `ro.build.ab_update=true`; static `_a`/`_b` partitions; no `super` |
| Bootloader | `ro.boot.verifiedbootstate=orange` (reported unlocked); `ro.boot.mode=normal` |
| DTBO entry | `ro.boot.dtbo_idx=0`; hardware version 21 |
| Recovery | No `recovery` partition, no TWRP properties, normal boot |
| `/` | `/dev/block/sda14` = `system_b`, ext4, 3.284 GiB total, 1.363 GiB avail |
| `/vendor` | `/dev/block/sde44` = `vendor_b`, ext4, 992 MiB total, 241.8 MiB avail |
| `/data` | `/dev/block/dm-39` over `sda19` = `userdata`, ext4, 218.75 GiB total, 193.26 GiB avail, `inlinecrypt` |
| `/metadata` | `/dev/block/sda17`, ext4, 9.98 MiB total, 8.75 MiB free |
| Internal storage (Android) | 256,000,000,000 B (238.4 GiB), per `dumpsys mount` |
| Encryption | `ro.crypto.type=file` (FBE), `ro.crypto.metadata.enabled=true`, `ro.crypto.state=encrypted`; `vold` and `keymaster-4-0` running |
| External media | No SD slot; `sm list-volumes all` shows only `private mounted`; no USB volume mounted |
| Shell privileges | uid 2000(shell), SELinux enforcing, no `su`, no `adb root` path |

### 1.1 Why unprivileged probing stops where it does

`/proc/partitions`, `/sys/class/block/*/size`, `/sys/class/block/*/start`, raw
`/dev/block/*` reads, GPT reads, `blkid` on block devices, `/metadata` listing,
`/mnt/media_rw`, and `df /metadata` are denied to `shell`. `statfs` on
`/metadata` still works, which is why the size/free figures above are available
without a block-device read. Exact partition offsets, `system_a`/`vendor_a`
sizes, and GPT free space are therefore **not** verified; they require root or a
separate Linux boot. No attempt was made to elevate.

---

## 2. Storage and partition layout

### 2.1 LUN 0 (`sda`) — the Android/`userdata` disk

`/dev/block/by-name/` exposes the following `sda` partitions (symlink targets
verified; the complete all-LUN map is in `partition-map.txt`):

| Partition | by-name | Current use | Size |
|---|---|---|---|
| `sda1` | `ssd` | Android secure storage metadata | unknown |
| `sda2` | `persist` | mounted at `/mnt/vendor/persist` (rw, ext4) — calibration/identity; **do not touch** | unknown |
| `sda3` | `misc` | bootloader/BCB; A/B slot control | unknown |
| `sda4` | `param` | boot parameters | unknown |
| `sda5` | `keystore` | keystore | unknown |
| `sda6` | `frp` | factory reset protection | unknown |
| `sda7` | `op2` | OnePlus reserve | unknown |
| `sda8`/`sda9` | `oem_dycnvbk`/`oem_stanvbk` | modem OEM NV | unknown |
| `sda10`/`sda11` | `mdm_oem_dycnvbk`/`mdm_oem_stanvbk` | modem OEM NV | unknown |
| `sda12` | `config` | config | unknown |
| `sda13` | `system_a` | **inactive slot A root** | ≈3.284 GiB (inferred = `system_b`) |
| `sda14` | `system_b` | **active `/`** | 3.284 GiB (statfs) |
| `sda15`/`sda16` | `odm_a`/`odm_b` | unused by this ROM (`/odm` is empty) | unknown |
| `sda17` | `metadata` | metadata-encryption key blobs | 9.98 MiB |
| `sda18` | `rawdump` | ramdump reserve; not general storage | unknown |
| `sda19` | `userdata` | **Android `/data`** | 218.75 GiB filesystem |

`userdata` is the last `sda` partition. The mounted `system_b` filesystem is
3,526,774,784 B (3.284 GiB). A/B partitions are normally created equal, so
`system_a` is expected to be the same size; that is an inference, not a
verified block-device read.

### 2.2 LUN 4 (`sde`) — firmware/boot/vendor pool

Relevant entries (the full map contains `sde1`–`sde80`):

| Partition | by-name | Current use |
|---|---|---|
| `sde4` / `sde32` | `modem_a` / `modem_b` | `modem_b` mounted at `/vendor/firmware_mnt` (vfat) |
| `sde5` / `sde33` | `bluetooth_a` / `bluetooth_b` | `bluetooth_b` mounted at `/vendor/bt_firmware` (vfat) |
| `sde9` / `sde37` | `dsp_a` / `dsp_b` | `dsp_b` mounted at `/vendor/dsp` (ext4) |
| `sde11` / `sde39` | `boot_a` / `boot_b` | Android boot images |
| `sde16` / `sde44` | `vendor_a` / `vendor_b` | `vendor_b` is `/vendor` (992 MiB) |
| `sde17` / `sde45` | `vbmeta_a` / `vbmeta_b` | AVB metadata |
| `sde18` / `sde46` | `dtbo_a` / `dtbo_b` | DTBO overlays; `dtbo_b` is active |

LUNs 1–3 and 5 hold `xbl`/`xbl_config`, `cdt`/`ddr`/`mdmddr`, and modem
`modemst`/`fsg`/`fsc` respectively. These are **identity, calibration, modem
and boot-chain storage**; none is a candidate for Linux and none was touched.

### 2.3 Is there genuinely unused space?

- `dumpsys mount` reports **256,000,000,000 B** total internal storage.
- Known large allocations: `userdata` 234.86 GB + `system_a`/`system_b`
  ~7.05 GB + `vendor_a`/`vendor_b` ~2.08 GB + `metadata` 0.01 GB ≈ **244 GB**.
- The remainder is not readable from the unprivileged shell. It is consistent
  with the many small `sda`/`sde`/`sdb`/`sdc`/`sdf` partitions (boot, modem,
  DSP, firmware, xbl, persist, rawdump, etc.), but their exact sizes and any
  alignment gap cannot be verified without root or a Linux boot.
- **There is no evidence of a free 8 GB region**, and `userdata` is the last
  `sda` partition. A small alignment gap could exist, but it cannot be
  discovered or relied on from the unprivileged shell.

**Conclusion:** the only verified free space is *inside* mounted filesystems:
1.36 GiB on `/`, 242 MiB on `/vendor`, 8.75 MiB on `/metadata`, and 193.26 GiB
inside `/data`. The first three are too small and are Android system storage;
the last is encrypted and unavailable to Linux. **No partition can be created
without either repurposing slot A, resizing `userdata`, or using external
storage.**

### 2.4 `userdata` filesystem and encryption

- `/dev/block/dm-39` is the mounted `userdata` device; it maps `sda19`.
- Mount options include `inlinecrypt`; filesystem is ext4.
- `ro.crypto.type=file` means **file-based encryption (FBE)**.
- `ro.crypto.metadata.enabled=true` and the 9.98 MiB `/metadata` partition mean
  **metadata encryption** (`dm-default-key`) is also enabled.
- AOSP's metadata-encryption documentation states that a single key, protected
  by KeyMint/Keymaster (TEE), encrypts everything not covered by FBE, and that
  **nothing in the userdata partition can be read until that key is present**
  ([source.android.com: metadata encryption](https://source.android.com/docs/security/features/encryption/metadata)).
  AOSP's FBE documentation likewise requires Keymaster/Gatekeeper in a TEE so
  that an unauthorized OS cannot simply request the DE keys
  ([source.android.com: file-based encryption](https://source.android.com/docs/security/features/encryption/file-based)).
- At probe time `dumpsys mount` reported `CE unlocked users: []` (the device was
  not presenting unlocked credential-encrypted storage). No user files were
  read.

**Consequence:** Linux cannot mount `/data`, and a Linux image stored as a file
inside `/data` is not readable by Linux either. Disabling encryption or
reformatting `userdata` would destroy Android data and weaken security; that is
out of scope and not recommended.

---

## 3. A/B is not two independent operating systems

The device has two copies of the *system* partitions, not two `userdata`
partitions:

- `system_a`/`system_b`, `vendor_a`/`vendor_b`, `boot_a`/`boot_b`,
  `dtbo_a`/`dtbo_b`, `vbmeta_a`/`vbmeta_b` exist in pairs.
- There is exactly **one** `userdata` (`sda19`), **one** `metadata` (`sda17`),
  **one** `misc` (`sda3`), and **one** `persist` (`sda2`).
- Android's OTA mechanism writes the inactive slot and then switches to it.
  Both slots are expected to contain Android; they share the same app data,
  accounts, settings and encryption keys.
- AOSP describes A/B as two sets of partitions used for seamless updates
  ([source.android.com: A/B updates](https://source.android.com/docs/core/ota/ab));
  the partition overview lists slots for `boot`, `system`, `vendor` and
  `radio`, not for `userdata`
  ([source.android.com: partitions](https://source.android.com/docs/core/architecture/partitions)).

Therefore, "install Linux in slot A" is **repurposing the dormant Android
update slot**, not a supported dual-OS scheme. It works because Android is
running from slot B, but it is fragile with respect to Android OTA: the next
update will normally target slot A and overwrite Linux. Conversely, if slot A
is ever activated with Android's `boot_a` but Linux's `system_a`, Android will
fail to boot that slot. Any persistent slot-A plan must handle `boot_a` and
`dtbo_a` consistently, not just `system_a`.

**Do not assume slot A is empty.** It had zero I/O since kernel boot and is
inactive, but its contents could not be read. It is an allocated Android
partition; overwriting it is destructive to whatever is there.

---

## 4. Options, ranked by disruption

### 4.1 Baseline: keep the current NBD rootfs

- **What it is:** the proven workflow. The phone boots a temporary `boot.img`
  via `fastboot boot`; the initramfs brings up a USB NCM gadget and connects
  `nbd-client` to a host server (port 10001); the rootfs image stays on the
  host.
- **Important distinction:** the current `oneplus-guacamole.img` is a host-side
  file. The current Linux rootfs is **not installed on the phone** and is not
  backed by Android storage. Booting or flashing the current `boot.img` alone
  does not change that. Its initramfs still expects an NBD server, and without
  that server it loops in `hooks/netboot.sh` rather than falling back to any
  local filesystem.
- **Phone writes:** none.
- **Pros:** proven on this phone; no partition risk; easy rollback (stop the
  server, reboot Android).
- **Cons:** a computer must serve NBD for every boot; the USB gadget link adds
  latency/throughput limits (this probe did not benchmark it); the initramfs is
  NBD-only; the host must stay up.
- **Verdict:** keep as the known-good fallback. Not a path to a local Linux
  installation.

### 4.2 USB-C SSD / thumb-drive rootfs (no phone partition writes)

- **What it is:** write the pmOS image to a USB-C mass-storage device; boot a
  **non-netboot** initramfs; the kernel enumerates the USB disk and pmOS finds
  `pmOS_root` on it. Nothing on the phone's UFS is written.
- **Evidence it can work:** the Nura 7.0 kernel has `CONFIG_USB_XHCI_HCD=y`,
  `CONFIG_USB_STORAGE=y`, `CONFIG_USB_DWC3=y`,
  `CONFIG_USB_DWC3_DUAL_ROLE=y`, `CONFIG_TYPEC=y`,
  `CONFIG_TYPEC_QCOM_PMIC=y`, `CONFIG_USB_ROLE_SWITCH=y`, `CONFIG_EXT4_FS=y`,
  and loop/ext4 built in. USB host/OTG was verified earlier on this device
  (`docs/PLAN.md`: a SanDisk thumb drive enumerated at SuperSpeed).
- **Caveats:**
  - `CONFIG_USB_UAS` is not set; USB mass storage uses the BOT `usb-storage`
    driver. Most thumb drives/SSDs fall back to BOT; a UAS-only bridge may not
    work.
  - The phone must switch from USB peripheral (fastboot) to host after
    `fastboot boot`. In practice a powered USB-C hub/dock or a cable swap is
    needed, and host mode does **not** charge the phone unless the hub is
    powered. This is a hardware test, not a verified flow.
  - The initramfs must be rebuilt without the netboot hook (see §5).
- **Storage overhead:** one USB device of **at least 16 GB** (the current image
  is 8.05 GB / 7.50 GiB; a nominal 8 GB drive is too small), zero phone storage.
- **Rollback:** unplug the drive; Android is untouched.
- **Verdict:** **GO for a test.** This is the lowest-risk way to answer "does a
  local rootfs remove the NBD bottleneck?" and it preserves every Android
  partition, including both slots.

### 4.3 Local image in the inactive `system_a` slot (on-device, conditional)

- **What it is:** write a shrunk pmOS image (nested GPT, 4096-byte sectors) to
  `/dev/block/by-partlabel/system_a` (`sda13`), leaving Android on slot B and
  all of `userdata`/`metadata`/`persist` untouched. Boot it with
  `fastboot boot` (temporary) or, later, `boot_a` + slot A (persistent).
- **Prior art on this exact device:** `docs/PLAN.md` records "Rootfs deployed on
  `/dev/sda13` (`system_a`, inactive slot `_a`), preserving stock Android on
  slot `_b`." `docs/BRINGUP_GUIDE.md` documents formatting `/dev/sda13`,
  installing a pmOS rootfs, and loop-mapping the nested image inside the
  initramfs. The mechanism is proven; only the image size and the non-netboot
  initramfs need to be rebuilt for the current camera rootfs.
- **Size:** `system_a` is ≈3.284 GiB (inferred from the active `system_b`). The
  current image uses 2.29 GiB for `/` plus 74 MiB for `/boot`, so a **full-size
  image matching the confirmed `system_a` partition** (boot ≈256 MiB, root
  rest) should fit with ≈0.7 GiB free. Build it to the exact partition size;
  a smaller image leaves the nested GPT backup before the partition end, which
  can confuse `losetup -P`/`fdisk`. If you must use a smaller image, add a
  `losetup --sizelimit` hook. Verify the exact `system_a` size before writing.
- **What is destroyed:** the dormant Android installation on slot A
  (`system_a`, and if you also use it, `vendor_a`). Active Android on slot B,
  `userdata`, `metadata`, `persist`, modem and identity partitions are **not**
  touched.
- **Android OTA:** while Android B remains the active slot, the next LineageOS
  update will normally write the inactive slot A and overwrite Linux. If you
  make slot A active, the OTA target becomes slot B instead. Disable automatic
  updates or accept that an update will replace one of the two systems.
- **Rollback:** Android B continues to boot normally if only `fastboot boot` is
  used. To restore slot A Android, flash the matching LineageOS slot-A images
  (`system_a`, `boot_a`, `dtbo_a`, and `vendor_a` if changed). The local
  `ref/lineageos/` trees contain reference `boot.img`/`dtbo.img` images, not a
  full OTA zip for the installed 2026-10-11 build; download the exact OTA first
  if a restorable slot A is required.
- **Verdict:** **CONDITIONAL GO.** GO only if the user accepts losing the
  dormant slot A Android and the OTA conflict. It preserves the Android the
  phone actually boots. If "preserve Android" means both A/B slots and OTA
  capability must remain intact, use USB instead.

### 4.4 `system_a` + `vendor_a` as root + `/home` (optional variant)

- **What it is:** put the 3.28 GiB rootfs image on `system_a` and format the
  inactive `vendor_a` (≈992 MiB, inferred) as ext4 for `/home`/media. This adds
  ~1 GiB of writable space without repartitioning `userdata`.
- **Cost:** destroys slot A's vendor blobs too; more slot-A breakage; an extra
  partition write and fstab entry. It does not solve the fundamental 3.28 GiB
  rootfs limit, it only adds media space.
- **Verdict:** **CONDITIONAL / usually NO-GO.** Only worth it if the full-size
  rootfs free space proves too small for camera recordings. Prefer a USB drive
  for media instead.

### 4.5 Shrink `userdata` to create a new partition (NO-GO)

- **Why it is attractive:** `userdata` has 193 GiB free; a 16–32 GB Linux
  partition would give ample space and keep both Android slots.
- **Why it is not safe here:**
  - `/data` is ext4 on `dm-39`, with **metadata encryption** (`dm-default-key`)
    and **FBE**. Offline resizing requires the metadata key, which is protected
    by KeyMint/Keymaster in the TEE; Linux does not have it.
  - Shrinking a mounted ext4 filesystem is not supported; it would need an
    offline `resize2fs`, plus shrinking the dm-default-key mapping, updating
    Android's fstab/metadata, and relocating the GPT backup at the end of
    `sda`. Each step can lose data.
  - Android's `vold`/`fs_mgr` and A/B OTA expect `userdata` to occupy the
    remainder of the disk. There is no verified Android-supported resize path
    for this encrypted layout.
  - A full `userdata` backup is not possible through unprivileged ADB (FBE,
    no root); without a backup, a failed resize is unrecoverable.
- **Verdict:** **NO-GO.** Do not attempt a "lossless" resize. If a large
  on-device Linux partition is a hard requirement, the honest options are
  external USB or replacing Android, not resizing encrypted `userdata`.

### 4.6 Shared `userdata` / Linux image file inside `/data` (NO-GO)

- **Direct sharing is not possible.** Mounting Android's `/data` from Linux
  would require the metadata-encryption key from KeyMint/Keymaster in the TEE
  and `vold`'s `dm-default-key` setup. Linux cannot obtain either; there is no
  supported path to reconstruct the dm mapping from the raw `sda19` device.
- **A file inside `/data` is equally unavailable.** The file would live on an
  ext4 filesystem whose contents and metadata are encrypted by `dm-default-key`
  + FBE; Linux cannot read the file without Android's keys and `vold`.
- AOSP: "nothing in the userdata partition can be read until the metadata
  encryption key is present"; the key is TEE-protected.
- Even if the keys were available, two OSes alternating on the same encrypted
  volume would need identical dm/fstab/encryption policy; concurrent or
  interrupted access risks corruption, and bypassing FBE would weaken Android's
  at-rest security.
- Even if the file were in `/data/media/0` (user storage), it is still inside
  the encrypted volume. `adb push`/MTP access through Android does not make it
  readable by a Linux kernel at boot.
- **Verdict:** **NO-GO.** This is the most common generic "dual boot" recipe
  and it does not apply to a metadata-encrypted Android 17 device.

### 4.7 Replace Android with Linux on `userdata` (separate decision)

- **What it is:** the earlier full-device installation: write the pmOS image to
  `userdata` (`sda19`) and boot it; Android is gone.
- **Pros:** full 218 GiB, no size compromise, proven on this phone.
- **Cons:** destroys Android and all Android data; no dual boot; reversing
  requires a full Android reflash and data restore.
- **Verdict:** **separate GO/NO-GO**, only if the user explicitly decides to
  abandon Android. It is not a "keep Android" option.

### 4.8 Android chroot / proot (not a boot-level dual boot)

- Termux + proot/chroot can run a Linux userland inside Android without
  touching partitions, but it cannot run the Nura kernel, camera drivers,
  Phosh/DRM stack or the hardware enablement work. It does not address the
  camera pipeline. Mentioned only to rule it out as a substitute.

---

## 5. Can a local rootfs boot without the host NBD server?

**Yes, but the current initramfs must be rebuilt.** The mount path itself
already exists in pmOS.

### 5.1 The current initramfs is netboot-only

Inspected from
`.work/rear-camera/rootfs/export/initramfs` (zstd cpio, 12.77 MB / 12.18 MiB):

- It contains `hooks/netboot.sh`.
- That hook unconditionally runs, in a retry loop:
  `busybox nbd-client <host> 10001 /dev/nbd0 -b 4096`.
- Only after the NBD connection succeeds does it call
  `mount_subpartitions()`.
- If NBD fails, it loops forever; it does **not** fall back to a local
  `pmOS_root`.
- `NETBOOT=0` in `scripts/build_bootimg.sh` only omits the
  `pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1` cmdline. It does **not** remove
  `hooks/netboot.sh`.

This matches `docs/NURA_ROOTFS_BUILD.md`: "If `nbd-client` fails, the hook loops
before the subpartition scan and does not fall back to the internal rootfs."

**Therefore:** a local-rootfs boot image needs a **non-netboot initramfs**.
The cleanest build is the normal pmOS initramfs without the
`postmarketos-mkinitfs-hook-netboot` package. If NBD fallback is desired, add a
guard to the hook (return early when a local `PMOS_ROOT` is already found), but
that is extra code and not needed for the first test. **Flashing or booting the
current `boot.img` alone is not enough**: the NBD hook still runs.

### 5.2 pmOS already knows how to mount a nested image on `system_a`

The default `init_functions.sh` `mount_subpartitions()`:

- tries `/dev/disk/by-partlabel/userdata /dev/disk/by-partlabel/system* /dev/mapper/system*`;
- for each candidate, counts partitions with `fdisk -l`;
- only if the candidate has **exactly two** subpartitions does it run
  `losetup --show -Pf --direct-io=on --sector-size 4096 <partition>`;
- then `find_root_partition()` looks for the `pmOS_root` filesystem label;
- `find_partition()` can also select a path or UUID directly, but a label scan
  is sufficient.

The initramfs does not ship a separate `fdisk` file, but its BusyBox binary
contains the `fdisk` applet and `init` runs `busybox --install -s`, which creates
the applet links at boot. The `losetup`, `blkid`, `e2fsck` and `resize2fs`
helpers are present as separate files (`host-image-check.txt`). If the BusyBox
`fdisk` in this build cannot parse the 4096-byte-sector GPT, add a tiny hook
that runs `losetup -Pf --sector-size 4096 /dev/disk/by-partlabel/system_a`
before the normal subpartition scan; the label scan then finds `pmOS_root`.
That is a one-line fallback, not a redesign.

The current rootfs image is a 2-partition GPT (p1 `pmOS_boot`, p2
`pmOS_root`), 4096-byte sectors. Therefore, **no custom mount hook is needed**:
with a non-netboot initramfs, the normal `mount_subpartitions()` pass should
find the image on `system_a`, create `/dev/loopNp1`/`/dev/loopNp2`, and mount
`pmOS_root`. The loop number is not fixed; do not hardcode `/dev/loop0`. This
is a code-path inference from the installed initramfs; it was not booted in
this investigation.

The earlier `system_a` deployment used the same idea with an explicit loop
mapping inside the initramfs (`docs/BRINGUP_GUIDE.md`). The current initramfs's
netboot hook, together with the NBD `pmos.root=/dev/nbd0p2` cmdline it is paired
with, is what prevents the built-in mechanism from running today. `NETBOOT=0`
removes the cmdline; the hook must also be removed or guarded.

### 5.3 Image fit

Current image (host-side inspection):

| Partition | Size | Used | Free |
|---|---:|---:|---:|
| p1 `pmOS_boot` | 1,016,070,144 B (969 MiB) | 77,570,048 B (74 MiB) | ~895 MiB |
| p2 `pmOS_root` | 7,024,410,624 B (6.54 GiB) | 2,458,353,664 B (2.29 GiB) | ~4.25 GiB |
| whole image | 8,049,917,952 B (7.50 GiB) | | |

The rootfs payload is only 2.29 GiB. A **full-size image matching the confirmed
`system_a` partition** (≈3.28 GiB, boot ≈256 MiB, root rest) leaves ≈0.7 GiB
free and keeps the nested GPT backup at the partition end, which is what the
default `mount_subpartitions()` `losetup -P` path expects. A smaller image fits
space-wise but its backup GPT is before the partition end; the kernel may warn
or fail to create the subpartitions. If a smaller image is required, add a
`losetup --sizelimit` hook, or write the image and run `sgdisk -e` on the loop
device to relocate the nested backup GPT to the partition end. Note that
pmbootstrap may round image sizes to MiB; verify the byte size and use one of
these fallbacks if it cannot match `system_a` exactly. The exact free space
depends on the rebuilt package set; verify with `dumpe2fs` before writing. Do
not write the current 7.5 GiB image; it does not fit.

### 5.4 Build/deploy changes that would actually be required (future, user-run)

No deployment was performed. The minimum sequence for the `system_a` option is:

1. **Confirm `system_a` size and contents.** From a read-only Linux boot
   (current NBD path) run `blockdev --getsize64 /dev/disk/by-partlabel/system_a`
   and `sgdisk -p /dev/sda`. Alternatively, from the bootloader,
   `fastboot getvar partition-size:system_a` is read-only. Do not write yet.
2. **Back up slot A if rollback matters.** Image `system_a`, `boot_a`,
   `dtbo_a` (and `vendor_a` if used) to the host or a USB drive; record
   SHA-256. The matching LineageOS OTA is needed to restore a bootable slot A.
3. **Build an image exactly the confirmed `system_a` size** (≈3.28 GiB) with
   4096-byte sectors, 2-partition GPT (`pmOS_boot` + `pmOS_root`), the camera
   kernel/modules and packages. A 256 MiB boot partition and the rest for root
   leaves ≈0.7 GiB free. If pmbootstrap's MiB rounding prevents an exact match,
   use a `losetup --sizelimit` hook or run `sgdisk -e` on the loop device after
   writing to relocate the nested backup GPT. Verify the root filesystem has
   enough free space for recordings. Rebuilding the image creates new ext4
   UUIDs; update `/etc/fstab` (or preserve the UUIDs) so the `/boot` mount still
   resolves.
4. **Build a non-netboot initramfs** from that rootfs (remove or guard
   `postmarketos-mkinitfs-hook-netboot`). Verify `hooks/netboot.sh` is absent
   (or guarded) and that `losetup`, `blkid`, `e2fsck`, loop and ext4 are
   present.
5. **Build the boot image** with the existing ABL-quarantine wrapper:
   `DTB_MODE=abl-quarantine NETBOOT=0 KERNEL_DIR=... INITRAMFS=... ./scripts/build_bootimg.sh`.
   The wrapper is required because ABL applies the active slot's stock DTBO;
   do **not** zero `dtbo_b` while Android is on B.
6. **Write only `system_a`** (e.g., `fastboot flash system_a <image>` or a
   verified `dd` from a Linux boot). Do not write `userdata`, `metadata`,
   `persist`, modem, `misc`, `boot_b`, or `dtbo_b`. Use the HS-G3-capped Nura
   kernel for any Linux-side write (kernel patch `0010`; HS-G4 silently dropped
   buffered writes on this phone), run `sync`, and verify the written image hash
   before booting.
7. **Temporary test:** `fastboot boot build/boot.img` (non-destructive). Confirm
   the initramfs finds `pmOS_root` on the loop device, the rootfs mounts
   read-write, no NBD is involved, and the camera stack works. Keep Android B
   active.
8. **Persistent option (only after the temporary test):** flash `boot_a` with
   the same boot image and `fastboot --set-active=a`. ABL will then apply
   `dtbo_a`; verify the quarantine wrapper against `dtbo_a`, or (if accepting
   slot-A destruction) zero `dtbo_a` as was done historically for `dtbo_b`.
   Returning to Android requires `fastboot --set-active=b`.

**No host NBD server is needed** after step 5. A computer is still needed for
`fastboot boot` and for any slot switch.

### 5.5 Boot selection reality

- `fastboot boot` loads the boot image into RAM and does not flash; it is the
  current non-destructive selector and the safest way to test Linux.
- A persistent Linux slot A boots without a computer on every boot, but
  **switching back to Android still needs `fastboot --set-active=b`**.
- There is no ABL boot menu, no `recovery` partition, no installed TWRP, and no
  root on Android to write the `misc` BCB/slot metadata. A phone-only selector
  is not available in the observed state.

---

## 6. Recovery and non-invasive boot selection

**Verified:** no `/dev/block/by-name/recovery`; `misc` is `sda3`; no TWRP
properties; `ro.boot.mode=normal`; `ro.boot.recoveryreason=000`; the device is
in normal Android boot. On A/B devices, recovery (if present) is normally part
of a boot image, not a separate partition, but no recovery image was verified
or assumed here.

**What exists:** the host-side `scripts/fastboot_boot.sh`, which runs
`fastboot boot` and works around the OnePlus ABL stall by resetting the USB
device and restarting the bootloader. That is a **host-side USB operation**, not
a phone write; it was not run during this investigation.

**What does not exist:** a boot menu, a dual-boot selector, a recovery
partition, TWRP, root, or a verified phone-only way to choose Linux. Historic
TWRP/root state from other sessions must not be assumed; the current probe shows
none of it.

**Consequence:** the "temporary fastboot boot selects Linux" flow is valid, but
it is **computer-dependent**. If the goal is to boot Linux without a PC, the
only on-device option would be a persistent slot-A boot plus a phone-side way to
switch slots; the latter requires root or a boot menu, neither of which is
present. Treat phone-only selection as **NO-GO** for now.

---

## 7. Recommendation and GO/NO-GO

### 7.1 Recommended sequence

**Phase 0 — measure, don't write (safe):**
1. Build a non-netboot initramfs from the current camera rootfs.
2. Test the existing 7.5 GiB image from a USB-C SSD/thumb drive (no phone
   writes). This answers whether local storage removes the NBD bottleneck. If
   the USB host-mode/cable dance is impractical, skip directly to the
   `system_a` temporary-boot test after backing up slot A; the on-device image
   needs no USB host mode.
3. Profile the camera pipeline (I/O wait, CPU, encoder, thermals). If NBD is
   not the bottleneck, the on-device install will not fix choppiness by itself.

**Phase 1 — on-device, conditional:**
4. If local storage helps and the user accepts losing the dormant slot A
   Android, rebuild the image at the confirmed `system_a` size, back up slot A,
   write `system_a`, and boot it with `fastboot boot`. Keep Android B and all
   user data untouched.
5. Only after a successful temporary boot, consider persistent `boot_a` +
   slot A. Keep a known-good Android boot path and the matching OTA for
   rollback.

**Do not:**
- shrink or reformat `userdata`;
- put the Linux image inside `/data`;
- zero `dtbo_b` or flash `boot_b` while Android is on B;
- touch `persist`, `modem`, `modemst`, `fsg`, `fsc`, `devinfo`, `cdt`,
  `secdata`, `apdp`, `msadp`, `frp`, `keystore`, or any identity/calibration
  partition;
- assume TWRP/root or a phone-only selector exists.

### 7.2 GO/NO-GO table

| Approach | Verdict | Condition / reason |
|---|---|---|
| Keep NBD rootfs | **GO (baseline)** | Proven; needs PC server; USB-gadget throughput (unbenchmarked); no phone writes. |
| USB-C SSD/thumb-drive rootfs | **GO (test)** | No phone writes; full 7.5 GiB image; reversible. Needs a ≥16 GB drive, non-netboot initramfs, host-mode USB dock/power, and hardware test. UAS is not enabled; BOT mass storage only. |
| `system_a` image sized to the slot (≈3.28 GiB), `fastboot boot` | **CONDITIONAL GO** | Proven pattern on this phone; Android B + userdata preserved. Destroys dormant slot A Android; OTA will overwrite Linux; requires exact-size image and non-netboot initramfs. |
| `system_a` + `vendor_a` | **CONDITIONAL / usually NO-GO** | More slot-A destruction for ~1 GiB of media space; use USB media instead unless needed. |
| Persistent `boot_a` + slot A | **CONDITIONAL GO** | Only after temporary boot works; requires `dtbo_a` handling and `fastboot --set-active` to return to Android. |
| Shrink `userdata` | **NO-GO** | FBE + metadata encryption; keys in TEE; no supported offline resize; high data-loss risk; no unprivileged backup. |
| Linux image file inside `/data` | **NO-GO** | Linux cannot read the encrypted volume or the file. |
| Replace Android on `userdata` | **SEPARATE DECISION** | Only if the user explicitly abandons Android; destroys Android data; not a dual-boot option. |
| Phone-only boot selector | **NO-GO (current state)** | No recovery partition, TWRP, root, or ABL menu; `fastboot` from a computer is required. |

### 7.3 Storage overhead, risk and rollback

| Option | Phone storage used | Main risk | Rollback |
|---|---:|---|---|
| NBD | 0 | PC/server dependency; USB bottleneck | Stop server; reboot Android |
| USB rootfs | 0 | Host-mode power/role; UAS-only drives; initramfs rebuild | Unplug; Android untouched |
| `system_a` image | ≈3.28 GiB (the full slot A) | Slot A Android lost; OTA overwrites Linux; image-fit/DTBO mistakes | Android B unaffected; reflash slot A from matching OTA; `fastboot --set-active=b` if changed |
| `system_a`+`vendor_a` | ≈4.25 GiB of slot A (3.28 + 0.97) | Same plus vendor_a loss | Same; reflash both slot-A images |
| Shrink `userdata` | 16–32 GiB from `/data` | Irreversible data loss; encryption/dm/GPT complexity | Only a full `userdata` backup; not available via read-only ADB |
| Replace Android | 218.75 GiB `userdata` | Android and all data lost | Reflash Android + restore backup |

**No lossless resize is promised or implied.** Do not proceed with `userdata`
resizing without a verified full backup and a tested restore path; neither
exists in the observed state.

---

## 8. What must be verified before any write

These are read-only checks for the user to run later; none was run in this
investigation. Before relying on `fastboot boot`/`flash`, confirm with the
read-only `fastboot getvar unlocked` that the bootloader really is unlocked;
`ro.boot.verifiedbootstate=orange` is strong evidence, but it is not a
substitute for the bootloader's own answer.

1. **`system_a` exact size and contents** — from a read-only Linux boot,
   identify the LUN 0 whole disk (the parent of
   `/dev/disk/by-partlabel/system_a`; `sdX` names are not stable), then run
   `blockdev --getsize64 /dev/disk/by-partlabel/system_a` and
   `sgdisk -p <LUN0 disk>`; also `blkid /dev/disk/by-partlabel/system_a`.
   Or from fastboot: `fastboot getvar partition-size:system_a` (read-only).
2. **`vendor_a` exact size** — same method, if the `system_a`+`vendor_a` variant
   is considered.
3. **Slot A restorability** — download the exact
   `lineage-24.0-20261011` OTA and verify the `system_a`/`boot_a`/`dtbo_a`
   images before overwriting slot A. The local `ref/lineageos/` trees contain
   only reference `boot.img`/`dtbo.img` files, not a full OTA zip for the
   installed 2026-10-11 build.
4. **DTBO behavior** — confirm whether the ABL-quarantine wrapper neutralizes
   `dtbo_a` as it does `dtbo_b`; otherwise decide whether zeroing `dtbo_a`
   (destructive to slot A) is acceptable.
5. **Image fit and initramfs** — verify the rebuilt image is exactly the
   confirmed `system_a` size (or has a sizelimit/regenerated-GPT plan), uses
   4096-byte sectors, has `pmOS_boot`/`pmOS_root`, and the initramfs has no
   active netboot hook. On the phone, confirm `fdisk -l` sees the image's two
   subpartitions (or add the `losetup -Pf` fallback hook).
6. **Rootfs fstab** — confirm `/boot` and `/` are referenced by UUID, not by a
   fixed `/dev/loop0pN`, so the loop number can vary.
7. **USB host boot** (if chosen) — test the drive/dock, role switch, power, and
   enumeration time on the actual phone; do not assume it works from the
   historical OTG test alone.

---

## 9. Unknowns

- Exact `system_a` size and contents; exact `vendor_a`/`odm_a` sizes. These need
  root or a Linux block-device read.
- Whether slot A currently contains a complete, bootable LineageOS build. It is
  allocated and inactive; its contents are unreadable from the unprivileged
  shell.
- Whether `fastboot flash system_a` accepts a ≈3.28 GiB nested GPT image and
  whether the ABL imposes a size/format restriction. This must be tested with a
  backup in place.
- Whether the ABL-quarantine wrapper works with `dtbo_a` (it is proven for the
  active `dtbo_b` path, not for slot A).
- AVB behavior when `boot_a`/`vbmeta_a` no longer match `system_a` after a
  persistent slot-A install. The unlocked/orange state should allow it, but
  this was not tested; do not modify `vbmeta_b`.
- Whether USB host mode enumerates a drive early enough for the initramfs, and
  whether a powered dock is required.
- Whether the camera choppiness is caused by NBD/USB I/O or by CPU/ISP/encoder
  limits. Local storage removes the former, not necessarily the latter.
- Android OTA behavior after slot A is repurposed: almost certainly it
  overwrites Linux; the exact updater behavior was not tested.
- Rootfs `/etc/fstab` loop/UUID details for the current image; the documented
  prior install used UUIDs, but the current image was not booted to confirm.

---

## 10. Safety statement

No phone or Android state was changed. No `su`, `adb root`, `adb remount`,
`adb reboot`, `fastboot`, recovery, mount/unmount, `setprop`, `settings put`,
package install, service stop, partition write, `dd`, `fsck`, `resize2fs`,
`mkfs`, or USB/network change was performed. Block-device, GPT, sysfs-size and
`/metadata` reads that required privilege were recorded as denied and not
retried. The device serial appeared only in ADB transport/cmdline output and is
redacted; no IMEI, MAC address, calibration data, keys, shell history, or user
files were read or published. The only files created are the reports in this
directory and temporary read-only extraction under `/tmp`.

Calibration and identity partitions (`persist`, `modem`, `modemst*`, `fsg`,
`fsc`, `devinfo`, `cdt`, `secdata`, `apdp`, `msadp`, `frp`, `keystore`) are
**not** disposable and are not part of any recommended option. The current
camera paths do not require extracting or modifying them.

---

## Appendix A — Evidence map

| Claim | Evidence |
|---|---|
| Android version/slot/build | `adb-probe.txt` §2–3; `getprop` |
| Partition map | `adb-probe.txt` §4; `partition-map.txt` |
| `/`, `/vendor`, `/data`, `/metadata` sizes/free | `adb-probe.txt` §5; `df`/`statfs` |
| FBE + metadata encryption | `adb-probe.txt` §7; `ro.crypto.*`, mount options, `dumpsys diskstats` |
| No recovery partition/TWRP | `adb-probe.txt` §8 |
| No external volume | `adb-probe.txt` §9 |
| Slot A inactive (I/O only) | `adb-probe.txt` §10 |
| Unprivileged limits | `adb-probe.txt` §11 |
| Rootfs image size/used | Appendix B; `host-image-check.txt` (host-side GPT/ext4 parse) |
| Netboot-only initramfs | Host-side `zstd|cpio` listing; `hooks/netboot.sh` |
| pmOS subpartition logic | Extracted `init_functions.sh` `mount_subpartitions()` |
| Prior `system_a` install | `docs/PLAN.md`; `docs/BRINGUP_GUIDE.md` |
| USB host config | `.work/rear-camera/linux-v7.0.0-sm8150/.output/.config`; `docs/PLAN.md` |

## Appendix B — Host-side rootfs image inspection (read-only)

Image:
`.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img`

GPT parsed at 4096-byte sectors (the image's MBR/GPT uses 4096-byte logical
sectors; `deviceinfo_rootfs_image_sector_size="4096"`):

```
p1  first=2048     last=250111    bytes=1,016,070,144  fs label=pmOS_boot
p2  first=250112   last=1965055   bytes=7,024,410,624  fs label=pmOS_root
file size: 8,049,917,952 bytes
```

ext4 superblocks:

```
pmOS_boot: block size 4096, blocks 248064, free 229126, used 77,570,048 B
pmOS_root: block size 4096, blocks 1714944, free 1114760, used 2,458,353,664 B
```

The exact commands and outputs are in `host-image-check.txt`; no image was
mounted or modified.

## References

- Android AOSP, *Metadata encryption*: https://source.android.com/docs/security/features/encryption/metadata
- Android AOSP, *File-based encryption*: https://source.android.com/docs/security/features/encryption/file-based
- Android AOSP, *A/B (seamless) system updates*: https://source.android.com/docs/core/ota/ab
- Android AOSP, *Partitions overview*: https://source.android.com/docs/core/architecture/partitions
- Local: `docs/PLAN.md` (UFS/rootfs history; `system_a` deployment), `docs/BRINGUP_GUIDE.md` (storage/loop mapping), `docs/NURA_ROOTFS_BUILD.md` (netboot hook behavior), `docs/TEMPORARY_BOOT.md` (ABL DTBO quarantine).

## Bottom line

- **GO:** test a local rootfs from a USB-C SSD/thumb drive first (zero phone
  writes, reversible). If that confirms the storage bottleneck and the user
  accepts losing the dormant slot A Android, a **GO (conditional)** for a
  full-size image on `system_a` booted with `fastboot boot`.
- **NO-GO:** shrinking/reformatting encrypted `userdata`; a Linux image file
  inside `/data`; a phone-only boot selector in the current state.
- **Preserve:** Android slot B, `userdata`, `metadata`, `persist`, modem,
  calibration and identity partitions. Do not touch `dtbo_b`/`boot_b` while
  Android is on B.
- **Unknowns that gate a write:** exact `system_a` size/contents, `dtbo_a`/AVB
  behavior, USB host enumeration/power, rootfs fstab UUIDs, and whether local
  storage (rather than CPU/encoder) is the camera bottleneck.
