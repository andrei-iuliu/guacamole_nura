# NFC (NQ330) feasibility on OnePlus 7 Pro / SM8150

Local-evidence investigation, 2026-11-14. Read-only: no builds, no pmbootstrap, no
fastboot, no phone/SSH, no sudo. Every claim carries `file:line`.

Path keys: `EXT`=`bringup-notes/nura-rc3-20261112/lineage-dtbs/extract`
(LineageOS 24 boot DTBs + dtbo, decompiled); `K7`=`flows/rc4/build-source/kernel-state/linux-v7.0.0-sm8150`
(identical to `/tmp/kbase/linux-v7.0.0-sm8150`); `LIN`=`bringup-workspace/sources/lineage-kernel-sm8150`
(vendor/CAF 4.14); `VIMG`=`bringup-notes/guacamole_nura/.work/sensor-partitions/vendor.img`
(ext4, read via `debugfs`); `REPO` = this repository.

## 0. Verdict

**Binds with one board-delta DT patch; reaching a carrier is an open question.**
The controller is a stock NXP NCI part that mainline `nxp-nci` already drives, on
the same TLMM pin map that upsteam uses on a sibling SM8150 board (Sony kumano).
So the DT delta is small and the driver needs no per-chip code.

**Corrected after audit (2026-11-14): an earlier revision of this document said
"no kernel-driver work".** That is too strong. The sibling project's own NFC
series for the OnePlus 7T Pro — *the same part, bus, address and pin set* —
needed, beyond the DT node:

* an optional reference clock in `nxp-nci` plus a DT clock link
  (`0141`/`0142` in `Sr-0w/hotdog-linux-bringup`), because "a missing reference
  is **invisible over NCI**: the controller answers every command and accepts
  RF_DISCOVER, and simply never raises a carrier";
* platform NCI configuration firmware (`0131`–`0133`);
* proprietary set-mode response handling (`0136`) and two read-retry patches.

Consequence: "probe OK, no RF" is a **known, measured** failure mode on this
reference design, and the pin map — while real evidence — is inherited from the
shared Qualcomm MTP design rather than proof that this board's RF path is wired.
Treat §3 as the enabling step and §7 step 4 as the decisive experiment.
Realistic result if the carrier comes up: **tag reading / NDEF**; card emulation
(HCE) and eSE/payments stay out of reach.

## 1. Chip identity, bus, GPIOs

The NFC node is **not** in any boot DTB (`EXT/dtb-*.dts`: `nq@28` = 0 hits in all 20); it
arrives only via the dtbo overlay `dtbo-entry-00.dts` (`model = "... SM8150 MTP 18821"`,
`EXT/dtbo/dtbo-entry-00.dts:4`; 18821 = this phone's `oplus,dtsi_no` in
`EXT/extraction-manifest.json`).

| item | value | evidence |
|---|---|---|
| chip | `NQ330`; vendor HAL: `NXP_FW_NAME="libpn553_fw.so"`, `/dev/nq-nci` | `EXT/dtbo/dtbo-entry-00.dts:7043-7049` (`oplus-nfc-chipset`, `chipset-18821 = "NQ330"`); `VIMG:/etc/libnfc-nxp.conf:22,38` |
| node | `nq@28`, `compatible = "qcom,nq-nci"`, `reg = <0x28>` | `EXT/dtbo/dtbo-entry-00.dts:7147-7149` |
| bus | `qupv3_se9_i2c` = `/soc/i2c@a84000` (mainline label `i2c9`), pins `gpio39`/`gpio40` fn `qup9` | `EXT/dtbo/dtbo-entry-00.dts:10610`; `EXT/dtb-00.dts:22429,7781-7795`; resolved `EXT/merged/merged-libfdt.dts:27106-27123`; `K7/.../sm8150.dtsi:1346,2586-2591` |
| GPIOs | IRQ `gpio47` (level-high), VEN `gpio41`, DWL_REQ `gpio48`, CLK_REQ `gpio113` (input only), eSE pwr `gpio42` | DT `...:7150-7156`; `LIN/drivers/nfc/nq-nci.c:1368-1381,1433` (`IRQF_TRIGGER_HIGH`) |
| eSE chip | `nxp,p61` on `spi@880000` (qupv3_se0_spi), no mainline driver | `EXT/dtbo/dtbo-entry-00.dts:7166-7176`; `EXT/merged/merged-libfdt.dts:26809` |
| supply / clock | **none modelled**: no `*-supply`, no `clocks`/`clock-names`/`qcom,clk-src`, hence `clk_pin_voting = false` | `...:7147-7161`; `LIN/.../nq-nci.c:1190-1195` |
| pinctrl groups | `gpio47`; `gpio41+42+48`; `gpio113` | `EXT/dtb-00.dts:7812-7903`, phandles `0x325..0x32a` (`:21687-21692`) |

Family: NQ330 + `libpn553_fw.so` + `pn557`/`pn80T`/`sn100` strings in the vendor HAL
(`VIMG:/lib64/nfc_nci_nxp_snxxx.so`) ⇒ PN553/PN557 NCI family; the exact die (PN553 vs
PN557 vs PN80T) is **UNVERIFIED** and irrelevant, since mainline `nxp-nci` has one
`of_device_id` and no per-chip code (`K7/drivers/nfc/nxp-nci/i2c.c:331`).

Not in the vendor DT: which rail powers the part (no supply property anywhere; the only
`regulator-always-on` in the live pmOS DT is S4A, `base_guacamole_working.dts:5037-5270`),
whether an external reference clock exists (implied no — `clkreq` is an input and never
voted), the firmware file name (`/vendor/etc/libnfc-nxp.conf`), and the silicon revision
(read at runtime by `oplus-nfc-chipset`).

## 2. Which mainline driver matches

| driver | enable/info GPIOs | clk req | firmware DL | verdict |
|---|---|---|---|---|
| `nxp-nci` (I2C) | `enable-gpios` (required) + `firmware-gpios` (optional), `i2c.c:289,295` | not handled | yes, `nxp_nci_fw_download`, `firmware.c:196` | **match** |
| `pn544` (HCI) | same two GPIOs, `pn544/i2c.c:894,901` | not handled | yes, gated on `sw_romlib`, `pn544/pn544.c:770-778` | wrong protocol |
| `st-nci` | `reset-gpios` only, `st-nci/i2c.c:222` | not handled | ST vendor cmds | wrong vendor |

`nxp-nci` is protocol-generic NCI with **no quirk table** (grep `quirk` across
`K7/drivers/nfc/{nxp-nci,pn544,st-nci}/` → 0 hits); it registers two RF notifications
(`core.c:96-113`) and the NCI protocol mask (`core.c:23-29`). Its binding,
`K7/Documentation/devicetree/bindings/net/nfc/nxp,nci.yaml`, accepts `nxp,pn553` as a
fallback of `nxp,nxp-nci-i2c` (`:17-21`), requires
`compatible`/`enable-gpios`/`interrupts`/`reg` (`:35-40`), and knows nothing about a
clock-request pin.

Strongest data point: **Sony Xperia kumano is the same SM8150 reference design** —
`nfc@28`, `enable-gpios = <&tlmm 41>`, `firmware-gpios = <&tlmm 48>`, IRQ 47, no clock, no
clkreq, no supply (`K7/arch/arm64/boot/dts/qcom/sm8150-sony-xperia-kumano.dtsi:495-507`),
with identical `gpio-line-names` `41 NFC_EN`, `42 NFC_ESE_PWR_REQ`, `47 NFC_IRQ`,
`48 NFC_DWL_REQ`, `113 NFC_CLK_REQ` (`:639-752`). Also `sm7325-nothing-spacewar.dts:951-970`
drives a PN553 with enable+firmware GPIOs and no clock.

Config already correct: `CONFIG_NFC=m`, `CONFIG_NFC_NCI=m`, `CONFIG_NFC_NXP_NCI=m`,
`CONFIG_NFC_NXP_NCI_I2C=m`
(`REPO/device/testing/rear-camera/linux-postmarketos-qcom-sm8150/config-postmarketos-qcom-sm8150.aarch64:1737-1756`;
same in `REPO/device/testing/linux-postmarketos-qcom-sm8150/…:1676-1695`).
`CONFIG_NFC_DIGITAL is not set` only gates the virtual-nci test driver — irrelevant. The
modules are already built and installed (`guacamole_nura/build/kernel-nura-r25/drivers/nfc/nxp-nci/{nxp-nci,nxp-nci_i2c}.ko`;
`REPO/.../APKBUILD:110-115`), so **Kconfig needs no change**.

## 3. DT to add — patch `0038`

Additions belong in the board delta, included at the *end* of
`sm8150-oneplus-guacamole.dts`
(`REPO/device/testing/rear-camera/linux-postmarketos-qcom-sm8150/0026-…patch:560-577`), so
labels are available. Next free number is `0038` (`APKBUILD:75-76` ends at `0037`); add it
to `source=` and `sha512sums`. Two gotchas from the live tree:

1. `qupv3_id_1` is already `okay` (`sm8150-oneplus-common.dtsi:420-422`), so `&i2c9` only
   needs `status = "okay"`.
2. **Drop `dmas` from `&i2c9` as cheap insurance — but it is not a proven hard
   failure.** `gpi_dma1` is `status = "disabled"` (`sm8150.dtsi:1297`) while `i2c9`
   references it (`sm8150.dtsi:1351-1352`), and `dma_request_chan()` failure fails
   `geni_i2c_probe()` (`K7/drivers/i2c/busses/i2c-qcom-geni.c:958-987`). **However**
   `setup_gpi_dma()` is only reached when the SE reports `FIFO_IF_DISABLE`
   (`i2c-qcom-geni.c:1095-1101`), which cannot be read without root, and patch
   `0006-i2c-qcom-geni-fifo-mode.patch` already raises the driver's DMA threshold
   to 65536 so transfers stay in FIFO mode. Deleting the property matches the fuel
   gauge (`0026:294-298`) and costs nothing; describing it as a certain probe
   failure was wrong in an earlier revision.

```dts
&tlmm {
	nfc_int_default: nfc-int-default-state {
		pins = "gpio47"; function = "gpio";
		drive-strength = <2>; bias-pull-down;	/* not the vendor pull-up; see note below */
	};
	nfc_enable_default: nfc-enable-default-state {	/* VEN, DWL_REQ */
		pins = "gpio41", "gpio48"; function = "gpio";
		drive-strength = <2>; bias-pull-up;
	};
	nfc_clk_req_default: nfc-clk-req-default-state {
		pins = "gpio113"; function = "gpio";
		drive-strength = <2>; bias-pull-up;
	};
};

&i2c9 {
	status = "okay";
	clock-frequency = <100000>;
	/delete-property/ dmas;		/* QUP1 GPI DMA is disabled */
	/delete-property/ dma-names;

	nfc: nfc@28 {
		compatible = "nxp,pn553", "nxp,nxp-nci-i2c";
		reg = <0x28>;
		interrupt-parent = <&tlmm>;
		interrupts = <47 IRQ_TYPE_LEVEL_HIGH>;
		enable-gpios = <&tlmm 41 GPIO_ACTIVE_HIGH>;
		firmware-gpios = <&tlmm 48 GPIO_ACTIVE_HIGH>;
		pinctrl-names = "default";
		pinctrl-0 = <&nfc_int_default>, <&nfc_enable_default>,
			    <&nfc_clk_req_default>;
	};
};
```

**IRQ pull on gpio47 must be pull-down, not pull-up.** The snippet above copies
the vendor `bias-pull-up`, which is wrong for mainline `nxp-nci`. The driver holds
VEN (gpio41) low from probe and requests the level-high IRQ at once. With the chip
unpowered, a pull-up keeps gpio47 asserted: an IRQ storm of about 226k/s, every I2C
read fails with `-121`, and UFS clock scaling fails with `-110` because CPU0 is
starved. The vendor nq-nci driver enabled its IRQ only after powering the chip, so
the pull-up was harmless on Android. The shipped rc4l patch 0074 uses
`bias-pull-down` (source fix; rc4l build pending). See
[RC4K_NFC_SENSORS.md](RC4K_NFC_SENSORS.md#nfc-irq-pull-up-and-the-ufs-scaling-failure-2026-11-15).

`gpio42` (eSE power) stays unclaimed — mainline kumano leaves it alone too, and keeping
the eSE unpowered is the safe default. `IRQ_TYPE_LEVEL_HIGH` follows the vendor driver
(`IRQF_TRIGGER_HIGH`, `LIN/.../nq-nci.c:1433`) and msm8916-samsung; kumano and spacewar
use `EDGE_RISING` — treat edge-vs-level as the first knob if tag reads stall.

**Flattened-DTS variant** (`base_guacamole_working.dts`, temporary-boot path only): it has
no `nfc` anything (grep: 0 hits) and `i2c@a84000` is `disabled` with `dmas = <0x3e …>`
pointing at the disabled `dma-controller@a00000` (`:1299-1309`); its `pinctrl-0 = <0x41>`
is the QUP9 state (`:1368-1381`, `:2426-2431`). TLMM is `pinctrl@3100000`,
`phandle = <0x4e>` (`:2238-2251`); highest existing phandle is `0x539`, so new groups start
at `0x53a`:

```dts
nq@28 {
	compatible = "nxp,pn553", "nxp,nxp-nci-i2c";
	reg = <0x28>;
	interrupt-parent = <0x4e>;
	interrupts = <0x2f 0x04>;		/* gpio47, LEVEL_HIGH */
	enable-gpios = <0x4e 0x29 0x00>;	/* gpio41 */
	firmware-gpios = <0x4e 0x30 0x00>;	/* gpio48 */
	pinctrl-names = "default";
	pinctrl-0 = <0x53a 0x53b 0x53c>;
};
```

plus three `nfc-*-default-state` groups under `pinctrl@3100000` with
`phandle = <0x53a>…<0x53c>`, and delete `dmas`/`dma-names` on `i2c@a84000`. That file is
only used by `scripts/patch_dtb.py`/`prepare_temporary_dtb.py` (6.17 temporary boot,
`docs/TEMPORARY_BOOT.md`), so **0038 is the authoritative change**.

## 4. Firmware — the make-or-break question

- The driver **never** loads firmware by itself: a download needs userspace to issue NFC
  generic-netlink `NFC_CMD_FW_DOWNLOAD` (`NFC_ATTR_DEVICE_INDEX` + `NFC_ATTR_FIRMWARE_NAME`,
  `K7/net/nfc/netlink.c:1229-1248`), reaching `nxp_nci_fw_download()` →
  `request_firmware()` → `set_mode(NXP_NCI_MODE_FW)` (DWL_REQ high, then VEN high):
  `K7/drivers/nfc/nxp-nci/firmware.c:196-232`, `i2c.c:45-60`.
- File format: frames of `[u16 BE len][len bytes]` whose first payload byte is an NXP
  download command (`0xC0` write, `0xF0` reset, `0xF1` version; `firmware.c:21-30`). The
  driver splits frames into 28-byte chunks with the `0x0400` flag and appends its own
  CCITT CRC (`firmware.c:88-135`), so **the file must not contain CRCs**.
- The PN553 patch image **is already on disk**: `VIMG:/lib64/libpn553_fw.so` (37,490 B)
  exports `gphDnldNfc_DlSequence` (34,462 B), `gphDnldNfc_DlSeq`, `gphDnldNfc_DlSeqSz`
  (== `0x869E`; ELF `.data` at vaddr `0x10430`, file offset `0x430`). Parsing
  `.data[0x430..0x8ACE]` as `[u16 BE len][len bytes]` consumes **exactly** 34,462 B in
  **64 frames**; first frame `len=228`, first payload byte `0xC0` (WRITE), **no `0xF0`
  frame**; parsing with CRCs fails to align. That is byte-for-byte the format mainline
  expects, so conversion is a `debugfs`/`objcopy` slice of `.data` → `/lib/firmware/nxp/…`
  → one netlink command. (PN553 has no user-visible flash; Android reloads the blob on
  every NFC start.)
- **UNVERIFIED**: whether ROM code alone suffices. Every mainline precedent (kumano,
  spacewar) ships no firmware file and no `firmware-name`, suggesting basic NCI
  (CORE_RESET, discovery, tag read) works from ROM, but the vendor stack always patches,
  so ROM-only RF may be unreliable. Plan: ROM first, download on evidence of failure. No
  public mainline `.hcd` for PN553 exists in this workspace, and
  `REPO/scripts/extract_lineage_firmware.py:1-24` fetches only modem/WLAN/BT/IPA, so
  `device/testing/firmware-oneplus-guacamole/` has no NFC payload (grep: 0 hits).

## 5. Userspace

| layer | state | notes |
|---|---|---|
| kernel NCI | present | `/dev/nfc0` + `AF_NFC`/generic netlink; modules already shipped |
| `neard` | **available**: `neard-0.20-r0` (see §8.3); not installed | `apk search -x neard` on the phone. `REPO/device/**` hits for `neard`/`libnfc`: 0 |
| `libnfc` | not usable | drives PN53x UM/HCI registers over `i2c-dev`, not NCI; it has no `AF_NFC`/netlink backend, so it cannot use the in-kernel device |
| Android `libnfc-nci` | not viable | needs `android.hardware.nfc-service.nxp` (`VIMG:/etc/init/nfc-service-nxp.rc`) and a `/vendor` runtime |
| `nfcutils` | irrelevant | Android userspace over the same HAL |

**Correction (audit): "neard is the only userspace that can drive an in-kernel
NCI device" was too strong.** `neard` is the only packaged *daemon* with
tag/NDEF logic, but `nfctool` (same project, separate binary) drives the device
directly over generic netlink — including `--fw-download/-w`, which issues
`NFC_CMD_FW_DOWNLOAD` (`tools/nfctool/main.c`) — and any `AF_NFC` netlink client
can do the same. `neard`'s own `plugins/` has no NCI plugin because NCI lives in
the kernel.

With `neard`: adapter bring-up, discovery, NDEF tag read/write, SNEP push. **Card emulation
/ payments are not achievable** — HCE needs listen mode plus APDU routing, which `neard`
does not implement (UNVERIFIED by source read; consistent with the kernel side, where SE
support needs an eSE driver and `nxp,p61` has none).

## 6. Risks / blockers

| # | risk | severity | mitigation |
|---|---|---|---|
| 1 | `&i2c9` keeps `dmas` while `gpi_dma1` is disabled → *maybe* probe fails | medium, conditional (see §3 gotcha 2 — `setup_gpi_dma()` only runs when the SE reports `FIFO_IF_DISABLE`, and patch `0006` keeps transfers in FIFO) | delete `dmas`/`dma-names` (mirrors `i2c8`, `0026:294-298`) |
| 2 | ROM-only NCI may fail CORE_RESET/RF config | medium | firmware download (§4); blob already on disk |
| 2a | **No modelled reference clock.** Upstream `nxp-nci` has no clock support at all, and the vendor node has no `clocks`; the sibling hotdog project had to add both — with the failure mode "answers everything, never raises a carrier" (`0141`/`0142`). **Concrete candidate for guacamole: `clocks = <&rpmhcc RPMH_LN_BB_CLK3>`** — `ln_bb_clk3` already exists in our tree (`clk-rpmh.c:354`, `DEFINE_CLK_RPMH_VRM(ln_bb_clk3, _a2, "lnbclka3", 2)`), so this is a two-patch port, not research | **high — this is the most likely reason a clean probe still yields no tag** | if step 2/3 of §7 succeed but RF is dead, port `0141`/`0142`; do not conclude "wrong driver" |
| 3 | ~~No packaged tool for `NFC_CMD_FW_DOWNLOAD`~~ — **wrong, `nfctool -w/--fw-download <file>` exists** in the neard sources and issues exactly `NFC_CMD_FW_DOWNLOAD` (`tools/nfctool/main.c`) | low | check whether the distro ships `nfctool` separately from `neard`; no custom helper needed |
| 4 | IRQ edge-vs-level mismatch → missed first interrupt | low | `LEVEL_HIGH` first, then `EDGE_RISING` |
| 5 | Supply rail unmodelled; if gated, the chip is unpowered | medium | step 3 below distinguishes "no ACK at 0x28" from "probe ok, RF fails" |
| 6 | eSE (`gpio42`, `nxp,p61`) unpowered | none for tag reading | do not power it |
| 7 | Redistributing `libpn553_fw.so` outside the phone | low | keep out of the package; document local extraction |

## 7. Minimal first experiment (no flash, but not entirely "reversible")

1. Build the kernel package with the NFC DT patch **only** (no firmware, no
   userspace change) and RAM-boot it (`scripts/fastboot_boot.sh`, as usual). Name
   the baseline when numbering: `0038` is free in `device/testing/rear-camera`
   (`0037-dsi-cmd-frame-sync.patch` is the highest), but in `flows/rc4/…`
   `0038`–`0041` are taken (next free `0074`).
2. `dmesg | grep -i -E "nxp|nfc|i2c-geni"` → expect an `…-0028` probe and `/dev/nfc0`
   (`ls /sys/class/nfc`). A clean probe proves GPIOs, IRQ and I2C, not RF.
3. `i2cdetect -l` then `i2cdetect -y <bus of 0xa84000>` should show `0x28`.
   `apk add i2c-tools` first (it is packaged, §8.3) — and `sync` before any reboot,
   per the project rule.
4. **The decisive step, in this order:** (a) `nfctool -d nfc0 -1 -p` to poll for a
   tag on the untouched image; (b) if that is silent, try the firmware download
   `nfctool -d nfc0 -w nxp/pn553-fw.bin` using the blob from §4; (c) if the chip
   still answers everything and never raises a carrier, the missing reference
   clock is the prime suspect — port `0141`/`0142` (risk 2a).

The firmware download is no longer a last resort in the "changes chip state"
sense: `nxp_nci_fw_work_complete()` always returns the chip to COLD, which drives
VEN/DWL low (a hardware reset). What it *does* change is flash content on a
successful download, so run step 4a first.

## 8. Device measurements (2026-11-14, live rc4j, read-only)

Collected over SSH on the running phone (`7.0.0-sm8150-guacamole-rc4`). Nothing
was enabled, loaded, written or flashed. This section **supersedes conflicting
UNVERIFIED items above**.

### 8.1 Blocker 1 confirmed present in the live DTB

```
/proc/device-tree/soc@0/geniqup@ac0000/i2c@a84000/status = "disabled"
nodes: #address-cells #size-cells clock-names clocks compatible dma-names dmas
       interrupts name pinctrl-0 pinctrl-names reg status
```

Both facts matter: the NFC bus is **disabled** (so §3's DT delta is mandatory,
not optional), and the node still carries `dmas`/`dma-names` — risk 1 above is
live, not hypothetical. There is no `i2c-9`/`i2c-T` adapter for it in
`/sys/bus/i2c/devices` today.

### 8.2 Firmware blob verified byte-exactly against the driver's file format

Extracted from the on-disk `vendor.img` (`debugfs -R "cat /lib64/libpn553_fw.so"`,
real size 37490) and parsed with the symbol table:

| Field | Value |
|---|---|
| symbol | `gphDnldNfc_DlSequence`, ELF `.data` **file offset 0x430**, 34462 bytes (the 0x10430 in `readelf -s` is the vaddr, not the offset) |
| `gphDnldNfc_DlSeqSz` | `34462` (`0x869E`) at file offset 0x8ACE — equals the sequence length exactly |
| framing | 64 records of `[u16 BE header][payload]`, consuming **all** 34462 bytes, zero leftover, no CRC bytes |
| header flags | all `0x0000` — no `0x0400` chunk flag, all lengths ≤ 550 |
| payloads | **every record starts with `0xC0`** (`NXP_NCI_FW_CMD_WRITE`, `firmware.c:25`); payload lengths 102–550 |
| trailing commands | **none** — the sequence ends on a `0xC0` write; there is no `CHECKINTEGRITY (0xE0)` and no `RESET (0xF0)` |

This matches what `nxp-nci` expects **of the file**, exactly:

* `nxp_nci_fw_send()` reads `frame_size = be16(data) & FRAME_LEN_MASK` and skips
  the 2-byte header (`firmware.c:136-141`), so the file must be header-prefixed
  frames — which it is. (The kernel re-frames the payload to
  `MAX_PAYLOAD 32 - HDR 2 - CRC 2 = **28**` bytes per I2C write and appends its
  own CRC16-CCITT-LSB per chunk, `firmware.c:87-119`; that also means
  `NXP_NCI_I2C_MAX_PAYLOAD 32` constrains the *chunks*, not the file. An earlier
  revision of this section said 27 — wrong, and it changes the chunk count: the
  550-byte frames become 20 chunks, not 21.)
* `nxp_nci_fw_work()` advances `data += frame_size` per completed frame, which is
  why 64 frames × (2 + payload) = 34462 closes perfectly.

So the make-or-break answer is **positive and now evidence-backed**: the vendor
container is the download sequence in exactly the on-disk format mainline wants.
Two caveats, both corrected after audit:

1. **The blob has no `CHECKINTEGRITY`/`RESET` tail, but the kernel does not need
   one to leave download mode.** `nxp_nci_fw_work_complete()` calls
   `set_mode(NXP_NCI_MODE_COLD)` on **every** exit path, success or failure
   (`firmware.c:52-63`), and COLD drives VEN/DWL low — a hardware reset
   (`i2c.c:46-58`). So the chip does not get stuck in firmware-download mode; what
   is genuinely absent is a *post-download integrity check*, i.e. the kernel will
   report success without proving the image took.
2. **Every frame needs an ACK, and the first missing one aborts.** All 64 headers
   are plain writes, so `nxp_nci_fw_send()` waits `NXP_NCI_FW_ANSWER_TIMEOUT`
   (30 s, `firmware.c:20,152-158`) per frame; a timeout returns `-ETIMEDOUT`
   immediately → `fw_work_complete` → COLD reset. So a silent chip costs **one**
   30 s stall, not 32 minutes (an earlier revision of this section said ~32 min —
   wrong), and nothing is left mid-session.

### 8.3 Userspace availability — §5 `neard` UNVERIFIED resolved

The phone's APK repositories carry the packages (queried with `apk search -x`):

```
neard-0.20-r0
i2c-tools-4.4-r3
```

No `nfcutils` package. Nothing is installed yet (`apk info` shows no `neard`,
`libnfc`, `nfc*`). So the §7 experiment can run from packaged tools — `i2c-tools`
for the address probe and `neard`/`nfctool` for NCI — **no custom firmware
helper is needed** (`nfctool -w` exists; an earlier revision of this paragraph
said otherwise). Confirm on the phone whether `nfctool` ships in `neard` or as a
separate APK, and `sync` after installing either.

### 8.4 Kernel side is ready

```
CONFIG_NFC=m   CONFIG_NFC_NCI=m   CONFIG_NFC_NXP_NCI=m   CONFIG_NFC_NXP_NCI_I2C=m
# CONFIG_NFC_DIGITAL is not set      (and no NFC_NCI_I2C_{SPI,UART})
```

`modinfo nxp_nci_i2c` resolves locally from
`/lib/modules/7.0.0-sm8150-guacamole-rc4/.../nxp-nci_i2c.ko.zst`, depends
`nxp-nci, nci`. No NFC module is currently loaded, and there is no `nq@28` node
in the live DTB, so probe cannot happen until §3's patch lands.

### 8.5 Recommended first boot after the DT patch

Unchanged from §7, with the measured starting point: expect
`/sys/bus/i2c/devices/i2c-<n>/` for `reg = <0x28>` **and** a fresh
`/sys/class/nfc/nfc0`. If the adapter appears but `0x28` does not, read this
session's two hard facts first — the bus was `disabled` with `dmas` present and
there is no modelled supply or clock, so the remaining suspect is rail/clock
gating, not the driver.

## 9. Independent audit (three reviewers, 2026-11-14)

A read-only oracle re-derived §8 from the phone and the kernel sources:
**16/16 claims confirmed** (one PARTLY, one arithmetically wrong). A web
evidence-auditor and a consistency reviewer then corrected the following, all
applied in place above.

| # | Was | Now |
|---|---|---|
| 1 | "Feasible with **no kernel-driver work**" (§0) | "binds with one DT patch; reaching a carrier is open" — the hotdog NFC series on the identical pin set needed a reference clock, platform config firmware and set-mode handling | 
| 2 | `32 - 2 - 2 = 27` bytes per chunk (§8.2) | **28** (`MAX_PAYLOAD 32 - HDR 2 - CRC 2`); 550-byte frames are 20 chunks, not 21 |
| 3 | "chip stays in firmware-download mode; kernel never resets it" | wrong: `nxp_nci_fw_work_complete()` always calls `set_mode(COLD)`, which drives VEN low (a reset). What is missing is a post-download *integrity check* |
| 4 | "~32 min of timeouts if the chip is silent" | wrong: the first timeout returns `-ETIMEDOUT` and aborts → one 30 s stall, then COLD |
| 5 | risk 1 `dmas` = "high, probe fails, no bus" | conditional: `setup_gpi_dma()` only runs when the SE reports `FIFO_IF_DISABLE`, and patch `0006` keeps transfers in FIFO. Deleting `dmas` stays as free insurance |
| 6 | risk 3 "no packaged tool for `NFC_CMD_FW_DOWNLOAD`" | wrong: `nfctool -w/--fw-download` exists and issues exactly that command |
| 7 | "`neard` is the only userspace that can drive an in-kernel NCI device" | too strong: `nfctool` and any `AF_NFC` netlink client can; `neard` is the only packaged daemon with tag/NDEF logic |
| 8 | `NXP_NFC_CHIP=0x08` cited as PN553 evidence | dropped from the evidence: the canonical `libnfc-nxp.conf` chip table maps `#PN553 0x07` and `#PN80T 0x08`, so 0x08 points the other way. **PN80T stays an open possibility** (it changes nothing in the driver — `nxp-nci` has no per-chip code — but it does change firmware expectations) |
| 9 | "The sequence expects both `CHECKINTEGRITY` and `RESET`" | unsupported by this doc's own parse (no `0xE0`, no `0xF0` in the blob); the observation is now stated as a fact about the blob only |
| 10 | hotdog popup files cited as repo paths | (front-camera doc) they exist only inside patch `0104`; corrected there |
| 11 | `firmware.c` line cites `:21`/`:26` | `:20`/`:25` |

**What the audit changed about the conclusion:** the enabling path (§3) is
unchanged and cheap, but the document no longer promises that a clean probe
implies working NFC. §7 step 4 is now ordered load-tags-first, firmware second,
clock third.

**Sibling-device corroboration (2026-11-14, second review).** The hotdog repo's
NFC patches match guacamole's vendor DTBO pin-for-pin and add three things we
still lack: `i2c9 @100 kHz` with `interrupts = <47 IRQ_TYPE_LEVEL_HIGH>`
(`0127`), a **platform configuration blob** shipped as
`/lib/firmware/nxp/pn553-hotdog.nci` and named in DT via `firmware-name`
(`0131`–`0133`, 774 B / 10 commands extracted from the OxygenOS vendor
partition), and the reference clock (`0141`/`0142`). Their GPIO 42 (eSE power)
is deliberately **excluded** from the pull-up group — pulling it up "was enough to
collapse something" — which is a concrete argument for our risk 6. Their final
status is reader mode only, with HCE and secure element explicitly out of scope:
the same ceiling this document claims. Packaging pattern to copy for
guacamole's blob: extract from `vendor.img`, commit into the non-free firmware
package, install to `/lib/firmware/nxp/`, name it in DT. Details and the
pinned-item list: [HOTDOG_GUACAMOLE_COMPARISON.md](HOTDOG_GUACAMOLE_COMPARISON.md)
§3.

**Their record is not self-consistent about which fix was decisive** (blob vs
clock vs the Geni FIFO change `0138`), so inherit their order of experiments,
not their causal story.

**Still unverified:** whether guacamole feeds the PN5xx a reference clock at all
(the vendor node has no `clocks`, and `nq-nci.c` sets `clk_pin_voting = false` —
but hotdog proves DT silence is not evidence of absence); whether ROM-only NCI
can raise a carrier here; and `oplus_motor.c`'s own compatible string (the
DRV8834 subdriver matches `motor_drv-8834`, which neither of the two vendor node
names matches — check before treating the vendor DTBO as "the driver's binding").
