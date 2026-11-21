# Installing Nura on the OnePlus 7 Pro

This is the current path from a fresh clone to a phone running Nura (rc4
kernel `7.0.0-sm8150-guacamole-rc4`, Phosh by default), with the rootfs on
`userdata` and the boot image on `boot_b`. Every step runs through `./nura`
(`./nura help`). It runs in Bash, so it works the same from fish.

> [!WARNING]
> Experimental. The install **erases `userdata`**: Android's data is lost, and
> on a phone that already runs Nura, so is everything under `/home/user`
> (`./nura install` backs that up first when it can reach the phone over SSH).
> Read the warning in [README.md](README.md) first.

The details behind each step (variables, pins, retries) are in
[docs/BUILD_ENVIRONMENT.md](docs/BUILD_ENVIRONMENT.md).

## What you need

- **Host:** Linux with about 40 GB free, sudo (pmbootstrap uses chroots),
  Clang/LLD 23.x and pahole for the kernel. `./nura prereqs` lists everything
  that's missing, with the install command for pacman, apt or dnf.
- **Lineage 24 OTA zips for guacamole.** They supply the stock DTBO (any
  Lineage 24 build works), plus the firmware and sensor bundles. Those two are
  pinned to the `20261011` (firmware) and `20261102` (sensors) builds.
  `./nura firmware` reports a different build and does not substitute it.
- **Phone:** bootloader unlocked, slot `b` active, a USB cable to the host.
  The installer checks both settings but does not change them. This guide
  doesn't cover unlocking the bootloader or preparing the slots.

## 1. Host setup and inputs

```bash
./nura prereqs                                     # what to install, per distro
cp build-env.example.sh build-env.local.sh         # then edit; ./nura loads it
./nura fetch --lineage-ota /path/to/lineage-24.0-*-guacamole-signed.zip
./nura firmware /path/to/lineage-24.0-20261011-*-guacamole-signed.zip
./nura firmware /path/to/lineage-24.0-20261102-*-guacamole-signed.zip
./nura baseline                                    # pinned pmaports + pmbootstrap
./nura env                                         # check the settings in use
```

- `fetch` downloads, verifies and never overwrites: platform-tools
  (fastboot), mkbootimg, avbtool, the kernel source archive, the pinned
  `apk.static` 3.0.9 for pmbootstrap and the DTB tools.
  It also extracts the stock `dtbo.img` from the OTA. `./nura fetch --check`
  only verifies.
- `firmware` extracts the two bundles and installs each one only if it
  matches its pin. Run it once per OTA zip; each one supplies the bundle it
  matches.
- `baseline` recreates `.work/baseline` from public sources (about 25 s).
  `./nura` uses it automatically while `NURA_BASELINE_ROOT` is unset. If you
  have a preserved runtime workspace instead, set `NURA_BASELINE_ROOT` in
  `build-env.local.sh`.

## 2. Signing key

Packages are signed with one project key in `~/.config/nura-guacamole/abuild/`
(`NURA_SIGNING_KEY_DIR` overrides it). The phone trusts the key its rootfs was
built with.

- **First install:** there's nothing to do. The first build generates the key
  and saves it there. Back that directory up.
- **The phone already runs a Nura rootfs** that you want to keep updating with
  kernel packages: adopt the key from the work directory that built it,
  *before* building or cleaning:

  ```bash
  ./nura signing-key adopt flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work
  ./nura signing-key status
  ```

## 3. Build

```bash
JOBS=16 ./nura kernel       # rootless kernel build
./nura check                # read-only recipe and rootfs preflight
./nura build                # rootfs + boot image (sudo via pmbootstrap)
./nura debug-image          # matching debug boot image, needed by the installer
```

- `build` asks once for the phone user's password (greeter and `sudo`). For
  an unattended build, put the password on the first line of a `chmod 600`
  file and set `NURA_USER_PASSWORD_FILE`.
- Without a preserved baseline, use `./nura build --fresh-baseline`. It builds
  libcamera 0.8 and `libcamera-rear` from their recipes instead of reusing the
  hardware-tested rear1 APKs, so test the cameras after the first fresh build.
- Add `--ui plasma` to any command for Plasma instead of Phosh.
- Outputs: `flows/rc4/local-storage/build/local-userdata-phosh/`
  (`DELIVERY.local.json`, boot image) and `.../build/local-debug-phosh/`.
- A failed build can leave chroots mounted: run `./nura shutdown` before
  retrying.

## 4. Install

Put the phone in fastboot. From Nura:
`ssh user@172.16.42.1 'sync; sudo sh -c "sync; reboot bootloader"'`. From
Android: `adb reboot bootloader`. If the phone runs Nura and you want the
`/home/user` backup, keep it booted for the installer instead: it reboots the
phone into fastboot after taking the backup.

```bash
./nura preflight            # read-only checklist: host, build, phone
./nura install --dry-run    # host checks + read-only getvars
./nura install              # the real install
```

`install` re-verifies the build manifest, backs up `/home/user` and
`/etc/apk/world` to `backups/userdata-install-<time>/` when SSH works, and
checks for one device, a guacamole product, `unlocked`, slot `b` and enough
room on `userdata`. After you type `ERASE USERDATA`, it flashes `userdata`.
Then it RAM-boots the debug image, runs a read-only `e2fsck` of both
subpartitions, continues the boot and waits for SSH.

If the bootloader reports an unexpected `product` (for example `msmnile`),
check that the phone really is a OnePlus 7 Pro, then rerun with
`--accept-product <value>`.

## 5. Make it the default boot

`install` never writes `boot_b`; at the end it **prints** the commands for
this step. Test the normal image in RAM first, then flash it:

```bash
# from fastboot:
PATH=$PWD/platform-tools:$PATH bash scripts/fastboot_boot.sh flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img
# check it over SSH (ssh user@172.16.42.1), go back to fastboot, then:
sha256sum flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img   # the hash install printed
platform-tools/fastboot flash boot_b flows/rc4/local-storage/build/local-userdata-phosh/boot/boot.img
platform-tools/fastboot reboot
```

Never flash `boot_a`. Android stays on slot `_a`, untouched.

## After installing

- **Kernel upgrades** keep `userdata`. A same-ABI change needs only a new
  `boot_b` image; a changed ABI needs the kernel APK installed on the phone
  first. See
  [Upgrading the kernel](docs/BUILD_ENVIRONMENT.md#upgrading-the-kernel-while-keeping-userdata).
- **Disk space:** `./nura clean` lists stale work directories and
  `./nura clean --apply` removes them. Add `--rootfs` for the pmbootstrap work
  dir; it stays refused until the signing key is adopted.
- **Recovery:** after a panic, `scripts/ramoops.sh` (from TWRP). For a broken
  rootfs, see "Rootfs repair" in [CLAUDE.md](CLAUDE.md#device-gotchas). Device
  status and known issues are in
  [docs/WORKSPACE_STATUS.md](docs/WORKSPACE_STATUS.md).
