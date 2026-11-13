#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Rootless, source-level checks of the rc4 popup-camera deliverables.

Every check prints PASS/FAIL with the file and line it looked at.  Nothing here
touches a phone, a package manager or a privileged path; the only inputs are
files in this directory, the read-only rc3 kernel source tree and the rc3
kernel config.

Run: checks/static_checks.py [--kernel-source PATH] [--config PATH]
"""

import argparse
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
TOPIC = HERE.parent
KERNEL_DEFAULT = pathlib.Path(
    "bringup-notes/nura-rc3-20261112/"
    "kernel-device/work/linux-v7.0.0-sm8150")
CONFIG_DEFAULT = pathlib.Path(
    "runtime-workspace/"
    "device/testing/rc3/linux-postmarketos-qcom-sm8150/"
    "config-postmarketos-qcom-sm8150.aarch64")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s %-58s %s" % ("PASS" if ok else "FAIL", name, detail))


def node_block(text, marker):
    """Return the brace-matched body of the first node line containing marker."""
    i = text.find(marker)
    if i < 0:
        return None
    start = text.find("{", i)
    if start < 0:
        return None
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def read(path):
    try:
        return pathlib.Path(path).read_text()
    except OSError as exc:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel-source", default=str(KERNEL_DEFAULT))
    ap.add_argument("--config", default=str(CONFIG_DEFAULT))
    args = ap.parse_args()
    ks = pathlib.Path(args.kernel_source)
    cfg = read(args.config)

    # ---------------------------------------------------------------- safety
    dtsi_path = TOPIC / "dt/sm8150-oneplus-guacamole-popup.dtsi"
    dtsi = read(dtsi_path)
    dtsi_code = re.sub(r"/\*.*?\*/", "", dtsi, flags=re.S)

    def preprocess(*defines):
        """cpp the fragment the way dtc does, with the given -D switches."""
        cmd = ["cpp", "-P", "-undef", "-D__DTS__", "-x", "assembler-with-cpp",
               "-I", str(ks / "include"),
               "-I", str(ks / "arch/arm64/boot/dts/qcom")]
        cmd += ["-D" + d for d in defines]
        cmd.append(str(dtsi_path))
        return subprocess.run(cmd, capture_output=True, text=True)

    default = preprocess("CAMERA_WIDE")
    check("DT: default build preprocesses", default.returncode == 0,
          default.stderr.strip()[:120])
    check("DT: default has NO front sensor node", "camera@10" not in default.stdout)
    check("DT: default has NO CAMSS/CSIPHY2 endpoint", "port@2" not in default.stdout)
    check("DT: default keeps the motor node present but disabled",
          (node_block(default.stdout, "camera-popup {") or "").count(
              'status = "disabled";') == 1)
    check("DT: default describes the Hall sensors on SE1",
          "magnachip,mxm1120" in default.stdout and "&i2c1 {" in default.stdout)
    check("DT: default enables qupv3_id_0 for SE1",
          re.search(r"&qupv3_id_0 \{\s*status = \"okay\";", default.stdout) is not None)
    check("DT: default enables the LPG step PWM provider",
          re.search(r"&pm8150l_lpg \{\s*status = \"okay\";", default.stdout) is not None)
    check("DT: default adds PM8009 LDO3 (VDIG)",
          "vreg_l3f_1p05: ldo3" in default.stdout)

    motor = preprocess("CAMERA_WIDE", "CAMERA_POPUP_MOTOR")
    check("DT: CAMERA_POPUP_MOTOR enables only the motor node",
          motor.returncode == 0 and
          'status = "okay";' in (node_block(motor.stdout, "camera-popup {") or "") and
          "camera@10" not in motor.stdout and "port@2" not in motor.stdout)

    sensor = preprocess("CAMERA_WIDE", "CAMERA_POPUP_SENSOR")
    check("DT: CAMERA_POPUP_SENSOR emits sensor + endpoint together",
          sensor.returncode == 0 and "camera@10" in sensor.stdout and
          "port@2" in sensor.stdout)
    check("DT: sensor-only build has no power-domains (must probe without motor)",
          sensor.returncode == 0 and
          "power-domains" not in (node_block(sensor.stdout, "camera@10 {") or "x"))
    check("DT: sensor-only build leaves the motor node disabled",
          'status = "disabled";' in (node_block(sensor.stdout, "camera-popup {") or ""))

    both = preprocess("CAMERA_WIDE", "CAMERA_POPUP_SENSOR", "CAMERA_POPUP_MOTOR")
    check("DT: sensor+motor build wires power-domains to the motor",
          both.returncode == 0 and
          "power-domains = <&camera_popup>;" in both.stdout and
          'status = "okay";' in (node_block(both.stdout, "camera-popup {") or ""))

    no_wide = preprocess("CAMERA_POPUP_SENSOR")
    check("DT: CAMERA_POPUP_SENSOR without CAMERA_WIDE is a build error",
          no_wide.returncode != 0 and "CAMERA_WIDE" in no_wide.stderr)

    isolate = preprocess("CAMERA_ISOLATE", "CAMERA_WIDE")
    check("DT: an isolate build gets none of this file",
          isolate.returncode == 0 and "camera-popup" not in isolate.stdout and
          "magnachip" not in isolate.stdout)

    camss_gate = read(TOPIC / "checks/camss_async_audit.py")
    check("check: CAMSS async audit models the waiting list",
          "camss_parse_ports" in camss_gate and "waiting list" in camss_gate)

    # -------------------------------------------------------------- motor code
    mot = read(TOPIC / "src/hotdog-popup-motor.c")
    defs = dict(re.findall(r"^#define (HOTDOG_[A-Z_]+)\s+(.+)$", mot, re.M))
    check("motor: course ceiling is 1380*32 microsteps",
          defs.get("HOTDOG_FULL_COURSE_MICROSTEPS") == "(1380 * 32)")
    check("motor: settle window sits inside the closed window",
          int(defs["HOTDOG_SETTLED_DOWN_MIN"]) > int(defs["HOTDOG_CLOSED_DOWN_MIN"]))
    check("motor: 'closed' requires the restore window, not the loose one",
          int(defs["HOTDOG_CLOSED_DOWN_MIN"]) <= int(defs["HOTDOG_RESTORE_DOWN_MIN"]))
    check("motor: open endpoint threshold above the finish window",
          int(defs["HOTDOG_FULL_OPEN_UP_MIN"]) > int(defs["HOTDOG_OPEN_HALL_MIN"]))
    check("motor: Hall samples bounded by a plausibility ceiling",
          defs.get("HOTDOG_HALL_ABS_MAX") == "450")
    check("motor: every movement attribute is one-shot per load",
          mot.count("if (popup->") >= 5 and
          all(flag in mot for flag in
              ("preflight_used", "restore_used", "open_used",
               "finish_open_used", "close_used")))
    check("motor: stall detection needs Hall progress",
          "HOTDOG_PROGRESS_MIN_COUNTS" in defs and
          mot.count("HOTDOG_PROGRESS_PROBE_MICROSTEPS") >= 2)
    check("motor: all controls off after every course",
          "static void hotdog_popup_stop" in mot and
          mot.count("hotdog_popup_stop(popup);") >= 8)
    check("motor: failed automatic open tries a bounded close",
          "recovery_ret = hotdog_popup_close_with_retry(popup);" in mot)
    check("motor: no free-fall/IRQ handling (documented gap)",
          "request_irq" not in mot and "IRQ" not in mot)
    check("motor: PM domain starts logically on (no motion at probe)",
          "pm_genpd_init(&popup->genpd, NULL, false)" in mot)
    check("motor: probe refuses to move the mechanism",
          mot.index("hotdog_popup_stop(popup);") < mot.index("pm_genpd_init("))

    # ------------------------------------------------------ LPG channel claim
    lpg = read(ks / "drivers/leds/rgb/leds-qcom-lpg.c")
    m = re.search(r"static const struct lpg_data pm8150l_lpg_data = \{(.*?)\n\};",
                  lpg, re.S)
    chans = re.findall(r"\.base = (0x[0-9a-f]+)", m.group(1)) if m else []
    check("LPG: pm8150l has 5 channels and index 4 is 0xbd00",
          len(chans) == 5 and chans[4] == "0xbd00", str(chans))
    stock = "runtime-workspace/" \
            ".work/investigation/E/dtbo/entry00.dts"
    stock_txt = read(stock)
    stock_pwm = re.search(r"qcom,pwms@bc00 \{(.*?)\n\t\t\t\t\};", stock_txt, re.S)
    stock_pwms = re.search(r"pwms = <0xa9 0x01", stock_txt)
    check("LPG: stock uses pwms@bc00 with 2 channels, index 1",
          bool(stock_pwm) and "qcom,num-lpg-channels = <0x02>" in stock_pwm.group(1)
          and bool(stock_pwms))
    check("LPG: stock index 1 -> 0xbc00 + 0x100 == mainline index 4 (0xbd00)",
          len(chans) == 5 and int(chans[4], 16) == 0xbc00 + 0x100)

    # ------------------------------------------------- imx471 mode arithmetic
    imx = read(TOPIC / "src/imx471.c")
    pclk = int(re.search(r"\.pclk = (\d+),", imx).group(1))
    w, h = re.search(r"\.width = (\d+),\n\t\.height = (\d+),", imx).groups()
    hb, vb = re.search(r"\.hblank = (\d+),\n\t\.vblank = (\d+),", imx).groups()
    fps = pclk / (int(w) + int(hb)) / (int(h) + int(vb))
    check("IMX471: one mode, 1748x1748", f".width = {w}" in imx and f".height = {h}" in imx)
    check("IMX471: mode timing gives ~81 fps, not the README's ~90",
          80.0 < fps < 82.0, "%.2f fps (pclk %d, %sx%s hb %s vb %s)"
          % (fps, pclk, w, h, hb, vb))
    check("IMX471: driver enforces 19.2 MHz input clock",
          "clk_set_rate(imx471->inclk, IMX471_INCLK_RATE)" in imx and
          "#define IMX471_INCLK_RATE\t19200000" in imx)
    check("IMX471: link frequency 525.2625 MHz with 4 lanes",
          "#define IMX471_LINK_FREQ\t525262500" in imx and
          "#define IMX471_NUM_DATA_LANES\t4" in imx)
    check("IMX471: VANA window is BOB-compatible (3.30-3.32 V)",
          "#define IMX471_VANA_MIN_UV\t3300000" in imx and
          "#define IMX471_VANA_MAX_UV\t3320000" in imx)

    # ------------------------------------------------------------- rc3 config
    for sym in ("CONFIG_IIO=y", "CONFIG_PWM=y", "CONFIG_REGMAP_I2C=y",
                "CONFIG_OF_GPIO=y", "CONFIG_PM_GENERIC_DOMAINS_OF=y",
                "CONFIG_LEDS_QCOM_LPG=m", "CONFIG_I2C_QCOM_CCI=m",
                "CONFIG_SM_CAMCC_8150=y", "CONFIG_I2C_QCOM_GENI=y"):
        check("config: rc3 already has %s" % sym, sym in cfg)
    for sym in ("CONFIG_MXM1120=m", "CONFIG_VIDEO_IMX471=m",
                "CONFIG_VIDEO_QCOM_HOTDOG_POPUP=m"):
        check("config: fragment sets %s" % sym,
              sym in read(TOPIC / "fragments/config.fragment"))

    # ---------------------------------------------------- rc3 DT prerequisites
    rear = read(ks / "arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-rear.dtsi")
    guac = read(ks / "arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts")
    common = read(ks / "arch/arm64/boot/dts/qcom/sm8150-oneplus-common.dtsi")
    check("rc3 DT: cci1 is gated on CAMERA_WIDE (documented dependency)",
          "#ifdef CAMERA_WIDE\n\tcci1: cci@ac4b000" in rear)
    check("rc3 DT: camss ports has 0/1/3 only (port@2 free for the front)",
          "port@2" not in rear and "port@3" not in rear.split("ports {")[1].split("};")[0]
          or "port@2" not in rear)
    pm8009 = re.search(r"pm8009-rpmh-regulators \{(.*?)\n\t\};", common, re.S)
    check("rc3 DT: PM8009 LDO3 is not described yet (fragment must add it)",
          bool(pm8009) and "ldo3 {" not in pm8009.group(1) and
          "ldo3 {" not in rear)
    check("rc3 DT: vreg_l7c_3p0 / vreg_l8c_1p8 exist for the Hall supplies",
          "vreg_l7c_3p0: ldo7" in common and "vreg_l8c_1p8: ldo8" in common)
    check("rc3 DT: pm8150l_lpg label exists upstream",
          "pm8150l_lpg: pwm" in read(ks / "arch/arm64/boot/dts/qcom/pm8150l.dtsi"))
    check("rc3 DT: i2c1 (SE1) is disabled upstream and needs qupv3_id_0",
          "i2c1: i2c@884000" in read(ks / "arch/arm64/boot/dts/qcom/sm8150.dtsi")
          and "qupv3_id_0" not in guac)

    bad = [n for n, ok, _ in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(bad)))
    if bad:
        print("FAILED: " + ", ".join(bad))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
