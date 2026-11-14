# Nura rc4: Phosh variant (`nura-phosh-rc4`)

**Status: source only.** Nothing on this page has been built, installed or
booted. The scripts were checked with `bash -n` and the rootless tests listed
in §6.

The local-userdata rootfs build (`flows/rc4/local-storage`) can now produce
either UI from the same rc4h kernel and the same device fixes:

| | Plasma Mobile | Phosh (default since 2026-11-14) |
| --- | --- | --- |
| selector | `--ui plasma` / `NURA_UI=plasma` / nothing | `--ui phosh` / `NURA_UI=phosh` |
| release label | `nura-plasma-rc4` | `nura-phosh-rc4` |
| state | `flows/rc4/local-storage/.work/local-userdata/` | `flows/rc4/local-storage/.work/local-userdata-phosh/` |
| output (boot.img, manifest) | `flows/rc4/local-storage/build/local-userdata/` | `flows/rc4/local-storage/build/local-userdata-phosh/` |
| UI package (built from the pinned pmaports, installed as `=<built version>`) | `postmarketos-ui-plasma-mobile` (6-r10) | `postmarketos-ui-phosh` (34-r0) |
| display manager | `plasma-mobile.service` | `greetd.service` (phrog greeter) |
| camera app | `plasma-camera` | `snapshot` (GNOME Snapshot, through PipeWire's libcamera plugin) |
| graphical package manager | `discover` + `discover-backend-apk` | `gnome-software` + `gnome-software-plugin-apk2` + `gnome-app-provider-gnome-software` |
| rounded corners | `plasma-mobile-device-presets/{oneplus,guacamole,qcom,sm8150-mtp}.conf` (device package) | patched `gmobile` 0.7.4-r100 with a guacamole display panel |
| GPU-fault session crash | not addressed | patched `phoc` 0.58.0-r100 with the thumbnail null-texture guard (§2.1) |
| no idle suspend (candidate policy) | logind drop-in + PowerDevil `AutoSuspendAction=0`; first boot unlocked | the same logind drop-in + gsettings `sleep-inactive-*-type='nothing'`; phrog asks for the password at every boot |
| default apps removed by the build | none (the image keeps every `_pmb_recommends` entry) | `phosh-tour`, `lpa-gtk`, `vvmplayer`, `decibels` (§4.1) |

The two UIs share:

* the rc4h kernel APK, the guarded netboot hook, `libcamera-rear`, the
  system libcamera 0.8 pins, ModemManager, `nura-clock`;
* the device package `device-oneplus-guacamole` **4-r16**, which adds the A/B
  slot marker (§3) and the Plasma corner preset;
* **`/etc/apk/world` pins** `linux-postmarketos-qcom-sm8150=<ver>`,
  `device-oneplus-guacamole=<ver>` and `firmware-oneplus-guacamole=<ver>`.
  The pins are passed to `pmbootstrap install --add` and checked in the
  installed rootfs (`check_local_candidate.py verify`). The current Plasma
  image pins only the kernel (its world file has a bare
  `device-oneplus-guacamole`, and the firmware package is not listed);
* **the PulseAudio audio backend** (`postmarketos-base-ui-audio-backend-pulseaudio`).
  Phosh works with it: phosh and gnome-settings-daemon use the PulseAudio API,
  which PulseAudio provides directly. pmaports `postmarketos-base-ui` also gives
  the pulseaudio provider priority 100 over pipewire's 90, so this is the
  normal choice for a GNOME-based UI too. `pipewire-pulse` is refused on both
  UIs. PipeWire itself still runs, for the cameras.

## 1. Package choices and sources

All names and versions were checked against the APKINDEX files of the pinned
edge snapshot that the current Plasma build used
(`.work/local-userdata/rootfs-state/pmbootstrap-work/cache_apk_aarch64`,
Alpine `v20260805-6306`) and the pinned pmaports (`.work/rear-camera/pmaports`,
commit `286d3b4e`):

* `postmarketos-ui-phosh` 34-r0 (pmaports `main/`). Like Plasma, it is built
  from the pinned recipe and installed as `postmarketos-ui-phosh=<built version>`.
* GNOME Software: `gnome-software` 51.1-r0. The apk plugin is packaged as
  **`gnome-software-plugin-apk2`** 0.0.3-r1, which `provides
  gnome-software-plugin-apk=1.0.0`. It talks to `apk-polkit-rs` 3.0.2, so
  system packages can be installed from the GUI. pmaports
  `postmarketos-base-ui-gnome` (subpackage `gnome-app-provider-gnome-software`,
  `provider_priority=100`) already depends on `gnome-software` and
  `gnome-software-plugin-apk`. The build lists them explicitly so that Bazaar
  (the other `gnome-app-provider`) cannot be selected instead.
* Plasma: `discover-backend-apk` 6.8.3-r0. `discover` is already installed,
  but the backend was missing, so Discover could not install system packages.
* `qbootctl` 0.2.2-r1 and pmaports `soc-qcom-qbootctl` exist, but are not used
  (see §3).
* `gmobile` 0.7.4-r0 (Alpine `community/gmobile`). The local recipe is the same
  APKBUILD plus one data patch, at pkgrel 100.
* `phoc` 0.58.0-r0 (Alpine `community/phoc`, aports commit `853ef7ae`). The
  local recipe is the same APKBUILD plus one render patch, at pkgrel 100 (§2.1).
  The pinned pmaports has no phoc recipe, so `main/phoc` in the isolated copy is
  the only recipe that shadows the Alpine binary; phosh depends on the `phoc`
  **package**, and phoc 0.58.0 installs no shared library (its `libphoc` is
  static inside the executable), so phosh is not rebuilt. phoc is built after
  gmobile because it needs `gmobile-dev`.

## 2. Rounded corners

* **Plasma**: the `plasma-corners` commits `945baff` and `a3141aa` are
  cherry-picked (top-level `device/testing/device-oneplus-guacamole`, pkgrel
  14). The same preset is installed by the device package that this flow
  ships (`flows/rc4/device/testing/device-oneplus-guacamole`, pkgrel 16),
  under both `oneplus,guacamole.conf` and `qcom,sm8150-mtp.conf`.
* **Phosh**: phosh (top-bar corner padding) and phoc read the panel geometry
  through `libgmobile.so.0`, keyed by the DT root compatible
  (`data/devices/display-panels/<compatible>.json`). gmobile 0.7.4 reads these
  files **only from its built-in GResource**: `gm-device-info.c` looks them up
  under `GM_DISPLAY_PANEL_RESOURCE_PREFIX`, and its own comment says
  "currently we only look at the built-in gresources". A JSON file shipped by
  the device package would therefore be ignored. The entry is compiled into a
  rebuilt gmobile instead (`flows/rc4/device/testing/gmobile`, patch `0001`),
  under both `oneplus,guacamole.json` and `qcom,sm8150-mtp.json` (the live
  boot DTB reports `qcom,sm8150-mtp`):

  ```json
  {"name": "OnePlus 7 Pro", "x-res": 1440, "y-res": 3120,
   "corner-radii": [145, 145, 145, 145], "width": 71, "height": 154}
  ```

  145 px is the circle that passes through the point the Plasma work measured
  on the LineageOS 223x223 px squircle mask: x < 70 px at 21 px from the edge.
  Solving r − √(r² − (r − 21)²) = 70 gives r = 145. The squircle is flatter
  than a circle, so this radius pads slightly more than the mask needs deeper
  into the bar. If the clock still clips, raise the radius (223 is the most
  conservative value). The patch was applied to the real gmobile-0.7.4
  tarball (sha512 matches the APKBUILD). With the real gmobile sources and the
  generated GResource, `gm_device_info_get_display_panel()` returns this panel
  for both compatibles: radii 145 ×4, no cutout.

### 2.1 rc4j: Phoc null-texture thumbnail guard (Phosh only)

After a GPU fault/reset the compositor died with SIGSEGV inside
`view_render_to_buffer_iterator()` (`src/render.c`), reached from
`thumbnail_frame_handle_copy()` when the overview asks for a window
thumbnail: `wlr_surface_get_texture()` returned NULL and
`wlr_render_pass_add_texture()` dereferenced it. That killed phoc and with it
the whole session (docs/NURA_GPU_FAULTS.md).

`flows/rc4/device/testing/phoc` is Alpine's `community/phoc` 0.58.0-r0 recipe
at `pkgrel=100` with one added patch:
`0001-render-skip-surfaces-that-have-no-texture.patch` (Alexandr Mironov,
written for 0.56.0 on a POCO X3 Pro; the hunk is refreshed from `-413,6` to
`-512,6` for 0.58.0, so it applies with `--fuzz=0` and no offset). The guard
is three lines, placed directly after the texture lookup and before the
render call:

```c
  texture = wlr_surface_get_texture (surface);
+  if (!texture)
+    return;
+
  phoc_view_get_geometry (data->view, &geo);
```

The flow builds it only for Phosh (`ui_builds+=(gmobile phoc)`), refuses any
other version (`0.58.0-r100`), installs it as `phoc=0.58.0-r100` next to
`gmobile=…` so `/etc/apk/world` keeps it, and verifies two things in the
installed rootfs: the `phoc` package version is `0.58.0-r100`, and
`/usr/bin/phoc` is **not** the unpatched Alpine binary (SHA256
`1645e7ab…b48245`, the build the crash was symbolized against).

**Scope: this prevents the session crash on this path, not the GPU faults.**
Kernel GPU faults, renderer corruption and damaged app content remain; a
session that survives with a corrupted desktop is not a repaired GPU. The
Plasma image does not build or install this phoc (KWin is its compositor).
Verified offline only: checksums, `patch -p1 --fuzz=0`, hunks and the flow
wiring. No abuild/meson build and no hardware test was done here.

## 3. A/B slot marker (both UIs)

ABL spends one retry on each boot of a slot that is not marked successful,
and Linux never marks it. The device package now ships:

* `/usr/libexec/guacamole-boot-slot`: omarchy-mobile's
  `devices/oneplus7pro/adapter/boot-slot.py` (commit `f702f68`), code
  unchanged. omarchy runs it on this phone as
  `/usr/local/sbin/guacamole-boot-slot mark-successful`, a minute after the
  desktop is up (`adapter/desktop-prepare.sh`). It sets the
  `successful` attribute bit of `boot_<slot>` in both GPT copies, updates the
  CRCs, and refuses mismatched copies, an unbootable slot or two active slots;
* `guacamole-boot-slot.timer`, which fires 2 minutes after boot, and
  `guacamole-boot-slot.service`, which runs only if `display-manager.service`
  is active (a kernel that never reaches the UI keeps spending retries, so ABL
  can still fall back);
* `82-guacamole-boot-slot.preset` (`enable guacamole-boot-slot.timer`).

No extra package is needed (the device package already depends on python3).
`qbootctl` would also work, but it finds the current slot from
`androidboot.slot_suffix` on the cmdline. The omarchy tool reads the active
bit directly, and it was already used on this phone. Check it with
`sudo /usr/libexec/guacamole-boot-slot status`. If the verify step warns that
the timer is not enabled in the image, run
`sudo systemctl preset guacamole-boot-slot.timer` on the phone.

## 4. Build (user-run)

The kernel identity file (`flows/rc4/build-source/kernel-output/kernel-inputs.json`)
records the checkout that built the kernel (`runtime-workspace`), and
`check_local_candidate.py` refuses to build from any other checkout. Run the
rootfs build there, after bringing in this branch (it is a fast-forward of
`rc4b`). Every script refuses to run as root: run it as your normal user, and
pmbootstrap asks for your sudo password itself.

```bash
cd runtime-workspace
git merge --ff-only phosh-variant          # only if that worktree may be updated

# read-only checks first
bash flows/rc4/scripts/rc4.sh check
bash flows/rc4/local-storage/scripts/local_storage_build.sh --ui phosh   # dry-run plan

# Phosh: new state under .work/local-userdata-phosh, Plasma state untouched
bash flows/rc4/scripts/rc4.sh rootfs --ui phosh --authorized-key /absolute/path/to/key.pub
```

For Plasma (same fixes, now with the world pins, Discover's apk backend and
device 4-r16), use `--ui plasma` or no `--ui`. **This rebuilds the image in
`.work/local-userdata/` in place.** Copy the current Plasma image first:

```bash
IMG=flows/rc4/local-storage/.work/local-userdata/rootfs-state/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img
sudo cp --reflink=auto "$IMG" ~/oneplus-guacamole-plasma-rc4h-$(date +%F).img
bash flows/rc4/scripts/rc4.sh rootfs --ui plasma --authorized-key /absolute/path/to/key.pub
```

Phosh results:

* rootfs image (pmOS_boot + pmOS_root, 4096-byte sectors):
  `flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img`
* boot image: `flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img`
* manifest: `flows/rc4/local-storage/build/local-userdata-phosh/DELIVERY.local.json`

The Phosh state takes its own copy of the kernel state and its own
pmbootstrap work directory, so expect it to download the package cache again
and to need a similar amount of disk space as the Plasma state.

### 4.1 Excluded default apps (Phosh only)

`postmarketos-ui-phosh` and the `postmarketos-base-ui-gnome*` packages it
depends on list their default apps in `_pmb_recommends`, so a plain build
installs all of them:

| package | recommended by |
| --- | --- |
| `phosh-tour` | `postmarketos-ui-phosh` (`main/postmarketos-ui-phosh/APKBUILD`) |
| `lpa-gtk` (eSIM Manager) | `postmarketos-base-ui-gnome-mobile` |
| `vvmplayer` (Visual Voicemail; its `vvmd` daemon is only used by it) | `postmarketos-base-ui-gnome-mobile` |
| `decibels` (Audio Player) | `postmarketos-base-ui-gnome` |

Nothing depends on them, so they are dropped from the image. Gapless
(`g4music`) stays.

**Why not `--add '!pkgname'`?** pmbootstrap has no "install every recommends
entry except X" switch. `create_device_rootfs()` appends the whole
`get_recommends()` result to the install list (`pmb/install/_install.py:1311-1312`
in the pinned pmbootstrap 3.12.1), and that version *rejects* a `!pkgname`
entry in any install package list (`pmb/chroot/apk.py:220-222`; the
`packages_split_to_add_del()` helper above it is unreachable). The
`depends="!pkgname"` skip is a pmaports-side mechanism
(`pmb/install/_install.py:1219-1242`) and would mean patching a recipe.

**What the flow does instead** (supported CLI only, data next to the UI
profile table):

* the list lives in `check_local_candidate.py` as
  `UI_PROFILES[...]["excluded_packages"]` (empty for Plasma) and is printed
  by `check_local_candidate.py exclude-list`;
* `rootfs_prepare_local.sh` installs in two passes when the list is not empty:
  1. `pmbootstrap install --no-image --add …` populates the device rootfs
     chroot — recommends included, no image written;
  2. `pmbootstrap chroot --rootfs -- sh -ec '… apk del …'` deletes the four
     apps from that chroot (only the ones that are really installed, so a
     rebuild of a cleaned chroot is a no-op) — the same mechanism the Plasma
     path already uses to drop GNOME Snapshot;
  3. `pmbootstrap install --no-split --filesystem ext4 --no-recommends
     --add …` writes the image from the cleaned chroot. `--no-recommends`
     keeps the deleted apps out of the second package resolution; the other
     recommends are already installed and explicitly recorded in
     `/etc/apk/world`, so they stay. Plasma keeps the single-install path
     because its list is empty;
* `check_local_candidate.py verify` fails the build if any name of the list is
  still in `/lib/apk/db/installed` (`verify_excluded_packages()`).

### 4.2 Shutdown after a failed build

A failed `pmbootstrap` run leaves its chroots mounted under
`…/rootfs-state/pmbootstrap-work`, and the next build stops with
`ERROR: local path has an active mount` (emitted by
`local_storage_build.sh`; the message now prints the command to run). Clear
only the selected UI's chroots with:

```sh
bash flows/rc4/scripts/rc4.sh shutdown --ui phosh      # or --ui plasma
bash flows/rc4/scripts/rc4.sh shutdown --ui phosh --dry-run   # print only
```

That runs exactly `python3 <state>/pmbootstrap/pmbootstrap.py -c
<state>/rootfs/pmbootstrap.cfg -p <state>/pmaports -w
<state>/pmbootstrap-work shutdown`, where `<state>` is
`flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state` for Phosh
and `.work/local-userdata/rootfs-state` for Plasma. It never force-unmounts,
prints the mounts it found, refuses to act if that pmbootstrap installation is
incomplete, does nothing (exit 0) when no mount is active, and reports the
remaining mounts and exits non-zero if one survives the shutdown. The printed
command is a single line with absolute paths and no shell variable
assignments, so it is also copy-pasteable in fish.

## 5. Putting Phosh on the phone

The repository does not record how the current Plasma image was written to
`userdata`: the flows stop at the image, and docs/NURA_RC4.md §6 leaves
delivery to the user. The steps below are the standard postmarketOS path for
this layout. `pmbootstrap flasher flash_rootfs` does the same thing as
`fastboot flash userdata <image>`, and the initramfs `mount_subpartitions()`
finds the two subpartitions inside `userdata`
(docs/DUALBOOT_FEASIBILITY.md). Afterwards, the matching boot image is
RAM-booted, as now.

> **WARNING: this replaces the current Linux rootfs on `userdata`, including
> `/home/user` and every package or setting changed by hand on the phone.**
> Android (LineageOS) lives on slot `_a` and is not touched; slot `_b` holds the nura Linux boot image. Back up first.

```bash
# 1. back up /home/user and the package list from the running Plasma system
#    (USB network up: sudo ip addr replace 172.16.42.2/24 dev <usb iface>)
ssh user@172.16.42.1 'tar -C /home -cpf - user' | gzip > ~/guacamole-home-user-$(date +%F).tar.gz
ssh user@172.16.42.1 'cat /etc/apk/world' > ~/guacamole-apk-world-$(date +%F).txt
gzip -t ~/guacamole-home-user-*.tar.gz && ls -l ~/guacamole-home-user-*.tar.gz

# 2. reboot into the bootloader
ssh -t user@172.16.42.1 'sync; sudo sh -c "sync; reboot bootloader"'

# 3. write the Phosh image to userdata (fastboot sends it in sparse chunks;
#    prefix sudo if your fastboot needs root for USB access)
cd runtime-workspace
platform-tools/fastboot flash userdata \
  flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img

# 4. RAM-boot the matching Phosh boot image (never `fastboot flash boot`);
#    scripts/fastboot_boot.sh adds the ABL stall recovery and uses fastboot from PATH
PATH="$PWD/platform-tools:$PATH" bash scripts/fastboot_boot.sh \
  flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img
```

Restore your files by copying back what you need, not the whole Plasma home
(its `~/.config` is for Plasma):

```bash
ssh user@172.16.42.1 'mkdir -p ~/restore'
gunzip -c ~/guacamole-home-user-<date>.tar.gz | ssh user@172.16.42.1 'tar -C ~/restore -xpf -'
```

On first boot, the phrog greeter asks for the password set during
`pmbootstrap install`. After two minutes,
`sudo /usr/libexec/guacamole-boot-slot status` should show the current slot
with `successful=1`.

### Back to Plasma

```bash
ssh -t user@172.16.42.1 'sync; sudo sh -c "sync; reboot bootloader"'
platform-tools/fastboot flash userdata ~/oneplus-guacamole-plasma-rc4h-<date>.img
#   (or the unchanged .work/local-userdata/.../oneplus-guacamole.img if Plasma was not rebuilt)
PATH="$PWD/platform-tools:$PATH" bash scripts/fastboot_boot.sh \
  flows/rc4/local-storage/build/local-userdata/boot/boot.img
```

The host image is the state at build time, not the phone's later state.
Restore `/home/user` from the backup, and re-apply hand-made changes (see the
saved `apk world` list). A rebuilt Plasma image already carries the
kernel/device/firmware pins and `discover-backend-apk`.

## 6. Verification done (rootless)

* `bash -n` on every changed shell script.
* `flows/rc4/tests/test_rc4_ui_variants.py` (new, 35 tests, 34 pass and one
  pre-existing skip) covers:
  * the release label for each UI, and refusal of an unknown UI;
  * a dry run of a copy of the entrypoint, showing that Phosh uses
    `-phosh` state/output and Plasma keeps its original paths;
  * the Phosh recipe policy edit: idempotent, with correct sha512sums;
  * the world-pin check (passes when pinned; fails on today's unpinned device
    entry);
  * the libgmobile panel check;
  * the per-UI exclusion list (Phosh has the four, Plasma none), its CLI
    output, and the post-install check that fails on each excluded package;
    plus the install order in `rootfs_prepare_local.sh` (populate the chroot,
    `apk del`, then write the image with `--no-recommends`);
  * `rc4.sh shutdown` with fake `findmnt`/`python3`: a state without mounts is
    a no-op, the dry run prints the exact command and never calls pmbootstrap,
    an incomplete state is refused, the pmbootstrap argv (config, pmaports,
    work dir) is exact, and a mount that survives the shutdown fails;
  * the device-package wiring and sha512sums;
  * the gmobile patch content and checksum;
  * the GPT fixture tests ported from omarchy.
* rc4j (§2.1), added after that run: the phoc recipe's `source=`/
  `sha512sums=` pairing, its patch sha512 and the upstream tar.xz sha512
  (`8abdf88f…a15dcc`, re-downloaded from `sources.phosh.mobi`), the guard's
  position before the render call, the Phosh-only build/pin wiring and the
  `verify_phoc_guard()` refusals. The patch was applied for real to an
  extracted phoc-0.58.0 tarball with `patch -p1 --fuzz=0`: no fuzz, no offset.
  No abuild/meson build (neither `abuild` nor `meson` exists on the host) and
  nothing on the phone.
* The world-pin check fails on the current Plasma rootfs tree (bare
  `device-oneplus-guacamole`), as expected.
* The plasma-mobile UI recipe edit is byte-for-byte the code it replaced.
* `rootfs_prepare_local.sh --check-only` runs with `NURA_UI=plasma` and
  `NURA_UI=phosh` up to the kernel-identity gate.

**Not verified:**

* the pmbootstrap build of either image, and the two-pass Phosh install in
  particular (`install --no-image`, then `apk del` in the real chroot, then
  `install --no-recommends`);
* `apk del` on the real Phosh chroot (whether it removes only the four apps
  and their exclusive dependencies);
* the `rc4.sh shutdown` command against real pmbootstrap mounts;
* the `gmobile` abuild `check()`;
* the `phoc` abuild build (its `-Dembed-wlroots` subproject) and the patched
  compositor on the hardware: the guard was not exercised against a real GPU
  fault, so it is expected but not measured to keep the session alive;
* apk resolution of the Phosh package set;
* Phosh on the hardware (display, corner padding, audio, cameras, GNOME
  Software installs);
* the boot-slot unit on this rootfs;
* the `fastboot flash userdata` step.

## Persistent charge limits (device package 4-r18)

The rc4 device package exposes the existing charging policy as a CLI:

```sh
sudo guacamole-chgctrl limit 75 80
guacamole-chgctrl status
```

Arguments are resume then stop percentages: `0 <= resume < stop <= 100`.
For example, `limit 60 85` resumes at 60% and inhibits input at 85%.
`limit 0 100` permits full charging while keeping thermal protections.
The setter requires systemd and root, saves a dedicated service drop-in and
restarts `guacamole-chgctrl.service`. Changes survive reboot of that rootfs.
Inspect configured values with `systemctl show guacamole-chgctrl.service -p Environment`.

Fresh builds include the CLI but default to full charging; previously chosen
limits are not copied automatically when replacing the rootfs. The current
hardware mechanism suspends charger input, so the phone runs from battery
while held. Polling and system suspend can delay threshold enforcement.
Setting limits is not evidence of a validated stop/resume cycle on hardware.
