# Findings: OnePlus 7 Pro in-display fingerprint

Everything here was read from the cited sources on 2026-11-13. Nothing was run on a phone.

## 1. Hardware

Stock device tree: `LOSK arch/arm64/boot/dts/18821/sm8150-mtp.dtsi` L100–L132. `18821` is guacamole: its `sm8150-mtp-overlay.dts` includes `guacamole.dtsi` and sets `qcom,board-id = <8 0>`, matching GN's `patch_dtb.py`.

```dts
oplus_fp_common {
	compatible = "oplus,fp_common";
	oplus,fp_gpio_0 = <&tlmm 90 0x0>;          /* vendor ID pin */
	goodix_optical {
		vendor-chip = <11>;                     /* FP_GOODIX_OPTICAL_95 */
		chip-name = "G_OPTICAL_18821_G2_4";
	};
};
goodix_fp {
	compatible = "goodix,goodix_fp";
	interrupts = <118 0>;
	goodix,gpio_irq = <&tlmm 118 0x1>;
	goodix,gpio_reset = <&tlmm 131 0x0>;
	goodix,goodix_pwr = <&tlmm 101 0x0>;
	power-mode = <2>;                           /* GPIO-switched supply */
};
```

| TLMM | Role | Stock pinctrl |
| --- | --- | --- |
| 90 | sensor variant ID | pull-up, input (`gpio_id2_default`) |
| 101 | supply enable, high = on (default `poweron-level` 1) | — |
| 118 | interrupt, rising edge | 2 mA, no bias, input (`gpio_goodix_irq_default`) |
| 131 | reset, low = held in reset | — |

- The **7T Pro** (HD) has the same pins and the same Goodix optical family (`G_OPTICAL_18865_G3`). HD's investigation (`HD docs/evidence/2026-08-19-fingerprint-goodix-udfps.md`) carries over.
- There is **no SPI node**: the sensor's data bus isn't described to Linux at all.
- **In GN:** none of the four GPIOs is used. 90 and 101 appear only in the default pin states of the unused `qup-spi8` and `qup-spi7` groups.

## 2. Stock software stack

**Blobs** (`LOSD proprietary-files.txt` @ `b194543`):
- L422–L448: `odm/bin/hw/vendor.oplus.hardware.biometrics.fingerprint@2.1-service` (HIDL), `odm/bin/init.oplus.fingerprints.sh`, `odm/lib64/hw/fingerprint.18821_goodix9508_G2_4.default.so`, `odm/lib64/libgf_ud_hal_18821_G2_4_oplus.so`
- L399–L420: `vendor/lib64/libQSEEComAPI.so`, `vendor/bin/qseecomd`, `vendor.qti.hardware.qseecom@1.0`
- L451–L454: gatekeeper (`android.hardware.gatekeeper@1.0-service-qti`)
- L664 onward: keymaster

**HAL → TrustZone.** HD read the 7T Pro's `libgf_ud_hal`: it links `libQSEEComAPI.so` and calls `QSEECom_start_app`, `QSEECom_send_modified_cmd` and `QSEECom_shutdown_app`. Capture, image processing, template storage and matching all run in the TrustZone app. guacamole uses the same library family (`libgf_ud_hal_*_oplus`), and its own copy should be checked the same way, with `strings` and `readelf -d`.

**HAL → kernel**, the downstream driver at `LOSK drivers/input/oplus_fp_drivers/`:
- `goodix_optical_fp/gf_spi.[ch]`: misc-style char device `/dev/goodix_fp` (class `goodix_fp`). Ioctl magic `'g'`:
  - 0 `INIT` returns the netlink protocol (25)
  - 1 `EXIT`
  - 2 `RESET`: reset low 5 ms, then high, then 10 ms
  - 3/4 IRQ enable/disable
  - 5/6 SPI clock (no-op, the bus isn't the AP's)
  - 7/8 power on/off
  - 9 key event
  - 10 sleep
  - 11 firmware info
  - 12 remove
  - 13 chip info
  - 14 navigation event
  - 17 power reset: reset low, power off, 50 ms, power on, reset high, 3 ms
  - 18/19 wakelock
  - 20 clear touch flag
  - 21/22 send touch down/up
  - 23 stop waiting

  Everything except power commands returns `-ENODEV` while the sensor is off. Open requests the IRQ (rising, enabled, wake-capable); the last close frees it.
- `goodix_optical_fp/gf_netlink.c`: kernel netlink socket, protocol `NETLINK_TEST` = 25. The HAL's first message registers its pid, and events are unicast as one byte:
  - 1 IRQ
  - 2/3 screen off/on
  - 4/5 touch down/up
  - 6/7 UI ready/disappear

  An extended form carries touch coordinates (`netlink_msg_info`) only when the DT sets `notify_tpinfo_flag`; guacamole's DT doesn't.
- **Event sources downstream:**
  - IRQ: the sensor.
  - Touch down/up: the touchscreen driver, through `opticalfp_irq_handler()`.
  - UI ready: the display driver (`MSM_DRM_ONSCREENFINGERPRINT_EVENT`).
  - Screen on/off: the DRM blank notifier.
- `oplus_fp_common/`: `/proc/fp_id` (the chip name), `/proc/lcd_type` (`samsung`, `boe` or `tm`, from the kernel command line; guacamole is Samsung), `/proc/tee_bind_core` (write-only CPU pinning) and `/proc/fp_kernel_event` (reads one int: 1 = netlink in non-GKI builds, which `CONFIG_OPLUS_FINGERPRINT_GKI_DISABLE=y` selects; see `LOSK arch/arm64/configs/vendor/oplus.config`).

**Display.** The HAL drives illumination (HD, from `libgf_ud_hal`): `card0-DSI-1/hbm`, `aod`, `notify_dim`, `panel0-backlight/brightness` and `/proc/touchpanel/touch_hold`. The panel's HBM DCS sequences are in `LOSK .../18821/dsi-panel-samsung_oneplus_dsc.dtsi` (L382–L436 and L457–L505).

## 3. TrustZone transport on mainline

| Piece | 6.17 (GN today) | 7.0 | Notes |
| --- | --- | --- | --- |
| `drivers/firmware/qcom/qcom_qseecom.c` (legacy QSEECom) | yes, `CONFIG_QCOM_QSEECOM=y` + uefisecapp | yes | It can only find and message an app the firmware already loaded (`qcom_scm_qseecom_app_get_id`/`_send`). No app loading, no listener service, no userspace device. |
| `drivers/tee/qcomtee` (QTEE object invoke) | **absent** | present | It registers when `qcom_scm_qtee_init()`'s probe call `qcom_scm_qtee_invoke_smc(0,…)` does not return `-EIO` (`qcom_scm.c` L2477–L2498). It exposes the TEE subsystem device. `QCOM_TZMEM` uses an SHM bridge and falls back to generic mode on `-EOPNOTSUPP` (`qcom_tzmem.c` L89–L104). |
| Userspace | — | `MINK` | `libminkadaptor` (`smcinvoke_client`: diagnostics, callback objects, memory objects, loading a `.mbn` app), `libminkteec` (GP TEE Client API), `qtee_supplicant` with listeners `libfsservice`, `librpmbservice`, `libtimeservice`, `libtaautoload` |

Stock SM8150 has smcinvoke: `LOSK drivers/soc/qcom/smcinvoke.c` uses `SMCINVOKE_INVOKE_CMD 0x32000600`, which is SiP service 6, command 0, the same call mainline's `QCOM_SCM_SVC_SMCINVOKE`/`QCOM_SCM_SMCINVOKE_INVOKE` makes. `CONFIG_QCOM_SMCINVOKE=y` and `CONFIG_QSEECOM=y` are both set in `LOSK sm8150-perf_defconfig`.

The catch: on this generation, smcinvoke hands listener requests to the legacy qseecom driver (`qseecom_process_listener_from_smcinvoke()`, `smcinvoke.c` around L1148), and the Goodix app is a **legacy** QSEECom app.

So the two open questions are:
1. Can mainline QCOMTEE plus MINK load a legacy app and exchange buffers with it on this TrustZone?
2. Are its storage requests delivered in a form `qtee_supplicant` understands?

## 4. The precedent: omarchy-mobile on the Pixel 7 Pro

`OM devices/pixel7pro/fingerprint/README.md` (also a Goodix optical sensor, with its app in Trusty):
- The stock trusted app is loaded with the stock `trusty_apploader`. It waited forever until secure storage (`storageproxyd` over RPMB through `sg`) was up.
- The stock AIDL fingerprint HAL, KeyMint and gatekeeper run in a RAM-only chroot. They use binderfs, service managers and a preloaded shim (`fp-shim.c`: logging, SELinux, binder context manager, wake locks, properties, missing power and sensor services).
- A kernel driver gives the HAL its `/dev/goodix_fp` with the ioctl numbers read from the HAL.
- Local high-brightness spot (LHBM) for illumination.
- Results: enrollment of 23 presses, matching with signed tokens, and a boot-time service the shell talks to over a socket.

Pixel → guacamole differences:
- **Trusty → QTEE.** This is gate 0/3; Trusty already had a working mainline driver.
- **AIDL → HIDL 2.1.** Needs `hwservicemanager` and hwbinder.
- **A clean-room driver → the published OnePlus driver.** OnePlus's driver is GPL and in `LOSK`, so 0034 reproduces its interface exactly instead of guessing ioctl numbers.

## 5. What 0034 does and does not do

It does:
- power, reset and IRQ handling;
- the downstream ioctl, netlink and `/proc` interface;
- sysfs `touch`, `ui_ready` and `screen` for the events downstream drivers injected;
- power-off on last close.

It does not:
- implement the downstream `read()` event path (`FP_DRIVER_INTERRUPT`, GKI builds only);
- report key or navigation events;
- read the variant ID pin (90), since the chip name comes from DT instead.

`goodix,goodix_fp` and the `/proc` nodes aren't upstreamable; keep 0034 out of tree or GN-local.

## 6. Sources

- `HD docs/evidence/2026-08-19-fingerprint-goodix-udfps.md` (7T Pro: DTBO nodes, HAL dependency on QSEECom, illumination nodes, zero-edge GPIO test)
- `OM devices/oneplus7pro/docs/hardware-plan-20260922.md` §12 ("Fingerprint is a research track")
- `OM devices/pixel7pro/fingerprint/README.md`, `fp-shim.c`, `fp-client.c`, `kernel/fp-v36/`
- `LOSK` @ `0521dc2`:
  - `drivers/input/oplus_fp_drivers/{goodix_optical_fp,oplus_fp_common,include}/`
  - `drivers/soc/qcom/smcinvoke.c`, `drivers/misc/qseecom.c`
  - `arch/arm64/configs/vendor/{sm8150-perf_defconfig,oplus.config}`
  - `arch/arm64/boot/dts/18821/{sm8150-mtp.dtsi,sm8150-pinctrl.dtsi,dsi-panel-samsung_oneplus_dsc.dtsi}`
- `LOSD` @ `b194543`: `proprietary-files.txt`
- `MINK` @ `ff9eaf9`: `README.md`, `libminkadaptor/README.md`, `listeners/`, `qtee_supplicant/`
- 7.0: `drivers/tee/qcomtee/{Kconfig,call.c}`, `drivers/firmware/qcom/{Kconfig,qcom_scm.c,qcom_tzmem.c}`
- GN @ `5b2241b`: `docs/PORTING.md` L189, kernel config (`QCOM_QSEECOM=y`, `TEE=y`, `ANDROID_BINDER_IPC=y`, `# ANDROID_BINDERFS`, `# CHR_DEV_SG`, `SCSI_UFS_BSG=y`)
