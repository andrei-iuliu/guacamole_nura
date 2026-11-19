# Nura rc3 kernel and device integration (source-ready)

Status: **source-ready only.**  This change prepares the rc3 kernel/device
sources and rootless checks.  No kernel, module, APK, image or boot image was
built, and nothing was installed, served or run on the phone.  Passing the
checks below does not establish that the kernel compiles or that haptics,
slider, torch or the clock work on hardware.  Actual builds remain USER-RUN
under the delivery pipeline.

Owned by the `kernel-device` worker:

- `device/testing/rc3/linux-postmarketos-qcom-sm8150/` (new kernel recipe;
  the canonical `device/testing/rear-camera/...` recipe is untouched);
- `device/testing/device-oneplus-guacamole/` (canonical device APKBUILD and
  the new device files listed below);
- `device/testing/nura-clock/` (new clock-floor package);
- `tests/test_rc3_device_sources.py`;
- this document.

Scripts, shared candidate checkers and `docs/NURA_RC3.md` belong to the
delivery worker and were not edited.

## 1. Kernel recipe

| Item | Value |
| --- | --- |
| Recipe | `device/testing/rc3/linux-postmarketos-qcom-sm8150` |
| Package | `linux-postmarketos-qcom-sm8150` 7.0.0-r4 |
| Kernel release (`uname -r`) | `7.0.0-sm8150-rc3` |
| `CONFIG_LOCALVERSION` | `"-sm8150-rc3"` |
| Upstream archive | `linux-v7.0.0-sm8150.tar.gz`, sha512 unchanged (`c1bdb11a…`) |
| Legacy patches | 0001–0037 are byte-identical copies of the rc2 recipe |

The recipe source list keeps the rc2 order and appends four patches.  The
review proposals used colliding numbers (torch 0038/0039 and haptics
0038/0039); rc3 renumbers them coherently:

| rc3 | Content | Origin |
| --- | --- | --- |
| `0038-arm64-dts-guacamole-rear-flash-bringup.patch` | rear flash torch only, ganged channels 1+2, `led-max-microamp = <70000>` (70 mA aggregate) | `flash-investigation/proposals/0038-...-bringup.patch` (reviewed) |
| `0039-input-aw8697-kconfig-makefile.patch` | `CONFIG_INPUT_AW8697`, Kconfig + Makefile wiring; module `aw8697_haptics.ko` | haptics proposal `0038-input-aw8697-kconfig-makefile.patch` |
| `0040-input-aw8697-haptics.patch` | new file `drivers/input/misc/aw8697_haptics.c` | haptics proposal `aw8697_haptics.c` |
| `0041-arm64-dts-guacamole-haptics-alert-slider.patch` | static i2c7 + AW8697 at 0x5a, reset GPIO 116, three-position alert slider | haptics proposal `0039-arm64-dts-...` |

The stock-flash follow-up (`0039-...-stock.patch`, 600 mA torch / 1 A flash)
is **not** included.  The bring-up node deliberately advertises no strobe
capability; Plasma Mobile's flashlight tile writes `max_brightness`, so the
single 70 mA aggregate cap is the limit for a normal toggle.

### 1.1 Config changes

Only the rc3 copy differs from `device/testing/rear-camera/...`:

```
CONFIG_LOCALVERSION="-sm8150-rc3"          # was "-sm8150-rear1"
CONFIG_INPUT_AW8697=m                      # added after CONFIG_INPUT_AW86927
# CONFIG_RTC_HCTOSYS is not set             # was CONFIG_RTC_HCTOSYS=y
                                           # CONFIG_RTC_HCTOSYS_DEVICE dropped
```

`CONFIG_RTC_DRV_PM8XXX=m`, `CONFIG_RTC_CLASS=y`, `CONFIG_RTC_INTF_DEV=y` and
`CONFIG_RTC_SYSTOHC=y` stay, so `/dev/rtc0` and alarms remain available.  The
vendor counter is no longer copied into `CLOCK_REALTIME` at probe; it has no
Unix offset in this DT and would otherwise overwrite PID 1's epoch.  Do not
add `nvmem-cells`, `qcom,uefi-rtc-info` or `allow-set-time` to the RTC node.

### 1.2 Device tree and driver

- `&i2c7` is enabled at 400 kHz.  QUP0 is already powered for i2c4/i2c8 and
  `gpi_dma0` stays disabled, so i2c7 uses the same FIFO path.
- `haptics@5a` is `awinic,aw8697`, `reset-gpios = <&tlmm 116 GPIO_ACTIVE_LOW>`.
  No interrupt is described (the driver does not use the stock GPIO 24 IRQ).
- The alert slider is a static `gpio-keys` node named `Alert slider` on TLMM
  27/125/134 (active low, `bias-disable`, `ABS_SND_PROFILE` 0/1/2).  Missing
  SoC pull configuration would read the board's pull-ups low permanently,
  hence the explicit pinctrl state.
- The in-tree driver exposes `FF_RUMBLE` through `input_ff_create_memless` and
  programs the 170 Hz actuator profile.  Input name: `Awinic AW8697 haptics`.

The driver was ported from the reviewed OnePlus 7T Pro (hotdog) work and its
7.0 API use was reviewed against the prepared 7.0 source (`i2c_driver.probe`
and `.remove` signatures, `input_ff_create_memless`,
`regmap_read_poll_timeout`, `devm_regmap_init_i2c`, `dev_err_probe`).  It was
**not compiled**.

## 2. Device package

`device/testing/device-oneplus-guacamole/APKBUILD` is now `pkgver=4 pkgrel=14`.
It gains:

| File | Installed as |
| --- | --- |
| `nura-boot-diagnostics` (byte-identical copy of `scripts/collect_boot_diagnostics.sh`) | `/usr/bin/nura-boot-diagnostics` (0755) |
| `nura-boot-diagnostics.service` | `/usr/lib/systemd/system/` |
| `nura-boot-diagnostics.timer` | `/usr/lib/systemd/system/` |
| `81-nura-boot-diagnostics.preset` | `/usr/lib/systemd/system-preset/` |
| `72-guacamole-haptics.rules` | `/usr/lib/udev/rules.d/` |

`depends=` gains `nura-clock`; the device package does not ship
`/usr/lib/clock-epoch` itself, so the two packages never own the same file.

The udev rule tags `Awinic AW8697 haptics` as `FEEDBACKD_TYPE=vibra` and
`TAG+="uaccess"` for the Plasma user, which stock `72-feedbackd.rules` cannot
do because it only knows a fixed list of other driver names.  The
`Alert slider` line is a name-based fallback for feedbackd's
`fbd-alert-slider` handler.  The rule name starts with `72-` so it is applied
before `73-seat-late.rules` grants the ACL.

**No `modules-load.d` file is shipped.**  The in-tree driver has an OF
modalias (`of:N*T*Cawinic,aw8697`) and is autoloaded by udev coldplug when
i2c7 creates the device, which happens before the graphical session.  If a
future boot shows no `aw8697_haptics` module, the smallest fallback is to add
the reviewed `guacamole-haptics.modules-load.conf`; do not add a different
load path speculatively.

The boot-diagnostics helper remains single-authoritative in
`scripts/collect_boot_diagnostics.sh`; the device-package file is a copy and
`tests/test_rc3_device_sources.py` fails if the bytes diverge.

## 3. Clock floor package

`device/testing/nura-clock/` installs exactly one file:

- `/usr/lib/clock-epoch`, whose **mtime** is the systemd clock epoch.
  `abuild` stamps payload files with `SOURCE_DATE_EPOCH`, so a normal user
  build installs the rc3 source/build date.  The file content is the declared
  lower bound `1794268800` (`2026-11-10T00:00:00Z`) as an inert marker; systemd
  reads only the mtime.  The current delivery checker expects a 10-digit
  content marker, so the package provides the declared floor there while the
  mtime remains authoritative.  If the checker is changed to mtime-only, the
  marker is harmless.

`_rtc_guard=0`: there is **no** `rtc_pm8xxx` blacklist and no
`/etc/modprobe.d/nura-rtc.conf`.  The rc3 kernel has `CONFIG_RTC_HCTOSYS` off,
so `rtc0` stays present for alarms while the raw vendor counter can never
become `CLOCK_REALTIME`.

A reproducible lower bound is declared in the test: `1794268800`
(2026-11-10T00:00:00Z, at or before the rc3 source checkout commit).  The source
payload and the APKBUILD mtime are at or after it; a simulated install must
produce a PID-1 epoch in `[1794268800, 1798761600]` (2027-01-01 sanity
ceiling).  This is a
**lower-bound stamp, not accurate UTC**: without an uplink `systemd-timesyncd`
cannot correct the clock, and the NBD initramfs before `switch_root` still has
1970 timestamps.  With an uplink the already-enabled native timesyncd corrects
the clock; no RTC write, vendor-offset change or host NTP dependency is added.

Trade-off: with HCTOSYS off, the RTC fallback sleep-time injection is compiled
out.  rc3 disables automatic suspend (`AutoSuspendAction=0`); revisit this
before enabling suspend.

## 4. Delivery-worker contract (not implemented here)

The delivery worker owns scripts and manifests.  Required rc3 wiring:

1. Build input recipe:
   `device/testing/rc3/linux-postmarketos-qcom-sm8150`, release
   `7.0.0-sm8150-rc3`, isolated state `.work/rc3-kernel` and output
   `build/rc3-kernel`.  Keep `.work/rear-camera` and `build/rear-camera`
   read-only.
2. Add `nura-clock` to the individual `pmbootstrap build` list and to the
   `install --add` list (or install it through the device dependency after
   building it).  The build list currently uses
   `--ignore-depends --no-depends`, so a dependency alone does not build it.
3. In the candidate checker, verify `/usr/lib/clock-epoch` exists, is a regular
   file, and its mtime is within the lower-bound window above.  Keep the check
   of the kernel release file equal to `7.0.0-sm8150-rc3`.
4. Rebuild the kernel package, DTBs, ABL-quarantine wrapper, initramfs, boot
   image and rootfs from the actual build outputs.  Do not relabel rc2 pins:
   `CONFIG_LOCALVERSION`, the AW8697 module (832 → 833 expected), the DTB,
   the wrapper and the initramfs all change.
5. Ensure the rc3 initramfs carries the rc3 module tree and the port-10001
   netboot hook; never reuse the rc2 `rear1` initramfs.
6. Old rc2 rollback must remain usable; the new global release/pins must not
   invalidate the self-describing old manifest path.

## 5. Validation performed (offline, rootless)

- Recomputed every changed SHA512 locally; the upstream tar checksum is the
  unchanged recipe value `c1bdb11a…`.  All recipe/package sums match the files
  (`tests/test_rc3_device_sources.py`).
- Disposable extraction of the upstream archive under this task's directory,
  then application of 0001–0041.  No rejects.  New patches 0038–0041 apply
  with `--fuzz=0`, zero offsets, in series order.  The inherited rc2 patches
  0004 and 0006 rely on GNU patch's default fuzz 2 (exactly as the rc2 build
  did); they were not edited, which is why the legacy bytes stay identical.
- `cpp` preprocessing plus the local `dtc` 1.7.2 compiled
  `sm8150-oneplus-guacamole.dts` successfully (DTB produced); only the
  pre-existing upstream duplicate-unit-address warnings appeared.  `fdtget`
  confirmed: i2c7 `okay` at 400 kHz; `haptics@5a` compatible/reg/reset; the
  slider name and `ABS_SND_PROFILE` values 0/1/2; `led-controller@d300`
  `okay` with one torch LED on sources 1+2 at 70000 µA and no flash-max
  properties.
- `python3 -m unittest tests.test_rc3_device_sources -v` → 11 tests, OK.
  `tests.test_boot_diagnostics` → 9 tests, OK (the existing helper/unit tests
  still pass).  `tests.test_sdsp_library_layout`,
  `tests.test_mpss_systemd_packaging` and `tests.test_rear_camera_7_0` (with
  `PYTHONPATH=tests`) → 36 tests, OK.

Not performed: kernel/module compile, APK build, pmbootstrap/chroot/mount,
image/boot build, service or package installation, phone access, flash.
No binary or hardware readiness is claimed.

## 6. Exact files, sizes and SHA512

Kernel recipe (`device/testing/rc3/linux-postmarketos-qcom-sm8150/`):

| File | Bytes | SHA512 |
| --- | ---: | --- |
| `APKBUILD` | 11 350 | `a607845373535a77a47df62cca355b930f928ecdfbde2805d9f66f4ddfa6b216e94712d64b5be3154e0d23a9995863858dd90609eaa86cb9c8a4fd2671d9fc9e` |
| `config-postmarketos-qcom-sm8150.aarch64` | 253 203 | `4777e229e253d4b244f11fffc721b2feb687571be7168990126df0aeeb55bf1fa86f8d989fd67eee5ad6b89cb0dd92f636cce2ab28494461e5bec62d51524335` |
| `0038-arm64-dts-guacamole-rear-flash-bringup.patch` | 3 071 | `ba0614d990c40aaca46cf69d40ed66ecb44107a7ef5ae2532c6bcbf4595f9a18bbbdafff413cdae9cb4eb53acff07d3be0b4520436486b31024fe00d26865d5a` |
| `0039-input-aw8697-kconfig-makefile.patch` | 1 672 | `1620a3f1a6d9190686adf812bea24dcb90a11c280189c91873b2451b4023a36f52de39c177d55e8657a82618d80a72203d412f39745b52981238060252c6ac38` |
| `0040-input-aw8697-haptics.patch` | 13 244 | `0840270cc0b8ae3cf92ac5426c3dfa845e5d51ba431dda51d2c971a25cdea8ad3b40979ddc2af752a08c011f15ff8f0ca342595985f38358a88e64397833ab4e` |
| `0041-arm64-dts-guacamole-haptics-alert-slider.patch` | 3 246 | `12611a833163f02391920876e4ee6446ec4f8d25e2accfa2cde38c492cc428a6a984333648ddb7ad63cf8a44e797137bcae3c1fe5ce5f2bf9b36172d0efd930d` |

Device package (`device/testing/device-oneplus-guacamole/`):

| File | Bytes | SHA512 |
| --- | ---: | --- |
| `APKBUILD` | 13 448 | `63c5698dc4aed92705e54e48987b8a128ade5a7e4466a3fd7247e1bfb9d3dfed08e130678142ac5d466ef9504a3a4bedce337c960190656ec14b67817e66bd03` |
| `nura-boot-diagnostics` | 3 198 | `7bd9bce90d395650b08a351f2dc0db052ae7cca316021b97c79a133c9b8882aa4545092dd47d2102b65713665d8d81054a98aa955ca38dbeeba5fc3d35990e89` |
| `nura-boot-diagnostics.service` | 881 | `cb51abec5762ffe36d0da419604d2d5ef37909669836e04cbe305bd2644aa5f286d72a73dcfb0787afb8f47c100af9ee3d53e2d223b1d1881a87d1058d7ee50b` |
| `nura-boot-diagnostics.timer` | 509 | `d69716f38debbb153ff91dea6764a67f2fbf3af12ad9bc77ded73e5e837e788afa2f785d0c2089385f6e59d90d9c5ec981004fbf91dafc63fc3695c7c3b9fe77` |
| `81-nura-boot-diagnostics.preset` | 224 | `1d93085e8d73fc3c6ac80b87cfd0ce7b811f510e33adfc8c7ac98e44f2ede35115110c0dcbbe65d94badf48085aaf8ff63cbba68ac657d4e0173a9d0d58815a6` |
| `72-guacamole-haptics.rules` | 1 180 | `f7dfc0748628b8cd46047e1f21cb6bf3a72dacbc5635f2f00facd97855ad8dafa1ec4c0ecc6c86f0105d52e3c00e365f1019f187e3fe7ccc8e3e44c0b306a9f4` |

Clock package (`device/testing/nura-clock/`):

| File | Bytes | SHA512 |
| --- | ---: | --- |
| `APKBUILD` | 1 843 | `7ca9da85f7728d5531978e7977b13891c51580eab10495e5c9584e808fb9a25f7ca409eb023d8966c9fdc8ff06e1a902d565d8038a28875fb99a579e1ab4df6c` |
| `clock-epoch` | 11 | `baa79109dc1741cbb3e3f6a7b0710be988a95596b47bc1467820920375ece6480af92a9f3a2f0f30d459bc0e58611fa5693150aa0a599c8fda446a6adc03230f` |

Test: `tests/test_rc3_device_sources.py`, 12 445 bytes, SHA512
`c3b3513d5e072a0b5ef898796c7ca0423edd70844cbc8573f92ef6ef26a75f6337b3a8b7cf7638cad306409712cebd7e0c47721a6af8a0a47d6f7aaf9982465e`.

## 7. Uncompiled integration risks

1. **AW8697 compile/runtime is unproven.**  The 7.0 API review is source-only;
   a compiler or sparse warning cannot be ruled out.  If a build error occurs,
   the failure is in `0040`/`0039`, not the DTS.
2. **Haptics autoload.**  The static DT + OF modalias path is expected to
   autoload `aw8697_haptics` during coldplug; this was not observed.  No
   modules-load file is shipped (see §2), so a missing autoload is the first
   thing to check on the first rc3 boot.
3. **feedbackd ACL.**  The `uaccess` tag and rule ordering are inferred from
   the shipped feedbackd/udev files; whether the Plasma user's ACL appears has
   not been observed.  The `feedbackd` group is not joined.
4. **Torch current/hardware.**  70 mA aggregate is the only setting verified on
   this hardware family (reference, 2 s, 6.17).  Continuous torch operation,
   boost readiness with no camera consumer and thermal behaviour are untested
   on 7.0.  A dark LED after the patch is an unknown, not proof of a driver
   bug.
5. **Alert slider integration.**  Only feedbackd's profile switch is expected;
   Plasma ringtone/media muting is not implemented and must not be claimed.
6. **Clock.**  The epoch is a build-date lower bound; accurate UTC needs an
   uplink for timesyncd.  Suspend-time RTC injection is lost with HCTOSYS off.
7. **Legacy patch fuzz.**  Patches 0004/0006 need GNU patch fuzz 2 against the
   pinned archive, exactly as in the rc2 build.  They are unmodified rc2
   bytes; do not regenerate them silently.
8. **Dependency wiring.**  The device package now depends on `nura-clock`; if
   the delivery build list omits it, installation fails instead of silently
   omitting the clock floor.
9. **Clock-checker convention.**  The delivery checker currently requires the
   `clock-epoch` content to be a 10-digit Unix date and rejects an empty file.
   The package satisfies that with the declared floor, while native systemd
   uses the mtime.  If the delivery worker changes the checker to mtime-only,
   the marker remains inert; if it starts comparing content to the actual
   build date, the static lower-bound marker must be reconciled then.
