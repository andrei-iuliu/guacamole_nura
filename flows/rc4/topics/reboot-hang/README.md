# rc4 topic: reboots that end in a black screen

Status (2026-11-23): **worked around, root cause open.** Device package
`4-r22` and kernel patch `0079` (rc4r) are installed; the kernel-side bugs are
still to be fixed. Investigation notes, the three source-reading reports and
the raw evidence are local only, in `notes/reboot-hang-2026-11-23/`.

## Symptom

Some reboots (Plasma's Restart, or `systemctl reboot`) never come back: the
panel shows garbled colours, goes dark, the bootloader's orange "unlocked"
screen never appears and no USB device shows up on the host (no Linux gadget,
no fastboot, no 05c6:900e crashdump). Only a forced key reset or a power cycle
recovers it. Cold boots always work. About half of the reboots on the morning of
2026-11-23 failed.

## Two failure points

**A. Abrupt reset at the start of shutdown.** The journal ends at
"System is rebooting" / "The system will reboot now!", and the pstore console
ends a fraction of a second later, with no panic and no
"reboot: Restarting system". PM8150 PON registers (`0x8C2`, `0x8C4`, `0x8C5`)
read PS_HOLD warm reset, not a power fault, but a normal reboot looks the same
there. Seen three times; in all three the speaker amps (`tfa9874`) were
toggling for the logout sound, and in two of them the fbdev console was
re-initialising the panel at 90 Hz (KWin at 60 Hz had just exited). Usually the
phone then boots normally by itself.

**B. Hang after userspace has shut down.** systemd's shutdown completes
("Journal stopped"), then garbled panel, black, nothing. The hardware watchdog
was armed (30 s) and still did not bring the phone back, so the black screen is
most likely after a reset (bootloader stuck on a panel left in a bad state).
Seen only with the panel lit when the kernel's final shutdown began.

## What is installed

| Piece | What it does |
|---|---|
| device `4-r22`: `/etc/systemd/system.conf.d/91-nura-watchdog.conf` | `RuntimeWatchdogSec=20s`, `RebootWatchdogSec=30s`. The default 10 min reboot watchdog is beyond qcom_wdt's ~32 s limit and was rejected, so nothing was armed. |
| device `4-r22`: `/usr/lib/tmpfiles.d/91-nura-fbdev-restore.conf` | `drm_kms_helper.fbdev_emulation=N` after boot: the fbdev console no longer re-initialises the panel when the compositor exits. |
| device `4-r22`: user unit `nura-panel-off-on-exit` (Plasma) | `kscreen-doctor --dpms off` when the session stops, before KWin, so the panel is off during the kernel's final shutdown. |
| kernel `0079` (rc4r, on `boot_b`) | 60 Hz is the panel's preferred mode, so the console and KWin start in the same mode and no 60 -> 90 Hz modeset happens at shutdown. |

Test results: with the panel off before the kernel's final shutdown, 9 of 9
reboots got past userspace shutdown cleanly (4 by hand, 2 through the user
unit, 3 on r22); without it, roughly half failed. That covers failure B only:
failure A struck once (10:17) with the r22 user unit installed, before the unit
had run, so r22 does not prevent A. Small samples; not proof.

## Findings from the source reading (2026-11-23)

- **The 60 and 90 Hz modes share one pixel clock** (vtotal 4704 vs 3136); a
  refresh switch changes no clock, PLL, OPP corner or bandwidth vote. The
  panel driver reads the new CRTC state at prepare (state is swapped before
  commit_tail), so its 0x20/0x30 byte was right; the 0004 handover's fallback
  suspicion does not explain the resets. What a mode change does force is a
  full panel power cycle (rails off/on, reset, about 45 DCS commands), the same
  as every DPMS-off/on. The 2026-11-21 journal had 3 resets within seconds of 24
  panel inits, all at 90 Hz.
- **Missing upstream fix:** `dispcc-sm8250.c` (also used for SM8150) lacks
  `CLK_OPS_PARENT_ENABLE` on `disp_cc_mdss_pclk0/1_clk_src`; upstream added it
  in `acf7a91d0b0e` ("clk: qcom: dispcc-sm8250: Enable parents for pixel
  clocks"). That is the source of the boot warning
  `disp_cc_mdss_pclk0_clk_src: rcg didn't update its configuration`. Not yet
  verified against the upstream commit; candidate for rc4s.
- **Shutdown order hypothesis (unconfirmed):** if `apps_smmu` is shut down
  (translation bypassed) before the display, the panel's DCS off/sleep payloads
  are DMA'd through an untranslated address: garbled panel, possibly a secure
  fault. fw_devlink should order the display first; the order has not been
  observed yet because pstore loses the end of every shutdown.
- **Reboot path:** a plain reboot writes no restart reason; PSCI SYSTEM_RESET is
  the restart handler; the TCSR download-mode cookie is cleared at qcom_scm
  probe and shutdown. qcom_wdt stays armed after systemd-shutdown.
- **pstore misses the end of shutdown** even on clean reboots (ramoops
  `max_reason` defaults to OOPS, and the shutdown kmsg dump runs after
  `device_shutdown()`).

## Next steps

1. rc4s kernel: backport `acf7a91d0b0e` (after checking it upstream), make
   ramoops keep the shutdown log, log the panel init stages; then reboot tests.
2. Observe the device shutdown order (initcall_debug) once pstore keeps it.
3. Failure A: test whether it needs the logout sound (audio teardown) or the
   panel power cycle.
4. Force a watchdog bite on purpose to confirm it resets the SoC.
