# OnePlus 7 Pro (guacamole) SM8150 v7.0.0-sm8150 bring-up

Status: **kernel, modules, DTB, user-built rootfs/initramfs and the final
stock-DTBO-wrapper boot image are generated. Host packaging checks pass;
hardware remains UNTESTED. See section 13 for the completed packaging.**

This document records the host-only attempt to move the Nura guacamole port
from `v6.17.0-sm8150` to the official postmarketOS `v7.0.0-sm8150` tree.  No
phone, bootloader, partition or rootfs was touched by this work.  No
`pmbootstrap`, `sudo`, mount, chroot or package installation was run.

The worktree is intentionally isolated from the 6.17 package.  The 6.17
recipe and the 6.17 flattened DTS are unchanged; all 7.0 inputs are under
`device/testing/7.0/` and `scripts/*7_0*`.

## 1. Pins and provenance

| Input | Pin / identity |
| :--- | :--- |
| Kernel source | `https://gitlab.postmarketos.org/soc/qualcomm-sm8150/linux` tag `v7.0.0-sm8150` |
| Tag object | `6c8b3f916cff0aec9f43de82d11a2dd196bef9e4` (annotated) |
| Tag commit | `8e126dbc4044ef2cec3ebe5754ddb05faa554af6` |
| Source archive | `linux-v7.0.0-sm8150.tar.gz`, 260,458,721 bytes |
| Archive SHA-256 | `708a97848f3e0e93553c30328f5a3d2a9ea2b483a8e072e973b33a30871c85ab` |
| Archive SHA-512 | `c1bdb11a9ad8be826ec5781e239c60ad92d6bb3a2d6b0adff8ef1f1e334bc6889ab91683257691ba3155f976011cd37969be379d509c9f928f4f85753e95635e` |
| pmaports | `postmarketOS/pmaports` commit `286d3b4ecce06510a967b33076cd333c52d3cc07` (official `pkgver=7.0.0`, `pkgrel=2`) |
| pmbootstrap (prepared, not run) | `e0dfe2cda7f3a0b70cdcc96f3060100839322aa4`, version 3.12.1 |
| Host compiler | clang 23.1.4, LLD 23.1.4 |
| Host tools | GNU Make 4.4.1, pahole 1.32, Python 3.14.8, bc 1.08.2 |
| Project-local dtc | 1.7.2, SHA-256 `49d483d0d0136e954bfb3b0ccb3120fcd30bef4ec9b8bc6fa3b7500c30451942` |
| mkbootimg | SHA-256 `37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c` |
| avbtool | SHA-256 `e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b` |

Source tag resolution was checked with `git ls-remote --tags` on 2026-11-10;
the archive was downloaded from the tag URL, not from an unpinned branch.
The official pmaports recipe and config were read from
`device/testing/linux-postmarketos-qcom-sm8150` at the pinned commit:

| Official pmaports file | SHA-256 |
| :--- | :--- |
| `APKBUILD` | `91fe308f9c6f30c8f1bbbd955e3ac467d6853240e21b11ea198f7e23442ccb01` |
| `config-postmarketos-qcom-sm8150.aarch64` | `305eda6abe5df19b2395cb4f38305cd8c4b8bdcc38f13dec4cd56bb15c765e6f` |

The official recipe keeps its own `0001-enable-ufs.patch`,
`0002-libbpf-Fix-UAF-in-strset__add_str.patch` and
`0003-xiaomi-vayu-fix-otg.patch`.  This port uses those three unchanged.  The
old `0002-enable-ufs.patch` is the same change as official `0001`; the old
`0003-fix-llvm-build.patch` is already in the 7.0 tree and is dropped.  The
`0002`/`0003` names in this recipe are therefore the official libbpf and
vayu fixes, not the old enable-UFS/LLVM patches.

## 2. Source and patch inventory

The isolated recipe is `device/testing/7.0/linux-postmarketos-qcom-sm8150/`.
Its `APKBUILD` is `pkgver=7.0.0`, `pkgrel=0`, and its source list is:

```
0001-enable-ufs.patch                         official
0002-libbpf-Fix-UAF-in-strset__add_str.patch   official
0003-xiaomi-vayu-fix-otg.patch                 official
0004-drm-msm-enable-native-guacamole-dsc-panel.patch
0005-input-s6sy761-add-optional-reset-gpio.patch
0006-i2c-qcom-geni-fifo-mode.patch              rebased
0007-asoc-codecs-add-tfa9874-speaker-amplifier-driver.patch
0008-asoc-sm8150-add-quaternary-mi2s-speaker-backend.patch  rebased
0009-ath10k-wcn3990-skip-quiet-mode.patch
0010-ufs-qcom-cap-sm8150-at-hs-g3.patch
0011-power-supply-qcom-smbx-smb5-limits-and-aicl.patch
0012-power-supply-bq27xxx-report-full-only-while-supplied.patch
0015-net-ipa-bound-gsi-trans-commit-wait.patch
0017-misc-fastrpc-allocate-sensors-messages-from-the-reserved-pool.patch
0018-power-supply-qcom-smbx-select-usb3-sdp-current.patch   rebased
0019-power-supply-qcom-smbx-expose-input-current-limit.patch
0020-power-supply-qcom-smbx-continuous-5v-to-12v-input-window.patch
0021-usb-typec-qcom-pmic-typec-count-vbus-above-vsafe0v-as-present.patch
0022-power-supply-qcom-smbx-follow-the-type-c-source-current-limit.patch
0024-usb-typec-qcom-pmic-typec-exit-the-sink-state-on-cc-only.patch
0025-dt-name-the-sm8150-ipa-node-after-its-reg.patch          new
0026-arm64-dts-add-the-nura-guacamole-board-delta.patch       new
```

Exact SHA-512 pins are in the recipe's `sha512sums` block and are checked by
`tests/test_sm8150_7_0.py`.

### Disposition of the original 24 patches

| Original | Disposition | Evidence in the pinned 7.0 tree |
| :--- | :--- | :--- |
| `0001` lid switch | dropped | `sm8150-xiaomi-nabu.dts` already has `// wakeup-source;` in `event-hall-sensor` |
| `0002` enable UFS | official copy | Official `0001-enable-ufs.patch` is byte-identical to the old 0002 (`316decaf…`) |
| `0003` LLVM build | dropped | `ftsIO.c` already declares `getClient(void)` |
| `0004` DSC panel | kept | Panel driver and 6.17 board panel/touch nodes are still needed; the 7.0 guacamole DTS alone has only simple-framebuffer |
| `0005` touch reset | kept | Optional reset GPIO is still useful |
| `0006` GENI FIFO | rebased | 7.0 added `gi2c->no_dma`; the two threshold calls were rebased, define preserved |
| `0007` TFA9874 | kept | No TFA9874 driver in the 7.0 tree |
| `0008` QUAT MI2S | rebased | 7.0 already has `hw_params`/`startup` MI2S and 3072000 BCLK; the remaining SLIMbus channel-map and shutdown clock release were rebased |
| `0009` ath10k | kept | Quiet-mode skip is still absent |
| `0010` UFS HS-G3 | kept | Still required for write integrity |
| `0011` SMB5 limits | kept | Applies; float/current/AICL fixes are not upstream |
| `0012` bq27xxx Full | kept | Applies |
| `0013` IPA v4.1 data | dropped | `drivers/net/ipa/data/ipa_data-v4.1.c` exists; `ipa_main.c` matches `qcom,sm8150-ipa` |
| `0014` IPA v4.1 regs | dropped | `drivers/net/ipa/reg/ipa_reg-v4.1.c` exists; `ipa_regs_v4_1` is wired |
| `0015` bounded GSI wait | kept | `wait_for_completion` is still unbounded in 7.0 |
| `0016` ENDP_INIT_CTRL | dropped | `ipa_reg-v4.1.c` already defines `REG_STRIDE_FIELDS(ENDP_INIT_CTRL, …, 0x800, 0x70)` |
| `0017` FastRPC pool | kept | Applies |
| `0018` USB3 SDP | rebased | 7.0's `smb_set_current_limit()` has the 0011-era `icl_max_ua`/`rc`; helper and call were rebased |
| `0019` writable ICL | kept | Applies; `input_current_limit` stays writable |
| `0020` input window | kept | Applies and is the base for 0022 |
| `0021` VBUS present | kept | Applies |
| `0022` TCPM contract | rebased | The guide's failed hunks were caused by 0018 not being applied first; after 0018 it applies cleanly |
| `0023` diagnostic port log | intentionally dropped | It only added a `dev_info` status dump on VBUS IRQs; it is not a functional fix |
| `0024` CC-only sink exit | kept | Applies |

`0025` renames the 7.0 IPA node from `ipa@1e00000` to `ipa@1e40000`, matching
its actual `reg` and removing a DTC simple-bus warning.  `0026` is the board
delta described below.

The series was applied in recipe order to a fresh pristine worktree
(`.work/7.0/applycheck`).  The resulting tree compares equal to the rebase
tree except for the source tarball's untracked `Documentation/.renames.txt`.
This is stronger evidence than a dry run because later patches see earlier
contexts.

## 3. Configuration

Base: official `config-postmarketos-qcom-sm8150.aarch64`
(SHA-256 `305eda6a…`).  Local config SHA-256 `0b9534f4…`.  Intentional
changes:

| Option | Value | Reason |
| :--- | :--- | :--- |
| `CONFIG_LOCALVERSION` | `"-sm8150"` | release `7.0.0-sm8150` |
| `CONFIG_LOCALVERSION_AUTO` | not set | deterministic release; no `+`/`-g…` suffix |
| `CONFIG_DRM_PANEL_SAMSUNG_ONEPLUS_DSC` | `y` | 0004 panel driver |
| `CONFIG_SND_SOC_TFA9874` | `m` | 0007 speaker amplifier |
| `CONFIG_KALLSYMS_ALL` | `y` | kept; no diagnostic reduction to force a fit |
| `CONFIG_DEBUG_INFO_BTF` | `y` | kept; official 7.0 config |

The official 7.0 config already sets the rest of the required options:
`CONFIG_BATTERY_BQ27XXX_DT_UPDATES_NVM` off, `CONFIG_CHARGER_QCOM_SMB2=y`,
`CONFIG_TYPEC_QCOM_PMIC=y`, `CONFIG_TYPEC_TCPM=y`,
`CONFIG_REGULATOR_QCOM_USB_VBUS=y`, `CONFIG_USB_ROLE_SWITCH=y`,
`CONFIG_QCOM_IPA=m`, `CONFIG_RMNET=m`, `CONFIG_QCOM_FASTRPC=m`,
`CONFIG_QCOM_PD_MAPPER=m`, `CONFIG_QCOM_Q6V5_PAS=m`,
`CONFIG_ATH10K_SNOC=m`, `CONFIG_BT_HCIUART_QCA=y`,
`CONFIG_BLK_DEV_NBD=m`, `CONFIG_USB_CONFIGFS_NCM=y`,
`CONFIG_USB_F_NCM=y`, `CONFIG_QCOM_SCM=y`, `CONFIG_PSTORE_RAM=y`.
`make olddefconfig` was run and the options above were verified afterwards.

The first build was made in the `rebase` git worktree and produced
`7.0.0-sm8150+` because `scripts/setlocalversion` adds `+` when the source is
a git checkout without a matching annotated tag.  That build was discarded.
The final build uses a clean source copy without `.git` and releases exactly
`7.0.0-sm8150`.

## 4. Host build and outputs

Final build tree: `.work/7.0/build-src` (patched source, no `.git`).
Build output: `.work/7.0/build-7.0-clean` (O= build).  To recreate the
source tree from the pinned archive:

```bash
ROOT=$PWD
mkdir -p .work/7.0/src
tar xzf .work/7.0/linux-v7.0.0-sm8150.tar.gz -C .work/7.0/src
cp -a .work/7.0/src/linux-v7.0.0-sm8150 .work/7.0/build-src
cd .work/7.0/build-src
for patch in "$ROOT"/device/testing/7.0/linux-postmarketos-qcom-sm8150/0*.patch; do
  patch -p1 --forward --no-backup-if-mismatch < "$patch"
done
cp "$ROOT"/device/testing/7.0/linux-postmarketos-qcom-sm8150/\
config-postmarketos-qcom-sm8150.aarch64 .config
```

Then build:

```bash
cd .work/7.0/build-src
make ARCH=arm64 LLVM=1 O=../build-7.0-clean -j24 \
  KBUILD_BUILD_VERSION="0-postmarketos-qcom-sm8150" \
  KBUILD_BUILD_TIMESTAMP="2026-11-10 00:00:00 UTC" \
  Image.gz modules dtbs
```

Build log: `.work/7.0/build-7.0-clean/build.log`
(SHA-256 `d7714b259259e3b2f4e3a3492ba0f7ee8e5a3a6baa411355c1e482898eefb103`).
The build exited 0.

| Artifact | Size (bytes) | SHA-256 |
| :--- | ---: | :--- |
| `build/7.0/Image` (also `.work/.../arch/arm64/boot/Image`) | 44,395,008 | `f8ddf7876676ac065f2d19097388e3591665ec9fb330fefb11d8be8a85b94358` |
| `build/7.0/Image.gz` / package `boot/vmlinuz` | 16,981,690 | `6559d8f2608599035019391d23829cb0fd5615d9cdd66421b44ed41dc3f535f2` |
| `boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb` | 105,738 | `5d4dceea4fa68626c887d712cd20e7ee160fb1b1d3f6aa811774753db7cb024e` |

The staged package tree is `build/7.0/kernel-package/`, mirroring the
official 7.0 recipe layout:

```
boot/vmlinuz
boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb (+ the other sm8150 DTBs)
usr/lib/modules/7.0.0-sm8150/...   (826 .ko.zst, depmod files)
usr/share/kernel/postmarketos-qcom-sm8150/kernel.release
```

`kernel.release` is `7.0.0-sm8150`; sample module vermagic is
`7.0.0-sm8150 SMP preempt mod_unload aarch64`.  `build` and `source`
symlinks were removed.  There is no 6.17 module in the tree.

## 5. Device tree

The board DTS is a proper source DTS on top of the 7.0 tree, not a reused
flattened 6.17 DTB:

- `0004` restores the 6.17 guacamole board display/touch description: the
  DSC panel, `mdss_dsi0`/`mdss_dsi0_phy`, `qupv3_id_2`/`gpi_dma2`, the
  correct touch supplies (`vreg_l1c_1p8`, `vreg_l10c_3p3`) and reset GPIO 54.
  The pinned 7.0 `sm8150-oneplus-guacamole.dts` is only a simple-framebuffer
  plus a reduced touch node, so this is required for the DRM panel path.
- `0026` adds `sm8150-oneplus-guacamole-nura.dtsi` and includes it from the
  board DTS.  It uses labels from `sm8150.dtsi`/`sm8150-oneplus-common.dtsi`
  and contains:
  - `qcom,msm-id = <0x153 0x20000>`, `qcom,board-id = <0x08 0 0 0>`,
    `qcom,msm-name`, `qcom,pmic-name`;
  - stock reserved-memory fixes: `xbl_aop_mem` at `0x85e00000`, `tz_mem`
    size `0x5500000`, splash/rdump at `0x9c000000`, bootloader log,
    `cdsp_sec`, ramoops at `0xa9800000`, param, mtp;
  - a 16 MiB FastRPC shared pool with `alloc-ranges` below 4 GiB, plus
    `memory-region` and `qcom,vmids = <3 5 6>` on the SLPI FastRPC node;
  - `serial1 = &uart13` and the WCN3990 Bluetooth child;
  - QUAT MI2S pinmux, TFA9874 at `i2c4` `0x34`/`0x35`, WCD9340 reset on
    GPIO 143 with the five S4A supplies from the 6.17 baseline, q6afe
    `dai@22`, six q6asm frontends, and the full sound card/audio routing;
  - bq27411 at `i2c8` `0x55` with no `monitored-battery`, and the FIFO-only
    I2C path for that bus;
  - `simple-battery` at 4.35 V / 3 A, PM8150B charger with
    `monitored-battery`, PM8150B Type-C connector with a 5 V/1.5 A source
    PDO and a 5 V/3 A sink PDO (no 9 V/12 V PDO), role/orientation endpoint
    links, VBUS regulator and DWC3 OTG;
  - volume keys on PM8150 GPIO 6/7;
  - TCSR syscon at `0x1fc0000` and `qcom,dload-mode = <&tcsr_regs 0x13000>`
    on SCM;
  - GPU, GMU and ADSP enabled (the 7.0 common disables the GPU and ADSP),
    the Adreno zap shader points at `qcom/a640_zap.mdt`, ADSP uses
    `qcom/sm8150/oneplus7/adsp.mdt`, SLPI is enabled with
    `qcom/sm8150/oneplus7/slpi.mdt`, and IPA enabled with
    `qcom/sm8150/oneplus7/ipa_fws.mbn`;
  - WCN3990 Wi-Fi rails (`vdd-0.8-cx-mx`, `vdd-1.8-xo`, `vdd-1.3-rfa`,
    `vdd-3.3-ch0`) restored on `&wifi`.

The generated DTB was decompiled and checked for every required compatible,
the board IDs, `qcom,dload-mode`, the reserved regions, the FastRPC pool size,
the 5 V-only PDO values, and the absence of `monitored-battery` on the gauge.
A set comparison against the 6.17 flattened DTB found **no compatible string
present only in the baseline**; the 7.0 DTB adds only `ramoops` and
`removed-dma-pool` metadata.  That does not prove every property is correct,
but it rules out a silently omitted board driver.
DTC warnings that remain are pre-existing upstream unit-address warnings
(i2c/spi share addresses, DSI address cells).  The IPA unit-address warning is
removed by `0025`.

### Reserved-memory caveat

The stock map carried from 6.17 has a pre-existing overlap:
`memory@85e00000` covers `0x85e00000..0x85f40000`, which includes
`aop_mem` at `0x85f00000` and `cmd-db` at `0x85f20000`.  This is the same
map the 6.17 baseline used.  It is reported, not silently "fixed": changing
it requires live/stock DTB evidence that is not in this worktree.

## 6. The boot-envelope fix

The arm64 Image header's `image_size` includes BSS.  The old 6.17 guard
compared the DTB address with `kernel_offset + max(header image_size, file
size)`.  It is preserved as `scripts/check_boot_envelope.py` and is called by
both `scripts/build_bootimg.sh` (stock `0x01f00000` default) and
`scripts/build_bootimg_7_0.sh` (measured `0x03000000` default) before packing.

Measured 7.0 Image:

| Value | Bytes | Hex |
| :--- | ---: | :--- |
| File size | 44,395,008 | `0x2a56a00` |
| Header `image_size` | 44,957,696 | `0x2ae0000` |
| Extent (`max`) | 44,957,696 | `0x2ae0000` |
| Kernel load end (`0x8000 + extent`) | 44,990,464 | `0x2ae8000` |

| DTB address | Result | Separation |
| :--- | :--- | :--- |
| `0x01f00000` (stock) | **overlap** | needs `0xbe8000` more |
| `0x02400000` (7.3.3 full-symbol experiment) | **overlap** | needs `0x6e8000` more |
| `0x03000000` (this port's default) | pass | `0x518000` (5,341,184 bytes) |

This treats the old conflict explicitly: the 7.3.3 B0 narrative used stock
`0x01f00000` with `KALLSYMS_ALL=n` (31.5 MB Image), while the later 7.3.3
source/tests used `0x02400000` with `KALLSYMS_ALL=y` (33.4 MB Image).  Neither
number applies to the 7.0 tree as-is.  The 7.0 official config keeps
`KALLSYMS_ALL=y` and `DEBUG_INFO_BTF=y`; the `.BTF` section alone is about
7 MiB, so removing BTF and all symbols would not plausibly recover the
~12.5 MiB needed to reach the 32.4 MB stock window.  That experiment was not
attempted; reducing diagnostics to force the old address was not done.
`0x03000000` is a measured relocation and remains **UNTESTED** on hardware;
it must be verified against the ABL/live DTB before a real boot.

The helper treats `kernel_load_end == dtb_offset` as adjacent/pass and one
byte less as overlap.  It reports the literal kernel/ramdisk overlap as a
warning only: the known-booting 6.17 baseline has the same literal overlap,
so this check does not establish bootloader relocation semantics for the
ramdisk.

Tests: `tests/test_boot_envelope.py` covers the known 7.3.3 overlapping
case, the valid 7.0 case, boundary equality, header-vs-file extent, bad
magic, short/zero-size headers, the ramdisk warning, and CLI exit codes.

## 7. Stock-DTBO quarantine wrapper

The wrapper was regenerated from the 7.0 DTB and the Lineage 24.0 stock DTBO
entry 0:

```bash
python3 scripts/prepare_temporary_dtb.py \
  --base-dtb build/7.0/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb \
  --base-source-dts .work/7.0/build-src/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts \
  --dtbo ref/lineageos/lineage-24.0-20261011/dtbo.img \
  --stock-boot ref/lineageos/lineage-24.0-20261011/boot.img \
  --out-dir build/7.0/temporary-dtbo
```

Result: `TEMPORARY_DTB=PASS`, `hardware=UNTESTED`.

| Artifact | Size | SHA-256 |
| :--- | ---: | :--- |
| `custom_guacamole-abl-quarantine.dtb` | 120,119 | `e2cb4f37c502454008fafa914036cd878bcc3e868e373704cd411823b9db0b3c` |
| stock entry 0 | 326,288 | `5755b70bba844ae433a9c3fac84d6bd301540d27c8f7e1f020db415a21cde238` |
| manifest | 9,385 | `366faa39e3a661828c22289ba44fe7c8ac16bade1a08bfcddcbfc9aa8fa5f868` |

The manifest records all checks: deep validator PASS, outside-quarantine
diff empty, only reviewed identity deltas, libufdt/libfdt agreement, symbol
map containment, reserved memory unchanged, and host tool hashes.  The
copied Lineage DTBO is reference evidence, not proof of the phone's installed
`dtbo_b`; do not erase or write that partition.

## 8. Boot-image packaging

`scripts/build_bootimg_7_0.sh` packs header v2/page 4096, local mkbootimg and
avbtool with hash pins, the stock-DTBO wrapper, and an AVB hash footer.

```bash
KERNEL_DIR=$PWD/build/7.0/kernel-package \
INITRAMFS=$PWD/.work/7.0/rootfs/export/initramfs \
DTB_MODE=abl-quarantine \
./scripts/build_bootimg_7_0.sh
```

The script fails closed if `INITRAMFS` is unset, if the staging release is
not exactly `7.0.0-sm8150`, if `scripts/check_initramfs_release.py` cannot
decompress/list the archive, finds no module release, finds a wrong release,
or finds mixed releases, or if the Image extent reaches the DTB address.
It never falls back to `/tmp/device_initramfs_r4`, a 6.17 ramdisk or a host
chroot.

`check_initramfs_release.py` is deliberately a **listing-only** check: it
compares the literal `lib/modules/<release>` paths in the cpio listing.  It
does not verify file contents, signatures, module loadability or a complete
rootfs, and the documentation does not present it as an integrity check.

**No real `boot.img` is delivered.**  The packing path was exercised with a
synthetic cpio ramdisk in `.work/` and then removed.  That test verified the
v2 header fields (`0x8000`/`0x01000000`/`0x100`/`0x03000000`, page 4096),
the wrapper hash inside the image, the AVB footer and the netboot cmdline
tokens.  A synthetic ramdisk is not a Nura rootfs and must not be booted.
The real boot image remains gated on the matching initramfs below.

## 9. Remaining user-run rootfs/initramfs stage

The following is prepared but **not executed by this task** because it needs
`pmbootstrap`'s privileged chroots/loop setup.  It uses only worktree-local
paths and never the shared `~/.local/var/pmbootstrap`:

- pmbootstrap clone: `.work/7.0/pmbootstrap` (`e0dfe2c`, 3.12.1),
  patched with `scripts/patches/pmbootstrap-netboot-port.patch` to add
  `netboot serve --port` and move its bind probe off port 9998.  A stock
  3.12.1 clone hardcodes NBD port 9999 and rejects `--port`; re-apply it
  after recreating the clone with
  `git -C "$ROOT/.work/7.0/pmbootstrap" apply "$ROOT/scripts/patches/pmbootstrap-netboot-port.patch"`.
- pmaports clone: `.work/7.0/pmaports` (`286d3b4`)
- local recipes symlinked into that clone:
  `device/testing/linux-postmarketos-qcom-sm8150` → `device/testing/7.0/...`,
  plus `device-oneplus-guacamole`, `firmware-oneplus-guacamole`,
  `main/hexagonrpcd`, `main/iio-sensor-proxy`, and
  `main/postmarketos-mkinitfs-hook-netboot` →
  `device/testing/7.0/postmarketos-mkinitfs-hook-netboot`
- netboot hook override: `pkgver=4.2.1`, `pkgrel=3`, `NBD_PORT=10000`;
  the repository `9999` package is not used
- pmbootstrap config: `.work/7.0/rootfs/pmbootstrap.cfg` (work dir
  `.work/7.0/pmbootstrap-work`, Phosh + systemd)
- envkernel output: `.work/7.0/build-src/.output` is a hardlink clone of
  `.work/7.0/build-7.0-clean` with the relative `include ../Makefile` stub.

Run these as your normal user.  pmbootstrap will invoke `sudo` itself; do
not run this task's agent with sudo.  Use absolute `ROOT` paths throughout:
the `--envkernel` step changes into `build-src`, so relative `PMB` paths
would break in that subshell.

Run the script as your normal user (it stops on errors and saves a build log
under `.work/7.0/rootfs/`). It packages the existing kernel, builds the local
packages for aarch64, pins the kernel and port-10000 hook, creates and checks
the rootfs, exports the initramfs, and packs `build/7.0/boot.img`.
The script passes `bash -n`; a mocked packaging-failure test verifies that it
stops before install, logs the failure, and works from another directory.
The privileged stages have not been executed:

```bash
bash guacamole_nura-workspace/nura-sm8150-7-0/scripts/rootfs_prepare_7_0.sh
```

The matching future netboot server port is **10000**.  Do not start it as
part of this build; when the user is ready to serve the image, stop the old
server and power the phone off first, then use `netboot serve --port 10000
--replace` with the same absolute `PMB` array.  The repository hook's port
9999 is deliberately not used.  The command is valid only with the patched
worktree-local pmbootstrap above; stock 3.12.1 has no `--port` option.

## 10. Tests and status

`python3 -m unittest discover -s tests -v`:

- 94 tests run, **94 passed, 0 skipped**.  The preserved firmware and sensor
  tarballs were imported into the worktree so the package-input tests run
  instead of skipping.
- New 7.0 tests: `tests/test_sm8150_7_0.py` (recipe checksums, config,
  DTB nodes/reserved memory/PDOs/gauge, release/vermagic, module tree,
  boot-script guards), `tests/test_boot_envelope.py`,
  `tests/test_initramfs_release.py` (valid gzip/raw cpio, wrong release,
  mixed releases, invalid/unreadable bytes, truncated gzip, literal release
  matching, and an end-to-end boot-script fail-closed check for
  invalid/truncated/wrong-release archives), and `tests/test_netboot_hook_7_0.py` (port 10000, package identity,
  checksums, the pmaports symlink, and local pmbootstrap CLI parsing for
  `--port`/`--boot-size`/`--extra-space`/`--add`/`--no-split`/`--filesystem`/
  `--no-install`).
- The baseline wrapper test now uses the 7.0 staged DTB when the 6.17
  flattened DTB is absent; it passed.
- `scripts/prepare_temporary_dtb.py --verify` passes for the 7.0 manifest.
- The patch series was applied to a pristine tree in recipe order.

Build vs hardware: **compilation proves only that the source, config, DTB,
modules and packaging path are internally consistent.**  Nothing here proves
that the 7.0 tree boots guacamole, that the panel or touch works, that the
charger/Type-C/audio/modem/sensors enumerate, or that the relocated DTB
address is accepted by ABL.

## 11. Not checked / remaining risks

- No phone, USB, `fastboot`, `adb`, SSH, partition or bootloader action.
- No `sudo`, mount, chroot, pmbootstrap build/install or rootfs image.
- No netboot server was started; the port-10000 pmbootstrap patch is a
  local tool change only.
- No real initramfs/rootfs and therefore no real `boot.img`.
- The `0x03000000` DTB relocation is unverified on hardware.
- The stock DTBO wrapper's base is the copied Lineage 24.0 image; the
  installed `dtbo_b` hash is not proven.
- The pre-existing `0x85e00000`/`0x85f00000` reserved-memory overlap is
  carried from the 6.17 baseline unchanged.
- The 7.0 tree's own guacamole DTS lacked a DRM panel and used different
  touch supplies; the 6.17 board description was restored through `0004`.
  That delta is source-level only and has not been re-verified on hardware.
- The 7.0 IPA node name was fixed in `0025`; the 7.0 `reg` values are kept.
- The 7.0 common DTS disables the GPU/GMU, ADSP and SLPI, and the 7.0
  guacamole DTS has no Wi-Fi rails or zap-shader firmware name.  Those are
  board policy, restored in `0026`; they have not been re-verified on
  hardware.
- `CONFIG_KALLSYMS_ALL=y` and `CONFIG_DEBUG_INFO_BTF=y` are kept, which is
  why the Image is much larger than the 6.17/7.3.3 images and why the DTB
  relocation is required.
- The `pmbootstrap` and `pmaports` clones/config are prepared but never
  invoked; the first `pmbootstrap` command will create its chroots and may
  fetch packages.

## 12. Worktree changes worth reviewing

New or changed for the 7.0 port:

- `device/testing/7.0/linux-postmarketos-qcom-sm8150/` (recipe, official
  7.0 config plus the local patch series)
- `device/testing/7.0/postmarketos-mkinitfs-hook-netboot/` (port-10000 hook
  override, `pkgver=4.2.1-r3`)
- `scripts/build_bootimg_7_0.sh`
- `scripts/check_boot_envelope.py`
- `scripts/check_initramfs_release.py`
- `scripts/build_bootimg.sh` (baseline script now calls the shared envelope
  guard; its stock `0x01f00000` default is unchanged)
- `scripts/prepare_temporary_dtb.py` (optional `--base-source-dts` and
  `--patched-dts`; defaults unchanged)
- `scripts/patches/pmbootstrap-netboot-port.patch` (local tool patch for a
  future `netboot serve --port 10000`; not applied to the project tree)
- `tests/test_boot_envelope.py`, `tests/test_sm8150_7_0.py`,
  `tests/test_initramfs_release.py`, `tests/test_netboot_hook_7_0.py`
- `tests/test_prepare_temporary_dtb.py` (uses the 7.0 staged DTB when the
  6.17 one is absent)
- `docs/SM8150_7_0_BRINGUP.md` (this file)

Imported from the preserved baseline before this attempt: the baseline
tracked edits (`device`/`firmware` recipes, `build_bootimg.sh`,
`patch_dtb.py`, `envsetup.sh`, `.gitignore`, `CLAUDE.md`) and the untracked
`docs/`, `scripts/` and `tests/` sources.  The 6.17 package and flattened
DTS were not modified.  All copied `baseline/files/` hashes match the
preserved `SHA256SUMS.json` except the three files intentionally extended
here: `scripts/build_bootimg.sh` (shared envelope guard),
`scripts/prepare_temporary_dtb.py` (optional DTS provenance arguments) and
`tests/test_prepare_temporary_dtb.py` (7.0 staged-DTB fallback).


## 13. User-run rootfs and completed boot image (2026-11-10)

The user completed the privileged rootfs/install/export stage. The final
packaging initially failed because zstd refuses a symlink filename by default;
`check_initramfs_release.py` now supplies archive bytes on stdin. A regression
test covers exported gzip/zstd symlinks; all 14 focused initramfs tests pass.

The real exported initramfs lists 348 cpio entries and exactly module release
`7.0.0-sm8150`. The installed rootfs's `/boot/vmlinuz` SHA-256 equals the
staged kernel's `6559d8f2608599035019391d23829cb0fd5615d9cdd66421b44ed41dc3f535f2`.
The coordinator ran only the rootless packaging script after the fix.

Final image: `build/7.0/boot.img`, 57360384 bytes, SHA-256
`9bd72b88b78514d56ba9892764b442c328f5f11c374c3982770ed9887fe92b24`.

Independent checks confirmed header v2/page 4096; embedded kernel, initramfs
and wrapper bytes equal their inputs; AVB footer is present; DTB address is
`0x03000000`; the kernel extent stays below that address. Wrapper verification
passes. No phone or server command was run. DTB relocation and the bootloader's
kernel/ramdisk relocation behavior remain unverified; these host checks do
not establish that the image boots.

Earlier sections describe the original host-only attempt before the user's
rootfs build; their “no real boot.img”/“privileged stage not run” statements
are historical and are superseded by this section.


## 14. First hardware boot and speaker regression (2026-11-10)

The user reports Phosh boots, including working Wi-Fi and Bluetooth. Audio
shows Dummy Output and PipeWire lists no audio devices. ADSP and WCD934x
probe in the supplied kernel log. The SoundWire port mismatch is diagnostic:
the driver logs it and continues with the DT-configured port count. SLPI
also reports repeated sensor-process initialization watchdog failures; this
is a separate unresolved issue.

The 7.0 DTB enabled both TFA9874 codecs and i2c4, but left their ancestor
`qupv3_id_0` disabled. Patch `0027` restores that wrapper's enabled status
from 6.17. The recipe is bumped to pkgrel 1 and includes its SHA-512.
Only the QUP0 status differs between the old and rebuilt board DTBs.
`tests/test_audio_dtb_7_0.py` checks both speaker nodes and every parent:
it fails on the original DTB and passes on the rebuilt DTB. The existing
19 package/config/artifact checks pass; the patch applies to the previously
validated original patch stack.

The audio candidate is under `build/7.0-audio/`, keeping
`build/7.0/boot.img` and the original staged kernel DTB/wrapper intact.
It reuses the same kernel, modules and exported initramfs; the running NBD
rootfs is not changed. Speaker recovery requires a fresh temporary boot of
the candidate and live ALSA/audio checks. No phone or administrative command
was run for this fix. Deferred-probe diagnostics were requested from the user.

For this DT-only test, do not rebuild/re-export or replace the served rootfs.
Keep the current NBD server running, reboot the phone into its bootloader
from its SSH session after `sync`, then temporarily boot
`build/7.0-audio/boot.img` from the host. The original boot image remains the
rollback. The default staging DTB/wrapper will be promoted only after the
candidate is verified on hardware; rerunning the normal rootfs builder is
not how to select this audio candidate.

Audio candidate packed successfully: `build/7.0-audio/boot.img`, SHA-256
`6177a2eae478699b2f1e74c04945565df3ffc2c9a3312c20d4bebf06ad2ce4cb`. Independent header/component checks confirm identical kernel,
initramfs and load geometry to the currently booted image, the expected
new wrapper, an AVB footer, and an untouched original image. Hardware
speaker recovery is not yet verified.


## 15. Audio hardware confirmation and default promotion (2026-11-10)

The user reports speakers work with `build/7.0-audio/boot.img`. QUP0
activation restores the speaker path. This is user-reported playback success;
no new measurement of microphone or call-audio behavior was made.

The successful audio image is now `build/7.0/boot.img` too. The main staged
DTB and the `.output` DTB used by `pmbootstrap build --envkernel` are updated,
and the default DTBO wrapper/manifest was regenerated and passes validation.
All three DTB copies match, the wrapper equals the tested candidate's wrapper,
and the default boot image equals the tested audio image byte for byte.
The previous image and DTB remain in `build/7.0/before-audio/` for rollback.
This supersedes section 14's pending-promotion/hardware status. The running
rootfs and netboot server were not modified.

## 16. Three user-run scripts (Bash or fish terminal)

Run each from the host, without putting sudo before the script:

1. In the server terminal, `bash scripts/serve_7_0.sh` waits for the phone's
   USB network interface, configures host address `172.16.42.2/24` if missing,
   and serves the existing rootfs on port 10000. Leave it running. It does
   not replace the rootfs image. The default interface is `enp19s0u4u4`;
   set `NURA_USB_IFACE` if a different USB port changes the interface name.
   The script invokes sudo for host networking and pmbootstrap's chroot;
   these actions happen only when the user runs it.
2. In another terminal, `bash scripts/boot_7_0.sh` reboots Android into
   fastboot and temporarily boots `build/7.0/boot.img`. If already in
   fastboot, it skips adb. Android must have USB debugging authorized.
   It uses the existing fastboot retry helper and never flashes anything.
3. After Phosh appears, `bash scripts/ssh_phone.sh` opens
   `user@172.16.42.1` using normal SSH host-key verification. It does not
   delete known-host entries or bypass key checks.

The project-local adb and fastboot binaries were copied from the existing
baseline tools and SHA-256 matched against their originals. Shell syntax
checks and six mocked command tests pass (Android/fastboot selection,
failed adb stopping before boot, IP setup/idempotence, correct server port
without image replacement, and normal SSH key checks). No phone, sudo,
network reconfiguration, SSH connection or server was executed by these tests.
