# rc4 topic: suspend-fixes

Source-ready integration of `bringup-notes/Patches/suspend-fixes`
for the nura **rc4** kernel.  Sections 1-11 record the source review, a
no-fuzz apply against the actual rc3-patched 7.0 tree, a clang syntax check of
the touched objects against that tree's configured build, and rootless unit
tests of the userspace helper.  The first hardware results are in section 0.

Two of the four supplied patches are **already in rc3** (0034, 0035); the two
that are new are renumbered **0044** (MSS rail vote) and **0045** (ath10k key
ack).  System suspend stays disabled: installing these does not enable it.

---

## 0. Hardware results (2026-11-19)

Setup: rc4n image on `boot_b`, rc4l rootfs, USB connected and charging,
WoWLAN armed, remoteproc recovery enabled. The 0044 module was loaded from
`/tmp` for the test, without installing it:
`GUACAMOLE_MSS_MODPROBE=insmod GUACAMOLE_MSS_MODULE=/tmp/guacamole_mss_vote.ko /usr/libexec/guacamole-mss-release`.

* **0044 as first integrated never loaded.** Its
  `of_machine_is_compatible("oneplus,guacamole")` check fails because the
  ABL-quarantine wrapper presents the root compatible `qcom,sm8150-mtp`.
  rc4o (`pkgrel=17`) also accepts that root when the board's
  `samsung,oneplus-dsc` panel node is present.
* **`pm_test` freezer:** pass.
* **`pm_test` devices:** pass, and no ath10k `-110` key-removal timeout
  (0045 not needed so far). On resume both CCI controllers failed to enable
  `camnoc_axi` (`cam_cc_camnoc_axi_clk status stuck at 'off'`, `-16`, from
  `cci_resume()`). Not seen in later cycles; the camera after a resume is
  untested.
* **Without the release,** the modem crashed once on its own at about
  1230 s of uptime (watchdog, SFR `Init: wdog or kernel error suspected`),
  a few seconds after the screen woke and Bluetooth was set up again. SSR
  recovered the modem and Wi-Fi, and the kernel survived. After SSR the proxy
  MSS power domain stayed `active` (no handover), so
  `guacamole-mss-release` correctly refuses to run. Run it early after boot.
* **With `mss.lvl` at corner 0:** `pm_test` platform passed, the stage that
  froze the reference phone. The module re-sent corner 0 whenever the MSS
  domain switched off or on.
* **Real s2idle:** `rtcwake -m mem -s 20` slept 20.8 s (CLOCK_BOOTTIME minus
  CLOCK_MONOTONIC) and woke on the PM8150 RTC. No modem crash after resume;
  Wi-Fi reconnected, Phosh was up, and unlocking with touch worked.

**rc4o installed (same day).** The rc4o kernel package (fixed 0044) and
`device-oneplus-guacamole-4-r21` were installed with `apk add` on the
existing rootfs; `boot_b` was not reflashed, because only a module changed.
At boot the unit released `mss.lvl` to corner 0 about 15 s after power-on,
with no manual step. Then:

* `rtcwake -m mem -s 30` slept 30.6 s and resumed cleanly (modem, SLPI and
  ADSP running, Wi-Fi reconnected, Phosh up, no CCI clock errors).
* `echo mem > /sys/power/state` slept about 35 s and woke on the Power
  button.
* On battery (USB unplugged, SSH over Wi-Fi), `rtcwake -m mem -s 600`
  slept the full 600 s, woke on the RTC, and resumed cleanly. Wi-Fi stayed
  associated through the sleep (no NetworkManager state change), the modem
  did not crash, and the gauge moved from 71 % to 70 % over about 16 minutes
  including the awake time.
* The first warm reboot after installing the packages hung with a black
  screen and no USB enumeration, and left no journal; a forced reset and
  `fastboot reboot` booted normally. The same symptom was seen once on rc4l.
  It happens before the rootfs is mounted, so the package change is not the
  suspect, but warm reboots need watching.
* **ath10k key timeouts after resume (2026-11-22).** Right after a resume
  the driver logged `ath10k_snoc: failed to install key ... -110`, then
  `failed to remove key ... -110`. This is the first time either was seen,
  and it happened on a kernel that should carry 0045. Wi-Fi recovered by
  itself within seconds; nothing stayed broken. 0045 doesn't cover installs:
  they still wait for HTT SEC_IND. The removal timeout fits the 0045 latch:
  the install's WMI timeout latches the sequence, so the following removal
  falls back to the old HTT-only 3 s wait (§5, ath10k desync). The running
  kernel and package version weren't recorded; confirm them and look for a
  WMI `install key complete` line on the next occurrence.

Still open: the camera after resume and the intermittent warm-reboot hang. Automatic suspend stays off.

## 1. Baseline actually used

| Item | Path | State |
| --- | --- | --- |
| Kernel recipe | `device/testing/rc3/linux-postmarketos-qcom-sm8150/` | `pkgver=7.0.0`, `pkgrel=4`, `CONFIG_LOCALVERSION="-sm8150-rc3"`, patches `0001`–`0041` (gaps at 0013/0014/0016/0023) |
| Patched kernel sources | `flows/local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150` | 7.0.0 + all rc3 patches, **configured** build in `.output/` (`CONFIG_LOCALVERSION="-sm8150-rc3"`, `vmlinux` objects present) |
| Second patched copy | `bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150` | not configured; every file touched here is byte-identical to the tree above (`cmp`) |
| Device package | `device/testing/device-oneplus-guacamole/` | `pkgrel=14`, systemd-first: `guacamole-mpss-start` is the shared modem starter, `guacamole-mpss.service` is deliberately not in `80-guacamole.preset` |
| Suspend policy | `flows/local-storage/scripts/rootfs_prepare_local.sh:1591-1597`, `check_local_candidate.py` | automatic suspend **disabled** (logind idle/suspend-key ignored, PowerDevil `AutoSuspendAction=NoAction`) |

The supplied `README.md` is written against guacamole_nura `5b2241b` (6.17,
patches 0001–0024) and is not a description of this baseline.  Its pinned
commits and "what was verified" section were re-checked, not trusted.

## 2. Numbering

| Supplied | rc4 number | Disposition | Where |
| --- | --- | --- | --- |
| `0025-misc-fastrpc-freezable-invoke-wait.patch` | **0042** | already present as rc3 `0034-fastrpc-freezable-wait.patch` | `superseded/0042-…patch.reference` |
| `0026-input-s6sy761-enable-irq-after-resume-restart.patch` | **0043** | already present, superset, as rc3 `0035-s6sy761-resume-irq.patch` | `superseded/0043-…patch.reference` |
| `0027-soc-qcom-guacamole-mss-vote.patch` | **0044** | integrated (repaired) | `patches/0044-soc-qcom-guacamole-mss-vote.patch` |
| `0028-ath10k-snoc-wmi-key-ack.patch` | **0045** | integrated (repaired) | `patches/0045-ath10k-snoc-wmi-key-ack.patch` |

Only `patches/` is merge-ready.  `superseded/*.patch.reference` do **not** end
in `.patch`, do **not** match the build script's `0*.patch` glob, and must not
be added to `source=`; they exist so the 0025→0042 / 0026→0043 mapping is
complete and to show exactly what is already applied.

## 3. Claim / evidence / verdict

Commands were run from the repository root; `K` is
`flows/local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150`.

| # | Claim (supplied) | Evidence / command | Verdict |
| --- | --- | --- | --- |
| 1 | "All four apply cleanly to 7.0 after GN 0005/0017" | `patch -p1 --dry-run -F0 < 0025` → `Reversed (or previously applied) patch detected`; `0026` → `Hunk #1 FAILED at 556`; `0027`/`0028` apply with `-F0`, zero offset | **Contradicted for 0025/0026** (already in rc3); verified for 0027/0028 |
| 2 | 0025 fixes the sensor-DSP crash on suspend | `K/drivers/misc/fastrpc.c:1240-1249` contains the `wait_for_completion_state(..., TASK_INTERRUPTIBLE \| TASK_FREEZABLE)` wait, identical to rc3 `0034` | **Already present**; hardware effect unverified |
| 3 | 0026 fixes `-ENODEV` on touch resume | `K/drivers/input/touchscreen/s6sy761.c` `s6sy761_resume()` powers on, restores sense, then `enable_irq()`; rc3 `0035` has 0026's change plus error/unwind paths and `powered_up` | **Already present (superset)**; hardware effect unverified |
| 4 | rpmhpd clamps every domain to the top corner until sync_state | `K/drivers/pmdomain/qcom/rpmhpd.c:882` `/* Clamp to highest corner if sync_state hasn't happened */` | **Verified in source** |
| 5 | sync_state never runs on guacamole, so the AP holds `mss.lvl` at 9 | Cannot be observed without the phone; `rpmhpd_sync_state()` exists and is registered (`rpmhpd.c:1096,1131`) | **Unverified** (behavioural claim) |
| 6 | `mss.lvl` is the sm8150 modem rail and the modem node has the `mss` domain | `rpmhpd.c:157` (`mss`, `res_name="mss.lvl"`), `sm8150_rpmhpds[SM8150_MSS]`; `sm8150.dtsi:2810-2812` `power-domains = <&rpmhpd SM8150_CX>, <&rpmhpd SM8150_MSS>; power-domain-names = "cx", "mss"` | **Verified** |
| 7 | `mss.lvl` has a cmd-db address on this board | Not directly observable. `rpmhpd_probe()` returns `-ENODEV` if any domain's resource is missing, so the provider would not register and the modem could not attach; the board boots the modem | **Inference**, not proven |
| 8 | The module's genpd notifier is called after rpmhpd's own vote | `pmdomain/core.c:873` (`GENPD_NOTIFY_ON` after `genpd->power_on`), `:924-928` (`OFF` after `power_off`) | **Verified** |
| 9 | Sleeping (`rpmh_write`) inside that notifier is legal | `dpm_suspend_noirq()` is at `kernel/power/suspend.c:436`, `arch_suspend_disable_irqs()` only at `:457`; genpd uses a mutex here. `rpmh_write` itself uses `might_sleep()`/`wait_event_lock_irq` | **Verified** (process context, no IRQ-off window) |
| 10 | `rpmh_write(&rpmhpd_pdev->dev, …)` routes to the right RSC | `rpmh.c:74` `dev_get_drvdata(dev->parent)`; `rpmhpd` is a child of `apps_rsc` (`sm8150.dtsi:4610-4632`) and `rpmh-rsc.c:1122` sets that drvdata | **Verified** |
| 11 | Notifier lifetime / unload are balanced | `dev_pm_genpd_remove_notifier(dev)` (one argument in 7.0) takes `genpd_lock` to unregister, and the same lock is held while the chain runs (`pmdomain/core.c:873,924`), so no callback can still be running when removal returns; `dev_pm_domain_detach()` → `genpd_dev_pm_detach()` then unregisters the virtual `genpd:1:mss_vote` device; `of_node_put` + `put_device(holder)` release the fake holder | **Verified by code reading**; not executed |
| 12 | Unloading restores the top corner | `mss_vote_exit()` sends `MSS_TOP_CORNER` when `level >= 0` | **Verified in code**; note it can re-trigger the very assertion if unloaded with the modem online |
| 13 | The module is inert on other machines | `of_machine_is_compatible("oneplus,guacamole")`, `cmd_db_read_addr("mss.lvl")`, `qcom,sm8150-rpmhpd` node, `qcom,sm8150-mpss-pas` node; all four must be present. Board DTS: `sm8150-oneplus-guacamole.dts:20 compatible = "oneplus,guacamole", …` | **Verified** |
| 14 | The genpd notifier is needed even without suspend | It also fires when genpd powers MSS off/on at modem stop/start and during system resume; without system suspend (current policy) the one-shot `level` write is what applies | **Verified mechanism** |
| 15 | 0028's WMI key-complete event exists on WCN3990 | `WMI_TLV_TAG_STRUCT_VDEV_INSTALL_KEY_COMPLETE_EVENT` exists (`wmi-tlv.h:605`); the old handler is a debug print only (`wmi.c:5339-5343`); firmware behaviour | **Unverified on hardware**; source side verified |
| 16 | 0028 leaves other buses alone | `wmi_ack` requires `ar->hif.bus == ATH10K_BUS_SNOC` *and* TLV; the new TLV handler hands every other bus to `ath10k_wmi_event_vdev_install_key_complete()` | **Verified** (unchanged behaviour and debug line for PCI/SDIO TLV) |
| 17 | 0028's late-reply guard is safe | The WMI event carries no transaction id; a repeated removal of the same peer produces the same vdev/peer/index/flags, so a delayed ack *can* match a later removal.  The audited design now latches on the first timeout and never arms the WMI wait again until the firmware restarts, falling back to the pre-patch HTT-only wait (a repeat removal then takes 3 s, never `-EIO`). | **Corrected**: the earlier "a late reply can never satisfy a later operation" was only true once the latch was set; it is now true for every operation because nothing waits while latched (`checks/key_state_model.py`) |
| 18 | 0028's `macaddr` can be NULL | All four callers pass a non-NULL address (`sta->addr`, `vif->bss_conf.bssid`, `vif->addr`, `peer->addr`); `ath10k_set_key()` never passes NULL | **Refuted concern**; no NULL deref, but the new `running_fw` guard was added anyway (helper callers skip the `nohwcrypt` check) |
| 19 | config.diff's symbols exist in 7.0 | `kernel/power/Kconfig:229-267` (`PM_DEBUG`, `PM_ADVANCED_DEBUG`, `PM_TEST_SUSPEND`, `PM_SLEEP_DEBUG` `def_bool y`, `DPM_WATCHDOG`); rc3 config has `CONFIG_PM=y`, `CONFIG_PM_SLEEP=y`, `CONFIG_SUSPEND=y`, `CONFIG_QCOM_RPMH=y`, `CONFIG_QCOM_COMMAND_DB=y`, `CONFIG_PM_GENERIC_DOMAINS=y`, `CONFIG_ARCH_QCOM=y` | **Verified**; `DPM_WATCHDOG` stays off because it needs `CONFIG_EXPERT` |
| 20 | `pm_test` is available on the current kernel | rc3 config has `# CONFIG_PM_DEBUG is not set`, so `/sys/power/pm_test` does not exist until the fragment is merged | **Contradicted for rc3**; fixed by `fragments/kernel-config.fragment` |
| 21 | `kernel/APKBUILD.diff` applies | It targets 6.17 (`pkgver=6.17.0`, `pkgrel 25→26`, patches 0025–0028); the rc4 recipe is 7.0.0 `pkgrel=4` with 41 patches | **Contradicted** (stale); replaced by `fragments/apkbuild-source.fragment` |
| 22 | `device/device-package.diff` applies | It targets `pkgrel 11→12`, enables the unit in the preset and patches `guacamole-mpss.initd`; the package is `pkgrel=14` and starts the modem with `guacamole-mpss-start` (systemd-first) | **Contradicted** (stale); replaced by `fragments/device/` |
| 23 | A userspace release service is required | The supplied unit works, but `guacamole-mpss.service` is not enabled by preset; a standalone unit would wait 120 s and fail on every boot where the modem is not started | **Verified with caveat**; the unit is the only integration and stays disabled in the preset (opt-in) |
| 24 | WoWLAN is an untested suspend path | The device package ships `90-ath10k-wowlan.rules`, `90-wifi-wowlan.conf`, `10-wifi-wowlan` and installs them unconditionally | **Verified present**; never tested here |
| 25 | The reported lock crash is a suspend bug | The candidate rootfs disables automatic suspend (logind `HandleSuspendKey=ignore`, PowerDevil `AutoSuspendAction=0`); a lock is not a suspend.  Only 0044 could plausibly matter at screen-off (modem RF idle) and even that is a hypothesis | **Contradicted as an assumption**; needs logs |
| 26 | `remoteproc state=running` proves the proxy MSS domains were handed over | `qcom_pas_start()` waits only for `qcom_q6v5_wait_for_start()` (`qcom_q6v5_pas.c:339`), completed by the **ready** interrupt (`qcom_q6v5.c:136-143`); `rproc->state = RPROC_RUNNING` follows `.start()` (`remoteproc_core.c:1311`).  The proxy PDs are dropped later by `qcom_pas_handover()` from the separate **handover** interrupt (`qcom_q6v5.c:163-180`, `qcom_q6v5_pas.c:386-396`) | **Refuted**: running proves firmware boot, not handover |
| 27 | A level-0 write cannot be overridden by the in-tree holder | `qcom_pas_pds_enable()` votes the proxy at `INT_MAX` (`qcom_q6v5_pas.c:154-178`); `rpmhpd_set_performance_state()` maps that to the last corner and keeps voting it while the domain is enabled.  `qcom_pas_handover()` → `qcom_pas_pds_disable()` sets pstate 0, but `rpmhpd_aggregate_corner()` still clamps to 9 while unsynced (`rpmhpd.c:882`); `rpmhpd_sync_state()` re-votes `max(pd->corner, enable_corner)` = 9 with **no genpd notification** (`rpmhpd.c:1096-1120`) | **Confirmed hazard**: a 0 written before handover can be re-voted to 9 by the handover, and a later `sync_state` can do it again with nothing to re-assert 0 |
| 28 | The mss release is safe to run automatically at modem start | Follows from rows 26/27.  The helper now waits for the proxy `genpd:1:4080000.remoteproc` runtime status to leave `active` and refuses to write if it cannot observe it (`MSS_ALLOW_UNVERIFIED_HANDOVER=1` overrides); there is no starter hook and the unit is not in the preset | **Gated (opt-in)**: `systemctl enable --now guacamole-mss-release.service` is the only way it runs |

## 4. Repairs made to the supplied patches

**0044** — logic unchanged.  Three early `-ENODEV`/`-EPROBE_DEFER` returns now
print a reason: a `module_init` returning `-EPROBE_DEFER` has no retry, so
dmesg was the only diagnostic and it was silent.  The header comment still
named the old sysfs path (`mss_vote`); corrected to `guacamole_mss_vote`.

**0045** — `wmi_ack` is computed after the `nohwcrypt` early return and guarded
with `ar->running_fw &&`: the two WEP helper callers of `ath10k_install_key()`
do not check `nohwcrypt` themselves, so the original dereferenced
`running_fw->fw_file` unconditionally.  The blank line after
`lockdep_assert_held()` that the supplied patch dropped is restored.  A
timeout now latches the sequence and later operations fall back to the
pre-patch HTT-only wait instead of returning `-EIO` while latched (the
supplied patch made every later key operation fail with `-EIO` after one
removal timeout, and an earlier revision of this topic only latched installs,
which let a delayed removal ack match a later removal of the same peer).
Non-SNOC TLV devices still get
`ath10k_wmi_event_vdev_install_key_complete()`.  `checks/key_state_model.py`
models these orderings and asserts the invariants on the applied source.

No other code changes.  Applying the supplied patches and the integrated
patches to the same rc3 tree and diffing the results shows exactly these hunks
and nothing else; the integrated result is what `checks/expected-files.sha256`
pins.

## 5. Interaction and risk review

* **Notifier lifetime.**  `dev_pm_genpd_add_notifier()`/`_remove_notifier()` and
  the call chain all run under `genpd_lock`, so removing the notifier cannot
  race a running callback; the virtual `genpd:1:mss_vote` device is then
  unregistered by `dev_pm_domain_detach()` and the fake holder released.  If
  `genpd_remove_device()` were to return `-EBUSY` (only while a system suspend
  is in progress), the virtual device would be left registered; do not unload
  the module during a suspend transition.
* **Two independent horses, same rail.**  `rpmhpd` and this module both write
  `mss.lvl`; the module's model is last-write-wins.  On power-on rpmhpd's
  raising vote is synchronous (`rpmhpd_send_corner(..., sync = higher)`) and
  the notifier runs after it, so the order is sound.  On power-off rpmhpd's
  vote is asynchronous (`active_corner > pd->active_corner` is false), so in
  theory the two commands could be applied out of order by different RSC DRVs.
  Not reproducible or observable here; it would leave the rail at 9 until the
  next on/off, not crash anything new.
* **Sleep vote gap.**  The module sends `RPMH_ACTIVE_ONLY_STATE` only.  The
  cached *sleep* vote for `mss.lvl` stays at rpmhpd's clamped corner, but
  `rpmh_suspend()`/`rpmh_flush()` only run for deep suspend, and this board
  offers `s2idle` only (`mem_sleep` = `[s2idle]`, per the supplied diagnosis
  doc).  Do not treat 0044 as covering a `deep`/`mem` transition.
* **Corner numbering.**  `level` accepts 0..9 and `MSS_TOP_CORNER` is 9.  Only
  corner 0 is used by the integration; any other value is untested.  The raw
  `rpmh_write()` bypasses rpmhpd's `level_count` validation.
* **`sync_state`.**  If it runs *after* the level-0 write, `rpmhpd_sync_state()`
  re-votes `pd->corner` (9, from the proxy `INT_MAX`) with no genpd
  notification, and nothing re-asserts 0.  That is an unobservable hazard, not
  a benign no-op: see the MSS audit in §6.  The supplied diagnosis ("sync_state
  never runs") is not proven by this work.
* **ath10k desync.**  Any WMI timeout latches the sequence; from then on the
  driver uses the pre-patch HTT-only wait until the firmware restarts, so a
  removal costs its old 3 s and a reconnect still works.  The latch is never
  cleared except by `ath10k_core_start()`, so no late acknowledgement can be
  matched.  Within a non-latched sequence the design still assumes the firmware
  sends exactly one ack per command; a duplicate ack could match the next
  operation and no AP-side state can tell the two apart.  `rmmod ath10k_snoc;
  modprobe ath10k_snoc` (or a reboot) restores the fast path.  This does not
  affect the current no-suspend policy beyond disconnect/roam.
* **WoWLAN.**  With WoWLAN armed (device default), key removal may not happen
  at all on suspend, so 0045 matters less on that path; it matters for
  disconnect/roam and for the non-WoW path.
* **No DT change** for 0044: the board already has the `mss` power domain and
  the `oneplus,guacamole` compatible.

## 6. Runtime MSS vote release: handover audit and opt-in

The supplied 0027 doc says the level is written only after the modem runs,
because "the proxy vote must be handed over first, or the modem can't boot at
the lower corner".  That precondition does not hold on mainline rc3 as
written:

1. `guacamole-mpss-start` (and the helper) wait for
   `/sys/class/remoteproc/*/state == running`.
2. `state=running` is set by `rproc_start()` after `rproc_ops->start()`
   returns (`remoteproc_core.c:1311`).
3. `qcom_pas_start()` returns after `qcom_q6v5_wait_for_start()`
   (`qcom_q6v5_pas.c:339`), which the modem **ready** interrupt completes
   (`qcom_q6v5.c:136-143`).
4. The proxy power domains are dropped by `qcom_pas_handover()`, called from
   `q6v5_handover_interrupt()` — a **separate interrupt** (`qcom_q6v5.c:163-180`),
   not from the start path (`qcom_q6v5_pas.c:386-396`).  `running` therefore
   does not prove that the handover ran.
5. `qcom_pas_pds_enable()` holds the proxy at `pstate = INT_MAX`
   (`qcom_q6v5_pas.c:154-178`), which rpmhpd maps to the last corner (9).
   `qcom_pas_pds_disable()` sets pstate 0, but `rpmhpd_aggregate_corner()`
   clamps it back to 9 until `sync_state` (`rpmhpd.c:882`).  A 0 written
   before that point is overwritten by the handover itself.
6. `rpmhpd_sync_state()` re-votes `pd->corner` (still 9 from the proxy
   `INT_MAX`) for every enabled domain and is not a genpd on/off event, so the
   module's notifier does not fire (`rpmhpd.c:1096-1120`).  If it runs after
   the level-0 write, the rail returns to 9 silently.

The module notifier does recover the ordering in most cases (the handover's
`pm_runtime_put` powers the MSS domain off, which notifies the module, which
re-sends the chosen corner), but "most cases" is not a precondition, and
`sync_state` has no notifier at all.

The proxy `runtime_status` file itself has exactly four values — `active`,
`resuming`, `suspending`, `suspended` (`drivers/base/power/sysfs.c:149-171`),
and only `suspended` is the state `qcom_pas_pds_disable()` leaves behind.  The
helper therefore accepts only that exact value; the other three and any read
failure are not treated as handover.

Consequences taken here:

* **No automatic hook.**  The `guacamole-mpss-start` integration was removed.
  The only ways to run the release are to enable
  `guacamole-mss-release.service` (not in `80-guacamole.preset`) or to run
  `/usr/libexec/guacamole-mss-release` by hand.  That is the opt-in.
* **Handover barrier.**  The helper reads the proxy `mss` virtual device
  (`/sys/devices/genpd:1:4080000.remoteproc/power/runtime_status`) and accepts
  **only the exact value `suspended`**, which is what
  `qcom_pas_pds_disable()`/`pm_runtime_put` produce from the handover
  interrupt.  `active` and `suspending` are waited on (the put is
  asynchronous) for at most `MSS_HANDOVER_TIMEOUT` (default 10 s); `resuming`,
  an empty or unreadable value, any other string, and a timeout all refuse the
  write.  The modem is re-checked as `running` on every poll and again
  immediately before the write, because a modem stop suspends the proxy too.
  `MSS_ALLOW_UNVERIFIED_HANDOVER=1` forces a one-off trial when the handover
  was not observed; it never bypasses the modem checks.
* **Inert by installation.**  Loading the module changes nothing until
  userspace writes `level`.

Residual risk after a successful handover wait: `sync_state` can still re-vote
9 later.  There is no AP-side readback of the actual rail state, so a trial
must watch the modem (`state`, Wi-Fi, `rflm_qlnk` assertion) after the write
rather than trusting the helper's log line.

## 7. Distinguishing the reported "lock crash" from suspend

The user's report is a **screen-lock crash**.  The rc4 rootfs candidate turns
automatic suspend off, so no system suspend is expected at lock.  That makes
the suspend patches a hypothesis, not a diagnosis:

* 0042/0043 only run on a system suspend/resume; they cannot affect a lock that
  does not suspend.
* 0045 only removes 3 s timeouts on Wi-Fi disconnect/roam; not a crash.
* 0044 is the only change that can act at screen-off without suspend, because
  the modem's RF can idle independently of the AP.  It is still a hypothesis.

Before calling anything fixed: capture `dmesg -w` over SSH, reproduce the lock,
and record `/sys/class/remoteproc/*/{name,state}`, `/sys/power/suspend_stats/*`
and (after the config fragment) `pm_test` levels.  `docs/supplied/00-diagnose.md`
has the procedure; note that its `pm_test` section requires this topic's
`CONFIG_PM_DEBUG` fragment.

## 8. Integration steps (parent)

Paths are relative to the repository root.  Nothing outside
`flows/rc4/topics/suspend-fixes` was modified.

1. **Create the rc4 kernel recipe** from the rc3 baseline (the parent's normal
   copy step), then copy the two patches next to the APKBUILD:
   ```sh
   cp flows/rc4/topics/suspend-fixes/patches/0044-soc-qcom-guacamole-mss-vote.patch \
      flows/rc4/topics/suspend-fixes/patches/0045-ath10k-snoc-wmi-key-ack.patch \
      <rc4-recipe>/
   ```
2. **Recipe `source=`**: append the two lines from
   `fragments/apkbuild-source.fragment` after `0041-…`.  Do not add anything
   from `superseded/`.
3. **Kernel config**: merge `fragments/kernel-config.fragment`
   (`CONFIG_PM_DEBUG=y`, `CONFIG_PM_SLEEP_DEBUG=y`, `CONFIG_QCOM_GUACAMOLE_MSS_VOTE=m`)
   and, as the last config merge of the release, `fragments/kernel-localversion.fragment`
   (`CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"`).  Then
   `pmbootstrap checksum linux-postmarketos-qcom-sm8150`.
4. **Device package**: follow `fragments/device/INSTALL.md` — install
   `guacamole-mss-release` and the unit, add the `source=`/`package()` lines,
   bump `pkgrel`.  Do **not** add the unit to the preset and do not add a
   `guacamole-mpss-start` hook: enabling the unit on the phone is the opt-in
   (topic §6).  Leave the udev rule uninstalled unless a diagnosis needs it.
5. **Build**: the existing rootless path works, e.g.
   ```sh
   NURA_RECIPE_DIR=<rc4-recipe> NURA_KERNEL_RELEASE=7.0.0-sm8150-guacamole-rc4 \
     bash scripts/build_rear_camera_kernel.sh
   ```
   It applies patches with `--fuzz=0` for names ≥ 0028, which 0044/0045 satisfy.
6. **Release docs**: merge `docs/RELEASE_NOTES_FRAGMENT.md` (wording already
   says untested).  Copy `docs/supplied/` only if the release wants the
   diagnostic material; it is 6.17-era wording and its errata are in §3.

## 9. Checks actually run

| Check | Result |
| --- | --- |
| `checks/patch_apply_check.sh` | both patches apply to the rc3-patched tree with `-F0`, **no offset, no fuzz**; patched files match `checks/expected-files.sha256` |
| `checks/fsyntax_check.sh` (via the above) | **PASS** for `guacamole_mss_vote.c`, `ath10k/mac.c`, `ath10k/core.c`, `ath10k/wmi-tlv.c` (clang 23, `--target=aarch64-linux-gnu`, rc3 `.output` `autoconf.h`) |
| `checks/test_guacamole_mss_release.sh` | 38/38: running modem + exact `suspended` proxy writes the corner; no modem, modem timeout, two modems, failed `modprobe`, `MSS_LEVEL=3` read-back; proxy `active`, `suspending` (immediate and after a wait), `resuming`, `unsupported`, unknown, empty, unreadable (directory and mode 000), missing/deleted and timeout all refuse; the modem dropping out during the wait and during `modprobe` refuses; `MSS_ALLOW_UNVERIFIED_HANDOVER=1` forces an unobserved handover but not a dead modem |
| `checks/key_state_model.py` | 33/33: model of the key state machine (install/removal/timeout/late-ack/reconnect/firmware-restart sequences) and structural assertions on the applied source |
| `checks/check_fragments.sh` | 34/34: config symbols, rc3 config dependencies, unit keys, preset fragment, no starter hook, exact-`suspended`/unknown-status/modem-recheck assertions on the helper, udev rule, inventory JSON |
| fsyntax of the unpatched baseline | used as the control (`qcom_gsbi.c`); the synthetic command line was validated before use |

Counts, as printed: `run_all.sh` emits **74 `PASS` lines** = 7 from
`patch_apply_check` (2 apply + 1 hash match + 4 fsyntax) + 33 from
`key_state_model.py` + 34 from `check_fragments.sh`.  The helper suite prints
only its own summary line, **38/38**, so the total is 74 + 38 = **112
assertions**.  (The previous report said "71 PASS" and then listed
31+33+18; that mixed the print-line count with the helper's case count.  The
current numbers are the ones above and are reproducible from the output in
`checks/RESULTS.md`.)

**Not done:** a kernel `make`/link, a DT build, a package build, a boot, any
phone access, any suspend test.  The syntax check compiles one translation
unit at a time and cannot catch link/modpost or section issues; a real
`build_rear_camera_kernel.sh` run is still required.  `sync_state`, cmd-db
contents, the WMI key-complete event and the modem freeze are hardware
behaviour and are **not** established by anything here.

## 10. Hardware test order (after the parent's review and a build)

Do not enable system suspend for this.  Test 0044 first, unplugged:

1. Boot; `dmesg | grep mss_vote` shows `ready` only when the release has run.
   Opt in with `systemctl enable --now guacamole-mss-release.service` (or run
   `/usr/libexec/guacamole-mss-release`); the journal must say
   `proxy handover observed` and `mss.lvl corner 0 written`.
   `cat /sys/module/guacamole_mss_vote/parameters/level` then reads `0` and the
   modem is `running`.  If it says `refusing to write`, do not force it until
   the proxy status has been inspected.
2. Screen off, no suspend, 5–10 min with the modem online: no
   `RFLM@qsf_hl_seq.c:118`, modem still `running`, Wi-Fi still up.
3. Reproduce the lock several times with `dmesg -w` attached.  This is the
   actual reported symptom; nothing here has reproduced it.
4. Only if a suspend test is wanted: disable WoWLAN first
   (`iw phy0 wowlan disable`), arm an RTC alarm, and use the `pm_test` ladder
   from `docs/supplied/00-diagnose.md`.  `pm_test=platform` is the risky one.
5. Rollback: boot the previous image.  In place, stop the modem first
   (`echo stop > /sys/class/remoteproc/<modem>/state`), then
   `rmmod guacamole_mss_vote` (restores corner 9); or simply leave the module
   loaded at corner 0 (it only drops a vote) and
   `systemctl disable --now guacamole-mss-release.service`.  Do not unload the
   module while the modem is running.

## 11. File map

```
README.md                       this file
inventory.json                  every supplied file, hash, disposition, dependencies
patches/0044-…patch             MERGE: MSS rail vote driver (Kconfig/Makefile/new file)
patches/0045-…patch             MERGE: ath10k SNOC WMI key ack
superseded/0042….patch.reference  DO NOT ADD: already rc3 0034
superseded/0043….patch.reference  DO NOT ADD: already rc3 0035
fragments/kernel-config.fragment   MERGE: PM_DEBUG/PM_SLEEP_DEBUG + MSS vote =m
fragments/kernel-localversion.fragment  MERGE LAST: -sm8150-guacamole-rc4
fragments/apkbuild-source.fragment      source= lines and sha512sums
fragments/device/INSTALL.md             device package steps
fragments/device/guacamole-mss-release  /usr/libexec helper, handover-gated (tested)
fragments/device/guacamole-mss-release.service  OPT-IN unit; not in the preset
fragments/device/80-guacamole.preset.fragment   no change; do not enable
fragments/device/90-guacamole-modem-recovery.rules  GATED diagnostic
docs/supplied/                  verbatim input docs (sha256 in inventory.json)
docs/RELEASE_NOTES_FRAGMENT.md  text for the merged rc4 release doc
checks/                         rootless checks; run checks/run_all.sh
checks/key_state_model.py       ath10k key state-machine model + source assertions
```
