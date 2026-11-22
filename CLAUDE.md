# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A bringup workspace for running postmarketOS / Nura on the OnePlus 7 Pro
(`oneplus-guacamole`, Qualcomm SM8150 / Snapdragon 855). The current
generation is **rc4**: kernel `7.0.0-sm8150-guacamole-rc4` (from the
`v7.0.0-sm8150` tag, `LLVM=1`, local patches), Phosh by default (Plasma
optional). The rootfs sits on `userdata`, and the boot image on `boot_b`.

- **Build and install:** [INSTALL.md](INSTALL.md) is the path from a fresh
  clone to an installed phone. [docs/BUILD_ENVIRONMENT.md](docs/BUILD_ENVIRONMENT.md)
  has the variables, pins and retry/upgrade procedures.
- **What runs on the phone now:** [docs/WORKSPACE_STATUS.md](docs/WORKSPACE_STATUS.md)
  (authoritative) and the status table in [README.md](README.md).
- **Install-work status and deferred decisions:** `notes/INSTALL_IMPROVEMENTS.md`
  and `notes/REVISIT_LATER.md`. Both are gitignored and local only.

## The current flow: `./nura`

Every step goes through `./nura` (Bash; works from fish). It loads
`build-env.local.sh` when present (template: `build-env.example.sh`) and parses
`--ui phosh|plasma` once.

```
./nura prereqs | fetch [--lineage-ota ZIP] [--check] | firmware OTA_ZIP | baseline
./nura signing-key [status|adopt WORK|seed WORK]
./nura kernel | check | build [--fresh-baseline] | debug-image
./nura preflight | install [--dry-run] | shutdown | clean [--apply] [--rootfs] | env
```

Where things live:

- `flows/rc4/`: kernel recipe (`device/testing/linux-postmarketos-qcom-sm8150`,
  patches `0001`–`0075`), the rc4 device package, `scripts/rc4.sh`
  (check/contract/kernel/shutdown) and `tests/`. Topic write-ups are in `topics/`.
- `flows/rc4/local-storage/`: the user-run rootfs and boot-image flow
  (`scripts/local_storage_build.sh`, `install_userdata.py`). Its
  `device/testing/` entries are symlinks into `flows/rc4/device/testing` and
  `flows/local-storage/device/testing`; edit the link targets.
- State and outputs: `flows/rc4/local-storage/.work/local-userdata-phosh/`
  (pmbootstrap work dir and kernel copy) and `.../build/local-userdata-phosh/`
  (`DELIVERY.local.json`, boot image). The kernel builds into `RC4_KERNEL_ROOT`
  (for example `.work/rc4o-build`) or `flows/rc4/build-source/`.
- `scripts/`: helpers called by `./nura`, indexed in `scripts/README.md`.
  `archive/scripts/`: superseded 6.17/7.0 scripts (see `archive/README.md`).
- Ignored local inputs: `local/` (host tools, `local/sources/` kernel
  tarball), `platform-tools/`, `ref/lineageos/`, and the firmware and sensor
  tarballs next to their APKBUILDs.

## Rules for working here

- **Phone writes.** Test every boot image in RAM first with
  `scripts/fastboot_boot.sh <img>`; only then `fastboot flash boot_b`. Never
  flash `boot_a`, `dtbo`, `vbmeta`, or change the active slot. `userdata` is
  written only by `./nura install`, which needs a typed confirmation. The
  ABL often stalls at `Sending 'boot.img'` or answers `Load Error`;
  `fastboot_boot.sh` resets the USB device and retries.
- **User-run steps.** `./nura build`, `install`, `shutdown` and
  `clean --rootfs` need sudo or the phone; the flow scripts mark them
  `USER RUN`. Hand them to the user unless asked to run them.
- **pmbootstrap.** Never run two pmbootstrap commands at once:
  `checksum`/`build` wipe the shared build dir and kill a running build. After
  a failed build, `./nura shutdown` clears leftover chroot mounts; never
  force-unmount them.
- **Signing key.** One project key in `~/.config/nura-guacamole/abuild/`. The
  phone only installs packages signed with the key its rootfs was built
  with. Don't delete a pmbootstrap work dir whose key isn't the project key
  (`./nura clean` enforces this).
- **Git.** `main` is a public fork. Commit locally; push only when the user
  says so. Never commit the large binaries (`*.img`, TWRP zip,
  `stock_boot*.img`), `build-env.local.sh` or anything under `notes/`.

## Testing

There is no app; "testing" means host checks, then booting the image and
checking over SSH (`ssh user@172.16.42.1`, USB gadget networking; key auth,
`sudo` needs the phone password).

- Host: `./nura check` (read-only recipe and rootfs preflight),
  `python3 -m unittest discover -s flows/rc4/tests` (rc4 flow), and
  `python3 -m unittest tests/test_<name>.py` (scripts; pytest isn't
  installed). The full `tests/` suite has known failures from older
  generations, so compare it with the state before your change, not with
  zero.
- Phone: `dmesg`, `journalctl -b`, the hardware itself. Reboot into the
  bootloader with `ssh user@172.16.42.1 'sync; sudo sh -c "sync; reboot bootloader"'`.

## Kernel upgrades on an installed phone

The release string stays `7.0.0-sm8150-guacamole-rc4` across candidates.
Compare `Module.symvers` and the BTF with the running kernel. If they're
unchanged, only a new `boot_b` image is needed. If the ABI changed, install
the kernel APK on the phone (`apk add`, then `sync`), then boot the matching
image. Details: BUILD_ENVIRONMENT.md, "Upgrading the kernel while keeping
userdata".

## Reference material (untracked, read-only)

- `hotdog-linux-bringup/`: OnePlus 7T Pro (`hotdog`) bringup, a sibling
  device. Useful for SM8150 patterns, but the hardware differs: for example,
  hotdog's touch power comes from PM8150L, while guacamole uses PM8150 LDO17
  plus GPIO 119.
- `qcom/`: upstream SM8150 audio references (WCD9340 dtsi, Xiaomi
  cepheus/raphael DTS, `qdsp6`, `sm8150.c`).
- `build/audio_ref/stock_vendor_b/`: stock `tfa98xx.cnt` and
  `mixer_paths_tavil.xml`, the source of truth for audio routes (parse with
  `hotdog-linux-bringup/scripts/inspect-tfa-container.py`).
- `ref/lineageos/`: LineageOS 24 guacamole images and OTA. This is also the
  firmware source: WLAN, board data, Bluetooth and IPA via
  `./nura firmware`.
- `linux-v6.17.0-sm8150/`, `extracted_dtb/device.dts` (live DTB from stock
  Android), `backups/dtbo_*.img`.

## Device gotchas

- **Charger.** PM8150B charging needs patch `0011` (SMB5 limits). Without it,
  `qcom_smbx` programs about 4.85 V float voltage. After any change to the
  charger or the `battery` node, read back `0x1070` (float voltage), `0x1061`
  (fast-charge current) and `0x1370` (input limit) from
  `/sys/kernel/debug/regmap/0-02/registers` before leaving the phone on a
  charger.
- **Charging policy** lives in `guacamole-chgctrl` (device package, systemd).
  It sets `pm8150b-charger/input_current_limit` from the PD contract or BC1.2
  type and suspends the input via `status` (`0` suspends, `1` resumes). It
  rewrites `status` every 5 s (`POLL_S`), so a hand-written `status=0` is
  undone within 5 s while it runs: stop the daemon before experimenting by
  hand, and never leave the input suspended. The charge window
  (`guacamole-chgctrl limit`) works through this input suspend (verified on
  the phone, 2026-11-22). A kernel `charge_behaviour`/inhibit-charge patch for
  `qcom_smbx` was written and dropped as unnecessary; don't redo it.
  `/run/guacamole/charger` shows the source type. The board never
  passes a 9 V PD supply to the PMIC, so the connector sinks 5 V/3 A only.
  Don't re-add 9 V/12 V PDOs without new evidence.
- **Fuel gauge.** `ti,bq27411` on `i2c@a80000`. Never add `monitored-battery`
  to it, because that lets the driver write the gauge's data memory. Its
  `capacity` is voltage-based and reads 0 / Critical near 3.4 V under load
  even when `charge_now` says 30%. UPower powers off on Critical, so treat it
  as real: use the 5 V DCP charger (1.8 A), not a USB 2 port.
- **Wi-Fi.** Never test by unbinding `ath10k_snoc` or by restarting
  `remoteproc0` at runtime: the MSA region stays assigned to the modem, and a
  reboot from that state has dropped the phone into Qualcomm crashdump mode
  (recover with Power + Volume Up). Use a fresh boot per test. For debugging,
  add `ath10k_core.debug_mask=0x300022` to the cmdline. Reboots with Wi-Fi on
  rely on `qcom,dload-mode` on the `scm` node (TCSR `0x1fc0000`, offset
  `0x13000`).
- **UFS** must stay at HS-G3 (patch `0010`): HS-G4 silently drops buffered
  writes. After suspected corruption, run `apk audit --system` and re-hash
  after `echo 3 > /proc/sys/vm/drop_caches`. UFS uses 4096-byte sectors. `sdX`
  names aren't stable (the LUNs probe asynchronously), so use
  `/dev/disk/by-partlabel/` or SCSI addresses.
- **DTBO.** ABL splices the active slot's `dtbo` into whatever DTB it boots.
  rc4 images carry the ABL-quarantine wrapper DTB
  (`scripts/prepare_temporary_dtb.py`, [docs/TEMPORARY_BOOT.md](docs/TEMPORARY_BOOT.md)),
  so the stock DTBO stays in place. If a boot hangs silently with no early
  output, check the wrapper first. Older setups zeroed the DTBO; the
  originals are in `backups/`.
- **Sensors** run on the SLPI DSP through `hexagonrpcd` and `iio-sensor-proxy`
  (libssc), both locally patched aports in the flow's `device/testing`.
  Never restart `hexagonrpcd` once it has attached to the sensor PD: the
  PD is created on the first attach only. Phosh releases the accelerometer
  while the screen is off, so test with the screen awake
  (`sudo monitor-sensor --accel`).
- Device packages that ship Hexagon `.so` files need `!strip !tracedeps`.
- **Sync.** Run `sync` on the phone after every `apk add` and before any
  reboot. A kernel upgrade followed straight by `reboot bootloader` once left
  `/usr/lib/modules/<ver>/` pointing at freed inodes, so no modules loaded on
  the next boot.
- **Rootfs repair.** Boot an image with `pmos.debug-shell` (the debug image
  from `./nura debug-image`), then `nc 172.16.42.1 23`. In that shell, find
  `userdata` by `PARTNAME=userdata` in `/sys/class/block/*/uevent` (it was
  `/dev/sda19`, but `sdX` moves; `install_userdata.py` does the same
  lookup), then run
  `losetup --show -Pf --direct-io=on --sector-size 4096 <dev>`,
  `e2fsck -fy /dev/loopNp2`, `losetup -d`, `pmos_continue_boot`. Restore
  cleared package files by extracting the APK over `/`, then run `depmod -a`,
  `apk audit --system` and `udevadm trigger --action=add`.
- **USB tethering** is the phone's local `usb-internet.service` (default route
  via `172.16.42.2`, metric 30000). Keep the metric: a lower one captured all
  traffic and DNS whenever the PC was not forwarding.
- **Panics.** Reboot into TWRP and run `scripts/ramoops.sh` to pull pstore
  logs into `logs/ramoops_<timestamp>/`.

## History: the 6.17 workflow

Before rc4 the dev loop edited the flattened, decompiled
`base_guacamole_working.dts`, which uses raw numeric phandles: new nodes take
an unused `phandle = <0x...>`. `scripts/patch_dtb.py` applied exact-string
fix-ups (reserved memory, `qcom,msm-id`/`qcom,board-id`), and its assert
anchors on the `rmtfs-mem` block formatting. `archive/scripts/build_bootimg.sh`
packed the image. The kernel was the 6.17 recipe in `device/testing/`, built
via pmbootstrap's `envkernel`. Those scripts are in `archive/scripts/` and
don't run from there as-is; `archive/README.md` explains how to run one from
git history. The 6.17 notes are in `docs/PLAN.md`, `docs/BRINGUP_GUIDE.md` and
`docs/KERNEL_PATCHES.md`.
