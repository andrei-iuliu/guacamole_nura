# OnePlus 7 Pro (guacamole): in-display fingerprint — findings and plan

A research handoff, not a feature patch.

**Bottom line:** fingerprint is reachable in principle, by running the stock Android fingerprint stack against a mainline TrustZone driver. The way omarchy-mobile did it on the Pixel 7 Pro is the precedent. Everything hinges on **gate 0**: whether the mainline Qualcomm TEE driver (`QCOMTEE`, new in 7.0) works with this phone's TrustZone. Run gate 0 before investing anything else.

**Status: nothing here has been compiled or run on a phone.**

## Pinned sources

| Repo | Commit | Short name |
| --- | --- | --- |
| guacamole_nura | `5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24` | `GN` |
| hotdog-linux-bringup (OnePlus 7T Pro) | `47087509c4289c2580c54f5433e55366b2e00445` | `HD` |
| omarchy-mobile (Pixel 7 Pro fingerprint work) | `f702f688288e59e3566f113d411ac7d18e4d0201` | `OM` |
| LineageOS android_kernel_oneplus_sm8150 (stock-derived 4.14) | `0521dc291cf10bd754fb5ff385e74875c406d577` (`lineage-23.2`) | `LOSK` |
| LineageOS android_device_oneplus_sm8150-common | `b194543aba563d33221aa4f637708e6908d5a73f` (`lineage-23.2`) | `LOSD` |
| LineageOS android_device_oneplus_guacamole | `4e24f54dba481671e18323b20ade88daa2b09e34` (`lineage-23.2`) | `LOSG` |
| qualcomm/minkipc (QTEE userspace) | `ff9eaf92d357ec35a54a7bf5d2465a5f818d73ea` | `MINK` |
| kernel 7.0 | `gitlab.postmarketos.org/soc/qualcomm-sm8150/linux` `v7.0.0-sm8150` | 7.0 |

## Contents

```
README.md                         this file: summary, gates, decisions
docs/findings.md                  the evidence: hardware, stock software stack, TEE, sources
gate0/config.fragment             kernel options for gates 0-1 (7.0 only)
gate0/gate0-check.sh              on-phone check: does QCOMTEE find QTEE?
kernel/0034-input-misc-add-goodix-fp.patch   sensor power/reset/IRQ driver, downstream ABI (gate 1)
dts/goodix-fp.dtsi                DT node + pinctrl for that driver (7.0 labels)
```

## The problem in one picture

```
Phosh lock screen / PAM ──► (bridge, gate 6)
                               │
      vendor.oplus.hardware.biometrics.fingerprint@2.1-service      HIDL, Bionic   (gate 4)
        → fingerprint.18821_goodix9508_G2_4.default.so
          → libgf_ud_hal_18821_G2_4_oplus.so
              ├─ libQSEEComAPI.so → /dev/qseecom ─┐                 (gate 3: replace)
              │                    qseecomd ──────┤ listeners: RPMB, files
              ├─ /dev/goodix_fp + netlink 25      │                 (gate 1: kernel/0034)
              └─ panel HBM + lit spot             │                 (gate 2)
                                                   ▼
                       Qualcomm TrustZone: Goodix app ── keymaster / gatekeeper apps (gate 5)
                       (capture, templates, matching)
```

The image never reaches Linux. HD documents this for the 7T Pro, and guacamole's stock stack is the same design (`docs/findings.md` §2).

## Gates, cheapest first

Stop at the first gate that fails, and record the result in GN `docs/PLAN.md`.

### Gate 0: does mainline QCOMTEE find QTEE? (hours)

- **Steps:**
  1. Build the 7.0 kernel with `gate0/config.fragment`.
  2. Boot, then run `gate0/gate0-check.sh` as root.
  3. Build `MINK` (needs `quic/quic-teec` and QCBOR or libcbor; see its README), then run `smcinvoke_client -d 1`.
- **Why it might pass:** `qcom_scm` probes QTEE with SMC `0x32000600` (`QCOM_SCM_SVC_SMCINVOKE`/`INVOKE`). The stock guacamole kernel uses exactly that call: `LOSK drivers/soc/qcom/smcinvoke.c`, `SMCINVOKE_INVOKE_CMD 0x32000600`, with `CONFIG_QCOM_SMCINVOKE=y` in `sm8150-perf_defconfig`.
- **Why it might fail:**
  - Mainline QCOMTEE has been developed on much newer SoCs.
  - The SM8150 TrustZone handles listener callbacks (RPMB, files) through the legacy qseecom path: `smcinvoke.c` calls `qseecom_process_listener_from_smcinvoke()`. Mainline's callback objects may not cover that.
- **Also check:** the modem, ADSP and SLPI still boot after the allocator switches to SHM-bridge mode (see the fragment).
- **Pass:** go to gate 1. **Fail:** see "If gate 0 fails" below.

### Gate 1: sensor power, reset, IRQ (days)

- **Steps:**
  1. Apply `kernel/0034-input-misc-add-goodix-fp.patch` and include `dts/goodix-fp.dtsi`.
  2. Expect `/dev/goodix_fp`, `/proc/fp_id` reading `G_OPTICAL_18821_G2_4`, and `/proc/fp_kernel_event` reading `1`.
  3. Power the sensor with ioctl 7 (`GF_IOC_ENABLE_POWER`), then pulse reset with ioctl 2 (`GF_IOC_RESET`).
  4. Count interrupts in `/proc/interrupts` (`goodix_fp`).
- **Expected:** HD saw zero edges on the 7T Pro without illumination or the TrustZone app driving the sensor. That proves nothing either way. omarchy's Pixel sensor answered each reset with an interrupt. **A reset-time interrupt is the useful signal here.**
- **Driver notes:** `0034` keeps the downstream interface exactly: ioctl numbers, netlink protocol 25 with one-byte events, and the `/proc` nodes. Touch and lit-spot events, which came from the touchscreen and display drivers downstream, come from userspace through sysfs `touch`, `ui_ready` and `screen` on the platform device.
- **Pins:** GPIOs 90, 101, 118 and 131 are free in GN; 90 and 101 only appear in default states of unused SPI groups.

### Gate 2: illumination (days)

The sensor only sees a finger that is lit. Stock lights a bright spot with the panel in high-brightness mode (HBM), and the HAL drives it through `card0-DSI-1/hbm`, `notify_dim` and the backlight. The HBM on/off DCS sequences are in `LOSK arch/arm64/boot/dts/18821/dsi-panel-samsung_oneplus_dsc.dtsi`:
- `qcom,mdss-dsi-panel-hbm-on-command-*` / `-off-command`, around L382–L436;
- `qcom,mdss-dsi-hbm-on/off-command`, around L457–L505.

Steps:
1. Add an HBM switch to GN's panel driver (`0004`, `samsung,oneplus-dsc`), for example a sysfs or backlight attribute that sends those sequences.
2. Have the shell draw a filled circle at the sensor position: **x 720, y 2728, radius 137 px** on the 1440×3120 panel. These come from LineageOS's guacamole device repo, `overlay/OPlusFrameworksResTarget/res/values/config.xml` L233–L237 `config_udfps_sensor_props` (`LineageOS/android_device_oneplus_guacamole` @ `4e24f54dba481671e18323b20ade88daa2b09e34`). LineageOS draws the pressed spot green (`config_udfpsColor #00ff00`, `overlay-lineage/.../SystemUI/res/values/config.xml` L15). Try that colour first, and white if the sensor doesn't respond.
3. With the sensor powered and the spot lit, a finger should raise interrupts.

### Gate 3: talk to the Goodix TrustZone app (1–3 weeks, the real unknown)

The HAL uses legacy QSEECom:
- It starts the app with `QSEECom_start_app` (the app image is in the firmware partition; get its name with `strings libgf_ud_hal_18821_G2_4_oplus.so`).
- It exchanges command and response buffers with `QSEECom_send_modified_cmd`.

Route A (preferred) is a replacement `libQSEEComAPI.so`, a Bionic library preloaded or dropped into the chroot, that maps those calls onto QCOMTEE:
1. Load the app through QTEE's app loader (`MINK`'s `smcinvoke_client -c app.mbn` proves the mechanism).
2. Reach a legacy QSEECom app through the app object. Whether SM8150 QTEE exposes that path is the open question; read `MINK`'s IDL and `libminkadaptor`.
3. Run `MINK`'s `qtee_supplicant` with its listener libraries (`libfsservice`, `librpmbservice`, `libtimeservice`) in place of `qseecomd`. RPMB needs the UFS RPMB well-known LU reachable: GN has `SCSI_UFS_BSG=y` but `# CONFIG_CHR_DEV_SG is not set`, and OM's Pixel needed `sg`.

Route B (fallback) is to extend mainline `drivers/firmware/qcom/qcom_qseecom.c`:
- GN already has `CONFIG_QCOM_QSEECOM=y`. That driver can look up and message an already-loaded app (`qcom_scm_qseecom_app_get_id`, `qcom_scm_qseecom_app_send`), but it can't load one and doesn't serve listeners.
- Add app load (`QSEOS_APP_START_COMMAND`) and listener handling following `LOSK drivers/misc/qseecom.c`.
- Expose an ioctl-compatible `/dev/qseecom` so stock `libQSEEComAPI.so` and `qseecomd` run unmodified. Stock `libQSEEComAPI` also allocates through `/dev/ion`, which mainline lacks, so it needs a dma-heap shim.

### Gate 4: stock HAL in a chroot (1–2 weeks)

Copy OM's Pixel lab (`OM devices/pixel7pro/fingerprint/`: `fp-hal-lab.sh`, `fp-shim.c`, README "The HAL in the lab"):
- a RAM-only chroot holding stock `odm` and `vendor` binaries;
- binderfs with `hwservicemanager` (this HAL is HIDL @2.1, not AIDL as on the Pixel);
- a preloaded shim for logging, SELinux, properties and missing services.

GN's kernel needs `CONFIG_ANDROID_BINDERFS=y`: it has `ANDROID_BINDER_IPC=y` with fixed devices, and binderfs is unset.

The blob list is the "Fingerprint", "Gatekeeper", "Keymaster" and QSEECom sections of `LOSD proprietary-files.txt` (L399–L454 and L664 onward). GN's `scripts/extract_lineage_firmware.py` already pulls from a LineageOS OTA.

### Gate 5: keymaster and gatekeeper (~1 week)

Enrollment takes a hardware auth token. The Goodix app checks its HMAC with the key the keymaster app shares at boot (`computeSharedHmac`), and gatekeeper issues the token after a PIN check. Run the stock QTI keymaster and gatekeeper HALs through the same transport as gate 3, as OM did with Trusty KeyMint and gatekeeper (`OM` README, "The client, and enrollment's auth token").

### Gate 6: integration (days)

libfprint has no driver, and can't have one, because there's no image. Use OM's design: a small service holds the HAL session and speaks a line protocol (OM `fp-client serve`). A PAM module, or the lock screen directly, talks to that service. The shell owns touches over the sensor circle, lights the spot and writes `touch` and `ui_ready` to the 0034 device. Check what Phosh's lock screen supports before choosing.

## If gate 0 fails

- Look at the failure first (`gate0-check.sh` prints the cases).
- If the TrustZone simply rejects the call (`-EIO`, so no qcomtee device), only route B is left. That means porting legacy QSEECom (app loading plus listeners) into mainline `qcom_qseecom`: weeks of kernel work with downstream source to follow, but no precedent on this SoC.
- Otherwise, leave fingerprint parked, as GN `docs/PORTING.md` L189 has it. Kernel patch 0034 and gates 1–2 can still land as groundwork, but don't call them progress toward authentication (HD's warning applies).

## Safety

- **Don't touch RPMB provisioning or keys outside the stock TrustZone apps' own use.** RPMB writes are irreversible.
- Back up `persist` first. Goodix's app and secure storage write there in normal use: OM saw `ss/nsp` change on the Pixel's first match.
- Keep a software credential as the fallback unlock. A raw interrupt is never authentication.
