# rc4k sensor validation — 2026-11-15

**All six existing SSC sensor streams report successfully on the booted rc4k
diagnostic image. NFC registers `nfc0`, but RF operation remains untested.**

This run was performed over SSH as ordinary `user` at phone timestamps
07:51:36–07:52:14. Kernel release remains
`7.0.0-sm8150-guacamole-rc4`; installed userspace is
`iio-sensor-proxy-3.9-r100`, `libssc-0.4.4-r100`, `neard-0.20-r0`.
The user had already disabled UFS clock scaling in initramfs to reach this boot.
This investigation did not readjust that setting or establish that the default
boot path is stable.

## Samples

Each sensor was sampled sequentially through the installed CLI with a 3-second
application timer and a 12-second external deadline:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=5 \
  -o UserKnownHostsFile="$PWD/.work/rc4k-build/known_hosts" \
  -o StrictHostKeyChecking=yes user@172.16.42.1 \
  'timeout -k 2 12 ssccli --sensor=accelerometer --timeout=3 --debug'
```

The same command used `gyroscope`, `magnetometer`, `compass`, `light`, and
`proximity` in turn. These are temporary sensor subscriptions, not passive
reads. All commands exited zero normally, before the external deadline, and
logged both a successful disable request and sensor-disabled message.

| Sensor | Live SSC identity | Reports | Representative result |
|---|---|---:|---|
| Accelerometer | `lsm6dsm`, `STMicro`, 26 Hz | 91 | X=1.054548, Y=-0.348509, Z=9.894452 m/s² |
| Gyroscope | `lsm6dsm`, `STMicro`, 26 Hz | 90 | X=-0.004414, Y=0.017605, Z=-0.019486; CLI incorrectly prints m/s |
| Magnetometer | `mmc5603x`, `memsic`, 5 Hz | 17 | X=19.746799, Y=-34.442539, Z=-123.883263 µT |
| Compass | `Rotation Vector`, `qualcomm`, 5 Hz | 16 | Heading around 206.5 degrees |
| Ambient light | `stk_stk2232`, `sensortek`, on-change | 18 | Reports around 23–26 lux |
| Proximity | `stk_stk2232`, `sensortek`, on-change | 1 | FAR |

These verify report delivery, not physical calibration or behaviour. No
controlled rotation, light exposure, covering/uncovering, call, or UI action was
requested. The light/proximity identity is STK2232, not the older TCS3701
assumption. The CLI's gyro help omission and incorrect unit label are described
in [GYROSCOPE_FEASIBILITY.md](GYROSCOPE_FEASIBILITY.md); gyro data are angular
velocity, not linear velocity.

SensorProxy advertised `HasAccelerometer`, `HasAmbientLight`, `HasProximity`
and (on its separate Compass object) `HasCompass` as true. Its idle compass
heading was zero before sampling; this did not prevent the direct SSC heading
stream from working.

## Service health and limitations

Both `iio-sensor-proxy` and `hexagonrpcd-sdsp` remained active, with zero
restarts before and after. Sensor-proxy CPU increased from 1,197,846,000 ns to
1,209,683,000 ns: **11.837 ms across approximately 38 seconds**. Hexagonrpcd CPU
stayed at 1,627,188,000 ns. No `ssccli` process remained afterward, and the
service journals had no new entries in the sampling window.

Accelerometer, gyro and magnetometer still warn that the firmware mount matrix
is all zero and that libssc falls back to identity. Therefore axis orientation
and heading accuracy remain unvalidated. This sequential test does not exercise
simultaneous SensorProxy clients, suspend/resume, or long-duration battery use.

## NFC

Read-only `nfctool --list` and sysfs checks showed:

```text
nfc0
  Protocols: Felica MIFARE Jewel ISO-DEP NFC-DEP
  Powered: No
  RF Mode: None
  Tags: []
  Devices: []
```

The sysfs device resolves to the intended I²C controller/address:
`/sys/devices/platform/soc@0/ac0000.geniqup/a84000.i2c/i2c-4/4-0028`.
The kernel journal contains the existing initialization-time error:

```text
Nov 15 07:46:29 nxp-nci_i2c 4-0028: NFC: Read failed with error -121
```

There was no additional NFC/UFS error in the sampling window. This does not
explain or clear the earlier NFC error. Registration and protocol advertisement
do not prove controller initialization, RF carrier or tag reads. No NFC power,
polling, firmware-download or daemon action was performed.

## Evidence and scope

Local logs are in `.work/rc4k-build/sensor-validation/`:
`before.txt`, `after.txt`, six per-sensor `.log` files, and `nfc-kernel.txt`.
These are workspace evidence; the concise observations above stand on their
own. No sudo, install, modprobe, restart, reboot, firmware change, UFS-setting
change, front-camera or motor operation was performed.

Next physical checks are near/far transitions, controlled compass/gyro turns,
ambient-light response, and the actual call/UI consumers. NFC separately needs
an intentional controller-power and tag test before claiming working NFC.
