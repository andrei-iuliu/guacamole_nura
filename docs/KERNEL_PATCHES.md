# Kernel patch taxonomy for the next SM8150 bump

Scope: the 60 patches of the rc4h recipe
`flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/` (kernel
`v7.0.0-sm8150`, `pkgrel=11`), classified so that the next bump to a
7.3/7.4 sm8150 tag can be a mechanical rebase. Nothing here is applied,
rebuilt or tested on hardware; no hunk was changed.

Patch numbers follow the recipe (`source=` order). Numbers `0013`, `0014`,
`0016`, `0023`, `0036`, `0042` and `0043` do not exist in this recipe:
`0013`/`0014`/`0016` were dropped on the 7.0 move, `0023` was a diagnostic,
`0036` was removed on rc4b (DSI PHY PM hang, kept in `flows/rc4-reverted/`)
and `0042`/`0043` are the old names of `0034`/`0035`.

## How the columns were produced

* **Subsystem** is the merge target, not the file's home directory
  (`misc` covers `drivers/misc`, `i2c`, `ufs`, `iio` and `soc`).
* **Upstream status** was checked against the mainline clone at
  `bringup-workspace/sources/linux-upstream`
  (`torvalds/linux`, `v7.2` plus the 7.3 merge window at
  `0a0d1d55dad5`, 2026-08-23) with `git cat-file`, `git grep <token> v7.2`
  and `git log v7.0..HEAD -- <path>`. "not in mainline v7.2" means the code
  hunk is still absent, not that nobody ever posted it.
* **Rebase risk** combines the patch size with the number of mainline
  commits touching the patched files between `v7.0` and `v7.2` (and
  `v7.2..HEAD` for the 7.3 window). `low` = ≤3 commits or fork-only files;
  `medium` = 4-12 commits, or Kconfig/Makefile insertion points; `high` =
  heavily reworked subsystem or a large multi-file patch.
* **Fork-only** means the file does not exist in mainline (`v7.2`): the
  guacamole/vayu/cepheus DTS files, `sound/soc/qcom/sm8150.c` (the SM8150
  machine driver), and the IPA node in `sm8150.dtsi`. These patches cannot
  conflict with mainline, but they can conflict with the sm8150 fork's own
  tag, so they must be diffed against that tag, not against mainline.

## Categories

| Category | Meaning | Count |
| --- | --- | ---: |
| `upstream-fix` | generic bug fix, suitable for upstream as-is or with a `Fixes:` tag | 19 |
| `upstream-feature` | new driver or SoC support that could go upstream | 11 |
| `device-dt` | board/SoC devicetree for this device or its fork | 12 |
| `local-workaround` | hack, quirk or policy that should not go upstream | 12 |
| `backport` | already taken from a newer upstream kernel | 1 |
| `reference-port` | ported from omarchy-mobile/hotdog, device-specific or not upstreamable as-is | 5 |

Risk: **low 38**, **medium 18**, **high 4** (`0004`, `0028`, `0064`, `0067`).

## Table

`Deps` lists patches that must be applied first (same file, same API, or the
configuration the patch assumes). `Action` is the recommendation for the
7.3/7.4 bump, not for rc4.

| # | Subject | Subsystem | Category | Origin | Upstream status | Deps | Rebase risk | Action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0001 | enable UFS (cepheus) | dts | device-dt | pmaports official recipe | n/a: fork-only DTS | — | low — fork-only file, no mainline churn | keep |
| 0002 | libbpf: fix strset UAF | misc | backport | Carlos Llamas, `b23705e6afb6` | **merged in v7.2** | — | low — already in the new base | **drop-when-bumping** |
| 0003 | vayu: drop try-power-role | dts | device-dt | Ali Nasrullayev, `b8e1eea25c09` | n/a: fork-only DTS | — | low — fork-only file | keep |
| 0004 | guacamole DSC panel + DSI DSC packets | drm-msm | reference-port | Robin Snyders; hotdog 0012 + omarchy display | not in v7.2 | 0026 | **high** — `dsi_host.c` +34 commits, panel Kconfig/Makefile churn, 720-line patch | keep; split out the DTS hunk, upstream the panel driver later |
| 0005 | s6sy761: restart the controller reliably | input | upstream-fix | Robin Snyders, omarchy touch | not in v7.2 | 0035 | low — 1 commit in the file | upstream |
| 0006 | geni I2C: stay in FIFO mode | misc (i2c) | local-workaround | hotdog 0138 | not in v7.2 | — | medium — 2 commits v7.0..v7.2, 10 in 7.3 | keep |
| 0007 | TFA9874 speaker amplifier driver | asoc | upstream-feature | hotdog 0038/0039/0041 | not in v7.2 (`tfa9874` absent) | — | medium — codec Kconfig/Makefile churn; needs fuzz 2 on the pristine tag already | upstream |
| 0008 | QUAT MI2S speaker backend | asoc | reference-port | hotdog 0045 + nura local | n/a: `sm8150.c` is fork-only | 0007 | low — fork-only file | keep |
| 0009 | ath10k: skip quiet mode for WCN3990 | net | upstream-fix | Malte Schababerle, linux-wireless (unmerged) | not in v7.2 | — | low — 1 commit | upstream |
| 0010 | UFS: cap SM8150 at HS-G3 | misc (ufs) | local-workaround | nura local | not in v7.2 | — | medium — 11 commits in `ufs-qcom.c` | keep |
| 0011 | SMB5 limits, ICL bits and AICL | power | upstream-feature | hotdog 0020/0155/0150 | not in v7.2 (driver is SMB2-only) | roots 0018/0019/0020/0022/0032 | low — 1 commit | upstream |
| 0012 | bq27xxx: Full only while supplied | power | upstream-fix | nura local | not in v7.2 | — | low — 0 commits v7.0..v7.2 | upstream |
| 0015 | IPA: bound `gsi_trans_commit_wait()` | net | upstream-fix | hotdog 0156 | not in v7.2 | — | low — file untouched since v7.0 | upstream |
| 0017 | fastrpc: sensors messages from the reserved pool | misc | upstream-fix | hotdog 0148 | not in v7.2 | — | medium — 9 commits in `fastrpc.c` | upstream |
| 0018 | SMB5: select USB3 SDP current | power | upstream-feature | hotdog 0151 | not in v7.2 | 0011 | low — small hunk | merge-with-0011 |
| 0019 | SMB5: writable `input_current_limit` | power | upstream-feature | hotdog 0152 | not in v7.2 | 0011 | low — small hunk | merge-with-0011 |
| 0020 | SMB5: continuous 5-12 V input window | power | local-workaround | nura local (emilyastranova) | not in v7.2 | 0011 | low — small hunk | keep (only needed for the abandoned 9 V path) |
| 0021 | qcom-pmic-typec: VBUS above vSafe0V is present | usb-typec | local-workaround | nura local (emilyastranova) | not in v7.2 | — | medium — port driver is being reworked upstream | keep |
| 0022 | SMBX: follow the TCPM contract | power | upstream-feature | nura local | not in v7.2 | 0011, 0018, 0020 | medium — 127-line hunk | merge-with-0011 |
| 0024 | qcom-pmic-typec: EXIT_SNK_BASED_ON_CC at init | usb-typec | upstream-fix | nura local (emilyastranova) | not in v7.2 (bit defined, never set) | 0021 | low — 2 commits | upstream |
| 0025 | dt: rename the IPA node to `ipa@1e40000` | dts | device-dt | nura local | n/a: node is fork-only | 0026 | low — 1 commit in `sm8150.dtsi` | keep |
| 0026 | nura guacamole board delta | dts | device-dt | nura local (6.17 bring-up) | n/a: guacamole DTS not upstream | 0004 | low — fork-only DTS | keep |
| 0027 | guacamole: enable the speaker parent bus | dts | device-dt | nura local | n/a: guacamole DTS not upstream | 0026 | low — fork-only DTS | keep |
| 0028 | CAMSS: SM8150 support | media | upstream-feature | omarchy-mobile `f702f688` (CAMSS by Sr-0w) | not in v7.2 (no SM8150 CAMSS) | 0029/0030/0031 build on it | **high** — 10 files/545 lines, CAMSS resource and PHY API churn | upstream |
| 0029 | rear sensors and lenses | media | upstream-feature | omarchy-mobile `f702f688` | not in v7.2 (IMX586/IMX481/LC898217XC absent) | 0028 | medium — 7 files/3.5 kloc, Kconfig/Makefile | upstream (split per driver) |
| 0030 | guacamole: three rear cameras | dts | device-dt | omarchy-mobile + nura port | n/a: guacamole DTS not upstream | 0028, 0029, 0031 | low — fork-only DTS plus a Makefile append | keep |
| 0031 | i2c-qcom-cci: init all master completions | misc (i2c) | upstream-fix | nura rear-camera port | not in v7.2 | 0028 | low — 6 commits | upstream |
| 0032 | SMB5: overvoltage and ICL readback | power | upstream-fix | nura local (+ hotdog `native3-charger`) | not in v7.2 (upstream SMB5 v4 proposal separate) | 0011 | low — 11-line hunk | upstream (coordinate with the SMB5 v4 proposal) |
| 0033 | qcom-ngd: setup ordering | slimbus | upstream-fix | omarchy-mobile `f702f688` (1/2) | **partial in v7.2** (workqueue/SSR order landed; `ngd->base` and `cancel_work_sync` did not) | pairs 0059 | medium — 8 commits; must be reduced | keep; upstream the remainder |
| 0034 | fastrpc: freezable invoke wait | misc | upstream-fix | omarchy-mobile `f702f688` | not in v7.2 | — | medium — 9 commits | upstream |
| 0035 | s6sy761: power on before the resume IRQ | input | upstream-fix | omarchy-mobile `f702f688` | not in v7.2 | 0005 | low — 1 commit | upstream |
| 0037 | drm/msm: DSI commands vs command-mode frames | drm-msm | upstream-fix | omarchy-mobile `f702f688` | not in v7.2 | — | medium — 5 DPU/DSI files, 14 commits | upstream |
| 0038 | guacamole: rear flash torch | dts | device-dt | nura local | n/a: guacamole DTS not upstream | 0026 | low — fork-only DTS | keep |
| 0039 | AW8697: Kconfig/Makefile | input | upstream-feature | hotdog 0129/0130 | not in v7.2 | — | low — 1 commit | merge-with-0040 |
| 0040 | AW8697: driver source | input | upstream-feature | hotdog 0130 | not in v7.2 | 0039 | low — new file | merge-with-0039 |
| 0041 | guacamole: haptics and alert slider | dts | device-dt | nura local | n/a: guacamole DTS not upstream | 0026, 0039/0040 | low — fork-only DTS | keep |
| 0044 | soc: `guacamole_mss_vote` | power | local-workaround | omarchy-mobile `mss_vote.c` | not in v7.2 (device policy module) | — | medium — new file using rpmhpd internals | keep |
| 0045 | ath10k: SNOC WMI key acknowledgement | net | upstream-fix | omarchy-mobile `f702f688` | not in v7.2 | — | medium — 5 files/108 lines | upstream |
| 0046 | q6afe-dai: DP RX as an input widget | asoc | upstream-fix | hotdog 0052 | not in v7.2 | — | low — 1-line change | upstream |
| 0047 | sm8150: accept the DisplayPort backend | asoc | reference-port | hotdog 0051 | n/a: `sm8150.c` is fork-only | 0008 | low — fork-only file | keep |
| 0048 | sm8150: prepare the DP codec before AFE start | asoc | local-workaround | nura rc4 DP series | not in v7.2 | 0047 | low — fork-only file, inert while the link is disabled | keep (drop if DP audio is abandoned) |
| 0049 | q6afe: split config/param/start errors | asoc | upstream-fix | nura rc4 DP series | not in v7.2 | — | low — message-only | upstream |
| 0050 | q6afe: send DP stream and controller index | asoc | local-workaround | nura rc4 DP series | not in v7.2 | — | low — 50-line hunk, gated by a disabled link | keep (gated) |
| 0051 | goodix_fp driver | input | reference-port | LineageOS downstream (supplied 0034) | not upstreamable as-is | 0055 | low — new file | keep |
| 0052 | MXM1120 Hall sensors | misc (iio) | upstream-feature | hotdog 0103/0104 | not in v7.2 | — | medium — `vendor-prefixes.yaml` 34 commits | upstream |
| 0053 | IMX471 front sensor | media | upstream-feature | hotdog 0102 | not in v7.2 | 0056 | medium — `media/i2c` Kconfig/Makefile churn | upstream |
| 0054 | popup camera motor driver | media | reference-port | hotdog popup-camera series | not in v7.2 (per-unit Hall thresholds) | 0052/0053 | low — new files only | keep |
| 0055 | guacamole: gated fingerprint node | dts | device-dt | nura fingerprint topic | n/a: guacamole DTS not upstream | 0026, 0051 | low — fork-only DTS | keep |
| 0056 | guacamole: gated pop-up camera | dts | device-dt | nura popup-camera topic | n/a: guacamole DTS not upstream | 0026, 0052-0054 | low — fork-only DTS | keep |
| 0057 | guacamole: DisplayPort alt mode video | dts | device-dt | nura rc4 DP series | n/a: guacamole DTS not upstream | 0026 | low — fork-only DTS | keep |
| 0058 | guacamole: DP audio link (disabled) | dts | device-dt | nura rc4 DP series | n/a: guacamole DTS not upstream | 0057, 0046-0050 | low — fork-only DTS | keep |
| 0059 | qcom-ngd: wait up to 5 s for QMI | slimbus | local-workaround | omarchy-mobile `f702f688` (2/2) | not in v7.2 (timeout still 1 s) | 0033 | medium — 1-line hunk in an 8-commit file | merge-with-0033 |
| 0060 | pdphy: always return the RX buffer | usb-typec | upstream-fix | nura rc4d dock debugging | not in v7.2 | — | low — 1 commit | upstream |
| 0061 | qcom-pmic-typec: vSafe0V fallback only as sink | usb-typec | local-workaround | nura rc4d dock debugging | not in v7.2 | 0021 | low — small hunk | keep |
| 0062 | tcpm: reset the hard-reset budget on unattach | usb-typec | upstream-fix | nura rc4h dock debugging | not in v7.2 | — | medium — `tcpm.c` 12 commits | upstream |
| 0063 | qcom-pmic-typec: re-read VBUS leaving sink | usb-typec | local-workaround | nura rc4h dock debugging | not in v7.2 | 0061 | low — small hunk | keep |
| 0064 | drm/msm/dp: real pixel rate for bandwidth | drm-msm | upstream-fix | nura rc4h dock debugging | not in v7.2 | — | **high** — `dp_display.c` and HPD handling reworked | upstream |
| 0065 | qcom-pmic-typec: hide re-attach in a role swap | usb-typec | local-workaround | nura rc4h dock debugging | not in v7.2 | 0024 | medium — port driver reworked | keep |
| 0066 | tcpm: tolerate one data-role mismatch | usb-typec | local-workaround | nura rc4h dock debugging | not in v7.2 | — | medium — `tcpm.c` churn | keep |
| 0067 | drm/msm/dp: re-apply/re-read EDID in get_modes | drm-msm | upstream-fix | nura rc4h dock debugging | not in v7.2 | — | **high** — EDID/HPD rework upstream | upstream |

## Upstream first

Priority is "small, self-contained, generic bug" first, then "new SoC/driver
support". Every entry needs `git format-patch` from a mainline-based branch
with a proper `Fixes:` tag where one exists. Maintainers below are from
`MAINTAINERS` in the v7.0.0-sm8150 tree (the sections are unchanged in v7.2
for these files).

| Priority | Patch | Why first | To |
| ---: | --- | --- | --- |
| 1 | 0060 | Real PD bug: the RX buffer is never returned on the error paths, so every later message is dropped. 19 lines, one file. | Bryan O'Donoghue `<bryan.odonoghue@linaro.org>`, `linux-usb@vger.kernel.org`, `linux-arm-msm@vger.kernel.org` (Qualcomm Typec Port Manager) |
| 2 | 0037 | DSI command/frame serialization fixes flicker and FIFO errors on every DSC command-mode panel, not just guacamole. | Rob Clark `<robin.clark@oss.qualcomm.com>`, Dmitry Baryshkov `<lumag@kernel.org>`, `dri-devel@lists.freedesktop.org`, `linux-arm-msm@vger.kernel.org` (DRM driver for Qualcomm display) |
| 3 | 0034 | FastRPC invoke duplicated across system freeze; hexagonrpcd hits it on every suspend. | Srinivas Kandagatla `<srini@kernel.org>`, `linux-arm-msm@vger.kernel.org` (Qualcomm FastRPC) |
| 4 | 0015 | Unbounded wait in the modem SSR path can wedge recovery; 27 lines, no ABI. | `netdev@vger.kernel.org` (networking drivers); IPA files are also `linux-arm-msm` |
| 5 | 0012 | bq27xxx reports Full while discharging when a supplier is described. Small and generic. | Sebastian Reichel `<sre@kernel.org>`, `linux-pm@vger.kernel.org` (Power supply class) |
| 6 | 0031 | CCI reset waits on master 0 even when that bus is disabled; four lines. | Wolfram Sang `<wsa+renesas@sang-engineering.com>`, `linux-i2c@vger.kernel.org`; cc `linux-arm-msm@vger.kernel.org` |
| 7 | 0062 | TCPM keeps an exhausted hard-reset budget across a VBUS-first detach, so the next partner never gets PD. | Badhri Jagan Sridharan `<badhri@google.com>`, `linux-usb@vger.kernel.org` (USB Type-C port controller drivers) |
| 8 | 0064 | DP mode_valid accepts modes that exceed the link budget (wide bus is not 4:2:0). | Rob Clark / Dmitry Baryshkov, `dri-devel@lists.freedesktop.org`, `linux-arm-msm@vger.kernel.org` |
| 9 | 0067 | DP get_modes falls back to 640x480 when the cached EDID is cleared around HPD. | as 0064 |
| 10 | 0009 + 0045 | Two ath10k WCN3990 fixes: quiet-mode crash on HL3.2, and key removal never acked on SNOC. Coordinate with the linux-wireless series already under review. | Jeff Johnson `<jjohnson@kernel.org>`, `linux-wireless@vger.kernel.org`, `ath10k@lists.infradead.org` |
| 11 | 0005 + 0035 | s6sy761: optional reset line, boot-complete polling and resume IRQ ordering. Two small patches, same file. | Dmitry Torokhov `<dmitry.torokhov@gmail.com>`, `linux-input@vger.kernel.org` |
| 12 | 0011 + 0018 + 0019 + 0022 (+0032) | SMB5/PM8150B support for `qcom_smbx`; mainline only knows SMB2/pmi8998. Send as one series and check the upstream SMB5 v4 proposal first. | Sebastian Reichel, `linux-pm@vger.kernel.org` |
| 13 | 0028 (+0029) | CAMSS SM8150 support; mainline CAMSS stops at 8x96/8300/8775P. Large, so split the CSIPHY/CSID/VFE enablement from the sensor drivers. | Robert Foss `<rfoss@kernel.org>`, Bryan O'Donoghue, `linux-media@vger.kernel.org` (Qualcomm camera subsystem) |
| 14 | 0052 + 0053 | MXM1120 (has a binding) and IMX471 sensor drivers; both are self-contained new drivers. | Jonathan Cameron `<jic23@kernel.org>`, `linux-iio@vger.kernel.org` (MXM1120); `linux-media@vger.kernel.org` (IMX471) |
| 15 | 0007 | TFA9874 codec driver; needs a DT binding and removal of the out-of-tree tfa9872 compatible first. | Liam Girdwood / Mark Brown, `linux-sound@vger.kernel.org` (ASoC) |
| 16 | 0046 + 0049 | Two small ASoC DP diagnostics/consistency fixes; 0048/0050 are experiments and stay local. | Mark Brown, `linux-sound@vger.kernel.org` |
| 17 | 0033 + 0059 | qcom-ngd startup ordering and QMI timeout. v7.2 already has most of the ordering, so send only the remaining hunks. | Srinivas Kandagatla, `linux-sound@vger.kernel.org` (SLIMbus) |

Not for upstream: 0004 (board panel driver belongs in the sm8150 fork until
the panel is described generically), 0006/0010 (device quirks), 0020/0021/
0061/0063/0065/0066 (board/PMIC policy), 0044 (policy module), 0048/0050
(DP audio experiments), 0051/0054 (downstream interfaces), and every
`device-dt` patch.

## Bump plan (7.3/7.4)

### Shape of the work

Keep the recipe's patch files as the source of truth, but do the rebase on a
branch of the new fork tag so `git am`/`git rebase` can carry authorship and
conflicts:

```bash
git clone --branch v7.3.0-sm8150 <fork> nura-7.3
cd nura-7.3 && git checkout -b nura-7.3-rebase
# apply in the groups below, patching the recipe files last
git am -3 --whitespace=nowarn /path/to/recipe/0005-*.patch
...
```

For every group: `git am` (3-way), then `git range-diff` against the old
branch, then a scratch `patch -p1 --fuzz=0` run of the whole reordered
series. Only when the series applies at zero fuzz, write the resulting
patch files back into the recipe and regenerate `sha512sums`.

Do **not** renumber patches in the recipe until the series is stable. The
recipe's order is also the apply order, so keep dependencies contiguous
(see table). Renumbering is a separate, mechanical commit.

### Order

1. **Preparation, no patch work.** Diff the new tag's
   `arch/arm64/boot/dts/qcom/sm8150*.dts*`,
   `sound/soc/qcom/sm8150.c`, `drivers/net/ipa/*` and
   `drivers/power/supply/qcom_smbx.c` against `v7.0.0-sm8150`. These are the
   fork-only or fork-diverged files where "upstream status" says nothing.
   **Drop 0002** (merged in v7.2) and audit `0033`/`0032`/`0059` for hunks
   that the new tag already has.
2. **Driver fixes with no dependencies:** `0005`, `0035`, `0012`, `0015`,
   `0017`, `0034`, `0031`, `0010`, `0006`.
3. **Power and USB-PD stack** (one file cluster, keep contiguous):
   `0011`, `0018`, `0019`, `0020`, `0022`, `0032`, then the Type-C files
   `0021`, `0024`, `0060`, `0061`, `0062`, `0063`, `0065`, `0066`.
4. **Display:** `0004` first (it rewrites the guacamole DTS and
   `dsi_host.c`), then `0037`, then the DP fixes `0064`, `0067`.
5. **Audio and SLIMbus:** `0033`, `0059`, `0007`, `0008`, `0046`, `0047`,
   `0048`, `0049`, `0050`. (`0033`/`0059` first: they touch a different
   file and may need reducing.)
6. **Camera and media:** `0028`, `0029`, `0053`, `0054`, `0052`.
7. **Board devicetree, in include order:** `0026`, `0027`, `0038`, `0041`,
   `0030`, `0057`, `0058`, `0055`, `0056`. `0026` creates the nura dtsi
   that the others extend; `0055` owns the middle include slot (after the
   rear-camera include from `0030`) and `0056` the last one.
8. **New/experimental drivers and policy:** `0044`, `0045` (ath10k),
   `0039`, `0040`, `0051`, `0052`, `0053`, `0054`.

`0002` is the only patch that must disappear. `0033` is the only patch that
must shrink (the v7.2 file already initializes the work and the master
workqueue before the PDR lookup and the SSR notifier; only the `ngd->base`
ordering, `ctrl->ngd = NULL` on failure and `cancel_work_sync()` remain).

### Conflict hot spots

* `drivers/gpu/drm/msm/dp/` (`0064`, `0067`): HPD, link training and EDID
  handling were reworked between v7.0 and v7.2/7.3 (`dp: rework HPD
  handling`, `clear EDID on display unplug`, `Move link training to
  atomic_enable()`, `atomic_create_state`). Expect to rewrite, not rebase.
* `drivers/gpu/drm/msm/dsi/dsi_host.c` (`0004`): 34 commits since v7.0;
  the DSC slice-packet code is the risky part.
* `drivers/media/platform/qcom/camss/` (`0028`): resource tables,
  `regulator_bulk_data`, CSIPHY generation tables — 12 commits since v7.0.
* `drivers/usb/typec/tcpm/qcom/qcom_pmic_typec_port.c` (`0021`, `0024`,
  `0061`, `0063`, `0065`): five patches on one file that upstream is
  actively changing; v7.2 already carries an unused
  `BYPASS_VSAFE0V_DURING_ROLE_SWAP` bit that conflicts conceptually with
  `0065`.
* `drivers/power/supply/qcom_smbx.c` (`0011`, `0018`, `0019`, `0020`,
  `0022`, `0032`): six stacked patches; never interleave them with other
  qcom_smbx work.
* `sound/soc/qcom/sm8150.c` (`0008`, `0047`, `0048`): fork-only file; a
  fork tag change lands here without any mainline warning.
* `drivers/slimbus/qcom-ngd-ctrl.c` (`0033`, `0059`): v7.2 already moved
  part of the initialization; reduce `0033` before applying.
* `drivers/misc/fastrpc.c` (`0017`, `0034`): 9 commits since v7.0.
* Kconfig/Makefile insertion points (`0007`, `0029`, `0039`, `0051`,
  `0052`, `0053`): the hunks are trivial but the context moves every
  release; `0007` already needs fuzz 2 on the pristine tag.
* `arch/arm64/boot/dts/qcom/Makefile` (`0030`) and
  `Documentation/.../vendor-prefixes.yaml` (`0052`): append-only hunks that
  drift.

## Trailers and checksums

Every patch in the recipe now carries three header trailers before its
`---` separator (or before the first diff line when the patch had no commit
message), and only the header changed:

```
Nura-Category: <category>
Nura-Origin: <author / project / commit>
Upstream-Status: <mainline status>
```

The diff body (from the first `diff --git`/`--- a/` line to EOF) is
byte-identical to the previous revision, and `sha512sums` in the APKBUILD
was regenerated. The checks that were run:

```bash
# 1. diff bodies unchanged
for f in 0*.patch; do
  p=flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/$f
  diff <(git show HEAD~:"$p" | sed -n '/^diff --git\|^--- a\//,$p') \
       <(sed -n '/^diff --git\|^--- a\//,$p' "$p") || echo "CHANGED $f"
done

# 2. hashes
sh -c '. ./APKBUILD; printf "%s\n" "$sha512sums"' | grep . | grep -v linux-v7 |
  sed 's/config-$_flavor.$arch/config-postmarketos-qcom-sm8150.aarch64/' |
  sha512sum -c

# 3. source= and 0*.patch agree
# (60 files, 60 source entries, 60 sha512 lines, no duplicates)
```
