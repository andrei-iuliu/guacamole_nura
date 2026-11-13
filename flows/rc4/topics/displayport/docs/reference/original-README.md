# OnePlus 7 Pro (guacamole): USB-C DisplayPort output and audio

Proposed changes that add DisplayPort Alt Mode video (and an attempt at DP audio) to [guacamole_nura](https://github.com/emilyastranova/guacamole_nura). Today guacamole_nura has neither (`docs/PLAN.md` L88, `docs/PORTING.md` L189).

**Status: proposal. Nothing here has been compiled or run on a phone.**
- Video is a port of a configuration that is proven on the sibling OnePlus 7T Pro.
- Audio is a set of fixes for a failure nobody has solved yet: one of them targets the most likely cause, and the others make the result easier to read.

This folder is independent of `../suspend-fixes/`. Its kernel patches are numbered 0029–0033, so both sets can land.

## Pinned sources

| Repo | Commit / tag | Short name |
| --- | --- | --- |
| guacamole_nura | `5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24` | `GN` |
| hotdog-linux-bringup (OnePlus 7T Pro, same SoC/PMIC) | `47087509c4289c2580c54f5433e55366b2e00445` | `HD` |
| LineageOS android_kernel_oneplus_sm8150 (stock-derived 4.14) | branch `lineage-23.2` | `LOS` |
| kernel, current | `gitlab.com/sm8150-mainline/linux` `v6.17.0-sm8150` | 6.17 |
| kernel, next | `gitlab.postmarketos.org/soc/qualcomm-sm8150/linux` `v7.0.0-sm8150` | 7.0 |

`LOS` stock device tree for guacamole is `arch/arm64/boot/dts/18821/` (its `sm8150-mtp-overlay.dts` includes `guacamole.dtsi`, board-id 8).

## Contents

```
README.md                                   this file
docs/01-video.md                            DP Alt Mode video: what changes and why, sources, tests
docs/02-audio.md                            DP audio: the failure, root-cause analysis, the patches, tests
dts/0001-dt-displayport-video.diff          base_guacamole_working.dts: FSA4480, DP controller, altmode
dts/0002-dt-displayport-audio-link.diff     base_guacamole_working.dts: DP dai-link (apply after 0001)
kernel/0029-asoc-q6afe-dai-displayport-rx-playback-widget.patch   (HD 0052) DAPM direction fix
kernel/0030-asoc-sm8150-accept-displayport-backend.patch          (HD 0051, rebased) machine driver
kernel/0031-asoc-sm8150-enable-dp-audio-before-afe-start.patch    NEW: prepare ordering fix
kernel/0032-asoc-q6afe-split-config-and-start-errors.patch         NEW: diagnostic log split
kernel/0033-asoc-q6afe-send-dp-stream-and-controller-index.patch   NEW, experimental: downstream params
```

## Integration steps (for the agent)

Work in a branch of guacamole_nura. Follow its `CLAUDE.md`: the pmaports copy of the kernel package is a separate copy, so sync it and run `pmbootstrap checksum`.

**Video only (low risk, do first):**
1. `patch -p1 < displayport/dts/0001-dt-displayport-video.diff` (from the GN root).
2. `scripts/build_bootimg.sh`. This runs `patch_dtb.py` and `dtc`. **Fix any `dtc` error first**: the diffs were never compiled because no `dtc` was available.
3. Flash and run the tests in `docs/01-video.md`.

No kernel or config change is needed for video. GN's config already has `DRM_MSM_DP=y`, `TYPEC_DP_ALTMODE=m`, `TYPEC_MUX_FSA4480=y`, `PHY_QCOM_QMP_COMBO=y` and `DRM_AUX_HPD_BRIDGE=y`.

**Audio (after video works):**
4. Copy `kernel/0029..0033-*.patch` into `device/testing/linux-postmarketos-qcom-sm8150/`, append them to `source=` in its APKBUILD, bump `pkgrel`, then run `pmbootstrap checksum`.
5. `patch -p1 < displayport/dts/0002-dt-displayport-audio-link.diff`.
6. Rebuild the kernel and the boot image, flash, and follow the test ladder in `docs/02-audio.md`.
7. Patches 0031–0033 carry the placeholder author `guacamole_nura proposal <TODO-set-author@example.invalid>`. Replace it before committing.

## Verified here / not verified

Verified:
- The kernel patches apply with `git am` in order on 6.17 after GN's `0008` (the only GN patch that touches these files).
- The DT diffs apply to GN `5b2241b` (dry run), and the braces balance.
- Every driver detail the docs rely on was read from the 6.17 and 7.0 sources:
  - `qcom_pmic_typec` registers the DP HPD bridge.
  - `fsa4480` needs no interrupt.
  - `qmp-combo` registers an orientation switch only.
  - The prepare order in `soc-dai.c`.
  - `drm_bridge_connector` audio ops have `prepare` but no `hw_params`.

Not verified:
- Not compiled (neither the kernel nor `dtc`).
- Not run on hardware.

On **7.0**, 0029 and 0031–0033 apply. **0030 must be rebased by hand**: on plain 7.0 `patch` applies it with fuzz and creates a duplicate `case DISPLAY_PORT_RX:` label. It also depends on GN `0008`, which itself needs rebasing on 7.0 (GN `docs/PORTING.md`). The DT diffs are against GN's flattened 6.17 DTS. On 7.0, express the same nodes in a proper `.dts` on top of `sm8150-oneplus-guacamole.dts`. The labels `&mdss_dp`, `&mdss_dp_out`, `&usb_1_qmpphy` and `&pm8150b_typec` exist there.

## Known limits (from HD)

- **Two lanes only.** The pin assignment carries USB3 on the other two lanes. 2560×1440@60 works; 2560×1440@120 is corrupt, because msm DP accepts a mode beyond the HBR2×2 budget and drops to 18 bpp. Select 60 Hz. This is a driver defect, not something the DT can fix.
- **No DP audio anywhere yet.** HD got the DSP to time out on port 0x6020. `docs/02-audio.md` explains why that might be our ordering bug rather than missing firmware.
