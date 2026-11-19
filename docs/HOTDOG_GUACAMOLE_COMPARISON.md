# Sibling-device comparison: hotdog (OnePlus 7T Pro) vs guacamole (OnePlus 7 Pro)

Investigation record, 2026-11-14. Read-only: no build, no flash, no phone state
change. Companion to [FRONT_CAMERA_FEASIBILITY.md](FRONT_CAMERA_FEASIBILITY.md)
and [NFC_FEASIBILITY.md](NFC_FEASIBILITY.md); this page records *what the sibling
device actually did* and where our plan follows, diverges from, or silently
inherits it.

## 0. What "the sibling" means here, and where the evidence lives

| Source | State | Use |
|---|---|---|
| `Sr-0w/hotdog-linux-bringup`, branch `main`, `aports/device/testing/linux-oneplus-hotdog-mainline616/` | **authoritative**, kernel 6.16, patches `0102`–`0143` | every number and decision below |
| `flows/rc4/topics/popup-camera/` (this repo) | **stale snapshot**: 6.17-era text + an rc4 delta note, patches `0052`–`0054` ≈ HD `0104`–`0124` | the drivers and DTSI we actually ship |
| `hotdog-linux-bringup/` (repo root, per CLAUDE.md) | **no longer present** — the tree is gone from this workspace | nothing |

Consequence: the only hotdog material that influences our builds is the rc4
topic snapshot, and it is **not** current with the sibling's own later findings.
Where the two disagree, this page follows the GitHub repo and says so.

## 1. Popup camera: the sibling's actual path

Patch-by-patch (each line is one patch in the series; numbers are the repo's):

| Patch | What it established |
|---|---|
| `0104` | Driver + binding `oneplus,hotdog-popup-motor`; one-shot 320-µstep up-pulse |
| `0105` | Fix: `iio_read_channel_raw()` returns `IIO_VAL_INT`, not 0 |
| `0106` | Hall-terminated full course (`44160` µsteps), `restore_closed` |
| `0107` | Separates the 320 (restore) and 1920 (recover) Hall windows |
| `0109` | `approach 31680` / `finish_open 12480` split with a 130..200 Hall gate |
| `0111` | Driver becomes a **genpd provider**; sensor gets `power-domains` |
| `0113` | GPIO bit-banging → **PM8150L LPG PWM**; 960-µstep slow ramp; unified `move_profile()` |
| `0115` | Drops the 320-pulse direction check (false stalls on close) |
| `0117` | `HALL_SUM_MIN` 250 → **4** ("both endpoint sensors are naturally weak around mid-travel") |
| `0118` | Realisable LPG periods: slow **105000 ns**, fast **13125 ns** |
| `0119`/`0120` | Boost GPIO left on from probe, released only on remove |
| `0121` | Commit the new LPG period with the PWM disabled before restarting |
| `0122` | `close_with_retry()`: one retry after `msleep(250)` on `-EPROTO` |
| `0123` | 3200-µstep fast probe to clear endpoint backlash before declaring a stall |
| `0124` | `SETTLED_DOWN_MIN 350` + one 320-µstep re-seat if the close ends high |

Hard numbers (verbatim from their driver, and identical in our
`flows/rc4/.../hotdog-popup-motor.c`): full course `1380*32 = 44160`,
approach `31680`, finish-open `12480`, ramp `960`, progress probe `3200`,
preflight `320`, recover `1920`, `HALL_SAMPLE 64`, `PROGRESS_MIN_COUNTS 2`.

The sibling needed **more than a DT endpoint** for the sensor: `0055` (four
CSIPHYs), `0064` (its own CSIPHY generation — the v1.1 lane table), `0089`
(SM8150 C-PHY tables), `0067/0074/0075/0076` (camnoc/QoS/HF-AXI), `0114`
(unwind failed stream starts). **All of that is already in guacamole's
`0028-media-camss-sm8150-rebase.patch`** (`CAMSS_8150`, `csiphy_res_8150`,
`lane_regs_sm8150_cphy`, camnoc, stream-unwind all present) — so this is a place
where our reuse verdict genuinely holds, and it holds because rc4 descends from
the same rebase, not because we verified it independently.

## 2. The five divergences that change decisions

### D1 — CCI master for the front sensor: our doc is right, the rc4 DTSI is wrong

Three sources, now all read directly:

| Source | Front sensor (csiphy2 / cell-index 2) |
|---|---|
| Sibling working DT, `0102` | `&cci1_i2c0 { camera@10 { … } } }` → **CCI1 master 0**, boot log `imx471 6-0010` |
| Guacamole merged stock tree (`merged-libfdt.dts`) | `qcom,cam-sensor@2 { csiphy-sd-index = 0x02; cci-device = 0x01; cci-master = 0x00; }` → **CCI1 master 0** |
| Our rc4 topic DTSI (`dt/…-popup.dtsi`) | `cci1_i2c1` → **master 1**, with the comment "shares cci1 master 1 with the rear ultra-wide … exactly as stock does" |

Master 1 is what the *losing* overlay copy (`fragment@47`) said; the merged
result is master 0. So the rc4 snapshot's placement matches neither stock's
effective tree nor the sibling's working implementation, and
[FRONT_CAMERA_FEASIBILITY.md](FRONT_CAMERA_FEASIBILITY.md) §1.3/§8.3's
"master 0" working hypothesis is corroborated twice. **The shipped DTSI would
put the front sensor on the ultra-wide's bus and must be fixed before use.**

Two honest caveats: master 0 needs a net-new `cci1 i2c-bus@0` node (an
untested silicon path on this unit), and master 1 is the only CCI master proven
to work on this phone *today*. A probe-only build settles it in one boot.

### D2 — below `|down| = 340` the downward course is not Hall-terminated

`motion_endpoint(false)` is `is_closed()`, which needs `|down| >= 340`. At our
measured 303 it can never fire, so a close runs the **full 44160 µsteps**
(≈0.58 s of fast-period pulses against the endstop at the tail) and then

```c
/* hotdog-popup-motor.c:414 */
ret = -ERANGE;
```

and because `close()` is

```c
ret = hotdog_popup_move_profile(popup, false);
if (ret || abs(popup->after_down) >= HOTDOG_SETTLED_DOWN_MIN)
    return ret;                      /* -ERANGE wins here */
```

the re-seat branch is **never reached**, and `close_with_retry()` only retries
`-EPROTO`, never `-ERANGE`. So on this unit "closed" is unobservable and the
escape hatch is two independent failures, not one. Neither doc previously said
this; both now do (§5 R2a/R2b).

### D3 — the genpd is not an extra safety net until a consumer exists

`pm_genpd_init(..., false)` starts the domain **logically on**, precisely so
probing the sensor cannot move the mechanism. `power_on`/`power_off` fire only
when a genpd **consumer** runtime-resumes or suspends. Therefore:

* §7 step 2 (motor enabled, sensor node absent) really is inert: no consumer, no
  course.
* the moment the `sony,imx471` node with `power-domains` lands, the *first
  runtime suspend* runs a close course unattended — and with D2 that is a full
  44160-µstep drive returning `-ERANGE`.
* `domain_power_on` on a failed open calls `close_with_retry()` first
  ("never leave a failed opening at an unknown intermediate point"), which
  cannot succeed while D2 holds.

### D4 — the Hall rails are not the same voltage, so the counts are not directly comparable

| Board | Hall VDD |
|---|---|
| hotdog | `vdd-supply = <&vreg_l7c_3p0>` → **3.0 V** |
| guacamole | PM8150L LDO7, DT `regulator-min/max-microvolt = 0x002B9440` → **2,856,000 µV**, confirmed live |

The sibling's own note warns that a Hall sensor at the wrong VDD "still answers
but its counts are not comparable to the hotdog thresholds". Guacamole reads
`up = -10`, `down = -303` where hotdog reads `-13..-14`, `-369..-370` — a span
ratio of **0.82** against a rail ratio of **0.95**. That is the right shape for a
rail/magnet-geometry difference and the wrong shape for "same mechanism, same
numbers", so threshold rescaling is mandatory rather than optional. It also means
the rescaled constants are per-board, not per-unit only.

### D5 — VDIG is precedent, not proof

The vendor tree says `cam_vdig = pm8009_l2 @1.2 V` in `fragment@47` and
`pm8009_l3 @1.056 V` in the merged result. LDO3 is what both the sibling's
working tree (`vreg_l3f_1p05`) and our topic DTSI use, and the live DT carries
`allow-set-load` — so choosing LDO3 is right, but for **precedent + merge
precedence**, not because the rail was independently confirmed. Extra landmine
the docs lacked: rc3/rc4 override PM8009 L2 to ~1.056 V for the rear telephoto
actuator, so voting L2 would put two consumers on one regulator.

## 3. NFC: the sibling is the strongest available template — and a warning

| Patch | What it established |
|---|---|
| `0127` | `&i2c9 @100 kHz`, `nfc@28` `nxp,nxp-nci-i2c`, **IRQ 47 LEVEL_HIGH, EN 41, DWL 48**, pull-ups incl. **113**, GPIO 42 deliberately excluded |
| `0131`–`0133` | Binding + code + DT for a **platform NCI configuration blob** (`firmware-name`), applied after `CORE_INIT`; shipped as `/lib/firmware/nxp/pn553-hotdog.nci`, 774 B / 10 commands, extracted from the OxygenOS vendor partition |
| `0134`/`0135` | Diagnostics; stop one marginal read from ending the session |
| `0136` | Handle the proprietary set-mode response so `nci_req_complete()` runs |
| `0141`/`0142` | Optional reference clock **`clocks = <&rpmhcc RPMH_LN_BB_CLK3>`** (19.2 MHz) |
| `0143` | Report configuration parameters the controller refuses |

Their documented failure mode is the one our NFC doc now carries as risk 2a:
*"a missing reference is invisible over NCI: the controller answers every command
and accepts RF_DISCOVER, and simply never raises a carrier."*

Their final status (`docs/status.md`): reader mode works — a real ISO 14443-4
document is detected, activated, typed and exchanges ISO 7816-4 APDUs; three
rfkill cycles recover cleanly; the `-5` read is the expected unauthenticated
ePassport refusal. **"HCE and secure-element operation are explicitly outside the
reader-mode scope."** That matches this project's ceiling exactly.

Two things guacamole can take directly:

1. `ln_bb_clk3` **exists in our tree** (`clk-rpmh.c:354` `DEFINE_CLK_RPMH_VRM(ln_bb_clk3, _a2, "lnbclka3", 2)`), so porting `0141` + `0142` is a
   two-patch port, not research. Their pin set is identical to guacamole's vendor
   DTBO (47/41/48/113/42), so the clock hypothesis carries over with high prior.
2. The configuration-blob pattern (extract from `vendor.img`, commit into the
   non-free firmware package, install to `/lib/firmware/nxp/`, name it in DT) is
   the right shape for guacamole's `libpn553_fw.so`; it is *also* a warning that
   a clean probe alone proved nothing on the sibling device.

Their own record is not self-consistent about which fix was decisive (blob vs
clock vs FIFO), so do not inherit a causal story — inherit the order of
experiments.

## 4. What to copy verbatim, and what to re-measure

| Item | Verdict |
|---|---|
| Driver sources (`mxm1120.c`, `imx471.c`, `hotdog-popup-motor.c`) | copy — already in rc4, `=m`, compiled |
| CAMSS/CSIPHY/C-PHY/camnoc work | already present as `0028`; nothing to port |
| DT node shape (GPIOs, LPG ch 4, io-channels, `pinctrl` default/pwm) | copy — **verified** against guacamole's live DT byte-for-byte (§8.1 of the front doc) |
| Sensor node properties (0x10, MCLK2, 4 lanes, 525262500, LDO3/LDO1/BOB) | copy — guacamole's merged stock tree agrees |
| CCI bus (`cci1_i2c0` = master 0) | adopt from the sibling, **fix the rc4 DTSI** (D1) |
| Hall gates (`340/368/320/350/300/50/130/200/450/4`) | **re-measure**; they encode hotdog's mechanics and 3.0 V Hall rail (D4) |
| Course length 44160, ramp 960, probe 3200 | **treat as a ceiling, not a fact** — guacamole has never completed a course |
| NFC pins, 100 kHz, LEVEL_HIGH, DWL 48 | adopt — matches guacamole's vendor DTBO |
| NFC reference clock `RPMH_LN_BB_CLK3` | adopt as hypothesis 1 for "probe OK, no carrier" |
| NFC config blob | adopt the packaging pattern; extract guacamole's own from `vendor.img` |

## 5. Residual risk the sibling cannot answer

* Nobody has published popup behaviour on guacamole: no course length, no Hall
  span at 3.0 V vs 2.856 V, no free-fall retraction (hotdog has none either), no
  suspend evidence.
* The sibling's numbers moved a lot inside one series (open threshold 150 → 300,
  `HALL_SUM_MIN` 250 → 4, direction check 320 → whole ramp → 960 + 3200). Treat
  every constant as an iteration artifact of one handset.
* No cross-unit calibration data exists anywhere: both projects' thresholds come
  from a single device each.
* The sibling's aggregate patch `0156-hotdog-integrate-validated-runtime-fixes.patch`
  (75 KB) was not read line-by-line, so a later refinement to any constant above
  cannot be excluded.
