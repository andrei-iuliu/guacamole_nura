# Nura Plasma Mobile release candidate rc2

Status: **source-ready; binary not built. No phone, build, serve or privileged
action was performed while preparing it.**

This checkout prepares `nura-plasma-rc2` sources and user-run commands only. It
becomes a testable binary candidate only after the user runs `--build` and the
host checks below pass. Hardware acceptance is the result of those tests.

The kernel identity and pinned boot chain are deliberately unchanged from
`rc1`:

| Item | Value |
| --- | --- |
| Kernel release / uname | `7.0.0-sm8150-rear1` |
| Release series | `nura-plasma` |
| Release candidate | `nura-plasma-rc2` |
| Artifact label | `nura-plasma-rc2-oneplus-guacamole` |
| Candidate state | `.work/plasma-mobile` |
| Candidate output | `build/plasma-mobile` |
| Pinned boot image | `1e4b12f34b153be612fbc72e0e97c5b45017b6932676cf9f4491b511060b0392` |
| Pinned boot initramfs | `a16b45a4701903fde5569f71140be7d90c33f33b3bbc9c4e4c87f3f082e8447f` |
| Pinned kernel `Image` | `ceb3a2603e0f6c702ea55d629f937048a17c116d96d149ae9d46a7072c635e92` |
| Pinned vmlinuz | `0ce4d7afd1e73112817aba710dd1acb7a7a36076d9900fa3f90a8589daa27df3` |
| Pinned DTB | `f9e1ae14950c88858487feefb1425775c7d4bb4fecd276af466770460427a74d` |
| Pinned module payload / count | `a8d4ea15a91d6d312391d9e7b6b3164b2a219256abd9d87ec3daedfaa27e55cc` / `832` |

The hashes above are the values already recorded in
`build/rear-camera/DELIVERY.json` / `packaging-inputs.json`. `rc2` does not
rebuild, migrate or rename the kernel, modules, DTB, boot initramfs or boot
image; `check_plasma_candidate.py source` re-verifies every pin before a build.

## 1. What changed in rc2

### 1.1 Release label only

`scripts/check_plasma_candidate.py` now defines
`RELEASE_CANDIDATE = "nura-plasma-rc2"`. `RELEASE`, the profile, the state and
output paths, the kernel package and the module tree are unchanged. Old `rc1`
manifests remain readable: add-on fields are optional for the launch checks.

### 1.2 Xwayland is an explicit package and is guarded

Observed on the running `rc1` (one bounded read-only SSH check):
`command -v Xwayland` returned nothing, and the earlier rc1 investigation
found no `xwayland` package record, with KWin logging
`Xwayland process failed to start`. Re-serving the rc1 rootfs removed an
earlier manual install.

rc2 therefore:

- adds `xwayland` explicitly to the pmbootstrap install list in
  `scripts/rootfs_prepare_plasma.sh`;
- requires the `xwayland` package record **and** an executable
  `/usr/bin/Xwayland` in the installed rootfs before writing a manifest;
- keeps dedicated rootless tests for both omissions.

The APK's signed dependency chain supplies the runtime libraries it needs
(`libtirpc` / `libtirpc-nokrb`, `libtirpc-conf`) from the configured pmaports
repositories during the normal user-run build. Inspection of the cached signed
APK (read-only; not a build input) shows its control metadata declaring
`so:libtirpc-nokrb.so.3`, and `libtirpc-nokrb` declaring `libtirpc-conf`;
normal APK dependency resolution therefore installs both. The cached APKs
staged by the live investigation under `.work/rc1-repair/` are **not** read,
copied or installed by this source.

This is expected to remove a likely blocker for KWin's X11 socket and
PulseAudio's X11 integration. It is **inferred**, not proven, that restoring
Xwayland makes PulseAudio accept clients and lets Plasma Camera open; that
must be tested on the next built image.

### 1.3 Rollback metadata names the displaced candidate correctly

The release constant is now rc2, but the previous output in
`build/plasma-mobile` is still rc1. The rollback therefore:

- reads `release_candidate` from the previous `DELIVERY.json` (falling back to
  `packaging-inputs.json`) instead of stamping the new build constant on old
  files;
- creates `build/plasma-mobile/rollback-nura-plasma-rc1-<timestamp>/`;
- records `release_candidate: nura-plasma-rc1`,
  `artifact_name: nura-plasma-rc1-oneplus-guacamole` and
  `replaced_by_release_candidate: nura-plasma-rc2` in `ROLLBACK.json`;
- says "Previous nura-plasma-rc1 candidate" in `RESTORE.txt`;
- reports the restored label read from the rollback during `--restore`.

A previous output with no readable label is preserved honestly as
`rollback-unlabeled-<timestamp>` with `release_candidate: null`; a malformed or
tampered label aborts before any file is changed. The existing rc1 rollback
directory (if present) and delivered rc1 manifests are left untouched.

### 1.4 Optional, explicit public-key seeding for the new rootfs

Fresh NBD rootfs images regenerate host keys, and a key manually added with
`ssh-copy-id` to a previously served image does not survive a rebuild.
`--authorized-key PATH` is a build-only, opt-in way to put existing debugging
**public** keys into the next rootfs:

- default `--build` adds **no** key and leaves `ssh_keys = false`;
- the option is rejected for `--check-only` and `--restore`;
- `PATH` must be a regular file (no symlink) containing one or more plain
  OpenSSH public-key lines; an existing `authorized_keys` file works;
- private keys (`BEGIN ... PRIVATE KEY`, `OPENSSH PRIVATE`) and
  `authorized_keys` options are rejected before any output is written;
- the validated key file is copied to a temporary mode-600 file, and a
  throw-away pmbootstrap config enables pmbootstrap's own install-time
  `copy_ssh_keys()` for that one install; the candidate config itself stays
  `ssh_keys = false`;
- pmbootstrap installs `home/<config.user>/.ssh/authorized_keys` as mode 600
  under a mode-700 directory owned by uid/gid 10000, using the existing
  `user = user` config;
- no private key is read or used, no key material is printed, hashed or added
  to `DELIVERY.json`/`packaging-inputs.json`, and no key is installed outside
  the future user-run `--build`;
- if the candidate rootfs skeleton already contains `.ssh`, the build stops
  early instead of overwriting an `authorized_keys` file.

The public key is trusted only because the user explicitly passed it. Host-key
verification for SSH is unchanged: `scripts/ssh_phone.sh` still asks for the
first fingerprint of each recorded boot, pins one alias per successful boot and
only removes that alias on an explicit `--forget-host-key`.

### 1.5 Preserved rc1 fixes

The Plasma Camera replacement (no Snapshot), pinned system libcamera 0.8 ABI,
Qt Multimedia backend check, PowerDevil/autosuspend policy, candidate-only lock
screen setting, isolated state/output and the pidfd-based
`stop_plasma_server.py` cleanup (5 s SIGTERM grace, ownership recheck, SIGKILL
only through the same pidfds) are preserved. Their tests were run unchanged.

## 2. Current evidence and known regressions

Observed on the live rc1 boot, 2026-11-11 (single read-only SSH check; see
`bringup-notes/nura-rc2-20261111/single-check/REPORT.md`):

- normal user `user` (uid/gid 10000), kernel `7.0.0-sm8150-rear1`;
- `command -v Xwayland` produced no path;
- `pactl info` and `pactl list short sinks` were immediately refused
  (`Connection refused`);
- from earlier rc1 investigation (not re-run): a PulseAudio process exists;
  PulseAudio and Plasma Camera main threads waited in
  `connect`/`unix_wait_for_peer`; KWin could not start Xwayland.

Inferred, not established:

- Xwayland is missing from the served rc1 rootfs again; the package database
  was not queried in this check;
- the immediate refusal means no accepting PulseAudio socket at that moment,
  not why it refused;
- missing Xwayland is a plausible cause of the PulseAudio/Qt `connect` wait
  and of Plasma Camera not opening. No media-driver regression is confirmed:
  all three rear sensors were visible in GNOME Snapshot.

Accepted / deferred for rc2:

| Topic | rc2 stance |
| --- | --- |
| Xwayland absence | Fixed at source: explicit package + package/executable guards. Must be verified on the next image. |
| PulseAudio refusing clients; Plasma Camera not visibly opening | Not patched speculatively. Re-test after the Xwayland fix; collect `pactl info`, KWin and Plasma Camera logs if it persists. |
| KRecorder never visibly recording; microphone signal | Unverified. The `parecord` discriminator in `NURA_RC1.md` §6.1 remains the user-run test. No UCM, mixer or kernel change is included. |
| Camera preview/video on all three lenses | Unknown until hardware testing; no camera source change in rc2. |
| Camera pitch-64/CAMSS alignment proposal | Staged by another worker, **not integrated**; no evidence justifies it for rc2. |
| GPU/DPU mapping faults and ring recovery | Unresolved and separate from camera bring-up; no graphics patch in rc2. |
| pstore / ramoops | Registered on the captured boot; duplicate `-EINVAL` probes are benign. Crash persistence is untested and not an rc2 acceptance requirement. |
| Clock 1970 / no uplink | Documented user-run Wi-Fi/NTP check only; no route, RTC or credential change in rc2. |
| Kernel/boot/module changes | None. Byte-identical pins remain required. |
| Audio UCM experiment | Not reviewed or integrated; other topic results are pending. |

Reports from the cancelled broad swarm (audio, camera, wake-graphics) were not
present or complete at review time; their dispositions remain with the parent.
No speculative patch from them is included.

## 3. User-run build

Run as the normal user from Bash. The script refuses root; pmbootstrap handles
privilege internally. **Stop the port-10001 server and boot the phone out of
Linux before a build.** Do not reboot or disrupt the currently booted phone
just to prepare sources.

Default candidate, no SSH key added:

```bash
cd runtime-workspace && bash scripts/rootfs_prepare_plasma.sh --build
```

To keep the dedicated debugging public key available in the next rootfs, pass
the **public** key explicitly. This is the opt-in; without the flag nothing is
added:

```bash
cd runtime-workspace && bash scripts/rootfs_prepare_plasma.sh --build --authorized-key "$PWD/.work/agent-ssh/rc1-investigation_ed25519.pub"
```

Use any public-key file you control. The build log states only how many public
keys were validated and that pmbootstrap will install them; it never prints key
material. The old image remains available as the rc1 rollback.

Optional read-only preflight (does not accept `--authorized-key`):

```bash
cd runtime-workspace && bash scripts/rootfs_prepare_plasma.sh --check-only
```

`--check-only` refuses while any mount remains under `.work/plasma-mobile` or
while the candidate port is in use; that is a deliberate read-only guard, not
a source failure. A live phone boot with a running server therefore blocks the
preflight. Stop serving and run `bash scripts/plasma_shutdown.sh` first; a
normal `--build` performs that cleanup itself. Do not clean up or reboot the
currently booted phone merely to run the preflight.

After a successful build, check the host artifacts before serving:

```bash
cd runtime-workspace && bash scripts/plasma_serve.sh --cleanup --check
```

`--check` must show `release_candidate: nura-plasma-rc2`,
`release: 7.0.0-sm8150-rear1`, the pinned boot hashes and a validated
rootfs/rootfs initramfs. It does not boot or flash anything.

## 4. Serve, boot and SSH (user-run)

Stop the Phosh profile first; the Plasma profile is fixed to port 10001.

```bash
# terminal 1
bash scripts/plasma_serve.sh
# terminal 2 (temporary boot only, never flash)
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
# after userspace is up
bash scripts/ssh_phone.sh
```

First contact with a fresh rootfs asks for the host-key fingerprint. Accept it
only after verifying the expected new boot; later changes within that boot are
refused. `bash scripts/ssh_phone.sh --forget-host-key` resets only the current
recorded boot alias.

## 5. Roll back to rc1

The rc2 build preserves the displaced rc1 output before touching it. The
directory name now carries the old label:

```text
build/plasma-mobile/rollback-nura-plasma-rc1-<YYYYmmdd-HHMMSS>/
```

Use the path printed at the end of `--build`, or read it from
`ROLLBACK.json` / `RESTORE.txt`. Stop the server and boot the phone out of the
candidate first, then:

```bash
bash scripts/rootfs_prepare_plasma.sh --restore build/plasma-mobile/rollback-nura-plasma-rc1-<stamp>
bash scripts/plasma_serve.sh --check
bash scripts/plasma_serve.sh --replace
bash scripts/plasma_boot.sh boot --dry-run
bash scripts/plasma_boot.sh boot
```

`--restore` verifies `ROLLBACK-SHA256SUMS` and the export inventory before
moving anything, preserves the current attempt under
`.work/plasma-mobile/rootfs/restore-before-<stamp>/`, keeps the rollback for
another attempt and prints the restored label (`nura-plasma-rc1`). It uses
sudo only for the rootfs image in pmbootstrap's protected directory.

Do not rename old rc1 rollback directories or manifests. They stay readable
alongside the rc2 output.

## 6. Rootless checks for this preparation

```bash
python3 -m unittest \
  tests.test_rootfs_prepare_plasma \
  tests.test_plasma_delivery \
  tests.test_ssh_phone \
  tests.test_stop_plasma_server \
  tests.test_plasma_shutdown -v
```

Result at source preparation: **95 tests passed**. New rc2 coverage includes:

- current release metadata is `nura-plasma-rc2` while the unchanged `RELEASE`
  and pinned boot bytes are still asserted;
- the rollback directory and `ROLLBACK.json` use the displaced rc1 manifest
  label, not the rc2 build constant, and record
  `replaced_by_release_candidate: nura-plasma-rc2`;
- an unlabeled previous output is preserved as `rollback-unlabeled-*` with
  `release_candidate: null`;
- `--authorized-key` is rejected outside `--build`; private keys and invalid,
  truncated or option-prefixed public keys are rejected before output; an
  existing multi-key `authorized_keys` file is copied in full; the generated
  pmbootstrap config enables `ssh_keys` for install only and leaves the
  candidate config at `ssh_keys = false`;
- a default build produces no key configuration or key note;
- existing tests still cover missing `xwayland` package and missing or
  non-executable `/usr/bin/Xwayland`, the Plasma Camera/Qt backend/libcamera
  0.8 ABI checks, rollback integrity, pidfd cleanup and per-boot SSH host-key
  pinning.

Also checked in this preparation: `bash -n scripts/rootfs_prepare_plasma.sh`
and `python3 -m py_compile scripts/check_plasma_candidate.py`.

No actual image build, pmbootstrap/chroot/mount, sudo, phone command, server,
SSH, fastboot, flash, package install, hashing of the writable served rootfs or
camera launch was performed. The read-only `--check-only` preflight was tried
but refused because the live candidate state still had active mounts and the
candidate port in use; that is the documented guard, so no live artifact was
hashed and nothing was cleaned up or restarted.

## 7. Source-ready vs binary-ready

- **Source-ready:** this checkout's scripts, guards, tests and docs are ready
  for the user-run build.
- **Binary-ready:** only after the user runs `--build` successfully and
  `plasma_serve.sh --check` passes on the resulting output.
- **Hardware-tested:** not established for rc2. Xwayland/PA/camera, microphone,
  video, graphics, pstore persistence, clock, charging and rollback boot still
  need user-run evidence from the next image.

## 8. Remaining blockers and missing evidence

- The next image has not been built or booted; whether Xwayland fixes
  PulseAudio and Plasma Camera startup is unknown.
- No `pactl`, KWin/Xwayland or Plasma Camera log exists for the rc2 image yet.
- Microphone frames/signal and KRecorder behavior are unverified.
- Photo/video behavior across the three rear lenses is unverified.
- Other swarm topic reports (audio, camera, wake-graphics) are pending; nothing
  from them is integrated.
- Any additional device/privileged action must be requested as a precise
  user-run command; no automatic trigger is added by this source.
