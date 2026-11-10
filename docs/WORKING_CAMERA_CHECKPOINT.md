# Working rear-camera checkpoint (`7.0.0-sm8150-rear1`)

Date: 2026-11-10. This is the checkpoint of the **user-confirmed rear-camera
boot**. It is a documentation and read-only verification artifact: no phone,
server, module, sensor, I2C, capture, flash or privileged host operation was
performed while producing it.

The canonical checkout is still undecided. Use `$CAMERA_ROOT` below and pass
the path explicitly; do not assume a worktree name. At checkpoint time
`nura-rear-cameras-7-0` matched the frozen source snapshot exactly. The
`runtime-workspace` checkout has the same primary build hashes, but four
untracked tooling/doc files differ, so it does not match the snapshot
byte-for-byte.

## 1. What is confirmed

| Item | Status | Evidence |
| --- | --- | --- |
| All three rear cameras work in the camera app | **User-confirmed** after an explicit `--replace` refreshed the port-10001 netboot cache | user report in the frozen handoff; appended section in `docs/REAR_CAMERA_7_0_BRINGUP.md` |
| Video records, but is choppy | **User-reported**; capture/encode/preview/playback were not separated | handoff brief |
| Kernel includes the speaker fix `0027` | Source recipe contains it; audio was not rechecked in this confirmation | kernel recipe source list |
| Ultra-wide AK7374 is bound | User/handoff report | handoff brief; rear graph recipe |
| Main and tele actuator suppliers unresolved and lenses intentionally unbound | User/handoff report | handoff brief; `docs/REAR_CAMERA_7_0_BRINGUP.md` |
| Wi-Fi and Bluetooth worked on the 2026-11-09 temporary boot | Earlier user report; not rechecked in this camera confirmation | `docs/LIVE_BASELINE.md` |

Do **not** claim from this checkpoint: which libcamera stack the app loaded
(system `libcamera 0.8.0-r1` + PipeWire SPA `1.6.11` vs isolated `0.7.2` under
`/opt/nura-rear-camera`), autofocus correctness/calibration, main/tele lens
identity, video FPS or dropped frames, suspend/resume, long-term stability, or
front-camera support. Those remain unverified.

## 2. Source identity and frozen snapshot

| Field | Value |
| --- | --- |
| Base commit | `5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24` |
| Working source | uncommitted: 9 modified tracked files, 39 untracked entries |
| Frozen snapshot | `working-source.patch` + `untracked/` + `SHA256SUMS.json` in the handoff directory |
| `working-source.patch` SHA-256 | `5fab7b4700a73b1db34119e6f6479f4a2b79fb4bba920bf0aa7f803fb305b547` |
| `SHA256SUMS.json` SHA-256 | `b73d5b00e81e289a242f68d833eea2cf8d7d31a89c6585f788d92fad49ce5530` |
| Snapshot entries verified | 141 of 141 |
| Snapshot tree digest | `39d2817a78f91ca3608392ac5793767c6562c3c52b5122da8fc75c6b9dbe2639` |
| Patch application | applies cleanly to `git archive HEAD`; 9 patched tracked files match the live worktree |
| Untracked snapshot | 140 files match the live worktree |

The snapshot, not a git branch, is the source of truth: all camera work is
uncommitted, so an ordinary branch merge would miss it. Preserve the snapshot
in place; the checkpoint tool verifies it and does not rewrite it. There is no
need to copy the 8 GB rootfs image again.

Snapshot tree digest is the SHA-256 of the sorted `"<sha256>  <relative-path>"`
lines for `working-source.patch` and every file below `untracked/`.

## 3. This build's artifacts

The per-build manifest is `build/rear-camera/DELIVERY.json` (SHA-256
`396a92ebbea4997c963b40f0269cd53fc17d016b81ced53ef19ba6fb7687c04e` at
checkpoint time). Its `created_at` is `2026-11-10T12:20:16Z`, its `git_head`
is the base commit, and its `release` is `7.0.0-sm8150-rear1`.

| Artifact | Role | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `build/rear-camera/boot.img` | immutable build artifact | 57,393,152 | `0e19bbb5524b8b6b7ae7cc024327375a14678796298c1e8110a4f28cffe0cec9` |
| `build/rear-camera/Image` | immutable build artifact | 44,411,392 | `4e798093d18fceb8d4708565e85203866ed80db06b2eef9ec8f00cf12c9883d4` |
| `kernel-package/boot/vmlinuz` | immutable build artifact | 16,985,044 | `455023be1092fca8e34911b4db9a7e3d518f2403d75cafd7c2d21cc638bd5a45` |
| `kernel-package/.../sm8150-oneplus-guacamole.dtb` | immutable build artifact | 114,058 | `f9e1ae14950c88858487feefb1425775c7d4bb4fecd276af466770460427a74d` |
| `temporary-dtbo/custom_guacamole-abl-quarantine.dtb` | boot-image DTB wrapper | 128,439 | `3992b3c61d721a0b14a48059745ceee5d30fe30629527bbaf1134e52265d6255` |
| `.work/rear-camera/rootfs/export/initramfs` | immutable initramfs | 12,771,769 | `476066665394df2626122c2e4398819f2bcba0abd9f836602d9c628377c99cad` |
| `.work/.../home/pmos/rootfs/oneplus-guacamole.img` | **immutable packaged rootfs** | 8,049,917,952 | `4e464ab75a0ef1d33a8824643f3e0f3da942fbfb5b2c5bb2869092018836b935` |
| `kernel-package/.../i2c-qcom-cci.ko.zst` | staged kernel module (this build) | 8,950 | `4cd35f05ff873700c43baad265efd0bf73166352baac79f88d4aeca456fb27e4` |

These hashes identify **this** build. They are not a permanent requirement:
a future build is verified against its own `DELIVERY.json` and this tool
derives the expected CCI module path/hash from that manifest. The CCI hash is
**not** hardcoded in the checkpoint tool.

Boot-image contents were unpacked from `boot.img` itself:

- header v2, page size 4096, DTB address `0x03000000`;
- kernel `4e798093...` = `Image`;
- ramdisk `47606666...` = initramfs;
- DTB `3992b3c6...` = the ABL quarantine wrapper;
- cmdline contains `pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1`,
  `PMOS_NO_OUTPUT_REDIRECT`, ramoops settings and `scsi_mod.scan=sync`.

Kernel package: release `7.0.0-sm8150-rear1`, one module tree, **832**
`*.ko*` files, and four DTBs (`sm8150-oneplus-guacamole.dtb` plus the rear
`main`, `tele`, `wide` variants). CCI vermagic:
`7.0.0-sm8150-rear1 SMP preempt mod_unload aarch64`.

### Immutable packaged rootfs vs writable served cache

| Path | Role | Observed at checkpoint time |
| --- | --- | --- |
| `.work/.../chroot_native/home/pmos/rootfs/oneplus-guacamole.img` | immutable **packaged** rootfs | `4e464ab7...` (8,049,917,952 B) |
| `.work/.../images_netboot/oneplus-guacamole.img` (nura checkout) | **writable served cache** | `5fba6b57d5c7189f07a516ae7cef214ec4af1bf1474d536dd948f57a56ef4f5a`, differs from packaged |
| `.work/.../images_netboot/oneplus-guacamole.img` (build checkout) | writable served cache | `4e464ab7...`, identical at check time |

The served cache is a writable copy: the guest mounted it and wrote to it
after the successful boot, and a port-10001 `nbd-server` was observed live
during this session. A difference is therefore expected and is **not** an
integrity failure by itself. It is also not proof that the cache contains the
fixed CCI module. The delivery path must verify or refresh the cache before a
phone boot; do not use this checkpoint's cache hash as a boot gate.

`build/rear-camera/SHA256SUMS` and `build/rear-camera/ARTIFACTS.json` are from
11:36 and still record the pre-fix CCI module (`6f61b608...`). They are
**stale auxiliary manifests**, retained for history, not authoritative. The
checkpoint records their single module mismatch and uses `DELIVERY.json` for
this build's identity. `APK-SHA256SUMS` (16 APKs) verifies cleanly.

## 4. Recipes, pins and toolchain

Kernel recipe:
`device/testing/rear-camera/linux-postmarketos-qcom-sm8150/APKBUILD`
(SHA-256 `dfca71025c2d0a4dde377a2ada7f809c835a75eb433cce454c435b3b2b5b8d66`),
`pkgver=7.0.0`, `pkgrel=2`, `CONFIG_LOCALVERSION="-sm8150-rear1"`. It lists
29 local sources (27 patches plus the config and the kernel tarball); every
listed local file matches its `sha512sums` entry. Camera-relevant patches
`0028`–`0031` are present; `0027` (speaker bus) is present; `0031`
initializes every master completion **before**
`for_each_available_child_of_node`; `0030` gives CCI1 only `i2c-bus@1`.
The kernel tarball `linux-v7.0.0-sm8150.tar.gz` is
`708a97848f3e0e93553c30328f5a3d2a9ea2b483a8e072e973b33a30871c85ab`
(260,458,721 B) and matches its `sha512sums` entry.

libcamera recipe:
`device/testing/rear-camera/libcamera-rear/APKBUILD`
(SHA-256 `7dc88a767dd84d20a58fd810c7e01e506468bb11aa1c7705f8220d14a40c153e`),
`pkgver=0.7.2`, private prefix `/opt/nura-rear-camera`, 24 local sources
(19 patches plus `cam-rear`, three YAML tuning files and the tarball). The
libcamera tarball SHA-512 matches `b317cdc9...`; its SHA-256 is
`64881a4bafbda02f34861153d8f0d42be9ff46f5598408f9b585b07a10c448c9`.

Pins in `device/testing/rear-camera/SOURCE_PINS.json`:

- omarchy `f702f688288e59e3566f113d411ac7d18e4d0201`;
- kernel `8e126dbc4044ef2cec3ebe5754ddb05faa554af6`;
- pmaports `286d3b4ecce06510a967b33076cd333c52d3cc07`;
- pmbootstrap `e0dfe2cda7f3a0b70cdcc96f3060100839322aa4`;
- libcamera `0.7.2`.

Toolchain, as recorded in the build logs/config:

- clang and ld.lld `23.1.4` (`CONFIG_CLANG_VERSION=230104`,
  `CONFIG_LLD_VERSION=230104`);
- musl sysroot GCC `15.2.0`, musl `1.2.6`;
- Meson `1.9.1`; build venv CPython `3.12.16`;
- pmbootstrap `3.12.1` (host Python `3.14.8`);
- build-machine GCC `16.2.1`.

Packaged APK identities (all 16 match `APK-SHA256SUMS`): kernel
`linux-postmarketos-qcom-sm8150-7.0.0_p20261110122346-r0`,
`device-oneplus-guacamole-4-r12`, `firmware-oneplus-guacamole-1-r7`,
`libcamera-rear-0.7.2-r0`, `postmarketos-mkinitfs-hook-netboot-4.2.1-r4`,
`hexagonrpcd-0.4.0-r4`, `iio-sensor-proxy-3.9-r3` (plus subpackages).

## 5. Reproduce the checkpoint (user-run, read-only)

The tool is `results/checkpoint/scripts/checkpoint_working_camera.py`. It
performs no hardware action: it only reads files, git metadata and JSON, plus
the bundled `unpack_bootimg.py` and the existing
`check_rear_camera_package.py`/`check_boot_envelope.py` checks. It writes only
below `--out` and the optional archive path, plus a short-lived temporary
directory used by `unpack_bootimg.py`.

Fish:

```fish
set -gx CAMERA_ROOT guacamole_nura-workspace/nura-rear-cameras-7-0
set -gx SNAPSHOT bringup-notes/nura-stabilization-handoff-20261110
set -gx CHECKPOINT_TOOL bringup-notes/nura-stabilization-handoff-20261110/results/checkpoint/scripts/checkpoint_working_camera.py
set -gx CHECKPOINT_OUT bringup-notes/nura-stabilization-handoff-20261110/results/checkpoint/out
python3 $CHECKPOINT_TOOL --root $CAMERA_ROOT --snapshot $SNAPSHOT --out $CHECKPOINT_OUT --archive $CHECKPOINT_OUT/nura-working-camera-checkpoint.tar.zst
```

What it does:

1. verifies `SHA256SUMS.json`, every snapshot file and the snapshot tree
   digest, then applies `working-source.patch` to a temporary extraction of
   `git archive HEAD` and compares the 9 patched tracked files and 140
   untracked files with the live worktree;
2. verifies the kernel/libcamera recipes, their `sha512sums`, the pins, patch
   `0031`/`0030`/`0027` evidence and the toolchain strings;
3. verifies this build's `DELIVERY.json` and `packaging-inputs.json` against
   the actual artifacts, unpacks `boot.img`, and checks the boot envelope;
4. records the immutable rootfs and the writable served cache separately;
   records stale `SHA256SUMS`/`ARTIFACTS.json` without treating them as truth;
5. writes `CHECKPOINT.json` and `CHECKPOINT.sha256`, plus a small `.tar.zst`
   archive containing the snapshot, recipes, docs, manifests and logs — **not**
   the 8 GB rootfs, `Image` or `boot.img`.

The first run hashes the two ~8 GB images and the other inputs; later runs
reuse `out/hash-cache.json` while size, mtime and inode are unchanged. Add
`--rehash` to force a full re-hash. Use `--source-only` when only source and
recipes are available. The return code is `0` only when there are no
failures; warnings (stale auxiliary manifests, writable cache divergence,
missing optional files) do not fail the checkpoint.

If you prefer a shell archive command instead of the tool's archive, this
keeps the same small set and still excludes the large images:

```fish
tar --zstd -cf $CHECKPOINT_OUT/nura-working-camera-checkpoint.tar.zst \
  -C $SNAPSHOT SHA256SUMS.json working-source.patch untracked \
  -C $CAMERA_ROOT docs/CAMERA_DELIVERY.md docs/CCI_PROBE_FAULT.md docs/LIVE_BASELINE.md docs/REAR_CAMERA_7_0_BRINGUP.md \
  build/rear-camera/DELIVERY.json build/rear-camera/packaging-inputs.json build/rear-camera/APK-SHA256SUMS \
  build/rear-camera/SHA256SUMS build/rear-camera/ARTIFACTS.json build/rear-camera/imported-inputs.json \
  build/rear-camera/sysroot-inputs.json build/rear-camera/temporary-dtbo/manifest.json \
  device/testing/rear-camera
```

Do not archive `.work/` or `build/rear-camera/Image`/`boot.img`/`kernel-package`
unless a full binary copy is explicitly wanted; their hashes are already in
`CHECKPOINT.json`, and the 8 GB rootfs image need not be duplicated.

## 6. Self-test for the new logic

From `results/checkpoint`:

```fish
python3 -m unittest discover -s tests -p 'test_checkpoint_working_camera.py' -v
```

The 11 tests are rootless and use temporary directories/local git only. They
cover: hash-cache reuse, snapshot hash/tamper, patch application and worktree
drift, manifest-derived artifact identity (including a future CCI hash), a
manifest mismatch, rootfs-vs-served-cache roles (equal and different), the
small archive exclusions/inclusions, arbitrary-cwd CLI execution, and the
absence of a hardcoded current CCI/boot hash. The captured result is
`results/checkpoint/TEST_RESULTS.txt`.

## 7. Stale statements in existing docs

These documents are inputs, not unquestioned truth. At checkpoint time the
following statements were superseded or time-stamped:

- `docs/CCI_PROBE_FAULT.md` says patch `0031` “has never run on the phone”.
  That was true before the explicit `--replace` refresh; the user then
  confirmed all three rear cameras. Keep the fault analysis, but treat the
  “never run” verdict as superseded by the confirmation in section 1.
- `docs/CAMERA_DELIVERY.md` records a stale served cache `3707af89...` and
  an older artifact table. The current nura cache is `5fba6b57...` after the
  guest mounted and wrote it. Its lifecycle warnings (refresh/verify before
  boot, never treat the cache as source identity) remain valid.
- `docs/REAR_CAMERA_7_0_BRINGUP.md` contains an older sentence that the rootfs
  and boot image “still contain the old module”. The appended hardware
  confirmation supersedes it: the packaged rootfs has the fixed module and
  `boot.img` has no CCI module (only the packaged rootfs carries CCI).
- `docs/LIVE_BASELINE.md` is the 2026-11-09 6.17/port-9999 baseline (and an
  older interface name); the candidate uses port 10001 and its own worktree.
- `build/rear-camera/SHA256SUMS` and `ARTIFACTS.json` are pre-fix 11:36
  manifests; `DELIVERY.json` is authoritative for this build.
- `packaging-inputs.json` and `DELIVERY.json` contain absolute worktree paths;
  the checkpoint maps the standard relative paths onto the `--root` checkout
  and verifies hashes, so a manifest copied from another worktree is still
  detectable by content.

## 8. What was not performed

No `sudo`/`su`/`chroot`/`mount`, no host network/firewall/IP change, no
phone `adb`/`fastboot`/flash/reboot, no module load/unload, no sensor or I2C
write, no capture, no server start/stop, and no change to ports 10000/10001.
No source worktree was edited, committed, pushed or deleted. The port-10001
`nbd-server` observed during the checkpoint was left untouched.

The only hardware statement is the user's report recorded in section 1 and
the frozen handoff. Everything else is a source/build/manifest identity check.
