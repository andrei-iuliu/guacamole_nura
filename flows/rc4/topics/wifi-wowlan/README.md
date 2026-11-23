# rc4 topic: Wi-Fi WoWLAN and the GTK rekey

## Symptom

The OnePlus 7 Pro running Plasma Mobile woke from suspend (s2idle) at random
and lit its screen. Seen at 17:38:15 and 20:38:15 on 2026-11-22.

## Cause

The device package arms WoWLAN for NetworkManager (magic packet and
disconnect, from `90-wifi-wowlan.conf`). When the phone is suspended in that
state, nobody answers the access point's hourly group-key (GTK) rekey, which
happens at :38 past each hour on the user's AP. The AP deauthenticates the
phone with reason 16 (`GROUP_KEY_HANDSHAKE_TIMEOUT`), and the WoWLAN disconnect
trigger wakes it. KWin turns the panel on after every resume.

Evidence:

* While the phone was awake, the rekeys at 15:38, 18:38, 19:38 and 21:38
  completed normally.
* A control night with Wi-Fi off (22:26 to 06:28) had no wakes.

## Fix

ath10k never implemented mac80211's `set_rekey_data`, so the WCN3990 firmware
(which advertises `WMI_SERVICE_GTK_OFFLOAD`) never received the KCK and KEK.
Kernel patch `0078-wifi-ath10k-offload-gtk-rekey-during-wow.patch` adds it:

* `set_rekey_data` stores the KCK, KEK and replay counter per vif.
  Disassociation wipes them.
* On WoW suspend, GTK_OFFLOAD enable is sent to each associated station vif.
* On resume, a status request is sent, then disable. The firmware's replay
  counter is passed to the supplicant with `ieee80211_gtk_rekey_notify`.
* `WOW_GTK_ERR_EVENT` is armed alongside the disconnect trigger.
* `WIPHY_WOWLAN_SUPPORTS_GTK_REKEY` is advertised.

The wire format was cross-checked against ath11k and Qualcomm's Android driver
(qcacld-3.0). The research write-up is at
`/home/alaghar/Docs/ath10k-gtk-rekey-offload-research.md` (outside this
repository).

The patch is in the "rc4q" candidate: `flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/`,
APKBUILD `pkgrel=19`.

## Verification (2026-11-23)

* With the patched modules, the phone suspended from 07:15 to 08:03 and slept
  through the 07:38 rekey with no wake. It was still associated when the user
  woke it with the power key.
* The log showed `wow gtk rekey offload enabled on 1 vdev(s)` and
  `disabled on 1 vdev(s)`, and no `GROUP_KEY_HANDSHAKE_TIMEOUT`.
* The rc4q kernel package `linux-postmarketos-qcom-sm8150-7.0.0_p20261123081257-r0`
  was built and installed with `apk add`. `apk audit` shows no ath10k files
  left over.
* `Module.symvers` and BTF are identical to rc4p, so `boot_b` was not
  reflashed.

## Still open

* A full night with Wi-Fi on, to cover about 8 rekeys.
* The magic-packet wake trigger has not been tested.
* Cable plug and unplug also wake the phone. That is expected and not
  addressed here.
