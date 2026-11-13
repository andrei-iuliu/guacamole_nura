# Downstream source evidence for the DP audio patches (0048, 0050)

Read-only retrieval, 2026-11-13, from the LineageOS kernel tree for this
device: `LineageOS/android_kernel_oneplus_sm8150`, branch `lineage-23.2`.  This
is a **downstream Lineage reference, not proven to be the factory build** the
phone shipped with — no stock image or vendor tag was compared.  Nothing here
was modified; the files were fetched into `/tmp` and only quoted.

```
repo   LineageOS/android_kernel_oneplus_sm8150
branch lineage-23.2
base   https://raw.githubusercontent.com/LineageOS/android_kernel_oneplus_sm8150/lineage-23.2/
```

| file | sha256 of the fetched copy |
| --- | --- |
| `techpack/audio/include/dsp/apr_audio-v2.h` | `f1c072bda53629d4d0214bc960fa90e838731fc073ce6436e5c0baee4c87d23f` |
| `techpack/audio/dsp/q6afe.c` | `17b818d47c997a7244d6916f57374ed760a428e5e59570a0cf4c72c5877da470` |
| `techpack/audio/asoc/msm-dai-q6-hdmi-v2.c` | `303a5d773603fb8d832db4544e8163d95261c144987655657a912a1582893792` |
| `sound/soc/soc-pcm.c` | `ecb540d18127a28020ea7f5f18c110adb0ec5f5dff735ad5bd64b8adb0d4a22e` |

## 1. Parameter ids (`apr_audio-v2.h`, lines 3050-3053)

```c
/* This param id is used to configure HDMI interface */
#define AFE_PARAM_ID_HDMI_CONFIG                 0x00010210
#define AFE_PARAM_ID_HDMI_DP_MST_VID_IDX_CFG     0x000102b5
#define AFE_PARAM_ID_HDMI_DPTX_IDX_CFG           0x000102b6
```

## 2. Payload shapes (`apr_audio-v2.h`, lines 2125-2133)

```c
struct afe_display_stream_idx {
	u32                  minor_version;
	u32                  stream_idx;
} __packed;

struct afe_display_ctl_idx {
	u32                  minor_version;
	u32                  ctl_idx;
} __packed;
```

Each struct is **8 bytes**: two u32 words, `minor_version` first and the index
second.  Both parameters together are 16 payload bytes before their parameter
headers (`param_hdr_v3` is 16 bytes per parameter, packed by
`q6common_pack_pp_params()`).  Downstream sets `minor_version = 1` before
packing them (`q6afe.c`, below).  The mainline patch sends one parameter per
`SET_PARAM_V2` and passes `sizeof(idx)` = 8, so the wire payload matches:
it must not be padded to 16 per command.

## 3. What downstream sends (`q6afe.c`, `afe_set_display_stream()`, lines 6758-6834)

```c
int afe_set_display_stream(u16 rx_port_id, u32 stream_idx, u32 ctl_idx)
{
	...
	param_hdr.module_id = AFE_MODULE_AUDIO_DEV_INTERFACE;
	param_hdr.instance_id = INSTANCE_ID_0;
	param_hdr.param_id = AFE_PARAM_ID_HDMI_DP_MST_VID_IDX_CFG;
	param_hdr.param_size = sizeof(struct afe_display_stream_idx);
	stream_data.minor_version = 1;
	stream_data.stream_idx = stream_idx;
	ret = q6common_pack_pp_params(packed_param_data, &param_hdr,
				      (u8 *) &stream_data, &single_param_size);
	...
	param_hdr.param_id = AFE_PARAM_ID_HDMI_DPTX_IDX_CFG;
	param_hdr.param_size = sizeof(struct afe_display_ctl_idx);
	ctl_data.minor_version = 1;
	ctl_data.ctl_idx = ctl_idx;
	...
	ret = q6afe_set_params(rx_port_id, q6audio_get_port_index(rx_port_id),
			       NULL, packed_param_data, packed_param_size);
```

Notes for the mainline port: both parameters are packed into **one** SET_PARAM
here, while the mainline port (patch 0050) sends two separate `SET_PARAM_V2`
commands through `q6afe_port_set_param_v2()`.  The per-parameter payload is the
same; whether firmware cares about the packaging is an open question recorded
in the patch message.

## 4. When downstream sends them (`msm-dai-q6-hdmi-v2.c`, lines 331-361)

```c
static int msm_dai_q6_hdmi_prepare(struct snd_pcm_substream *substream,
		struct snd_soc_dai *dai)
{
	...
	if (!test_bit(STATUS_PORT_STARTED, dai_data->status_mask)) {

		rc = afe_set_display_stream(dai->id, dai_data->stream_idx,
						dai_data->ctl_idx);
		if (rc < 0) {
			dev_err(dai->dev, "fail to set AFE ctl, stream ID params %x\n",
				dai->id);
			if (rc != -EOPNOTSUPP) {
				dev_err(dai->dev, "not starting AFE port\n");
				goto err;
			}
		}

		rc = afe_port_start(dai->id, &dai_data->port_config,
				    dai_data->rate);
		...
	}
err:
	return rc;
}
```

So: the display-stream parameters go out *before* the port start, only while
the port is not already started.  On failure downstream continues only for
`-EOPNOTSUPP`; any other error aborts the prepare.  Patch 0050 deliberately
diverges here (warn and continue for every error) and says so.

`stream_idx` and `ctl_idx` come from `msm_dai_q6_hdmi_dai_data`, which is
`kzalloc`ed in the DAI probe, so their default is 0; the "Display Port RX
DEVICE IDX" control (`msm_dai_q6_ext_disp_device_idx_put`) can change them at
runtime.  Patch 0050 therefore sends index 0, matching the downstream default.

## 5. Prepare ordering for patch 0048 (`soc-pcm.c`, lines 775-830)

```c
static int soc_pcm_prepare(struct snd_pcm_substream *substream)
{
	...
	if (rtd->dai_link->ops && rtd->dai_link->ops->prepare) { ... }
	if (platform->driver->ops && platform->driver->ops->prepare) { ... }

	for (i = 0; i < rtd->num_codecs; i++) {
		codec_dai = rtd->codec_dais[i];
		if (codec_dai->driver->ops && codec_dai->driver->ops->prepare) {
			ret = codec_dai->driver->ops->prepare(substream,
							      codec_dai);
			...
		}
	}

	if (cpu_dai->driver->ops && cpu_dai->driver->ops->prepare) {
		ret = cpu_dai->driver->ops->prepare(substream, cpu_dai);
		...
	}
```

This is the downstream ordering patch 0048 restores: machine link prepare,
then **all codec DAIs**, then the **CPU DAI**.  The mainline 7.0 equivalent
(`snd_soc_pcm_dai_prepare()` walking `rtd->dais`, CPUs first) does the
opposite, which is the whole premise of 0048.  Attribution caveat: this is the
LineageOS 4.14 reference tree, not a proven factory build, so it establishes
what downstream *reference* code does, not what the shipped image executed.

## 6. Still not verified

* Whether the guacamole ADSP firmware accepts, needs or ignores these
  parameters: no hardware was available.
* Whether one packed SET_PARAM (downstream) and two SET_PARAM_V2 commands
  (mainline) are equivalent from the firmware's point of view.
* Whether the order relative to the port configuration matters; only the
  boundary "before the start" is sourced.
* Whether this LineageOS branch is what the phone actually shipped with: it is
  a downstream reference tree, not compared against a stock image or vendor
tag.
