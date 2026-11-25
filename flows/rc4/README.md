# rc4 integration

**rc4t (optional, EXPERIMENTAL, not built yet):** the SM8150 Venus video
codec as an opt-in kernel set, `optional/venus` (49 patches: the AOSPA
raphael IRIS1 series backported from 7.2, plus guacamole enablement and the
board's firmware). Not in the default build; enable with
`./nura kernel --optional venus`. See `optional/README.md` and
`topics/venus/README.md`.

**rc4r (2026-11-23):** patch 0079 (panel prefers 60 Hz, pkgrel 20) is on
`boot_b`; device 4-r22 adds the qcom_wdt watchdog and the reboot-hang
workaround. See `topics/reboot-hang/README.md`.

**rc4q (2026-11-23):** patch 0078 (Wi-Fi GTK-rekey offload during WoWLAN
suspend, pkgrel 19) is installed on the phone's Plasma rootfs; `boot_b` was not
reflashed. See `topics/wifi-wowlan/README.md`.

**rc4l source fix (2026-11-15):** patch 0074 now pulls the NFC IRQ line (GPIO47)
down instead of up. That pull-up caused the rc4k UFS clock-scaling failure. The
rc4l kernel (pkgrel 15) and boot images are built and host-verified in
`.work/rc4l-build` (see its README); installed as a fresh userdata image; debug and normal boot images
passed the scaling load test on hardware (2026-11-15). See [the NFC IRQ section](../../docs/RC4K_NFC_SENSORS.md#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15).

**rc4k candidate (2026-11-15):** NFC DT patch 0074 and kernel pkgrel 14 built
rootlessly in `.work/rc4k-build`; all four NFC DTB checks pass. Rootfs and boot
image are built with a HOST-VERIFIED manifest; hardware tests remain.
See [candidate instructions](../../docs/RC4K_NFC_SENSORS.md).

**Status (2026-11-14): rc4j built and booted successfully with Phosh.**
Speakers, Bluetooth and Wi-Fi were confirmed by the user. The original integration
review below was rootless; current hardware evidence and remaining issues are
recorded in `docs/RC4J_SUCCESS_20261114.md`. The release notes are
`docs/NURA_RC4.md`; the original review record is
`flows/rc4/REVIEW.md`; the supplied-file ledger is `flows/rc4/INVENTORY.json`.

## 1. What is where

```
flows/rc4/
  device/testing/linux-postmarketos-qcom-sm8150/   rc4 kernel recipe (isolated copy)
      APKBUILD                                     pkgrel=5, release -guacamole-rc4
      0001-0041-*.patch                            byte-identical to rc3
      0044-0058-*.patch                            the reviewed rc4 additions
      config-postmarketos-qcom-sm8150.aarch64      rc3 config + the reviewed delta
  device/testing/device-oneplus-guacamole/         rc4 device package (pkgrel=15)
      guacamole-mss-release{,.service}             installed, NOT enabled
  integration/
      config.fragment                              one merged config delta
      apply-sequence.log                           zero-fuzz apply of 0044-0058
      object-build.log                             syntax/object evidence
      dtb-build.log, dtb-gates.log                 four DTBs + gate assertions
      kernelrelease.log                            uname -r from the merged config
      EVIDENCE.md                                  every command and its result
  scripts/
      rc4.sh                                       check | contract | kernel | rootfs | bootimg
      check_recipe.py                              read-only recipe/flow checks (59)
      dt_facts.py                                  DTB gate facts (CAMSS/ALSA/DT gates)
      make_inventory.py                            regenerates INVENTORY.json
      toolshim/bc                                  timeconst.bc substitute (no bc here)
  tests/                                           rootless integration + guard tests
      fixtures/fake_build_rear_camera_kernel.sh    NURA_* fake builder for the contract test
      test_rc4_kernel_contract.py                  kernel -> rootfs executable fixture
  local-storage/                                   the user-run packaging flow
      scripts/                                     adapted copies of flows/local-storage
      device/testing/                              links: unchanged recipes read-only
      ORIGIN-SHA256.json                           provenance of the adapted copies
  build-source/
      artifacts/{dtb,obj}/                         the compiled evidence
      (kernel-state/, kernel-output/ appear only after `rc4.sh kernel`)
```

Nothing outside `flows/rc4/` and `docs/NURA_RC4.md` was modified: the rc3
recipe, the rc3 scripts, the rc3 artifacts, `flows/local-storage` and the
supplied `Patches` trees are read-only inputs.

## 2. Kernel build contract (one set of paths)

The kernel phase, the rootfs flow and the checkers agree on exactly this
layout, which mirrors the reviewed rc3 tree (`rc3_build.sh` used the same
"output inside the state, package/Image beside it" shape):

| Path | Env var | Written by |
| --- | --- | --- |
| `build-source/kernel-state/linux-v7.0.0-sm8150/` | `NURA_KERNEL_STATE` (source) | builder |
| `build-source/kernel-state/output/` | `NURA_KERNEL_OUTPUT` | builder (`O=`) |
| `build-source/kernel-output/kernel-package/` | `NURA_KERNEL_STAGE` | builder |
| `build-source/kernel-output/Image` | `NURA_KERNEL_IMAGE` | builder |
| `build-source/kernel-output/kernel-inputs.json` | — | `rc4.sh kernel` → `kernel-record` |
| `build-source/kernel-output/DELIVERY.json` | — | rootfs phase (`kernel-manifest`, non-local profile only) |

`build_rear_camera_kernel.sh` writes the staged files and `Image` but **not**
`kernel-inputs.json`/`DELIVERY.json` (rc3 did that in `rc3_build.sh`); `rc4.sh
kernel` therefore runs `kernel-record` itself and then verifies the contract.
The local-userdata rootfs profile stops before `kernel-manifest` and produces
`DELIVERY.local.json` in its own build dir instead, so the local flow never
needs the kernel `DELIVERY.json`.

`bash flows/rc4/scripts/rc4.sh contract` proves this end to end with a fake
builder (`tests/fixtures/fake_build_rear_camera_kernel.sh`): real wrapper, real
`kernel-record`, the rootfs dry-run and `--check-only` preflight, and the
negative cases (missing `kernel-state/output`, missing `kernel-package`, wrong
release, missing `kernel-inputs.json`, and the three bad `output/source` link
shapes: outside the state, dangling, or pointing at another tree).

## 3. Patch map and apply order

| Number | Patch | From |
| --- | --- | --- |
| 0001-0041 | rc3 series, unchanged bytes | rc3 recipe |
| 0044 | `soc-qcom-guacamole-mss-vote` | suspend-fixes 0027 |
| 0045 | `ath10k-snoc-wmi-key-ack` | suspend-fixes 0028 |
| 0046 | `asoc-q6afe-dai-displayport-rx-input-widget` | displayport 0029 |
| 0047 | `asoc-sm8150-accept-displayport-backend` | displayport 0030 |
| 0048 | `asoc-sm8150-prepare-dp-codec-dai-before-afe-start` | displayport 0031 |
| 0049 | `asoc-qdsp6-q6afe-split-port-config-and-start-errors` | displayport 0032 |
| 0050 | `asoc-qdsp6-q6afe-send-dp-stream-and-controller-index` | displayport 0033 |
| 0051 | `input-misc-add-goodix-fp` | fingerprint 0034 |
| 0052 | `iio-magnetometer-add-mxm1120` | popup-camera 0035 |
| 0053 | `media-i2c-add-imx471` | popup-camera 0036 |
| 0054 | `media-qcom-add-oneplus-hotdog-popup-motor` | popup-camera 0037 |
| 0055 | `arm64-dts-guacamole-fingerprint` | fingerprint dts |
| 0056 | `arm64-dts-guacamole-popup-camera` | popup-camera dt (packaging patch: new dtsi + include line) |
| 0057 | `arm64-dts-guacamole-displayport-video` | displayport dts 0001 (DP video works on hardware; docks blocked by USB-PD, see `docs/NURA_RC4.md` §0) |
| 0058 | `arm64-dts-guacamole-displayport-audio-link` | displayport dts 0002 |
| 0059 | `slimbus-qcom-ngd-wait-5s-for-qmi` | **rc4b**, omarchy slim-ngd-pd-up-race (QMI-wait half); mic test, not a proven fix |
| 0078 | `wifi-ath10k-offload-gtk-rekey-during-wow` | **rc4q**, Wi-Fi GTK rekey offload during WoWLAN suspend; installed 2026-11-23 (`topics/wifi-wowlan`) |
| 0079 | `drm-panel-samsung-oneplus-dsc-prefer-60hz` | **rc4r**, 60 Hz preferred, control-display byte by refresh rate; on `boot_b` 2026-11-23 (`topics/reboot-hang`) |

* **rc4b removed `0036-dsi-phy-pm`** (from the rc3 series): it causes a hard
  hang on screen unblank (`dsi0_phy_pll_out_dsiclk: Zero divisor`). See
  `docs/NURA_RC4.md` §0. The file is kept in `flows/rc4-reverted/`.

* 0042/0043 are **not** reapplied: they are the rc4 names for what rc3 already
  ships as `0034-fastrpc-freezable-wait` and `0035-s6sy761-resume-irq`.
* `0057`/`0058` are the two DisplayPort devicetree patches. They carry numbers
  after the popup block so that every file in `source=` starts with `0` (the
  reviewed builder globs `0*.patch` and applies `--fuzz=0` for names ≥ 0028)
  and no name collides with a kernel-series number.
* The devicetree include order is: board delta (`...-nura.dtsi`, which includes
  the DisplayPort dtsi) → fingerprint dtsi → rear-camera dtsi → popup dtsi. The
  patches were rebased in that order; `0055` owns the middle slot and `0056` the
  end-of-file slot, as the topic review requested.

## 4. Merge verification (zero fuzz)

Applied to a reflink copy of the actual rc3-patched 7.0 source:

```
0044 … 0058   patch -p1 --fuzz=0   15/15 exit 0, no offsets, no rejects
              (flows/rc4/integration/apply-sequence.log)
```

The command and its per-patch result are reproducible with
`flows/rc4/scripts/rc4.sh check` once a kernel state exists, and the
`apply-sequence.log` in this tree is the run that was made during integration.

## 5. Build evidence, separated by kind

| Kind | What was done | Where |
| --- | --- | --- |
| Syntax + object | 11 affected translation units compiled with clang 23 / `LLVM=1` / `W=1` against the merged config: `mxm1120.o`, `imx471.o`, `hotdog-popup-motor.o`, `goodix_fp.o`, `guacamole_mss_vote.o`, `ath10k/{mac,core,wmi-tlv}.o`, `q6afe-dai.o`, `q6afe.o`, `sm8150.o`. No diagnostic from the patched sources. | `integration/object-build.log`, `build-source/artifacts/obj/` |
| Devicetree | The combined full DTB and all three rear isolate DTBs compile with the kernel's own dtc; the gates are asserted on the compiled blob. | `integration/dtb-build.log`, `integration/dtb-gates.log`, `build-source/artifacts/dtb/` |
| Identity | `make kernelrelease` → `7.0.0-sm8150-guacamole-rc4` from the merged config. | `integration/kernelrelease.log` |
| Full build | **Not run** (out of scope for this task). The kernel package, rootfs, boot image and APKs must still be built with `rc4.sh kernel` / `rc4.sh rootfs`. | — |

Compiled-DTB gates (all three checked by `dt_facts.py --require-rc4-gates`):

* 3 CAMSS async connections, `port@0/1/3`, all bindable — the popup endpoint is
  absent, so `camss_subdev_notifier_complete()` still runs and the rear cameras
  keep their video nodes;
* 12 children of `/sound`, 11 available links, `dp-dai-link` the only disabled
  one — the same card rc3 registers;
* fingerprint node present and `disabled`;
* DP controller `okay`, FSA4480 present, `dp-dai-link` `disabled`;
* popup motor node `disabled`, front sensor and CSIPHY2 endpoint absent.

## 6. Commands

```bash
# read-only: 59 recipe/flow checks + DT gates + 42 write-free unit tests
bash flows/rc4/scripts/rc4.sh check

# executable proof of the kernel->rootfs contract (fake builder, temp trees
# inside flows/rc4/build-source, removed afterwards)
bash flows/rc4/scripts/rc4.sh contract

# user-run, rootless, isolated under flows/rc4
bash flows/rc4/scripts/rc4.sh kernel
bash flows/rc4/scripts/rc4.sh rootfs --authorized-key /path/to/key.pub
bash flows/rc4/scripts/rc4.sh bootimg

# after a failed rootfs build: clear the selected UI's leftover chroots
# (pmbootstrap shutdown, never a force-unmount; --dry-run only prints)
bash flows/rc4/scripts/rc4.sh shutdown --ui phosh
```

`check` writes nothing at all (no logs, no `__pycache__`, no files);
`contract` is the only action that creates temporary trees, and it removes them.
`kernel` reuses the reviewed parameterized builder
(`scripts/build_rear_camera_kernel.sh`) through its `NURA_*` interface with
`NURA_RECIPE_DIR`, `NURA_KERNEL_STATE`, `NURA_KERNEL_OUTPUT`,
`NURA_KERNEL_STAGE`, `NURA_KERNEL_IMAGE` and `NURA_KERNEL_RELEASE` pointing
inside `flows/rc4`. `rootfs` runs the copied local-storage pipeline with
`NURA_KERNEL_RECIPE`/`NURA_KERNEL_BUILD`/`NURA_KERNEL_STATE` pointing at the rc4
build, writes only under `flows/rc4/local-storage`, and keeps the local-root
(no NBD), active-mount and flock guards.

No command flashes, boots, mounts, repartitions, cleans up or restarts anything.

## 7. Gates

The candidate is inert by default. The authoritative list is in
`docs/NURA_RC4.md` §3; the machine checks are in
`flows/rc4/scripts/check_recipe.py` and `flows/rc4/tests/`:

* MSS release helper installed, unit **not** in `80-guacamole.preset`, no
  `guacamole-mpss-start` hook;
* automatic suspend disabled (`HandleSuspendKey=ignore`,
  `AutoSuspendAction=0`);
* DP video enabled, DP audio link `status="disabled"`;
* fingerprint node `disabled`, `goodix_fp.allow_power` defaults to false;
* popup motor node `disabled`, front sensor + CAMSS endpoint absent by default;
* `module_blacklist=qcomtee` on the rc4 boot cmdline (kernel-enforced for
  explicit loads too).

## 8. Not done, and honest blockers

* No full kernel/rootfs/boot build, no package, no boot, no hardware test.
* The QCOMTEE fnid mismatch (mainline `0x0602` vs stock `0x32000600`) is
  unresolved and can only be settled on hardware; it is why gate 0 stays
  passive.
* The lock-screen crash observed after a successful local boot is unexplained.
  Suspend/resume is a hypothesis; the rc4 rootfs does not suspend automatically.
* The pop-up camera needs this unit's Hall measurements before the motor may be
  enabled, and free-fall retraction is still not implemented.
* The local-storage flow's heavy path (rootfs + boot image) is adapted and
  reads-only verified here; its first real run is the user's.
