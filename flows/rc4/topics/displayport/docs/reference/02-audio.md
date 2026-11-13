# 02: DisplayPort audio

**Files:**
- `dts/0002-dt-displayport-audio-link.diff`
- `kernel/0029`–`0033`

**Status:** unsolved anywhere. These patches give it the best shot found from the sources, and they make the result readable if it still fails.

**Prerequisite:** DP video working (`01-video.md`).

## The failure (HD, OnePlus 7T Pro, mainline 6.16)

HD added the DP dai-link, the machine-driver DAI ids and a DAPM fix. ALSA then exposes the path, the card lists `hdmi-audio-codec.0.auto`, and playback fails:

```
qcom-q6afe: AFE enable for port 0x6020 failed -110
q6afe-dai: ASoC error (-110): at snd_soc_dai_prepare() on DISPLAY_PORT_RX_0
```

`-110` means the DSP never answered (port 0x6020 = `AFE_PORT_ID_HDMI_OVER_DP_RX`). HD's leading hypothesis is that the ADSP firmware lacks DP audio because "the OnePlus 7T Pro never shipped DisplayPort output". HD labels this a hypothesis, not a conclusion ([HD evidence, "DisplayPort audio: everything binds, the DSP refuses the port"](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/docs/evidence/2026-08-07-mainline616-usb-role.md)).

## What this investigation found

### 1. The prepare order is reversed relative to the stock kernel (most likely cause)

**Mainline (6.17 and 7.0):**
- `snd_soc_pcm_dai_prepare()` (`sound/soc/soc-dai.c`) walks `rtd->dais` with `for_each_rtd_dais`, and that array holds the **CPU DAIs first, then the codec DAIs** (`snd_soc_rtd_to_codec(rtd, n) = rtd->dais[n + num_cpus]` in `include/sound/soc.h`).
- On the DP link, the CPU DAI is q6afe. `q6afe_dai_prepare()` calls `q6afe_port_start()`, which sends the port config and then `AFE_PORT_CMD_DEVICE_START`.
- The codec DAI is the DP controller's `hdmi-codec`. Its `prepare` is the **only** hook that reaches `msm_dp_audio_prepare()` (`drivers/gpu/drm/msm/dp/dp_audio.c`), and that function is what programs SDP and ACR and sets `MMSS_DP_AUDIO_CFG` bit 0 (audio engine on).
- `drm_bridge_connector`'s hdmi audio funcs have `.startup`, `.prepare` and `.shutdown`, but **no `.hw_params`** (6.17 `drm_bridge_connector.c` L551–L555; same in 7.0). So nothing turns DP audio on earlier.

So the DSP is told to start the DP audio interface while the DP side of it is still off.

**Stock (LOS 4.14):**
- `soc_pcm_prepare()` prepares the **codec DAIs before the CPU DAI**.
- The msm-ext-disp codec sets up DP audio first, and then `msm_dai_q6_hdmi_prepare()` (`techpack/audio/asoc/msm-dai-q6-hdmi-v2.c`) starts the AFE port.

A DSP start that blocks on hardware fits "no answer at all", rather than a quick error reply.

**Fix: 0031.** `sm8150_snd_prepare()` is the link's `.prepare` and runs before either DAI. For `DISPLAY_PORT_RX` it now calls `snd_soc_dai_prepare()` (exported) on the codec DAI first. The core prepares the codec again after the CPU DAI. `msm_dp_audio_prepare()` only rewrites the same registers and calls `reinit_completion()` (6.17 `dp_display.c` L266–L273), so the second call is harmless.

### 2. The stock kernel sends two DP parameters before every start (secondary)

`msm_dai_q6_hdmi_prepare()` calls `afe_set_display_stream(port, stream_idx, ctl_idx)` (LOS `techpack/audio/dsp/q6afe.c` L6766–L6834) before `afe_port_start()`. That sends two parameters, each `{ minor_version = 1, idx }`:
- `AFE_PARAM_ID_HDMI_DP_MST_VID_IDX_CFG` (0x102b5)
- `AFE_PARAM_ID_HDMI_DPTX_IDX_CFG` (0x102b6)

If the firmware answers `-EOPNOTSUPP`, stock carries on. Mainline never sends them.

**Fix: 0033 (experimental).** It sends both with index 0 for port 0x6020, warns on any error, and carries on, as stock does.

### 3. The same log line for two different failures (diagnostic)

`q6afe_port_start()` prints "AFE enable for port 0x%x failed" both when the **config** set-param fails and when **DEVICE_START** fails. HD's log can't say which one the DSP ignored.

**Fix: 0032.** It prints "AFE config …" and "AFE start …" instead.
- A config timeout points at the DSP not knowing the port (firmware).
- A start timeout points at hardware, meaning the ordering in section 1.

### 4. The firmware hypothesis is weaker for guacamole than it looked

The stock guacamole device tree (LOS `arch/arm64/boot/dts/18821/`) fully wires DP:
- `sde_dp` with `qcom,ext-disp`, `qcom,dp-aux-switch = <&fsa4480>` and `qcom,dp-usbpd-detection` (`sm8150-sde-display.dtsi` L558–L580)
- the ext-disp audio codec (`ext_disp_audio_codec`, same file)
- `qcom,ext-disp-audio-rx = <1>` on the sound card (`sm8150-audio-overlay.dtsi` L19, L63)
- OnePlus's own guacamole file `guacamole_t0.dtsi` L45–L53 tunes the `sde_dp` PHY supply

That is not proof the ADSP image has DP audio. HD rightly warns that some stock audio config is unedited Qualcomm boilerplate. But it is the board's kernel DT, not a mixer file. Rule out 1 and 2 before blaming the firmware.

### 5. Already fixed by HD, carried here

- **0029** (HD 0052): `DISPLAY_PORT_RX` was declared `SND_SOC_DAPM_AIF_OUT`, and every other RX port is `AIF_IN`. It's still `AIF_OUT` in 6.17 and 7.0, and it's upstreamable. HD's own log shows `-110` remaining after it, so it isn't the cure.
- **0030** (HD 0051, hand-rebased onto GN's `sm8150.c`): `DISPLAY_PORT_RX` is added to the three machine-driver switches, so the logs stop saying `invalid dai id 0x68`.
- **DT 0002** (HD 0050): `dp-dai-link` with `cpu = <&q6afedai 104>` (`DISPLAY_PORT_RX`), `platform = <&q6routing>` and `codec = <&mdss_dp>`. The DP controller already has `#sound-dai-cells = <0>`, and `CONFIG_SND_SOC_HDMI_CODEC=m` is set in GN.

### Minor differences noted, not patched

Mainline sends the HDMI config with `sizeof(port->port_cfg)` (the whole union), while stock sends `sizeof(afe_param_id_hdmi_multi_chan_audio_cfg)` (16 bytes). Mainline does this for every port type, and the HDMI_RX port works on other boards (APQ8096). So this is unlikely to be the cause. If 0031–0033 don't help, try sending the exact size for HDMI/DP ports.

## Test ladder

Plug in a monitor with speakers (or HDMI audio through an adapter). Video must already work.

```sh
# route a free front end (MultiMedia3 = PCM 2; MM1/MM2 are the speaker/mic UCM paths)
amixer -c0 cset name='DISPLAY_PORT_RX Audio Mixer MultiMedia3' 1
speaker-test -D hw:0,2 -c 2 -r 48000 -F S16_LE -t sine -l 1
dmesg | tail -30
```

| Result | Meaning | Next |
| --- | --- | --- |
| Sound | 0031 (and/or 0033) fixed it | Bisect: revert 0033 and retest, so only what's needed is kept. Add a DP/HDMI device to the UCM (`HiFi.conf`). Upstream 0029, 0031 and 0032. |
| `AFE start … -110` | The DSP accepts the config but not the start | Check `dmesg` for the `DP stream/controller index` warning (0033). Read `MMSS_DP_AUDIO_CFG` with `devmem` while blocked to confirm the engine is on. Try 48 kHz/16-bit/2 ch only. |
| `AFE config … -110` | The DSP doesn't answer for the port at all | Firmware is now the leading suspect: `strings` the ADSP image (`adsp.mdt`/`.b*` from GN's firmware package) for `hdmi`/`dp`/`0x6020`, and compare with an SM8150 device whose DP audio works under stock. |
| `DP stream/controller index … -95/-22` only | The firmware rejects the MST params | That's expected on firmware without MST, and harmless. Keep or drop 0033 based on the other results. |
| `prepare` fails `-EINVAL` from msm DP | DP not powered (`!power_on`) | The monitor is not connected or trained; fix video first. |

## Sources

- HD:
  - [`0050-arm64-dts-hotdog-add-displayport-audio-link.patch`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0050-arm64-dts-hotdog-add-displayport-audio-link.patch)
  - [`0051-asoc-sm8150-accept-the-displayport-backend.patch`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0051-asoc-sm8150-accept-the-displayport-backend.patch)
  - [`0052-asoc-q6afe-dai-make-displayport-a-playback-widget.patch`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0052-asoc-q6afe-dai-make-displayport-a-playback-widget.patch)
  - [evidence log](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/docs/evidence/2026-08-07-mainline616-usb-role.md)
- LOS ([LineageOS/android_kernel_oneplus_sm8150](https://github.com/LineageOS/android_kernel_oneplus_sm8150) `lineage-23.2`):
  - `techpack/audio/asoc/msm-dai-q6-hdmi-v2.c` (`msm_dai_q6_hdmi_prepare`, `msm_dai_q6_hdmi_hw_params`)
  - `techpack/audio/dsp/q6afe.c` (`afe_set_display_stream` L6766)
  - `techpack/audio/include/dsp/apr_audio-v2.h` (L1382, L1584, L2125–L2133, L3051–L3053)
  - `arch/arm64/boot/dts/18821/*` as cited above
- Mainline, read at `v6.17.0-sm8150` and `v7.0.0-sm8150`:
  - `sound/soc/soc-dai.c` (`snd_soc_pcm_dai_prepare`, exported `snd_soc_dai_prepare`)
  - `include/sound/soc.h` (`for_each_rtd_dais`, `snd_soc_rtd_to_codec`)
  - `sound/soc/qcom/qdsp6/q6afe.c` (`q6afe_port_start`)
  - `sound/soc/qcom/qdsp6/q6afe-dai.c` (`q6afe_dai_prepare`, `q6hdmi_ops`)
  - `drivers/gpu/drm/msm/dp/dp_audio.c` (`msm_dp_audio_prepare`)
  - `drivers/gpu/drm/display/drm_bridge_connector.c` (hdmi audio funcs)
  - `include/dt-bindings/sound/qcom,q6dsp-lpass-ports.h` (`DISPLAY_PORT_RX` = 104)

## Upstream

- 0029 and 0032 are generic and upstreamable as they are.
- If 0031 is confirmed, the proper upstream fix is generic rather than in a machine driver: either have `hdmi-codec`/`drm_bridge_connector` enable DP audio in `hw_params`, or let a q6afe DP link request codec-first prepare.
