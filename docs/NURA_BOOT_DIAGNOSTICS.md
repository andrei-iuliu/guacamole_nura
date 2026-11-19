# Nura bounded boot diagnostics (journal-only)

**Status: source-ready; runtime unverified.** The collector and units are in the
source tree, but nothing has been installed, enabled or run on the phone. This
document is the rc3 integration description; a future user build must install
the files.

## What this is

A tiny systemd oneshot snapshot that writes a bounded, non-personal view of
startup state to **stdout**, i.e. to the existing persistent journal. It is not
a second log store, a crash dump or a probe framework.

It runs twice per boot:

- `nura-boot-diagnostics.service` at `multi-user.target` (early);
- `nura-boot-diagnostics.timer` at monotonic boot +3 min (late).

The same unit is triggered both times. Journald records `_BOOT_ID` and
`__MONOTONIC_TIMESTAMP` for every line, so the script does not print a boot ID,
uptime or sequence. The kernel journal is already persisted by journald; this
script does not copy, filter or retain it.

There is **no StateDirectory**, no `/var/lib` output, no boot-ID directory, no
boot-sequence counter, no retention and no journal copy. The source collector
is about 100 lines; the only test hook is `NURA_BOOT_DIAGNOSTICS_ROOT` (a
temporary fake `/proc`+/`sys` prefix used by the rootless tests; leave it unset
on the phone).

## What is captured

Output markers:

```
nura-boot-diagnostics: begin
nura-boot-diagnostics: failed-units
<systemctl --failed --no-legend --no-pager output, verbatim>
nura-boot-diagnostics: sysfs
<path=value lines>
nura-boot-diagnostics: end
```

`systemctl --failed` output is **not** passed through any kernel-interest or
identifier filter; the task explicitly requires that. It is bounded only by
line/byte caps. If `systemctl` is missing, the snapshot contains
`[MISSING] systemctl`; if the tool runs but lists nothing, it contains
`[NONE-OR-UNAVAILABLE]`.

The exact sysfs allowlist is:

- `/proc/asound/cards`
- `/sys/class/drm/card[0-9]/status` and `card[0-9][0-9]/status`
- `/sys/class/drm/card*-*/status` (connectors)
- `/sys/class/udc/*/state` and `current_speed`
- `/sys/class/remoteproc/remoteproc*/state`, `name`, `firmware`
- `/sys/class/typec/port*/data_role`, `power_role`

Values are `head -c 512` and at most 64 entries. No `/sys/kernel/debug`, no
debugfs, no register sweep, no i2c/SMBus probing, no `/proc/<pid>` and no
process command lines.

## Bounds

| Bound | Value |
|---|---:|
| Total stdout | 16 KiB / 120 lines |
| failed-units section | 80 lines / 8 KiB |
| Per sysfs value | 512 bytes |
| Sysfs entries | 64 |
| `systemctl --failed` time | 5 s (`timeout`) |
| Unit start time | 15 s (`TimeoutStartSec`) |

The pipeline applies a final byte/line cap to stdout, so the journal receives a
bounded entry even if a tool misbehaves. Missing tools and unreadable paths do
not fail the unit; missing `head`/`tr` is reported and the unit exits 0.

## Journald persistence and size cap

Inspection of the rc2 candidate rootfs (read-only):

- `/usr/lib/systemd/journald.conf.d/10-postmarketos.conf` ships
  `[Journal]` / `SystemMaxUse=1G`.
- `/etc/systemd/journald.conf` is absent; the vendor drop-in is the effective
  cap.
- `/var/log/journal` exists, and journald's default `Storage=auto` therefore
  persists the journal.
- The rootfs has systemd 262.2-r0.

**No new journald drop-in is needed.** Native persistence and the shipped 1 GiB
cap are sufficient; the collector's own output is at most 16 KiB per run. A
smaller global cap would be a separate decision and is not part of this
change.

Query the snapshots with:

```sh
journalctl -u nura-boot-diagnostics.service -b -o short-monotonic
journalctl -k -b -o short-monotonic
```

`journalctl -o verbose` shows `_BOOT_ID` and `__MONOTONIC_TIMESTAMP` without the
script having to record them. Use `journalctl --list-boots` to find a previous
boot.

## Failure modes and evidence limits

- **Unlock freeze:** the actual reported rc2 unlock failure is a **freeze**;
  the phone stops responding and requires holding **Power+VolumeUp** to force a
  reset. It is **not a spontaneous reboot**. A freeze can prevent a clean
  shutdown and may lose the last journal lines; the forced reset is not a
  normal log flush.
- **Later freeze:** the early and 3-minute snapshots capture startup state, not
  the freeze moment. Diagnosing a later freeze needs the journal from that boot
  and pstore/ramoops evidence. pstore is a separate mechanism and may be empty;
  this collector does not read or replace it.
- **NBD loss:** if the rootfs is served over NBD and the cable drops, the rootfs
  (and journald) can become unavailable, so logging can block or fail and this
  snapshot cannot be written. This collector does not survive cable loss.
- **Fresh serve discards the writable cache:** the NBD workflow serves a
  writable copy of the chroot image. A fresh serve replaces that copy, so
  journal entries and snapshots written in the served rootfs are discarded.
  Copy evidence out before re-serving. A persistent internal rootfs would avoid
  this, but internal-storage boot is deferred.

## Privacy

- No journal copy, no kernel log filtering, no domain files.
- `systemctl --failed` is printed verbatim (unit names and descriptions only;
  no command lines) because the task forbids running it through the
  kernel-interest filter.
- The sysfs allowlist is fixed and contains no serials, MACs, IMEI/ICCID/IMSI,
  SSIDs, personal files, recordings or credentials.
- No `/proc/<pid>/environ`, `/proc/<pid>/cmdline`, process list, `lsusb`,
  `udevadm`, debugfs, registers or `/sys/kernel/debug`.
- The script writes no files; stdout is the journal.

## Installing in a future user build (parent-owned)

No APKBUILD, rootfs-preparation or plasma-check file is edited by this change.
To integrate:

1. Make the collector available in the package `$srcdir`: copy
   `scripts/collect_boot_diagnostics.sh` to
   `device/testing/device-oneplus-guacamole/nura-boot-diagnostics` (or use a
   relative symlink; use a copy if abuild stores the symlink).
2. Add to `source=`:
   `nura-boot-diagnostics nura-boot-diagnostics.service
   nura-boot-diagnostics.timer 81-nura-boot-diagnostics.preset`.
3. In `package()`:
   ```sh
   install -Dm755 "$srcdir/nura-boot-diagnostics" \
       "$pkgdir/usr/bin/nura-boot-diagnostics"
   install -Dm644 "$srcdir/nura-boot-diagnostics.service" \
       "$pkgdir/usr/lib/systemd/system/nura-boot-diagnostics.service"
   install -Dm644 "$srcdir/nura-boot-diagnostics.timer" \
       "$pkgdir/usr/lib/systemd/system/nura-boot-diagnostics.timer"
   install -Dm644 "$srcdir/81-nura-boot-diagnostics.preset" \
       "$pkgdir/usr/lib/systemd/system-preset/81-nura-boot-diagnostics.preset"
   ```
4. Bump `pkgrel`, run `pmbootstrap checksum device-oneplus-guacamole`, and keep
   the repo and pmaports copies identical.
5. In the user build, `systemctl preset-all` (or
   `systemctl enable nura-boot-diagnostics.timer`). The service also has
   `WantedBy=multi-user.target`; if only the late timer is wanted, remove the
   service's `[Install]` section and do not enable the service in the preset.
6. Verify:
   ```sh
   systemctl status nura-boot-diagnostics.service
   journalctl -u nura-boot-diagnostics.service -b -o short-monotonic
   systemctl list-timers nura-boot-diagnostics.timer
   ```

No journald drop-in and no `StateDirectory` are required.

## Tests

```sh
sh -n scripts/collect_boot_diagnostics.sh
python3 -m unittest tests.test_boot_diagnostics -v
```

The tests use a temporary fake `/proc`/`/sys` tree and a fake `systemctl`; they
do not read the real journal, start a unit, use the phone or require root. They
cover verbatim failed-unit output (including a `serial-getty` line that a
kernel-interest filter would have removed), the exact sysfs allowlist, output
and time bounds, missing tools, no file/state output, and the unit/timer/preset.
