# Nura Plasma Mobile release candidate rc1

> Historical: this page documents rc1. The current source prepares
> `nura-plasma-rc2` in [NURA_RC2.md](NURA_RC2.md). Existing rc1 artifacts and
> rollback directories are preserved unchanged.

Status: **source-ready; binary not built. Ready-to-test after the user-run build and host artifact checks.**

This checkout prepares `nura-plasma-rc1` source and user-run commands only. No
privileged build, chroot, mount, service change, server, SSH, fastboot, flash or
phone action was performed while preparing it. After the build succeeds and the
host-side artifact checks pass, the candidate is ready to test. Hardware
acceptance is the outcome of those tests, not a prerequisite to calling it
ready-to-test.

The kernel identity is deliberately unchanged:

| Item | Value |
| --- | --- |
| Kernel release / uname | `7.0.0-sm8150-rear1` |
| Release series | `nura-plasma` |
| Release candidate | `nura-plasma-rc1` |
| Artifact label | `nura-plasma-rc1-oneplus-guacamole` |
| Candidate state | `.work/plasma-mobile` |
| Candidate output | `build/plasma-mobile` |

`rc1` is release metadata and output naming, not a kernel, profile or module
rename. Existing rear-camera/Phosh manifests and the working Phosh paths are
unchanged. The next candidate changes the release-candidate label to
`nura-plasma-rc2`; see [Next release candidate](#next-release-candidate).

## 1. What changed in this source preparation

### 1.1 Release identity is separate from the kernel uname

`scripts/check_plasma_candidate.py` owns `RELEASE` (`7.0.0-sm8150-rear1`) and
`RELEASE_CANDIDATE` (`nura-plasma-rc1`). The candidate receipt,
`DELIVERY.json` and `packaging-inputs.json` now also record:

```json
"release_series": "nura-plasma",
"release_candidate": "nura-plasma-rc1",
"artifact_name": "nura-plasma-rc1-oneplus-guacamole"
```

`scripts/rootfs_prepare_plasma.sh` reads the label from that helper, so the
log, rollback directory and preview output use the same rc1 name. Delivery
scripts still read the unchanged `release` field for the kernel/runtime
identity; the new fields are additive and do not change old manifests.

### 1.2 A previous candidate now has a real, servable rollback

Before any build step can replace the chroot rootfs, `--build` copies a
coherent snapshot to:

```text
build/plasma-mobile/rollback-nura-plasma-rc1-<YYYYmmdd-HHMMSS>/
```

The snapshot contains:

- `rootfs.img` — the actual previous NBD rootfs image from
  `.work/plasma-mobile/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img`;
- `rootfs-initramfs` and `rootfs-export/` — the initramfs and export metadata
  that the previous manifest names when serving that rootfs;
- `boot.img`, `boot-initramfs`, `DELIVERY.json`, `packaging-inputs.json`,
  `APK-SHA256SUMS`;
- the key kernel-package files the manifest/boot checks read (`vmlinuz`, DTB,
  CCI module, `kernel.release`), `Image`, and the temporary-DTBO wrapper;
- `candidate-state.json`, `ROLLBACK.json`, `RESTORE.txt` and
  `ROLLBACK-SHA256SUMS`.

Safety rules in the build path:

- a live listener on TCP port 10001 is refused before preservation;
- active mounts under the Plasma state/output are refused;
- symlinked output, rootfs, export or preserved file paths are refused;
- the rootfs is copied only when the user runs `--build`, and only after the
  candidate server is stopped; the live rootfs is **not hashed in place**;
- the copy is staged under a `.partial` name, checksummed, and only then
  renamed into place; a failed or partial copy aborts before any existing file
  is changed;
- free space is checked conservatively before the copy;
- the rootfs image identity (`device:inode:size:mtime`) is compared before and
  after the copy; a writer that changes it aborts the build;
- a previous `DELIVERY.json` without its rootfs or rootfs initramfs aborts the
  build instead of silently overwriting the only rollback;
- a failed later build leaves the rollback directory intact.

This is intentionally not a boot-image-only rollback: restoring only the
unchanged kernel would leave the new rootfs in place.

### 1.3 Plasma Camera change is preserved

The existing Plasma Camera source change is retained: the candidate installs
`plasma-camera` instead of GNOME Snapshot, removes a previously reused Snapshot
package once, and verifies the app, its desktop file, Qt Multimedia backend and
the pinned system libcamera 0.8 ABI. The prior rear-camera/Phosh artifacts and
the `7.0.0-sm8150-rear1` boot chain are not renamed or rebuilt by this work.

## 2. Build command (user-run, normal user, Bash)

Exact command for this checkout:

```bash
cd runtime-workspace && bash scripts/rootfs_prepare_plasma.sh --build
```

The read-only preflight is optional but recommended:

```bash
cd runtime-workspace && bash scripts/rootfs_prepare_plasma.sh --check-only
```

`--build` invokes pmbootstrap and therefore sudo/chroots internally. It does
not flash, reboot or touch the phone.

**Do not call this a binary candidate yet.** It becomes binary-ready only after
the user-run build succeeds and the resulting manifest/output exists. Even then
it is not hardware-ready until the acceptance checks pass.

## 3. Serve, boot and SSH

`ssh_phone.sh` keeps host-key pins separately for each successful boot recorded
by `plasma_boot.sh`: fresh NBD copies generate new SSH host keys. First contact
with each boot asks you to accept its fingerprint; a change within that boot
is refused. This does not independently authenticate the first connection.
An explicit `--forget-host-key` resets only the current boot's project-local
entry, never the global known_hosts file.

Normal `plasma_serve.sh` and `rootfs_prepare_plasma.sh --build`/`--restore`
automatically run the Plasma workspace's `pmbootstrap shutdown` when leftover
mounts are present, then verify the mounts are gone. This may request sudo.
Stop other builds/chroots first. If the candidate port is busy, cleanup only
stops its NBD server and pmbootstrap restart loop after confirming the last
booted phone's serial is now in Android (authorized ADB) or fastboot, and
verifying the listener's executable, export, chroot and pmbootstrap workspace.
A missing phone or failed SSH connection is insufficient evidence. Unrelated
servers and baseline ports are refused. Cleanup never force-unmounts.
Verified stale processes receive SIGTERM first. If they remain after five
seconds, ownership is checked again before SIGKILL is sent through their
original pidfds; a different listener is never killed as a fallback.
Plain `--check`/`--check-only` stay read-only.
To clean up leftover mounts and validate without starting the server, use:

```fish
bash scripts/plasma_serve.sh --cleanup --check
```

Stop any candidate server before a build or rollback. The Plasma path always
uses port 10001. The current `camera_serve.sh` also uses 10001 for the
rear-camera profile and rejects 9999/10000; do not assume that the old
port-10000 reference identifies a running baseline. Check the actual listener
before serving either profile.

```bash
# terminal 1: validate, then serve the stopped cache
bash scripts/plasma_serve.sh --cleanup --check
bash scripts/plasma_serve.sh

# terminal 2: temporary boot only (no flash)
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot

# after userspace is up
bash scripts/ssh_phone.sh
```

For fish, use the same Bash wrappers directly (fish can run Bash scripts):

```fish
bash scripts/plasma_serve.sh --cleanup --check
bash scripts/plasma_serve.sh
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
bash scripts/ssh_phone.sh
```

For a non-default USB interface:

```fish
env NURA_USB_IFACE=enp19s0u4u4 bash scripts/plasma_serve.sh
```

`plasma_boot.sh` fixes the profile to `plasma-mobile`; it refuses a
`--profile` override and has no flash path.

## 4. Roll back to the preserved candidate

The rollback directory is printed at the end of `--build`. It is also listed
in `build/plasma-mobile/rollback-nura-plasma-rc1-*/RESTORE.txt`.

```bash
# 1. Ctrl-C the Plasma serve terminal (or stop only the port-10001 process).
# 2. Boot the phone out of the candidate if it is running.
# 3. Restore the old rootfs, initramfs, export, boot image and manifests:
bash scripts/rootfs_prepare_plasma.sh --restore build/plasma-mobile/rollback-nura-plasma-rc1-<stamp>

# 4. Validate and refresh the stopped NBD cache, then boot the old candidate:
bash scripts/plasma_serve.sh --check
bash scripts/plasma_serve.sh --replace
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
```

`--restore` verifies `ROLLBACK-SHA256SUMS` before changing anything, moves the
current manifests, rootfs, export and key kernel files aside to
`.work/plasma-mobile/rootfs/restore-before-<stamp>/`, and keeps the rollback
directory for another attempt. It uses `sudo` only to preserve/install the
rootfs image in pmbootstrap's protected rootfs directory; it does not change
that directory's ownership or permissions. A tampered or incomplete rollback
is refused before any current file is moved. `--replace` is explicit:
the restored rootfs must be copied to the stopped served-cache path; the script
refuses while port 10001 is live.

## 5. Clock: 1970, USB NBD and NTP

### Evidence

The 2026-11-10 boot report observed `Sat Sep 19 22:40:26 UTC 1970` while the
kernel release was `7.0.0-sm8150-rear1`. The journal showed the PMIC RTC setting
the system clock, no default route (only `172.16.0.0/16 dev usb0`; `wlan0` was
DOWN), and `systemd-timesyncd` unable to resolve the NTP pool. A timesync
service running without a route cannot correct the clock.

`usb0` is the point-to-point USB/NBD link. It is **not** an internet uplink and
does not provide a default route. Wi-Fi association or another real uplink is
required before NTP can work. This source preparation does not add a network
profile, Wi-Fi credentials, a fake route or an RTC write.

### Bounded user-run check (phone)

Run over SSH after userspace is up. Replace the SSID/password locally; do not
commit credentials.

```sh
date -u
ip -4 route
nmcli radio wifi
nmcli device status
nmcli device wifi list
timeout 60 sudo nmcli device wifi connect "<SSID>" password "<password>"
ip -4 route
getent hosts pool.ntp.org
sudo systemctl restart systemd-timesyncd
timeout 90 sh -c 'until [ "$(timedatectl show -p NTPSynchronized --value)" = yes ]; do sleep 2; done'
timedatectl show -p NTPSynchronized -p TimeUSec
date -u
```

Pass condition: a real default route exists, `pool.ntp.org` resolves, and
`date -u` is no longer 1970 after `NTPSynchronized=yes`. If Wi-Fi does not
associate, the clock remains untested/incorrect; that is not evidence that the
NTP service is the root cause.

Do **not** run `hwclock --systohc` (or any RTC write) while the clock is wrong.
This candidate deliberately does not persist a time to the RTC. If a correct
time is obtained and persistence is desired, treat it as a separate guarded
experiment after the acceptance run.

## 6. Hardware acceptance checklist

A release candidate is hardware-ready only when every applicable row has
user-collected evidence. The source preparation cannot satisfy any row.

| Area | User run | Pass evidence |
| --- | --- | --- |
| Identity | `bash scripts/plasma_serve.sh --check`; `bash scripts/plasma_boot.sh status` | Manifest says `release_candidate: nura-plasma-rc1`, `release: 7.0.0-sm8150-rear1`; boot is temporary `fastboot boot`, never flash |
| Photo, all rear lenses | Open Plasma Camera; switch camera for each rear module and take a still | Three distinct camera selections, three non-empty files, preview/open succeeds; record app version and lens order |
| Video, all rear lenses | Record a short clip per rear selection with Plasma Camera | Non-empty playable clips and no crash; choppiness/quality/FPS must be reported honestly, not inferred |
| Microphone raw capture | Run the `parecord` discriminator in section 6.1 while speaking/tapping | Bytes and `rms_dbfs`/`peak_dbfs` classify frames vs no frames; do not infer from KRecorder |
| Clock | Section 5 | `date -u` not 1970 and `NTPSynchronized=yes` after a real uplink |
| Graphics | Use the shell/camera for several minutes; collect `journalctl -k` and KWin/Mesa errors | Accelerated baseline remains unchanged. Compositor mapping faults remain unresolved; camera EGL import fallback is understood, while the proposed CAMSS stride change is untested and not integrated. |
| pstore | No panic test is required for rc1; optional persistence testing is a separate hardware experiment | The captured boot registered ramoops. `/sys/fs/pstore` was empty after a normal boot; persistence across a crash is untested. See [pstore investigation](bringup-notes/nura-rc1-20261111/pstore/REPORT.md). |
| Charging | Connect the 5 V charger; check `/run/guacamole/charger`, `guacamole-chgctrl`, charge current and `0x1070`/`0x1061`/`0x1370` readback | Expected source/current and no input suspension; never leave input suspended |
| Rollback | Section 4 against a real rc1 rollback | `--restore` verifies checksums, old rootfs serves and boots, current attempt is preserved |

### 6.1 Microphone discriminator (no route, mixer or UCM change)

The microphone report found the PulseAudio source unmuted at 100 %, the
installed UCM route correct at capture time, and the single KRecorder clip
unreadable. **KRecorder never visibly started recording in the user's test;
successful recording is not assumed.** Nothing in that evidence justifies a
UCM, mixer or kernel change.

Run this raw-PCM check on the phone and speak/tap during the 5 s:

```sh
timeout 5 parecord --device=alsa_input.platform-sound.HiFi__Mic__source \
  --format=s16le --rate=44100 --channels=1 --raw /tmp/mic-check.raw
ls -l /tmp/mic-check.raw
python3 - <<'PY'
import math, struct
d = open('/tmp/mic-check.raw', 'rb').read()
d = d[:len(d)//2*2]
s = struct.unpack('<%dh' % (len(d)//2), d)
print('frames', len(s), 'bytes', len(d))
def db(x):
    return -float('inf') if x == 0 else 20 * math.log10(x / 32768)
if s:
    rms = math.sqrt(sum(x*x for x in s) / len(s))
    print('peak_dbfs %.1f rms_dbfs %.1f' % (db(max(abs(x) for x in s)), db(rms)))
else:
    print('no audio frames')
PY
```

| Result | Meaning | Next |
| --- | --- | --- |
| ~440 kB, `rms_dbfs` > -60 | Frames with signal; capture works | KRecorder/Qt is the failing layer; test another recorder, no kernel change |
| ~440 kB, `peak_dbfs` = `-inf` or < -80 | Frames are digital zero | Only then test a UCM TX0 clear/re-set; this is not diagnosed yet |
| 0/tiny file or `parecord` error | No frames reach userspace | Instrument the 7.0 capture path; do not patch UCM blindly |

If the source is reported busy, the report's fallback is:

```sh
timeout 5 arecord -D hw:0,1 -f S16_LE -r 44100 -c 1 /tmp/mic-check.wav
```

## 7. Deferred work

These findings do not change the rc1 source:

- **Graphics** — the accelerated baseline is unchanged. Compositor GPU mapping
  faults and recovery remain unresolved. The camera EGL import stride mismatch
  is understood and falls back to CPU upload; the CAMSS alignment proposal is
  untested and not integrated. See the [graphics investigation](bringup-notes/nura-rc1-20261111/graphics/REPORT.md).
- **pstore** — ramoops registered successfully; duplicate-probe errors are
  benign for this boot and need no source fix. Crash persistence is untested;
  rc1 does not require an induced panic. See the [pstore investigation](bringup-notes/nura-rc1-20261111/pstore/REPORT.md).
- **Microphone/UCM/audio route** — no confirmed defect; no speculative route,
  mute or kernel change.
- **GPU fallback / compositor backend switch** — no evidence-based fallback is
  prepared.
- **Clock/RTC source change** — no safe source fix without an uplink; only the
  bounded user-run WiFi/NTP check is documented.
- **Debug log flood** — the report traced the bare `debug` to ABL boot args,
  not this repository's boot cmdline. Silencing it would alter the boot chain
  or hide diagnostics, so it is deferred rather than patched.
- **Actual image build, serve, boot, SSH, fastboot, calibration extraction** —
  user-run only.

## 8. Rootless checks run for this preparation

```bash
python3 -m unittest tests.test_rootfs_prepare_plasma tests.test_plasma_delivery -v
```

The 48 tests use synthetic checkouts, fake `sudo`/`pmbootstrap`/`ss`/`findmnt`/
`df`/`cp`/`sha256sum` and never invoke privileged real tools or the phone. New
regression coverage includes:

- preservation of the previous rootfs image, rootfs initramfs, export and key
  kernel files before a failing build;
- no hashing of the live writable rootfs;
- refusal when the previous manifest has no rootfs, when the rootfs is a
  symlink, when the rollback copy does not fit, and when a partial copy fails;
- restore-only-after-checksum verification, current-attempt preservation, and
  refusal of a tampered rollback;
- rc1 release metadata in the manifest, packaging inputs and candidate receipt;
- existing Plasma Camera, profile isolation and delivery tests.

No actual image, hardware, clock, camera, microphone, graphics, pstore or
charging test was run. Photo and video behavior remain unknown until tested on
hardware. Microphone capture remains unverified; no speculative microphone or
kernel fix is included. No kernel migration is proposed.

## Next release candidate

To prepare rc2, change only the release-candidate metadata:

1. Set `RELEASE_CANDIDATE = "nura-plasma-rc2"` in
   `scripts/check_plasma_candidate.py`. The shell wrapper reads it; do not
   duplicate or rename `RELEASE`.
2. Re-run the rootless tests and `--check-only`.
3. `--build` preserves the displaced candidate. Because the rollback is named
   from the old manifest, an rc2 build creates
   `rollback-nura-plasma-rc1-*`, not `rollback-nura-plasma-rc2-*`. (The rc2
   implementation corrected this; see NURA_RC2.md §1.3.)
4. Do not rename `build/plasma-mobile`, `.work/plasma-mobile`, the kernel
   package, the kernel release or the Phosh profile.

`camera_serve.sh` / `camera_boot.sh` already ignore additive manifest fields, so
an rc1 rollback and an rc2 manifest can coexist in this checkout.
