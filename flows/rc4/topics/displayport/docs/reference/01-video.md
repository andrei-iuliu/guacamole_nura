# 01: DisplayPort Alt Mode video

**File:** `dts/0001-dt-displayport-video.diff`, which edits `base_guacamole_working.dts` only. No kernel or config change.

**Risk:** low to medium.
- Enabling the DP controller only affects external displays.
- The FSA4480 shares `i2c@890000` with the TFA9874 speaker amps. That bus already runs in FIFO mode (GN `0006`), so check that the speakers still work afterwards.

## What GN already has

| Piece | Where in `base_guacamole_working.dts` |
| --- | --- |
| DP controller `displayport-controller@ae90000` (`qcom,sm8150-dp`), wired to the DPU (port@0) and to the QMP combo PHY's DP input (port@1, endpoint phandle `0xb0` ↔ `0xc7`) | L3911–L3950, but `status = "disabled"` |
| QMP combo PHY with `orientation-switch`, connector port@1 ↔ PHY (SuperSpeed mux) | L3583–L3640, L4380–L4395 |
| PM8150B Type-C (`qcom,pm8150b-typec`) with a dual-role `usb-c-connector`, PD, port@0 ↔ DWC3 HS | L4355–L4395 |
| Kernel config `DRM_MSM_DP=y`, `TYPEC_DP_ALTMODE=m`, `TYPEC_MUX_FSA4480=y`, `PHY_QCOM_QMP_COMBO=y`, `DRM_AUX_HPD_BRIDGE=y` | `config-postmarketos-qcom-sm8150.aarch64` |

SuperSpeed over the combo PHY is already verified in GN: a SanDisk drive enumerated at 5 Gbps (GN `docs/PLAN.md` "USB host (OTG)").

## What the diff adds

1. **FSA4480 enable pin.** It adds `fsa_usbc_ana_en` under TLMM: GPIO 100, output-low (the enable is active-low). The stock guacamole tree has exactly this, in `LOS 18821/sm8150-pinctrl.dtsi` L3956–L3968. GPIO 100 is otherwise only named in the default state of the unused `qup-spi7` group.
2. **FSA4480 node** `typec-mux@42` on `i2c@890000` (QUP0 SE4, next to the speaker amps). It uses `compatible = "fcs,fsa4480"` with `mode-switch` and `orientation-switch`, and its `port` endpoint links to the connector. The stock guacamole tree puts it at the same place: `LOS 18821/sm8150-audio.dtsi` L162–L170, `&qupv3_se4_i2c { fsa4480@42 }`. The binding needs only `compatible`, `reg` and `port`, and the driver requests no interrupt. This switch routes the DP AUX channel over the SBU pins and flips it with plug orientation. Without it, link training can't read the monitor's DPCD or EDID.
3. **DP controller** gets the label `mdss_dp:`, `status = "okay"`, and `data-lanes = <0 1>` on its output endpoint. Two lanes are used because the other two carry USB3 (DP+USB pin assignment).
4. **Connector** gains:
   - `altmodes { displayport { svid = /bits/ 16 <0xff01>; vdo = <0x1c46>; } }`, so TCPM advertises DisplayPort and enters the mode.
   - `port@2` (SBU) linked to the FSA4480. This follows the `usb-c-connector` binding numbering: port@0 data role, port@1 SuperSpeed/DP mux, port@2 SBU.

How hot-plug reaches the DP driver: `qcom_pmic_typec` registers a DRM DP HPD bridge on the connector node (`devm_drm_dp_hpd_bridge_alloc()` in `drivers/usb/typec/tcpm/qcom/qcom_pmic_typec.c`, 6.17 L95). `typec_displayport` sends HPD to that fwnode, and the msm DP bridge chain ends there through the QMP PHY's aux bridge. This is why no `displayport = <&mdss_dp>` property is needed on the connector.

## Sources

- HD:
  - [`0049-arm64-dts-hotdog-enable-superspeed-and-displayport.patch`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0049-arm64-dts-hotdog-enable-superspeed-and-displayport.patch) (DP enable, data-lanes, altmode, port@2)
  - [`0037-arm64-dts-hotdog-enable-fsa4480-discovery.patch`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/aports/device/testing/linux-oneplus-hotdog-mainline616/0037-arm64-dts-hotdog-enable-fsa4480-discovery.patch) (FSA4480 node; same GPIO 100, same bus and address)
  - [`docs/evidence/2026-08-07-mainline616-usb-role.md`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/docs/evidence/2026-08-07-mainline616-usb-role.md), "SuperSpeed and DisplayPort" and "External display: usable at 60 Hz, corrupt at 120 Hz"
  - [`README.md`](https://github.com/Sr-0w/hotdog-linux-bringup/blob/47087509c4289c2580c54f5433e55366b2e00445/README.md) L92 and L152: "DisplayPort video at 2560×1440@60", validated on a 7T Pro
- LOS guacamole stock DT (`lineage-23.2`):
  - `arch/arm64/boot/dts/18821/sm8150-audio.dtsi` L162–L170 (FSA4480)
  - `sm8150-pinctrl.dtsi` L3956–L3968 (GPIO 100)
  - `sm8150-sde-display.dtsi` L558–L580 (`sde_dp` with `qcom,dp-aux-switch = <&fsa4480>`, `qcom,dp-usbpd-detection`)
  - `guacamole_t0.dtsi` L45–L53 (OnePlus's own `sde_dp` PHY-supply tuning for guacamole)

  Stock wired DP on this board.
- GN: [`docs/PLAN.md` L84–L88](https://github.com/emilyastranova/guacamole_nura/blob/5b2241bb5c1bd0af2ce455a4efdabd7e8a5d9e24/docs/PLAN.md#L84-L88) (USB host work this builds on)

## Differences from HD

- HD's `0049` also removed a USB 2.0-only description. GN never had that (it already does SuperSpeed), so that part is not needed.
- HD's `0037` dropped `dmas`/`iommus` on the I2C bus for FIFO mode. GN gets the same effect from kernel patch `0006` (geni I2C FIFO mode), so the bus is left as it is.
- HD wrote a `.dts` on top of the upstream tree. GN edits the flattened `base_guacamole_working.dts`, mixing new labels with existing numeric phandles; `dtc` assigns phandles to the new labelled nodes.

## Test

1. Boot without anything plugged in.
   - `dmesg | grep -i -E 'fsa4480|displayport|ae90000'`: FSA4480 probes, and `bound ae90000.displayport-controller`.
   - The speakers still play (same I2C bus).
2. Plug in a USB-C → DP/HDMI adapter or dock with a monitor.
   - `grep . /sys/class/typec/port0-partner/port0-partner.*/svid /sys/class/typec/port0-partner/port0-partner.*/active` shows `ff01` / `yes`.
   - `lsmod | grep typec_displayport` (it is a module, `=m`; it should autoload).
   - `cat /sys/class/drm/card*-DP-1/status` is `connected`, and `modetest -c` lists the monitor's modes (EDID read).
3. In Phosh, enable the external output at **60 Hz**. If the image is corrupt, check the refresh rate first (see the known limit in the README).
4. Flip the plug and repeat (FSA4480 orientation).
5. Unplug: the connector goes `disconnected`, and USB gadget SSH comes back when connected to a PC.
6. Charging through a powered dock still works. GN sinks 5 V/3 A only, by design.

If step 2 shows no `port0-partner`, it's TCPM/PD. If there is a partner but no `ff01`, it's the altmode node. If `ff01` is active but DP stays `disconnected`, it's the HPD path: check `dmesg` for `drm_dp_hpd_bridge` and `msm_dp` and confirm the aux bridge is registered.
