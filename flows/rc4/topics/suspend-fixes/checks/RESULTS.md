# Check results (suspend-fixes, rc4) — follow-up 3

Run on the integration host, 2026-11-13.  Rootless only: no sudo, no mount,
no phone, no package build.

Environment:

* `clang 23.1.4`, `--target=aarch64-linux-gnu`, `-print-resource-dir` include
* Kernel tree read for the checks:
  `flows/local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150`
  (7.0.0 + rc3 patches `0001`–`0041`, configured build in `.output`,
  `CONFIG_LOCALVERSION="-sm8150-rc3"`)
* Second, independent copy of the same patched sources:
  `bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150`
* rc3 recipe: `device/testing/rc3/linux-postmarketos-qcom-sm8150` (`pkgrel=4`)
* Device package: `device/testing/device-oneplus-guacamole` (`pkgrel=14`)

## Counts (corrected)

`checks/run_all.sh` prints **74 `PASS` lines**, 0 `FAIL`, exit 0:

* 7 from `checks/patch_apply_check.sh`: 2 "applies with -F0, no offset",
  1 "patched files match", 4 `fsyntax_check` objects;
* 33 from `checks/key_state_model.py`;
* 34 from `checks/check_fragments.sh`.

`checks/test_guacamole_mss_release.sh` prints only its summary line:
**38/38 cases**.  Total assertions: 74 + 38 = **112**.

The previous report mixed the two: it printed "71 PASS" and then listed
31+33+18 (= 82), which double-counted the helper's 18 cases as `PASS` lines.
The numbers above are what the commands actually print.

## Full suite

Exact output of `checks/run_all.sh`:

```
== 0044-soc-qcom-guacamole-mss-vote.patch
PASS: applies with -F0, no offset
== 0045-ath10k-snoc-wmi-key-ack.patch
PASS: applies with -F0, no offset
PASS: patched files match the reviewed sources
PASS drivers/soc/qcom/guacamole_mss_vote.c
PASS drivers/net/wireless/ath/ath10k/mac.c
PASS drivers/net/wireless/ath/ath10k/core.c
PASS drivers/net/wireless/ath/ath10k/wmi-tlv.c
PASS install completes on WMI ack
PASS install does not latch
PASS removal completes on WMI ack
PASS removal timeout returns -ETIMEDOUT
PASS removal timeout latches
PASS late ack dropped while desynced
PASS latched removal is not armed
PASS latched removal keeps the old 3 s timeout
PASS late ack did not satisfy the latched removal
PASS latch survives the removal
PASS late ack dropped while desynced
PASS latched install works through HTT
PASS late ack did not satisfy the latched install
PASS latched operations never return -EIO
PASS firmware restart re-arms the WMI wait
PASS WMI status error returns -EIO
PASS WMI status error latches
PASS install after status error works
PASS latched removal is not silently successful
PASS latched removal is not silently successful
PASS latched removal is not silently successful
PASS pending is armed exactly once
PASS pending is armed only when not desynced
PASS desync is latched on a failed wait
PASS desync is never cleared from mac.c
PASS desync is cleared by ath10k_core_start
PASS ath10k_install_key no longer returns -EIO
PASS both waits are gated by wmi_wait
PASS non-SNOC keeps the legacy handler
PASS ack match uses key_wmi_vdev
PASS ack match uses key_wmi_idx
PASS ack match uses key_wmi_flags
PASS ack match uses key_wmi_addr
key state model: 0 failed
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/fragments/device/guacamole-mss-release
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/checks/check_fragments.sh
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/checks/fsyntax_check.sh
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/checks/patch_apply_check.sh
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/checks/run_all.sh
PASS syntax runtime-workspace/flows/rc4/topics/suspend-fixes/checks/test_guacamole_mss_release.sh
PASS syntax key_state_model.py
PASS no automatic mpss-start hook
PASS release helper gates on the proxy handover
PASS handover accepts the exact status suspended
PASS handover rejects unknown/empty status
PASS modem is rechecked while waiting and before the write
PASS QCOM_GUACAMOLE_MSS_VOTE defined by 0044 and enabled as =m
PASS Kconfig has PM_DEBUG
PASS Kconfig has PM_SLEEP_DEBUG
PASS Kconfig has PM_ADVANCED_DEBUG
PASS Kconfig has PM_TEST_SUSPEND
PASS Kconfig has DPM_WATCHDOG
PASS rc3 config has CONFIG_ARCH_QCOM=y
PASS rc3 config has CONFIG_QCOM_RPMH=y
PASS rc3 config has CONFIG_QCOM_COMMAND_DB=y
PASS rc3 config has CONFIG_PM_GENERIC_DOMAINS=y
PASS rc3 config has CONFIG_PM_SLEEP=y
PASS rc3 config has CONFIG_SUSPEND=y
PASS unit ^\[Unit\]
PASS unit ^\[Service\]
PASS unit ^\[Install\]
PASS unit ^Type=exec$
PASS unit ^RemainAfterExit=yes$
PASS unit ^ExecStart=/usr/libexec/guacamole-mss-release$
PASS unit ^WantedBy=multi-user.target$
PASS preset fragment enables nothing
PASS modem-recovery udev rule
PASS inventory.json parses
fragment checks: 34 passed, 0 failed
guacamole-mss-release tests: 38 passed, 0 failed
```

## What the helper tests cover (38 cases)

Handover acceptance: exact `suspended` only.  `active`, `suspending`
(immediately and after a bounded wait), `resuming`, `unsupported`, an unknown
string, an empty file, a directory (read fails), a mode-000 file (when the test
user cannot read it), a missing/deleted path and a timeout all refuse to
write.  The modem must be `running`: it is re-checked on every handover poll
(a test stops the modem during the wait) and immediately before the write (a
fake `modprobe` stops the modem; the parameter must stay untouched).
`MSS_ALLOW_UNVERIFIED_HANDOVER=1` forces an unobserved handover but does not
bypass either modem check.  `MSS_LEVEL=3` read-back, failed `modprobe`, no
modem, two modems and the modem timeout are covered as before.

## Key state model (33 checks)

`checks/key_state_model.py` mirrors `ath10k_install_key()` and
`ath10k_wmi_tlv_key_complete()` and asserts:

* an install or removal completes on a matching WMI ack;
* the first timeout latches; from then on the WMI wait is **not armed**, so a
  delayed acknowledgement for the same vdev/peer/key/flags (the repeated
  removal case) is dropped, not matched;
* a latched install still works through HTT; a latched removal keeps the old
  3 s timeout and is never reported as successful without an HTT ack;
* no latched operation returns `-EIO`; only `ath10k_core_start()` clears the
  latch and re-arms the WMI wait;
* a firmware status error latches, and the next install still works.

Structural assertions bind the model to the applied source: `key_wmi_pending =
true` appears once and only inside `if (!ar->key_wmi_desynced)`,
`key_wmi_desynced = true` once on a failed wait, `key_wmi_desynced = false`
only in `core.c`, no `-EIO` in `ath10k_install_key()`, both waits gated by
`wmi_wait`, and the non-SNOC path calls
`ath10k_wmi_event_vdev_install_key_complete()`.

## Extra evidence taken by hand

Both new patches against the **second** patched tree (all hunks exact):

```
$ cd .../nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150
$ patch -p1 --dry-run -F0 --verbose < .../0044-...patch
Hunk #1 succeeded at 171.
Hunk #1 succeeded at 5.
Hunk #1 succeeded at 1.

$ patch -p1 --dry-run -F0 --verbose < .../0045-...patch
Hunk #1 succeeded at 2983.
Hunk #2 succeeded at 3686.
Hunk #1 succeeded at 1154.
Hunk #1 succeeded at 310.
Hunk #1 succeeded at 25.
Hunk #2 succeeded at 634.
Hunk #3 succeeded at 785.
Hunk #1 succeeded at 2680.
```

Supplied 0025/0026 are already in rc3 (same tree):

```
$ patch -p1 --dry-run -F0 < .../0025-misc-fastrpc-freezable-invoke-wait.patch
checking file drivers/misc/fastrpc.c
Reversed (or previously applied) patch detected!  Assume -R? [n]
Apply anyway? [n]
Skipping patch.
1 out of 1 hunk ignored

$ patch -p1 --dry-run -F0 < .../0026-input-s6sy761-enable-irq-after-resume-restart.patch
checking file drivers/input/touchscreen/s6sy761.c
Hunk #1 FAILED at 556.
1 out of 1 hunk FAILED
```

The patched files match `checks/expected-files.sha256`:

```
b977b1f...  drivers/soc/qcom/Kconfig
37b155f...  drivers/soc/qcom/Makefile
60cc326...  drivers/soc/qcom/guacamole_mss_vote.c
636c723...  drivers/net/wireless/ath/ath10k/core.c
6a31722...  drivers/net/wireless/ath/ath10k/core.h
f2105a6...  drivers/net/wireless/ath/ath10k/mac.c
10eb309...  drivers/net/wireless/ath/ath10k/wmi-tlv.c
4896146...  drivers/net/wireless/ath/ath10k/wmi-tlv.h
```

## Source evidence for the MSS handover audit

Read from the rc3-patched tree (`K` above); the topic README section 6 has the
same citations.

* `K/drivers/remoteproc/qcom_q6v5.c:67` — `qcom_q6v5_prepare()` enables the
  handover IRQ.
* `K/drivers/remoteproc/qcom_q6v5.c:136-143` — `q6v5_ready_interrupt()`
  completes `start_done`.
* `K/drivers/remoteproc/qcom_q6v5_pas.c:339` — `qcom_pas_start()` waits only
  for `qcom_q6v5_wait_for_start()`.
* `K/drivers/remoteproc/remoteproc_core.c:1311` — `rproc->state =
  RPROC_RUNNING` after `.start()` returns.
* `K/drivers/remoteproc/qcom_q6v5.c:163-180` — `q6v5_handover_interrupt()`
  calls the handover callback; it is a **separate** interrupt.
* `K/drivers/remoteproc/qcom_q6v5_pas.c:386-396` — `qcom_pas_handover()` →
  `qcom_pas_pds_disable()` (pstate 0 + `pm_runtime_put`).
* `K/drivers/remoteproc/qcom_q6v5_pas.c:154-178` — `qcom_pas_pds_enable()`
  sets the proxy performance state to `INT_MAX`.
* `K/drivers/pmdomain/qcom/rpmhpd.c` — `rpmhpd_set_performance_state()` maps
  `INT_MAX` to the last level; `:882` clamps an aggregate to the top corner
  while unsynced; `rpmhpd_sync_state()` (`:1096-1120`) re-votes
  `max(pd->corner, enable_corner)` with no genpd notification.

The kernel's `runtime_status` shows exactly `active`, `resuming`,
`suspending` and `suspended` (`K/drivers/base/power/sysfs.c:149-171`), which
is why only `suspended` is accepted and only `active`/`suspending` are waited
on.

`state=running` still proves only that the firmware booted, not that the proxy
domains were handed over, and a stopped modem also suspends the proxy.  Hence
the exact-status gate plus the modem re-checks; the runtime release remains
opt-in.

## Not run (and therefore not claimed)

* No kernel build, module build, `modules_install`, DT build or boot image.
* No `pmbootstrap`, no package build, no checksum.
* No phone access, no boot, no suspend/resume, no `pm_test`, no WoWLAN test.
* No test of the `mss.lvl` write, the proxy runtime status, the genpd notifier
  or the WMI key ack on hardware; `sync_state` behaviour and the cmd-db
  contents were not observed.
