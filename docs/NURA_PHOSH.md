# Nura rc4: Phosh variant (`nura-phosh-rc4`)

**Status: source only.** Nothing on this page has been built, installed or
booted. The scripts were checked with `bash -n` and the rootless tests listed
in §6.

The local-userdata rootfs build (`flows/rc4/local-storage`) can now produce
either UI from the same rc4h kernel and the same device fixes:

| | Plasma Mobile (default) | Phosh |
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
| no idle suspend (candidate policy) | logind drop-in + PowerDevil `AutoSuspendAction=0`; first boot unlocked | the same logind drop-in + gsettings `sleep-inactive-*-type='nothing'`; phrog asks for the password at every boot |

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
* `flows/rc4/tests/test_rc4_ui_variants.py` (new, 17 tests, all pass) covers:
  * the release label for each UI, and refusal of an unknown UI;
  * a dry run of a copy of the entrypoint, showing that Phosh uses
    `-phosh` state/output and Plasma keeps its original paths;
  * the Phosh recipe policy edit: idempotent, with correct sha512sums;
  * the world-pin check (passes when pinned; fails on today's unpinned device
    entry);
  * the libgmobile panel check;
  * the device-package wiring and sha512sums;
  * the gmobile patch content and checksum;
  * the GPT fixture tests ported from omarchy.
* The world-pin check fails on the current Plasma rootfs tree (bare
  `device-oneplus-guacamole`), as expected.
* The plasma-mobile UI recipe edit is byte-for-byte the code it replaced.
* `rootfs_prepare_local.sh --check-only` runs with `NURA_UI=plasma` and
  `NURA_UI=phosh` up to the kernel-identity gate.

**Not verified:**

* the pmbootstrap build of either image;
* the `gmobile` abuild `check()`;
* apk resolution of the Phosh package set;
* Phosh on the hardware (display, corner padding, audio, cameras, GNOME
  Software installs);
* the boot-slot unit on this rootfs;
* the `fastboot flash userdata` step.
