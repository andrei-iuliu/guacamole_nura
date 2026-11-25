# Self-powered USB-C dock: no USB-PD as sink — investigation 2026-11-15

> Restored on 2026-11-23 from the older fork checkout, where this topic was
> never merged. The candidate patches are numbered `0075`/`0076`, but the
> recipe now uses those numbers for other patches, so renumber them before
> trying them again. The state below is as of rc4l.

**Status: open. The candidate patches here did not fix it and are not in the
kernel recipe (rc4l stays pkgrel 15).** Workaround: use dock A without its
power supply (phone sources, DP and docked mode work), or dock B, which
works with charging and video on rc4l/rc4m.

## Hardware

- **Dock A**: multi-port USB-C hub with captive cable, HDMI, RJ45,
  **PD pass-through** — the PSU is a USB-C PD laptop
  charger on the hub's power port; the hub is sink towards it and source
  towards the host, which is why it requests PR_Swap as soon as it has power.
  The PD controller chip is not identified.
- Control: **dock B** (another USB-C hub) on the **same ~100 W PD charger** works on rc4l and
  rc4m (charging + DP). The charger is therefore not the cause; the failure is
  specific to dock A's PD controller talking to the PM8150B PD PHY with
  the phone as sink.

## Symptoms

- Dock A **with** its PSU: if the phone attaches as source, DP alt mode is
  entered (docked icon flashes), then the dock sends PR_Swap; the phone sends
  PS_RDY but never receives the dock's PS_RDY → ERROR_RECOVERY → PORT_RESET.
- Then (and on direct sink attaches) the phone never receives a single PD
  message from the dock: SNK_WAIT_CAPABILITIES times out, 2 hard resets, then
  SNK_READY without PD (BC1.2 charging only, no DP).
- A USB-PD wall charger in the same sink role works (Source_Capabilities at
  174 ms, 5 V/3 A contract). The same dock's messages are received in the
  source role.

## Evidence (logs in `.work/rc4l-build/`, `.work/pd-wait620/`, `.work/pd-wait2000/`, `.work/rc4m-build/`)

| Test | Result |
| --- | --- |
| `sink-wait-cap-time-ms` 620 (DT) | no message |
| `sink-wait-cap-time-ms` 2000 (DT, diagnostic) | no message; `msg-rx-discarded` = exactly 1 per attach + 1 per hard reset |
| rc4m kernel with `candidate/0075` (PD RX on during the hard-reset wait, early Source_Caps handled) | no message although RX was enabled; discards continue |

The PM8150B PD PHY raises `msg-rx-discarded` for the dock's messages **even
with RX enabled** (`/proc/interrupts`; mainline ignores that IRQ). So this is
not a tcpm timing race. The PHY rejects messages from this source in the sink
role for a reason not yet identified.

Facts collected:
- Lineage (downstream `qpnp-pdphy`) waits 500 ms for caps, up to 3 hard resets,
  re-opens the PHY per attach (EN_CONTROL off/on, frame filter, RX_ACK = 0) and
  configures MSG_CONFIG spec rev PD 2.0 — mainline also uses PD 2.0.
- PD PHY supply is PM8150 L2 in both trees.
- Hard resets are transmitted (`sig-tx` IRQ); no `sig-rx`.

## Next steps (not started)

1. On a discard IRQ, dump the PD PHY RX registers (RX status/size/buffer,
   MSG_CONFIG, FRAME_FILTER, RX_ACKNOWLEDGE) with a temporary dev_info patch;
   compare charger vs dock A.
2. Compare the sink-attach PHY sequence with downstream `pd_phy_open()`
   (EN_CONTROL reset, frame filter re-write) — e.g. re-enable the PHY on
   every sink attach.
3. Check whether dock A sends on SOP' / uses a different BMC rate
   (scope or a PD analyser would settle this).

## Candidate patches (not applied)

- `candidate/0075-usb-typec-tcpm-listen-during-sink-hard-reset-wait.patch` —
  built and hardware-tested as rc4m (`.work/rc4m-build`); no effect on the
  dock A, no regression with dock B.
- `candidate/0076-usb-typec-qcom-pmic-typec-report-discarded-pd-rx.patch` —
  dev_dbg only; compiled out here (`CONFIG_DYNAMIC_DEBUG` is off).
