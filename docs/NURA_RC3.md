# Nura Plasma Mobile release candidate rc3

Status: **source-ready; binary not built; no hardware result.**

This checkout prepares `nura-plasma-rc3` sources, one user-run build entrypoint
and the rootless checks around it.  Nothing in this preparation ran pmbootstrap,
sudo, a mount, a served image, a phone command, a flash or a real build.  The
candidate becomes binary-ready only after the user runs
`bash scripts/rc3_build.sh build` and its final checks pass.

## 1. Identity

Unlike rc2, rc3 rebuilds the whole boot chain.  No rc2/`rear1` binary is
relabeled and no hash in this document is a substitute for the build output:
the manifests are written from the files the user's build actually produced.

| Item | Value / rule |
| --- | --- |
| Kernel uname | `7.0.0-sm8150-rc3` |
| Release series | `nura-plasma` |
| Release candidate | `nura-plasma-rc3` |
| Artifact label | `nura-plasma-rc3-oneplus-guacamole` |
| Kernel recipe | `device/testing/rc3/linux-postmarketos-qcom-sm8150` (kernel/device worker) |
| Kernel state / output | `.work/rc3-kernel` / `build/rc3-kernel` |
| Candidate state / output | `.work/plasma-mobile` / `build/plasma-mobile` (unchanged, so the rc2 rollback works) |
| rc2 baseline (read-only) | `.work/rear-camera`, `build/rear-camera` |
| Boot transport | temporary `fastboot boot` + USB NBD on port 10001; never flash |
| Kernel distribution | NBD-served rootfs only; no internal-storage install |

The only pinned bytes reused from rc2 are the unchanged packages that were
already pinned for a reason:

- `postmarketos-mkinitfs-hook-netboot` `4.2.1-r4` (port-10001 hook);
- the private `libcamera-rear` `0.7.2-r0` stack;
- the system libcamera `99990.8.0-r1` APK payload (re-signed for the candidate
  workspace, payload compared byte-for-byte).

Everything else is rebuilt: `Image`, `vmlinuz`, modules, DTB, the ABL
quarantine wrapper, the rootfs initramfs and the boot image.  The rc3 boot
image's ramdisk is the **newly exported rc3 rootfs initramfs** (its own module
tree and port-10001 hook); a reused `rear1` initramfs fails the release check in
`check_plasma_candidate.py`, `rootfs_prepare_plasma.sh` and the packer.

The boot image is accepted for a Plasma boot only when its hash is the exact
`boot_img` recorded by `build/rc3-kernel/DELIVERY.json`, whose release,
worktree, initramfs, DTB and vmlinuz entries must all agree with
`build/plasma-mobile/DELIVERY.json`.  A restored rc2 candidate keeps using the
old rc2 rule (byte-identical to the known-good `rear1` boot), so the rollback
remains bootable without weakening the rc3 path.

## 2. What rc3 changes

Kernel/device (prepared by the kernel/device worker, integrated here):

- kernel release `7.0.0-sm8150-rc3`, `CONFIG_LOCALVERSION="-sm8150-rc3"`;
- `# CONFIG_RTC_HCTOSYS is not set`; the PM8150 RTC stays available for alarms
  and can no longer overwrite `CLOCK_REALTIME` at probe;
- AW8697 haptics (`CONFIG_INPUT_AW8697=m`) with static I2C7, reset GPIO 116 and
  the alert-slider device; torch bring-up at the reviewed 70 mA aggregate;
- inherited rc2 patches, including 0004/0006 which require GNU patch default
  fuzz 2 (the build helper applies them exactly like rc2);
- device package `pkgrel=14`: haptics udev rule, boot-diagnostics helper,
  service, timer and preset, and a `nura-clock` dependency;
- `device/testing/nura-clock` installs only `/usr/lib/clock-epoch`; systemd
  uses its **mtime** as the clock floor (a reproducible build-date lower bound,
  not accurate UTC).  Its content (`1794268800`, 2026-11-10T00:00:00Z) is an
  inert source-date marker; the candidate verifier requires a 10-digit marker
  whose installed mtime is not older than the marker.

Modem (prepared by the modem worker):

- `device/testing/rc3/modemmanager` is the pinned aports recipe plus the
  reviewed Primary-GW SIM-slot selection; shared `main/modemmanager` staging is
  intentional in the candidate pmaports fork;
- builds `modemmanager` and `libmm-glib` at `1.25.95_git20261005-r1` for
  aarch64 plus the noarch `-systemd`, `-udev` and `-lang` subpackages; the
  pipeline installs and verifies them as one set;
- the pinned ModemManager source archive is not in the reused pmbootstrap
  cache.  The pipeline seeds a verified local copy from `.work/rc3-distfiles/`
  (or the recipe directory) when present; otherwise pmbootstrap fetches the
  pinned URL and verifies the recipe's SHA-512 itself.  Signatures and normal
  APK trust are retained; `--allow-untrusted` is never used.

Retained from rc2 (unchanged source/config): Plasma Camera, system libcamera
0.8 ABI plus private 0.7.2, Xwayland, PulseAudio, speaker/UCM fixes, PowerDevil
autosuspend off, candidate-only `LockOnStart=false`, isolated NBD state, the
pidfd stale-server cleanup and the per-boot SSH host-key alias.

Not integrated: any forced microphone rate change (the 48 kHz patch remains
withdrawn; the direct 44.1/48 kHz ALSA test still decides), an internal-storage
install, partition/flash changes, or unreviewed haptics/torch/voltage changes.

## 3. The single user-run build

Run as your **normal user** from the canonical checkout.  The script refuses
root; pmbootstrap uses sudo internally.

Before starting:

1. Boot the phone out of the Linux candidate (Android or fastboot is fine).
2. Stop every port-10001 server (Phosh `camera_serve.sh` and
   `plasma_serve.sh`); the build refuses a live candidate port.
3. Make sure pmbootstrap is not running and no mount remains below
   `.work/plasma-mobile` / `.work/rc3-kernel`.  A leftover Plasma work tree is
   normally cleared by `bash scripts/plasma_shutdown.sh`; the top-level
   preflight refuses active mounts, so run that first if needed.
4. Allow at least **24 GiB** free on the checkout filesystem (kernel source and
   output, rootfs + export, boot image and the preserved rollback).
5. Optional offline modem source: the modem worker's verified tarball is not in
   the reused pmbootstrap cache.  Seed it (the pipeline verifies the SHA-512
   before copying; otherwise pmbootstrap fetches the pinned URL itself):

```bash
mkdir -p .work/rc3-distfiles
cp bringup-notes/nura-rc3-20261112/modem-package/distfiles/\
ModemManager-7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a.tar.gz .work/rc3-distfiles/
# sha512 e049eecc104c989f516402ca7c21e3374c152e3840cf05c34ac91bc8e3305c83b53693bc0964f657664d4156baaac1a4e0fa35dd577733623dd669b67f1675f6
```

```bash
cd runtime-workspace
bash scripts/rc3_build.sh check     # read-only preflight; prints missing inputs
bash scripts/rc3_build.sh build     # kernel -> APKs -> rootfs -> boot -> manifests
```

`build` performs, in dependency order:

1. preflight (recipes, baseline pins, archives, modem archive, disk, port,
   mounts);
2. rootless rc3 kernel build (`.work/rc3-kernel` → `build/rc3-kernel`) and
   `kernel-inputs.json` computed from the produced Image/vmlinuz/DTB/modules;
3. `rootfs_prepare_plasma.sh --build`:
   - preserves the current `build/plasma-mobile` as
     `rollback-<previous-label>-<stamp>` **before** touching it;
   - builds the rc3 kernel APK with `pmbootstrap --envkernel` from the same
     rootless output, then device/firmware/UI/modem packages and `nura-clock`;
   - installs the exact versions, exports the rootfs and verifies the installed
     vmlinuz, DTB, complete module payload, initramfs, Camera/libcamera ABI,
     Xwayland, PowerDevil, modem set and clock floor;
   - packs the new rc3 boot image with the exported rc3 initramfs and the ABL
     quarantine wrapper;
   - writes `build/rc3-kernel/DELIVERY.json`, `build/rc3-kernel/packaging-inputs.json`,
     `build/plasma-mobile/DELIVERY.json` and `packaging-inputs.json` from the
     actual artifacts;
4. a final read-only candidate consistency check.

To keep the dedicated debugging **public** key in the new rootfs, opt in
explicitly (default adds none):

```bash
bash scripts/rc3_build.sh build --authorized-key "$PWD/.work/agent-ssh/rc1-investigation_ed25519.pub"
```

Logs: `.work/plasma-mobile/rootfs/nura-plasma-rc3-pipeline-<stamp>.log` and
`.../nura-plasma-rc3-package-<stamp>.log`.  The long phases print a message
before they run; a silent kernel compile is not a hang.  The script stops on
the first missing or mismatched input and does not continue with partial
artifacts.

The live-state guard (port 10001 listener plus mounts below
`.work/plasma-mobile` / `.work/rc3-kernel`) runs **before any candidate
artifact is read or hashed**.  `build` fails there; `check` prints an explicit
warning and skips the mutable candidate validation, so a served rootfs is
never hashed.  A failed `findmnt` is refused as "cannot prove no mounts",
never treated as "no mounts".

Optional read-only checks before/after the build:

```bash
# after `rc3_build.sh kernel` (or after a full build): validates the rc3
# kernel staging + read-only rc2 APK baseline without building
bash scripts/rootfs_prepare_plasma.sh --check-only
# after a full build: validates the built rc3 candidate end to end
python3 scripts/check_plasma_candidate.py check
bash scripts/plasma_serve.sh --check                 # delivery preflight (no server)
```

## 4. Serve, boot and SSH (user-run, never flash)

```bash
# terminal 1
bash scripts/plasma_serve.sh
# terminal 2
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
# after Plasma Mobile is up
bash scripts/ssh_phone.sh
```

`plasma_serve.sh` refuses to replace a cache while port 10001 is in use, stages
and verifies the new image before exposing it, and records the pre-exposure
identity.  `plasma_boot.sh` checks the manifest, the rc3 kernel-build
provenance, the boot image, DTB, initramfs, CCI module, rootfs pin and device
identity before one temporary `fastboot boot`.  There is no flash path.

The first contact of a fresh rootfs asks for its host-key fingerprint; pin it
only after verifying the expected boot.  `bash scripts/ssh_phone.sh
--forget-host-key` resets only the current per-boot alias.

## 5. Rollback to rc2

The build preserves the displaced candidate automatically:

```text
build/plasma-mobile/rollback-<previous-label>-<YYYYmmdd-HHMMSS>/
```

The rollback is self-describing: its own `kernel.release` (for rc2,
`7.0.0-sm8150-rear1`) names the module tree/CCI path that was copied, so a new
global rc3 release cannot orphan it.  Normal builds never consume the old
manifest; only the explicit restore path does.

```bash
# stop the server and boot the phone out of the rc3 candidate first
bash scripts/rootfs_prepare_plasma.sh --restore build/plasma-mobile/rollback-<previous-label>-<stamp>
bash scripts/plasma_serve.sh --check
bash scripts/plasma_serve.sh --replace
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
```

`--restore` verifies `ROLLBACK-SHA256SUMS` and the export inventory before
moving anything, preserves the current attempt under
`.work/plasma-mobile/rootfs/restore-before-<stamp>/`, keeps the rollback for
another attempt, and prints the restored label.  It uses sudo only for the
rootfs image in pmbootstrap's protected parent directory.  Do not rename old
rc1/rc2 rollback directories or manifests.

## 6. Rootless checks

```bash
python3 -m unittest \
  tests.test_rc3_build \
  tests.test_rootfs_prepare_plasma \
  tests.test_plasma_delivery \
  tests.test_plasma_shutdown \
  tests.test_ssh_phone \
  tests.test_stop_plasma_server \
  tests.test_phone_scripts -v

python3 -m unittest discover -s tests -p 'test_*.py'
```

The rc3-specific coverage:

- `rc3_build.sh check` refuses a missing rc3 kernel/modem/clock recipe, a
  missing firmware/sensor archive, low disk, a live port, active candidate
  mounts and a symlinked state path; it prints the missing-input guidance and
  the read-only baseline status;
- `kernel-record` rejects the old `7.0.0-sm8150-rear1` staging and is
  deterministic for identical staging; a changed CCI module, an added module
  or a recipe changed after the build fails `source`;
- the old rc2 rollback keeps its `rear1` module path, restores through the
  explicit `--restore` path, and is never treated as the current rc3 candidate;
- a reused rc2 initramfs is rejected for the rc3 boot image;
- the rc3 boot image must be the one recorded by the rc3 kernel build; a
  different boot image or a missing `kernel_build` fails the launcher;
- `check` validates a built rc3 candidate end-to-end against its own manifests;
- the existing cache/mount/cleanup, system libcamera, Camera, Xwayland,
  PowerDevil, clock floor, SSH alias and pidfd tests remain in place.

No test runs a kernel compile, pmbootstrap, sudo, a mount, a server or a phone
command.  **Passing source tests is not binary or hardware readiness.**

## 7. Source-ready vs binary-ready vs hardware-tested

- **Source-ready:** recipes, scripts, guards, tests and docs are ready for the
  user-run build.  Nothing above claims a compiled kernel, APK or image.
- **Binary-ready:** only after `rc3_build.sh build` succeeds and
  `plasma_serve.sh --check` passes on the resulting artifacts.
- **Hardware-tested:** not established.  Haptics/AW8697 compile and autoload,
  alert-slider → feedbackd, torch thermal behaviour, modem slot selection,
  clock floor, boot diagnostics, camera preview/recording and the rollback
  boot all still need user-run evidence.

## 8. Known risks and deferred work

- The AW8697 driver has not been compiled or run; the optional modules-load
  file was deliberately not added because the in-tree driver has an OF
  modalias.  Add it only if a real boot shows the module missing.
- feedbackd `uaccess` ACL/rule ordering is inferred, not observed; the slider
  feeds feedbackd only (no Plasma ringtone/media muting).
- Torch 70 mA aggregate is the only hardware-verified current setting (from the
  older reference); continuous/thermal behaviour on 7.0 is untested.
- `CONFIG_RTC_HCTOSYS=n` removes the RTC fallback for sleep time; automatic
  suspend is disabled in this candidate, so that tradeoff is acceptable here.
  Accurate UTC still needs NTP/uplink; the clock floor is a lower bound.
- ModemManager r1 is only source-verified.  Its aarch64 build, `check()`,
  signature trust, in-place rollback APK set and runtime slot selection are
  user-run work; r0 rollback is retained but the no-downgrade path is a
  previous image, not an untested APK downgrade.
- Microphone: no rate patch; the direct 44.1/48 kHz ALSA test remains the
  discriminator.
- pstore/ramoops persistence, GPU/DPU ring recovery, internal-storage install,
  partition/DTBO/flash actions and audio-call routing are outside rc3.
- GPIO/voltage/current changes beyond the reviewed torch/haptics set are not
  authorized by this candidate.

## 9. Non-goals

No `fastboot flash`, no partition/DTBO/vbmeta write, no internal-storage
install, no service change on the phone, no forced microphone rate, no
`--allow-untrusted`, no repository rollback from the phone, and no claim that a
source test proves hardware behaviour.
