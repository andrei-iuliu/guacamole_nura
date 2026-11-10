# Diagnostic protocol — Nura rear cameras, video chop, regressions

This is a **user-run** protocol. The agent did not execute any phone, SSH,
privileged, recording or power command. Everything below is read-only except
where the user deliberately performs a normal UI action (play audio, connect a
charger, suspend the phone).

Do not install packages, change governors/power settings, reload modules,
rebind sensors, write rails/actuator/EEPROM/sysfs, or start a recording from a
script. Do not touch the running phone/rootfs/server or ports 10000/10001.
Main/tele autofocus is intentionally unbound; do not use this protocol to
change that.

## 0. What is being separated

| Symptom location | Evidence that separates it |
| --- | --- |
| Capture / ISP | recorded frame timestamps have gaps or a low effective rate; CAMSS/CCI errors; sensor-specific behavior |
| Encoding | same timestamp gaps plus high CPU pressure, low CPU/GPU frequency or thermal attention; watch log |
| App preview | preview is choppy but the recorded file timestamps are regular |
| Playback | file is timestamp-regular and smooth on the host but choppy on the phone |
| Stack identity | live process maps show `/opt/nura-rear-camera/lib/libcamera.so.0.7.2` and/or `/usr/lib/libcamera.so.0.8` |

The analyzer reports `UNKNOWN`/`MISSING`/`PARTIAL` when evidence is absent. It
does not emit `PASS`.

## 1. Baseline snapshot (before opening the camera app)

On the phone, as the normal user:

```sh
sh /tmp/nura-probe.sh > /tmp/nura-baseline.txt
```

Copy `nura-baseline.txt` to the host. If `dmesg` was denied in the
snapshot, a user-run read-only capture can be supplied separately:

```sh
sudo dmesg > /tmp/nura-dmesg.txt
```

Pass it with `--dmesg /tmp/nura-dmesg.txt` (and a journal capture with
`--journal`). The analyzer appends it to the captured log window; it does not
use sudo itself. If you prefer SSH from the host, use the normal host-key
verification once:

```sh
bash scripts/collect_profile.sh --ssh user@172.16.42.1 --out ./collections/baseline
```

The prior password-only phone denied SSH. If that happens, stop; do not retry
or change keys. Use the on-phone snapshot instead. The script makes exactly one
`BatchMode` attempt and never inspects credentials.

The baseline captures, among other things:

- kernel release, boot cmdline, installed camera/PipeWire packages;
- CCI module path/size/sha256 (for comparison with the build manifest);
- media topology, video nodes and camera device holders;
- the live camera app/PipeWire process maps and loaded libcamera libraries;
- audio cards/codecs, Wi-Fi/BT rfkill and sysfs state, charger/power-supply
  state, suspend statistics, CPU/GPU/thermal state and existing recordings.

Nothing is started, loaded, written or reset.

## 2. Regression smoke checks

For each row: perform the **user action**, then take a fresh snapshot (or run
the watch only where noted). Save the output with a descriptive name. These are
functional observations by the user; the analyzer only records state and marks
functional behavior `NOT_TESTED`.

| Domain | User action | Look for in the snapshot | Do not |
| --- | --- | --- | --- |
| Speakers | Play a known stereo track through the normal UI at low volume; confirm left/right | `AUDIO` cards, `AUDIO_I2C` amp/codec entry, speaker/amp errors in `DMESG`; no under-voltage/oops | write mixer controls, change routes, reload modules |
| Mic | Record a short voice memo with the normal app; play it back | capture devices in `AUDIO`, `wpctl status` if PipeWire is running; DMESG codec errors | start an unsolicited `arecord` stream or change routes |
| Wi-Fi | Connect to a known AP and load one page; note association | `NET wlan0 operstate=up`, `iw dev`, `/proc/net/wireless`, `ath10k`/`wcn` errors | unbind/rebind `ath10k_snoc`, stop/start `remoteproc0`, or scan from a script |
| Bluetooth | Pair/connect a known device through Settings; note state | `rfkill list`, `HCI hci0`, `hciconfig` if present; no `bluetoothctl` call is made by the probe | pair from a script, power-cycle the adapter, or change services |
| Charging | Connect the known 5 V DCP charger; note whether it charges; disconnect afterwards | `SUPPLY` type/online/status/voltage/current, `/run/guacamole/charger`, `input_current_limit` | write `input_current_limit`, suspend input via `status`, or leave input suspended |
| Suspend | Save a snapshot; suspend once through the normal desktop control; wait 10–15 s; wake; save another snapshot | `suspend_stats` success/fail, `PM: suspend`/`PM: resume`, `last_failed_dev` | write an RTC wake alarm, change autosleep, or force a resume |

If the phone does not resume, photograph the screen and recover with the normal
user procedure; do not repeatedly power-cycle. If charging reports an input
limit of `0`, do not “fix” it by hand; check the charger daemon’s normal policy
and ask before changing anything.

The probe deliberately avoids `bluetoothctl`/NetworkManager activation when it
is not already running, and avoids `sudo`. `[DENIED]` means the user may
separately run a read-only command such as `sudo dmesg > dmesg.txt`.

## 3. Recording protocol — one sensor at a time

Use the normal camera app. Do not write a capture client. Do not switch lenses,
enable fusion, use the front camera, or change AF/rails/calibration.

1. Use a fresh boot for the comparison. Keep the phone off a charger unless the
   test explicitly compares charging; use the same lighting and a static scene
   followed by a slow pan.
2. Select **one** rear sensor in the app (main, tele, or wide/ultrawide). The
   app’s own label is the user’s evidence; if the app cannot select one sensor,
   label the clip by what it reports and treat per-sensor comparison as
   unavailable.
3. In one phone terminal, start the read-only watch **before** pressing record:

   ```sh
   sh /tmp/nura-probe.sh --watch 60 > /tmp/nura-watch-main.txt
   ```

   It samples CPU/GPU/thermal/charger once a second. It does **not** start the
   camera or recording. If you prefer, use `--watch 0` and Ctrl-C after the
   clip.
4. In the app, record 10–15 s at the same resolution/fps for every sensor.
   Note the app’s visible timer or use a stopwatch; this is `wall_seconds`.
   Stop the watch after the clip (Ctrl-C for `--watch 0`, or wait for the
   timeout).
5. Save the clip as `main-sceneA.mp4`, `tele-sceneA.mp4`, or
   `wide-sceneA.mp4`. Save the watch log alongside it.
6. Repeat for the other sensors with the same scene and settings. At least one
   clip per sensor is required for a per-sensor comparison.
7. Optionally repeat one sensor at a lower resolution/fps as a **control**
   only after the baseline clips exist. This is a user-run app setting, not a
   fix; it helps separate throughput from sensor capture.

Sidecar written next to each clip (plain text is enough):

```text
sensor=main
symptom=preview
wall_seconds=12
resolution=1920x1080
fps=30
charging=no
notes=preview choppy; recording file not yet played on host
```

- `sensor`: `main`, `tele`, `wide` (ultrawide is accepted as `wide`).
- `symptom`: `preview`, `recording`, `playback`, `both`, or `unknown`.
  Use `preview` if only the live preview is choppy, `recording` if the act of
  recording/its immediate result seems choppy, `playback` if only replay is
  choppy. Use `both` if you cannot separate them yet.
- `wall_seconds`: measured clip duration. The analyzer compares it with the
  container duration; a shortfall is a flag, not a driver dropped-frame count.
- `fps`: the app setting, if visible. The analyzer compares it with the
  container’s nominal rate and the effective rate; a large shortfall is marked
  as low-rate, but the tool does not assume the app honored its setting.

Do not create one long clip that switches lenses; label and compare separate
clips.

## 4. Playback distinction

For each clip, play the file on the host (for example `mpv` or `ffplay`) and on
the phone. Record both observations in the sidecar notes. Then:

- file timestamps irregular → capture/encode path;
- file regular and host playback smooth, phone playback choppy → playback/
  decode/display;
- file regular and preview choppy → preview/display path;
- file regular and only the recording result seems choppy → check playback on
  both before blaming capture.

The analyzer cannot play a file; it only measures timestamps and metadata.

## 5. Build the bundle on the host

```sh
bash scripts/collect_profile.sh \
  --snapshot /path/to/nura-baseline.txt \
  --manifest /path/to/DELIVERY.json \
  --recordings /path/to/clips \
  --dmesg /path/to/nura-dmesg.txt \
  --out ./collections/phone-$(date -u +%Y%m%dT%H%M%SZ)
```

- The clips are read in place; only metadata/report files are written into the
  bundle.
- The manifest is optional. With it, the analyzer compares the observed kernel
  release and CCI module hash/size to the manifest’s values. Without it, those
  checks are `UNKNOWN`; the tool does not embed a fixed release or today’s
  binary hash. Recognized keys are `release`/`kernel_release` and
  `i2c_qcom_cci.sha256`/`size` (the existing `DELIVERY.json` shape); a nested
  `kernel.release` or `artifacts.*` entry is also searched.
- If `ffprobe` is missing, video entries are `TOOL_MISSING` and the verdict is
  `PARTIAL`; install nothing on the phone. Host tooling is the user’s choice.
- Read `report.md` first, then `analysis.json` and `videos.tsv` for details.

## 6. Interpretation without overclaiming

- `CHOP_OBSERVED` means timestamp gaps or a low effective rate were measured;
  it does not by itself identify capture vs encode.
- `NO_CHOP_DETECTED_IN_ANALYZED_WINDOW` is timestamp regularity in the capped
  window; it is not a visual smoothness pass.
- `PARTIAL` means some clips/tools/sensors are missing; do not generalize.
- `OBSERVED_ERRORS` is a log match, not a proven root cause.
- A `mixed` camera stack means both private 0.7.2 and system 0.8 paths are
  mapped. Identify which process owns the camera device before changing
  anything. Do not downgrade the system libcamera or mix ABIs.
- Main/tele lenses remain unbound; do not treat focus quality as a video
  choppiness cause or change actuator/rail/calibration state.

## 7. Stop conditions

Stop and report rather than continue if:

- SSH denies authentication (do not retry, do not change keys);
- the kernel release or CCI identity does not match the manifest;
- a snapshot shows an oops/CCI reset/thermal-critical line;
- the phone does not resume from suspend, or the screen shows a crash;
- charging input is suspended or the battery is at/near Critical.

No agent command should be used to recover; the user follows the normal
recovery procedure.


Consolidation note: process environments are not read. Runtime stack identity
comes from mapped library paths; this avoids inspecting potential credentials.
