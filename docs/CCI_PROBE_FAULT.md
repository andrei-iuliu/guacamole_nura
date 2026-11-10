# CCI probe fault and rear-camera delivery — root cause record

Date: 2026-11-10. Candidate: `7.0.0-sm8150-rear1`, worktree
`guacamole_nura-workspace/nura-rear-cameras-7-0`.
This record supersedes the trace reading in
`docs/REAR_CAMERA_7_0_BRINGUP.md` §“First hardware boot: CCI completion fault”.

## Verdict

The `cci_probe` oops is a real driver bug: CCI1’s DT enables only
`i2c-bus@1`, so `master[0].irq_complete` is never initialized, while
`cci_reset()` always waits on `master[0]`. Patch 0031 initializes completions
for every master and fixes it.

The fix has **never run on the phone**. The fixed module was built at
12:23:01 and installed into the chroot rootfs image at 12:26:57, but the
port-10001 netboot server kept serving a single stale copy made at
**12:11:12** from the 12:08 install, which contains the pre-fix module. Every
boot after 12:23:46 therefore loaded the pre-fix `i2c_qcom_cci` and produced
the same oops. That is the immediate reason “the camera patch did not work”.

## The crash, exactly

- Photo: `Unable to handle kernel NULL pointer dereference at virtual address
  0000000000000000`; ESR `0x96000044` → EC `0x25` data abort, **WnR=1**,
  FSC `0x04` (level-0 translation fault). `Internal error: Oops: 96000044
  [#1] SMP`, kernel `7.0.0-sm8150-rear1 #2 PREEMPT`, Comm `(udev-worker)`.
- Correct trace reading: `wait_for_common+0x40/0x1b0 →
  wait_for_completion_timeout → cci_probe+0x2e0/0x4c0 [i2c_qcom_cci] →
  … → init_module`. The older `+0x2c4/0x6c8` reading was wrong: `0x2c4` is
  the `str wzr,[x19,#0x480]` store into valid memory; `0x2e0` is the return
  address of the reset-wait `bl`, and `0x4c0` is exactly the pre-fix
  `cci_probe` size. The oops itself therefore proves the pre-fix module ran.
- Faulting instruction: in `wait_for_common+0xa4`,
  `str x22,[x8]` = `WRITE_ONCE(prev->next, new)` from `list_add_tail()`,
  with `prev` = `x->wait.task_list.prev` = NULL. `+0x40` in the trace is
  `lr` (return from `_raw_spin_lock_irq`), not `pc`.
- Mechanism: `devm_kzalloc` zeroes `struct cci`.
  `master[0].irq_complete.wait.task_list.next/prev` are NULL.
  `cci_reset()` calls `reinit_completion()`, which only resets `done`; it
  cannot repair the list head. `wait_for_completion_timeout()` →
  `do_wait_for_common()` → `__prepare_to_swait()` → `list_add_tail()` →
  `__list_add(new, head->prev=NULL, head)` stores through NULL.
- `CONFIG_DEBUG_LIST`, `CONFIG_LIST_HARDENED`, `CONFIG_DEBUG_SPINLOCK` and
  KASAN are all off, so nothing catches the zeroed list head.
- Only CCI1 can crash: the booted DTB gives CCI0 both `i2c-bus@0` and
  `i2c-bus@1` (its master-0 completion is initialized), and CCI1 only
  `i2c-bus@1`. CCI0 probes first. This is structural, independent of probe
  order. The port deleted the reference’s enabled-but-empty `cci1_i2c0`;
  that turned a latent upstream driver bug into a deterministic oops.

## Patch 0031

`0031-i2c-qcom-cci-initialize-all-master-completions.patch` adds

```c
for (i = 0; i < cci->data->num_masters; i++)
    init_completion(&cci->master[i].irq_complete);
```

before the `for_each_available_child_of_node()` loop. The matched data is
`qcom,sm8150-cci` → fallback `qcom,sdm845-cci` → `cci_v2_data`
(`num_masters = 2`). `init_completion()` self-links the swait list; the later
`reinit_completion()` in `cci_reset()` is then safe. The same protection
covers the `cci_run_queue()` timeout path and `cci_halt()`.

This is **not** an upstream backport: current upstream still initializes
completions only for available children and still waits on `master[0]`. The
only nearby upstream fix (`729ac5a4b966`, CVE-2026-53339) guards
`cci_halt()` in `cci_remove()`, not `cci_reset()`. 0031 is the correct local
fix; it should be carried forward with a proper `Fixes:` line and, ideally,
sent upstream.

Verified by rebuilding both variants with the same clang/LLD 23.1.4 flags:
the pre-fix module reproduces the shipped 8920-byte module bit-for-bit
(`cci_probe` `0x4c0`), the 0031 module reproduces the shipped 8950-byte
module (`cci_probe` `0x4ec`). The packaged “fixed” module really is the fixed
source.

## Delivery evidence

| Artifact | Time | Size | SHA-256 | 0031? |
|---|---:|---:|---|---|
| `build/rear-camera/kernel-package/.../i2c-qcom-cci.ko.zst` | 12:23 | 8950 | `4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4` | yes |
| chroot rootfs image `chroot_native/home/pmos/rootfs/oneplus-guacamole.img` | inode mtime 12:23:49, ctime 12:26:57 | 8950 module | `4e464ab7…` (image) | yes |
| served `images_netboot/oneplus-guacamole.img` | inode mtime 12:01:47, ctime 12:08:55; file mtime 12:29:30 = guest writeback | 8920 module | `3707af89…` (image), `6f61b608…` (module) | **no** |
| archived APKs 11:45–12:01 | 12:01 | 8920 | `6f61b608…` (all four) | no |

Only `i2c-qcom-cci.ko.zst` differs between the two rootfs images; all other
846 module files, the kernel Image, all DTBs and the initramfs are
byte-identical. The fixed rootfs image is `4e464ab7…`
(`packaging-inputs.json:rootfs_image_sha256`).

`pmbootstrap/commands/netboot.py:start_nbd_server()` copies
`/home/pmos/rootfs/<dev>.img` to `<work>/images_netboot/<dev>.img` only when
the destination is missing or `--replace` is passed. `scripts/serve_rear_camera.sh`
passes neither. The log has exactly one `NOTE: Copied device image` line, at
12:11:12; the 12:29:14 serve went straight to `nbd-server`. Therefore the
12:29 server served the 12:11:12 file — the logical proof, independent of
image scanning. The 12:29:30 mtime is an NBD client writeback after the guest
mounted the ext4 image read-write, not a copy.

`build/rear-camera/boot.img` (12:27:46, `0e19bbb5…`, 57,393,152 bytes) is
self-consistent: kernel `4e798093…`, ramdisk `47606666…`, DTB wrapper
`3992b3c6…`, header v2/page 4096, DTB address `0x03000000`, cmdline
`pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1`. The initramfs contains no
camera/CAMSS/CCI module, so **only the served rootfs needs replacing**; do not
rebuild the boot image/initramfs/DTB.

## What to do for the next boot

1. Stop the port-10001 server; confirm `ss -ltnp 'sport = :10001'` is empty.
2. Replace the stale cache with the fixed 12:27 rootfs image before booting
   (`--replace` and `-y`, or delete the root-owned cache and let
   `netboot serve` copy it).
3. Verify the served cache is byte-identical to
   `4e464ab7…` and contains the module `4cd35f05…`/8950.
4. Boot the existing `0e19bbb5…` boot image once.
5. On the phone: `uname -r` must be `7.0.0-sm8150-rear1`; the running
   module hash must be `4cd35f05…`; `dmesg | grep -i cci` must show no NULL
   deref. A `CCI reset timeout` instead means the reset-done ACK is masked at
   first probe (see residual risks).
6. If it oopses, photograph the screen before rebooting; preserve the trace.

## Residual risks (after CCI is fixed)

- **Reset ACK vs IRQ mask (driver-level, low–medium):** `cci_probe()` calls
  `cci_reset()` before `cci_init()` writes `CCI_IRQ_MASK_0`. Upstream and
  Qualcomm’s downstream driver use the same order, and CCI0 on this phone
  already passes that reset, so it is likely fine. If `dmesg` shows
  `CCI reset timeout`, program the reset-done mask before reset or poll
  `CCI_IRQ_STATUS_0`.
- **CCI clock rate:** 1 MHz fast-plus timings are calibrated for 37.5 MHz.
  `assigned-clock-rates` sets 37.5 MHz; verify
  `clk_summary | grep -i cci` after boot.
- **`cci_remove()` on CCI1:** with 0031, unbind can halt non-existent master 0
  and log a 100 ms `CCI halt timeout`. Cosmetic; upstream guards this.
- **Streaming-only risks:** CSID `vdda-phy`/`vdda-pll` are dummy regulators
  (probe warns, first stream may fail); MCLK is 19.2 MHz for IMX586/IMX481
  while the stock overlay asks 24 MHz; tele VDDD is 1.056 V vs stock 1.2 V;
  the IMX586 C-PHY path is first-time.
- **DSI/display errors in the photo are secondary:** they appear ~11 s after
  the CCI oops from `drm_crtc_wait_one_vblank`; the display/`dispcc` DT
  subtrees are unchanged. No camera-patch causality found.

## Source reports

The full swarm evidence is under
`.work/investigation/reports/{A..F}.md` (artifact provenance, crash
disassembly, CCI DT/binding review, pipeline remediation, camera blockers,
upstream check). Delivery scripts/runbook are described in
`docs/CAMERA_DELIVERY.md`.
