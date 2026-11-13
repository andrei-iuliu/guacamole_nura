# rc4 integration evidence

Every command below was run on the host with no sudo, no phone, no
pmbootstrap, no mount and no privileged operation. Paths are relative to the
checkout root unless absolute. Results are the actual output, not a summary of
what should happen.

Environment: clang 23.1.4 + ld.lld, kernel source
`bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150`
(= `v7.0.0-sm8150` + rc3 patches 0001-0041, not a git tree, no `.config`),
`bc(1)` is not installed so `flows/rc4/scripts/toolshim/bc` generates
`timeconst.h` (HZ=1000 output verified byte-identical to the bc-generated header
in `.work/rear-camera/output/include/generated/timeconst.h`).

## 1. Zero-fuzz apply of the new series

```sh
S=flows/rc4/build-source/scratch-source      # reflink copy of the rc3-patched tree
for p in flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/00*.patch; do
    case "$(basename "$p")" in 00[0-3]*|004[0-1]*) continue;; esac
    (cd "$S" && patch -p1 --fuzz=0 --no-backup-if-mismatch -i "$PWD/$p")
done
```

Result: `flows/rc4/integration/apply-sequence.log` — 0044…0058 all `exit=0`,
`clean`, i.e. no fuzz, no offsets, no rejects. `find "$S" -name '*.rej'` is
empty. (The rc3 patches 0001-0041 are already applied in that tree by
definition; their bytes are compared against the rc3 recipe instead.)

## 2. Config merge and identity

```sh
cp flows/rc4/device/testing/linux-postmarketos-qcom-sm8150/config-postmarketos-qcom-sm8150.aarch64 OUT/.config
bash scripts/kconfig/merge_config.sh -O OUT -m OUT/.config flows/rc4/integration/config.fragment
make -C "$S" O=OUT ARCH=arm64 LLVM=1 olddefconfig
make -C "$S" O=OUT ARCH=arm64 LLVM=1 -s kernelrelease
diff -u <rc3 config> OUT/.config
```

Result: 12 changed lines in total (LOCALVERSION, PM_DEBUG, PM_ADVANCED_DEBUG,
PM_TEST_SUSPEND, PM_SLEEP_DEBUG, QCOMTEE, QCOM_GUACAMOLE_MSS_VOTE,
INPUT_GOODIX_FP, MXM1120, VIDEO_IMX471, VIDEO_QCOM_HOTDOG_POPUP, and the
`# CONFIG_QCOMTEE` removal). `kernelrelease` =
`7.0.0-sm8150-guacamole-rc4` (`integration/kernelrelease.log`). The result is the
config shipped next to the APKBUILD.

## 3. Object compile (syntax/object evidence only)

```sh
make -C "$S" O=OUT ARCH=arm64 LLVM=1 W=1 \
  drivers/iio/magnetometer/mxm1120.o drivers/media/i2c/imx471.o \
  drivers/media/platform/qcom/hotdog-popup-motor.o drivers/input/misc/goodix_fp.o \
  drivers/soc/qcom/guacamole_mss_vote.o \
  drivers/net/wireless/ath/ath10k/{mac,core,wmi-tlv}.o \
  sound/soc/qcom/qdsp6/q6afe-dai.o sound/soc/qcom/qdsp6/q6afe.o sound/soc/qcom/sm8150.o
```

Result: 11 objects built; `grep -E '(warning|error):' integration/object-build.log`
filtered of `/scripts/` and `/tools/` is empty. Artifacts copied to
`flows/rc4/build-source/artifacts/obj/` (hashes in `artifacts/SHA256SUMS`).
**This is not a link, not a module and not a kernel package.**

## 4. Devicetree compile and gates

```sh
make -C "$S" O=OUT ARCH=arm64 LLVM=1 \
  qcom/sm8150-oneplus-guacamole.dtb \
  qcom/sm8150-oneplus-guacamole-rear-{main,tele,wide}.dtb
python3 flows/rc4/scripts/dt_facts.py OUT/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb --require-rc4-gates
```

Result (`integration/dtb-build.log`, `integration/dtb-gates.log`): four DTBs
built, no dtc diagnostic. Gates on the full DTB:

```
12 sound children, 11 available links
CAMSS port@0  cci@ac4a000/i2c-bus@0/camera@10  status=okay  bindable=True
CAMSS port@1  cci@ac4a000/i2c-bus@1/camera@1a  status=okay  bindable=True
CAMSS port@3  cci@ac4b000/i2c-bus@1/camera@1a  status=okay  bindable=True
fingerprint present, status=disabled
popup: motor present status=disabled, front_sensor absent, camss_port2 absent
displayport: controller present status=okay, fsa4480 present, dp-dai-link present status=disabled
disabled sound links: dp-dai-link
rc4 gates: OK
```

## 5. Kernel -> rootfs contract (fake builder, rootless)

```sh
bash flows/rc4/scripts/rc4.sh contract
```

Result: 16/16 cases pass.  `flows/rc4/tests/fixtures/fake_build_rear_camera_kernel.sh`
consumes the same `NURA_KERNEL_{STATE,OUTPUT,STAGE,IMAGE,RELEASE}` variables as
the real builder and writes the documented layout only; the test then runs the
real `rc4.sh kernel` path (including `kernel-record`), the entrypoint dry-run,
`rootfs_prepare_local.sh --check-only` (real preflight + `source` checker
handoff) and five refusals:

| Case | Expected |
| --- | --- |
| correct fake build | `kernel-inputs.json` written, contract ok, no `DELIVERY.json` |
| rootfs dry-run with it | exit 0, plan printed, no flow state created |
| `--check-only` with it | "kernel recipe/staging and rc2 APK baseline pins: OK" |
| missing `kernel-state/output` | `missing read-only input ... kernel.release` |
| missing `kernel-package` | `missing canonical rc4 kernel package` |
| wrong staged release | `expects the rc4 kernel release` |
| missing `kernel-inputs.json` | refused with the `rc4.sh kernel` hint |
| absolute `output/source` link to the validated tree (normal kbuild) | accepted (`copyable: true`) |
| `output/source` link outside the state | `leaves the kernel state` |
| dangling `output/source` link | `dangling` |
| `output/source` link to another tree inside the state | `another tree inside the state` (and two source trees: `exactly one linux-*`) |

The fake trees are created under `flows/rc4/build-source/fake-*` (the checker
requires build/state inside the checkout) and removed by `tearDownClass`.

## 6. Read-only source checks

```sh
bash flows/rc4/scripts/rc4.sh check
```

Result: `59 checks, 0 failed`; `Ran 27 tests ... OK`; `Ran 11 tests ... OK`;
`Ran 4 tests ... OK`; `== rc4 check: PASS ==`. This includes the 56-file rc3
baseline hash pin, the recipe checksum verification, the gate assertions, the
flow guard tests and the device-package selection (pkgrel 15 via the link farm).

## 7. `check` writes nothing

```sh
touch /tmp/rc4-marker && sleep 1
bash flows/rc4/scripts/rc4.sh check
find . -maxdepth 1 -newer /tmp/rc4-marker -type f      # nothing
find flows/rc4 docs -newer /tmp/rc4-marker -type f     # nothing
find flows/rc4 -name __pycache__                       # nothing
```

`PYTHONDONTWRITEBYTECODE=1` is exported by the wrapper, the DT gate output goes
to stdout only (the earlier `| tee integration/dtb-gates.log` is gone), and the
`find ... -exec rm -rf __pycache__` cleanup was removed.  The only action that
writes is `contract` (temporary trees, removed) and the user-run build steps.

## 8. What is deliberately absent

* no `vmlinux`, `Image`, kernel package, APK, rootfs or boot image;
* no full `make`, no `abuild`, no `pmbootstrap`;
* no phone, SSH, adb, fastboot, sudo, mount, umount, flash or boot;
* no hardware result of any kind.

The full build is the parent's next step (`flows/rc4/scripts/rc4.sh kernel`), and
it is where link/modpost/package problems would still appear.
