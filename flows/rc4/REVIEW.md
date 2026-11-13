# rc4 review record

What was inspected, what was changed, and what remains unproven. This is the
document the parent should read next to the actual recipe/flow rather than
instead of it. Nothing here is a hardware result.

## 1. Inputs reviewed

| Input | How it was reviewed |
| --- | --- |
| `flows/rc4/topics/suspend-fixes/` | README (341 lines), inventory, patches 0044/0045, config/APKBUILD/device fragments, `checks/RESULTS.md` |
| `flows/rc4/topics/displayport/` | README (419 lines), inventory, 5 kernel patches + 2 dts patches, recipe fragments, `checks/RESULTS.md` |
| `flows/rc4/topics/fingerprint/` | README (403 lines), inventory, 0051/0055, config fragments, recipe additions, `check_recipe.sh`, artifacts |
| `flows/rc4/topics/popup-camera/` | README (321 lines), inventory, 0052-0054, dt fragment, `docs/rc4-device-notes.md` |
| rc3 recipe | APKBUILD, config, all 37 patch files; hashes recomputed |
| rc3-patched sources | `bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150` (read-only; reflink copies for builds) |
| reviewed local-storage flow | scripts, README, ORIGIN manifest, tests |

## 2. Decisions taken (and why)

1. **Recipe is an isolated copy, not an edit of rc3.** `flows/rc4/device/testing/
   linux-postmarketos-qcom-sm8150/` was created from the rc3 package; the
   0001-0041 bytes are unchanged (checked by `check_recipe.py` and
   `test_rc4_integration.py`). `pkgrel` 4 → 5, release
   `-sm8150-guacamole-rc4`.
2. **0042/0043 are not added.** They are the rc4 numbering of what rc3 already
   carries as 0034/0035; re-applying them would fail or double-apply.
3. **DP devicetree patches are 0057/0058, not 0001/0002.** The reviewed builder
   applies `$RECIPE/0*.patch` with `--fuzz=0` for names ≥ 0028, so a name
   without a numeric prefix would be silently skipped. They are unique,
   builder-compatible names and they apply after the popup DT patch (0056)
   without touching guacamole.dts.
4. **Popup DT became a packaging patch (0056).** The topic shipped a dtsi file
   plus a one-line include; a recipe needs a patch. It was generated against the
   post-0055 include state (so it applies at zero offset), adds
   `arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-popup.dtsi` and appends
   the include at end of file.
5. **Device package is a copy with one addition.** `pkgrel` 14 → 15, the MSS
   release helper and unit are added to `source=`, `package()` and
   `sha512sums=`; `80-guacamole.preset` is byte-identical to rc3's, so the unit
   is installed but not enabled.
6. **Local-storage flow is a copy with an explicit constant map** (see
   `flows/rc4/local-storage/README.md` §3). State and output are isolated under
   `flows/rc4`; unchanged recipes are symlinked read-only from
   `flows/local-storage/device/testing` instead of being duplicated (the
   firmware recipe alone is 39 MB).
7. **`module_blacklist=qcomtee` is on the cmdline**, not a modprobe.d file.
   `early_mod_check()` refuses every load of a blacklisted module, so the module
   cannot be autoloaded and a manual `modprobe qcomtee` fails too. The
   deliberate gate-0 test needs a boot image without the parameter.
8. **The pop-up sensor and CAMSS endpoint stay out of the default DTB.** The
   async-notifier trace from the popup topic (`camss.c:4672`,
   `v4l2-async.c:735/714/281`, `i2c-core-of.c:99`) shows an endpoint whose
   remote sensor is disabled removes the `/dev/video*` nodes of *all* cameras.
   The integration asserts three bindable CAMSS connections in the compiled DTB.

## 2b. Follow-up corrections after the parent's caller audit

The first hand-off broke the kernel→rootfs chain: the wrapper exported
`NURA_KERNEL_OUTPUT=.../kernel-output` (a sibling of the state) and
`NURA_KERNEL_STAGE/IMAGE` outside `kernel-output`, while the rootfs flow and its
snapshot guard read `kernel-state/output` and `kernel-output/{kernel-package,
Image,kernel-inputs.json}`.  The builder also never wrote `kernel-inputs.json`
(rc3 did that in `rc3_build.sh`), and the copied checker's release pattern did
not accept `7.0.0-sm8150-guacamole-rc4` at all.  Nothing caught it because the
checks were static string assertions.

Fixes, all in `flows/rc4`:

| Problem | Fix |
| --- | --- |
| `NURA_KERNEL_OUTPUT` pointed at a sibling directory | `rc4.sh` now maps state/`output`/`kernel-package`/`Image` exactly as rc3 did: build output is `kernel-state/output`, stage and Image are inside `kernel-output`. `verify_kernel_contract()` fails the phase if any of the four paths is wrong. |
| `kernel-inputs.json` never created | `rc4.sh kernel` runs the copied `check_local_candidate.py kernel-record` after the builder, then verifies the contract. |
| entrypoint snapshot wanted `kernel-output/DELIVERY.json` before it can exist, plus an rc3 `build/plasma-mobile` file and hook paths under the checkout | the snapshot now pins only the inputs this run reads: `kernel-output/kernel-inputs.json`, `kernel-state/output/include/config/kernel.release`, and the flow's own hook recipe (`$FLOW_HOOK_RECIPE`). |
| `rootfs_prepare_local.sh` defaulted `RECIPE_ROOT` to the read-only rc3-era tree, so the device package could resolve to pkgrel 14 | `RECIPE_ROOT` is now the flow's own `device/testing` link farm; the checker asserts the resolved device recipe is pkgrel 15 and the kernel recipe pkgrel 5. |
| checker `RELEASE_PATTERN` rejected the rc4 release (`rc[0-9]+|rear[0-9]+`) | accepts `guacamole-rc[0-9]+` as well and keeps the rc/rear rollback names. |
| `check` claimed read-only but teed a DT gate log and deleted `__pycache__` | `check` writes nothing (no logs, no cache deletion) and exports `PYTHONDONTWRITEBYTECODE=1`; the fake-builder fixture moved to `rc4.sh contract`, which creates temporary trees under `build-source` and removes them. |
| the copy step had no testable precondition | `check_local_candidate.py kernel-copy-check` validates the state before `copy_kernel_state` copies it (single source tree, `output/`, `.config`, and a `output/source` link that resolves to that tree); both the flow and the fixture call it. |
| `kernel-copy-check` refused the *normal* kbuild `output/source` link (absolute, pointing at the exact source tree inside the state) | the link's **target** is now what is judged: it must resolve (strictly) to the one validated source tree. Dangling, a different tree inside the state, or a target outside the state are still refused. The fake builder now reproduces kbuild's absolute link, so the fixture would catch a regression. |

Regression test: `flows/rc4/tests/test_rc4_kernel_contract.py` (+
`fixtures/fake_build_rear_camera_kernel.sh`) runs the **real** wrapper with a fake
builder that honours the documented `NURA_*` variables, then the real
`kernel-record`, the rootfs dry-run, `rootfs_prepare_local.sh --check-only` and
five negative cases.  This is the test the first hand-off lacked; it fails on
every path mismatch listed above.

## 3. Corrections made to the supplied material

| Input claim | Correction | Where |
| --- | --- | --- |
| suspend 0025/0026 needed for rc4 | already in rc3 as 0034/0035 | README §2, inventory |
| supplied suspend APKBUILD/device diff | target 6.17/pkgrel 11-12; replaced | `docs/NURA_RC4.md` §2 |
| DP DT diffs apply | target the flattened 6.17 tree; rewritten as a 7.0 dtsi | `docs/NURA_RC4.md` §2 |
| DP audio link is harmless when unused | it defers the whole card if enabled and the DP codec is absent; ships disabled | DP topic C28/C29, `docs/NURA_RC4.md` §3 |
| fingerprint gate 0 is passive by config | `qcomtee` has a platform modalias; blacklist needed | `docs/NURA_RC4.md` §3 |
| popup: IMX471 on CCI1 master 0 | stock says `cci-master = <1>`; master 1 used | popup topic C7 |
| popup: sensor/endpoint can be gated separately | no: the endpoint alone stalls the CAMSS notifier | popup topic C22 |
| popup: ~90 fps | the single mode is 81.3 fps by its own timing | popup topic C18 |
| "lock crash is suspend" | unverified; the rc4 rootfs does not suspend automatically | `docs/NURA_RC4.md` §4 |

## 4. Verification performed (rootless)

| Check | Result | Evidence |
| --- | --- | --- |
| Recipe `source=`/`sha512sums` consistency | 54 entries, 54 sums, all match | `scripts/check_recipe.py` (59 checks) |
| 0001-0041 bytes vs rc3 | identical | same |
| New patch set + order | exactly 0044-0058 in numeric order | same |
| Recipe identity | `pkgver=7.0.0`, `_tag=v$pkgver-sm8150`, `pkgrel=5`, LOCALVERSION → `7.0.0-sm8150-guacamole-rc4` | same + `integration/kernelrelease.log` |
| Config delta vs rc3 | 12 lines, all intended | `integration/config.fragment` |
| Zero-fuzz apply of 0044-0058 on the rc3-patched tree | 15/15 clean, no offsets/rejects | `integration/apply-sequence.log` |
| Object compile (11 TUs, `W=1`) | clean, no diagnostics from patched sources | `integration/object-build.log`, `build-source/artifacts/obj/` |
| DTB compile (full + 3 isolates) | clean | `integration/dtb-build.log`, `build-source/artifacts/dtb/` |
| DT gates on the compiled full DTB | CAMSS 3/3 bindable, ALSA 11/12 available, fp disabled, DP video okay + audio link disabled, popup absent | `integration/dtb-gates.log` |
| Device gates | MSS unit installed and not in preset, no starter hook, preset byte-identical to rc3 | `scripts/check_recipe.py`, `tests/test_rc4_integration.py` |
| Flow isolation and guards | rc4 paths only, state under `flows/rc4`, local-root/active-mount/flock/`--authorized-key` present | `tests/test_rc4_flow_guards.py` |
| rc3 baseline untouched | 56 pinned files match `tests/rc3-baseline.sha256` | `tests/test_rc4_integration.py` |
| Wrapper safety | no flash/boot/mount/repartition/cleanup actions | `tests/test_rc4_flow_guards.py` |

Unit tests: 27 (integration) + 11 (flow guards) + 4 (copied public-key/storage
tests) = 42, all passing with `bash flows/rc4/scripts/rc4.sh check`, which writes
nothing.  `bash flows/rc4/scripts/rc4.sh contract` adds 16 executable
kernel→rootfs contract cases (fake builder, real `kernel-record`, real rootfs
preflight, five refusal cases) and removes its temporary trees.

## 5. Not verified — do not read the source checks as behaviour

* No `vmlinux`/`Image`/module link, no kernel package, no APK, no rootfs, no
  boot image, no boot. The object/DTB evidence is syntax-level only.
* No phone, SSH, adb, fastboot, sudo, mount, pmbootstrap or flash was used.
* No suspend/resume, modem, camera, DP, fingerprint or popup hardware behaviour
  was measured. The lock-screen crash is unexplained.
* QCOMTEE's fnid compatibility on SM8150 is unresolved.
* The local-storage flow's rootfs/boot steps have never run in this layout.  The
  preflight, the copy preconditions, the checker handoff and the identity
  manifests are covered by the fake-builder fixture; the actual `rsync` copy,
  pmbootstrap build, install, export and boot pack are not.  That is the point
  where the remaining path assumptions would surface.

## 6. Open items for the parent before a trial

1. Review the actual recipe (`APKBUILD`, config, 0044-0058) and the device
   package diff against this record.
2. Decide whether the DP video DT is acceptable as a *candidate* default (it is
   enabled) with the audio link disabled.
3. Confirm the `module_blacklist=qcomtee` cmdline is carried into whichever boot
   image is used for the trial — it lives in the copied
   `build_local_bootimg.sh`, not in a shared script.
4. Run `rc4.sh kernel` and inspect the produced kernel package/DELIVERY before
   building a rootfs; the full build is where patch-order or config surprises
   would still appear.
5. Nothing here should be announced as "fixed" or "boot-ready" until step 4 and
   a hardware trial with the diagnostics in `docs/NURA_RC4.md` §5 are done.

## Parent integration review, 2026-11-13

Inspected the actual wrapper, source/output/stage contract, copied local-storage
callers, checker release validation and rc4 device recipe selection after the
handoff corrections. Independently ran `bash flows/rc4/scripts/rc4.sh check`
(59 recipe checks and 42 tests passed) and `bash flows/rc4/scripts/rc4.sh contract`
(12 executable fake-builder/handoff cases passed). The DT checks retain three
bindable rear-camera connections, eleven available audio links, and the
documented disabled experimental features. Baseline hash checks passed.

Ready for the user-run full kernel build, not ready for boot or flash. The
fixture does not execute the real kernel compilation, rsync source copy,
pmbootstrap packaging, rootfs installation/export or final boot packing.
A non-fatal ResourceWarning in the DT reader was observed during tests.

### Follow-up after the real kernel build (same day)

The user's real `rc4.sh kernel` succeeded (source checker accepted the actual
recipe hashes and **839** modules). Running `kernel-copy-check` on the real
`flows/rc4/build-source/kernel-state` then failed on `output/source`, the
absolute symlink kbuild itself creates to the exact extracted source tree inside
that state. That was a checker false-positive, not a build defect:
`copy_kernel_state()` already rewrites the link to a relative one after copying.

* `kernel-copy-check` now judges the link's **target**: it must resolve
  (`strict=True`) to the one validated `linux-*` tree, absolute or relative.
  Dangling targets, a different tree inside the state and targets outside the
  state are still refused with distinct messages.
* `tests/fixtures/fake_build_rear_camera_kernel.sh` now creates kbuild's real
  absolute `output/source` (plus `output/build`) so the fixture reproduces the
  shape instead of the sanitised one.
* Contract cases: 16/16 (`test_rc4_kernel_contract.py`), including the positive
  absolute-link case and the three negative link cases.
* Read-only validation of the actual build:
  `kernel-copy-check --state flows/rc4/build-source/kernel-state` → exit 0,
  `copyable: true`; `source --source-build ... --source-state ...` → exit 0,
  839 modules, release `7.0.0-sm8150-guacamole-rc4`.
* The DT reader's `ResourceWarning` is gone (`dt_facts.py` reads bytes through
  `pathlib`).
