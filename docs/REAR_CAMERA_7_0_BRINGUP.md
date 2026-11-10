# Three rear cameras on Nura 7.0 — implementation/build report

2026-11-10. Worktree:
`guacamole_nura-workspace/nura-rear-cameras-7-0`.

The full kernel, 832 modules, combined rear DTB, three isolation DTBs, and an
isolated libcamera software-ISP stack have been cross-compiled successfully.
**No camera has been tested on this phone with this candidate.** Candidate
artifacts may exist from an earlier packaging run, but can be stale relative
to the latest source patches. Rerun the privileged packaging stage below after
the latest changes before attempting a temporary boot.

## Inputs and isolation

The frozen handoff at
`bringup-notes/nura-camera-handoff-20261110`
was verified against every entry in `SHA256SUMS.json`; `git apply --check`
passed before importing `changes.patch`, then `untracked/` was copied with
modes/symlinks preserved. Starting Git HEAD was
`5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24`. All work remains uncommitted.

| Input | Pin |
| --- | --- |
| SM8150 kernel | `v7.0.0-sm8150`, `8e126dbc4044ef2cec3ebe5754ddb05faa554af6` |
| Official pmaports | `286d3b4ecce06510a967b33076cd333c52d3cc07` |
| pmbootstrap | `e0dfe2cda7f3a0b70cdcc96f3060100839322aa4`, plus the imported local netboot-port patch |
| Camera reference | [Cube-Oakley/omarchy-mobile at f702f688](https://github.com/Cube-Oakley/omarchy-mobile/tree/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/kernel/camera) |
| Userspace reference | [same commit, libcamera](https://github.com/Cube-Oakley/omarchy-mobile/tree/f702f688288e59e3566f113d411ac7d18e4d0201/devices/oneplus7pro/adapter/camera/libcamera) |
| libcamera source | upstream `v0.7.2`; complete SHA-512 is pinned in the candidate APKBUILD |

The kernel archive was copied from the read-only 7.0 workspace and its full
SHA-512 checked against the imported recipe. Host tools, existing firmware/
sensor package archives and stock boot/DTBO reference inputs were copied and
hash-checked. No kernel/module binary from the 6.17 camera reference was used.
The downloaded reference is source-only under ignored `ref/omarchy-mobile/`.
`device/testing/rear-camera/SOURCE_PINS.json` records reference-file SHA-256s.

The original 7.0 and primary 6.17 checkouts, served image and port-10000
server were not modified. The imported `boot_7_0.sh`, `serve_7_0.sh` and
`ssh_phone.sh` retain the handoff hashes. The known working baseline boot
image was re-hashed as
`6177a2eae478699b2f1e74c04945565df3ffc2c9a3312c20d4bebf06ad2ce4cb`.
Its speaker-fixed DTB was independently copied as a comparison input and
verified as `cf613ffd17b4d4b2f1afee6c7b53d0a979a41e5044eb6dfde44ae519530688b3`.

All mutable candidate paths are under this checkout:

- Recipes: `device/testing/rear-camera/`.
- Kernel source/output: `.work/rear-camera/linux-v7.0.0-sm8150` and `output`.
- pmaports/pmbootstrap clones/config: `.work/rear-camera/{pmaports,pmbootstrap,rootfs}`.
- Future privileged work/chroots: `.work/rear-camera/pmbootstrap-work` (not shared).
- Staged artifacts/logs: `build/rear-camera/`.
- Candidate kernel release: **`7.0.0-sm8150-rear1`**.
- Candidate hook: **`postmarketos-mkinitfs-hook-netboot=4.2.1-r4`, port 10001**.

pmaports recipe links are relative and resolve within this checkout. The
privileged preparer refreshes a real, local `.output` directory inside the
kernel source for envkernel's bind mount; a symlink to a sibling output would
not work inside `/mnt/linux`. No mutable baseline build/cache is shared.
About 128 GiB was available initially; about 124 GiB remained after building
(the filesystem shares reflink extents). Parallelism was 12 kernel jobs and
8 libcamera jobs.

## Kernel changes and rebase decisions

The candidate kernel APKBUILD copies baseline patches byte-for-byte through
**0027**, including the speaker bus fix. Only nine config values differ:
LOCALVERSION, SM_CAMCC_8150, VIDEO_IMX481, VIDEO_IMX586, VIDEO_S5K3M5,
VIDEO_LC898217XC, VIDEO_AK7375, V4L2_CCI and V4L2_CCI_I2C. Existing CAMSS/CCI
module selections and all charging/UFS/audio/network settings are retained.
CAMCC is built in; sensors, lenses and CAMSS remain modules.

The additions are three rebased patches:

| Local patch | Reference intent and 7.0 adaptation |
| --- | --- |
| `0028-media-camss-sm8150-rebase.patch` | Combines 0055, 0064, 0066, 0067, 0075, 0076, 0089 and 0114. Adds four PHY/two CSID/two VFE resources, CAMSS_8150, v1.1 D-PHY and SM8150 C-PHY tables, 31-bit DMA, four NoC votes and the HF AXI bridge. Adapts regulators to 7.0 `regulator_bulk_data`, removes the obsolete resource `link_entities` field, retains the newer PHY generations/status offsets and 7.0 prepare/unprepare streaming ownership. |
| `0029-media-guacamole-rear-sensors-and-lenses.patch` | IMX586/IMX481 plus Kconfig/Makefile; adapts the already-present 7.0 S5K3M5 instead of duplicating its Kconfig/driver; adds LC898217XC and AK7374 support/runtime sensor-linked power to AK7375. Keeps 7.0's `devm_v4l2_sensor_clk_get()` for S5K3M5. |
| `0030-arm64-dts-guacamole-three-rear-cameras.patch` | Static board include on the actual 7.0 DTS, with CCI0/CCI1, CAMSS, rear-only pins/rails/endpoints and combined plus main/tele/wide isolation targets. No runtime overlay loader or regulator rebind is needed. |

The reference 0076 has a malformed hunk count: it can consume the VFE1 hunk
as text while applying VFE0 with fuzz. It was **not** accepted that way.
Both VFE0 and VFE1 explicitly have `gcc_camera_axi`, with corresponding
zero-rate entries in clock order. A regression check counts both tables.

0114's buffer deduplication/unwind is retained. 7.0 moved power acquisition
into `prepare_streaming` and release into `unprepare_streaming`; those remain
intact. The stop helper was inserted before the actual 7.0 start function,
and flush clears buffer references/active counts before completion. Optional
C-PHY register fields use zero-initialized allocation on other generations.

The initial reference application failures are saved in `reference-apply.log`.
Final camera patches apply with `--fuzz=0` to the saved pre-camera source;
all **23 changed files** then compare byte-for-byte with the built source
(`rebased-apply-check.log`). The imported baseline patches were retained with
their existing context/offset behavior; no claim is made that those historical
patches are newly rebased without fuzz.

CCI already supports the SDM845 v2 fallback used in the reference, so no
redundant CCI driver patch was added. CAMCC and the SM8150 interconnect
providers already exist in 7.0; no borrowed clock/power module is needed.
Diagnostic CAMNOC QoS/interrupt patches 0065/0074 were not added.

The candidate APK package function installs the four built guacamole DTBs
explicitly: individual DTB targets do not generate `dtbs-list`, so relying on
`make dtbs_install` would silently install none. A mocked-module-install test
executes the actual package function and checks all four DTBs and Image bytes.
The candidate source recipe is 7.0.0-r2; pmbootstrap **envkernel intentionally
packages it as `7.0.0_p<TIMESTAMP>-r0`**. The preparer reads and pins that exact
local version instead of expecting the recipe's r2 filename.

## Wiring, suppliers and power

| Rear sensor | Control bus | Receiver | MCLK | VDIG |
| --- | --- | --- | --- | --- |
| IMX586 | CCI0 master 1, 0x1a | PHY1, 3-trio C-PHY | MCLK1, 19.2 MHz | PM8009 LDO1, 1.104 V |
| S5K3M5 | CCI0 master 0, 0x10 | PHY0, 4-lane D-PHY | MCLK0, 24 MHz | existing PM8009 LDO2 narrowed to 1.056 V |
| IMX481 | CCI1 master 1, 0x1a | PHY3, 4-lane D-PHY | MCLK3, 19.2 MHz | PM8009 LDO4, 1.056 V |

These are the pinned reference's stock-derived rear mappings. The telephoto
reference driver supports 24 MHz even though stock Android uses 19.2 MHz.
No front sensor, CCI1 master-0 bus/pins, PHY2 endpoint, PM8009 LDO3, pop-up
motor, Hall sensor or flash node was added or enabled.

Ordered sensor power is preserved: IMX586 VANA/gate, custom1, loaded VDIG,
custom2, VIO, clock, reset; IMX481 VANA/gate, loaded VDIG, VIO, clock, reset;
S5K3M5 AF/BOB, VANA, VDIG, VIO, clock, reset. Errors unwind the enabled
supplies in reverse. The reference reports PMIC UVLO from digital-before-
analogue ordering; successful compilation cannot validate that on this unit.

The main/wide drivers vote BOB at 3.300–3.320 V, then release the vote on
power-off. The 7.0 baseline permits BOB down to 3.008 V, and a fixed-regulator
VANA child does not propagate a parent voltage request. The telephoto now
makes the **same reference-derived BOB voltage vote** on its AF/BOB consumer,
including failure/off release, so isolated telephoto operation does not depend
on the main/wide holding a vote. No global BOB constraints or unrelated PMIC
rails were changed. PM8009 LDO1/LDO4 load permission is retained. VIO remains
PM8150L LDO1 at 1.8 V; all controller/provider ancestors are enabled in the
compiled graph. PM8150/PM8150L already exist/enabled in our baseline.

PM8009 LDO1/LDO4 upstream input-supply properties are absent in the reference
and not described by the inspected stock base's RPMh regulator nodes. They
were not guessed or connected to a new parent. This retains the reference's
model and is a limitation of its physical supply description, to revisit if
probe/power diagnostics show missing supply votes.

**Main/telephoto actuator supplier is unresolved on this phone.** Both lens
drivers compile, but the reference's LC898217XC nodes at 0x72/0x74 are disabled
and neither sensor has a `lens-focus` binding. The reference author's chip
identification and measured focus ranges are not this phone's calibration;
those measured DT defaults were removed. Main/tele autofocus is unavailable
until supplier identity is established. Merely binding AK7375 does not prove
an AK7374 is present because it does not read a chip ID at probe.

The ultrawide uses AK7374 at 0x0c with its PM8150L GPIO4 VAF switch and a
sensor runtime-PM link. The reference reports **both** O-Film and Semco IMX481
vendor module descriptions name this actuator, stronger evidence than one
unit's probe. It still needs verification here. No EEPROM, persist calibration,
actuator identity record or factory data from another phone was imported.

To resolve main/tele later, obtain trustworthy evidence for this unit first.
Then edit only the selected rear actuator address/compatible, enable that
node and add the corresponding sensor `lens-focus`; an O-Film result needs
the correct AK7374 address/wiring evidence rather than renaming the 0x72/0x74
LC node. Rebuild/repackage. This handoff deliberately includes no active bus
probe/focus-sweep command and does not select both possible clients at once.

## CAMCC/RPMh and modem dependency

7.0 still clamps RPMh corners before sync and releases them in
`drivers/pmdomain/qcom/rpmhpd.c:rpmhpd_sync_state()`. Its PAS handover still
drops proxy power-domain votes in `qcom_pas_handover()`; SM8150 MPSS uses
`mpss_resource_init` with CX/MSS proxy domains. Thus enabling CAMCC can expose
the synchronization/suspend class of issue seen in the reference. No source
proof establishes that the 6.17 workaround is necessary or correct on 7.0.

No RPMh hold module, permanent top-corner request, remoteproc policy change
or modem proxy-vote change is included. The user-supplied idle baseline shows
MMCX on at performance 64, an active af00000 clock controller, and suspended
CX/MX/MSS/ADSP/MPSS entries. Those idle values do not establish firmware failure
or justify a permanent hold. After a user-run fresh candidate boot, compare
provider/probe logs and power summary first; suspend/resume and modem/audio/
Wi-Fi health remain separate hardware gates. Keep the first observation
controlled and powered; do not treat idle camera enumeration as suspend proof.

## libcamera: musl build without changing the installed ABI

The forwarded live diagnostics establish:

- running kernel `7.0.0-sm8150`;
- `libcamera-99990.8.0-r1`, `libcamera-ipa-99990.8.0-r1`;
- `pipewire-spa-libcamera-1.6.11-r0`;
- no matching camera/CAMCC/CCI/lens/RPMh modules or camera-related log lines;
- no camera GDSCs in the idle power summary.

Full user-collected text is `build/rear-camera/user-baseline-20261110.txt`.
The one read-only, BatchMode, strict-host-key SSH attempt was denied with
`Permission denied (publickey,password,keyboard-interactive)`. No authentication
configuration, host keys or credentials were inspected/changed. Later evidence
was forwarded from the user's already-open phone session. APK DNS warnings in
that collection are separate from camera enablement.

The actual prepared Nura `temp/libcamera` source recipe uses `_pkgver=0.8.0`,
`pkgver=9999$_pkgver` and pkgrel 1. Its software ISP paths/tuning directory
(`softisp`) differ from the reference's `simple` IPA. Applying the 0.7.2
stack there blindly would also cross the libcamera **0.8 versus 0.7 SONAME**
boundary used by PipeWire. The chosen solution is a separate APK:

- `libcamera-rear=0.7.2-r0`, installed under **`/opt/nura-rear-camera`**;
- local-package `!fhs` explicitly permits this private `/opt` prefix under abuild;
- no `provides=libcamera`, no conflicts/replacement of the system packages;
- only `cam-rear` is installed into `/usr/bin`, to set its child process's
  private library path; no global loader path or PipeWire environment change;
- abuild `ldpath` points at the private directory for correct self-dependency
  tracing; it does not alter the runtime system loader;
- simple pipeline/IPA, GPU software ISP explicitly enabled, `cam` built;
- IPA signing enabled with GnuTLS/OpenSSL, post-install re-signing preserved,
  and stripping disabled so the signature stays valid.

This supports isolated camera testing; it does **not** make the existing
0.8-linked PipeWire SPA use the new 0.7 stack. Integrating into that ABI would
need a separate reviewed 0.8 rebase, not an installed-library downgrade.

Reference patches **0002–0006 and 0008–0021** apply with `--fuzz=0` to the
fully SHA-512-verified 0.7.2 archive. 0001 changes only Pinephone's sun6i
pipeline and was dropped. **0007 (IMX471) was omitted with no dependency
exception needed.** The remaining patches include sensor helpers/properties,
autofocus/3A, system-heap allocator fallback, visible-input GPU statistics,
lens settling, tuning defaults, configuration-state preservation and highlight
handling. There is no Omarchy UI port.

All three tuning files retain their pinned source bytes and license headers.
IMX586 borrows IMX519 colour matrices (Raspberry Pi BSD-2-Clause data); IMX481
and S5K3M5 use identity matrices. They are **provisional tuning, not factory
calibration**. AF algorithms cannot drive the deliberately unbound main/tele
lenses. Rotation 90 for IMX481 follows the reference driver's observed readout,
not an independently verified orientation on this phone.

Cross-build details: host clang/LLD 23.1.4, private Meson 1.9.1/Ply 3.11/
Jinja2 3.1.6/PyYAML 6.0.3 environment; aarch64 musl sysroot unpacked rootlessly
from cached APK archives (no install scripts/chroot). It includes musl
1.2.6-r6, GCC/libstdc++ 15.2.0-r9 and the existing target runtime libraries.
Public architecture-independent EGL/GLES/KHR, event, YAML/GnuTLS/udev headers
and pkg-config metadata were copied from the host; no host library was linked.
The GnuTLS .pc's host-only private static dependency list was removed for this
shared-library cross-build. Sysroot archive hashes are in `sysroot-inputs.json`;
public header/metadata hashes in `sysroot-public-headers.json`. Absolute sysroot
symlinks were rebased locally. The aarch64-musl cross file is under `.work/`.
The APK recipe uses actual musl distribution `*-dev` packages for its build;
its privileged APK build remains unexecuted.

ELF inspection confirms AArch64 and `/lib/ld-musl-aarch64.so.1`. The installed
staged IPA signature verifies with `openssl dgst` (`ipa-verify.log` and
`libcamera-rebuild-check.log`). This is compile/link/signature evidence; target
runtime/GPU behavior has not been tested.

## Artifacts, builds and regressions

Rootless rebuild commands, using the already-prepared inputs in this worktree:

```sh
bash scripts/build_rear_camera_kernel.sh
bash scripts/build_rear_camera_libcamera.sh
python3 -m unittest discover -s tests -p 'test_rear_camera*.py' -v
```

Kernel builds use fixed timestamp `2026-11-10 00:00:00 UTC`, version 2,
builder `nura@rear-camera`; no Git release suffix. Source recipes/archives are
checked against full SHA-512s; source marker mismatches refuse stale rebuilds.
A fresh kernel source can be reconstructed from the copied archive and recipe
patches. The private libcamera sysroot/venv are prepared local build inputs;
for a clean environment the APKBUILD is the distribution dependency recipe.

Build/validation records under `build/rear-camera/`:

- `kernel-build.log`, `kernel-build-final.log`, `rebuild-final.log`,
  `modules-install.log`: successful full kernel, modules, DTBs and staging.
- `baseline-apply.log`, `reference-apply.log`, `rebased-apply-check.log`:
  historical baseline application, initial 6.17 failures, final rebase proof.
- `libcamera-apply.log`, `libcamera-configure.log`, `libcamera-build.log`,
  `libcamera-install.log`, `libcamera-rebuild-check.log`: patch/build/link/install.
- `quarantine.log`, `quarantine-verify.log`: stock-DTBO wrapper validation PASS,
  including libufdt/libfdt comparison and containment.
- `rear-tests-final.log`: 25 focused tests, all passed, no skips.
- `all-tests-final.log`: 131 tests run, 112 passed, 19 skipped, no failures.
  The skips refer to original 7.0 artifacts/clones absent from this checkout;
  all 25 focused candidate checks run without skips.
- `ARTIFACTS.json` and `SHA256SUMS`: full module/userspace/artifact hashes.

Focused tests cover reciprocal graph links, lane/bus type, all camera
clock/domain/interconnect/IOMMU providers and ancestors, single-sensor graphs,
main/tele lens non-selection, speaker bus availability, unchanged baseline
patches/config safety, checksums, vermagic, musl ELF/signature identity,
port-10000/wrong-release initramfs rejection, input hash mismatch, actual
DTB packaging and mocked privileged-build failure before installation.
Existing DT checks are reused against the candidate for GPU/Wi-Fi/ADSP,
codec supplies, reserved memory, gauge and 5 V PDO safety. No DT schema suite,
runtime sanitizer, physical calibration, camera streaming or suspend test was run.

The ARM64 Image header's BSS-aware extent is `0x02af0000`; at load offset
`0x8000` it ends at **`0x02af8000`**, leaving **`0x00508000`** before the DTB
at `0x03000000`. The candidate packer still runs the shared envelope check,
including actual ramdisk size, instead of accepting this address unconditionally.
Header v2/page 4096, quarantine wrapper, exact release check and explicit real
initramfs are retained. `check_rear_camera_package.py` additionally checks the
ramdisk's actual netboot hook is 10001 and requires recorded genuine rootfs/
Image/module/DTB inputs. No dummy or baseline-release initramfs was substituted.

| Artifact (under `build/rear-camera`) | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 44411392 | `4e798093d18fceb8d4708565e85203866ed80db06b2eef9ec8f00cf12c9883d4` |
| `kernel-package/boot/vmlinuz` | 16985044 | `455023be1092fca8e34911b4db9a7e3d518f2403d75cafd7c2d21cc638bd5a45` |
| `kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb` | 114058 | `f9e1ae14950c88858487feefb1425775c7d4bb4fecd276af466770460427a74d` |
| `temporary-dtbo/custom_guacamole-abl-quarantine.dtb` | 128439 | `3992b3c61d721a0b14a48059745ceee5d30fe30629527bbaf1134e52265d6255` |
| `libcamera-stage/opt/nura-rear-camera/bin/cam` | 275312 | `15edaf9a49c98f3b78b56537b2762ec35544726048f481f858eaca8f126f1435` |
| `libcamera-stage/opt/nura-rear-camera/lib/libcamera.so.0.7.2` | 2211064 | `1a54ae711345b51dcddb46e080f51c9c97d244b6d8840d1bb6b5b93877bbc50f` |
| `libcamera-stage/opt/nura-rear-camera/lib/libcamera/ipa/ipa_soft_simple.so` | 320336 | `8395c9698da2babf5e1bf5afc0a7a7178e76cbbe32edc84c951dd9a32848dcd7` |

## User-run packaging and temporary test

**The following commands are for the user.** The agent did not execute them.
The preparer invokes pmbootstrap's sudo/mount/chroot/image stages; that is the
specific remaining privilege boundary required by the user's AGENTS.md.
It never boots the phone or starts/stops either netboot server.

From this worktree, with sufficient space for a second rootfs:

```sh
bash scripts/rootfs_prepare_rear_camera.sh
```

This packages the already-built kernel with envkernel, builds the local
firmware/device/sensor userspace recipes plus the port-10001 hook and private
libcamera APK, and pins the exact three local kernel/hook/libcamera versions
in the new install. It records local APK SHA-256s, verifies the installed
kernel/DTB/module releases and ramdisk's port, exports a separate image and
initramfs, then invokes the guarded rootless boot packer. It refuses ambiguous APK selections. Before each envkernel rebuild, older
candidate kernel APKs are retained under
`.work/rear-camera/rootfs/previous-kernels/` (moving from a root-owned package
directory requests sudo); the working baseline's packages are untouched.
The receipt will be `build/rear-camera/packaging-inputs.json` and
`APK-SHA256SUMS`. No package hash is claimed before this stage runs.

Candidate results will be:

```text
build/rear-camera/boot.img
.work/rear-camera/rootfs/export/initramfs
.work/rear-camera/rootfs/export/oneplus-guacamole.img
.work/rear-camera/pmbootstrap-work/packages/edge/aarch64/*.apk
```

Use the fish shell on the host from this worktree. Keep the original working
port-10000 server running. After packaging succeeds, start the candidate server
in one host terminal:

```sh
bash scripts/serve_rear_camera.sh    # separate image/work dir, port 10001
```

The server waits for `enp19s0u4u4`, requests sudo to bring it up, and adds
`172.16.42.2/24` only if missing. Override `NURA_USB_IFACE` if using another
USB port (in fish: `env NURA_USB_IFACE=YOUR_INTERFACE bash scripts/serve_rear_camera.sh`).
Leave it running; it configures the newly enumerated USB interface and serves
only the candidate on port 10001. The stopped candidate cache is refreshed using `--replace`; confirm the prompt.
Never start this script while a phone is using the candidate rootfs.

In a second host terminal, run the one-attempt launcher:

```sh
bash scripts/boot_rear_camera.sh
```

It finds the bundled `platform-tools/adb` and `fastboot` even when they are
absent from PATH. If the phone is already in fastboot, it skips adb; otherwise
it requests one `adb reboot bootloader`, then makes one `fastboot boot` attempt.
It never flashes. If ABL reports Load Error, stop and inspect rather than
repeatedly power-cycling or changing partitions. The original helper's USB-reset
recovery is a separate user-run procedure, not part of this launcher.

After collecting diagnostics, return to Android and stop the candidate server
with Ctrl-C. From the host, fastboot can return the phone to its installed
system with:

```sh
./platform-tools/fastboot reboot
```

Before packaging another candidate, stop the port-10001 server and leave the
phone in Android or bootloader. Then rerun `bash scripts/rootfs_prepare_rear_camera.sh`
from this checkout. It exports into this candidate's separate rootfs paths; do
not package while the candidate server is serving, and do not replace or point
it at the live port-10000 rootfs.

Fish launch sequence, from this checkout:

```fish
bash scripts/rootfs_prepare_rear_camera.sh  # only when artifacts need refreshing
bash scripts/serve_rear_camera.sh            # terminal 1; keep running
bash scripts/boot_rear_camera.sh             # terminal 2
ssh user@172.16.42.1                          # once the phone reaches userspace
```

For rollback, Ctrl-C terminal 1 and run `./platform-tools/fastboot reboot` if
the phone remains in fastboot; if Android is already running, no rollback step
is needed. The package step is user-run and privileged; it was not run here.

Once userspace is reachable, run `scripts/collect_rear_camera.sh` **on the
phone**, or copy its commands into the existing interactive session. It reads
kernel/package/module/audio/power/topology information and performs no loads,
streams, lens commands, I2C probes, rail writes or service restarts. If logs
are denied, the user may separately collect:

```sh
sudo dmesg > rear-camera-dmesg.txt
sudo cat /sys/kernel/debug/pm_genpd/pm_genpd_summary > rear-camera-power.txt
```

First check `uname -r` is exactly `7.0.0-sm8150-rear1`, audio/Wi-Fi/BT and
charging remain healthy, suppliers did not defer, and all three graph links
exist. The installed tool `cam-rear -l` can then check the private libcamera
view; a listing alone is not success. Actual hardware validation should test
one rear stream at a time, then main/wide/tele/wide switching and buffer data,
followed separately by orientation, focus, exposure/colour and suspend/resume.
No simultaneous capture capability is claimed. Missing main/tele AF is
expected with their unverified lenses unbound. Do not diagnose this by loading
6.17 modules or forcibly raising domains/rails.

### Isolating a failed sensor binding

A single failing sensor can prevent CAMSS graph completion for all three.
The prebuilt main/tele/wide DTBs each carry only that rear graph. After the
combined package has been prepared, to select **main only** (replace `main`
with `tele` or `wide` for the other diagnostic candidates):

```sh
ROOT=$PWD
BASE_DTB="$ROOT/build/rear-camera/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole-rear-main.dtb"
python3 scripts/prepare_temporary_dtb.py \
  --base-dtb "$BASE_DTB" \
  --base-source-dts "$ROOT/.work/rear-camera/linux-v7.0.0-sm8150/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-rear-main.dts" \
  --patched-dts "$ROOT/build/rear-camera/main.dts" \
  --out-dir "$ROOT/build/rear-camera/main-dtbo"
BASE_DTB="$BASE_DTB" \
OUTPUT_DIR="$ROOT/build/rear-camera/main" \
KERNEL_DIR="$ROOT/build/rear-camera/kernel-package" \
TEMP_DTB_DIR="$ROOT/build/rear-camera/main-dtbo" \
INITRAMFS="$ROOT/.work/rear-camera/rootfs/export/initramfs" \
  bash scripts/build_bootimg_rear_camera.sh
# Once the phone is in fastboot, user runs:
"$ROOT/platform-tools/fastboot" boot "$ROOT/build/rear-camera/main/boot.img"
```

The isolation boot uses the same rear1 Image/modules and the same separate
10001 rootfs; only its validated DTB/wrapper changes. It never edits the served
image. Main/tele sensor isolation still leaves their unverified lens nodes
disabled. Restore the combined graph by booting `build/rear-camera/boot.img`.

### Rollback

No Android/UFS/slot/persist/dtbo/vbmeta write is involved. To return to the
user-confirmed working 7.0 system, put the phone into fastboot using the user's
normal recovery procedure, leave the original server on **10000**, then:

```sh
BASE=guacamole_nura-workspace/nura-sm8150-7-0
"$BASE/platform-tools/fastboot" boot "$BASE/build/7.0/boot.img"
```

For the original baseline's convenient serve/boot commands, use its workspace:
`bash "$BASE/scripts/serve_7_0.sh"` (only if that server is not already running)
and `bash "$BASE/scripts/boot_7_0.sh"`. The new checkout deliberately has no
shared writable port-10000 work/rootfs paths. Stop a candidate server only
from its own terminal when the phone no longer uses its image.

## Remaining evidence and boundaries

Implemented/built: all three rear sensor paths together, both lens drivers,
combined/isolation DTs, CAMSS/CCI/CAMCC dependencies and the private musl GPU
software ISP with tuning and signed IPA. Source/compile/host checks are recorded.

Outstanding user-run stages: actual APK build/install/export into the isolated
candidate rootfs, matching initramfs generation, guarded camera boot-image
packing, temporary boot, sensor binding/streaming, actuator identification,
quality calibration and CAMCC/modem suspend validation. The PMIC UVLO history
makes ordered-power hardware observation material; do not repeatedly retry a
power cut. The pre-existing reserved-memory overlap and stock-DTBO selection
assumptions described in the baseline documents remain unchanged.

No agent-run sudo/su/mount/chroot/pmbootstrap build/install, host networking,
service/USB change, phone boot/reboot/flash, module load/unload, camera stream,
I2C/SPMI probe, regulator write, EEPROM/calibration write, commit or push occurred.


## First hardware boot: CCI completion fault

The user photograph (`build/rear-camera/diagnostics/phone-cci-oops.png`)
shows a NULL-pointer fault in `wait_for_common`, reached from
`wait_for_completion_timeout` and `cci_probe` in `i2c_qcom_cci`, followed
by display/DSI errors. Host logs show an NBD connection which later stalls.
The USB ID's generic lsusb fastboot label is misleading: descriptors identify
this gadget as postmarketOS.

CCI1 has only master 1 enabled in the rear-only graph. The pinned CCI driver
initializes completions only for available child buses, but controller reset
always waits on master 0. Patch 0031 initializes completions for all supported
masters before parsing children; no front bus or sensor is enabled. A source
regression check failed before this change and passes after it. The rootless
kernel/modules rebuild passed (`cci-fix-build.log`). This is a concrete fault
matching the trace, not proof that every subsequent boot problem is solved.

The already-packaged rootfs and boot image still contain the old module.
Before repackaging, return the phone to Android/bootloader and stop only the
candidate port-10001 server. Never rebuild a rootfs while the phone uses it.
Rerun `rootfs_prepare_rear_camera.sh`, then start the candidate server and
perform a fresh temporary boot. Hardware validation of patch 0031 is pending.


Delivery correction: `serve_rear_camera.sh` now uses `--replace` to refresh
the NBD cache after packaging. Stop the old candidate server and leave the
candidate OS first, then confirm the replacement prompt. The port-10000
baseline is untouched. Earlier no-replacement instructions are superseded.


## Hardware confirmation: all three rear cameras

2026-11-10: after refreshing the candidate netboot cache with `--replace`,
the user confirmed all three rear cameras working inside the camera app.
This validates rear-camera operation on this phone with the CCI completion
fix (0031) and corrected rootfs delivery. The previous repeated CCI fault
was observed while the pre-fix cached rootfs was served.

The report does not independently establish which libcamera stack the app
loaded, main/tele actuator supplier identity, autofocus performance, image
calibration, video recording or suspend/resume stability. No front-camera
validation is claimed. The baseline speaker fix remains included; audio,
Wi-Fi and Bluetooth were not explicitly rechecked in this confirmation.
