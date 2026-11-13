# 0028: ath10k: snoc: complete key operations on the WMI acknowledgement

- **Patch:** `kernel/0028-ath10k-snoc-wmi-key-ack.patch`. Files under `drivers/net/wireless/ath/ath10k/`: `core.h`, `core.c`, `mac.c`, `wmi-tlv.c`, `wmi-tlv.h`.
- **Fixes:** a 3 s `-110` timeout on every Wi-Fi key removal (suspend entry, disconnect, roam).
- **Not a crash fix:** it shortens suspend entry and removes error noise. Lowest priority of the four.
- **Risk:** medium-low. Only SNOC + TLV firmware takes the new path, and every other bus keeps the HTT wait.

## Problem

`ath10k_install_key()` waits on `install_key_done`, which is only signalled by HTT `SEC_IND`. On WCN3990, a key **removal** produces `WMI_VDEV_INSTALL_KEY_COMPLETE_EVENTID` (2.16 ms after the command) but no HTT `SEC_IND`. So every removal waits the full 3 s and returns `-ETIMEDOUT` (-110). The existing WMI handler only prints a debug line.

OM traced this with `debug_mask=10` (WMI + HTT, no key dumps):
- `1779.306663` WMI key removal sent
- `1779.308821` `WMI_VDEV_INSTALL_KEY_COMPLETE_EVENTID`
- `1782.328943` `-110`

## Fix

For `ATH10K_BUS_SNOC` with TLV WMI only:
- Record the pending operation (vdev, peer MAC, key index, flags) under `data_lock`.
- Complete it from a new `ath10k_wmi_tlv_key_complete()`. That function parses the TLV with a minimum-length policy and checks the firmware `status`.
- Removals finish on the WMI ack. Installs still also wait for HTT `SEC_IND`, which is needed for PN setup.
- If a wait times out, the sequence is marked desynced until the next `ath10k_core_start()`, so a late reply can never satisfy a later operation. This answers the objection raised on the upstream RFC.

OM measurement: awake disconnect **3.399 s → 0.391 s**, no `-110`, HTTPS fine after reload and after suspend.

## Sources

- OM write-up: [`docs/wifi-key-ack-20260917.md`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/wifi-key-ack-20260917.md) ("Evidence and diagnosis" L7; "Live results" L49)
- OM patch: [`kernel/radio/ath10k-snoc-key-ack.patch`](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/radio/ath10k-snoc-key-ack.patch)
- OM first sighting during suspend: [`docs/suspend-20260917.md` "Native5 unplugged trial passed" (L182)](https://github.com/Cube-Oakley/omarchy-mobile/blob/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/docs/suspend-20260917.md#L182)
- Related upstream discussion, as cited by OM:
  - [RFC skipping removal waits](https://www.mail-archive.com/ath10k%40lists.infradead.org/msg17566.html)
  - [review objection about late replies](https://www.mail-archive.com/ath10k%40lists.infradead.org/msg17572.html)
  - payload layout from Qualcomm [`wmi_unified.h`](https://android.googlesource.com/kernel/msm.git/+/f6da9680a25d881411386981a92a35bfa4c5b049/drivers/staging/qcacld-2.0/CORE/SERVICES/COMMON/wmi_unified.h)

## Applies to

- 6.17 and 7.0: clean. GN's `0009` touches `thermal.c` only, so they don't overlap.
- Changes from OM:
  - Paths rebased from `ath10k/` (an out-of-tree module build) to `drivers/net/wireless/ath/ath10k/`.
  - OM's whitespace-only change of the `ath10k_install_key()` signature from tabs to spaces was dropped.
  - Logic unchanged.
- OM had to replace `ath10k_core` and `ath10k_snoc` together because `struct ath10k` changed. As an in-tree patch that happens automatically.

## Verify

- `nmcli device disconnect wlan0` takes well under 1 s, and dmesg has no `failed to remove key … -110` / `ath10k_snoc … key` timeout.
- Reconnect works, and `ping`/HTTPS work over wlan0.
- One suspend/resume cycle: no `-110` during entry.
- With `debug_mask=0x10` (WMI), see `key ack vdev … status 0` for both installs and removals.

## Interaction with WoWLAN

With WoWLAN armed (GN default), mac80211 suspends via WoW and may not remove keys at all, so this patch matters most for the non-WoW path and for roaming. See `00-diagnose.md` section 4.
