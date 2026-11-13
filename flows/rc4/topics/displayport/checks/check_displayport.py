#!/usr/bin/env python3
"""Rootless regression checks for the rc4 displayport integration.

Nothing here needs root, a kernel build, a phone or the network.  It reads the
patched files produced by applying patches/0046..0050 and dts/0001..0002 to the
rc3 7.0.0-sm8150 sources, plus the pristine rc3 kernel tree (read-only) for the
devicetree label checks.

Usage:
  # apply the series into a scratch tree first (see README "How to re-run the checks")
  python3 checks/check_displayport.py --tree .verify/tree --kernel "$KERNEL"

Exit status is 0 only if every check passes.
"""

import argparse
import os
import re
import sys

FAILURES = []
CHECKS = 0


def check(cond, name, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}" + (f": {detail}" if detail else ""))
        FAILURES.append(name)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- sm8150.c ---

def c_switch_blocks(src):
    """Yield (start_line, labels) for every `switch (cpu_dai->id) { ... }`."""
    lines = src.splitlines()
    i = 0
    while i < len(lines):
        if re.search(r"switch\s*\(cpu_dai->id\)", lines[i]):
            depth = 0
            labels = []
            start = i + 1
            j = i
            while j < len(lines):
                depth += lines[j].count("{") - lines[j].count("}")
                for m in re.finditer(r"^\s*case\s+([^:]+):", lines[j]):
                    labels.append((j + 1, m.group(1).strip()))
                if depth == 0:
                    break
                j += 1
            yield start, labels
            i = j
        i += 1


def check_sm8150(tree):
    print("sm8150.c")
    src = read(os.path.join(tree, "sound/soc/qcom/sm8150.c"))

    dup_found = []
    for start, labels in c_switch_blocks(src):
        seen = {}
        for lineno, label in labels:
            if label in seen:
                dup_found.append((start, label, seen[label], lineno))
            seen[label] = lineno
    check(not dup_found, "no duplicate case label in any cpu_dai->id switch",
          f"{dup_found}")

    blocks = list(c_switch_blocks(src))
    check(len(blocks) == 4,
          "four cpu_dai->id switches (dai_init/hw_params/startup/shutdown)",
          f"found {len(blocks)}")
    for fn in ("sm8150_snd_hw_params", "sm8150_snd_startup",
               "sm8150_snd_shutdown"):
        m = re.search(rf"static (?:int|void) {fn}\(.*?\n\}}", src, re.S)
        check(m is not None, f"{fn}() present")
        names = [line.strip() for line in m.group(0).splitlines()
                 if line.strip().startswith("case ")]
        check(any(n.startswith("case DISPLAY_PORT_RX") for n in names),
              f"{fn}() handles DISPLAY_PORT_RX", f"{names}")

    # the only switch without DP must be dai_init (jack/channel-map setup)
    m = re.search(r"static int sm8150_dai_init\(.*?\n\}", src, re.S)
    check(m is not None and "DISPLAY_PORT_RX" not in m.group(0),
          "dai_init() left alone (no DP-specific channel map)")

    # prepare hook must prepare the codec DAI before returning for DP, and must
    # not touch the SoundWire path for it.
    m = re.search(
        r"static int sm8150_snd_prepare\(.*?\n\}", src, re.S)
    check(m is not None, "sm8150_snd_prepare() present")
    body = m.group(0)
    dp = body.find("cpu_dai->id == DISPLAY_PORT_RX")
    sruntime = body.find("if (!sruntime)")
    check(dp != -1, "prepare() has a DISPLAY_PORT_RX branch")
    check(dp != -1 and sruntime != -1 and dp < sruntime,
          "DP branch runs before the SoundWire stream handling",
          f"dp={dp} sruntime={sruntime}")
    check("snd_soc_dai_prepare(codec_dai, substream)" in body,
          "prepare() calls snd_soc_dai_prepare() on the codec DAI")
    check("for_each_rtd_codec_dais" in body,
          "prepare() iterates codec DAIs (no unchecked index)")
    check(body.count("return 0;") >= 2 and "return ret;" in body,
          "prepare() returns the codec prepare error")

    # The be_ops assignment must cover the DP link, i.e. DPCM back ends.
    check("link->no_pcm == 1" in src and "link->ops = &sm8150_be_ops" in src,
          "machine driver only installs be_ops on DPCM back ends")


# --------------------------------------------------------------- q6afe-dai ---

def check_q6afe_dai(tree):
    print("q6afe-dai.c")
    src = read(os.path.join(tree, "sound/soc/qcom/qdsp6/q6afe-dai.c"))

    m = re.search(r'SND_SOC_DAPM_AIF_(IN|OUT)\("DISPLAY_PORT_RX",\s*([^,]+),',
                  src)
    check(m is not None, "DISPLAY_PORT_RX widget declared")
    if m:
        check(m.group(1) == "IN", "DISPLAY_PORT_RX is declared AIF_IN",
              f"is AIF_{m.group(1)}")
        check(m.group(2).strip() == "NULL",
              "DISPLAY_PORT_RX passes NULL, not the string \"NULL\"",
              f"got {m.group(2).strip()}")

    # every other *_RX widget (MI2S/TDM/SLIMBUS/HDMI/CODEC_DMA/USB) is AIF_IN
    bad = []
    for m in re.finditer(r'SND_SOC_DAPM_AIF_(IN|OUT)\("([A-Z0-9_]*RX[0-9_]*)",',
                         src):
        name, kind = m.group(2), m.group(1)
        if kind == "OUT":
            bad.append(name)
    check(not bad, "no other *_RX port is declared AIF_OUT", f"{bad}")

    check('{"DISPLAY_PORT_RX_0 Playback", NULL, "DISPLAY_PORT_RX"}' in src,
          "playback route into DISPLAY_PORT_RX unchanged")


# ------------------------------------------------------------------- q6afe ---

def check_q6afe(tree):
    print("q6afe.c")
    src = read(os.path.join(tree, "sound/soc/qcom/qdsp6/q6afe.c"))

    for macro in ("AFE_PARAM_ID_HDMI_DP_MST_VID_IDX_CFG",
                  "AFE_PARAM_ID_HDMI_DPTX_IDX_CFG",
                  "AFE_API_VERSION_HDMI_DP_IDX_CFG"):
        check(len(re.findall(rf"^#define {macro}\b", src, re.M)) == 1,
              f"{macro} defined exactly once")
    check("0x000102b5" in src and "0x000102b6" in src,
          "DP index parameter ids are 0x102b5/0x102b6")
    m = re.search(r"struct afe_param_id_dp_idx_cfg \{\s*(.*?)\s*\} __packed;",
                  src, re.S)
    check(m is not None,
          "payload struct afe_param_id_dp_idx_cfg is __packed")
    if m:
        fields = re.findall(r"\b(?:u32|u16|u8)\s+([a-zA-Z0-9_]+)\s*;",
                            m.group(1))
        check(fields == ["minor_version", "idx"],
              "payload struct is exactly two u32 words (8 bytes), no padding",
              f"fields={fields}")
    check(re.search(r"static_assert\(sizeof\(struct afe_param_id_dp_idx_cfg\) == 8\);",
                    src) is not None,
          "payload struct has a compile-time 8-byte size assertion")
    check("sizeof(idx)" in src and "sizeof(struct afe_param_id_dp_idx_cfg)" not in
          src.split("int q6afe_port_start", 1)[-1],
          "SET_PARAM passes sizeof(idx), i.e. the 8-byte payload, not a padded size")
    for bad in ("16);", "sizeof(idx) + 8"):
        check(bad not in src, f"no padded DP payload size ({bad!r})")

    m = re.search(r"int q6afe_port_start\(struct q6afe_port \*port\)\s*\{.*?\n\}",
                  src, re.S)
    check(m is not None, "q6afe_port_start() present")
    body = m.group(0)
    dp = body.find("port_id == AFE_PORT_ID_HDMI_OVER_DP_RX")
    cfg = body.find("&port->port_cfg")
    start = body.find("AFE_PORT_CMD_DEVICE_START")
    check(dp != -1, "port_start() has a DP branch")
    check(-1 < dp < cfg < start,
          "DP parameters are sent before port config and before start",
          f"dp={dp} cfg={cfg} start={start}")
    check("AFE_PARAM_ID_HDMI_DP_MST_VID_IDX_CFG" in body and
          "AFE_PARAM_ID_HDMI_DPTX_IDX_CFG" in body,
          "both DP parameters are sent")
    check("dev_warn" in body and "return ret" not in body[:cfg],
          "a rejected DP parameter only warns (does not abort the start)")

    messages = re.findall(r'"AFE (enable|config|start|set param|TDM slot mapping)',
                          src)
    check("enable" not in messages,
          "no ambiguous \"AFE enable\" message left", f"{sorted(set(messages))}")
    for want in ("config", "start", "set param", "TDM slot mapping"):
        check(want in messages, f"\"AFE {want}\" message present")


# --------------------------------------------------------------------- DT ---

def strip_dts_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def dtb_include_chain(root, top):
    """Return the .dts/.dtsi files that make up one dtb (transitive #include)."""
    seen = []
    pending = [os.path.join(root, top)]
    while pending:
        path = pending.pop()
        if path in seen or not os.path.exists(path):
            continue
        seen.append(path)
        body = strip_dts_comments(read(path))
        for inc in re.findall(r'#include\s+"([^"]+)"', body):
            pending.append(os.path.join(os.path.dirname(path), inc))
    return seen


def check_dt(tree, kernel):
    print("devicetree")
    dtsi = os.path.join(tree, "arch/arm64/boot/dts/qcom/"
                              "sm8150-oneplus-guacamole-displayport.dtsi")
    nura = os.path.join(tree, "arch/arm64/boot/dts/qcom/"
                              "sm8150-oneplus-guacamole-nura.dtsi")
    text = strip_dts_comments(read(dtsi))

    # delimiter balance, ignoring <> (they appear in cells and in includes)
    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        check(text.count(open_c) == text.count(close_c),
              f"{open_c}{close_c} balanced", f"{text.count(open_c)} vs {text.count(close_c)}")

    # every &ref used here resolves to a label in the same dtb's include chain
    qcom = os.path.join(kernel, "arch/arm64/boot/dts/qcom")
    chain = dtb_include_chain(qcom, "sm8150-oneplus-guacamole.dts")
    check(len(chain) > 5, "include chain parsed",
          f"{len(chain)} files: {[os.path.basename(p) for p in chain]}")
    label_file = {}
    for path in chain:
        body = strip_dts_comments(read(path))
        for label in re.findall(r"^\s*([a-zA-Z0-9_]+):", body, re.M):
            label_file.setdefault(label, os.path.basename(path))
        for label in re.findall(
                r"([a-zA-Z0-9_]+):\s*(?:[a-zA-Z0-9,@-]+\s*)?\{", body):
            label_file.setdefault(label, os.path.basename(path))

    # our own labels must not collide with labels already in that chain
    own = re.findall(r"^\s*([a-zA-Z0-9_]+):", text, re.M)
    check(bool(own), f"file defines labels: {own}")
    for label in own:
        other = label_file.get(label)
        check(other is None, f"label {label} is not defined elsewhere in the dtb",
              f"also in {other}")

    refs = set(re.findall(r"<&([a-zA-Z0-9_]+)", text)) | set(
        re.findall(r"=\s*&([a-zA-Z0-9_]+)", text))
    known = set(label_file) | set(own)
    missing = sorted(r for r in refs if r not in known)
    check(not missing, "all &label references resolve in the guacamole include chain",
          f"missing {missing} (refs checked: {sorted(refs)})")

    # node names we add must not already exist in the guacamole board files
    for node in ("typec-mux@42", "port@2", "dp-dai-link", "altmodes"):
        for name in ("sm8150-oneplus-guacamole.dts",
                     "sm8150-oneplus-guacamole-nura.dtsi",
                     "sm8150-oneplus-common.dtsi"):
            path = os.path.join(qcom, name)
            body = strip_dts_comments(read(path))
            check(not re.search(rf"\b{re.escape(node)}\s*\{{", body),
                  f"{node} is new in {name}")

    # guacamole.dts must still include the board delta (which includes us)
    gts = read(os.path.join(qcom, "sm8150-oneplus-guacamole.dts"))
    check('#include "sm8150-oneplus-guacamole-nura.dtsi"' in gts,
          "guacamole.dts still includes the board delta")
    nura_text = read(nura)
    check('#include "sm8150-oneplus-guacamole-displayport.dtsi"'
          in nura_text, "board delta includes the displayport dtsi")

    # the DP node we enable already exists in sm8150.dtsi with #sound-dai-cells
    dtsi_soc = read(os.path.join(qcom, "sm8150.dtsi"))
    check(re.search(r"mdss_dp: displayport-controller@ae90000", dtsi_soc)
          is not None, "sm8150.dtsi defines the mdss_dp label")
    check(re.search(r"mdss_dp_out: endpoint", dtsi_soc) is not None,
          "sm8150.dtsi defines the mdss_dp_out label")
    check("usb_1_qmpphy: phy@88e8000" in dtsi_soc,
          "sm8150.dtsi defines the usb_1_qmpphy label")

    # the DP audio link must ship disabled: an enabled link whose codec DAI is
    # not registered defers the whole sound card (see check_kernel_invariants)
    m = re.search(r"dp-dai-link\s*\{(.*?)\n\t\};\n\};", text, re.S)
    check(m is not None, "dp-dai-link node present")
    if m:
        body = m.group(1)
        check(re.search(r'\bstatus\s*=\s*"disabled"\s*;', body) is not None,
              'dp-dai-link carries status = "disabled" (default: no DP audio)')
        check(re.search(r'\bstatus\s*=\s*"okay"\s*;', body) is None,
              "dp-dai-link is not enabled in the shipped default")
        for child in ("cpu", "platform", "codec"):
            check(re.search(rf"\b{child}\s*\{{", body) is not None,
                  f"dp-dai-link has a {child} child (DPCM back end)")
        check("q6afedai DISPLAY_PORT_RX" in body,
              "dp-dai-link cpu is DISPLAY_PORT_RX")
        check("&mdss_dp" in body, "dp-dai-link codec is the DP controller")

    raw = read(dtsi)
    check("To run the experiment" in raw and "status = \"okay\"" in raw,
          "file documents the one-line opt-in")
    check(text.count('status = "disabled"') == 1,
          "exactly one disabled node in the file (the DP audio link)")


def check_kernel_invariants(kernel):
    """Guards for the assumptions the design rests on (read-only tree)."""
    print("kernel invariants")
    core = read(os.path.join(kernel, "sound/soc/soc-core.c"))

    # 1. a missing codec DAI defers the card, so an enabled DP link is a
    #    whole-card dependency.
    m = re.search(r"int snd_soc_add_pcm_runtime\(.*?\n\}", core, re.S)
    check(m is not None, "snd_soc_add_pcm_runtime() present")
    body = m.group(0) if m else ""
    check("CODEC DAI %s not registered" in body and "_err_defer" in body,
          "missing codec DAI logs and jumps to defer")
    check(re.search(r"_err_defer:\s*\n\s*snd_soc_remove_pcm_runtime\(card, rtd\);\s*\n\s*return -EPROBE_DEFER;",
                    body) is not None,
          "missing codec DAI returns -EPROBE_DEFER")

    # 2. status = "disabled" really excludes the link from the card.
    common = read(os.path.join(kernel, "sound/soc/qcom/common.c"))
    check("of_get_available_child_count" in common,
          "qcom_snd_parse_of() counts only available children")
    check("for_each_available_child_of_node" in common,
          "qcom_snd_parse_of() walks only available children")
    check("of_get_child_by_name(np, \"platform\")" in common and
          "of_get_child_by_name(np, \"codec\")" in common,
          "platform+codec children still make a DPCM back end")
    check("link->no_pcm = 1" in common,
          "qcom_snd_parse_of() still sets no_pcm for back ends")

    # 3. snd_soc_dai_prepare() does no bookkeeping beyond validity + ops.
    dai = read(os.path.join(kernel, "sound/soc/soc-dai.c"))
    m = re.search(r"int snd_soc_dai_prepare\(.*?\n\}", dai, re.S)
    check(m is not None, "snd_soc_dai_prepare() present")
    body = m.group(0) if m else ""
    check("soc_dai_mark_push" not in body and "soc_dai_mark_pop" not in body,
          "snd_soc_dai_prepare() touches no rollback marks")
    check("ops->prepare" in body, "snd_soc_dai_prepare() calls ops->prepare")

    # 4. the DRM HDMI audio path has prepare but no hw_params, so prepare is
    #    the only hook (the reason 0048 exists).
    conn = read(os.path.join(kernel, "include/drm/drm_connector.h"))
    m = re.search(r"struct drm_connector_hdmi_audio_funcs \{.*?\n\};", conn, re.S)
    check(m is not None, "drm_connector_hdmi_audio_funcs present")
    body = m.group(0) if m else ""
    check("int (*prepare)" in body, "DRM HDMI audio has prepare")
    check("hw_params" not in body, "DRM HDMI audio has no hw_params")
    flat = re.sub(r"[\s*]+", " ", body)
    check("Can be called multiple times" in flat,
          "DRM HDMI audio prepare is documented as repeatable")

    # 5. the completion re-armed by prepare is only consumed by the DP disable
    #    path, with a bounded timeout.
    dp_audio = read(os.path.join(kernel, "drivers/gpu/drm/msm/dp/dp_audio.c"))
    dp_disp = read(os.path.join(kernel, "drivers/gpu/drm/msm/dp/dp_display.c"))
    check("msm_dp_display_signal_audio_start(msm_dp_display);" in dp_audio,
          "msm_dp_audio_prepare() re-arms the completion")
    check(dp_disp.count("wait_for_completion_timeout(&dp->audio_comp") == 1,
          "exactly one waiter on dp->audio_comp")
    check("HZ * 5" in dp_disp, "that waiter is bounded (5 s)")
    check("msm_dp_display_signal_audio_complete" in dp_audio,
          "audio shutdown completes the completion")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True, help="patched source tree root")
    ap.add_argument("--kernel", required=True,
                    help="pristine rc3 kernel source tree (read-only)")
    args = ap.parse_args()

    check_sm8150(args.tree)
    check_q6afe_dai(args.tree)
    check_q6afe(args.tree)
    check_dt(args.tree, args.kernel)
    check_kernel_invariants(args.kernel)

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} checks passed")
    if FAILURES:
        print("failed: " + ", ".join(FAILURES))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
