# rc4 topic package: pop-up front camera (guacamole)

Status: **source-only integration candidate.** The three drivers are rebased onto
the actual rc3 tree, compile cleanly against the rc3 kernel config, and the DT
fragment compiles and provably changes nothing outside its own nodes. Nothing
here has run on a phone. **The motor node is `status = "disabled"` and the front
sensor node plus its CAMSS endpoint are not emitted at all, so after this topic
is merged no code path can move the module, power the sensor, or alter the
camera graph the three rear cameras use.**

Baseline: `device/testing/rc3/linux-postmarketos-qcom-sm8150` (7.0.0-sm8150-rc3,
0001–0041) applied to `linux-v7.0.0-sm8150`. Target release name:
`7.0.0-sm8150-guacamole-rc4`.

```
patches/   0052 Hall sensors (IIO)       rebased, 2 hunks moved
           0053 IMX471 front sensor      rebased, 1 hunk moved
           0054 pop-up motor + PM domain applies as supplied
dt/        sm8150-oneplus-guacamole-popup.dtsi   static board fragment
fragments/ config.fragment, APKBUILD.sources.txt, guacamole-dts-include.txt,
           rear-dtsi-cci1-gate.diff (optional), 
docs/      motor-procedure.md (byte-identical original), rc4-device-notes.md
src/       the three drivers as the patches leave them, for reading
checks/    run_all.sh, static_checks.py, dtb_regression.py,
           camss_async_audit.py, toolshim/bc, logs/, artifacts/
```

## 1. How to merge this into rc4

1. Create the rc4 recipe (`device/testing/rc4/linux-postmarketos-qcom-sm8150`)
   from rc3. Copy `patches/0052-…`, `0053-…`, `0054-…` into it next to 0041.
2. Add the three filenames to `source=` — see `fragments/APKBUILD.sources.txt`.
   Order matters only in the sense that they must come after the rc3 series;
   they do not depend on each other.
3. Append `fragments/config.fragment` to the rc4 kernel config
   (`CONFIG_MXM1120=m`, `CONFIG_VIDEO_IMX471=m`,
   `CONFIG_VIDEO_QCOM_HOTDOG_POPUP=m`). Every Kconfig dependency is already
   satisfied by the rc3 config (checked, §6 C2).
4. Set the release name. For `uname -r` = `7.0.0-sm8150-guacamole-rc4`:
   `CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"` (rc3 has `-sm8150-rc3`). The
   APKBUILD `pkgver` stays `7.0.0` and `_tag` stays `v$pkgver-sm8150`; do not
   rename or reuse any rc3 artifact.
5. Copy `dt/sm8150-oneplus-guacamole-popup.dtsi` to
   `arch/arm64/boot/dts/qcom/` and append the one include line from
   `fragments/guacamole-dts-include.txt` to the end of
   `sm8150-oneplus-guacamole.dts`.
6. If you want the release notes to link the procedure, keep `docs/` with the
   release documentation. Nothing in `docs/` or `checks/` ships in the APK.
7. Rebuild and re-run `checks/run_all.sh` from this directory (about 20 s with
   reflinks) before packaging. It uses the read-only 7.0 tree and never touches
   the phone.

What the user gets at the end of step 5, on a booted rc4: two Hall sensors on
the QUPv3 SE1 bus with IIO labels `camera-popup-up`/`camera-popup-down`, an
unused PWM channel, an LDO3 regulator with no consumer, and one disabled
platform node (the motor). **No motor probe, no front-sensor node, no CAMSS
endpoint, no change to the camera graph.** The rc4 full DTB carries exactly the
three CAMSS async connections rc3 has (checked, §7). See
`docs/rc4-device-notes.md` for the measurement steps and the `-D` switches that
add each piece back for a measurement build.

## 2. Rebase: exactly what was changed

Applied the supplied patches to the rc3-patched files with `git apply --check`
(`checks/logs/apply-original-rc3.log`) and `patch --fuzz=0`. Result:

| patch | file | rc3 | action |
| --- | --- | --- | --- |
| 0035 | `drivers/iio/magnetometer/Kconfig` | **reject** | inserted after `MMC5633` (7.0 added it between `MMC35240` and `IIO_ST_MAGN_3AXIS`, which the patch quoted as adjacent) |
| 0035 | `drivers/iio/magnetometer/Makefile` | **reject** | inserted after `obj-$(CONFIG_MMC5633)` for the same reason |
| 0035 | `vendor-prefixes.yaml`, `magnachip,mxm1120.yaml`, `mxm1120.c` | clean | unchanged (vendor-prefixes applies at offset +58) |
| 0036 | `drivers/media/i2c/Kconfig` | **reject** | inserted after `VIDEO_IMX412`, before `VIDEO_IMX481` (rc3 patch 0029 added IMX481/IMX586 between IMX412 and IMX415) |
| 0036 | `drivers/media/i2c/Makefile`, `imx471.c` | clean | unchanged (Makefile at offset +2) |
| 0037 | all four files | clean | byte-identical to the supplied patch |

**No driver code was modified.** The rejected hunks are kept in
`checks/logs/rejects/`. Renumbered 0035/0036/0037 → **0052/0053/0054**; rc3's
highest is 0041 and 0042–0051 are reserved for the other rc4 topics.

The rebased patches carry a `rc4 rebase on v7.0.0-sm8150-rc3` paragraph naming
the conflict, so the next person does not have to rediscover it.

## 3. DT integration decisions

The supplied material is an overlay for omarchy-mobile's 6.17 runtime-overlay
tree (`guacamole-camera.dts` with `-DCAMERA_*` slots plus a
`build_camera_overlay.sh` variant). rc4 has neither: the board is described by a
static `dtsi` chain and there is no overlay loader. So the overlay was rewritten
as one static fragment:

* **Outer gating.** Everything is inside `#ifdef CAMERA_POPUP_FRONT`, which is
  auto-defined only when `CAMERA_ISOLATE` is *not* set. Consequence: **the
  rear-main, rear-tele and rear-wide isolate DTBs stay byte-identical to rc3**
  (checked with `cmp`, §6 C16). The overlay's `build_camera_overlay.sh` change
  (new `camera-front` variant) has no rc4 equivalent and is dropped.
* **Default content is inert.** The Hall sensors (SE1), `qupv3_id_0`, the LPG,
  the motor pin states, PM8009 LDO3 and a *disabled* `camera-popup` node are
  described. The front sensor node and its CSIPHY2 `port@2` are **not emitted**
  (see the next bullet). Nothing here touches the camera graph or actuates.
* **Sensor and CAMSS endpoint are gated together, off by default.** They are
  emitted only under `-DCAMERA_POPUP_SENSOR`, and the sensor is `status =
  "okay"` whenever they are. This is not cosmetic: `camss_parse_ports()` adds a
  v4l2-async connection per CAMSS endpoint without checking whether the remote
  device is available, and `camss_subdev_notifier_complete()` — the only caller
  of `v4l2_device_register_subdev_nodes()` and of the sensor↔CSIPHY pad links —
  runs only when every connection is bound. An endpoint whose sensor is
  disabled therefore kills **all** cameras, rear ones included. Full trace and
  evidence: §6 C22, `checks/logs/camss-async-audit-prefix.log`. The build
  switches are: default (nothing), `CAMERA_POPUP_MOTOR` (motor node okay, no
  sensor), `CAMERA_POPUP_SENSOR` (sensor + endpoint, no power domain),
  both (sensor raises the module). `CAMERA_POPUP_SENSOR` without `CAMERA_WIDE`
  is a compile error.
* **Motor `status = "disabled"` unless `CAMERA_POPUP_MOTOR`.** Beyond the
  status, the driver cannot move anything at probe (`pm_genpd_init(..., is_off =
  false)` plus `hotdog_popup_stop()`), but its first runtime suspend runs a
  closing course, so an enabled node is still an action on the mechanism.
* **Hall sensors and the LPG are enabled.** They are read-only in practice and
  are exactly what Step 1 of `docs/motor-procedure.md` needs. Enabling
  `pm8150l_lpg` registers a 5-channel PWM chip; nothing consumes channel 4 while
  the motor node is disabled.
* **`qupv3_id_0` is enabled**, which the supplied material does not do. SE1
  (`i2c@884000`) sits under `geniqup@8c0000`, and that wrapper is `disabled` in
  `sm8150.dtsi`; without this line `status = "okay"` on `i2c1` does nothing.
* **`dmas`/`dma-names` are deleted from `i2c1`** so geni-i2c uses the FIFO path
  (the same one the touch bus uses); SE1 has no other user on this board.
* **CSIPHY2 `port@2` is added to `camss` only in a `CAMERA_POPUP_SENSOR` build**
  — the rear series only wired ports 0, 1 and 3, so this is the one free PHY.
  `camss_parse_ports()` takes `csiphy_id` from the port number, so port@2 →
  CSIPHY2, matching stock.
* **IMX471 sits on cci1 master 1 at 0x10**, next to the rear ultra-wide at 0x1a.
  Stock says `cci-master = <1>` for the front sensor. The supplied README's
  "CCI1 master 0" is wrong (see §6 C7).
* **PM8009 LDO3 (1.056 V) is added** as `vreg_l3f_1p05`, with
  `regulator-allow-set-load` and the LPM/HPM mode list. Without them the
  driver's 1.05 A load vote is silently dropped (not an error — the regulator
  core returns 0 when DRMS is not allowed).
* The front sensor needs cci1, which rc3 instantiates only under
  `#ifdef CAMERA_WIDE`; `CAMERA_POPUP_SENSOR` therefore requires it and fails the
  build otherwise. The full build always has CAMERA_WIDE.
  `fragments/rear-dtsi-cci1-gate.diff` is the optional one-line widening for a
  future popup-only isolate.
* The fragment lives at the end of `sm8150-oneplus-guacamole.dts` because it
  extends `camss`, which the rear dtsi defines, and keys off the `CAMERA_*`
  switches that the rear dtsi sets.

## 4. Safety review of the motor driver

The task asked for a review of bounded timeouts, calibration, failure unwind,
process death and free-fall. Findings, all source-level:

**Bounded (good).** Course ceiling `1380 * 32 = 44160` microsteps (0.58 s at
13.125 µs); slow ramp 960; endpoint dead-zone probe 3200; preflight 320; settle
320; recovery 1920; Hall poll every 15 ms with a 25 ms per-read IIO timeout;
`HOTDOG_HALL_ABS_MAX = 450` rejects implausible readings mid-course. Every
sysfs movement attribute accepts only its exact step count and works once per
module load, which matches the one-step-per-load procedure.

**Calibration is the weak point.** All thresholds are `#define`s measured on one
7T Pro, compiled in. There is no DT-based calibration and no way to run a course
without matching those windows: `open` requires `|down| >= 340` and
`|up| < 50` to even start. If this unit's closed baseline is outside the window,
`open` returns `-ERANGE` and the mechanism never moves — safe, but it means
Step 1 must produce numbers comparable to the hotdog table before Step 2 can do
anything.

**Failure unwind (good).** `hotdog_popup_stop()` runs on every exit path of
`drive_exact()` and `move_profile()`; a failed automatic open runs a bounded
close before returning the error; `close()` retries once on `-EPROTO` (the
LPG/DRV8834 pair can ignore the first ramp); the PM-domain `power_on` error
propagates to the sensor's runtime resume, so a failed raise fails the stream
instead of silently streaming with the module down.

**Process death (unproven mechanism).** Nothing watches the camera process. The
module comes down only because the last close of the sensor fd lets the IMX471
runtime-suspend, which calls the PM domain's `power_off`. If CAMSS holds the
subdev (for example after a failed stream start) the module stays up. There is
no timeout and no watchdog. This is the open item the original material lists
and it is still open; `echo 1 > close` on `camera-popup` is the manual recovery,
once per load.

**Free-fall (not implemented).** TLMM163 (`motor_pl` in stock) is deliberately
left unconfigured: the driver has no IRQ path and no bounded-retract-on-drop
behaviour. Nothing in this package reduces the risk of a raised module hitting
the floor. Adding it is a real code change (IRQ + a close-only state machine
that must not fight an in-flight course) and was out of scope for a source
rebase; it is recorded as blocked.

**Other review notes.**

* Movement (up to ~0.6 s) happens inside a genpd `power_on`/`power_off`
  callback while holding the driver mutex. genpd callbacks may sleep and the
  domain has one consumer, so this is legal, but it means a stream start can
  block for a second and a system suspend can be delayed by a closing course.
* The STEP pad is claimed twice: as a GPIO (`step-gpios`, idle state) and as an
  LPG PWM (`pwms`), with the pad muxed by `pinctrl-0`/`pinctrl-1`. That is the
  hotdog pattern and the only way to idle the pad low, but it is exactly the
  kind of thing that earns a "pin already requested" failure on real hardware.
  No change made without hardware.
* `boost-enable` is driven high at probe and never released (matches OxygenOS).
  On this board that is PM8150B GPIO12, which the charger/torrent code does not
  use, so there is no interaction with the rc3 charger work — but it does mean
  the boost converter is on from probe until the node is disabled or the module
  is unloaded.
* The compatible stays `oneplus,hotdog-popup-motor`. The hardware is the same
  wiring and the thresholds are hotdog's; inventing a `guacamole` compatible
  would imply measurements that do not exist.

## 5. What this package does *not* claim

* Not run on hardware. No Hall readings, no probe logs, no stream.
* No FPS, suspend, process-death or free-fall result.
* The user-reported rc3 lock-up is **not** addressed or diagnosed here; the
  popup code was not in that image. Suspend/resume remains a hypothesis owned by
  the suspend topic.
* The rear cameras are not "re-tested" — they are proven *unaffected at DT
  level* (§6 C16); their runtime series is rc3's, unchanged.

## 6. Claim / evidence / verdict

`V` = verified here, `C` = contradicted, `U` = unverified (cannot be settled
without hardware or a source we do not have). Commands are reproducible from
this directory.

| # | Claim (supplied material) | Evidence / command | Verdict |
| --- | --- | --- | --- |
| C1 | "On 7.0, 0036 and 0037 apply with `patch`; only 0035's Kconfig+Makefile conflict" (`README.md` "What was verified") | `patch --dry-run -p1 --fuzz=0` (and `git apply --check`) on a pristine subset of the rc3-patched files → `checks/logs/apply-original-rc3.log`: 0035 Kconfig+Makefile fail, **0036 Kconfig fails**, 0036 Makefile + 0037 clean | **C** (0036 also needs a rebase on rc3; true only for vanilla 7.0) |
| C2 | "Kconfig dependencies are all set: IIO, PWM, REGMAP_I2C, OF_GPIO, PM_GENERIC_DOMAINS_OF, LEDS_QCOM_LPG=m" | `grep` in the rc3 config: `CONFIG_IIO=y` (7330), `CONFIG_PWM=y` (7972), `CONFIG_REGMAP_I2C=y`, `CONFIG_OF_GPIO=y`, `CONFIG_PM_GENERIC_DOMAINS_OF=y`, `CONFIG_LEDS_QCOM_LPG=m` | V |
| C3 | "GN has no camera foundation; add CAMSS, CCI1, camcc, PM8009" (Phase 0) | rc3 config `CONFIG_SM_CAMCC_8150=y`, `CONFIG_I2C_QCOM_CCI=m`; rc3 DT already has camss/cci0/cci1/pm8009; user confirmed all three rear cameras work | already present (Phase 0 is done; only CSIPHY2/cci1-master1 and LDO3 were missing) |
| C4 | "The provider starts logically powered, so probing doesn't move the mechanism" | `hotdog-popup-motor.c`: `pm_genpd_init(&popup->genpd, NULL, false)` and `hotdog_popup_stop()` before provider registration | V (code) |
| C5 | "Hall thresholds measured on one 7T Pro" | `#define`s 300/50/340/368/350/1920/320-360/368/44160/130-200/50/150/450/300 | V (as documented); unit-specific values U |
| C6 | "LPG channel 5 … `pwms` index 4" | `leds-qcom-lpg.c` `pm8150l_lpg_data` channels = `0xb100,0xb200,0xb300,0xbc00,0xbd00`; stock DTBO `qcom,pwms@bc00` has `qcom,num-lpg-channels = <2>` and `pwms = <0xa9 0x01 …>` → stock index 1 = `0xbd00` = mainline index 4 | V |
| C7 | "Sony IMX471, CCI1 master 0 at 0x10" | stock DTBO `qcom,cam-sensor@2`: `cci-master = <0x01>`, `csiphy-sd-index = <2>`; rc3 main IMX586 already uses 0x10 on cci0 master 1, so the front cannot share that master; hardware-verified rear mapping puts CSIPHY2/3 on cci1 | **C** (master 1; the supplied overlay already did it right) |
| C8 | "MCLK2 on TLMM 15 (19.2 MHz)" | stock DTBO `clock-rates = <0x16e3600>` = 24 MHz; `imx471.c` calls `clk_set_rate(inclk, 19200000)` and fails probe unless `clk_get_rate` returns 19.2 MHz | V for the driver (19.2 MHz is enforced in code and valid — the rear IMX481 runs 19.2 MHz on this camcc); stock's 24 MHz is downstream-only |
| C9 | "VDIG = PM8009 LDO3 1.056 V" | stock DTBO maps `cam-sensor@1/2/3` `cam_vdig` to `pm8009_l2` and gives `@2` `rgltr-min-voltage` `0x124f80` = 1.2 V; rc3 uses LDO2 for the tele VDIG and redefines it to 1.056 V | **U / weak** — two candidate rails, and LDO2 would fight the tele vote. Kept LDO3 (only working IMX471 tree), sensor stays disabled |
| C10 | "Hall sensors on QUP SE1, 0x0c upper / 0x0d lower, PM8150L L7/L8, IRQ 120/121" | stock DTBO `fragment@105` `magnachip@0C/0D` (`reg`, `dhall,irq-gpio = 0x78/0x79`); stock `qupv3_se1_i2c_active` = `gpio114/115`, `function = "qup1"`, `bias-disable` = upstream `qup_i2c1_default` | V (the DTBO target phandle is unresolved in the dump; the pin group + hotdog log agree) |
| C11 | "Bounded courses, Hall-terminated, restore after close" | see §4; `checks/static_checks.py` asserts the constants and the unwind paths | V (code) |
| C12 | "Free-fall retraction: add it to the motor driver" (Phase 4) | driver has no IRQ; TLMM163 absent from the fragment | **not implemented** (blocked, not enabled) |
| C13 | "Kill the app mid-stream and check the module comes down" (open item) | mechanism is runtime PM only; no watchdog | U |
| C14 | "0035–0037 apply with `git am` on 6.17" | no 6.17 tree in scope here | not evaluated (out of scope) |
| C15 | "After a failed stream start, CAMSS CSID/VFE resets time out until reboot" | not reproducible at source level | U |
| C16 | (task) "Preserve all rear cameras" | `checks/run_all.sh` step 4: rear-main/tele/wide DTBs byte-identical (`cmp`); full DTB `dtb_regression.py` (with `/soc@0/camss@acb3000` and both cci nodes as forbidden paths) → 14 new nodes, 8 intended property changes, no camera-graph node; `camss_async_audit.py` → the baseline's 3 connections, all bindable | **V** |
| C17 | (task) "no actuation" | motor node `status = "disabled"` by default; the front sensor node and its CAMSS endpoint are not emitted; `static_checks.py` preprocesses the fragment for every switch combination and asserts this; the driver has no manual-instantiation path | V (source/DT) |
| C18 | "IMX471 … about 90 fps", "one 1748×1748 RAW10 mode", link 525.2625 MHz / 4 lanes | `supported_mode`: 1748×1748, `pclk 420210000`, `hblank 812`, `vblank 270` → 81.3 fps; `IMX471_LINK_FREQ 525262500`, 4 lanes → 4.2021 Gbps / 4 lanes / 2 = 525.26 MHz | mode and link V; **fps C** (≈81, not ≈90) |
| C19 | "IMX471 driver follows OxygenOS supply order, runtime PM; motor hooks a PM domain" | `imx471_power_on()`: set VANA, VDIG load, VIO, VANA, vana-enable GPIO, VDIG, reset, MCLK; balanced `SET_RUNTIME_PM_OPS`; `power-domains` on an I2C client is attached by `i2c_device_probe()` | V (code) |
| C20 | "pm8150l_lpg must be okay for the step PWM" | `pm8150l.dtsi` marks it `disabled`; `lpg_probe()` always adds the pwmchip, no child LEDs needed | V (source) |
| C21 | README: "the camcc node is okay but its driver isn't built" (GN 6.17) | rc3 config has `CONFIG_SM_CAMCC_8150=y` | true for the old 6.17 checkout, **not for rc4** |
| C22 | (parent) "does CAMSS skip a disabled remote endpoint, or stall the whole notifier?" | Trace: `camss.c:4672 camss_parse_ports()` adds one connection per endpoint via `v4l2_async_nf_add_fwnode_remote()`; `v4l2-async.c:735 __v4l2_async_nf_add_fwnode_remote()` resolves `remote-endpoint` with no availability test and `:714 __v4l2_async_nf_add_fwnode()` queues it; `v4l2-async.c:281-287 v4l2_async_nf_try_complete()` returns 0 early while `waiting_list` is non-empty; a `status="disabled"` i2c node is never instantiated (`i2c-core-of.c:99 for_each_available_child_of_node()`), so it never binds; `camss.c:5007-5044 camss_subdev_notifier_complete()` is the only caller of `v4l2_device_register_subdev_nodes()` and the only creator of sensor pad links. Measured on the pre-fix DTB: `checks/camss_async_audit.py checks/artifacts/pre-fix/harmful-full.dtb` → 4 connections, port@2 `status=disabled bindable=NO`, exit 2 (`checks/logs/camss-async-audit-prefix.log`) | **Harmful — it stalls, nothing is skipped.** The rear sensors still probe and bind; what never happens is the completion step (pad links + `/dev/video*` nodes), so every camera becomes unusable. Fixed by emitting sensor+endpoint only together under `-DCAMERA_POPUP_SENSOR`; the shipped full DTB now has the baseline's 3 bindable connections (asserted in `run_all.sh`) |
| C23 | config fragment: "keep them as modules [so a broken probe cannot touch the camera stack at boot]" | A module is autoloaded from its `of:`/`i2c` modalias once a device exists; a disabled node creates no device. `CONFIG_*=m` therefore changes nothing about boot-time probing | **C** — reworded: the DT `status`/node presence is the gate, `=m` is only about not shipping built-ins |

## 7. Checks actually run

`bash checks/run_all.sh` (rootless, ~20 s, reflinks the read-only tree into
`checks/scratch/` and deletes nothing outside this directory):

| Step | Result | Log |
| --- | --- | --- |
| `git apply --check` + `git apply` of 0052–0054 on the rc3-patched sources | clean, no fuzz | `checks/logs/apply-git.log` |
| `patch -p1 --fuzz=0` of the same series | clean, no `.rej`/`.orig` | `checks/logs/apply-patch.log` |
| both methods produce the same tree | identical `sha256sum` lists | `checks/logs/apply-trees-equal.log` |
| compile `mxm1120.o`, `imx471.o`, `hotdog-popup-motor.o` (clang 23, `LLVM=1`, rc3 `.config` + 3 new symbols, `W=1`) | 3 objects, 0 diagnostics from the patched sources | `checks/logs/compile-w1.log`, `checks/artifacts/*.o` |
| build full + 3 rear-isolate DTBs | 4 DTBs, no dtc diagnostics | `checks/logs/dtb-build.log` |
| DTB regression, with `/soc@0/camss@acb3000` and both cci nodes declared **forbidden** for new nodes | full: 14 new nodes + 8 intended changes, no camera-graph node; isolates byte-identical | `checks/logs/dtb-regression.log` |
| CAMSS async audit: the full DTB's awaited-sensor set equals the rc3 baseline, and no connection is unbindable | 3 connections, identical, all bindable | `checks/logs/dtb-regression.log` |
| 54 source-level assertions (switch matrix via cpp, thresholds, LPG channel, fps, config, rc3 prerequisites) | 54 pass | `checks/logs/static-checks.log` |
| `-DCAMERA_POPUP_SENSOR` measurement build: DTS compiles, audit sees 4 bindable connections | 4/4 bindable, exit 0 | `checks/logs/sensor-build-audit.log` |

Caveats on the environment, so nobody over-reads this:

* `bc` is not installed on this host, so `make` cannot generate
  `include/generated/timeconst.h`. `checks/toolshim/bc` reimplements
  `kernel/time/timeconst.bc` in Python; its output for `CONFIG_HZ=1000` is
  **byte-identical** to the bc-generated header already present in this
  checkout (`.work/rear-camera/output/include/generated/timeconst.h`), which is
  how the shim is validated. Only the header generation is affected; the three
  new objects and the DTBs are built by the real toolchain.
* The compile is object-level (`make <file>.o`), not a full kernel build and not
  a module link. `modpost`/`.ko` packaging, APK packaging and pmbootstrap are
  left to the parent's normal release pipeline.
* The kernel config used is the rc3 production config with only the three new
  symbols added; that is the closest thing to the real recipe available without
  a privileged build.

## 8. Rejected / not done

* **Runtime overlay**: the supplied `build_camera_overlay.sh` variant and its
  `-DCAMERA_FRONT` slot mechanism are not used. rc4 has a static DTB; the
  fragment replaces the overlay.
* **`camera-front.dtbo`**: not produced. The popup is part of the main DTB.
* **Motor driver changes**: none. The review found no defect that can be fixed
  without hardware (see §4), and every candidate change (free-fall IRQ,
  DT-provided thresholds, no-motion-at-probe guarantees) needs either the
  mechanism or a measurement to validate.
* **Threshold changes**: none. Scaling them to "guacamole" without readings
  would be inventing data.
* **`vreg_l6f`/other rail guesses**: not added. Only LDO3, which the only
  working IMX471 tree uses.
* **A `oneplus,guacamole-popup-motor` compatible**: not added, for the reason in
  §4.
* **libcamera sensor data / tuning file**: out of scope for the kernel topic.
  The original README's Phase 3 step (add `imx471.yaml` to the libcamera
  PKGBUILD) is a separate package change and is not shipped here.

## 9. Blockers before any hardware trial

1. Hall baseline for **this** unit (Step 1) must be measured and compared with
   the hotdog windows. Without it the motor driver will refuse every course.
2. The VDIG rail question (§6 C9) must be answered — cheapest test is to enable
   only `camera@10` (motor node disabled, `power-domains` removed) and check for
   `imx471 …-0010` + chip ID `0x0471`.
3. Free-fall retraction must exist before the module is used raised in daily
   use. It does not, and this package does not add it.
4. Suspend with the camera stack loaded is untested; the rc4 suspend topic owns
   that question. Do not enable the popup nodes on a candidate whose only
   suspend result is "lock crashed".
5. **Any `CAMERA_POPUP_SENSOR` build is all-or-nothing for the camera stack.**
   The front sensor shares CAMSS's single async notifier, so if it does not
   bind (wrong VDIG rail, deferred probe because its power domain is missing,
   I2C NAK) the notifier never completes and the three rear cameras lose their
   `/dev/video*` nodes too. Use such a build as a dedicated probe image, and
   check `dmesg | grep -i -e imx471 -e camss` plus
   `checks/camss_async_audit.py` on the DTB before blaming the rear cameras.
