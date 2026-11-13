# Pop-up camera: what changes for rc4 (7.0 board, static DT)

`docs/motor-procedure.md` is the original, byte-identical 6.17 procedure; it is
still the safety script. This file lists only what differs in rc4, and what is
*worse* than the original material claims. Nothing here has been run on a
phone. No measurement exists yet.

## 0. State after rc4 integrates this topic

| Piece | Built | In the shipped DTB | Can move the module |
| --- | --- | --- | --- |
| `mxm1120` (Hall) | `CONFIG_MXM1120=m` | `okay` on the SE1 bus | no |
| `imx471` (front) | `CONFIG_VIDEO_IMX471=m` | **node not emitted** | no |
| `hotdog-popup-motor` | `CONFIG_VIDEO_QCOM_HOTDOG_POPUP=m` | **`disabled`** | no |
| `pm8150l_lpg` (step PWM) | `CONFIG_LEDS_QCOM_LPG=m` (already) | `okay` | no (no consumer) |
| `qupv3_id_0` + SE1 | `CONFIG_I2C_QCOM_GENI=y` (already) | `okay` | no |
| CAMSS CSIPHY2 endpoint | rc3 camss | **not present** | n/a |

So the rc4 boot gains three inert things: an I2C bus with two Hall sensors that
answer polling reads, a PWM chip with an unused channel, and a disabled motor
platform node. The camera graph (CAMSS async connections, pad links, video
nodes) is bit-for-bit rc3's. If `mxm1120` does not bind, nothing else is
affected.

The DT fragment has switches (`-D` at DTS build time, or `#define` above the
`#ifdef`s):

| Switches | Front sensor node | CAMSS port@2 | Motor node | Sensor power domain |
| --- | --- | --- | --- | --- |
| (none) | absent | absent | `disabled` | n/a |
| `CAMERA_POPUP_MOTOR` | absent | absent | `okay` | n/a |
| `CAMERA_POPUP_SENSOR` | `okay` | present | `disabled` | absent |
| both | `okay` | present | `okay` | `<&camera_popup>` |

**The sensor node and the CAMSS endpoint are inseparable by design.** CAMSS
queues one v4l2-async connection per endpoint and never checks whether the
remote device is available; its `.complete` callback — the only place that makes
the sensor↔CSIPHY links and registers the `/dev/video*` nodes — runs only when
every queued connection is bound. An endpoint whose sensor is disabled or fails
to probe therefore disables **all** cameras, including the three rear ones. That
is why there is no "sensor node present but disabled" state any more.

## 1. Step 1 of the procedure (Hall readings) works as written, with two changes

* The bus is `i2c1` (`i2c@884000`, QUPv3 SE1) at 100 kHz, enabled by this
  fragment. Adapter numbers are not stable; find the devices as
  `/sys/bus/i2c/devices/*-000c` and `*-000d`, or by IIO label. The IIO labels stay
  `camera-popup-up` / `camera-popup-down`.
* The supplies are PM8150L LDO7 (3.0 V) and LDO8 (1.8 V). They are enabled by
  the driver, but their **voltage is never set by software**: it is whatever the
  boot chain left in RPMH. Before trusting a reading, record
  `cat /sys/kernel/debug/regulator/regulator_summary | grep -E 'ldo7|ldo8'`
  (or the `/sys/class/regulator/*/microvolts`) and compare with stock
  (3.0 V / 1.8 V). A Hall sensor at the wrong VDD still answers but its counts
  are not comparable to the hotdog thresholds.

## 2. Step 2/3 (one bounded pulse, full courses) needs a dedicated DTB

There is no runtime overlay in rc4. Build the DTB with
`-DCAMERA_POPUP_MOTOR` (e.g. `KCFLAGS`-style extra DTS flags or a `#define` at
the top of the fragment); that turns the motor node `okay` and leaves the front
sensor out of the tree entirely. The driver does not move anything at probe
(`pm_genpd_init(..., is_off = false)` + `hotdog_popup_stop()`), but its first
*runtime suspend* runs a closing course, which is a no-op while the module is
already closed.

Rebuild the DTB (`make ... qcom/sm8150-oneplus-guacamole.dtb`), deliver it the
same way the rest of rc4 is delivered, boot, and follow
`docs/motor-procedure.md` from Step 2.

The sysfs attribute values are unchanged: `pulse_up` wants `1` (320 microsteps),
`restore_closed` wants `1` (320) or `1920`, `open`/`close` want the course limit
`44160`, `finish_open` wants `12480`. Anything else returns `-EINVAL`, and each
attribute works **once per module load** — reload the module for a second try
(`rmmod` is safe at that point: no camera is streaming).

## 3. Step 4 (sensor, then automatic lifecycle)

* Sensor only, no motor: build with `-DCAMERA_POPUP_SENSOR` (and
  `CAMERA_WIDE`, which the full board always has). The sensor node is emitted
  with `status = "okay"` and **no** `power-domains`, so it probes and binds, the
  CAMSS notifier completes, and the rear cameras keep their `/dev/video*` nodes.
  This is the build to use for the VDIG-rail question: look for
  `imx471 ...-0010` and chip ID `0x0471` in dmesg.
* Automatic lifecycle: build with `-DCAMERA_POPUP_SENSOR -DCAMERA_POPUP_MOTOR`.
  The sensor then defers until the motor's power domain exists and opens the
  module on runtime resume. Expect one closing course on the first runtime
  suspend after boot; that is the driver establishing "closed", not a bug.

> **All-or-nothing warning.** In any `CAMERA_POPUP_SENSOR` build the front
> sensor shares CAMSS's single async notifier with the rear sensors. If it does
> not bind — wrong VDIG rail, deferred probe because the motor node is disabled
> while `power-domains` is still present, or an I2C NAK — the notifier never
> completes and **the rear cameras lose their video nodes too**. Check
> `dmesg | grep -iE 'imx471|camss'` and run
> `checks/camss_async_audit.py <dtb>` (it lists every awaited connection and
> flags unbindable ones) before concluding a rear camera broke.

## 4. Open risks, in the order they bite

0. **A sensor build can take the rear cameras down with it.** See the warning in
   §3: CAMSS waits for every endpoint it can see. Nothing in this fragment can
   emit an endpoint without a sensor, but a sensor that fails to *probe* (rail,
   MCLK, I2C, missing power domain) has the same effect. Ship the default DTB.
1. **VDIG rail is not settled.** The hotdog driver tree and
   `Patches/popup-camera` say PM8009 **LDO3** at 1.056 V. The stock guacamole
   DTBO says the front sensor's `cam_vdig` is `pm8009_l2` at **1.2 V** (and the
   same `cam_vdig` for the rear main/wide sensors). rc4 describes LDO3 at
   1.056 V because that is what the only working IMX471 tree uses. If the sensor
   answers at 0x10 but fails its chip-ID read, suspect this first. Do not "fix"
   it by moving the rail to LDO2 while the telephoto is enabled: rc3 overrides
   LDO2 to 1.056 V for the rear tele, so the two votes would fight.
2. **MCLK.** The mainline driver calls `clk_set_rate(inclk, 19200000)` and
   refuses to probe on any other rate, so the 19.2 MHz in the fragment is
   enforced by code, not just DT. Stock's DTBO asks for 24 MHz
   (`clock-rates = <0x16e3600>`); that is downstream-only and wrong for this
   driver's PLL/mode tables. If probe fails with "inclk frequency mismatch",
   the camcc rate is the problem, not the DT value.
3. **Hall thresholds are one 7T Pro's.** Every `#define` in
   `hotdog-popup-motor.c` (300/50 closed, 340-368 restore, 350 settled,
   368 open, 150/300 open hall, 450 plausibility, 44 160 ceiling) is measured on
   a different unit. `status` prints the values the driver used and the values it
   read, so Step 1 can decide before Step 2.
4. **Free-fall retraction is not implemented.** TLMM163 is untouched; there is
   no IRQ in the driver. A dropped, raised module has no protection. This is the
   main hardware risk and it is *not* solved by this topic.
5. **Process death is not proven.** The only mechanism is runtime PM: the last
   close of the camera fd suspends the sensor, whose power domain then closes the
   module. If a process dies in a way that leaks the fd (or CAMSS keeps the
   subdev busy after a failed start), the module stays up. Test by killing the
   app mid-stream; if it stays up, `echo 1 > close` is the manual fallback, one
   shot per module load.
6. **Suspend with the camera stack loaded is untested** on 6.17 *and* here. Do
   not attribute the rc3 lock-up crash to this topic: the user's crash happened
   before any popup code was in the image, and suspend/resume remains a
   hypothesis for another topic.
7. **CAMSS recovery after a failed stream start** (CSID/VFE reset timeouts until
   reboot) is unaddressed. Once the front camera is enabled, a failed start may
   cost a reboot.

## 5. What the original README gets wrong (kept here so it is not re-derived)

* "CCI1 master 0 at 0x10": stock says `cci-master = <1>`, and the rear ultra-wide
  (hardware-verified on this unit) shares that master. The fragment uses cci1
  master 1, address 0x10, next to the ultra-wide's 0x1a.
* "0036 applies with `patch` on 7.0": it does on vanilla 7.0, not on rc3 — the
  rear-camera series inserted IMX481/IMX586 into the same Kconfig hunk.
* "about 90 fps": the single mode's own timing is 81.3 fps
  (`pclk 420.21 MHz / 2560 / 2018`).
* "MCLK2 19.2 MHz" is right for the driver and wrong for stock's DTBO (24 MHz);
  the driver wins because it sets the rate itself.
