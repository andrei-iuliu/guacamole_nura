# 00: Find which standby failure you have

Do this before applying (or while bisecting) the patches. The standby crash on guacamole has several independent causes. Each one leaves a different trace.

## Why logs are usually empty

OM reports that pstore was empty after each frozen-phone incident, because a forced reset (long-press Power) loses the ramoops region ([OM `docs/sleep-20260927.md` "Incidents"](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/sleep-20260927.md#incidents)). Don't expect GN's `scripts/ramoops.sh` to explain a freeze. Use a live `dmesg -w` over SSH (USB networking) and narrow it down with `pm_test`.

## 1. Enable `pm_test` (kernel/config.diff)

GN ships `# CONFIG_PM_DEBUG is not set` ([GN config line 596](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/device/testing/linux-postmarketos-qcom-sm8150/config-postmarketos-qcom-sm8150.aarch64#L596)), so `/sys/power/pm_test` doesn't exist. `kernel/config.diff` turns on `CONFIG_PM_DEBUG=y`, which brings in `CONFIG_PM_SLEEP_DEBUG=y`. It costs nothing at runtime. OM did the same (native5, [OM `docs/suspend-20260917.md`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/suspend-20260917.md)).

To get a stack trace instead of a silent hang when a driver's suspend callback blocks, you can also set `CONFIG_DPM_WATCHDOG=y`. It panics after a timeout, and a panic (unlike a forced reset) preserves ramoops. This option is left off in the diff because it is debugging only.

## 2. Narrow it down

`mem_sleep` on this phone offers only `[s2idle]`, so `echo mem > /sys/power/state` is s2idle.

```sh
echo freezer  > /sys/power/pm_test; echo mem > /sys/power/state   # tasks frozen only
echo devices  > /sys/power/pm_test; echo mem > /sys/power/state   # + device suspend
echo platform > /sys/power/pm_test; echo mem > /sys/power/state   # + noirq / genpd
echo none     > /sys/power/pm_test                                  # real suspend again
```

What OM saw at each level before the fixes ([OM `docs/sleep-20260927.md` L79–L83](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/sleep-20260927.md#L79-L83)):

| `pm_test` | Failure | Points to |
| --- | --- | --- |
| `freezer` | `EX:sensor_process:0x1:frpc_dsp:0x6c`, SLPI remoteproc crashed | 0025 |
| `devices` | (Wi-Fi `-110` key-removal timeout, ~3 s) | 0028 |
| `platform` | **whole phone froze**, nothing logged | 0027 |
| real | modem watchdog `rflm_qlnk_ls_retry_cnt < 2` ~1 s after resume; touch `-ENODEV` | 0027, 0026 |

## 3. Modem crash or kernel crash?

Check: `grep . /sys/class/remoteproc/remoteproc*/{name,state,recovery}`.

Upstream, modem recovery (SSR) is `enabled` by default. In OM, a modem crash with recovery enabled killed the kernel "while recovering modem", and the phone rebooted with nothing in ramoops. OM's rule is "Keep modem recovery disabled" ([OM `docs/cellular-sim-20260926.md` L185–L194](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/cellular-sim-20260926.md#L185-L194)).

GN carries `0015` (bounded `gsi_trans_commit_wait()`, "so a modem crash cannot wedge recovery from the SSR notifier"). That may already prevent the kernel death OM saw, but it hasn't been tested.

Test: `echo disabled > /sys/class/remoteproc/<modem>/recovery` (or install `device/90-guacamole-modem-recovery.rules`), then go to standby.
- If the phone now survives but the modem shows `crashed` (Wi-Fi gone too, since WCN3990 firmware runs on the MPSS), the root cause is the modem, so apply 0027.
- If it still dies, look at 0025, WoWLAN and the `platform` stage.

Trade-off of the rule: with recovery disabled, a modem crash leaves cellular and Wi-Fi down until reboot. Keep the rule only for diagnosis, or until 0027 is proven and recovery has been shown to survive.

## 4. WoWLAN (GN-only path, untested anywhere)

GN arms WoWLAN by default:
- `90-ath10k-wowlan.rules` sets `power/wakeup=enabled` on `18800000.wifi`.
- `90-wifi-wowlan.conf` sets `wake-on-wlan=12`.
- The `10-wifi-wowlan` dispatcher runs `nmcli device reapply` so `iw phy0 wowlan show` reports it enabled.

With WoWLAN armed, suspend goes through `ath10k_wow_op_suspend()` and `ath10k_snoc_hif_suspend()` (`enable_irq_wake` on the CE wake IRQ) instead of a plain disconnect. GN's README lists "WoWLAN pending / Test WoWLAN" ([GN README L45](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/README.md#L45)). OM kept Wi-Fi wake disabled and lists WoWLAN as future work ([OM `docs/sleep-20260927.md` "Next"](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/sleep-20260927.md#next)). So nobody has run this path on guacamole.

Test: `iw phy0 wowlan disable` (it is re-armed on the next Wi-Fi "up"), then suspend. If standby only survives with WoWLAN off, the fix is not in this folder. In that case, change GN's default (drop the dispatcher's `reapply`, or `wake-on-wlan=0`) until it is debugged. Also capture dmesg around `wow` and `ath10k` for that investigation.

## 5. Other things worth recording, not believed to crash

- RTC: GN builds `CONFIG_RTC_DRV_PM8XXX=m`. Without the module loaded there is no wake alarm for timed tests. OM had to enable the PM8150 RTC node before the SPMI parent populates its children ([OM `docs/suspend-20260917.md`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/suspend-20260917.md)).
- Userspace input loss after resume: under Hyprland, OM's input devices vanished after the seat suspend cycle, and `udevadm trigger --action=add` on input devices fixed it ([OM `docs/suspend-20260917.md` L83](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/suspend-20260917.md#L83)). This is not seen under Phosh yet; check it if touch is dead even though the kernel log shows a clean resume.
- Charging in suspend: GN's `guacamole-chgctrl` polls every 5 s and does not take suspend into account. OM refuses to suspend on the charger, so its charge guard can't be skipped. Test unplugged first.
- GN `docs/PLAN.md` L180 lists hotdog 0156 suspend candidates that were not applied: the IPA `no_system_suspend` diagnostic, the remoteproc proxy power-domain hold, and wdog/fatal IRQ wakeups ([GN PLAN L180](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/docs/PLAN.md#L180)). Look at those if the modem still misbehaves after 0027.
