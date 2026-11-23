# rc4 release notes fragment: suspend / standby fixes (topic suspend-fixes)

Merge this text into the rc4 release document.  It is written so that nothing
untested reads as working.

---

## Suspend / standby fixes (source-only, untested)

The `Patches/suspend-fixes` hand-off was reviewed against the actual
`7.0.0-sm8150-rc3` sources and the rc3 device package.  Of its four kernel
patches, two were already in rc3 and two are new:

* `0034-fastrpc-freezable-wait` (rc3) already contains supplied 0025: a
  FastRPC invoke waiting across the task freezer no longer restarts as a
  duplicate call for the sensor DSP.
* `0035-s6sy761-resume-irq` (rc3) already contains supplied 0026 and more: the
  touch controller is powered on before its interrupt is unmasked on resume,
  with the error paths unwound.
* **0044** (new, supplied 0027): an optional `guacamole_mss_vote` module plus a
  handover-gated, opt-in release helper.  Once the modem has booted *and* the
  proxy MSS power domain reports the exact runtime status `suspended` (the
  handover; `active`/`suspending` are only waited on, everything else refuses),
  the helper drops the AP's active-only `mss.lvl` vote from the top-corner
  clamp; the module re-sends the chosen corner after every MSS genpd on/off.
  It re-checks that the modem is still `running` before writing, is not
  installed as an automatic modem-start hook, and `state=running` alone is not
  treated as proof of handover.  The handover path itself re-votes the clamped
  corner.  It
  is the only change that can act at screen-off without system suspend, and it
  is a hypothesis for the lock freeze, nothing more.
* **0045** (new, supplied 0028): ath10k SNOC/TLV key operations complete on the
  firmware's WMI key-complete event.  It removes a 3 s `-110` timeout on Wi-Fi
  key removal; it is a latency/noise fix, not a crash fix.  After any timeout
  it permanently falls back to the old wait until the firmware restarts, so a
  late acknowledgement can never satisfy a later operation and Wi-Fi keeps
  working.
* **0078** (rc4q, Wi-Fi, not part of the suspend-fixes hand-off): ath10k offloads the GTK rekey to the firmware during WoWLAN suspend, so the AP's hourly group rekey no longer wakes the phone; see `flows/rc4/topics/wifi-wowlan/README.md`.

Diagnostics: the kernel config now has `CONFIG_PM_DEBUG=y` /
`CONFIG_PM_SLEEP_DEBUG=y`, so `/sys/power/pm_test` exists for
`freezer`→`devices`→`platform` bisection.  `CONFIG_DPM_WATCHDOG` is left off
(it would require `CONFIG_EXPERT`).

### What this does not do

* It does **not** enable system suspend.  The candidate rootfs still ignores
  the logind idle/suspend key and sets PowerDevil `AutoSuspendAction=NoAction`.
  Suspend remains a manual, separately reviewed test.
* It does **not** claim to fix the reported lock crash.  A lock is not a
  suspend; the crash has not been reproduced or logged.  0044 is a hypothesis
  based on the modem rail, nothing more.
* No kernel package, image, DT or boot chain was built by this topic.  The
  patches apply with `-F0` to the rc3-patched tree and the touched translation
  units pass a clang syntax check against that tree's configured build; that
  is all.  The MSS vote, the WMI key ack and the freeze are hardware behaviour
  and remain untested.

### Risks recorded

* 0044 changes a power-rail vote.  Only corner 0 has been used on a phone; the
  module is inert until userspace writes it.  Unloading it with the modem
  online re-sends the top corner and can, in principle, trigger the modem
  assertion it is meant to avoid — stop the modem first.
* 0044 re-votes after genpd on/off only.  It does not update the cached sleep
  vote, so it is not a fix for a `deep` suspend; this board offers `s2idle`.
* 0045 latches the key sequence after any WMI timeout and then uses the
  pre-patch HTT-only wait until the firmware restarts.  The cost is the old
  3 s `-110` on removals; there is no permanent `-EIO` failure mode.  A
  firmware restart (`rmmod ath10k_snoc; modprobe ath10k_snoc`, or a reboot)
  restores the fast path.
* WoWLAN is armed by default in the device package.  The disconnect wake works on
  hardware, and with 0078 the AP's group rekey no longer triggers it; the
  magic-packet wake and a full night with Wi-Fi on are not yet verified.  Disable it (`iw phy0 wowlan disable`) before any suspend trial.

### Rollback

Boot the previous image, or in place: stop the modem path
(`systemctl mask guacamole-mpss.service`), `rmmod guacamole_mss_vote` (restores
corner 9), and/or `systemctl disable --now guacamole-mss-release.service`.  No
partition, slot, DTBO or vbmeta change is part of this topic.
