# Front (pop-up) camera on guacamole — local-evidence feasibility

Investigation only, 2026-11-14. No build, no pmbootstrap, no fastboot, no phone,
no sudo. Claims are labelled **VERIFIED** (read from a file named here),
**UNVERIFIED** (local evidence is contradictory or indirect) or **UNKNOWN** (no
local evidence).

## 0. Sources actually read

Paths are relative to the repo root unless absolute.

| # | Source | What it settles |
|---|---|---|
| S1 | `../nura-rc3-20261112/lineage-dtbs/extract/`: `dtb-00..19.dts`, `dtbo/dtbo-entry-00.dts`, `merged/merged-libfdt.dts`, `merged/merged-libufdt.dts` | `dtb-*.dts` are SoC-level only (**no** camera node, `grep -i imx` empty); the DTBO entry 0 (project 18821) is the authoritative board wiring; both mergers give the same effective values |
| S2 | `../nura-rc3-20261112/lineage-wiring/{REPORT.md,WIRING.tsv}` | Prior wiring inventory; the pop-up camera Hall rows are `WIRING.tsv:52` and `REPORT.md:331` (an earlier revision cited the NFC rows at `:50`/`:329`) |
| S3 | `device/testing/rear-camera/linux-postmarketos-qcom-sm8150/0028..0031` | The three-rear-camera mainline port this work sits on |
| S4 | `flows/rc4/build-source/kernel-state/linux-v7.0.0-sm8150` (kernel source; `/tmp/kbase/linux-v7.0.0-sm8150` differs only in `nura.dtsi` + two msm files) | CAMSS/CCI/i2c driver capability |
| S5 | `flows/rc4/device/testing/linux-postmarketos-qcom-sm8150` (66 patches, `pkgrel=13`, the recipe behind the running rc4j kernel) and `flows/rc4/build-source/artifacts/dtb/sm8150-oneplus-guacamole.dtb` | What already ships in the phone's kernel **and** in its DTB |
| S6 | `../Patches/popup-camera/` (supplied hand-off; present on this host) + `flows/rc4/topics/popup-camera/` (its local rc4 rebase, includes `docs/motor-procedure.md`) | Existing driver and DT material |
| S7 | `device/testing/rear-camera/libcamera-rear/`, `device/testing/rear-camera/SOURCE_PINS.json` | Userspace soft-ISP state |

## 1. Front sensor: identity and electrical wiring

### 1.1 Identity — **UNVERIFIED (indirect)**

The vendor device tree does **not** name the sensor: `qcom,cam-sensor@2` carries
only `cell-index`, `csiphy-sd-index` and `<0xffffffff>` phandles, and `strings`
on `dtbo-entry-00.dtb` shows no `imx*` string. The IMX471 identity rests on the
supplied hand-off's citation of the LineageOS *kernel source*
(`Patches/popup-camera/README.md:59`, "`sm8150-oem-camera-t0.dtsi` L561–L605") —
those `.dtsi` files are **not** in this checkout, so it cannot be re-checked
here. Corroboration is only the local driver's chip ID
(`drivers/media/i2c/imx471.c:31`, `IMX471_ID 0x0471`), plus the LineageOS
commit that names "IMX471 on OP7" and the vendor HAL blobs
(`com.qti.sensor.imx471.so`, `com.qti.tuned.sunny_imx471.bin`) — see §8.7.

### 1.2 Wiring — mostly **VERIFIED**

Decisive node, effective (merged) form, `merged-libfdt.dts:5809-5849`:

```
qcom,cam-sensor@2 {                              /* merged-libfdt.dts:5809 */
    csiphy-sd-index = <0x02>;                                       /* :5845 */
    cci-device = <0x01>; cci-master = <0x00>;                  /* :5810, :5817 */
    clocks = <0x33 0x4c>; clock-rates = <0x124f800>;   /* :5815, :5812 = MCLK2 19.2 MHz */
    gpios = <0x38 0x0f 0> <0x38 0x0c 0> <0x5d2 0x0c 0>;  /* :5823 = TLMM15, TLMM12, PM8150L GPIO12 */
    gpio-req-tbl-label = "CAMIF_MCLK2", "CAM_RESET2", "CAM_VANA_2";  /* :5819 */
    cam_vio-supply  = <0x447>;   /* :5838 = pm8150l_l1  (merged-libfdt.dts:22067) */
    cam_vana-supply = <0x44f>;   /* :5836 = pm8150l_bob (merged-libfdt.dts:22275) */
    cam_vdig-supply = <0x456>;   /* :5835 = pm8009_l3   (merged-libfdt.dts:22369) */
    rgltr-min/max-voltage = <.. 0x325aa0 0x101d00 ..>; /* :5829-5830 = VANA 3.3 V, VDIG 1.056 V */
    eeprom-src = <0x610>;  /* :5841 = eeprom@9 "eeprom_front_0" (merged-libfdt.dts:30234) */
}
```

| Item | Value | Evidence |
|---|---|---|
| Sensor | Sony IMX471 16 MP, 4-lane D-PHY | **UNVERIFIED** — see 1.1; lanes match the driver (`imx471.c:59` `IMX471_NUM_DATA_LANES 4`) |
| CSIPHY | **CSIPHY2**, `csiphy@ac67000`, cell-index 2, `qcom,csiphy-v1.1`, CSI vdd 1.2 V, `gdscr` = `titan_top_gdsc` | `merged-libfdt.dts:5332-5350`; symbol `cam_csiphy2` `:30643` |
| CCI | **CCI1 (`cci@ac4b000`), master 0** | `merged-libfdt.dts:5810,5817`; `:30650` |
| CCI1 pins | DATA2/CLK2 = **TLMM 31/32** (master 0), DATA3/CLK3 = TLMM 33/34 (master 1) | `merged-libfdt.dts` `cci@ac4b000` (`:6316`) `gpios` `:6337`, labels `:6340` (`0x1f..0x22`) |
| I²C address | **0x10** — *not present in the vendor DT* (`reg = <0x02>` is the cell index); comes from the downstream sensor driver | `dtbo-entry-00.dts:9450`, `Patches/popup-camera/README.md:59` |
| MCLK | camcc `MCLK2`, **19.2 MHz** | `merged-libfdt.dts:5815` (`0x33 0x4c`), `:5812` (`0x124f800`) |
| Reset | TLMM **12**, plus `CAM_RESET2` pinctrl group | `merged-libfdt.dts:5823-5825` |
| VANA | PM8150L **BOB** 3.3 V, enabled by **PM8150L GPIO12** switch | `merged-libfdt.dts:5836`, `:5823`; same tree in `Patches/popup-camera/README.md:61` |
| VDIG | **PM8009 LDO3**, 1.056 V | `merged-libfdt.dts:5835`, `:22369` |
| VIO | **PM8150L LDO1**, 1.8 V | `merged-libfdt.dts:5838`, `:22067` |
| MIPI switch | none: no C-PHY/D-PHY switch node exists | `grep -in "mipi.*switch\|switch.*mipi"` over the DTBO and merged trees matches only SMB5 charger-switch properties |

### 1.3 Two hard contradictions in the local evidence

1. **CCI master.** Two overlay fragments both target `cam_cci0` (`dtbo-entry-00.dts`
   `__fixups__`: `cam_cci0 = "/fragment@47:target:0", "/fragment@119:target:0"`).
   The `fragment@47` copy of `qcom,cam-sensor@2` says `cci-master = <0x01>` with no
   `cci-device` (→ CCI0 master 1, `dtbo-entry-00.dts:6381`); the later
   `fragment@119` copy wins the merge with `cci-device = <1>, cci-master = <0>`, and
   two further local sources agree: `docs/REAR_CAMERA_7_0_BRINGUP.md:122` ("No
   front sensor, **CCI1 master-0 bus/pins**, PHY2 endpoint, PM8009 LDO3") and
   `Patches/popup-camera/README.md:59`. **Consequence:** the existing rc4 popup DT
   puts the sensor on `cci1_i2c1` (TLMM 33/34, `…-popup.dtsi`, `&cci1 { cci1_i2c1 … camera@10 }`)
   = the **ultra-wide's** bus, i.e. the wrong pins if master 0 is right.
   Working hypothesis: master 0. **UNVERIFIED.**
2. **VDIG rail.** `fragment@47` gives the front sensor `pm8009_l2` @1.2 V; the
   effective `fragment@119` value is `pm8009_l3` @1.056 V (`merged-libfdt.dts:5835`
   plus the `pm8009_l3` fixup). Carried as open question C9 in
   `flows/rc4/topics/popup-camera/README.md` §6; the merged tree resolves it to
   **LDO3 1.056 V**. If wrong, the sensor's digital rail is wrong and probe NAKs.

## 2. The pop-up mechanism: how it is driven downstream

Everything is in the vendor DT; **no downstream driver source is present
locally** (`grep -rn "oneplus,step-motor\|drv8834"` over the fork finds only the DT
node and the mainline port). Vendor node, `dtbo-entry-00.dts:8772-8791`:

```
step_motor {
    compatible = "oneplus,step-motor";
    pwms = <0xa9 0x01 0x1312d00>;          /* pm8150l_pwm ch.1, 20 ms period */
    op,boost-en-pin = <0xaa 0x0c 0x01>;    /* PM8150B GPIO12 */
    op,mode0-pin    = <0xaa 0x05 0x01>;    /* PM8150B GPIO5  */
    op,mode1-pin    = <0xab 0x08 0x01>;    /* PM8150L GPIO8  */
    op,nsleep-pin   = <0x09 0x0a 0x01>;    /* PM8150  GPIO10 */
    op,dir-pin      = <0xab 0x0b 0x01>;    /* PM8150L GPIO11 */
    op,step-pin     = <0xab 0x0a 0x01>;    /* PM8150L GPIO10 */
    pinctrl-names = "boost", "m0_gpio", "m0_high_impedance", "m1_gpio",
                    "sleep_gpio", "dir_gpio", "pwm_config";
}
```

| Element | Value | Evidence |
|---|---|---|
| Motor | DRV8834 stepper, 1/32 microstep (MODE0 high-Z), PWM-driven STEP | `dtbo-entry-00.dts:8772-8782` (mode0 `:8777`); `Patches/popup-camera/README.md:63` |
| STEP PWM | vendor `qcom,pwms@bc00` (`#pwm-cells=2`, 2 channels), index **1** → register `0xbd00` → **mainline LPG channel 4** of `pm8150l_lpg` (`{0xb100,0xb200,0xb300,0xbc00,0xbd00}`) | `dtbo-entry-00.dts:1878-1886` (`phandle 0xa9`); `drivers/leds/rgb/leds-qcom-lpg.c:1782-1796` |
| STEP/DIR/MODE1/nSLEEP/BOOST gpio owners | `0x09` = `qcom,pm8150@0` pinctrl `:94`; `0xaa` = `qcom,pm8150b@2` pinctrl `:450`; `0xab` = `qcom,pm8150l@4` pinctrl `:1394` | `dtbo-entry-00.dts` |
| Position feedback | **2× MagnaChip MXM1120 Hall** on QUP SE1 `i2c@884000`: `0x0c` "up", `0x0d` "down"; IRQs TLMM 120/121; vdd PM8150L LDO7, vio LDO8 | `dtbo-entry-00.dts:8741-8770`; fixups `:10757-10758` |
| Free-fall / limit input | `motor_pl { compatible = "oneplus-motor"; … motor,irq-gpio = <.. 0xa3 ..> }` = **TLMM163**, pin state `free_fall_input` | `dtbo-entry-00.dts:8699-8712`, `:8717-8731` |
| Regulator | none dedicated: boost converter gated by PM8150B GPIO12, motor fed from it | `dtbo-entry-00.dts:8776` |
| Downstream driver source | **absent** (UNKNOWN) | no `oneplus,step-motor` C source anywhere under `bringup-notes` |
| Unrelated neighbour | `oneplus-infrared` IR projector on PM8150L LDO9 — separate device | `dtbo-entry-00.dts:8681-8693` |

## 3. Mainline feasibility in *this* tree

### 3.1 What exists (reuse)

| Need | Local state | Verdict |
|---|---|---|
| 4th CSIPHY | `csiphy_res_8150[]` has **four** entries, `.id = 0..3`, all `csiphy_ops_3ph_1_0` (`camss.c:1706-1798`, CSIPHY2 at `:1753`) | **reuse** — CSIPHY2 is already described in the board `camss` node's `reg`/`reg-names` (`0030`), only `port@2` is missing |
| CSIPHY→CSID routing | full mesh: one pad link per csiphy×csid pair (`camss_link_entities`, `camss.c:4792-4802`) | **reuse** — 2 CSIDs (`csid_res_8150`, `camss.c:1800-1850`) serve 4 PHYs; no 4th CSID |
| IMX471 sensor driver | `drivers/media/i2c/imx471.c` (1299 lines, 4-lane, `IMX471_LINK_FREQ 525262500` `:58`, 1748×1748 `:299-301`, VANA-enable GPIO `:866`) | **reuse** — already patched in as `0053-media-i2c-add-imx471.patch`, `CONFIG_VIDEO_IMX471=m` (`flows/rc4/device/testing/…/config-postmarketos-qcom-sm8150.aarch64:4681`) |
| Popup motor driver | `drivers/media/platform/qcom/hotdog-popup-motor.c` (23 KB) + `CONFIG_VIDEO_QCOM_HOTDOG_POPUP=m` (`:4583`); registers a generic PM domain (`:785-799`) | **reuse, but hotdog-calibrated** — ported, not new |
| Hall sensors | `drivers/iio/magnetometer/mxm1120.c` via `0052`, `CONFIG_MXM1120=m` (`:7834`) | **reuse** |
| DT binding schema for `sony,imx471` | **absent** (`Documentation/devicetree/bindings/media/i2c/` has only `sony,imx412.yaml`, `sony,imx415.yaml`) | works at runtime; `dtbs_check` warning only |
| Upstream mainline popup support | **none** — `grep -ril popup drivers/ arch/arm64/boot/dts/qcom/` matches only unrelated files (`gma500/intel_bios.h`, `smsusb.c`, `newport_con.c`) plus the local port files | **needs the ported driver**, nothing upstream to wait for |
| libcamera sensor data | **absent** — `libcamera-rear` ships `imx586/imx481/s5k3m5` (`APKBUILD:20-23,39-41,63-64`) but no IMX471 patch or `imx471.yaml`; `SOURCE_PINS.json` pins `0007-libcamera-add-Sony-IMX471-sensor-data.patch` (sha256 `42413e76…`) that **is not in this checkout** | **needs writing/obtaining** |

### 3.2 What the running kernel already has (and deliberately does not)

`flows/rc4` (rc4j, `pkgrel=13`) already carries `0052`(mxm1120), `0053`(imx471),
`0054`(popup motor) and `0056`(popup DT) — see
`flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/APKBUILD:10-11`. The
popup DTSI is included from `sm8150-oneplus-guacamole.dts:140` but ships gated
off. Verified on the artifact:

```
strings flows/rc4/build-source/artifacts/dtb/sm8150-oneplus-guacamole.dtb
  → camera-popup, camera-popup-up, camera-popup-down, magnachip,mxm1120, sony,imx586
  → no sony,imx471   (no front sensor node, no port@2)
```

On the phone today: Hall sensors and LPG exist, the motor node is
`status = "disabled"`, the front sensor is absent from the DTB.

## 4. Build-plan deltas

Two baselines exist; name yours before writing patches.

* **A — the running one**, `flows/rc4/device/testing/…` (highest `0073`): the
  sensor and motor drivers are already there, so the deltas are DT only (plus a
  possible CCI-pins fix and the libcamera package).
* **B — the repo-root recipes**, `device/testing/rear-camera` (highest
  `0037-dsi-cmd-frame-sync.patch`) / `device/testing/7.0` (highest
  `0027-arm64-dts-enable-guacamole-speaker-bus.patch`): the series must be
  re-added, so the task's numbering applies — **next free is `0038`** in
  `rear-camera`.
* **Numbering collision to avoid:** in the *running* baseline `flows/rc4/…`,
  `0038`–`0041` are already taken (rear flash bring-up, aw8697 Kconfig, aw8697
  haptics, haptics/alert-slider DT), so there the next free number is `0074`.
  State the baseline in any patch header.

| New patch (Baseline B) | Content | Source to copy from |
|---|---|---|
| `0038-media-i2c-add-imx471.patch` | IMX471 driver (Kconfig/Makefile/imx471.c) | `Patches/popup-camera/kernel/0036-media-i2c-add-imx471.patch` (= `flows/rc4/…/0053-…`) |
| `0039-iio-magnetometer-add-mxm1120.patch` | Hall sensors (needed before any motor step) | `Patches/popup-camera/kernel/0035-…` (= `0052`) |
| `0040-media-qcom-add-oneplus-popup-motor.patch` | DRV8834 + PM-domain driver | `Patches/popup-camera/kernel/0037-…` (= `0054`) |
| `0041-arm64-dts-guacamole-popup-front.patch` | board delta: `cci1` front node + `camss port@2` + motor/Hall/regulator nodes | `flows/rc4/topics/popup-camera/dt/sm8150-oneplus-guacamole-popup.dtsi` |
| (userspace) `0007-libcamera-add-Sony-IMX471-sensor-data.patch` + `imx471.yaml` + APKBUILD lines | `CameraSensorHelperImx471` + `REGISTER_CAMERA_SENSOR_HELPER("imx471", …)` and a `camera_sensor_properties.cpp` entry (unit cell / delays), a soft-ISP tuning file, installed next to `imx586.yaml` | model on `0004-libcamera-add-Sony-IMX586-sensor-data.patch:19-30` (helper) and `:43` (properties) and `APKBUILD:63-64`; the pinned copy is **missing** |

> **Correction (audit).** Upstream libcamera master *does* now carry an `imx471`
> entry in `src/libcamera/sensor/camera_sensor_properties.cpp` (unit cell
> `{1400, 1400}`, five test-pattern modes, empty `sensorDelays`) — **properties
> only**, no analogue-gain helper, no tuning file. So the correct statement is
> "`libcamera-rear` lacks IMX471 data", not "libcamera lacks it". Also note the
> hotdog-derived parameters this patch was to copy use a **1.0 µm** pixel and a
> `G = 1024/(1024-code)` gain model, which **conflicts** with upstream's
> 1400 nm unit cell; take both from a datasheet, not from either repo.

DTS nodes to add (all already prototyped in the rc4 popup DTSI):

1. `&camss { ports { port@2 { csiphy2_ep: endpoint { bus-type = DPHY; data-lanes = <0 1 2 3>;
   remote-endpoint = <&imx471_ep>; } } } }` — CSIPHY2 is the only PHY the rear
   series left unconnected (`0030` defines ports 0, 1, 3 only).
2. `&cci1` front node: `i2c-bus@0` with pinctrl on **TLMM 31/32** if master 0 is
   confirmed (today only `cci1_i2c1`/GPIO33-34 exists, `0030`); `camera@10 {`
   `compatible = "sony,imx471"; reg = <0x10>; clocks = <&camcc CAM_CC_MCLK2_CLK>;`
   `assigned-clock-rates = <19200000>; vio-supply = <&vreg_l1c_1p8>; vana-supply = <&vreg_bob>;`
   `vdig-supply = <&vreg_l3f_1p05>; reset-gpios = <&tlmm 12 GPIO_ACTIVE_LOW>;`
   `vana-enable-gpios = <&pm8150l_gpios 12 GPIO_ACTIVE_HIGH>; port { … 4 lanes, 525262500 Hz } }`.
3. `&{/soc@0/rsc@18200000/pm8009-rpmh-regulators} { ldo3 { 1056000 µV;
   regulator-allow-set-load; allowed-modes LPM/HPM } }` — without allow-set-load the
   driver's 1.05 A vote is silently dropped.
4. `camera-popup` platform node: `pwms = <&pm8150l_lpg 4 105000>`, the six GPIOs
   from §2, `io-channels = <&hall_up>, <&hall_down>`, `#power-domain-cells = <0>`;
   plus `&pm8150l_lpg { status = "okay" }`, `&qupv3_id_0 { status = "okay" }`, and
   `&i2c1` with `magnetometer@c`/`@d` and `dmas`/`dma-names` dropped.
5. Required for a self-raising sensor: `power-domains = <&camera_popup>` on
   `camera@10`, so streaming runs the genpd `power_on/off` courses.

Userspace appearance (given the current soft-ISP stack):

| Object | Name it will have |
|---|---|
| CSIPHY / CSID / VFE | `msm_csiphy2` (`camss-csiphy.c:25,797`); `msm_csid0`/`msm_csid1` (`camss-csid.c:38,1355`); `msm_vfe0_rdi0…` (`camss-vfe.c:28,2069-2072`) |
| Sensor subdev | `imx471 4-0010` (i2c client of whichever CCI bus is chosen) |
| libcamera | a fourth entry in `cam -l` / `cam-rear` (isolated 0.7.2 soft-ISP prefix `/opt/nura-rear-camera`, `libcamera-rear/cam-rear:4`); index depends on probe order — **UNVERIFIED** |

Hard rule from the existing port: **never emit `port@2` without a probe-able
sensor.** `camss_parse_ports()` (`camss.c:4672`) queues a v4l2-async connection
per endpoint with no availability test, and `camss_subdev_notifier_complete()`
(`camss.c:5007-5044`) is the only caller of `v4l2_device_register_subdev_nodes()`:
one unbindable endpoint therefore removes **all** `/dev/video*` nodes, rear
cameras included (audit: `flows/rc4/topics/popup-camera/checks/camss_async_audit.py`).

## 5. Risks

| # | Risk | Severity | Evidence / mitigation |
|---|---|---|---|
| R1 | **Motor thresholds are hotdog's**, all `#define`s measured on one 7T Pro (`hotdog-popup-motor.c:19-47`); `open` returns `-ERANGE` if this unit's closed Hall window (`\|down\| ≥ 340`, `\|up\| < 50`) does not match | **confirmed blocker** — §8.2 measures `\|down\| = 303`, so the gate fails today (blocks, does not damage) | see §8.2 for the exact failing thresholds and the rescale |
| R2 | **Bricking the mechanism**: a course is bounded (44 160 µsteps ≈ 0.58 s at 13.125 µs) and Hall-terminated, but a jam or an obstructed opening ends in a stall, and there is no stall-current feedback | high | `hotdog-popup-motor.c:19-47`, then the progress/ceiling block at `:340-410` (ramp 960 → fast probe 3200 → `-EPROTO`, ceiling `-ERANGE`) and the endpoint test at `:277-283`; "someone holds the phone" procedure: `flows/rc4/topics/popup-camera/docs/motor-procedure.md` (not `docs/`). **The `restore_closed` escape hatch is dead on this unit — see R2a/R2b.** |
| R2b | **The close course cannot terminate on this unit either.** `motion_endpoint(false)` is `is_closed()`, which needs `\|down\| >= 340`; at 303 it never fires, so `move_profile(false)` runs the **full 44160 µsteps against the endstop** and returns `-ERANGE` (`:414`). `close()` then short-circuits (`if (ret \|\| …) return ret`) so the 320-µstep re-seat is **never reached**, and `close_with_retry()` only retries `-EPROTO`. Closed is unobservable until the closed window is rescaled | high | same rescale as R2a — do them together or neither |
| R2a | **The recovery path is gated by the same hotdog constants.** `restore_closed()` accepts only 320 or 1920 µsteps (`hotdog-popup-motor.c:606-609`), and both windows require `\|down\| ≥ 340` (preflight) / `≥ 320` (recover) (`:247-255`). At the measured `\|down\| = 303` **both refuse with `-ERANGE`** | high — this is the failure mode that leaves the module up | rescale `RESTORE_DOWN_MIN/MAX` **and** `RECOVER_DOWN_MIN/MAX` together before the first move, and do not enable `power-domains` until a manual `pulse_up` → `close` round trip is proven |
| R3 | **No free-fall retraction**: TLMM163 (`oneplus-motor` downstream) is deliberately left unconfigured; nothing retracts a raised module on a drop | high for daily use | rc4 topic README §4 "Free-fall (not implemented)"; `dtbo-entry-00.dts:8699-8731` |
| R4 | **Process death / stuck raised**: normal lowering is via sensor runtime-PM; if CAMSS holds the subdev the module stays up, and there is no watchdog | medium | the `close`/`restore` write-only attributes (`hotdog-popup-motor.c:594-624,714-719`) are the only manual exit, and R2a shows they can refuse |
| R5 | **STEP pad claimed twice** (GPIO + LPG PWM on PM8150L GPIO10) — classic "pin already requested" failure | medium | popup DTSI `motor_step_default` + `motor_step_pwm`; flagged in rc4 topic README §4 |
| R6 | **CCI probe fault class** (corrected — this row previously had it backwards): `cci_probe` oopses when a CCI instance is **missing** one of its buses, so the live shape (CCI0 with both, CCI1 with only `i2c-bus@1`) makes **CCI1** the fragile one today. Adding **`cci1 i2c-bus@0`** removes that shape rather than creating it | medium, already mitigated | `docs/CCI_PROBE_FAULT.md` §Verdict ("Only CCI1 can crash … structural, independent of probe order"); `0031-…-initialize-all-master-completions.patch` is present in **both** recipes — do not build this delta from a recipe lacking it |
| R7 | **One unbindable endpoint kills every camera** (see §4) | high | `camss.c:4672,5007-5044`; audit script above |
| R8 | **Physical dependency**: the sensor is hidden inside the body; it cannot stream until the module is raised, and the raise happens inside a genpd callback, so a stream start can block ~1 s in `power_on` | medium | `hotdog-popup-motor.c:785-799`; popup DTSI `power-domains` comment |
| R9 | **VDIG rail** (LDO3 1.056 V vs LDO2 1.2 V). Resolved for the rc4 baseline and confirmed on the device (§8.4), **but `0030` in the repo-root recipe still leaves PM8009 LDO3 undescribed** | low for baseline A, medium for baseline B — §4 item 3 is still required there | §1.3 item 2; `flows/rc4/topics/popup-camera/README.md` §6 C9; §8.4 |
| R10 | **Boost converter is on from probe** (PM8150B GPIO12 driven high, never released, as OxygenOS does) — a standing current draw and a live motor supply while the node is enabled | low-medium | popup DTSI `motor_boost_default`; rc4 topic README §4 |
| R11 | **Suspend with the camera stack** is untested on this device; the popup adds a sleeping genpd callback to a path that already has no suspend evidence | medium | `docs/WORKING_CAMERA_CHECKPOINT.md:26-30`, `docs/REAR_CAMERA_7_0_BRINGUP.md:543-552` |
| R12 | Rear-sensor fixture ordering (VANA before VDIG) is a documented UVLO hazard in reference drivers; the front driver keeps that order (`imx471.c` `imx471_power_on`) | low | `docs/REAR_CAMERA_7_0_BRINGUP.md:128-130` |
| R13 | **Everything about the mechanism's envelope is hotdog's, not guacamole's**: course length, microstep ratio, boost policy, Hall labels/polarity and the 4-lane/0x10 electrical details all come from the sibling device. CLAUDE.md already documents one such trap for this board (touch power: PM8150L LDO17 + GPIO 119 on guacamole vs a different arrangement on hotdog) — that is the class of difference to expect | high, structural | [HOTDOG_GUACAMOLE_COMPARISON.md](HOTDOG_GUACAMOLE_COMPARISON.md) §2/§4: each inherited item has a named falsification step; nothing here may be treated as guacamole-verified without a measurement |

## 6. Ledger: verified vs not

**VERIFIED locally:** CSIPHY2 instance and rails; CCI1 as the front bus; TLMM
12/15, PM8150L GPIO12 and the BOB/LDO1/LDO3 power tree; the full DRV8834
GPIO/LPG/Hall map; LPG mapping (vendor index 1 → mainline channel 4); camss has
four PHYs and a full PHY↔CSID mesh; IMX471, mxm1120 and popup-motor drivers all
exist in the tree and are already built into rc4j (`=m`); the shipped rc4j DTB
has the Hall/LPG/camera-popup nodes but **no** `sony,imx471`; libcamera has no
IMX471 sensor data and its pinned patch is missing.

**UNVERIFIED:** the IMX471 identity itself (depends on the cited but absent
LineageOS `.dtsi`; §8.7 records what could be corroborated instead); the
sensor's I²C address 0x10 (not in the DT); whether front and ultra-wide can
share one CCI master (now *corroborated* rather than settled: master 0 is what
the merged stock tree and the sibling's working DT both use — see §8.3 and
[HOTDOG_GUACAMOLE_COMPARISON.md](HOTDOG_GUACAMOLE_COMPARISON.md) §2 D1).

**Inherited from hotdog and NOT independently verified for guacamole:** course
length 44160 µsteps, microstep ratio, ramp/probe lengths, the four Hall gate
windows, boost-always-on, Hall up/down labels and polarity, 4-lane 0x10 sensor
electricals, MCLK 19.2 vs the vendor's 24 MHz. Read every one as "a different
phone said so" — §5 R13, and the falsification step per item in the comparison
doc §4.

**SETTLED by §8 since:** the Hall thresholds do *not* transfer (R1/R2a) and the
closed values are now measured; PM8009 LDO3 exists at 1.056 V with
`allow-set-load`; `camera-popup`'s GPIO/LPG map matches the vendor DTBO; CSIPHY2
is unconnected (`port@2` absent); the three rear cameras are a clean regression
baseline (8 `/dev/video*`, no async errors).

**UNKNOWN:** any downstream driver source for `oneplus,step-motor` /
`oneplus-motor`; the IMX471 tuning content; its unit-cell size and exposure/gain
delays for `camera_sensor_properties.cpp`; frame rate (the driver's numbers give
≈81 fps, not the hand-off's "≈90", rc4 topic README §6 C18); and every hardware
behaviour — nothing in this material has run on a phone.

## 7. Recommended next step

Revised after the 2026-11-14 audit and device session (the original ordering
"do R6/R7 first" is obsolete: R6 is inverted and already mitigated, and step 1
below is done):

1. ~~Measure the two Hall channels~~ — **done, §8.2: `up = -10`, `down = -303`.**
   The rescale in §8.2 / R2a is now the gating work: `RESTORE_DOWN_MIN/MAX`,
   `RECOVER_DOWN_MIN/MAX`, and the open-side gates.
2. **One `pulse_up` (320 µsteps) with both Hall channels logged**, on a build
   with the motor enabled but `power-domains` **not** wired. This is the only
   legal move at the measured values, it is bounded by a count, and it teaches
   the direction/Hall-sign convention that every other threshold depends on. Have
   someone hold the phone; be ready to cut power (the `sleep-gpios` line is the
   motor enable). Note: with no genpd *consumer* in the tree this build cannot
   move anything by itself — the domain only acts once a sensor node references
   it, at which point the first runtime suspend drives a **full close course**
   and, per R2b, that course cannot terminate properly yet.
3. Settle CCI1 master 0 vs 1 (§8.3) **in parallel**: probe-only build
   (`CAMERA_POPUP_SENSOR`, motor node still disabled), `dmesg | grep imx471`, read
   register `0x0016` = `0x0471`. **Default to master 0** — it is what both the
   merged stock tree and the sibling's working DT use — and fix the rc4 DTSI's
   `cci1_i2c1` placement if it wins. Fall back to master 1 (the only CCI master
   proven on this phone today) if master 0 does not ACK.
4. Only then: full `open`/`close` courses with rescaled thresholds, and finally
   the `power-domains` coupling. Keep R7's rule throughout — never emit `port@2`
   without a sensor that probes, or every camera disappears.

## 8. Device measurements (2026-11-14, live rc4j, read-only)

Collected over SSH on the running phone (`7.0.0-sm8150-guacamole-rc4`, model
`SM8150 MTP 18821`, up 37 min). Nothing was enabled, loaded, written or moved;
this section **supersedes any conflicting UNVERIFIED item above**.

### 8.1 What the live DTB actually contains

| Fact | Value | How read |
|---|---|---|
| `camera-popup` node | **present**, `compatible = "oneplus,hotdog-popup-motor"`, `status = "disabled"` | `/proc/device-tree/camera-popup/*` |
| STEP PWM | `pwms = <&pm8150l_lpg 4 105000>`, `pwm-names = "step"`; `pwmchip0/npwm = 5` so channel 4 is valid | `od` on `pwms`, `/sys/class/pwm/pwmchip0/npwm` |
| pinctrl states | `pinctrl-names = "default", "pwm"` (the step GPIO/PWM swap is present) | same |
| GPIO decoding | `boost-enable-gpios` = PM8150B GPIO12, `direction-gpios` = PM8150L GPIO11, `mode1-gpios` = PM8150L GPIO8, `sleep-gpios` = PM8150 GPIO10, `step-gpios` = PM8150L GPIO10 | `od -Atu4` on each property (BE phandle/gpio cells) |
| Hall wiring | `io-channel-names = "hall-up", "hall-down"` | same |

The GPIO map matches the vendor DTBO decode in §2 exactly. **The `camera-popup`
provider node is complete — it needs `status = "okay"` plus the §8.2/§8.5
threshold fixes and nothing else.** The *sensor* side is a different story: no
`sony,imx471` node, no `port@2`, no CCI1 master-0 bus (§4 items 1–5).

### 8.2 Hall sensors are bound and readable — R1 is now measured, not guessed

`mxm1120` is loaded and both endpoints probe:

```
iio:device3  name=mxm1120  label=camera-popup-up    in_magn_raw = -10
iio:device4  name=mxm1120  label=camera-popup-down  in_magn_raw = -303
```

Stable across repeated reads and across the session (±1 count). Rails are up:
PM8150L LDO7 = 2.856 V, LDO8 = 1.8 V, both `enabled`.

**Caveat on those two numbers (added after the sibling comparison).** Hotdog's
Hall VDD is a 3.0 V rail (`vreg_l7c_3p0`); guacamole's DT asks for **2.856 V**
(`regulator-min/max-microvolt` bytes `00 2b 94 40` = 2,856,000 µV, confirmed in
the live DT). The sibling's own note warns that a Hall sensor at the wrong VDD
"still answers but its counts are not comparable to the hotdog thresholds".
Span ratio 303/369 = **0.82** against rail ratio 2.856/3.0 = **0.95** — the right
shape for a rail/magnet difference, so do not assume the counts are on the same
scale as hotdog's. This is why the rescale is mandatory rather than prudent.

**This contradicts the hotdog thresholds.** In
`hotdog-popup-motor.c:80-84`, `hotdog_popup_is_closed()` requires
`abs(up) < HOTDOG_CLOSED_UP_MAX (50)` **and** `abs(down) >=
HOTDOG_RESTORE_DOWN_MIN (340)`. On this unit `abs(down) = 303 < 340`, so
`is_closed()` returns **false at the fully closed position**. The exact
consequences (corrected after audit — the earlier text of this section named the
wrong paths):

* `hotdog_popup_open()` → `move_profile(true)` **refuses with `-ERANGE`**
  (`:307-309`), so the **full open course and the genpd auto-open are blocked**,
  and `motion_endpoint(false)` can never be reached.
* `pulse_up` is **not** blocked: it calls `hotdog_popup_preflight_up()`, which
  gates on `HOTDOG_CLOSED_DOWN_MIN (300)` / `CLOSED_UP_MAX (50)` (`:210-211`) and
  is satisfied by 303/10. So the only move available on this unit today is a
  bounded **320-microstep upward nudge** (`:567-585`).
* `close()` → `move_profile(false)` only needs plausible Halls, so it *will*
  drive down; but its settle gate is `SETTLED_DOWN_MIN (350)` (`:478`), so it
  reports failure at 303 even if the module seated correctly.
* `restore_closed()` **refuses in both profiles**: 320 µsteps needs `|down|` in
  340–368, 1920 µsteps needs 320–360 (`:247-255`). The documented escape hatch is
  dead at 303 — carried as R2a in §5.
* `HOTDOG_CLOSED_DOWN_MIN (300)` *is* used (`:210`), so it survives; the
  constants that must change are the four in R2a plus the open-side gates.

**Practical consequence:** before anything else, one `pulse_up` (320 µsteps) with
both Hall channels logged is the only legal move — it is bounded, terminated by a
count rather than by Halls, and it is the cheapest way to learn which direction
raises the module and how the Halls respond. Do **not** enable `power-domains`
first, because the genpd path calls the blocked full `open`.

R1 is therefore a **confirmed blocker**, not a risk:

* `HOTDOG_RESTORE_DOWN_MIN` must drop below 303 (measure the closed value on
  more than one unit before picking a bound; ~290 leaves margin).
* `HOTDOG_OPEN_DOWN_MIN (368)` / `HOTDOG_SETTLED_DOWN_MIN (350)` /
  `HOTDOG_FINISH_UP_MIN (130)` / `HOTDOG_FULL_OPEN_UP_MIN (300)` are
  **unmeasurable without a first move** — provisional until one bounded course
  has been run with the Hall channels logged. Note `open_down_min` is **dead
  code** (defined at `:28`, never referenced); the constants that actually bind
  the open path are `FULL_OPEN_UP_MIN` (`:41`, via `is_open`), `OPEN_HALL_MIN`
  (`:460`), `FINISH_UP_MIN` (`:443`) and `SETTLED_DOWN_MIN` (`:478`).
* Endpoint plausibility passes: `|up|,|down| <= 450` and `|up|+|down| = 313 >= 4`.

### 8.3 CCI/CSIPHY state — CSIPHY2 confirmed free, CCI master still open

| Fact | Value |
|---|---|
| camss ports in live DT | `port@0`, `port@1`, `port@3` only — **CSIPHY2 (`port@2`) is free**; no `sony,imx471` node exists in the booted DTB |
| `cci@ac4a000` (CCI0) | has **`i2c-bus@0`** and **`i2c-bus@1`** → `i2c-6` = s5k3m5@0x10, `i2c-7` = imx586@0x1a |
| `cci@ac4b000` (CCI1) | has **only `i2c-bus@1`** → `i2c-8` = ak7375@0x0c + imx481@0x1a (`8-000c`, `8-001a`). **There is no CCI1 master-0 bus in the DT at all.** |
| Which master is right | **master 0 is now corroborated twice**, independently of §1.3's merge argument: (a) the merged stock tree gives `qcom,cam-sensor@2 { cci-device = 0x01; cci-master = 0x00 }`; (b) the sibling device's *working* DT puts its IMX471 on `&cci1_i2c0` (boot log `imx471 6-0010`). **The rc4 topic DTSI's `cci1_i2c1` placement matches neither** — it matches the losing `fragment@47` copy, and its comment "exactly as stock does" is wrong. See [HOTDOG_GUACAMOLE_COMPARISON.md](HOTDOG_GUACAMOLE_COMPARISON.md) §2 D1. |
| Consequence | a front sensor on CCI1 **master 0** requires a brand-new `i2c-bus@0` node on TLMM 31/32 (it would enumerate next, i.e. `i2c-9`, but adapter numbering is probe-order dependent); the existing rc4 DTSI's `cci1_i2c1` placement would put the front sensor on the **ultra-wide's** bus. **Note this section does not settle master 0 vs 1** — it only proves the mainline DT has no master-0 bus. |
| Side observation | every existing CCI bus already carries a lens driver (`lens@74`, `lens@72`, `lens@c`); the planned `cci1 i2c-bus@0` would be the only lens-free, client-free CCI master — i.e. an untested silicon path |
| Rear-camera baseline (for regression) | 8 `/dev/video*`, `s5k3m5`/`imx586`/`imx481`/`ak7375` loaded, dmesg shows only the three known `Fixed dependency cycle(s)` lines and **four** `vdda-phy`/`vdda-pll` dummy-regulator notices (two per camss instance) — no async/registration errors |

### 8.4 Rails — §5 R9 resolved

`/proc/device-tree/soc@0/rsc@18200000/pm8009-rpmh-regulators/ldo3` exists in the
live DT with `regulator-min-microvolt = regulator-max-microvolt = 1056000`
(1.056 V), `regulator-allow-set-load` **and** `regulator-allowed-modes`:

```
regulator.33  ldo3  unknown  1056000     <- PM8009 LDO3, the front sensor's VDIG
regulator.10  ldo3  enabled  1200000     <- different PMIC, ignore
regulator.6   bob   enabled  3032000     <- PM8150L BOB = front VANA
```

So the merged-tree resolution (LDO3 @1.056 V, not LDO2 @1.2 V) is confirmed, and
the rail already carries the `allow-set-load`/`allowed-modes` properties that
§4 item 3 asks for.

**Scope of that closure: the rc4 baseline only.** `flows/rc4/topics/popup-camera`
emits the LDO3 block unconditionally, but `0030` in the repo-root recipe does
not describe PM8009 LDO3 at all — for Baseline B, §4 item 3 is still required.
(BOB reads 3032000 µV here, not the 3300000 of the sensor's `rgltr-max`, so treat
"VANA 3.3 V" above as the vendor's nominal figure.)

**And it is a choice, not a proof.** LDO3 is right because the merged tree wins
with it, because the sibling device's working tree uses `vreg_l3f_1p05`, and
because our topic DTSI emits it. What the device proves is only that the rail
exists at 1056000 µV with `allow-set-load`/`allowed-modes`. Note the landmine the
docs lacked: rc3/rc4 override PM8009 **L2** to ~1.056 V for the rear telephoto
actuator, so voting L2 for the front sensor would put two consumers on one rail.

### 8.5 Sensor driver is DT-ready, but two GPIOs are mandatory

`drivers/media/i2c/imx471.c` in the running tree has
`of_device_id imx471_of_match[] = { { .compatible = "sony,imx471", .data = "imx471" } }`
(`:1279-1284`, `.of_match_table` at `:1292`). Both `reset-gpios` (`:858`) and
`vana-enable-gpios` (`:866-870`) are fetched with the **non-optional**
`devm_gpiod_get()`, so the DT node must supply **both** or probe fails. §4's
snippet already has both; this note exists so nobody trims one as "optional".
(Upstream's driver has neither, which is why it cannot bind this board.)

### 8.6 What is still unknown after this session

Everything that requires moving the mechanism or enabling nodes: the open
endpoint Hall values, whether the IMX471 answers at 0x10 on CCI1 master 0, the
VDIG-vs-VANA sequencing behaviour on this board, the real frame rate, and
whether the genpd coupling blocks a stream start for ~1 s. §7 stands, with step 1
already done.

### 8.7 Independent audit of this document (three reviewers, 2026-11-14)

A read-only oracle re-derived every §8 claim from the phone and the kernel
sources: **16/16 confirmed** (one PARTLY, see below). A web auditor and a
consistency reviewer then found the following, all corrected in place:

| # | Was | Now |
|---|---|---|
| 1 | "the node is complete and only needs `status = "okay"`" | scoped to the `camera-popup` provider; the sensor side needs §4 items 1–5 |
| 2 | R6 claimed adding `cci1 i2c-bus@0` **creates** the crash shape | inverted: that shape is "a CCI instance missing a bus"; the live DTB is fragile **today** and adding the bus removes it. `0031` is in both recipes |
| 3 | `restore_closed` called the working escape hatch | it refuses at the measured 303 in both profiles → new R2a; recovery constants must be rescaled too |
| 4 | `docs/motor-procedure.md` | actual path `flows/rc4/topics/popup-camera/docs/motor-procedure.md` |
| 5 | "rear-camera highest 0031, 7.0 highest 0037" | 0037 and 0027; rc4 already uses 0038–0041 (next free 0074) |
| 6 | `is_closed()` cited at `:85-89` | `:80-84` (`:85-89` is `is_open()`) |
| 7 | "two vdda dummy-regulator notices" | four (two per camss instance) |
| 8 | "upstream libcamera does not list IMX471" | it lists **properties** only (unit cell 1400 nm); no gain helper, no tuning |
| 9 | R9 "closed, item 3 already done" | closed for rc4 only; Baseline B still needs §4 item 3 |
| 10 | §8.3 headed "§1.3 resolved" | master 0 vs 1 remains open; §8.3 only proves the DT shape — **second review then corroborated master 0 from the sibling's working DT and the merged stock tree (§8.8)** |

Two claims the oracle graded **PARTLY**: the `27`-byte chunk figure quoted in
the NFC doc (it is 28 — corrected there), and the note that `dmas` on `i2c9` is
fatal (conditional — see the NFC doc's corrected risk 1).

Still unverifiable from this checkout: the IMX471 identity's primary citation
(`sm8150-oem-camera-t0.dtsi` is not present; LineageOS commit `5ddc74f7`
names "IMX471 on OP7" and the vendor HAL ships
`com.qti.sensor.imx471.so` / `com.qti.tuned.sunny_imx471.bin`, which corroborates
but does not replace it).

### 8.8 Second review: comparison with the sibling device (2026-11-14)

A three-lane review re-derived everything against the OnePlus 7T Pro (`hotdog`)
bring-up — the phone this entire design descends from. Full record:
[HOTDOG_GUACAMOLE_COMPARISON.md](HOTDOG_GUACAMOLE_COMPARISON.md). What it
changed here:

| # | Was | Now |
|---|---|---|
| 1 | CCI master 0 was "a working hypothesis, UNVERIFIED" | **corroborated twice**: the sibling's working DT puts its IMX471 on `&cci1_i2c0` (master 0, boot log `imx471 6-0010`) and guacamole's merged stock tree says `cci-device=1, cci-master=0` for `cell-index 2`. The rc4 topic DTSI's `cci1_i2c1` matches neither — it follows the losing overlay copy and its "exactly as stock does" comment is wrong |
| 2 | R2a said the restore hatch is dead | also **R2b**: at 303 the *close course itself* cannot terminate (`motion_endpoint(false)` needs `>= 340`), so it runs all 44160 µsteps into the endstop and returns `-ERANGE` before the re-seat branch is ever reached |
| 3 | Hall 2.856 V presented as authoritative | **caveat added**: hotdog's Hall rail is 3.0 V and its own note says counts are not comparable at the wrong VDD. Span ratio 0.82 vs rail ratio 0.95 — the rescale is mandatory, and the constants are per-board, not just per-unit |
| 4 | R9 "§5 closed, item 3 done" | VDIG LDO3 is a *choice* justified by merge precedence + sibling precedent, not an independent confirmation; plus the rc3/rc4 L2-for-telephoto conflict that makes voting L2 actively harmful |
| 5 | No general "sibling may differ" risk | **R13** added, using CLAUDE.md's own touch-power example as the precedent |
| 6 | `IMX471_ID` cited at `:29-30` | `:31` (introduced by the first audit's own fix) |
| 7 | R2's stall citation `:281-312` | the real abort paths: ramp/probe `:340-410`, ceiling `:414`, endpoint `:277-283` |
| 8 | §7 step 2 implied a motor-enabled build is inert | true only while no genpd *consumer* exists; the first sensor runtime suspend drives a full close course, which R2b then blocks |

Also confirmed by this lane, so it does not need repeating as a risk: our
`0028-media-camss-sm8150-rebase.patch` already contains the sibling's entire
CAMSS dependency chain (`CAMSS_8150`, four CSIPHYs, `lane_regs_sm8150_cphy`,
camnoc/QoS, stream-unwind). The camera-side reuse verdict holds on evidence, not
just on inheritance.

**The one sibling fact that should accelerate this work:** hotdog's popup motor
also runs off PM8150L LPG **channel 4** with the same 105000 ns slow period, and
its five motor GPIOs name the identical PMIC pins we decoded from guacamole's
vendor DTBO. The mechanism really is the same design; only the *mechanical
envelope* (course length, Hall geometry, Hall rail) is per-board.
