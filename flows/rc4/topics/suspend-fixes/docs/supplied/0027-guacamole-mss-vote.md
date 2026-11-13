# 0027: soc: qcom: add guacamole_mss_vote (release the AP's modem-rail vote)

This is the most likely cause of the hard standby crash.

## Files

- `kernel/0027-soc-qcom-guacamole-mss-vote.patch`: new `drivers/soc/qcom/guacamole_mss_vote.c`, plus Kconfig `QCOM_GUACAMOLE_MSS_VOTE` and a Makefile entry.
- `kernel/config.diff`: `CONFIG_QCOM_GUACAMOLE_MSS_VOTE=m`
- `device/guacamole-mss-release`: installed as `/usr/libexec/guacamole-mss-release`. It waits for the `modem` remoteproc to be `running`, loads the module, and writes `0` to `level`.
- `device/guacamole-mss-release.service`: systemd unit, `After=rmtfs.service`, `Type=exec`.
- `device/device-package.diff`: installs both files, enables the unit in `80-guacamole.preset`, and calls the script from `guacamole-mpss.initd` on OpenRC.

**Fixes:** the modem firmware asserting after the radio idles, and the phone freezing during the suspend `noirq` stage.

**Risk:** medium. It changes a power-rail vote. OM validated level `0` with the modem online over many sleeps. The module does nothing on machines other than guacamole, and unloading it restores the top corner.

## Problem

1. **rpmhpd never syncs.** `rpmhpd` clamps every power domain to its highest corner until its `sync_state` runs: `/* Clamp to highest corner if sync_state hasn't happened */` in `drivers/pmdomain/qcom/rpmhpd.c` (L852 in 6.17, L882 in 7.0). On this board `sync_state` never runs, so after the modem drops its proxy vote at handover, the AP keeps voting `mss.lvl` at corner 9 (`turbo_l1`) for the whole boot. Stock Android drops that vote.
2. **The modem crashes when its RF idles.** With MSS held at the top corner, the modem's QLink to the SDR8150 transceiver can't re-establish after the RF sleeps, and the modem watchdog fires:
   ```
   qcom_q6v5_pas 4080000.remoteproc: watchdog received:
   rflm_diag_error.cc:368:RFLM@qsf_hl_seq.c:118 Assertion (rflm_qlnk_ls_retry_cnt < 2) failed
   remoteproc remoteproc0: crash detected in modem: type watchdog
   ```
   OM saw this 103 s after going online with nothing else happening, which is consistent with "screen off, phone idle → crash". WCN3990 Wi-Fi firmware runs on the MPSS, so Wi-Fi dies with it.
3. **Suspend brings it back on every resume.** genpd powers the MSS domain on in `resume_noirq` (`genpd_sync_power_on`) and queues it off again in `genpd_complete`. Each time, rpmhpd re-sends its clamped top-corner vote. So even after the vote is dropped once, every resume puts it back. Before the fix, OM's `pm_test=platform` (through noirq) **froze the whole phone** with no log, and a real s2idle trial had the modem assert 0.96 s after resume.
4. **Modem recovery can take the kernel down.** With modem recovery (SSR) enabled, OM's kernel died "while recovering modem" and rebooted with an empty ramoops. See `00-diagnose.md` section 3.

GN has none of this:
- `grep -r 'mss\|sync_state' guacamole_nura` finds no vote handling.
- GN `docs/PLAN.md` L180 lists the hotdog remoteproc power-domain hold as "not applied … candidates for the suspend work".

## Fix

`guacamole_mss_vote.c`, unchanged from OM's `mss_vote.c` apart from the source comment:

- Module parameter `level` (0..9, write-only effect, mode 0600). A write sends one synchronous **active-only** `mss.lvl` vote (`cmd_db_read_addr("mss.lvl")`) through the rpmhpd device with `rpmh_write()`.
- On load it attaches a virtual device to the modem node's `"mss"` power domain (`dev_pm_domain_attach_by_name`) and registers a genpd notifier (`dev_pm_genpd_add_notifier`). After every `GENPD_NOTIFY_ON` and `GENPD_NOTIFY_OFF` it re-sends the chosen corner, so the top corner lasts microseconds instead of the rest of the boot. `reasserted` (read-only) counts those re-sends.
- `of_machine_is_compatible("oneplus,guacamole")` guard; returns `-ENODEV` on any other machine.
- On unload it detaches and restores the top corner (what rpmhpd last sent).

Userspace sets the level only after the modem is `running`. OM does the same in `guacamole-radio-start` (`cellular/mss-release-at-boot`). The proxy vote must be handed over first, or the modem can't boot at the lower corner.

Changes from OM:
- Renamed `mss_vote` → `guacamole_mss_vote`, so the sysfs path is `/sys/module/guacamole_mss_vote/parameters/level`.
- Kconfig and Makefile glue added so it builds in-tree as `=m`.
- The doc comment points at OM.
- The C code is otherwise byte-identical.

## Sources

- OM root-cause write-ups:
  - [`docs/sleep-20260927.md` "Why sleep crashed the modem" (L61–L96)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/sleep-20260927.md#L61-L96)
  - [`docs/cellular-sim-20260926.md` "The assertion" (L101–L135)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/cellular-sim-20260926.md#L101-L135)
  - [`docs/cellular-sim-20260926.md` "The QLink assertion was the AP's MSS vote" (L282–L295)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/cellular-sim-20260926.md#L282-L295)
  - [`docs/cellular-sim-20260926.md` L185–L194](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/cellular-sim-20260926.md#L185-L194) (kernel death during modem recovery)
- OM code:
  - [`kernel/radio/mss_vote.c`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/radio/mss_vote.c)
  - [`scripts/phone-radio-start.sh` L68–L74](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/scripts/phone-radio-start.sh#L68-L74) (load and write after the modem runs)
- OM history:
  - [`docs/mss-handoff-test-20260917.md`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/mss-handoff-test-20260917.md): dropping MSS **without** the genpd notifier crashed the modem during suspend. That is why the notifier is part of the fix.
  - [`docs/deeper-suspend-20260917.md`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/deeper-suspend-20260917.md): forcing `sync_state` dropped CX/MX/MSS together and the modem watchdogged. So don't "fix" this by forcing `sync_state`.
- GN: [`docs/PLAN.md` L180](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/docs/PLAN.md#L180), base DTB modem node `power-domain-names = "cx", "mss"` (`base_guacamole_working.dts` L2782)

## Applies to

- 6.17 and 7.0: clean.
- 7.0 still has everything the module relies on:
  - the clamp in `rpmhpd.c`
  - `qcom,sm8150-rpmhpd` and `qcom,sm8150-mpss-pas`
  - `power-domain-names = "cx", "mss"` in `sm8150.dtsi`
  - `GENPD_NOTIFY_ON` and `dev_pm_genpd_add_notifier` in `pm_domain.h`
  - rproc name `"modem"` (`sysmon_name`)
  - machine compatible `oneplus,guacamole`

## Verify

1. After boot: `dmesg | grep mss_vote` shows `ready, mss.lvl at 0x…, following genpd:…:mss_vote`, then `active corner 0: 0`. `cat /sys/module/guacamole_mss_vote/parameters/level` reads `0`.
2. Modem online (SIM, ModemManager), screen off, no suspend, ≥ 5 min: no `rflm_qlnk` assertion, and modem `state` is `running`.
3. `echo platform > /sys/power/pm_test; echo mem > /sys/power/state`: the phone returns, and `parameters/reasserted` has gone up by 2 (domain on, domain off).
4. Real s2idle for 60 s: the modem stays registered and Wi-Fi reconnects.

## Notes and open questions

- The level is adjustable: `MSS_LEVEL=<n>` in the environment of `guacamole-mss-release` (for example a systemd drop-in with `Environment=MSS_LEVEL=…`). OM only validated `0`.
- The right long-term fix is upstream: let rpmhpd reach `sync_state` safely, or skip clamping MSS once the remoteproc has handed over. OM leaves this open ("A kernel fix (rpmhpd not clamping MSS, or …)").
- `guacamole-mpss.initd` hard-codes `remoteproc0`. The release script looks the modem up by `name == modem` instead, so it keeps working if SLPI or ADSP probe first.
