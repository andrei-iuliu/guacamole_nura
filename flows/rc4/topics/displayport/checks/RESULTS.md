# Verification results (displayport, rc4)

Everything below was run on the integration host, rootless, with the baseline
kernel tree `bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150`
mounted **read-only** by convention: no file in it was written, and no phone,
sudo, mount, pmbootstrap or network access was involved.

Baseline definition used for every test:

| item | value |
| --- | --- |
| kernel package | `device/testing/rc3/linux-postmarketos-qcom-sm8150` (7.0.0, pkgrel 4, patches 0001–0041) |
| kernel source | the rc3-prepared tree above (`apply-rc3-final.log`, 0001–0041 applied) |
| config | `device/testing/rc3/.../config-postmarketos-qcom-sm8150.aarch64` |

Summary:

| check | result |
| --- | --- |
| apply sequence, `patch --fuzz=0` | 7/7 patches clean, 0 rejects, 0 fuzz |
| apply sequence, `git apply --check` | 7/7 clean, byte-identical result |
| patched files identical to the reviewed "after" trees | yes (5/5 files, byte compare) |
| source-level checks (`checks/check_displayport.py`) | 95/95 |
| `clang -fsyntax-only` on the 3 touched translation units | 0 errors (patched and unpatched control) |
| devicetree compile (kernel `dtc`, built from `scripts/dtc`) | exit 0, 0 errors, 115698 bytes, no new warnings vs baseline |
| DP audio link state in the shipped DT | `status = "disabled"`, excluded from the card (11 available links = rc3 set) |
| downstream protocol facts for patch 0050 | verified against LineageOS `lineage-23.2` (read-only fetch, §7) |
| `scripts/checkpatch.pl` | 0 errors, 7 warnings (6 commit-message line length, 1 MAINTAINERS for a new file) |
| on-hardware behaviour | **not tested** — no phone access in this task |

## 1. Apply sequence, zero fuzz

Scratch tree = the four rc3 files the series touches, copied into
`.verify/tree`, then:

```sh
cd .verify/tree
for p in ../../patches/0046-*.patch ../../patches/0047-*.patch \
         ../../patches/0048-*.patch ../../patches/0049-*.patch \
         ../../patches/0050-*.patch ../../dts/0001-*.patch ../../dts/0002-*.patch; do
    patch -p1 --fuzz=0 --no-backup-if-mismatch -i "$p"
done
```

```
0046-asoc-q6afe-dai-displayport-rx-input-widget.patch: patching file sound/soc/qcom/qdsp6/q6afe-dai.c
0047-asoc-sm8150-accept-displayport-backend.patch: patching file sound/soc/qcom/sm8150.c
0048-asoc-sm8150-prepare-dp-codec-dai-before-afe-start.patch: patching file sound/soc/qcom/sm8150.c
0049-asoc-qdsp6-q6afe-split-port-config-and-start-errors.patch: patching file sound/soc/qcom/qdsp6/q6afe.c
0050-asoc-qdsp6-q6afe-send-dp-stream-and-controller-index.patch: patching file sound/soc/qcom/qdsp6/q6afe.c
0001-arm64-dts-guacamole-displayport-video.patch: patching file arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-displayport.dtsi
                                                patching file arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-nura.dtsi
0002-arm64-dts-guacamole-displayport-audio-link.patch: patching file arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-displayport.dtsi
```

`find . -name '*.rej' -o -name '*.orig' | wc -l` → `0`.

The same seven patches also pass `git apply --check` (rc=0 each) against a
second scratch copy, and applying them with `git apply` produces files that are
byte-identical to the `patch -p1` result (`cmp` on all five files), so the
series works with either tool the parent's packaging step may use.

Cross-check that the rebase did not drift: the patched files were compared
byte-for-byte with the trees the patches were generated from
(`cmp`) → `q6afe-dai.c OK`, `q6afe.c OK`, `sm8150.c OK`,
`sm8150-oneplus-guacamole-displayport.dtsi OK`,
`sm8150-oneplus-guacamole-nura.dtsi OK`.

Blob cross-check of the base file: `git hash-object` on the rc3
`sm8150.c` is `93294d6d4ec4cfff9d514b73b3895e36095b7c38`, which is exactly the
`index …..93294d6d4` recorded in patch `0008`, so the tree really is the rc3
series applied on top of upstream `v7.0.0-sm8150` for that file.

### Supplied patch 0030 on plain 7.0 (reproducing the input README's warning)

```sh
cp $KERNEL/sound/soc/qcom/sm8150.c /tmp/plain70/sound/soc/qcom/sm8150.c
patch -p1 -R --fuzz=0 -i device/testing/rc3/…/0008-asoc-sm8150-add-quaternary-mi2s-speaker-backend.patch
patch -p1 -i  Patches/displayport/kernel/0030-asoc-sm8150-accept-displayport-backend.patch
git apply --check -v Patches/displayport/kernel/0030-…
```

```
Hunk #1 succeeded at 209 (offset -1 lines).
Hunk #2 succeeded at 334 (offset 2 lines).
Hunk #3 FAILED at 363.
1 out of 3 hunks FAILED -- saving rejects to file sound/soc/qcom/sm8150.c.rej
```

and `git apply --check` reports `patch does not apply` for the same hunk.
`case DISPLAY_PORT_RX:` count afterwards: **2** (one per switch), i.e. no
duplicate label is created — the hunk rejects instead.  With the default fuzz
factor 2 the outcome is the same.  The rebased `0047` applies on the real rc3
tree at zero fuzz and yields exactly three labels (hw_params, startup,
shutdown) with no duplicates inside any switch.

## 2. Source-level checks

```sh
KERNEL=$KERNEL PATCHED=.verify/tree python3 checks/check_displayport.py
```

```
95/95 checks passed
```

What it asserts (full list in the script):

* `sm8150.c`: no duplicate `case` label in any `switch (cpu_dai->id)`;
  the three backend hooks (`hw_params`, `startup`, `shutdown`) handle
  `DISPLAY_PORT_RX`; `sm8150_dai_init()` is left alone; the DP branch in
  `sm8150_snd_prepare()` runs before the SoundWire handling, iterates
  `for_each_rtd_codec_dais()`, calls `snd_soc_dai_prepare()` and propagates the
  error; `be_ops` is only installed for DPCM back ends.
* `q6afe-dai.c`: `DISPLAY_PORT_RX` is `SND_SOC_DAPM_AIF_IN` with `NULL`
  stream name, no other `*_RX` port is `AIF_OUT`, playback route unchanged.
* `q6afe.c`: both DP parameter ids defined once; the payload struct is exactly
  two u32 words (`minor_version`, `idx`) `__packed`, i.e. **8 bytes**, with a
  `static_assert(sizeof(...) == 8)` so it cannot drift, and the SET_PARAMs pass
  `sizeof(idx)` = 8 rather than a padded 16; the two SET_PARAMs are sent before
  the port config and before `DEVICE_START`, a rejection only warns, and no
  ambiguous `AFE enable` message is left (config / TDM slot mapping / set param
  / start are distinct).
* devicetree: `{}`/`()`/`[]` balanced; every `&label` reference resolves
  inside the real include chain of `sm8150-oneplus-guacamole.dts`; the three
  labels the new file defines are not defined anywhere else in that chain; the
  node names `typec-mux@42`, `port@2`, `dp-dai-link`, `altmodes` are new to the
  guacamole board files; the include chain is still intact; `sm8150.dtsi`
  provides `mdss_dp`, `mdss_dp_out`, `usb_1_qmpphy`.
* **default-safety assertions (new)**: `dp-dai-link` exists, carries
  `status = "disabled"`, has no `status = "okay"`, is the only disabled node
  in the file, has the `cpu`/`platform`/`codec` children that make it a DPCM
  back end, points its cpu at `DISPLAY_PORT_RX` and its codec at `&mdss_dp`,
  and the file documents the one-line opt-in.
* **kernel invariants (new, read from the pristine tree)**: a missing codec DAI
  logs `CODEC DAI %s not registered` and returns `-EPROBE_DEFER` from
  `snd_soc_add_pcm_runtime()`; `qcom_snd_parse_of()` still counts and walks
  only *available* children and still sets `no_pcm` for links with platform +
  codec children; `snd_soc_dai_prepare()` neither pushes nor pops any rollback
  mark; `struct drm_connector_hdmi_audio_funcs` has `prepare`, has no
  `hw_params`, and documents prepare as callable repeatedly; exactly one waiter
  on `dp->audio_comp` and it is bounded by `HZ * 5`; audio shutdown completes
  the completion.

## 3. Compile check of the touched code

The host has no `dtc` and no cross toolchain matching the pmOS build, so this
is a syntax/type check, not a kernel build:

```sh
make -C $KERNEL O=/tmp/dpbuild/kbuild ARCH=arm64 LLVM=1 olddefconfig   # rc3 config
make -C $KERNEL O=/tmp/dpbuild/kbuild ARCH=arm64 LLVM=1 prepare        # into /tmp only
KERNEL=$KERNEL PATCHED=$PWD/.verify/tree OBJ=/tmp/dpbuild/kbuild checks/compile_check.sh
```

```
=== sound/soc/qcom/sm8150.c
    ok (no diagnostics of severity error)
=== sound/soc/qcom/qdsp6/q6afe.c
    ok (no diagnostics of severity error)
=== sound/soc/qcom/qdsp6/q6afe-dai.c
    ok (no diagnostics of severity error)
```

Control run with `PATCHED=$KERNEL` (unpatched rc3 files) is also clean, so the
harness exercises the same include/generated header set the real build uses.
The warnings that do appear (2–3 per file) are pre-existing:
`-Wmicrosoft-anon-tag` from `include/linux/fs.h:2429`,
`-Wpointer-sign` from `include/sound/control.h:164`, and
`-Wpointer-sign` at `q6afe-dai.c:1032` (`of_property_read_u32(..., "reg", &id)`
with `int id`), which no patch in this series touches.

Two build-environment notes for anyone reproducing this:

* `bc` is not installed on this host, so `include/generated/timeconst.h`
  cannot be generated by the kernel's own `timeconst.bc`.  A two-line stub
  (documented, in `/tmp` only) was used; nothing in the three touched files
  includes it.  A real build (with `bc`) is unaffected.
* The check needs `-fms-extensions` (the kernel Makefile sets it) because
  `include/linux/fs.h` uses an anonymous tagged struct member.

No `.config`-driven link or full kernel build was attempted: that needs
pmbootstrap and would either write into the baseline tree or duplicate it.

## 4. Devicetree compile

No system `dtc` exists on this host, so the kernel's own `dtc` was built from
`$KERNEL/scripts/dtc` into `/tmp/dpbuild` (gcc + host `flex`/`bison`/`libyaml`:
`DTC 1.7.2-ga26ef640`), the patched devicetree was preprocessed exactly like
kbuild does, and then compiled:

```sh
cpp -nostdinc -undef -D__DTS__ -x assembler-with-cpp \
    -I .verify/dts-tree -I $KERNEL/include -I $KERNEL/arch/arm64/boot/dts \
    -o /tmp/dpbuild/guacamole.dts .verify/dts-tree/sm8150-oneplus-guacamole.dts
/tmp/dpbuild/dtc -I dts -O dtb -o /tmp/dpbuild/guacamole.dtb /tmp/dpbuild/guacamole.dts
```

`dtc` exit 0, **0 errors**, output 115698 bytes with the disabled DP link
(115674 before the `status = "disabled"` line and its comment), reproducible.
Warnings: 25×`unique_unit_address`, 2×`avoid_unnecessary_addr_size` — exactly
the same classes and counts as the unpatched baseline compile
(`2 Warning (avoid_unnecessary_addr_size)`, `25 Warning (unique_unit_address)`),
i.e. the series adds none.  The only difference in the log text is one extra
"also defined at …/sm8150-oneplus-guacamole-displayport.dtsi:32.7-52.3" note
attached to the pre-existing `i2c@890000` warning, because the FSA4480 adds
children there.

Card-impact measurement on that blob (`dtc -I dtb -O dts`, then walk the card
node): the sound node has 12 direct children — `mm1`…`mm6`, `speaker`,
`slim`, `slimcap`, `slim2`, `slimcap2` (all without a status property) and
`dp-dai-link` (`status = "disabled"`).  `of_get_available_child_count()`
therefore returns 11, exactly the rc3 link set, so the card binds identically.

Structural check of the compiled blob (`dtc -I dtb -O dts` dump):

| item | resolved value |
| --- | --- |
| `displayport-controller@ae90000` | `status = "okay"`, `#sound-dai-cells = <0>` |
| `mdss_dp` port@1 endpoint | `data-lanes = <0 1>`, remote = `usb_1_qmpphy_dp_in` |
| `typec-mux@42` | `compatible = "fcs,fsa4480"`, `reg = <0x42>`, pinctrl-0 → `fsa-usbc-ana-en-state` |
| FSA4480 endpoint | remote-endpoint → `spmi@c440000/pmic@2/typec@1500/connector/ports/port@2/endpoint` |
| connector port@2 | remote-endpoint → `geniqup@8c0000/i2c@890000/typec-mux@42/port/endpoint` |
| connector `altmodes/displayport` | `svid = [ff 01]`, `vdo = <0x1c46>` |
| `dp-dai-link` cpu | `sound-dai = <… 0x68>` → q6afe dai node, arg 104 = `DISPLAY_PORT_RX` |
| `dp-dai-link` platform | `sound-dai` → `apr-service@8/routing` (`q6routing`) |
| `dp-dai-link` codec | `sound-dai` → `displayport-controller@ae90000` (`mdss_dp`) |
| `dp-dai-link` status | `"disabled"` → not parsed, not a card link |

`dtc` does not validate against the YAML bindings; `dtbs_check` was not run
(no `dtschema`/network).  The node set was instead compared by hand with the
in-tree `sm8150-hdk.dts`, which uses the same SoC, the same PMIC Type-C
controller, the same `altmodes` VDO and the same `port@2` SBU wiring.

## 5. checkpatch

```sh
for p in patches/*.patch dts/*.patch; do
    perl $KERNEL/scripts/checkpatch.pl --no-tree --terse --no-signoff "$p"
done      # raw output: .verify/logs/checkpatch.log
```

```
0 errors in all 7 patches
warnings: 6× "Prefer a maximum 75 chars per line (possible unwrapped commit description?)"
          1× "added, moved or deleted file(s), does MAINTAINERS need updating?"  (dts/0001)
```

The line-length warnings are in commit-message prose (the code hunks are
clean); the MAINTAINERS warning is expected for a new board dtsi and is
irrelevant to this local release.  The one error checkpatch initially found
(a bare `commit <sha>` reference in `0046`) was fixed; no TODO author
placeholder remains.

## 6. Card-defer trace (why the DP audio link ships disabled)

Read from the rc3 7.0 sources, no writes:

| step | source | what it shows |
| --- | --- | --- |
| 1 | `sound/soc/soc-core.c:1205-1266` (`snd_soc_add_pcm_runtime`) | for each codec, `snd_soc_find_dai()` NULL → `dev_info("ASoC: CODEC DAI %s not registered")` → `_err_defer:` → `snd_soc_remove_pcm_runtime()` → `return -EPROBE_DEFER` |
| 2 | `sound/soc/soc-core.c:2167-2187` (`snd_soc_bind_card`) | `snd_soc_add_pcm_runtimes()` error → `probe_end` → `soc_cleanup_card_resources()` → returns the error |
| 3 | `sound/soc/soc-devres.c:65-84` (`devm_snd_soc_register_card`) | returns that error to the machine driver, i.e. `snd-sm8150`'s probe returns `-EPROBE_DEFER` |
| 4 | `sound/soc/soc-core.c:830-840` (`soc_component_to_node`) | a component without its own `of_node` matches through `dev->parent->of_node` — this is how `sound-dai = <&mdss_dp>` finds the hdmi-codec child of the DP device |
| 5 | `sound/soc/qcom/common.c:75,85` | `qcom_snd_parse_of()` sizes links with `of_get_available_child_count()` and walks `for_each_available_child_of_node()` |
| 6 | `drivers/of/base.c:523-532` (`__of_device_is_available`) and `:762-767` (`of_get_next_available_child` → `of_get_next_status_child(node, prev, __of_device_is_available)`) | only absent status / `okay` / `ok` is available; `disabled` is skipped by the iterator the parser uses |

Net effect: with the link enabled, the whole "OnePlus 7 Pro" card depends on
`mdss_dp` probing; if it does not, there is no sound card at all.  With
`status = "disabled"` the link is not parsed (step 5/6) and `card->num_links`
is the rc3 value (measured: 11 links in §4).  Both facts are asserted by
`checks/check_displayport.py` ("kernel invariants", "default-safety").

Caveat recorded honestly: no other in-tree qcom board disables a `*-dai-link`
child, so the disabled-node approach rests on the parser code above, not on
precedent.  The alternative with no reliance at all is to omit `dts/0002`
entirely (README, "Variant if the parent prefers zero DP-audio code").

## 7. 0048 audit: DAI bookkeeping and repeated codec prepare

| question | evidence | verdict |
| --- | --- | --- |
| Does `snd_soc_dai_prepare()` do bookkeeping? | `sound/soc/soc-dai.c:354-368`: validity check, `ops->prepare`, `soc_dai_ret()` only | no |
| Can it unbalance startup/shutdown? | rollback marks are pushed/popped only for `hw_params` (`:417,434`), `startup` (`:451,471`), `trigger` (`:637,653`) and compress startup (`:699,717`) | no |
| May a codec's prepare run twice per setup? | `include/drm/drm_connector.h:1166-1176`: "Can be called multiple times for each setup. Mandatory." | yes, by contract |
| Does the codec prepare keep state? | `sound/soc/codecs/hdmi-codec.c:607-641` recomputes IEC958 params from the runtime each call | no |
| Is `msm_dp_audio_prepare()` re-entrant/idempotent? | `dp_audio.c:268-308`: SDP/ACR/exit-level programming, `MMSS_DP_AUDIO_CFG` bit 0 via read-modify-write (`:237-249`), `signal_audio_start()`, `audio_enabled = true` | yes for registers/state |
| What is not idempotent? | `msm_dp_display_signal_audio_start()` does `reinit_completion(&dp->audio_comp)` (`dp_display.c:275-282`) | the completion re-arm |
| Who waits / completes it? | exactly one waiter: `msm_dp_display_disable()`, `wait_for_completion_timeout(&dp->audio_comp, HZ * 5)` + `DRM_ERROR("audio comp timeout")` (`dp_display.c:890-905`); completion in `msm_dp_audio_shutdown()` (`dp_audio.c:333-338`) | bounded |
| Can a double re-arm lose a notification? | two re-arms happen back-to-back in one `snd_pcm_prepare()` with no waiter in between; the only possible observer is an unplug racing those microseconds, and it then ends at its existing 5 s timeout instead of an early wake | bounded, logged, self-clearing |
| What cleans up a failed prepare? | `soc_pcm_clean()` → `snd_soc_dai_shutdown()` → `hdmi_codec_shutdown()` → `msm_dp_audio_shutdown()` → clear bit 0 + complete (`soc-pcm.c:757-790`, `hdmi-codec.c:492-509`) | yes, on PCM close |

Verdict: 0048's double prepare is safe in the normal sequence and in the
error-cleanup sequence; the single bounded exception is recorded in the patch
message, in the README risks, and in the check script.  It stays gated behind
the disabled link, so the default build never reaches it.

## 8. Downstream protocol verification for 0050 (read-only fetch)

The four LineageOS `lineage-23.2` files were fetched read-only into `/tmp` on
2026-11-13 and grepped; no repo file was copied from them.  The tree is a
downstream Lineage reference, **not proven to be the factory build** (no stock
image or vendor tag was compared).  Excerpts, line numbers and sha256 of the
fetched copies are in `docs/reference/lineage-23.2-dp-audio-evidence.md`.

Negative control for the wire size: the struct assertion is real, not
cosmetic.  Changing the check to `static_assert(sizeof(...) == 16)` in a copy
of the patched tree makes `checks/compile_check.sh` fail with
`static assertion failed due to requirement 'sizeof(struct
afe_param_id_dp_idx_cfg) == 16'`, which also confirms the struct is 8 bytes
and not 16.

| claim in patch 0050 | evidence |
| --- | --- |
| ids `0x102b5` / `0x102b6` | `apr_audio-v2.h:3052-3053` |
| payload `{u32 minor_version; u32 idx;}`, **8 bytes each** (16 bytes for both, before the per-parameter headers) | `apr_audio-v2.h:2125-2133` (`afe_display_stream_idx`, `afe_display_ctl_idx`); mainline sends `sizeof(idx)` = 8 per command |
| module `AFE_MODULE_AUDIO_DEV_INTERFACE`, instance 0, `minor_version = 1` | `q6afe.c:6789-6826` (`afe_set_display_stream`) |
| sent before the port start, only while stopped | `msm-dai-q6-hdmi-v2.c:341-355` |
| default indices 0 | struct `kzalloc`ed in `msm_dai_q6_hdmi_dai_probe`; settable via "Display Port RX DEVICE IDX" (`:44-45,95-96,229`) |
| downstream tolerance | continues only for `-EOPNOTSUPP`, aborts otherwise (`:348-351`); patch 0050 warns for every error and says so |
| one packed SET_PARAM vs two `SET_PARAM_V2` | downstream packs both into one `q6afe_set_params()` call (`q6afe.c:6827-6829`); mainline sends two commands | **difference, documented, unverified** |
| firmware acceptance on guacamole | nothing available | **unverified** |
| order relative to the port config | downstream order is params → config → start; only the "before start" boundary is sourced | **partially verified** |
| 0048 premise: downstream prepares codecs before the CPU DAI | `sound/soc/soc-pcm.c:775-830`: machine prepare, platform prepare, all codec DAIs, then the CPU DAI | **verified in the reference tree** |

## 9. What was not verified

* No phone, SSH, adb, fastboot, mount, sudo or pmbootstrap was used, so nothing
  here says the DP controller probes, that a monitor trains, or that DP audio
  produces sound.  All hardware behaviour in the docs is a plan, not a result.
* No full kernel build (`pmbootstrap build`) and no `dtbs_check`.
* The DSP firmware's acceptance of the two DP index parameters, and whether one
  packed SET_PARAM and two `SET_PARAM_V2` commands are equivalent to it, remain
  assumptions (§8).
* The fetched LineageOS branch is a downstream reference, not proven to be the
  factory build: no stock image or vendor tag was compared.  It establishes
  what downstream code does (including the `soc_pcm_prepare()` ordering behind
  claim C15), not what the shipped image executed.
