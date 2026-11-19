# Cellular diagnostics: repeated SIM identifier change → modem teardown

Status: **source trace complete; the read-only diagnostic has reporting fixes
and focused regression tests; no live device action or modem behavior change
was made.** The remaining
blocker is a live fact that masked logs cannot provide: the actual slot-status
ICCID bytes versus the EF2FE2 bytes, and which of them produced the cached
ModemManager property.

The Wi-Fi/Bluetooth baseline, active netboot image, boot wrapper, kernel, DTB,
rootfs transport and modem ownership are untouched.

## Observed loop

`local/modem-diagnostic.log` (SHA-256
`743f7ca9962c34a22133f508adb734127495dd4de29cf86c1770695530c6e7a1`) shows a
~27-second cycle with three samples:

```
19:38:36  modem3 disabling -> disabled
19:38:38  qcom-soc created modem4 (2 ports)
19:39:03  modem4 power on
          SIM identifier has changed: '###' -> '###'
          registration searching -> disabling -> disabled
19:39:05  modem5 created
19:39:30  same SIM-change/disable
19:39:32  modem6 created
```

The log masks every identifier as `###` by design. It contains no APN/data
activation attempt and no evidence that the modem DSP/IPA path is absent.

## Installed source identity

| Component | Installed package | Exact upstream source |
|---|---|---|
| ModemManager | `1.25.95_git20261005-r0` | commit `7f0bf17ab518cd78925cf7e53cc0d5c42f88b41a` |
| libqmi / qmicli | `1.38.0_git20261005-r0` | commit `1c972b8ab901565f26b9f95e1705269277bbfa1b` |

The installed APK metadata points at Alpine aports commits
`50e299499516a04bde89c5566034ccfec6e1063a` (ModemManager) and
`14b8753b0ff769970ffb9113b72f0d48143c1f35` (libqmi). Those APKBUILDs pin the
upstream commits above. The neighbour checkout
`bringup-workspace/sources/modemmanager-current`
is exactly `7f0bf17` and was used read-only via `git show`; no checkout,
fetch or update was performed. Full hashes and the exact qmicli file identity
are recorded in ignored `local/cellular-source-pin.txt`; exact copies of the
APKBUILDs and qmicli service files are in ignored `.work/modemmanager-cell/`.

## Exact call chain

1. `src/mm-shared-qmi.c:uim_get_slot_status_ready()` (around 3347–3465)
   parses each physical slot's ICCID with
   `mm_utils_bin2hexstr()` → `mm_3gpp_parse_iccid()`, then creates the SIM with
   `mm_sim_qmi_new_initialized(..., dms_uim_deprecated = TRUE, slot = i + 1,
   sim_identifier = parsed_slot_iccid, imsi = NULL)`. This is the **cached**
   `MM_IFACE_MODEM_SIM` identifier used by the comparison.
2. `src/mm-sim-qmi.c:load_sim_identifier()` (around 416–432) chooses the
   **current** source when the SIM is rechecked:
   - `dms_uim_deprecated = FALSE`: DMS UIM Get ICCID, a string returned without
     `mm_3gpp_parse_iccid()` normalization.
   - `dms_uim_deprecated = TRUE`: UIM Read Transparent of EF2FE2 (`0x2FE2`, path
     MF `0x3F00`), then the same `mm_utils_bin2hexstr()` +
     `mm_3gpp_parse_iccid()` normalization.
   A slot-created SIM always has `dms_uim_deprecated = TRUE`, so the expected
   current source is EF2FE2.
3. `src/mm-broadband-modem.c:modem_check_for_sim_swap()` (around 4757) loads
   the current ICCID, then the current IMSI, and calls
   `complete_sim_swap_check()` (around 4583–4632). `g_strcmp0(current, cached)`
   emits the exact log line and calls
   `mm_iface_modem_process_sim_event()` when different.
4. `src/mm-iface-modem.c:iface_modem_process_sim_event_internal()` (around
   358–380) calls `cleanup_sim_hot_swap()`, sets the modem to reprobe it,
   disables it with an override lock, and after disable calls
   `mm_base_modem_set_valid(FALSE)`. The device manager then destroys the modem
   object and probes a new one. That is why the log shows a new modem index
   every cycle, not a simple same-object retry.
5. The enabling path invokes the check immediately after power-on
   (`ENABLING_STEP_CHECK_FOR_SIM_SWAP`, `src/mm-iface-modem.c` around
   4800–4810), which matches the `power state updated: on` → `SIM identifier
   has changed` timing. The QMI UIM refresh indication can also request the
   same check; both callers converge on the same comparison.

## Established facts versus hypotheses

**Established from source and log:**

- The loop is driven by `current != cached`. A successful unequal read or an
  exhausted read failure compared as NULL can produce this result. In
  `load_sim_step_ready()` (`mm-broadband-modem.c:4642–4683`), ordinary errors
  retry three times, then call `complete_sim_swap_check(task, NULL)`; unsupported
  errors skip the field. The supplied excerpt lacks identifier-read warnings,
  favoring a successful unequal read, but the masked line alone cannot prove it.
- For this QMI/qcom-soc path the cached value comes from UIM Get Slot Status;
  the normal recheck source is EF2FE2 (UIM Read Transparent).
- Both paths call the same MM ICCID normalization, so a same-byte ICCID must
  compare equal. A mismatch after a successful read can mean different input
  bytes, a different path/slot, or a cache/timing difference. Failed reads must
  be distinguished before drawing an encoding conclusion.
- `mm_log_str_personal_info()` returns the literal `###` for both a real value
  and `NULL`/empty when personal-info logging is off. The log cannot distinguish
  an empty cached value, cannot show lengths, and cannot show which source won.
- The `/dev/qrtr0` warning is not causal. `mm_port_qmi_reset()` builds
  `/dev/<port>` and fails for QRTR; `port_qmi_reset_ready()` logs and continues.
  QRTR is socket-backed. Do not create `/dev/qrtr0`.
- SUPL/predicted-orbit timeouts, `unhandled QMI radio interface '9'`,
  `autoconnect InvalidOperation 70`, and `Unknown device subsystem` have no
  path into the SIM comparison. They can slow the cycle but do not explain it.
- `msm-modem-uim-selection` runs before ModemManager, requires rmtfs and
  activates primary-gw provisioning. No restart or loop evidence appears in the
  log. A slot/provisioning mismatch remains a hypothesis, not a proven cause.

**Not established, and required before a justified patch:**

- Actual slot-status ICCID byte length, nibble order and padding versus EF2FE2.
- Whether the cached property is the slot-status value, a DMS value, or an
  older/asynchronous value.
- Whether the active slot reported by UIM Get Slot Status is the slot used by
  the primary-GW provisioning session for EF2FE2.
- Whether the SIM is unlocked at the exact sample; a locked SIM can make the
  EF2FE2 read unavailable.

A patch that suppresses the hot-swap event, globally disables SIM hot-swap, or
hard-codes an ICCID normalization would hide real SIM changes and is not
justified by the present evidence. No such patch was made.


## External reference inspected (read-only)

The coordinator supplied the exact Omarchy Mobile script and pinned commit
`f702f688288e59e3566f113d411ac7d18e4d0201`:

| Reference | SHA-256 |
|---|---|
| `devices/oneplus7pro/scripts/phone-cellular-status.sh` | `ed8603818145fab117c00bd219e9dca5053b9a48e93b5c416345fc34fdf2916c` |
| `devices/oneplus7pro/scripts/run-cellular-trial.sh` | `21779820e30c4a75a2aae17431ccff95797b54fa44a40df2c88d86ccdc630504` |
| `devices/oneplus7pro/scripts/phone-online-trial.py` | `5f812fe4ace68d9f7e73d229e9a3713ff177d324c5d17686c15bf159d6506394` |
| `devices/oneplus7pro/scripts/phone-radio-start.sh` | `f8142c5fe1d9ce489f8d7975d177f5e598a5a856174bef83a15e9bcb17001b62` |
| `devices/oneplus7pro/scripts/phone-qmi.py` | `6870ee31026546d8e57fc062c39aee82b0efc2452eb3dae694c111f2ffc3b3a2` |
| `devices/oneplus7pro/docs/cellular-sim-20260926.md` | `0e1aa864da061c82c40e60b52f06f00d84a362c8965af19e63f40aee78ebefa0` |

All of these were inspected read-only; none was executed.

`phone-cellular-status.sh` is a broad live-status helper for the Omarchy
Arch/chroot stack: it assumes modem index 0, `/root/radio-bringup/cellular`,
`diag-router`, `mss_vote`, NetworkManager and live HTTPS/IMS checks, and masks
only IPv6 addresses. None of that is available or appropriate in the Nura
netboot rootfs, and its `mmcli -m 0` assumption would fail while the modem is
re-indexed every cycle. It does not read or report an ICCID.

`run-cellular-trial.sh` is **deployment/trial, not a status probe**. It uploads
three phone scripts over SSH, loads `qcom_stats` and optionally `mss_vote`,
writes the AP's `mss.lvl` vote, runs `phone-online-trial.py`, pulls a DIAG
capture, and may reboot after a modem crash. `phone-online-trial.py` opens the
primary-GW provisioning session, sets DMS online/low-power, and samples NAS and
sleep counters. Both change radio and service state and are explicitly out of
scope for this worker; neither was run. They do not contain a ModemManager
ICCID-change workaround.

`phone-qmi.py` is a useful source comparison for holding one QRTR client across
multiple UIM/PDC operations. It does not implement UIM Get Slot Status or Read
Transparent, so replacing qmicli here would add a new untested raw-QMI surface.
The existing diagnostic keeps the public qmicli reads and is read-only.

The Omarchy known-working sequence is still useful as a **static** comparison.
`phone-radio-start.sh` and `phone-online-trial.py` open the primary-GW
provisioning session on **slot 1 with the USIM AID** before starting
ModemManager; the notes say the firmware and ModemManager do not open it by
themselves, and slot 2 is empty (`cellular-sim-20260926.md`). Nura's
`msm-modem-uim-selection` instead selects the first present USIM slot and AID
before ModemManager. Our read-only diagnostic now also reads
`--uim-get-card-status` and reports whether the active slot-status slot equals
the primary-GW session slot. A mismatch remains a hypothesis to test, not a
proven cause; the Omarchy notes do not report the Nura repeated ICCID-change
teardown.

No ModemManager ICCID or SIM-swap workaround exists in the reference. Its
known-working path explicitly opens the USIM session before ModemManager and
uses ModemManager 1.24.2 from Arch, while this Nura rootfs uses 1.25.95; that
version/setup difference is another reason to establish the live slot/session
state before changing source.

## User-run read-only diagnostic

Copy the current `scripts/cellular_iccid_compare.py` to the phone through the
user's normal workflow, then run locally on the original 6.17 baseline:

```sh
python3 /tmp/cellular_iccid_compare.py --local
```

Local mode allows normal sudo authentication at the terminal. SSH mode remains
available, but captured SSH authentication prompts may be awkward; prefer local
mode. `--no-sudo` is for environments that already permit the reads. Workers do
not perform live device or administrator actions.

The helper reads modem/SIM properties before and after four QMI queries:
slot status, card status, DMS ICCID, and EF2FE2. The corrected EF argument is
`--uim-read-transparent=0x3F00,0x2FE2`: pinned qmicli treats the final path
component as the file ID. The earlier reversed request did not test EF2FE2,
so its failure is not evidence of SIM lock. Both qmicli and MM use primary-GW
provisioning with empty AID for this read.

No slot, provisioning, PIN, radio or service changes are requested. Queries
open temporary QMI clients. Raw results and errors stay in process memory;
the report prints only lengths, industry prefixes, shared-domain per-run salted
hashes, physical slot numbers, primary/secondary GW application indices, safe
exit/error categories and numeric QMI error codes. No raw identifiers, AIDs,
PINs or arbitrary error text are printed or saved. Salt is secret and changes
per run: equal strings share a digest within one report, not across runs.

A missing property is UNAVAILABLE, a `--` value is the mmcli missing-value
sentinel, and an exposed zero-length property is EMPTY. They are distinct.
Unavailable comparisons say UNKNOWN. Cached comparisons also say UNKNOWN if
before/after modem and primary SIM paths are missing, changed or inconsistent.
Physical slot numbers are preserved even when slot 1 is empty; comparisons use
the uniquely active physical slot, not the first present card. Multiple active
slots are ambiguous and yield UNKNOWN comparisons.

These reads are sequential, not atomic. Unchanged object paths do not prove
unchanged SIM/session state between reads. Slot ICCIDs are qmicli's decoded
strings: internal A–F nibbles truncate its printed result, while MM supports
some such values. Reconstructing bytes from that string cannot recover the
original response and is no longer presented as a hardware observation.

## Reading the result

| Result | Evidence-supported next step |
|---|---|
| Stable identity, cached equals active slot but differs from successful EF read | Investigate slot decoding, provisioning/session mapping and sample timing; do not assume a parser bug. |
| Stable identity, cached equals EF but differs from active slot | Trace initial SIM source and slot mapping. |
| Cached equals DMS | Equality alone does not prove the legacy initialization path. |
| All views agree | A transient power-on/refresh mismatch remains possible. |
| EF unavailable | Use sanitized query status/error code; absence alone does not establish PIN lock. |
| Cached unavailable or object identity unverified | Rerun manually when a modem object exists; no actual empty cache or mismatch was established. |
| Active slot differs from primary GW slot | Session selection is a candidate; do not change provisioning as part of diagnosis. |

The smallest next test is one corrected local sample. Send only the helper's
safe report, never raw qmicli/mmcli output or unfiltered selection-service
logs (which may contain an AID). A root-cause patch requires the successful
EF-vs-active-slot comparison or a classified failure, plus usable cached data
from unchanged modem/SIM objects. Neither global SIM-swap suppression nor a
fake `/dev/qrtr0` node follows from the current evidence.

## Discriminator result (two cards) and the shortest next step

A user-run privileged sample (`USER_PRIVILEGED_COMPARISON.md` in the nura-rc2
modem investigation) changed the picture:

* `SLOT`, `CARD` and `READ` succeeded; `MODEM`/`SIM` were unavailable at both
  sample points (the loop removes the object), and `DMS UIM Get ICCID` returned
  QMI protocol error 94 = `QMI_PROTOCOL_ERROR_NOT_SUPPORTED` (libqmi
  `qmi-errors.h:317`), which says nothing about identity.
* **Both physical slots hold a card**, with distinct 19-digit `89`-prefix ICCIDs.
* The EF2FE2 read (10 bytes) normalizes/decodes exactly to **slot 1's** ICCID,
  not slot 2's (per-run salted hash equality).
* `Primary GW` provisioning session = slot 1 / application 1; `Secondary GW` =
  slot 2 / application 1.
* The old single-active parser reported “unavailable” because it had no way to
  print the per-slot status fields; that is ambiguous (zero or several ACTIVE
  slots). The earlier MM `PrimarySimSlot=2` is a different-time snapshot and the
  cached identifier was not sampled, so “cached = slot 2” is inferred from code,
  not measured.
* A separate **locked baseline** (fresh boot, same kernel) shows `modem0`
  `locked`/`sim-pin`, power off, `primary-sim-slot = 2`, `sim-slots.length = 2`,
  all DSPs running and no loop while locked. MM already selects slot 2 at probe,
  before any enable; different boot from the user-run sample, so the per-slot
  ACTIVE/logical fields are still needed for an atomic pairing.

Code consequence (ModemManager `7f0bf17`): `uim_get_slot_status_ready()` assigns
`active_slot_number` and `active_logical_id` for **every** ACTIVE entry with no
`break`, so with two ACTIVE slots the **last** wins (slot 2); EF2FE2 Read
Transparent instead follows the `PRIMARY_GW_PROVISIONING` session (slot 1).
qmicli's switch-slot helper takes the **first** ACTIVE slot. If both slots are
ACTIVE, cached=slot2 and current=slot1 on every probe, reproducing the loop — a
slot/session mapping fault (H2), not the single-source ICCID issue.

### Shortest next discriminator (read-only, identifier-free)

Run it on the next fresh boot **while the modem is still locked** (the state the
baseline above captured): no unlock, no PIN, no enable, no loop. It reports the
per-slot ACTIVE flags and logical slots that MM's probe saw, plus the same-instant
MM primary slot and the GW session slots.

```sh
# on the phone, from the copied helper; user-run
python3 /path/to/cellular_iccid_compare.py --slot-summary --local
```

It runs only `mmcli -m any` (state/primary slot), `--uim-get-slot-status` and
`--uim-get-card-status`, and prints **no ICCID/IMSI/AID, not even a hash**:
per-slot card status, slot status and logical slot; the active-slot list/count;
the same-instant `modem.generic.primary-sim-slot`; the Primary/Secondary GW
session slots; and a MATCH/MISMATCH line. It does not switch slots, attempt a
PIN, touch firmware or restart a service.

| `--slot-summary` shows | Next step |
|---|---|
| 2 active slots, modem primary 2, primary GW 1 | Mapping confirmed: align the session/primary selection (device `msm-modem-uim-selection`/slot choice, or MM's multi-active selection). Do not adopt the single-source patch. |
| 1 active slot = primary GW slot = modem primary, loop persists | Re-run the full four-view sample at that instant; the mismatch is then in the identifier sources (same-card H1/H3), not the slot mapping. |
| 1 active slot but primary GW/modem primary differ | Session/primary selection mismatch; fix that path first. |
| no active slot parsed | Check query status and the `Slot status:` values/schema before concluding anything. |

## Focused validation

```sh
python3 -m unittest tests.test_cellular_iccid_compare -v
```

The focused suite covers upstream MM vectors, ASCII-only normalization,
qmicli's lossy operator-hex decoding, padded exact-key parsing, missing/empty/
sentinel distinctions, shared per-run hashes, physical slot 2 selection,
identity changes, private error categorization and execution of generated
query commands against synthetic binaries. Tests never contact a phone.
Passing unit tests does not demonstrate registration, data, or reset recovery.

## Candidate source patch (opt-in, gated on the discriminator)

Status: **source candidate only; not built, not installed, not integrated, not
in the default rootfs build list or the RC label.** Nothing here changes the
active delivery artifacts.

Location: `device/testing/modemmanager/`. It is an **aports delta, not a pmaports
package**: there is deliberately no `APKBUILD` file, so no build or install list
can pick it up by accident. The earlier identifier-from-EF2FE2 workaround is
**withdrawn and archived** under `superseded/` (not in the recipe).

* `0002-primary-sim-slot-selection.patch` — `src/mm-shared-qmi.c` only. It asks
  **UIM Get Card Status first**, decodes the **Primary GW** session index (high
  byte = physical slot, low byte = application, both 0-based), and selects that
  physical slot as primary **only while it is ACTIVE and a SIM object was
  successfully constructed for it** (ICCID bytes alone do not count). Otherwise it
  falls back to **exactly the previous behaviour, the last ACTIVE slot** (no extra
  “has a SIM” preference in the fallback). The selection is a pure
  `select_primary_sim_slot()` helper (no QMI or GLib calls), compiled and
  behaviour-tested on the host. The obsolete `LoadSimSlotsContext.active_logical_id`
  (assigned, never read) is removed; the separate switch-slot path keeps its own
  local variable. The invariant is ModemManager's own:
  `mm_qmi_uim_get_card_status_output_parse()` already defines “the SIM to use” as
  the Primary GW one, and `UIM Read Transparent` follows that same session. No
  physical slot number is hardcoded and no first/last-ACTIVE rule is applied when
  the session is valid.
* `apkbuild.patch` + `APKBUILD.upstream` — delta against the exact pinned Alpine
  APKBUILD (sha256 `c952de6a…`, aports `50e2994…`, ModemManager `7f0bf17…`,
  tarball sha512 `e049eec…`): `pkgrel` 0→1, adds the source patch and its
  recomputed sha512. Dependencies, sources and all other checksums are unchanged
  (no invented checksum).
* `README.md` — selection rule and fallbacks, the one-extra-card-status-request
  tradeoff, preserved swap/indication paths, unverified aarch64 build/runtime,
  the rootlessly verified aports-apply step, and why no runnable install/rollback
  command is provided at this source-only stage.

**Mapping status: measured.** `--slot-summary` shows both slots present/ACTIVE
with logical slots 1 and 2, the locked modem reporting primary **slot 2**, and
the Primary GW provisioning session on **slot 1** (Secondary on slot 2); the
prior EF2FE2 read matched slot 1. The candidate therefore targets
**selection**, not the identifier comparison. It is still opt-in: review, an
aarch64 build and a runtime test are required before any integration.

**Packaging is not buildable with a host `abuild -r`.** The retained r0 APKs are
`arch = aarch64` (`modemmanager`, `libmm-glib`; the `-lang`/`-systemd`/`-udev`
subpackages are `noarch`), while `abuild` defaults to the host architecture and
`arch="all"` in the APKBUILD only means “buildable on all architectures”.
Producing r1 needs a supported **aarch64** build environment; no cross-build
command is provided or verified here.

**Unverified:** aarch64 build, install, runtime behaviour, and the live selection
on this firmware. The patch applies to the exact pinned source and the helper's
behaviour cases pass, and the new libqmi call shapes were checked against the
existing in-tree caller and qmicli, but the full ModemManager build and the new
card-status callback have not been compiled or run. No runtime stop of the loop
is claimed.

**Rollback:** only a package-version switch back to the exact retained r0 APKs
(`modemmanager`/`libmm-glib` aarch64 plus the noarch subpackages), and only after
their signatures are verified as trusted by the phone. It must not fetch from a
repository (the phone has no uplink) and must not use `--allow-untrusted`. If
those APKs or their trust are unavailable, rollback is not available from this
source-only stage and the candidate must not be installed. No configuration,
udev rule or service unit is touched by the candidate.

Rootless validation of the recipe, the exact patch application and the compiled
selection helper (single/dual ACTIVE; Primary GW on either slot; stale Primary GW
pointing at an INACTIVE slot; Primary GW slot with no constructed SIM; unknown
session; out-of-range index; reversed order; no card; swap/indication paths
unchanged; obsolete context field gone):

```sh
python3 -m unittest tests.test_modemmanager_primary_sim_selection -v
```
