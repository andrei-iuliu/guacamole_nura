# OnePlus 7 Pro (guacamole): fixes for crashes in standby

Proposed changes to [guacamole_nura](https://github.com/emilyastranova/guacamole_nura) for the phone crashing when it goes into standby (screen off / s2idle). The fixes are taken from [omarchy-mobile](https://github.com/Cube-Oakley/omarchy-mobile), which runs the same `sm8150` kernel line on the same phone and documented each failure.

**Status: proposal. Nothing here has been built or run on a phone.** Each patch was checked with `git am` against the real kernel sources (see "What was verified"). Start with [docs/00-diagnose.md](docs/00-diagnose.md) so the fixes are matched to the failure you actually see.

## Pinned sources

| Repo | Commit | Short name in the docs |
| --- | --- | --- |
| guacamole_nura | `5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24` | `GN` |
| omarchy-mobile | `f702f688288e59e3566f113d411ac7d18e4d0201` | `OM` |
| kernel, current | `gitlab.com/sm8150-mainline/linux` tag `v6.17.0-sm8150` | 6.17 |
| kernel, next | `gitlab.postmarketos.org/soc/qualcomm-sm8150/linux` tag `v7.0.0-sm8150` | 7.0 |

`OM` paths are under `devices/oneplus7pro/`. The `OM` repo has no LICENSE file. The kernel code is GPL-2.0 like the files it patches, and `mss_vote.c` carries `SPDX-License-Identifier: GPL-2.0-only`. Patch authorship is credited to `Cube-Oakley`.

## What is in this folder

```
README.md                      this file: handoff, order, checklist
docs/00-diagnose.md            find which failure you have (pm_test, recovery, WoWLAN)
docs/0025-fastrpc-freezable-wait.md
docs/0026-s6sy761-resume-irq.md
docs/0027-guacamole-mss-vote.md
docs/0028-ath10k-snoc-wmi-key-ack.md
kernel/0025..0028-*.patch      git format-patch, -p1, on top of GN's 0001..0024
kernel/APKBUILD.diff           adds 0025..0028 to linux-postmarketos-qcom-sm8150, pkgrel 26
kernel/config.diff             CONFIG_PM_DEBUG (+PM_SLEEP_DEBUG), CONFIG_QCOM_GUACAMOLE_MSS_VOTE=m
device/guacamole-mss-release   /usr/libexec script for 0027 (waits for modem, writes level 0)
device/guacamole-mss-release.service   systemd unit for it
device/device-package.diff     device-oneplus-guacamole: installs the above, preset, OpenRC hook
device/90-guacamole-modem-recovery.rules   OPTIONAL: disable modem SSR (diagnostic/mitigation)
```

## The fixes, most likely cause first

| # | Fixes | Symptom | Severity | Docs |
| --- | --- | --- | --- | --- |
| 0027 | AP keeps voting the modem rail (`mss.lvl`) at the top corner. The modem's QLink asserts after the radio idles, and resume re-sends the vote | modem watchdog after screen-off; whole phone **freezes** at `pm_test=platform`; with modem recovery on, kernel dies → reboot | **crash** | [0027](docs/0027-guacamole-mss-vote.md) |
| 0025 | FastRPC invoke restarted after the task freezer → sensor DSP gets a duplicate call | SLPI crashes 6 ms after every resume; sensors dead until reboot | **crash** (DSP) | [0025](docs/0025-fastrpc-freezable-wait.md) |
| 0026 | s6sy761 IRQ enabled before the controller restart finishes | touchscreen `-ENODEV` on resume; phone "looks dead" | looks like a crash | [0026](docs/0026-s6sy761-resume-irq.md) |
| 0028 | ath10k waits 3 s for an HTT event WCN3990 never sends on key removal | `-110` timeout, ~3 s extra on every suspend/disconnect | slow, not a crash | [0028](docs/0028-ath10k-snoc-wmi-key-ack.md) |

Not patched, but must be tested (see 00-diagnose): **WoWLAN**. GN arms WoWLAN by default (`90-ath10k-wowlan.rules`, `90-wifi-wowlan.conf`, `10-wifi-wowlan`), but its own README says "WoWLAN pending" and OM never used WoWLAN. That is an untested suspend path that GN has and OM doesn't.

## Integration steps (for the agent)

Work in a branch of guacamole_nura. Follow GN's `CLAUDE.md`, in particular: the pmaports copy of `linux-postmarketos-qcom-sm8150` is a **separate copy**. After editing the repo, copy the files there, run `pmbootstrap checksum`, and check that both directories are identical.

1. Copy `kernel/0025..0028-*.patch` into `device/testing/linux-postmarketos-qcom-sm8150/`.
2. From the repo root: `patch -p1 < suspend-fixes/kernel/APKBUILD.diff` and `patch -p1 < suspend-fixes/kernel/config.diff`.
3. Copy `device/guacamole-mss-release` (mode 755) and `device/guacamole-mss-release.service` into `device/testing/device-oneplus-guacamole/`, then `patch -p1 < suspend-fixes/device/device-package.diff`.
4. `pmbootstrap checksum linux-postmarketos-qcom-sm8150 device-oneplus-guacamole` (the diffs do not touch `sha512sums`).
5. Build: `pmbootstrap build linux-postmarketos-qcom-sm8150 device-oneplus-guacamole`, then `scripts/build_bootimg.sh` and flash as usual (`scripts/flash_boot.sh`).
6. Optional, for diagnosis only: install `device/90-guacamole-modem-recovery.rules` to `/usr/lib/udev/rules.d/` (see 00-diagnose).
7. Update GN docs: the patch list in `CLAUDE.md` (line 42), `docs/PORTING.md` patch table, `docs/PLAN.md` (a "Suspend" item), and README's Wi-Fi row once WoWLAN is tested.

The patches are independent. 0026 needs GN's `0005` (it applies on top of it); everything else depends only on the upstream tree. If you want to land them one at a time, use the order 0025 → 0026 → 0027 → 0028. 0025 and 0026 are the smallest and lowest-risk.

## Moving to the 7.0 kernel

The user reports that guacamole already boots on the postmarketOS **7.0.0** kernel (`v7.0.0-sm8150`). GN's `docs/PORTING.md` ("Moving to a newer kernel") describes that move. For these patches:

- All four apply cleanly with `git am` to the 7.0 files after GN's `0005` and `0017`.
- 7.0 still has every bug they fix:
  - `rpmhpd.c` still clamps to the top corner until `sync_state`.
  - `fastrpc.c` still uses `wait_for_completion_interruptible()`.
  - The ath10k and s6sy761 code they touch is unchanged.
- 7.0 still has what 0027 relies on:
  - `qcom_q6v5_pas.c` still names the rproc `"modem"`, which the script looks up.
  - The `sm8150.dtsi` MPSS node still has `power-domain-names = "cx", "mss"`.
  - `sm8150-oneplus-guacamole.dts` still has `compatible = "oneplus,guacamole"`, which the module checks.
- On stock 7.0, `sm8150-oneplus-common.dtsi` sets `&remoteproc_slpi { status = "disabled"; }`. 0025 only matters once GN's SLPI and sensor enablement is carried to 7.0, but it is harmless before that.
- `kernel/config.diff` is against GN's 6.17 config. On 7.0, add the same symbols to pmaports' 7.0 config instead of applying the diff.
- `kernel/APKBUILD.diff` is against GN's 6.17 APKBUILD. On 7.0, add the four patch names to that APKBUILD's `source=` by hand.

## What was verified (and what was not)

Verified:
- The patch series applies with `git am` on both trees, after GN's `0005` and `0017` (the only GN patches that touch the same files):
  - 6.17: `gitlab.com/sm8150-mainline/linux` `v6.17.0-sm8150`
  - 7.0: `gitlab.postmarketos.org/soc/qualcomm-sm8150/linux` `v7.0.0-sm8150`
- The APKBUILD, config and device-package diffs apply with `patch -p1 --dry-run` to GN at `5b2241b`.
- `guacamole-mss-release` passes `sh -n`.
- The kernel symbols and DT names the patches use exist in both trees:
  - functions and constants: `GENPD_NOTIFY_ON`, `dev_pm_genpd_add_notifier`, `WMI_TLV_TAG_STRUCT_VDEV_INSTALL_KEY_COMPLETE_EVENT`
  - compatibles: `qcom,sm8150-rpmhpd`, `qcom,sm8150-mpss-pas`
  - the `"mss"` power-domain name

Not verified:
- **Not compiled.** No kernel build was run here. Build first, and fix anything the compiler reports.
- Not run on a phone. OM ran the original versions of 0025–0028 on guacamole with its own 6.17 build, which is different from GN's tree and config.
- 0027 was validated in OM as an out-of-tree module (`mss_vote.ko`). Here it is turned into an in-tree driver (`guacamole_mss_vote`): renamed, with Kconfig and Makefile entries added and the code unchanged.

## Hardware test checklist

Before each test, record `cat /sys/power/suspend_stats/*`, `dmesg -w` over SSH (USB networking at 172.16.42.1), and `cat /sys/class/remoteproc/*/state`.

1. Boot. Check that `dmesg | grep mss_vote` shows `ready` and `active corner 0: 0`, and that `/sys/module/guacamole_mss_vote/parameters/level` reads 0.
2. Screen off, no suspend, for 5 minutes with a SIM and the modem online. Expect no `rflm_qlnk` assertion and the modem still `running`. This tests 0027 without suspend.
3. `echo freezer > /sys/power/pm_test; echo mem > /sys/power/state` (s2idle). Expect the SLPI to stay running and `monitor-sensor` to still report. This tests 0025.
4. `echo devices`, then `echo platform`, into `pm_test`, then suspend. Expect no freeze, and `parameters/reasserted` to go up by 2 per cycle. This tests 0027.
5. `echo none > /sys/power/pm_test`, then a real suspend for 60 s with an RTC alarm (`rtcwake -m mem -s 60`; needs `rtc-pm8xxx`, which GN builds as a module). Check that touch works after wake (0026) and look for an `ath10k` `-110` in dmesg (0028).
6. Repeat step 5 with WoWLAN armed (GN default) and disarmed (`iw phy0 wowlan disable`), and compare.
7. Run 20 cycles of the Phosh/userspace standby path, unplugged.
