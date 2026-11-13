# rc4 topic: DisplayPort (video + DP audio experiment)

Status: ready for review.  Video is a devicetree-only port and is expected to
work but has **not been run on a phone**.  DP audio is **not solved**, is
**off by default**, and this topic does not claim it works; it ships the
smallest set of source-level fixes that make the failure readable and tests the
one hypothesis that can be checked from source, behind an explicit one-line
opt-in (`status = "disabled"` on the DP audio link).

Owner: this directory only
(`flows/rc4/topics/displayport`).  Everything outside it was treated as
read-only; the input under
`bringup-notes/Patches/displayport` was not
modified.

* Baseline: `device/testing/rc3/linux-postmarketos-qcom-sm8150`
  (7.0.0-sm8150-rc3, pkgrel 4, patches 0001–0041).
* Target: **7.0.0-sm8150-guacamole-rc4** (`CONFIG_LOCALVERSION`, see
  `recipe/config.fragment`).  Old image names are never relabelled.
* Input: all 10 files of `Patches/displayport` (README, two docs, two
  flattened-DTS diffs, five kernel patches).  Every one is tracked in
  `inventory.json` with its sha256 and disposition.

| supplied file | disposition | becomes |
| --- | --- | --- |
| `README.md` | integrated (review copy) | `docs/reference/original-README.md` + this file's claim review |
| `docs/01-video.md` | integrated (review copy) | `docs/reference/01-video.md` |
| `docs/02-audio.md` | integrated (review copy) | `docs/reference/02-audio.md` |
| `dts/0001-dt-displayport-video.diff` | integrated (rewritten for 7.0) | `dts/0001-arm64-dts-guacamole-displayport-video.patch` |
| `dts/0002-dt-displayport-audio-link.diff` | integrated (**gated**: ships `status = "disabled"`) | `dts/0002-arm64-dts-guacamole-displayport-audio-link.patch` |
| `kernel/0029-…-playback-widget.patch` | integrated | `patches/0046-…-input-widget.patch` |
| `kernel/0030-…-accept-displayport-backend.patch` | integrated | `patches/0047-…-accept-displayport-backend.patch` |
| `kernel/0031-…-enable-dp-audio-before-afe-start.patch` | integrated (**experiment**: inert unless the link is enabled) | `patches/0048-…-prepare-dp-codec-dai-before-afe-start.patch` |
| `kernel/0032-…-split-config-and-start-errors.patch` | integrated | `patches/0049-…-split-port-config-and-start-errors.patch` |
| `kernel/0033-…-send-dp-stream-and-controller-index.patch` | integrated, **gated** (experimental) | `patches/0050-…-send-dp-stream-and-controller-index.patch` |

## What is in this directory

```
README.md                                   this file (integration + claim review)
inventory.json                              every input and artifact, hashes, disposition
patches/0046…0050-*.patch                   five kernel patches, zero-fuzz rebase
dts/0001…0002-*.patch                       two devicetree patches for 7.0
recipe/APKBUILD.fragment                    source= lines + pkgrel note (parent merges)
recipe/config.fragment                      CONFIG_LOCALVERSION change + symbol check
checks/check_displayport.py                 source/DT checks + kernel-invariant guards, 90 assertions
checks/compile_check.sh                     clang -fsyntax-only harness for the 3 files
checks/RESULTS.md                           exact commands and outputs of every check
.verify/tree/                               patched copies of the 5 touched files (evidence)
.verify/dts-tree/                           symlink overlay used for the dtc compile
.verify/logs/                               raw dtc and checkpatch logs
docs/reference/                             verbatim copies of the three input docs
                                            + LineageOS evidence for patch 0050
```

Nothing here is wired into a package yet: the parent merges the fragments and
patch files into the rc4 kernel package after review.

## Integration steps (parent)

1. Create `device/testing/rc4/linux-postmarketos-qcom-sm8150/` as a copy of the
   rc3 package (0001–0041 unchanged).
2. Copy the seven patch files in, keeping the names:
   `patches/0046…0050-*.patch` and `dts/0001…0002-*.patch`.
   The `dts/` names carry no kernel-series number on purpose: `0042–0045` are
   left free for the other rc4 topics, and these two only need unique names
   inside the package.
3. Apply `recipe/APKBUILD.fragment`: append the seven names to `source=` (in
   numeric order after `0041-…`), bump `pkgrel`, update the header comment.
4. Apply `recipe/config.fragment`: only
   `CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"` changes.  All required
   symbols are already `=y`/`=m` in the rc3 config (list with line numbers in
   that file).
5. `pmbootstrap checksum linux-postmarketos-qcom-sm8150` in the package dir,
   then `diff -rq` against the pmaports copy as usual.
6. Build, flash with the normal rc4 boot flow, and follow the video test list
   below.  Keep DP audio out of the "release notes claims" — see the last
   section.
7. Merge order inside the series matters only for 0047→0048 (same function) and
   0049→0050 (same function); the numbering above is the order that applies at
   zero fuzz.

No new makedepends, no subpackage, no systemd unit, no udev rule and no ALSA
UCM file: DP video needs no userspace support, and DP audio is not at the point
where a UCM entry would be honest.

## Default integration vs optional experiment

This is the part the parent must not blur when merging.

### Default rc4 build (safe)

| what | state in the default build |
| --- | --- |
| `dts/0001` — FSA4480, DP controller, altmode, port@2 | **enabled** (this is the feature) |
| `dts/0002` — `dp-dai-link` under `&sound` | **present but `status = "disabled"`** |
| kernel patches `0046`, `0047`, `0048`, `0049`, `0050` | applied, but each one is reachable only through a `DISPLAY_PORT_RX` stream — i.e. only when the link above is enabled |
| config | only `CONFIG_LOCALVERSION`; no DP-audio symbol exists |

Why the disabled link is the whole safety story:

* `qcom_snd_parse_of()` sizes the link array with `of_get_available_child_count()`
  and then walks `for_each_available_child_of_node()`
  (`sound/soc/qcom/common.c`).  That iterator is
  `of_get_next_available_child()` → `of_get_next_status_child(child,
  __of_device_is_available)`, whose own kerneldoc says it "automatically skips
  any disabled nodes (i.e. status = \"disabled\")" (`drivers/of/base.c`).  So a
  child with `status = "disabled"` is not parsed and `card->num_links` is
  exactly what rc3 has.  Measured on the compiled DTB: 12 children of the
  sound node, 11 available links, and `dp-dai-link` is the only disabled one —
  the same 11 links rc3 registers.
  Honest caveat: no other in-tree qcom board disables a `*-dai-link` child, so
  this is verified from the parser rather than from precedent.  If a reviewer
  does not want to rely on it, use the "zero DP-audio code" variant below,
  which removes the node entirely.
* If the link *were* enabled and the DP controller (the parent device of the
  `hdmi-codec` component the link points at) did not probe,
  `snd_soc_add_pcm_runtime()` would return `-EPROBE_DEFER` for that link, the
  whole `snd_soc_bind_card()` would be torn down, and `snd-sm8150`'s probe
  would defer: **no speakers, no microphone, no headset** until DP appears.
  That is the regression this split exists to prevent.
* No kernel patch changes that: 0048 only runs in `sm8150_snd_prepare()` for
  `DISPLAY_PORT_RX`; 0046/0047 touch declarations and log noise for a port no
  link references; 0049 renames AFE messages (log text only); 0050 runs only
  when port 0x6020 is started.

### Optional DP audio experiment (opt-in, one line + rebuild)

1. In
   `arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-displayport.dtsi`, change
   the `dp-dai-link` node from `status = "disabled"` to `status = "okay"`.
   (The comment above the node says the same.)
2. Rebuild and boot.
3. Follow the DP audio ladder in `docs/reference/02-audio.md`, with the
   `S24_LE` correction from claim C21.  Expect the DSP to time out on port
   0x6020; nothing in this topic is known to produce sound.
4. Optional bisecting builds: drop `0048` from `source=` to test whether the
   ordering change matters, or drop `0050` to test whether the extra AFE
   parameters matter.  Both variants still need the `status = "okay"` flip.

A build with `status = "okay"` must never be published as the rc4 release
image: it makes the whole audio card depend on the DP controller, for a feature
nobody has made work.

### Variant if the parent prefers zero DP-audio code in the tree

Omit `dts/0002` (and optionally `0048`/`0050`) from `source=`.  The remaining
patches still apply at zero fuzz and stay inert; `dts/0001` is then the only
functional change.  The reason this variant is *not* the default here is that
the task asked for all supplied files to be integrated, and the disabled node
documents the experiment in-tree.

## What changed relative to the supplied files

### Renumbering 0029–0033 → 0046–0050

Required: rc3 already uses 0029–0033
(`0029-media-guacamole-rear-sensors-and-lenses`, `0030-arm64-dts-guacamole-three-rear-cameras`,
`0031-i2c-qcom-cci-initialize-all-master-completions`, `0032-smb5-readback`,
`0033-slim-ngd-work-init`).  The input's "so both sets can land" note is wrong
for the 7.0 baseline.  Existing maximum is 0041, so the series starts at 0046.

### DT: rewritten, not applied

`dts/0001`/`0002` edit `base_guacamole_working.dts`, the flattened **6.17**
devicetree.  rc4 boots a real 7.0 devicetree, so those diffs cannot apply at
all.  The same nodes are expressed as a new board file
`arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-displayport.dtsi`, included
from the board delta `sm8150-oneplus-guacamole-nura.dtsi`:

* FSA4480 at `&i2c4` (QUP0 SE4, address 0x42, same bus as the TFA9874 amps at
  0x34/0x35 — no address conflict), `compatible = "fcs,fsa4480"`,
  `mode-switch`, `orientation-switch`, graph `port`/`endpoint`.
* `fsa_usbc_ana_en` on TLMM GPIO 100, `output-low`: the pin is driven low at
  boot, which asserts EN# (active low) and keeps the switch powered and
  reachable at probe; GPIO 100 is unused elsewhere in the guacamole devicetree.
* `&mdss_dp { status = "okay"; };` and
  `&mdss_dp_out { data-lanes = <0 1>; };` (two lanes; the DP+USB pin
  assignment carries USB3 on the other two).
* Connector `altmodes { displayport { svid = /bits/ 16 <0xff01>; vdo = <0x00001c46>; }; }`
  and `port@2` (SBU) linked to the FSA4480 endpoint.
* DP audio: `&sound { dp-dai-link { … } }` in `dts/0002`, with
  `cpu = <&q6afedai DISPLAY_PORT_RX>`, `platform = <&q6routing>`,
  `codec = <&mdss_dp>` — **shipped with `status = "disabled"`**, see
  "Default integration vs optional experiment" above for why and for the
  one-line opt-in.

Layout and values were taken from the in-tree `sm8150-hdk.dts` (same SoC, same
PM8150B Type-C controller, same `vdo = 0x1c46`, same `port@2` SBU wiring), which
is stronger local evidence than the 6.17 flattened diff.

### Kernel patches: what was repaired

* **0046** (was 0029) — same one-line change
  (`SND_SOC_DAPM_AIF_OUT` → `AIF_IN`, `"NULL"` → `NULL`).  Only the commit
  message was rewritten, because the mechanism it claimed does not hold on
  7.0: AIF direction no longer influences DAPM power (see claim C9).  This is
  a consistency fix, not a fix for `-110`.
* **0047** (was 0030) — rebased onto the rc3 tree, where `0008` already moved
  `QUATERNARY_MI2S_RX` into its own `shutdown` case; all three `case
  DISPLAY_PORT_RX:` labels land in the right switches, no duplicates.  The
  input's hand-rebase note is correct in spirit (the patch does not apply on
  plain 7.0) but the failure mode it describes is wrong (see claim C16).
* **0048** (was 0031) — premise re-verified against 7.0, plus two hardening
  changes: iterate `for_each_rtd_codec_dais()` instead of indexing codec 0, and
  propagate the codec prepare error.  The comment now names the 7.0 hooks
  (`drm_bridge_funcs.dp_audio_prepare`, no `hw_params` in
  `drm_connector_hdmi_audio_funcs`, `reinit_completion` inside
  `msm_dp_display_signal_audio_start()`) and no longer claims the repeated
  prepare is simply "harmless".  The DAI-bookkeeping / symmetry audit and the
  completion analysis are in the commit message and below (claims C30-C31).
  Reachable only with the DP audio link enabled.
* **0049** (was 0032) — extended.  The input renamed two of four identical
  `"AFE enable for port 0x%x failed"` messages.  The other two are also
  reachable for port 0x6020 (`q6afe_port_set_param_v2()` logs the inner
  set-param failure, and the TDM slot-mapping branch logs its own), so the
  diagnostic goal was not met; all four are now distinct.
* **0050** (was 0033) — same parameters and ordering, with an explicit
  `AFE_API_VERSION_HDMI_DP_IDX_CFG` instead of an implicit `minor_version`.
  The commit message was rewritten: the ids, the 8-byte `{u32 minor_version;
  u32 idx;}` payload (8 bytes per parameter, 16 bytes for both before their
  parameter headers), the module/instance, the "before the port start"
  boundary and the default index 0 are now cited line-by-line from the
  LineageOS lineage-23.2 reference tree (quoted excerpts in
  `docs/reference/lineage-23.2-dp-audio-evidence.md`), and everything that is
  still an assumption (firmware acceptance, two SET_PARAM_V2 vs one packed
  SET_PARAM, order relative to the config) is labelled as such.  The payload
  struct carries `static_assert(sizeof(...) == 8)` and each `SET_PARAM_V2`
  keeps `sizeof(idx)` = 8, so nothing is padded to 16 per command.  It also
  states the deliberate divergence: downstream aborts the prepare for errors
  other than `-EOPNOTSUPP`, this patch only warns.  Runs only when port 0x6020
  is started, so it cannot affect video or other audio.

### Rejected

* Applying the flattened-DTS diffs to `base_guacamole_working.dts` (legacy 6.17
  boot flow).  rc4 does not boot that tree; nothing in this topic touches
  `base_guacamole_working.dts`, `patch_dtb.py` or `build_bootimg.sh`.
* Using the supplied `From: guacamole_nura proposal <TODO-set-author@example.invalid>`
  placeholder.  The series is signed off as `Nura rc4 displayport series
  <rc4-displayport@nura.invalid>`, a non-person workgroup identity.  Replace it
  with a real author before any upstream submission.
* Claiming the DAPM direction fix as the cause of `-110`, and claiming any DP
  audio result at all.

## Claim review (what was checked, how, and what survived)

Legend: **verified** = re-read in the 7.0 baseline sources or measured here;
**contradicted** = the stated mechanism/claim does not hold; **unverified** =
cannot be established without hardware or sources that are not on this host.
"Applies" means `patch --fuzz=0`; "compiles" refers to `checks/RESULTS.md`;
nothing below is a hardware result.

| # | claim (from the input) | verdict | evidence (path / command) |
| --- | --- | --- | --- |
| C1 | Video only needs the DT change; no kernel/config change | **verified** | `config-postmarketos-qcom-sm8150.aarch64`: `CONFIG_DRM_MSM_DP=y` L4927, `CONFIG_TYPEC_DP_ALTMODE=m` L6320, `CONFIG_TYPEC_MUX_FSA4480=y` L6306, `CONFIG_PHY_QCOM_QMP_COMBO=y` L8060, `CONFIG_DRM_AUX_HPD_BRIDGE=y` L4859; drivers present in `drivers/gpu/drm/msm/dp/`, `drivers/usb/typec/…`, `drivers/phy/qualcomm/phy-qcom-qmp-combo.c` |
| C2 | The DT diffs were never compiled because no `dtc` was available | **verified for the supplied diffs / superseded** | no system `dtc` here either; the kernel's own `dtc` was built from `scripts/dtc` into `/tmp` and the rewritten DT compiles: `dtc` exit 0, 0 errors, reproducible 115698-byte DTB with the disabled DP link, same 27 warnings as the unpatched baseline (`checks/RESULTS.md`, "Devicetree compile").  The stale "Not compiled: no dtc was available" sentence was removed from `dts/0001` |
| C3 | FSA4480 binding needs only `compatible`, `reg`, `port` | **verified** | `Documentation/devicetree/bindings/usb/fcs,fsa4480.yaml` (`required:`); driver `drivers/usb/typec/mux/fsa4480.c` takes no interrupt, `vcc` optional (`devm_regulator_get_enable_optional`) |
| C4 | FSA4480 needs no interrupt | **verified** | same driver; probe only reads `FSA4480_DEVICE_ID` and warns `FSA4480 not found` if the chip does not answer (so a wrong EN# polarity shows up there) |
| C5 | `qcom_pmic_typec` registers the DP HPD bridge on the connector node | **verified** | `drivers/usb/typec/tcpm/qcom/qcom_pmic_typec.c:95` `devm_drm_dp_hpd_bridge_alloc(tcpm->dev, to_of_node(tcpm->tcpc.fwnode))` |
| C6 | `typec_displayport` sends HPD to that fwnode, so no `displayport = <&mdss_dp>` property is needed | **verified (source), unverified (hardware)** | `drivers/usb/typec/altmodes/displayport.c:190` `drm_connector_oob_hotplug_event(dp->connector_fwnode, …)`; connector fwnode is the one the bridge was allocated for; `sm8150-hdk.dts` has the same DT shape with no extra property |
| C7 | Connector port numbering: port@0 data role, port@1 SuperSpeed/DP mux, port@2 SBU | **verified** | `sm8150-hdk.dts:549-572`, `qrb5165-rb5.dts:1549-1572`; compiled blob resolves port@2 ↔ FSA4480 endpoint both ways |
| C8 | `DISPLAY_PORT_RX` is declared `AIF_OUT` while every other RX port is `AIF_IN` | **verified** | `sound/soc/qcom/qdsp6/q6afe-dai.c:961` vs. lines 758-1009 (`HDMI_RX`, all `SLIMBUS_*_RX`, `QUAT_MI2S_RX`, TDM RX, `USB_RX` are `AIF_IN`) |
| C9 | …therefore the playback path never powers the port and the DSP is not started, giving `-110` | **contradicted (7.0)** | `sound/soc/soc-dapm.c`: `aif_in` and `aif_out` both use `dapm_generic_check_power`, are not endpoints (`w->is_ep` is set only for mic/input/spk/hp/output/sink/…), and sit in the same up/down sequence groups (L85-86, L144-145, L3842-3844); there is no direction-dependent code left.  Also: the port start happens in `q6afe_dai_prepare()` (core-driven), not via DAPM.  HD's own log showed `-110` persisting after the patch.  → 0046 is cosmetic |
| C10 | ASoC prepares CPU DAIs before codec DAIs | **verified** | `sound/soc/soc-dai.c:588-601` (`snd_soc_pcm_dai_prepare` walks `rtd->dais`), `include/sound/soc.h:1197` (`snd_soc_rtd_to_codec(rtd, n) = dais[n + num_cpus]`), `:1217` (`for_each_rtd_dais`) |
| C11 | The codec's `prepare` is the only hook that reaches `msm_dp_audio_prepare()` | **verified, with a 7.0 correction** | the path is now `drm_bridge_connector_audio_prepare()` (`drivers/gpu/drm/display/drm_bridge_connector.c:617-638`) → `drm_bridge_funcs.dp_audio_prepare` = `msm_dp_audio_prepare` (`drivers/gpu/drm/msm/dp/dp_drm.c:121`); `struct drm_connector_hdmi_audio_funcs` (`include/drm/drm_connector.h:1154+`) has startup/prepare/shutdown/mute_stream and **no** `hw_params`, so `hdmi_codec_hw_params()` returns immediately (`sound/soc/codecs/hdmi-codec.c:578`) |
| C12 | `snd_soc_dai_prepare()` is exported and safe to call from a link `.prepare` | **verified** | `sound/soc/soc-dai.c:354-368`, `EXPORT_SYMBOL_GPL`; `__soc_pcm_prepare()` calls the link prepare first (`soc-pcm.c:933-951`), i.e. after hw_params, before the CPU DAI prepare |
| C13 | `msm_dp_audio_prepare()` is idempotent (registers rewritten, completion re-armed) | **partially verified** | `dp_audio.c:268-308` rewrites SDP/ACR/safe-to-exit level, sets `audio_enabled = true`, calls `msm_dp_display_signal_audio_start()` → `reinit_completion(&dp->audio_comp)` (`dp_display.c:275-282`).  Double-calling is therefore not obviously harmful, but a disconnect with `audio_enabled` set waits 5 s for that completion (`dp_display.c:896`) — pre-existing behaviour, unchanged by 0048 |
| C14 | The DP codec prepare returns `-EINVAL` when DP is not powered | **verified** | `dp_audio.c:287-290` (`if (!msm_dp_display->power_on) rc = -EINVAL`) — with 0048 this error now surfaces at link prepare instead of later |
| C15 | Stock 4.14 prepared codec DAIs before the CPU DAI | **verified against the downstream reference (not the factory build)** | LineageOS `lineage-23.2` `sound/soc/soc-pcm.c:775-830`: `soc_pcm_prepare()` runs the machine prepare, then the platform prepare, then **all codec DAIs**, then the CPU DAI.  Fetched read-only; hash and excerpt in `docs/reference/lineage-23.2-dp-audio-evidence.md` §5.  This is a downstream reference tree, not proven to be the factory image |
| C16 | `0029`/`0031`/`0032`/`0033` apply on 7.0; `0030` needs a hand rebase and on plain 7.0 creates a duplicate `case DISPLAY_PORT_RX:` label | **partly contradicted** | 0046/0048/0049/0050 correspond to those, all rebased here and applied at fuzz 0.  Measured on plain 7.0 (0008 reverted) the supplied 0030 *rejects* hunk 3 (`patch --fuzz=0`, default fuzz and `git apply --check` all fail) instead of creating a duplicate label; `grep -c 'case DISPLAY_PORT_RX:'` → 2.  See `checks/RESULTS.md`, "Supplied patch 0030 on plain 7.0" |
| C17 | The four labels `&mdss_dp`, `&mdss_dp_out`, `&usb_1_qmpphy`, `&pm8150b_typec` exist in the 7.0 tree | **verified** | `sm8150.dtsi:3953`, `:4007`, `:3544`, `pm8150b.dtsi:61`; also `&i2c4` (`sm8150.dtsi:1140`) and `&sound`/`&q6afedai`/`&q6routing` from the board delta |
| C18 | Patch numbers 0029-0033 let both patch sets land | **contradicted** | rc3 already ships 0029-0033 (camera/slim); renumbered to 0046-0050 |
| C19 | DP audio can be attempted after video works, by opening MM3 and routing it | **verified (plumbing only)** | `q6routing.c:524` `Q6ROUTING_RX_MIXERS(DISPLAY_PORT_RX)`, `:743` the `"DISPLAY_PORT_RX Audio Mixer"` widget, `:961` its route to `"DISPLAY_PORT_RX"`; ALSA exposes `<widget> <kcontrol>` = `"DISPLAY_PORT_RX Audio Mixer MultiMedia3"` (`snd_soc_cnew` prefixes with the widget name).  Whether sound comes out is untested |
| C20 | The DP RX DAI runs 48 kHz, 2–8 ch, S16/S24 | **verified** | `sound/soc/qcom/qdsp6/q6dsp-lpass-ports.c:82-97` (`Q6AFE_DP_RX_DAI`) |
| C21 | "Try 48 kHz/16-bit/2 ch only" in the audio test ladder | **contradicted (for this card)** | `sm8150.c:47-62` `sm8150_be_hw_params_fixup()` forces every DPCM back end to 48 kHz, 2 ch, `S24_LE`, so a 16-bit request is overridden.  Test with `S24_LE`/`S32_LE` in mind |
| C22 | FSA4480 sits on the same I2C bus as the TFA9874 amps, which keeps working | **verified (addresses), unverified (behaviour)** | `&i2c4` already has `audio-codec@34`/`@35`; FSA4480 is `@42`, GPIO 100 is unused in the board dts.  `CONFIG_I2C_QCOM_GENI` FIFO mode is a kernel patch already in rc3 (`0006`) |
| C23 | DP Alt Mode VDO `0x1c46` and SVID `0xff01` | **verified** | identical values in `sm8150-hdk.dts:542-547` and `qrb5165-rb5.dts:1542-1547`; `connector/usb-connector.yaml` restricts `svid` to `0xff01`; compiled blob keeps `[ff 01]`/`0x1c46` |
| C24 | Two lanes are enough for 2560×1440@60, @120 is corrupt (driver defect) | **unverified** | hardware claim; no source evidence either way on this tree |
| C25 | FSA4480 `pinctrl` enable state (GPIO 100, output-low) | **verified as expressed, unverified as effective** | pin group is new and unique in the guacamole dtb; the polarity comes from the stock 18821 tree via the input doc (no LOS sources here).  If probe logs `FSA4480 not found`, this is the first thing to check |
| C26 | `lsmod | grep typec_displayport` should show the module autoloaded | **verified (mechanism)** | `drivers/usb/typec/altmodes/displayport.c:837-841` has a `typec_device_id` table + `MODULE_DEVICE_TABLE(typec, …)`; the altmode device is created from the connector's `altmodes` node by `typec_port_register_altmodes()` (`class.c:2610`, called from `tcpm.c:7918`) |
| C27 | Nothing here was compiled or run on hardware | **still true for hardware; changed for compile** | see `checks/RESULTS.md`: applies at fuzz 0, syntax check clean, DT compiles.  No phone, SSH, adb, fastboot, sudo or pmbootstrap was used |
| C28 | "Adding the DP dai-link is harmless as long as DP audio is not used" | **contradicted** | `snd_soc_add_pcm_runtime()` returns `-EPROBE_DEFER` when a link's codec DAI is not registered (`sound/soc/soc-core.c:1205-1266`), `snd_soc_bind_card()` tears the card down on any error from `snd_soc_add_pcm_runtimes()` (`:2185-2187`), and `devm_snd_soc_register_card()` hands that error back to `snd-sm8150`'s probe (`soc-devres.c:65-84`).  The codec is the DP controller's `hdmi-codec`, whose component matches `&mdss_dp` via the parent-node fallback in `soc_component_to_node()` (`soc-core.c:830-840`).  So an enabled link makes the **whole card** depend on `mdss_dp` probing.  Hence the link ships `status = "disabled"` |
| C29 | A disabled DAI-link node is ignored by the card | **verified** | `qcom_snd_parse_of()` uses `of_get_available_child_count()` and `for_each_available_child_of_node()` (`sound/soc/qcom/common.c:75,85`); `__of_device_is_available()` treats only absent status/`okay`/`ok` as available (`drivers/of/base.c:523-532`).  Measured on the compiled DTB: 12 sound-node children, 11 available links, `dp-dai-link` the only disabled one — identical to the rc3 link set |
| C30 | 0048 cannot disturb ASoC DAI bookkeeping | **verified** | `snd_soc_dai_prepare()` only checks `snd_soc_dai_stream_valid()`, calls `ops->prepare` and logs via `soc_dai_ret()` (`sound/soc/soc-dai.c:354-368`); the rollback marks are pushed/popped only for `hw_params`, `startup`, `trigger` and compress startup (`soc-dai.c:417,434,451,471,637,653`).  Calling prepare out of band touches no mark, so startup/shutdown symmetry is unchanged.  Guarded by `checks/check_displayport.py` ("touches no rollback marks") |
| C31 | Repeating the codec prepare in one `snd_pcm_prepare()` is safe | **verified with one bounded exception** | the DRM HDMI audio `prepare` is documented as "Can be called multiple times for each setup" (`include/drm/drm_connector.h:1166-1176`); `hdmi_codec_prepare()` keeps no state (`sound/soc/codecs/hdmi-codec.c:607-641`); `msm_dp_audio_prepare()` re-programs SDP/ACR/exit level, sets `MMSS_DP_AUDIO_CFG` bit 0 through an idempotent read-modify-write and re-arms `dp->audio_comp` (`dp_audio.c:237-308`).  Completion: completed in `msm_dp_audio_shutdown()`, waited on only in `msm_dp_display_disable()` with `HZ * 5` (`dp_display.c:896`, exactly one waiter).  Exception: an unplug racing the microseconds between the two prepares would extend that existing wait to its 5 s timeout ("audio comp timeout"), then clear the state.  Error cleanup: close runs `soc_pcm_clean()` -> `snd_soc_dai_shutdown()` -> `hdmi_codec_shutdown()` -> `msm_dp_audio_shutdown()`, so a failed prepare cannot leave the DP codec enabled |
| C32 | 0050's parameter ids, payload and ordering are downstream facts | **verified against primary source** | LineageOS `lineage-23.2` (fetched read-only; excerpts and sha256 in `docs/reference/lineage-23.2-dp-audio-evidence.md`): `apr_audio-v2.h:3050-3053` ids `0x102b5`/`0x102b6`; `:2125-2133` `struct afe_display_stream_idx` / `afe_display_ctl_idx` = `{u32 minor_version; u32 idx;}` `__packed`, i.e. **8 bytes each (16 bytes for both)** before their parameter headers; `q6afe.c:6758-6834` `afe_set_display_stream()` packs both with `AFE_MODULE_AUDIO_DEV_INTERFACE`/`INSTANCE_ID_0`/`minor_version = 1` in one SET_PARAM; `msm-dai-q6-hdmi-v2.c:331-361` calls it before `afe_port_start()` while the port is stopped, continues only on `-EOPNOTSUPP`, and takes index 0 by default.  Still unverified: firmware acceptance on guacamole, two SET_PARAM_V2 vs one packed SET_PARAM, and whether the position relative to the config matters (patch 0050 says all three) |

Two consequences of the review worth stating plainly:

1. **0046 is not a fix.** Keep it if the goal is upstream/consistency, but do
   not expect it to change behaviour on 7.0; the input's own HD log agrees
   (`-110` survived it).
2. **0048 is the only structural difference from stock that can explain
   `-110` without blaming firmware**, and it is a hypothesis: the ordering
   asymmetry is proven from source, the causal link is not.
3. **The DP audio link is a whole-card dependency if it is ever enabled.**
   That is why it ships disabled; see C28/C29 and the section above.  This is
   not a stylistic choice, it is the difference between "DP audio fails" and
   "the phone has no audio".

## Risks, dependencies and interactions (for the reviewer)

* **Enabling the DP audio link couples the whole card to the DP controller.**
  With `status = "okay"`, `snd_soc_add_pcm_runtime()` returns `-EPROBE_DEFER`
  if `mdss_dp`'s `hdmi-codec` component is not registered, the card bind is
  torn down and `snd-sm8150` defers: no speakers, mic or headset.  The shipped
  default avoids this with `status = "disabled"`; do not flip it in a release
  build (claims C28/C29).
* **0048 depends on the link being a DPCM back end.** `sm8150_add_be_ops()`
  installs the `prepare`/`hw_params` hooks only for `link->no_pcm == 1`, and
  `qcom_snd_parse_of()` sets that only when the DT link has both a `platform`
  and a `codec` child.  `dts/0002` has both, so the hook runs; if someone later
  writes the DP link without `platform`, 0048 silently becomes dead code.
* **Repeated codec prepare and the audio-start completion.** Proven safe in
  the normal sequence (C30/C31), with one bounded exception: an unplug racing
  the microseconds between the link prepare and the core's codec prepare would
  extend the DP disable path's existing wait to its 5 s timeout.  Bounded,
  logged, self-clearing; not corruption.  Marked in the patch and here rather
  than called "harmless".
* **Array bounds.** `DISPLAY_PORT_RX` is 104
  (`include/dt-bindings/sound/qcom,q6dsp-lpass-ports.h:109`) and the machine
  driver's per-port arrays are `AFE_PORT_MAX` = 137
  (`sound/soc/qcom/qdsp6/q6afe.h:6`, used in `sm8150.c:30` and `sm8150.c:33`), so
  `stream_prepared[104]` / `sruntime[104]` are in range.
* **Error path.** With 0048, a DP playback attempt while no display is trained
  now fails in link `prepare` with `-EINVAL` (from `msm_dp_audio_prepare`)
  instead of later; same error, earlier and clearer.
* **0050 ordering.** The two DP index parameters are sent *before* the port
  configuration, matching downstream's sequence (display-stream params, then
  config, then start); only the "before the start" boundary is sourced
  (C32).  A rejection only warns, so the worst case is two extra APR
  round-trips and a `dev_warn`.  The APR response handler treats any
  `SET_PARAM_V2` response generically (`q6afe.c:975-990`), so extra parameters
  cannot corrupt port state.  If the DSP required them *after* the config, the
  warning text is the signal.
* **0050 diverges from downstream on errors.** Downstream aborts the prepare
  for any error other than `-EOPNOTSUPP`; this patch warns and continues for
  every error.  Deliberate (the experiment wants the port-start result even if
  the parameters are rejected), documented in the patch, with the one-line
  change needed to match downstream.
* **0050 cannot affect video or other audio**: it is inside
  `if (port_id == AFE_PORT_ID_HDMI_OVER_DP_RX)` in `q6afe_port_start()`.
* **TFA9874 risk** (from the input docs, still worth watching): the FSA4480
  joins the speaker amp bus.  rc3 already has geni-FIFO mode (`0006`), so the
  main risk is probe-time interference; the video test list checks the
  speakers first.
* **GPIO 100 polarity** is the one hardware detail taken on faith from the
  stock tree.  Failure mode is explicit: `fsa4480: FSA4480 not found` in
  `dmesg`, and DP AUX/EDID will fail while video may still come up on some
  docks.
* **No suspend/resume interaction is claimed.** The user-reported lock-up on
  the current local build is being handled by the `suspend-fixes` topic;
  nothing here changes PM paths, and DP being enabled adds a runtime-PM device
  that is absent when `status = "disabled"`.  That is a plausible new variable
  for the suspend topic, not an established cause of anything.

## Test plan when the parent goes to hardware

1. Boot with nothing plugged in.  `dmesg | grep -iE 'fsa4480|ae90000|typec'`:
   expect the FSA4480 to probe (Vendor ID line) and the DP controller to bind.
   Speakers must still play (same I2C bus).
2. Plug a USB-C→DP/HDMI adapter with a monitor:
   `grep . /sys/class/typec/port0-partner/port0-partner.*/svid` → `ff01`;
   `active` → `yes`; `lsmod | grep typec_displayport`;
   `cat /sys/class/drm/card*-DP-1/status` → `connected`; `modetest -M msm -c`
   shows the monitor's modes (EDID over the FSA4480 AUX path).
3. Enable the external output at **60 Hz** (not 120: two lanes; unverified
   claim, but the cheap thing to do).
4. Flip the plug, repeat (FSA4480 orientation switch).
5. Unplug: connector `disconnected`, USB gadget SSH returns on a PC connection.
6. Charging through a powered dock: expect the existing 5 V/3 A sink contract.
7. Audio regression check on the **default** build (link disabled): speakers,
   earpiece/headset and mic must behave exactly as on rc3, and
   `/proc/asound/cards` must list the same card as before.  This is the check
   that proves the disabled link did not disturb card registration.
8. Only then the DP audio experiment, in a separate build: flip `status` to
   `"okay"` (see "Default integration vs optional experiment"), rebuild and
   reboot.  Expected first failure is `AFE config … -110` or
   `AFE start … -110`; the split message tells which.  Keep 0050 in place for
   the first attempt and drop it for the second, so the logs separate
   "firmware needs the indices" from "firmware rejects them".

Rollback: remove `0001`/`0002` and `0046…0050` from `source=`, `pmbootstrap
checksum`, rebuild.  For the experiment alone, revert the single
`status = "okay"` line.  The DT is additive and confined to the new dtsi plus
one `#include` line in the board delta, so removing the patches cannot leave a
half-configured DP controller behind.

## How to re-run the checks

```sh
KERNEL=bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150
# 1. scratch tree + zero-fuzz apply (see checks/RESULTS.md, "Apply sequence, zero fuzz")
# 2. source/DT assertions
python3 checks/check_displayport.py --tree .verify/tree --kernel "$KERNEL"
# 3. devicetree compile (kernel dtc built into /tmp; see RESULTS, "Devicetree compile")
# 4. compile check
KERNEL=$KERNEL PATCHED=$PWD/.verify/tree OBJ=/tmp/dpbuild/kbuild checks/compile_check.sh
```

`checks/RESULTS.md` records every command with its actual output, including the
environment quirks (no `bc`, no system `dtc`, `-fms-extensions`).  The check
script also guards the assumptions that make the default safe: the disabled
`dp-dai-link`, the `-EPROBE_DEFER` behaviour in `snd_soc_add_pcm_runtime()`, the
available-child parsing in `qcom_snd_parse_of()`, and the absence of rollback
marks in `snd_soc_dai_prepare()`.

## What is not claimed

* DP video has **not** been booted.  It is devicetree-only, compiles, and
  mirrors an upstream board, nothing more.
* DP audio does **not** work as far as anyone knows, including after these
  patches.  0048 is a hypothesis test, 0049 is diagnostics, 0050 is an
  experiment, 0046 is cleanup — and in the default build none of them is even
  reachable, because the link ships disabled.
* No claim is made about 2560×1440@120, about DP audio firmware contents, about
  suspend/resume behaviour with DP enabled, or about docks that require more
  than two lanes.
