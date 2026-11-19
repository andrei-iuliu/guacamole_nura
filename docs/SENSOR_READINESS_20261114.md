# Sensor readiness for the next RC — live SSH research, 2026-11-14

Front camera remains deferred. **Compass and magnetometer already stream on
rc4j; proximity also reports.** Follow-up source inspection and sampling also
confirmed **raw gyroscope streaming**; see [GYROSCOPE_FEASIBILITY.md](GYROSCOPE_FEASIBILITY.md).
Their remaining work is physical validation and
application integration, not initial kernel bring-up. NFC remains a separate
board-DT experiment.

## Environment and method

Connected as `user@172.16.42.1`, kernel
`7.0.0-sm8150-guacamole-rc4`. Installed packages are
`iio-sensor-proxy-3.9-r100` and `libssc-0.4.4-r100`.

Read D-Bus introspection, service state, journal, registry filenames and CLI help.
Then sampled each supported sensor sequentially with existing `ssccli`, using
its own timeout and an outer process deadline:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  user@172.16.42.1 \
  'timeout -k 2 12 ssccli --sensor=magnetometer --timeout=3 --debug'
```

Repeated with `compass` (5 seconds), `proximity`, `light` and `accelerometer`
(3 seconds each). A first magnetometer sample also ran for 5 seconds.
All completed normally, logged a successful disable request and sensor-disabled
message; the outer deadline did not fire. `pgrep -a ssccli` afterward returned
no processes. These were temporary subscriptions, not strictly passive reads.
No sudo, package installation, service restart, firmware change, registry edit,
motor operation or other administrative operation was performed.

Detailed local logs: `.work/sensor-research-20261114/{compass,magnetometer,proximity,light,accelerometer}.log`.
They are workspace evidence, not committed release artifacts. The relevant
observations are retained below.

## Observed results

| Sensor | Live SSC attributes / D-Bus evidence | Samples | What this proves |
|---|---|---|---|
| Compass | `Rotation Vector`, vendor `qualcomm`, datatype `rotv`; `/net/hadess/SensorProxy/Compass` has `HasCompass=true` | 29 headings, approximately 209.11–209.58 degrees | SSC fused heading stream works; no controlled rotation or accuracy test |
| Magnetometer | `mmc5603x`, vendor `memsic`, datatype `mag`, available, 5 Hz | 17 saved samples; example X=21.868755, Y=-30.491089, Z=-125.973396 microtesla | Actual magnetic stream and firmware-reported device identity; no axis/calibration accuracy validation |
| Proximity | `stk_stk2232`, vendor `sensortek`, datatype `proximity`, on-change; `HasProximity=true` | One `FAR` event | Sensor produces a report; no near/far transition or call test |
| Ambient light | `stk_stk2232`, vendor `sensortek`, datatype `ambient_light`; `HasAmbientLight=true` | Mostly 0 lux, one 1-lux sample | Light reports arrive; no controlled lighting or automatic-brightness validation |
| Accelerometer | `lsm6dsm`, vendor `STMicro`, datatype `accel`; `HasAccelerometer=true` | Example X=1.057421, Y=-0.334144, Z=9.836994 m/s² | Existing acceleration stream still works; no orientation test |

The **live light/proximity identity contradicts the TCS3701 assumption** in
`docs/PLAN.md` and the README. Use `stk_stk2232` as the firmware-reported active
identity for this session; this is stronger evidence than a config filename,
but is not an independent silicon-ID read.

The magnetometer and accelerometer report an all-zero firmware mount matrix;
libssc falls back to identity. The existing accelerometer udev correction is a
separate path. Do not infer that magnetometer axes or heading are calibrated
because readings exist. The stationary magnetometer field also does not prove
heading accuracy in the current magnetic environment.

After sampling, both sensor-proxy and hexagonrpcd-sdsp remained active with zero
restarts. Sensor-proxy CPU rose from 512,657,000 ns to 545,341,000 ns across the
investigation; hexagonrpcd stayed at 937,397,000 ns. No new sensor-proxy journal
errors appeared. This is a short sequential test, not the simultaneous-client
regression test for the earlier busy-loop bug.

## Candidates still unproven

* **Gyroscope — resolved by follow-up:** the earlier inference from CLI help
  was wrong: help omits `gyroscope`, but libssc 0.4.4 implements the API, decoder
  and `ssccli --sensor=gyroscope` branch already. A bounded live sample produced
  72 XYZ measurements from `lsm6dsm` / `STMicro`, advertised at 26 Hz, then
  disabled successfully. No new driver/decoder is needed. Physical axis and
  calibration validation remain; details in the companion feasibility note.
* **IMU temperature:** `sensor_temperature` and per-IMU temperature registry
  entries exist, but no live discovery or sample was obtained. The existing
  CLI has no temperature mode. Lower priority than gyro.
* **SAR/body proximity:** SX9324/SX932x registry alternatives exist; neither
  fitted hardware nor a live SSC endpoint is proven. Defer integration until
  discovery identifies a real sensor and its intended consumer.
* **Other virtual sensors:** registry files include gravity, game/geomagnetic
  rotation vectors, pedometer, tilt and bring-to-ear. These are possibilities,
  not observed services. No claim of availability is made.

The registry also contains generic alternatives for humidity, gas, pressure,
multiple IMUs and multiple ALS/proximity devices. **Presence in the registry
must not be used as a hardware inventory.**

## Recommended next RC scope and next tests

1. Keep the existing sensor stack; add NFC independently.
2. Validate proximity by covering/uncovering the sensor while running
   `ssccli --sensor=proximity --timeout=15`, then verify call-screen blanking
   through the actual call/session UI. A FAR report alone is not completion.
3. Validate compass with `ssccli --sensor=compass --timeout=15` during controlled
   quarter-turns away from magnets, and verify a desktop client receives the
   heading through SensorProxy. Its initial idle D-Bus heading of 0 was not
   evidence that the SSC stream was broken.
4. Verify light changes under controlled illumination and screen brightness
   response. The measured near-zero lux is inconclusive without knowing light
   exposure and placement.
5. Validate the already-working raw gyro stream with controlled axis rotations;
   select an application consumer only when needed. Do not hold the
   NFC/proximity/compass RC for temperature or SAR exploration.

No physical movement, covering/uncovering, calls, suspend/resume or prolonged
multi-client test was performed. No implementation changes were made.
