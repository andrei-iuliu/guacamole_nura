# Opt-in ModemManager candidate: primary SIM slot from the Primary GW session

Status: **source candidate only, not built, not installed, not integrated.** There
is no aarch64 build plan here (see “Build plan”); the only rootlessly verified
operations are the patch application and the compiled selection-helper tests.
Nothing in this directory is referenced by the default rootfs build list, the
device package, the delivery scripts or the RC label. The previous
identifier-from-EF2FE2 workaround is **withdrawn and archived** under
`superseded/`; it is not part of the recipe.

## Why (the mapping is now measured)

User-run `--slot-summary` on a locked boot:

| Observation | Value |
|---|---|
| Physical slot 1 | card present, slot **active**, logical slot 1 |
| Physical slot 2 | card present, slot **active**, logical slot 2 |
| ModemManager `primary-sim-slot` | **2** (last ACTIVE wins) |
| Primary GW provisioning | **slot 1**, application 1 |
| Secondary GW provisioning | slot 2, application 1 |
| Prior EF2FE2 read | exactly **slot 1** |

`uim_get_slot_status_ready()` assigned `active_slot_number` for every ACTIVE
entry without a break, so with two ACTIVE slots the **last** (slot 2) became the
primary SIM, while `UIM Read Transparent` follows the **Primary GW** session
(slot 1). The cached ICCID was slot 2's, the recheck returned slot 1's, and the
modem was reprobed every cycle.

The authoritative invariant is not “first ACTIVE”, “last ACTIVE” or “physical
slot 1”: ModemManager's own card-status parser already defines “the SIM to use”
as the one identified by the **Primary GW** session
(`mm_qmi_uim_get_card_status_output_parse`, `mm-modem-helpers-qmi.c:2914+`), and
that is the same session `Read Transparent` uses with an empty AID. This patch
selects that session's physical slot.

## The patch (one file, `src/mm-shared-qmi.c`)

`0002-primary-sim-slot-selection.patch`:

1. `mm_shared_qmi_load_sim_slots()` issues **UIM Get Card Status first**, then
   the existing UIM Get Slot Status. The primary GW index is decoded as
   `((index & 0xFF00) >> 8) + 1` (high byte physical slot, low byte application,
   both 0-based), or left as “unknown” on any error.
2. `select_primary_sim_slot()` (pure `guint`/`gboolean` arithmetic, no QMI or
   GLib calls) chooses:
   * the Primary GW physical slot **only if it is in range, ACTIVE, and a SIM
     object was successfully constructed for it** (ICCID bytes alone are not
     enough: hex conversion or `mm_3gpp_parse_iccid()` may have failed);
   * otherwise **exactly the previous behaviour: the last ACTIVE slot**, with no
     extra “has a SIM” preference in the fallback (no unrelated no-card policy
     change).
3. The SIM slots array is built exactly as before (same `mm_sim_qmi_new_initialized`
   arguments, ICCID/EID parsing, active flags); only the reported primary slot
   changes, from the last ACTIVE to the selected one. The obsolete
   `active_logical_id` field is removed from `LoadSimSlotsContext`: it was only
   assigned and never read. The switch-slot path keeps its own local
   `active_logical_id` (it is passed to `Switch Slot`) and is untouched.

No physical slot number is hardcoded, and no “first/last ACTIVE” rule is applied
when the session is valid.

Source justification for the two conditions: the returned index is consumed by
`load_sim_slots_ready()` in `mm-iface-modem.c`, which binds `MM_IFACE_MODEM_SIM`
to `sim_slots[primary - 1]`. Returning an INACTIVE slot would expose a non-active
SIM as the current one, and returning a slot whose `sim_slots` entry is NULL
would leave the modem without a primary SIM even when another ACTIVE slot has a
usable one. The ACTIVE check also rejects a stale Primary GW index left over from
before a slot switch.

### Tradeoff

* Always querying card status costs **one extra UIM request per slot load**
  (probe and each reprobe). Card status is already queried during initialisation
  for the unlock checks, so the service/message is supported on this path.
* Narrower alternatives: query card status only when more than one slot is
  ACTIVE (less traffic, more branches and a second parse path), or use the
  slot-status `logical_slot == 1` as primary (one line, but relies on a
  “logical slot 1 is primary” convention that the API does not state). The
  card-status session is the only value that directly matches what
  `Read Transparent` uses, so it is the chosen mapping.

## Behaviour

| Case | Result |
|---|---|
| One ACTIVE slot, Primary GW on it | Selected; same as before. |
| Primary GW points at a non-ACTIVE slot (stale index) | Ignored; last ACTIVE slot selected (previous behaviour). |
| Both ACTIVE, Primary GW slot 1 | Slot 1 selected (fixes the observed loop). |
| Both ACTIVE, Primary GW slot 2 | Slot 2 selected; nothing is hardcoded to slot 1. |
| Both ACTIVE, physical/logical order reversed | The ACTIVE Primary GW physical slot wins; logical numbers are not used for selection. |
| Card status unavailable / no Primary GW session | Last ACTIVE slot (exact previous behaviour). |
| Primary GW slot ACTIVE but its SIM failed to build (invalid ICCID / hex error) | Fallback last ACTIVE slot; no NULL-SIM primary is forced. |
| Primary GW slot has no SIM in slot status | Fallback last ACTIVE slot. |
| No card in any slot | Last ACTIVE slot with a NULL SIM object, as before. |
| Real SIM swap while disabled | Detection unchanged: the swap check still compares the cached pre-swap EF2FE2 read with a fresh one; `complete_sim_swap_check()` and `modem_check_for_sim_swap()` are byte-identical. |
| Active-slot insert/remove/change | Unchanged: `uim_slot_status_indication_cb()` still reprobes on an active-slot status change. |
| Inactive-slot update / profile refresh | Unchanged: `update_sim_from_slot_status()` and the refresh path are untouched. |
| PIN-locked probe | Unchanged: slot selection does not read the SIM; the lock/unlock flow is untouched. |

## Unverified preconditions and gaps

1. **Full build/runtime:** the patch applies to the exact pinned source and the
   pure helper is compiled and tested on the host, but the full ModemManager
   build (including the new libqmi card-status callback) has not been compiled or
   run. The helper compile is not a claim about the full MM build. The new call
   shapes were matched against the existing in-tree use
   (`mm-broadband-modem-qmi.c:1110-1115`, `unlock_required_uim_get_card_status_ready`)
   and libqmi's own qmicli caller, but compilation is still required.
2. **Aarch64 package:** no build plan is provided here (see below).
3. **Firmware semantics:** the patch relies on Get Card Status's `index_gw_primary`
   being the session that `Read Transparent` follows. That matches the API and
   MM's own parser, but has not been observed together with the EF read on this
   firmware after the change.
4. **Live test:** no phone action was performed.

## Build plan (open item — no command provided)

The delta is architecture-independent source, but the package is not: the
retained r0 APKs are `arch = aarch64` (`modemmanager`, `libmm-glib`; `-lang`,
`-systemd`, `-udev` are `noarch`), and `arch="all"` means “buildable on all
architectures”, not that a host build is installable. Do not run `abuild -r` on
an x86_64 host and treat the result as the phone package. Producing r1 needs a
supported aarch64 build environment (for example the existing
pmbootstrap/abuild aarch64 setup); that workflow was not verified here and no
cross-build command is provided.

## Verified rootless step: apply the delta inside an aports checkout

Tested in a synthetic git tree (`git init` + `main/modemmanager/APKBUILD`):

```sh
git -C aports checkout 50e299499516a04bde89c5566034ccfec6e1063a
cd aports/main/modemmanager
git apply --check /path/to/worktree/device/testing/modemmanager/apkbuild.patch
git apply /path/to/worktree/device/testing/modemmanager/apkbuild.patch
cp /path/to/worktree/device/testing/modemmanager/0002-primary-sim-slot-selection.patch .
```

The patch path may be absolute or copied locally and invoked as
`git apply apkbuild.patch`; both were tested. The delta is relative to
`main/modemmanager`; `git apply` from the aports root fails with
`APKBUILD: No such file or directory`, as expected. Only the
`git -C aports checkout <commit>` line is not exercised by the test.

## Install and rollback (source-only stage: no runnable commands)

Installing an unsigned local APK is **experimental** and is deliberately not
prescribed here. It would require, at minimum: a confirmed mapping (now
measured), a successful **aarch64** build, package signatures trusted by the
phone's apk policy (never `--allow-untrusted`), and the exact rollback APKs
retained locally. Rollback must not fetch from a repository (the phone has no
Internet uplink): keep the exact r0 APKs from
`bringup-notes/guacamole_nura/.work/pmbootstrap/cache_apk_aarch64/`
(`modemmanager`/`libmm-glib` aarch64 and the noarch subpackages) and verify their
signatures are trusted before any switch. If those APKs or their trust are
unavailable, rollback is not available from this source-only stage and the
candidate must not be installed. No configuration, udev rule or service unit is
changed by the candidate.

## Rootless validation

```sh
python3 -m unittest tests.test_modemmanager_primary_sim_selection -v
```

The suite checks the exact pinned APKBUILD identity and the delta (only
pkgrel/source/sha512 change; no invented checksum; the archived patch is not in
the recipe), applies the source patch to the exact pinned blobs, compiles the
extracted `select_primary_sim_slot()` helper with the host `cc` and runs
behavioural cases (single/dual ACTIVE; Primary GW on either slot; stale Primary
GW pointing at an INACTIVE slot; Primary GW slot with no constructed SIM; unknown
session; out-of-range index; no card; reversed order), and asserts that swap
detection, the slot-status indication path and the inactive-slot path are
byte-identical to upstream, and that the obsolete `active_logical_id` context
field is gone. Source-level checks skip with a message when no pinned
ModemManager checkout is available (`MODEM_MANAGER_SOURCE_REPO`).
