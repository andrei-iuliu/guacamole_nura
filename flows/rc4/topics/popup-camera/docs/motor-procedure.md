# Pop-up motor: measure-first procedure

The motor driver (`kernel/0037`) stops each course on Hall-sensor endpoints, with a hard ceiling of 44,160 microsteps. **Every threshold is a `#define` measured on one OnePlus 7T Pro** (`drivers/media/platform/qcom/hotdog-popup-motor.c` L19–L47). guacamole has the same mechanism per the stock DT, but magnet placement and sensor offsets vary between units. So measure first, and adjust the defines if this phone reads differently.

Rules for every step:
- A person is holding the phone, the pop-up opening is unobstructed, and the phone is face-up.
- Keep a second SSH session open, so you can `echo 1 > restore_closed` or unload the module.
- One step per module load. If anything is unexpected, stop and record the readings in GN `docs/PLAN.md`.

## Step 1: Hall sensors only (0035, no motor driver)

Load `mxm1120`, with the DT `camera-popup` node absent or its driver not loaded.

```sh
for d in /sys/bus/iio/devices/iio:device*; do echo "$(cat $d/label 2>/dev/null) $(cat $d/name)"; done
# then, 20 samples at 100 ms:
for i in $(seq 20); do
  printf '%s %s\n' "$(cat /sys/bus/iio/devices/<up>/in_magn_raw)" "$(cat /sys/bus/iio/devices/<down>/in_magn_raw)"
  sleep 0.1; done
```

`mxm1120.c` exposes one unindexed `IIO_MAGN` raw channel (`in_magn_raw`), and the device `label` is `camera-popup-up` / `camera-popup-down` from the DT.

Physically confirm the camera is **down**. HD's closed baseline on the 7T Pro (evidence doc, "Result") was:

| | upper (`0x0c`) | lower (`0x0d`) |
| --- | --- | --- |
| closed | −13..−14 | −369..−370 |
| open (after full course) | −349..−350 | −11..−12 |

The driver's checks on these readings:

| `#define` | Value | Used for |
| --- | --- | --- |
| `HOTDOG_CLOSED_DOWN_MIN` | 300 | closed when `|down| >= 300` … |
| `HOTDOG_CLOSED_UP_MAX` | 50 | … and `|up| < 50` |
| `HOTDOG_OPEN_HALL_MIN` | 150 | open endpoint accepted from `|up|` |
| `HOTDOG_SETTLED_DOWN_MIN` | 350 | below this after a close → one 320-step restore |
| `HOTDOG_RESTORE_DOWN_MIN/MAX` | 340 / 368 | window in which a restore is allowed |
| `HOTDOG_RECOVER_DOWN_MIN/MAX` | 320 / 360 | recovery window |
| `HOTDOG_OPEN_DOWN_MIN` | 368 | |
| `HOTDOG_FINISH_UP_MIN/MAX`, `HOTDOG_FINISH_DOWN_MAX` | 130 / 200, 50 | finishing the opening course |
| `HOTDOG_HALL_ABS_MAX` | 450 | readings above are implausible: refuse |
| `HOTDOG_FULL_OPEN_UP_MIN` | 300 | |

**Go/no-go:**
- If guacamole's closed readings fall inside the closed thresholds with margin (`|down|` well above 300, `|up|` well under 50) and the sign convention matches, continue.
- If not, scale the thresholds to this unit's baseline before loading 0037.
- Either way, record the numbers.

## Step 2: one bounded pulse (0037, manual)

Load `hotdog-popup-motor`, still **without** `power-domains` on the IMX471 node, so nothing moves on its own. The provider starts logically powered, so probing doesn't move the mechanism. The sysfs attributes are under the `camera-popup` platform device (`/sys/bus/platform/devices/camera-popup/`):

```sh
cd /sys/bus/platform/devices/camera-popup
cat status                 # before: hall readings, used/error counters
echo 1 > pulse_up          # exactly 320 microsteps, only from the measured closed region, once per load
cat status
```

**Expect:** the lower Hall magnitude falls by a few counts and the upper barely changes. HD measured −370 → −365 for 320 microsteps (evidence, "Bounded motor preflight"). The driver leaves STEP, SLEEP, mode and boost inactive afterwards.

If the readings moved the **wrong** way, or not at all, stop. Check DIR polarity (PM8150L GPIO 11) and that the STEP pin reaches the motor (pinctrl `pwm` state, `func1` on PM8150L GPIO 10, LPG channel 5 = `pwms` index 4).

Then bring it back down: `echo 1 > restore_closed`.

## Step 3: full open, then full close

```sh
echo 1 > open      # Hall-terminated; hard ceiling 44160 microsteps
cat status         # endpoint=1 error=0; HD: ~30k-32k steps, Hall about -315/-10
echo 1 > close
cat status         # endpoint=1 error=0; if |down| < 350, one bounded 320-step restore runs
```

Look at the module after each course: fully up, and fully seated afterwards. HD needed the seated-close restore (0124) because one unit stopped at about −344.

## Step 4: automatic lifecycle

Restore `power-domains = <&camera_popup>` on `camera@10`. Then:
- start a stream (`cam -c <imx471> --capture=60`, or the camera app) and check the camera rises before frames and lowers on release;
- check `automatic_open_count` and `automatic_close_count` in `status`;
- kill the app mid-stream and confirm the camera comes down;
- switch between cameras in the app.

## Things the driver does on its own (from HD 0115–0124)

- A slow ramp of 960 microsteps at a 105,000 ns period, then 13,125 ns, as OxygenOS does; these are the closest periods the LPG can represent exactly.
- If the slow ramp moves neither Hall sensor (the dead zone at the endpoints), a bounded 3,200-microstep probe runs before it declares a stall. A real obstruction stops after about 4,160 microsteps.
- A failed automatic opening attempts the bounded closing course before returning the error.
- The motor boost stays enabled between courses, as stock does (0119/0120).

## Sources

- HD `docs/evidence/2026-08-10-mainline616-camera-imx471-popup.md` (all measurements quoted here)
- HD patches 0104–0124 (`aports/device/testing/linux-oneplus-hotdog-mainline616/`)
- `LOSK arch/arm64/boot/dts/18821/sm8150-oem.dtsi` L620–L793 (stock `motor_pl`, MXM1120 nodes, `step_motor`, pins, `pwms = <&pm8150l_pwm 1 20000000>`)
