# rc4 fingerprint: gate-0/gate-1 groundwork

**Status: source-ready, compiled object/DT evidence only. No phone was used.
No fingerprint authentication works or is claimed.**  This topic carries the
Goodix sensor power/reset/IRQ groundwork and the QCOMTEE diagnostic path only.
It does not include the stock HAL, a TrustZone transport, illumination, a PAM
module or any unlock integration.

The user's current phone boots and then the lock screen crashed.  Nothing in
this topic establishes suspend/resume as the cause; suspend/resume is a
hypothesis from another topic, not a result here.  This topic does not change
suspend/resume code and does not claim to fix the lock crash.

## 1. Supplied files and dispositions

All six files under
`bringup-notes/Patches/fingerprint` were read.
The originals are copied byte-for-byte under `original/`; `inventory.json`
has their hashes and every output hash.

| Supplied file | Disposition | What happened |
| --- | --- | --- |
| `README.md` | integrated, corrected | Claims checked against real 7.0 sources; corrections and verdicts are below. Original kept. |
| `docs/findings.md` | integrated | Kept as evidence, copied to `original/docs/findings.md`. |
| `dts/goodix-fp.dtsi` | **gated** | Rebased into `patches/0055`; node is `status = "disabled"` and pinctrl adds the stock `input-enable`. |
| `gate0/config.fragment` | integrated / partly already present | `TEE=y` and SHM-bridge mode already exist in rc3; only `QCOMTEE=m` is new. |
| `gate0/gate0-check.sh` | **gated** | Rewritten passive-by-default; `--load` and `--diagnostics` are explicit opt-ins. |
| `kernel/0034-input-misc-add-goodix-fp.patch` | integrated as 0051, repaired | Rebased to 7.0/rc3, ioctl validation and lifetimes fixed, activation gated. Original kept. |

No userspace HAL or service is integrated.  `service/` contains only a
disabled, passive diagnostic unit template.  Gate 3/4/5/6 remain untouched.

## 2. Deliverables

```
README.md                                  this file
inventory.json                             every supplied file + hashes + deps
patches/0051-input-misc-add-goodix-fp.patch  driver, rebased from 0034
patches/0055-arm64-dts-guacamole-fingerprint.patch  DT node, status=disabled
dts/sm8150-oneplus-guacamole-fingerprint.dtsi       source of the DT file
src/goodix_fp.c, src/Makefile              reviewed driver source + compile check
config/gate0.fragment                      QCOMTEE=m (TEE/SHMBRIDGE already)
config/gate1.fragment                      INPUT_GOODIX_FP=m
config/release-name.fragment               -sm8150-guacamole-rc4
recipe/APKBUILD.additions                  source + sha512 lines
recipe/check_recipe.sh                     read-only recipe check
recipe/README.md                           merge order and caveats
scripts/gate0-check.sh                     passive gate-0 check
scripts/gate1-check.sh                     passive gate-1 presence check
service/fingerprint-gate0.service          disabled template, passive only
checks/                                    actual command logs and hashes
artifacts/                                 rc4-built .ko/.o/.dtb files
original/                                  untouched supplied inputs
```

The compiled `artifacts/goodix_fp.ko` has vermagic
`7.0.0-sm8150-guacamole-rc4 SMP preempt mod_unload aarch64`.  It is an
out-of-tree check build, not a packaged module.  The DTBs in `artifacts/`
were built by the patched in-tree source with the merged config.

## 3. Gates and safety

### Gate 0 (QCOMTEE/QTEE)

Use `config/gate0.fragment`, then run `scripts/gate0-check.sh` **without
arguments first**.  The passive check is read-only and is valid on the current
rc3 boot: `qcom_scm.c` calls `qcom_scm_qtee_init()` on every boot and
registers the `qcomtee` platform device whenever the smcinvoke probe does not
return `-EIO`.

`QCOMTEE=m` does not load itself, but it is **not passive by configuration
alone**.  `qcomtee` has a platform modalias (`MODULE_DEVICE_TABLE(platform,
qcomtee_ids)` in `qcomtee/call.c`); udev/systemd can autoload `qcomtee.ko` when
qcom_scm registers that platform device.  If it loads, `qcomtee_probe()`
issues a QTEE feature/version query.  For a truly passive gate-0 check, keep
`qcomtee.ko` out of the installed modules tree or boot with
`module_blacklist=qcomtee`, then load it manually only for the deliberate
check.  Do not describe the module setting as passive on a normal udev system.

`--load` (`modprobe qcomtee`) and `--diagnostics` (`smcinvoke_client -d 1`)
are explicit secure-world actions.  Neither was run here.  The input README's
gate-0 prose was corrected because it implied QCOMTEE causes the probe; the
probe already happens in `qcom_scm`, and the module can auto-bind it.

**Critical open risk:** the mainline probe command does not match the stock
one.  See the claim table and §8.

### Gate 1 (power/reset/IRQ)

It is deliberately gated twice:

1. the DT node is `status = "disabled"` in `patches/0055`; and
2. `goodix_fp` refuses `GF_IOC_ENABLE_POWER`, `GF_IOC_RESET` and
   `GF_IOC_POWER_RESET` unless loaded with `goodix_fp.allow_power=1`.

To run the gate-1 test, both steps are required: change the DT status to
`"okay"` and load the module with `allow_power=1`.  A reset-time interrupt is
the useful signal; a raw interrupt is still not authentication.  No sensor
was powered, reset or queried during this work.

### What was not done

No phone/SSH/adb, no sudo, mount/umount, pmbootstrap, flash, boot, process
kill, system change, commit, reset or stash.  No shell history, credentials or
private keys were inspected.  No RPMB, persist, secure-world or HAL action was
performed.  All writes stayed under
`flows/rc4/topics/fingerprint/`; the `Patches/` originals and shared recipes
are unchanged.

## 4. Integration steps for the parent

1. Create the rc4 recipe from the reviewed rc3 recipe; keep 0001-0041 bytes.
2. Apply `0051` then `0055` with `patch -p1 --fuzz=0`.  They touch disjoint
   files, but 0051 must precede 0055 conceptually because gate-1 config
   depends on the new Kconfig symbol.  0052 is reserved for the popup-camera
   Hall patch; never reuse that number.  The 0055 include is placed after the
   nura delta and before the rear-camera include, so the popup patch can own
   the end-of-file include slot; sequence/rebase it independently.
3. Merge `config/gate0.fragment`, `config/gate1.fragment` and
   `config/release-name.fragment` with
   `scripts/kconfig/merge_config.sh`, then `make olddefconfig`.
4. Add the two patch names and sha512 lines from
   `recipe/APKBUILD.additions`; run checksum only after the config is final.
5. Keep the new release name `7.0.0-sm8150-guacamole-rc4`.  Do not relabel,
   overwrite or reuse rc3 images, modules, DTBs or boot images.
6. Build and review the combined rc4 before any hardware trial.  Gate 0 must
   be checked before enabling the DT node or `allow_power`.

A ready-to-merge config was produced and diffed:
`checks/config-final.diff` contains exactly three changes from rc3:
`LOCALVERSION`, `INPUT_GOODIX_FP=m` and `QCOMTEE=m`.  `TEE=y` and
`QCOM_TZMEM_MODE_SHMBRIDGE=y` are already in rc3.

## 5. Claim / evidence / verdict table

The table separates source-level verification from hardware behavior.  A
clean patch apply or a compiled object is not a hardware result.

| Claim from the supplied material | Evidence (actual source/command) | Verdict |
| --- | --- | --- |
| Mainline QCOMTEE exists in 7.0. | `.work/rc3-kernel/linux-v7.0.0-sm8150/drivers/tee/qcomtee/` and `drivers/tee/Kconfig:23` | **verified** |
| 6.17 has no `drivers/tee/qcomtee`; gate 0 needs 7.0. | `ls .work/linux-v6.17.0-sm8150/drivers/tee/qcomtee` -> no such directory; no QCOMTEE in 6.17 `drivers/tee` | **verified** |
| `QCOMTEE` selects SHM-bridge mode, changing the allocator for all `qcom_scm` users. | `drivers/tee/qcomtee/Kconfig:8` selects it, but rc3 config already has `CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y` (config line 2068) and `GENERIC` unset | **contradicted for rc3**: enabling QCOMTEE does not switch the allocator; it is already SHM-bridge |
| Mainline QCOMTEE probes QTEE with the same call as stock, `0x32000600`. | 7.0 `qcom_scm.h:164-166`: `INVOKE_LEGACY=0x00`, `INVOKE=0x02`; `qcom_scm.c:2404-2405` uses `INVOKE` (0x02). Stock `smcinvoke.c` uses `SMCINVOKE_INVOKE_CMD 0x32000600` (cmd 0x00) | **contradicted**: mainline's fnid is `0x0602`, stock's is `0x0600`; gate 0 may still pass only if SM8150 firmware accepts both |
| QCOMTEE probe registers when the call does not return `-EIO`. | `qcom_scm.c:2477-2498`: returns on `-EIO`, otherwise registers `qcomtee` platform device | **verified** |
| The probe is a QCOMTEE action. | `qcom_scm.c:2812` calls `qcom_scm_qtee_init()` unconditionally at `qcom_scm_probe`; QCOMTEE only binds the driver | **clarified/contradicted**: the probe already runs on rc3 without QCOMTEE |
| 0034 keeps the downstream ioctl numbers, netlink protocol 25 and `/proc` node names. | Stock `gf_spi.h` and `gf_netlink.c` fetched from LineageOS `lineage-23.2`: ioctl numbers 0-23, `NETLINK_TEST 25`, `MAX_MSGSIZE 32`, event values 1-7; `oplus_fp_common.c` creates `fp_id`, `lcd_type`, `tee_bind_core` | **verified** for numbers/events/names; **not exact** for proc modes and write support (see below) |
| 0034 keeps the downstream interface exactly. | Stock `/proc/fp_id` is `0666` and writable; `/proc/tee_bind_core` is `0666`; 0051 makes `fp_id` `0444` and `tee_bind_core` `0200`. 0051 also prefers `NETLINK_CB(skb).portid` and falls back to `nlmsg_pid`. | **partially contradicted / deliberate**: gate-1 restrictions; restore parity only if gate-4 HAL needs it |
| GPIOs 90, 101, 118 and 131 are free; 90/101 only appear in unused SPI groups. | `sm8150.dtsi:2566` (`gpio98-101`, qup-spi7), `:2580` (`gpio88-91`, qup-spi8); no references to `qup_spi7_default`/`qup_spi8_default` in guacamole/common DTS | **verified** |
| There is no SPI node for the sensor in the 7.0 guacamole DTS. | `grep -rn goodix arch/arm64/boot/dts/qcom/sm8150-oneplus*` -> no matches | **verified** |
| Stock pin roles: 90 ID, 101 power, 118 IRQ, 131 reset. | Stock `sm8150-mtp.dtsi` fetched: `goodix,gpio_irq=<&tlmm 118 1>`, `gpio_reset=<&tlmm 131 0>`, `goodix_pwr=<&tlmm 101 0>`, `oplus_fp_common` uses 90; `sm8150-pinctrl.dtsi` `gpio_goodix_irq_default` = 2 mA, bias-disable, input-enable | **verified** |
| Mainline `qcom_qseecom` can only find/message an already-loaded app, not load one or serve listeners. | `qcom_qseecom.c` is 120 lines and calls `qcom_scm_qseecom_app_get_id`; no `QSEOS_APP_START`/listener code in `qcom_qseecom.c` or `qcom_scm.c` | **verified** for this tree |
| rc3 lacks binderfs and `CHR_DEV_SG`; stock has them. | rc3 config: `# CONFIG_ANDROID_BINDERFS is not set`, `# CONFIG_CHR_DEV_SG is not set`; stock `sm8150-perf_defconfig` has `CONFIG_ANDROID_BINDERFS=y`, `CONFIG_CHR_DEV_SG=y`, `CONFIG_ION=y`, `CONFIG_QSEECOM=y`, `CONFIG_QCOM_SMCINVOKE=y` | **verified** (gate 3/4 dependencies still absent) |
| `/proc/fp_kernel_event` reads 1 in the non-GKI stock build. | Stock `oplus.config`: `CONFIG_OPLUS_FINGERPRINT_GKI_DISABLE=y`; `fingerprint_event.h`: `FP_DIRVER_NETLINK=1`; stock `oplus_fp_common.c` selects netlink when GKI disable is set | **verified** |
| Gate 1: /dev/goodix_fp, /proc/fp_id, /proc/fp_kernel_event and reset-time IRQ work on guacamole. | No phone; DT disabled and `allow_power` false; only source/compile evidence | **unverified** (hardware) |
| Gate 2: HBM, spot at x=720/y=2728/r=137, green/white. | Not integrated; not rechecked against the panel driver | **unverified / out of scope** |
| Gate 3/4/5/6: QSEECom replacement, stock HIDL HAL, keymaster/gatekeeper, Phosh/PAM bridge. | Not implemented; no secure-world query or HAL launch | **blocked / not attempted** |
| Stock `libQSEEComAPI` needs `/dev/ion`; mainline lacks it. | Not inspected in the blob; no ion node in mainline config | **unverified** |
| The current lock crash is caused by suspend/resume. | No suspend/resume test or log in this topic; user reports boot works then lock crashes | **unverified hypothesis**, not established |

## 6. Actual checks performed (rootless)

All commands ran on the host.  The `checks/` logs are the source of record.

### 6.1 Patch application, no fuzz

```sh
# rc3 patched source is read-only:
SRC=.work/rc3-kernel/linux-v7.0.0-sm8150
patch -d "$SRC" -p1 --dry-run --fuzz=0 -i patches/0051-input-misc-add-goodix-fp.patch
patch -d "$SRC" -p1 --dry-run --fuzz=0 -i patches/0055-arm64-dts-guacamole-fingerprint.patch
```

Both exited 0 with no offsets, and `git apply --check` from the rc3 source
root also exits 0 for each.  A fresh reflinked copy of the rc3 source was
then patched in sequence; `checks/apply-sequence-final.log` shows both
`exit=0`.  No original source or recipe was modified.

### 6.2 Config merge and release name

```sh
scripts/kconfig/merge_config.sh -O <out> -m <rc3-config> \
  config/gate0.fragment config/gate1.fragment config/release-name.fragment
make -C <patched-src> O=<out> ARCH=arm64 LLVM=1 olddefconfig
make -C <patched-src> O=<out> ARCH=arm64 LLVM=1 -s kernelrelease
```

Results (`checks/config-symbols.txt`, `checks/kernelrelease.txt`,
`checks/config-final.diff`):

```
CONFIG_TEE=y
CONFIG_QCOMTEE=m
CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y
# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set
CONFIG_INPUT_GOODIX_FP=m
CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"
7.0.0-sm8150-guacamole-rc4
```

The diff from rc3 is only `LOCALVERSION`, `INPUT_GOODIX_FP=m` and
`QCOMTEE=m`.  `CONFIG_ANDROID_BINDERFS` and `CONFIG_CHR_DEV_SG` remain unset;
that is intentional for gates 3/4.

### 6.3 Compile

```sh
make -C <patched-src> O=<out> ARCH=arm64 LLVM=1 \
  drivers/input/misc/goodix_fp.o \
  qcom/sm8150-oneplus-guacamole.dtb \
  qcom/sm8150-oneplus-guacamole-rear-{main,tele,wide}.dtb
```

Exit 0; `checks/in-tree-compile.log` and `checks/in-tree-driver-final.log`.
All four DTBs contain the node with `status = "disabled"`; the decoded node
and pinctrl are in `checks/dt-fingerprint.txt`:

```
fingerprint {
    compatible = "goodix,goodix_fp";
    status = "disabled";
    interrupts-extended = <0x3e 0x76 0x01>;   /* tlmm 118, edge rising */
    reset-gpios = <0x3e 0x83 0x01>;           /* tlmm 131, active low */
    power-gpios = <0x3e 0x65 0x00>;           /* tlmm 101, active high */
    ...
};
```

`goodix_fp.ko` was also built as an external module against the merged rc4
config (`checks/module-build-final.log`); `modinfo` reports vermagic
`7.0.0-sm8150-guacamole-rc4` and the `allow_power` parameter.  `qcomtee.ko`
was built the same way (`checks/qcomtee-module-build.log`), so the gate-0
config is buildable.  The external-module checks used the rc4 output config
plus the rc3 `Module.symvers` (the config-only output has no vmlinux); BTF
generation was skipped for both modules, which is a tooling limitation, not a
source failure.  A full `vmlinux`/`modules` build remains for the parent.

`checkpatch.pl --no-tree --terse --file src/goodix_fp.c` exits 0 with no
output (`checks/checkpatch-final.log`).  Both shell scripts pass `sh -n`.

### 6.4 Lock-order static check

`checks/check_lock_order.py` is a small static regression for the deadlock
class: it rejects synchronous `disable_irq()` anywhere, rejects
`synchronize_irq()`, `devm_free_irq()`, `netlink_kernel_release()`,
`misc_deregister()`, `kfree(g)` and `gf_release_hw()` while `g->lock` is held,
requires the IRQ handler to take only `g->lock`, and requires
`misc_deregister()` before `ops_lock` in `gf_remove()`.  Its `--self-test`
feeds deliberately bad snippets and must catch each one.

```sh
python3 checks/check_lock_order.py --self-test      # self-test PASS
python3 checks/check_lock_order.py src/goodix_fp.c  # PASS
```

### 6.5 Recipe check

`recipe/check_recipe.sh` was exercised against a synthetic copy with the
additions and against the untouched rc3 recipe.  The synthetic copy passed;
the rc3 recipe failed with the expected missing-source/config messages.  The
script is read-only and does not run `abuild`.

### 6.6 What is not compiled or tested

- No full kernel `Image`, package, APK, boot image or rootfs was built.
- No `qcomtee` module was loaded; no `/dev/tee*` check was run.
- No phone, sensor power/reset, IRQ count, HBM or HAL test was run.
- No suspend/resume or lock-screen crash investigation was done here.

## 7. Source-level changes and review notes

### 7.1 Driver (0051, rebased from 0034)

Kept from 0034: the downstream `GF_IOC_*` numbers, netlink protocol 25 with
32-byte payload, event values, `/proc` names, power/reset/IRQ GPIO handling,
the sysfs `touch`/`ui_ready`/`screen` hooks and the downstream compatible
string.  Repairs:

- **ioctl validation:** `_IOC_TYPE` check, `access_ok()` for `_IOC_READ` and
  `_IOC_WRITE` ranges before dispatch, exact `copy_to_user`/`copy_from_user`
  sizes, and `-ENODEV` after the device is removed.  Downstream does the same
  `access_ok()` check; this restores it.
- **IRQ deadlock fix:** `gf_irq()` (threaded) takes `g->lock`; the old
  `disable_irq()` calls in ioctl/release/remove waited for that handler while
  holding the same mutex.  All disable paths now use
  `disable_irq_nosync()` under `g->lock`, drop it, and call
  `synchronize_irq()` outside.  A separate `ops_lock` serializes
  open/ioctl/release/remove so the line cannot be re-enabled or freed during
  the sync.  Lock order is `ops_lock -> g->lock`; the handler never takes
  `ops_lock`.
- **File lifetime:** `filp->private_data` now carries `struct gf_dev *`, the
  device is allocated with `kzalloc_obj()` rather than devm, and a `removed`
  plus `remove_done` state lets an open file survive an unbind without a
  use-after-free.  `remove()` deregisters the misc device before taking
  `ops_lock` (avoiding the `misc_mtx`/`ops_lock` ABBA), blocks new opens,
  waits for the threaded IRQ with `devm_free_irq()` after the nosync+sync
  pair, removes proc entries, clears the RCU global and only then frees the
  netlink socket.  `suppress_bind_attrs` keeps remove on module unload,
  which `fops.owner` blocks while a file is open.
- **Netlink registration:** prefers `NETLINK_CB(skb).portid` and falls back
  to `nlh->nlmsg_pid` (the stock field).  This fixes the case where userspace
  sends a message with `nlmsg_pid` left at zero.
- **Activation gate:** `allow_power` module parameter defaults to false; only
  `GF_IOC_ENABLE_POWER`, `GF_IOC_RESET` and `GF_IOC_POWER_RESET` are refused.
  Disable/close/cleanup always work.  The DT node is disabled independently.
- **Proc permissions:** `/proc/fp_id` is `0444` and `/proc/tee_bind_core` is
  `0200`, versus stock `0666`.  The write handlers in 0051 are absent or
  no-ops, so the stricter modes do not change gate-1 behavior.  This is a
  deliberate divergence; see the claim table.
- **No downstream `read()` path, key/nav input, variant-ID GPIO 90 read or
  downstream display/touch notifier.**  Those are unchanged omissions from
  0034.  The userspace sysfs hooks replace the notifier sources.

### 7.2 DT (0055)

The node is added in a separate file and included after the nura delta and
before the rear-camera include in `sm8150-oneplus-guacamole.dts`, so all four
DTBs (main and three rear-camera variants) get it.  The end-of-file include
slot is deliberately left free for the popup-camera patch; the parent should
sequence/rebase popup independently rather than making both patches add an
include at the same anchor.  `status = "disabled"` is the gate.  The pinctrl
state matches stock: GPIO 118, function gpio, 2 mA, bias-disable,
input-enable.  The stock variant-ID GPIO 90 is not consumed; the chip name
comes from the DT property.  GPIO 101/131 are claimed as GPIOs by the driver
at probe, not by pinctrl.

### 7.3 Config

Gate 0 only adds `QCOMTEE=m`; `TEE=y` and SHM-bridge mode are already present.
Gate 1 adds `INPUT_GOODIX_FP=m`.  The release fragment sets
`LOCALVERSION="-sm8150-guacamole-rc4"`.  `QCOMTEE=m` builds a module with a
`platform:qcomtee` modalias; qcom_scm registers that platform device at boot,
so udev/systemd can autoload and bind it, and `qcomtee_probe()` then issues a
QTEE feature/version query.  The config is therefore **not passive by itself**.
For a passive gate-0 check, omit `qcomtee.ko` from the installed modules tree
or boot with `module_blacklist=qcomtee`, then load it manually for the
deliberate test.  `QCOMTEE` does not declare a
`depends on TEE`, so keeping `TEE=y` is required.  The built `qcomtee.ko`
proves the module links with TEE built-in.

## 8. Blockers and recommendations

1. **Gate 0 is still unverified, and the probe command differs from stock.**
   Mainline uses `QCOM_SCM_SMCINVOKE_INVOKE` (0x02, fnid 0x0602); stock uses
   `SMCINVOKE_INVOKE_CMD 0x32000600` (fnid 0x0600).  The legacy constant
   `QCOM_SCM_SMCINVOKE_INVOKE_LEGACY` exists but is not used by
   `qcom_scm_qtee_invoke_smc()`.  Run the passive gate-0 check on the current
   boot first: if `/sys/bus/platform/devices/qcomtee*` is absent, the probe
   returned `-EIO` and gate 0 fails as shipped.  Do not add a legacy probe
   fallback without a separately reviewed hardware experiment; it is a
   secure-world call and was deliberately not added here.
2. **`QCOMTEE=m` is not passive by itself.**  The module has a
   `platform:qcomtee` modalias, so udev/systemd can autoload it against the
   platform device qcom_scm registers at boot; its probe issues the QTEE
   feature/version query.  For a passive check, omit `qcomtee.ko` from the
   installed modules tree or add `module_blacklist=qcomtee`, then load it
   manually for the deliberate gate-0 test.
3. **Do not enable the DT node or `allow_power=1` before gate 0 passes.**
   The current integration keeps both off.
4. **Gate 3/4 dependencies are absent.**  `CONFIG_ANDROID_BINDERFS` and
   `CONFIG_CHR_DEV_SG` are unset; `qcom_qseecom` cannot load an app or serve
   listeners.  No HAL, ion shim, RPMB listener or persist backup was added.
5. **Proc ABI divergences** (`fp_id` read-only, `tee_bind_core` root-only) are
   deliberate gate-1 restrictions.  If the gate-4 HAL is observed to write
   them, restore `0666` and a write handler in a separate reviewed patch.
6. **No full package/boot build** was done.  The parent must build the
   combined rc4 image and verify the module/DTB payload before any trial.
7. **Hardware claims are not made.**  In particular, this topic does not
   claim the lock crash is fixed, that suspend/resume is the cause, or that
   any fingerprint authentication stage works.

## 9. Reproduce the checks

The short path, with `$TOPIC` set to this directory:

```sh
# 1. patch a reflinked copy of the rc3 source (never the baseline)
find <rc3-src> -maxdepth 1 -mindepth 1 ! -name .output -print0 |
  xargs -0 -I{} cp -a --reflink=always {} <copy>/
patch -d <copy> -p1 --fuzz=0 -i "$TOPIC/patches/0051-input-misc-add-goodix-fp.patch"
patch -d <copy> -p1 --fuzz=0 -i "$TOPIC/patches/0055-arm64-dts-guacamole-fingerprint.patch"

# 2. config
scripts/kconfig/merge_config.sh -O <out> -m <rc3-config> \
  "$TOPIC/config/gate0.fragment" "$TOPIC/config/gate1.fragment" \
  "$TOPIC/config/release-name.fragment"
make -C <copy> O=<out> ARCH=arm64 LLVM=1 olddefconfig kernelrelease

# 3. compile the touched object and all four DTBs
make -C <copy> O=<out> ARCH=arm64 LLVM=1 \
  drivers/input/misc/goodix_fp.o \
  qcom/sm8150-oneplus-guacamole.dtb \
  qcom/sm8150-oneplus-guacamole-rear-{main,tele,wide}.dtb

# 4. passive checks only; do not add --load/--diagnostics yet
sh "$TOPIC/scripts/gate0-check.sh"        # safe on current rc3 boot
sh "$TOPIC/scripts/gate1-check.sh"        # read-only presence check
```

`checks/` contains the logs from exactly these steps.  The only build warning
seen was the pre-existing host `scripts/dtc/dtc-lexer.lex.c` `dts_version`
warning; the fingerprint driver and DT produced no warnings.  BTF for the
external modules was skipped because the config-only output has no vmlinux.
