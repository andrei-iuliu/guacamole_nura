# Gyroscope: already supported and streaming on rc4j

Research and bounded SSH sampling, 2026-11-14. **No new kernel driver, libssc
API, protobuf decoder or discovery code is needed.** The earlier readiness
assessment inferred absence from incomplete CLI help; source inspection and
live sampling disprove that inference.

## Source checked

Fetched the recipe's official pinned source:
[libssc v0.4.4](https://codeberg.org/DylanVanAssche/libssc/archive/v0.4.4.tar.gz).
Its SHA-512 matches the existing APKBUILD exactly:

```text
9240970f6a70f4900ef2d8fad337dac815ea2602b3b395943326fb086f59d24dcf59b76bdbc6014ebd3c450eaad5393465ca038c165845ffe99bf8e282a5c170
```

Local source is `.work/gyro-research/libssc/`.

| Layer | Existing implementation in v0.4.4 |
|---|---|
| Public API | `src/libssc-sensor-gyroscope.h`: asynchronous and synchronous new/open/close APIs; included from `src/libssc.h` |
| Discovery | `src/libssc-sensor-gyroscope.c:272`: constructor supplies datatype `gyro` to the shared sensor discovery machinery |
| Decoder | `src/libssc-sensor-gyroscope.c:63`: matches SUID and measurement message, unpacks protobuf, requires at least three values, emits XYZ measurement signal |
| Protocol | `data/ssc-sensor-gyroscope.proto`: repeated float field 1 and accuracy field 2 |
| CLI | `src/libssc-cli.c:277`: `--sensor=gyroscope` creates, subscribes and opens; timer closes it |
| Build | `src/meson.build` includes gyro C and public header; no optional feature switch |
| Misleading help | `src/libssc-cli.c:171` omits gyroscope from the `--sensor` option description, although the unsupported-sensor error lists it |

## Live result

Ran the existing installed binary as ordinary `user`, without installing or
modifying anything on the phone:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes \
  user@172.16.42.1 \
  'timeout -k 2 12 ssccli --sensor=gyroscope --timeout=3 --debug'
```

Captured in `.work/gyro-research/gyroscope.log` (local workspace evidence).
SSC reported:

```text
name: lsm6dsm
vendor: STMicro
data-type: gyro
sample-rate: 26.000000 Hz
```

**72 XYZ measurements** arrived. Initial values included
`X=-1.064875 Y=1.180692 Z=-0.373788`; later values included
`X=0.000473 Y=0.012718 Z=-0.018265`. No controlled physical movement was
requested, so do not interpret the initial transient as a calibrated rotation
test. The CLI prints these with a misleading `m/s` suffix.

The sample exited normally after its own timer, with both
`Sensor disable request sent successfully` and `gyroscope sensor disabled`.
The external deadline did not fire. `pgrep -a ssccli` afterward was empty.
Sensor-proxy and hexagonrpcd remained active, zero restarts; CPU totals were
549,468,000 ns and 937,397,000 ns respectively. No sudo, service restart,
package install, firmware change, motor command or administrative operation
was used. Sampling is a temporary subscription, not a passive read.

## Smallest useful change

**None is required to expose raw gyro readings today.** Use the existing CLI
or libssc API. SensorProxy's currently exposed D-Bus interfaces do not provide
raw gyro data; adding a desktop-wide gyro service without an application
consumer would be speculative.

Optional packaging polish is three text edits, retaining the same numeric data:

1. `src/libssc-cli.c:171`: append `, 'gyroscope'` to the supported-sensor help.
2. `src/libssc-cli.c:153`: change the gyro print suffix from `m/s` to `rad/s`.
3. `data/ssc-sensor-gyroscope.proto:24`: describe angular velocity in `rad/s`.

Qualcomm's own [QSH sensor overview](https://docs.qualcomm.com/bundle/publicresource/topics/80-70014-7)
defines datatype `gyro` as three-axis rotation rate in **rad/s**. The current
CLI and proto comment both use `m/s`; the decoder performs no unit conversion.
This is a label correction, not a scale-factor change. Physical calibration
still needs testing.

If shipping that patch, bump the local libssc recipe to r101, update its source
list/checksum, and keep the exact pins in `check_local_candidate.py`,
`rootfs_prepare_local.sh`, the sensor/UI tests and sensor topic documentation
coherent. The existing verification script explicitly applies only patch 0001,
so it must also apply the new patch. No such package changes were made during
this research; r100 remains intact.

## Remaining checks

Run the same bounded command for about 15 seconds while rotating the phone
around each axis in turn, then compare sign/magnitude and stationary bias.
The firmware mount matrix is all zero and libssc falls back to identity, so
device-axis correctness has not been established. Retain an external deadline:
the CLI's own close timer is installed only after synchronous discovery/open
returns, and those calls can otherwise wait indefinitely.

The existing v0.4.4 tests have no gyro-specific test case. No new implementation
or test was added here; source reuse and actual measurements settle the
bring-up question, not long-term accuracy, suspend behaviour or application
integration.
