# Nura rc4 (source candidate): 7.0.0-sm8150-guacamole-rc4

**Status: source candidate only. Nothing here is built, packaged, booted or
tested on hardware by this work, and it must not be described as fixed or
boot-ready until the full build and the parent's review are done.**

This document describes what the rc4 source candidate changes relative to the
booted rc3 (`7.0.0-sm8150-rc3`) and what still has to be verified on the phone.
The integration lives in `flows/rc4/` (see `flows/rc4/README.md` for the exact
build commands and `flows/rc4/REVIEW.md` for the review record).

GPU follow-up for the next RC: [measured faults, symbolized Phoc crash and
candidate fixes](NURA_GPU_FAULTS.md). The 2026-11-14 research is a proposal;
its GPU candidates are not integrated or hardware-validated fixes.

## 0. rc4b: hardware results and corrections (2026-11-13)

rc4 was built and RAM-booted (`fastboot boot`, nothing flashed) on the phone
with the local-userdata rootfs. Three findings changed the recipe; the result
is **rc4b** (APKBUILD `pkgrel=6`, same `uname -r` `7.0.0-sm8150-guacamole-rc4`).

| Finding on hardware | Evidence | rc4b change |
| --- | --- | --- |
| Boots, panel stays black | The only display difference vs the rc3 DTB is `displayport-controller@ae90000` `okay`; the same image with that node `disabled` showed the panel. **But the cause was `0036` (next row), not DP:** rc4b (without `0036`) with DP `okay` binds DSI *and* DP (`bound ae94000.dsi`, `bound ae90000.displayport-controller`), panel up, `devices_deferred` empty | none: `0057` stays as supplied (`mdss_dp` `okay`). A temporary `disabled` edit was tested and reverted |
| Hard hang on unlock (screen unblank) | `dsi0_phy_pll_out_dsiclk: Zero divisor` (WARN `clk-divider.c:147`, kwin atomic commit) → `Failed to set rate pixel clk, -22` → panel init `-22` → DPU IRQ storm (`no registered cb`) → hang. The PLL divider read 0, consistent with the PHY register clock/power being off | `0036-dsi-phy-pm` **removed** (kept in `flows/rc4-reverted/`). rc4b: 3 lock/unlock cycles clean, 0 DSI errors, ~3 h 40 min stable |
| DisplayPort to a dock/monitor | DP itself works: DP alt mode `ff01` entered, AUX/EDID (3440x1440 monitor, DPCD 1.4), link training at HBR2 ×2 succeeds, 3440×1440@60 24 bpp stream starts (`mainlink READY`). Then the **USB-PD session** drops: powered docks request `PR_SWAP` and the phone never receives the dock's `PS_RDY` → `ERROR_RECOVERY`/`PORT_RESET`; with `preferred_role=sink` the phone misses `Source_Capabilities`/`PS_RDY` → hard-reset loop; an unpowered HDMI adapter sticks in `SRC_ATTACH_WAIT` (tcpm requires `vbus_vsafe0v`, `tcpm.c:4821`). Suspects: pdphy RX in sink role and the nura VBUS-detection patches `0021`/`0023`/`0024` | none yet: USB-PD (`qcom_pmic_typec`) work for rc5 |
| Microphone captures exact zeros on some boots | Full WCD934x DAPM path on, MCLK hold did not help, `SLIM TX0` list not stale (toggle test); codec TX0 only `Port Closed … value 4`, never the healthy `underflow … value 6`. Boot logs `QMI wait timeout` | `0059` (new): omarchy's 5 s QMI wait. **Not a proven fix**: the timeout still appears after 5 s on rc4b; the mic worked on the first rc4b boot, but the failure is intermittent |

Notes:

* Evidence (logs, DAPM snapshot, MCLK test): `flows/rc4/evidence-rc4b-20261113/`.
* Rebuilding the kernel invalidates **every** module on the rootfs (BTF:
  `failed to validate module [...] BTF: -22`) even with an identical release
  string. The rc4b module tree must be installed with the rc4b Image; the
  phone keeps the rc4 set in `/usr/lib/modules/rc4-original-modules`.
* Rollback inputs: `flows/rc4/build-source/kernel-output.rc4-original/`,
  `flows/rc4-recipe-backup-20261113/`,
  `build-source/kernel-state/linux-v7.0.0-sm8150.rc4-original/`.
* **rc4d** (`pkgrel=7`) adds `0060` (pdphy: always hand the RX buffer back,
  warn on dropped frames) and `0061` (0021's vSafe0V VBUS fallback only while
  presenting Rd). Hardware: with a powered dock the `PR_SWAP` now completes
  (PS_RDY received, phone sinks from the dock) and the USB/DP session held
  113 s until unplug (`evidence-rc4b-20261113/rc4d-dock-pd-success.log`). The
  new `invalid size` warning did not fire, so which `0060` path mattered is
  unconfirmed. Still open: that dock's EDID read fails (DP falls back to
  640x480), kwin "external only" hits `Atomic commit failed: EBUSY`, and an
  unpowered USB-C adapter (`CC1=Rd, CC2=Ra`) loops `SRC_ATTACH_WAIT` →
  `Received hard reset` → `ERROR_RECOVERY`.
* **rc4h** (`pkgrel=11`) adds, each from a dock-test log
  (`evidence-rc4b-20261113/rc4e|rc4g|rc4h-dock-pd.log`):
  * `0062` tcpm: reset `hard_reset_count` on every unattach (a VBUS-first
    detach kept the PC's exhausted budget, so the next partner never got PD);
  * `0063` qcom-pmic-typec: re-read VBUS when leaving the sink role (stale
    "VBUS on" kept TCPM in `SRC_ATTACH_WAIT`);
  * `0064` drm/msm/dp: use the real pixel rate for the link bandwidth check
    (wide bus halved it, so 3440x1440@100 passed on 2 lanes and stayed black;
    @60 remains);
  * `0065` qcom-pmic-typec: hide the PM8150B re-attach glitch for 500 ms
    after an Rp<->Rd flip (power role swap), as downstream's
    `pr_swap_in_progress` does;
  * `0066` tcpm: tolerate one data role mismatch per attach (USB-C dock sends a
    wrong DFP bit after a swap);
  * `0067` drm/msm/dp: re-apply and if needed re-read the EDID in get_modes
    (JSAUX fell back to 640x480).
  Hardware (rc4h): JSAUX dock + 3440x1440 works twice at 60 Hz, unpowered
  Cypress USB-C→HDMI adapter works, USB through the docks works. **Open:**
  the USB-C dock never delivers a PD message while it is the source (no
  Source_Capabilities on a sink attach, no PS_RDY after a PR_SWAP, and the
  PM8150B never re-attaches as sink within 500 ms), so it only worked on the
  rare runs where the swap completed (rc4b/rc4d/rc4e). The keyboard-adapter-
  on-phone case for `0063` is still untested.
* **rc4i** (`pkgrel=12`) adds four drm/msm recovery backports for the recurring
  `gpu fault: ... type=TRANSLATION source=CCU` + `hangcheck recover!` on both
  Plasma and Phosh, in upstream order, each rebased onto this tree and applied
  with `patch -p1 --fuzz=0` (no offsets):
  * `0068` `4625fe5bbdac` (v7.1) forbids memory reclaim from the reset path —
    the recover worker holds `gpu->lock` while `msm_gpu_crashstate_capture()`
    allocates, and reclaim there waits on the DMA fences of the GPU that is
    holding the lock, so one hang can wedge the GPU permanently;
  * `0069` `01a0d6cd7032` (v7.1) always recovers — the old `msm_gpu_active()`
    early-out skipped the reset whenever the hung ring had no further
    submits, which is the single-submit case a compositor hits at startup;
  * `0070` `b303e1d52811` (v7.3) recovers the HW before retiring the hung
    submit — retiring first frees its BOs while the GPU may still be reading
    them, which is itself a source of `TRANSLATION` faults;
  * `0071` `40b793714ad8` (v7.3) adds the missing `put_task_struct()` in the
    `recover_worker()` else branch (one leaked task reference per recovery).
  `0072` (device-dt) gives the PM8150 PON node `mode-recovery`/`mode-bootloader`,
  so `systemctl reboot --reboot-argument=bootloader` reaches the ABL instead
  of doing a warm reset; both properties are present under `pon@800` in the
  compiled `sm8150-oneplus-guacamole.dtb`. `CONFIG_DRM_MSM=y`, so the Image
  and its BTF change while `uname -r` stays `7.0.0-sm8150-guacamole-rc4`:
  reinstall the module tree with the new Image. **Not tested on hardware**
  yet: these are the explanation for the fault pattern, not a proven fix.
* **rc4i GPU crash capture** (device package, `pkgrel=17`, enabled by default).
  On a hang drm/msm publishes `/sys/class/devcoredump/devcd<N>/`, and that
  dump is the only input Mesa's `crashdec` can decode. `nura-gpu-crash-capture`
  copies `data` plus a 200-line `dmesg` tail into
  `/var/lib/nura/gpu-crash/<utc-stamp>-<devcd>/` (`devcoredump`, `dmesg.txt`,
  `INFO`) and then writes `1` to `data` to release it. Releasing is what keeps
  the feature useful: the framework holds one unread dump per device, deletes
  it after 5 minutes, and **discards** a new hang while an unread one exists.
  `90-nura-gpu-crash.rules` starts the oneshot on the devcoredump uevent and
  `nura-gpu-crash-capture.timer` (enabled by `83-nura-gpu-crash.preset`) is a
  one-minute backstop. Retention: newest 10 captures, at most ~50 MB in total,
  8 MiB per dump. No debugfs, no extra package. Decode a capture on a dev box
  with Mesa's crashdec: `crashdec -f /var/lib/nura/gpu-crash/<dir>/devcoredump`
  (it prints the GPU, ring and IB state, then replays the command stream up to
  the fault); the same directory's `dmesg.txt` holds the kernel's
  `hangcheck`/`gpu fault` lines for the timestamp.
* `fastboot boot` after `systemctl reboot --reboot-argument=bootloader` gave a
  black, USB-less boot on rc4c/rc4g/rc4h and worked on rc4d/rc4e; the same
  images always booted after a full power-off. Independent of the patches.
* `drm.debug=0x106` (runtime) plus `console=tty0` hung the phone on screen
  lock (no oops, ramoops empty); without it lock/unlock is clean. Do not
  combine DRM debug with lock tests.
* After such hangs the first boot can show colour garbage on the panel; a
  lock/unlock (panel re-init) clears it.
* `CONFIG_DYNAMIC_DEBUG=y` made this kernel hang before the display came up
  (rc4c, reproducible from a cold start); it stays off.
* After a hung boot or dock session a warm reset was not always enough; a full
  power-off restored booting.
* rc4b DTB was regenerated in place after re-enabling DP (DT-only change);
  the kernel `Image` is unchanged, so the installed rc4b modules stay valid.
* Also seen while docking: WCD934x headset detection WARNs
  `ln_bb_clk2 already disabled/unprepared` from `__wcd934x_cdc_mclk_enable`
  (MCLK refcount imbalance in the MBHC micbias path).
* Still open: USB-PD with docks (above), the mic's intermittent mechanism,
  GPU hangcheck in `plasmashell` at boot, Bluetooth `Frame reassembly failed`.

## 1. What is in the candidate

| Area | Change | Default state in rc4 |
| --- | --- | --- |
| Kernel identity | `CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"` → `uname -r` = `7.0.0-sm8150-guacamole-rc4` | — |
| Suspend fixes | 0044 `guacamole_mss_vote` (MSS rail release) and 0045 ath10k SNOC key ack; 0042/0043 were already rc3 0034/0035 | module built; release unit installed but **not enabled**; no mpss-start hook |
| Suspend policy | untouched | automatic suspend stays **disabled** (logind/PowerDevil) |
| DisplayPort | 0057/0058 devicetree (FSA4480 + DP controller + SBU), 0046-0050 ASoC plumbing | DP video **enabled** (candidate), DP audio DAI link **`status="disabled"`** |
| Fingerprint | 0051 goodix_fp driver (repaired), 0055 devicetree | driver built; DT node **`disabled`**; `goodix_fp.allow_power` defaults to **false**; QCOMTEE built but blacklisted |
| Pop-up camera | 0052 MXM1120 Hall sensors, 0053 IMX471, 0054 motor/PM domain, 0056 devicetree | Hall sensors and LPG described; motor node **`disabled`**; front sensor node and CAMSS CSIPHY2 endpoint **not emitted** |
| QCOMTEE / gate 0 | `CONFIG_QCOMTEE=m` | **`module_blacklist=qcomtee`** on the boot cmdline: the module cannot be autoloaded *or* explicitly loaded |
| Rear cameras | unchanged from rc3 | three bindable CAMSS connections, all rear sensors and lenses as in rc3 |
| Audio | unchanged except the disabled DP link | 11 available sound links, exactly as rc3 |

Patch numbers: rc3 keeps `0001-0041` byte-for-byte; the rc4 additions are
`0044-0058` (`0042`/`0043` are the rc3 equivalents of the two suspend patches
and are **not** reapplied). The full map, including the two DisplayPort
devicetree patches that deliberately carry numbers outside the DP block
(`0057`/`0058`), is in `flows/rc4/README.md`.

## 2. Corrections applied to the supplied material

The input trees under `bringup-notes/Patches` were
written against other baselines (6.17, other kernel trees, other package
versions). These are the corrections the rc4 integration applies; each topic
README has the full claim/evidence table.

* **suspend-fixes**: 0025/0026 are already in rc3 (as 0034/0035) and were not
  reapplied. 0027/0028 became 0044/0045. 0044 keeps its logic but prints why it
  declines; 0045 gains a `running_fw` guard and latches after a timeout instead
  of failing every later key operation with `-EIO`. The supplied APKBUILD and
  device diffs target 6.17 and are replaced. The MSS release is **opt-in**: the
  unit is installed but not in the preset, and there is no automatic hook,
  because `state=running` does not prove that the proxy MSS power domains were
  handed over (topic README §6).
* **displayport**: the two supplied devicetree diffs edit the flattened 6.17
  tree and cannot apply to 7.0; they were rewritten as
  `sm8150-oneplus-guacamole-displayport.dtsi` (included from the board delta),
  modelled on `sm8150-hdk.dts`. The audio DAI link is `status="disabled"`
  because an enabled link whose DP codec never registers would defer the whole
  sound card (no speakers, no mic). 0046 is a consistency fix, not the fix for
  `-110`; 0048 is the only structural hypothesis and is inert while the link is
  disabled.
* **fingerprint**: 0034 was rebased and repaired (ioctl validation, IRQ
  deadlock, file lifetime, netlink portid) and became 0051; the DT node became
  0055 with `status="disabled"`. Gate 0's "passive by configuration" claim was
  corrected: `qcomtee` has a platform modalias and would be autoloaded, hence
  the kernel cmdline blacklist. `QCOMTEE` does not switch the TZMEM allocator on
  this baseline (SHM-bridge is already set).
* **popup-camera**: 0035/0036/0037 became 0052/0053/0054 (two Kconfig/Makefile
  hunks moved past 7.0/rc3 entries; no driver code changed); the overlay became
  0056, a static dtsi. Two claims were contradicted while integrating: the IMX471
  is on CCI1 **master 1** (stock `cci-master = <1>`), and a CAMSS endpoint whose
  remote sensor is disabled stalls `camss_subdev_notifier_complete()` and
  removes **all** camera video nodes. The fragment therefore emits the sensor
  node and the CAMSS endpoint only together, behind
  `-DCAMERA_POPUP_SENSOR`, and the shipped DTB has neither.

## 3. Deliberate gates

These are properties of the candidate, checked rootlessly
(`flows/rc4/scripts/rc4.sh check`):

1. **MSS release**: `/usr/libexec/guacamole-mss-release` and
   `guacamole-mss-release.service` are installed; the unit is **not** in
   `80-guacamole.preset`; `guacamole-mpss-start` has no hook. Enabling it on the
   phone is the opt-in:
   `systemctl enable --now guacamole-mss-release.service`.
2. **Suspend**: automatic suspend remains disabled in the rootfs policy
   (`HandleSuspendKey=ignore`, `AutoSuspendAction=0`). 0042/0043 (already in
   rc3) would only run if a suspend actually happened.
3. **DisplayPort**: video enabled; `dp-dai-link` **disabled**. Do not ship the
   `status="okay"` experiment as a release image.
4. **Fingerprint**: DT node `disabled`; the driver refuses
   `GF_IOC_ENABLE_POWER`/`GF_IOC_RESET` unless loaded with
   `goodix_fp.allow_power=1`.
5. **Pop-up camera**: motor node `disabled`; the front sensor and the CAMSS
   CSIPHY2 endpoint are absent together by default. The motor must not be
   enabled before the Hall thresholds of this unit are measured
   (`flows/rc4/topics/popup-camera/docs/motor-procedure.md`).
6. **QCOMTEE**: built as a module so gate 0 can be tested, but
   `module_blacklist=qcomtee` is on the boot cmdline. That parameter is enforced
   in `early_mod_check()` (`kernel/module/main.c`) for every load, not just
   autoload, so a `modprobe qcomtee` will fail with "Module qcomtee is
   blacklisted". A deliberate gate-0 test therefore needs a boot image without
   that parameter; a modprobe.d file would not be enough.

## 4. What is *not* claimed

* rc4 is a **source candidate**. No kernel package, rootfs, boot image or APK
  was built from it here, and no part of it has been booted.
* The lock-screen crash the user saw after a successful local boot is **not
  explained** by this candidate. Suspend/resume is a hypothesis (and the rootfs
  policy does not suspend automatically), so the honest statement is: the crash
  is reproducible on the booted local image, the cause is unknown, and none of
  these patches is claimed to fix it.
* The DisplayPort video path has never been booted. It compiles and mirrors an
  upstream board; that is all.
* DisplayPort audio does not work as far as anyone knows. The shipped default
  makes sure it cannot break the working audio card.
* The pop-up camera cannot move: the motor node is disabled and the sensor is
  not in the default DTB.
* The fingerprint sensor cannot authenticate. This is power/reset/IRQ
  groundwork plus a gated probe path, nothing more.
* QCOMTEE/QTEE behaviour on SM8150 is unverified: mainline uses
  `qcom_scm` fnid `0x0602` while stock uses `0x32000600` (cmd `0x00`), and the
  SM8150 firmware may or may not accept both.

## 5. Hardware tests still required (after a full build and review)

Run these in this order; each one has a "stop" condition. Nothing here is a
substitute for the parent's review of the built artifacts.

1. **Boot and identify**: `uname -r` must be `7.0.0-sm8150-guacamole-rc4`;
   `dmesg | grep -iE 'qcomtee|blacklist'` must show no qcomtee load.
2. **Regression sweep on the default image** (no opt-ins): three rear cameras
   (`cam`/libcamera), speakers, internal mic, haptics, alert slider, modem
   (`mmcli -m any`), clock, boot diagnostics. Any regression here stops the
   trial before the optional features are touched.
3. **QCOMTEE gate 0 (passive)**: `lsmod | grep qcomtee` must be empty. Only for
   the deliberate probe, rebuild the boot image without the blacklist and load
   it by hand; expect `-EIO` or a feature query, not authentication.
4. **DisplayPort video**: dock with a monitor, check
   `/sys/class/typec/port0-partner/*/svid` = `ff01`, `card*-DP-1/status` =
   `connected`, and 60 Hz output (two lanes; 120 Hz is unverified). Then unplug
   and confirm USB gadget SSH and the speakers still work.
5. **MSS release (opt-in)**: enable `guacamole-mss-release.service`, expect
   `proxy handover observed` and `mss.lvl corner 0`, then 5-10 minutes
   screen-off with the modem online. Do **not** unload the module with the modem
   running (it re-votes corner 9). Rollback: `systemctl disable --now` and reboot.
6. **Fingerprint gate 1 (opt-in, doubly gated)**: flip the DT node to `okay`
   *and* load `goodix_fp` with `allow_power=1`; only a reset-time interrupt is
   meaningful. Authentication is out of scope (no HAL, no TrustZone transport).
7. **Pop-up camera**: measure the Hall values first
   (`flows/rc4/topics/popup-camera/docs/motor-procedure.md`), then a bounded
   pulse, then courses. Never with the sensor enabled and the motor disabled.
8. **The lock-screen crash**: reproduce it with `dmesg -w` attached, record
   `/sys/class/remoteproc/*/{name,state}`, `/sys/power/suspend_stats/*` and the
   last frames before the freeze. That log is the only way to turn the
   suspend/resume hypothesis into a diagnosis.

## 6. Next user-run command

```bash
# 0. read-only verification of the source candidate (writes nothing)
bash flows/rc4/scripts/rc4.sh check

# 0b. executable proof of the kernel -> rootfs contract (fake builder, rootless)
bash flows/rc4/scripts/rc4.sh contract

# 1. build the rc4 kernel package (rootless, isolated under flows/rc4)
#    leaves build-source/kernel-state/output, kernel-output/kernel-package,
#    kernel-output/Image and kernel-output/kernel-inputs.json
bash flows/rc4/scripts/rc4.sh kernel

# 2. after reviewing the kernel output, build the local-rootfs image
bash flows/rc4/scripts/rc4.sh rootfs --authorized-key /absolute/path/to/key.pub

# 3. repack the boot image only (optional, after a rootfs rebuild)
bash flows/rc4/scripts/rc4.sh bootimg
```

**UI variants.** Step 2 takes `--ui plasma|phosh` (default `plasma`). Both
images use the same rc4i kernel (pkgrel 12) and device package 4-r17, which
adds:

* the A/B slot marker `guacamole-boot-slot.timer`;
* the GPU crash collector `nura-gpu-crash-capture.timer` + udev rule
  (`/var/lib/nura/gpu-crash`, see section 0);
* the Plasma corner preset;
* exact kernel/device/firmware pins in `/etc/apk/world`;
* a graphical package manager (Discover with its apk backend on Plasma,
  GNOME Software with its apk plugin on Phosh);
* the PulseAudio backend.

Phosh state and output go to `*-phosh` directories. See `docs/NURA_PHOSH.md`
for the build and install commands and for the fallback to Plasma:

```bash
bash flows/rc4/scripts/rc4.sh rootfs --ui phosh --authorized-key /absolute/path/to/key.pub
```

None of these commands flashes, boots, mounts, repartitions or restarts
anything, and the userdata upgrade path is deliberately not automated: the
user already has a local Linux on `userdata`, and the delivery method for the
candidate is the parent's decision after reviewing the actual build.
