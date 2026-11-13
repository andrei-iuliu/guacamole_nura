# Device package integration (device-oneplus-guacamole)

The rc4 device package lives at `device/testing/device-oneplus-guacamole/`.
It is systemd-first: the modem is started by `guacamole-mpss-start`, shared by
the opt-in `guacamole-mpss.service` and the OpenRC initd.  The supplied
`device-package.diff` targeted the 6.17 package (pkgrel 11, OpenRC-only
`guacamole-mpss.initd`); do not apply it.

All paths are relative to the repository root.

## 1. Install the helper and the unit (unit stays disabled)

```sh
install -m 0755 flows/rc4/topics/suspend-fixes/fragments/device/guacamole-mss-release \
	device/testing/device-oneplus-guacamole/guacamole-mss-release
install -m 0644 flows/rc4/topics/suspend-fixes/fragments/device/guacamole-mss-release.service \
	device/testing/device-oneplus-guacamole/guacamole-mss-release.service
```

Add to `source=` in `device/testing/device-oneplus-guacamole/APKBUILD`
(tab-indented, next to the other `guacamole-*` entries):

```
	guacamole-mss-release
	guacamole-mss-release.service
```

Add to `package()`:

```
	# Modem rail: opt-in release of the AP's mss.lvl vote (kernel 0044).
	install -Dm755 "$srcdir/guacamole-mss-release" \
		"$pkgdir/usr/libexec/guacamole-mss-release"
	install -Dm644 "$srcdir/guacamole-mss-release.service" \
		"$pkgdir/usr/lib/systemd/system/guacamole-mss-release.service"
```

Then `pmbootstrap checksum device-oneplus-guacamole` (or `abuild checksum`)
and bump `pkgrel`.

**Do not add the unit to `80-guacamole.preset`.**  It is the opt-in, and it
must not run automatically until the handover precondition has been checked on
the phone (topic README section 6).  Enabling it on the phone is the opt-in:

```sh
systemctl enable --now guacamole-mss-release.service
```

There is deliberately **no** `guacamole-mpss-start` hook: an automatic release
at modem start cannot prove that the proxy MSS power domains have been handed
over yet, and the handover path itself re-sends rpmhpd's clamped corner.

## 2. Running the release by hand

```sh
sudo /usr/libexec/guacamole-mss-release
```

The script waits for the modem, then reads the `mss` proxy device
(`/sys/devices/genpd:1:4080000.remoteproc/power/runtime_status`).  Handover is
accepted only on the exact value `suspended`; `active` and `suspending` are
waited on for at most `MSS_HANDOVER_TIMEOUT`, and `resuming`, empty or
unreadable values, unknown strings and a timeout all refuse the write.  The
modem is re-checked as `running` during the wait and immediately before the
write.  `MSS_ALLOW_UNVERIFIED_HANDOVER=1` forces an unobserved handover for a
one-off trial; it never bypasses the modem checks.  The journal lines say
which case happened.

## 3. Optional and gated: standalone unit

The unit is the standalone integration; there is nothing else to enable.  Keep
it disabled on a boot where the modem is not used: the script waits up to
120 s for the modem and then fails.

## 4. Optional and gated: modem SSR off (diagnosis only)

```sh
install -m 0644 flows/rc4/topics/suspend-fixes/fragments/device/90-guacamole-modem-recovery.rules \
	device/testing/device-oneplus-guacamole/90-guacamole-modem-recovery.rules
```

plus `source=` / install lines.  With this rule a modem crash leaves the modem
(and WCN3990 Wi-Fi, whose firmware runs on the MPSS) down until reboot; it
exists to tell "the kernel died recovering the modem" apart from a real
freeze.  Do not ship it by default.

## 5. Not needed

* No systemd preset entry, no modules-load file, no OpenRC service.  The
  module is loaded by the release script only when the unit is enabled.
* No DT change: `sm8150.dtsi` already has `power-domain-names = "cx", "mss"`
  on the `qcom,sm8150-mpss-pas` node and `compatible = "oneplus,guacamole"` is
  already in the board DTS.
