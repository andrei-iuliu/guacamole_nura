#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Rootless, reproducible checks for the rc4 popup-camera topic.
#
#   1. apply 0052-0054 to the actual rc3-patched 7.0 sources, with git apply and
#      with patch --fuzz=0, and prove both give the same tree;
#   2. compile the three new objects (W=1) against the rc3 kernel config;
#   3. build the four guacamole DTBs (full + three rear isolates);
#   4. prove the full DTB gained only the popup nodes and that the three isolate
#      DTBs are byte-identical to the unpatched tree;
#   5. run checks/static_checks.py.
#
# No sudo, no phone, no package manager, no privileged path.  Everything is
# written under this directory (scratch/, out/, logs/).  The read-only sources
# are copied with reflinks, never modified.
#
# Usage: checks/run_all.sh            (takes about two minutes)
#        KERNEL_SOURCE=/path/to/linux-v7.0.0-sm8150 checks/run_all.sh
#        SKIP_BUILD=1 checks/run_all.sh     (only step 1 and 5)

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPIC="$(dirname "$HERE")"
WORK="$HERE/scratch"
LOGS="$HERE/logs"
KERNEL_SOURCE="${KERNEL_SOURCE:-bringup-notes/nura-rc3-20261112/kernel-device/work/linux-v7.0.0-sm8150}"
CONFIG="${CONFIG:-runtime-workspace/device/testing/rc3/linux-postmarketos-qcom-sm8150/config-postmarketos-qcom-sm8150.aarch64}"
SKIP_BUILD="${SKIP_BUILD:-0}"

PATCHES=(0052-iio-magnetometer-add-mxm1120.patch
         0053-media-i2c-add-imx471.patch
         0054-media-qcom-add-oneplus-hotdog-popup-motor.patch)
TOUCHED=(Documentation/devicetree/bindings/vendor-prefixes.yaml
         Documentation/devicetree/bindings/iio/magnetometer
         drivers/iio/magnetometer/Kconfig
         drivers/iio/magnetometer/Makefile
         drivers/media/i2c/Kconfig
         drivers/media/i2c/Makefile
         drivers/media/platform/qcom/Kconfig
         drivers/media/platform/qcom/Makefile)

mkdir -p "$LOGS"
say() { printf '\n=== %s ===\n' "$*"; }

[[ -d "$KERNEL_SOURCE" ]] || { echo "missing kernel source: $KERNEL_SOURCE" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "missing rc3 config: $CONFIG" >&2; exit 1; }
for p in "${PATCHES[@]}"; do
    [[ -f "$TOPIC/patches/$p" ]] || { echo "missing patch: $p" >&2; exit 1; }
done

say "step 1: apply the series to the rc3-patched sources, no fuzz"
rm -rf "$WORK/apply-git" "$WORK/apply-patch"
for dir in apply-git apply-patch; do
    mkdir -p "$WORK/$dir"
    for path in "${TOUCHED[@]}"; do
        mkdir -p "$WORK/$dir/$(dirname "$path")"
        cp -a --reflink=auto "$KERNEL_SOURCE/$path" "$WORK/$dir/$path"
    done
done
{
    echo "# git apply --check / --stat, then apply"
    ( cd "$WORK/apply-git" && git init -q . )
    for p in "${PATCHES[@]}"; do
        ( cd "$WORK/apply-git" && git apply --check -v "$TOPIC/patches/$p" )
        ( cd "$WORK/apply-git" && git apply "$TOPIC/patches/$p" )
        echo "git apply OK: $p"
    done
} 2>&1 | tee "$LOGS/apply-git.log"

{
    echo "# patch -p1 --fuzz=0"
    for p in "${PATCHES[@]}"; do
        ( cd "$WORK/apply-patch" && patch -p1 --fuzz=0 --no-backup-if-mismatch \
            -i "$TOPIC/patches/$p" ) | tee -a "$LOGS/apply-patch.log"
        echo "patch OK: $p" | tee -a "$LOGS/apply-patch.log"
    done
} 2>&1 | tee -a "$LOGS/apply-git.log"

if find "$WORK/apply-patch" -name '*.rej' -o -name '*.orig' | grep -q .; then
    echo "FAIL: rejects left behind" >&2
    exit 1
fi
( cd "$WORK/apply-git" && find . -path ./.git -prune -o -type f -print | sort |
  xargs sha256sum ) > "$WORK/hash-git.txt"
( cd "$WORK/apply-patch" && find . -type f -print | sort |
  xargs sha256sum ) > "$WORK/hash-patch.txt"
diff -u "$WORK/hash-git.txt" "$WORK/hash-patch.txt" > "$LOGS/apply-trees-equal.log"
echo "apply: git apply and patch --fuzz=0 produce identical trees" | tee -a "$LOGS/apply-git.log"

if [[ "$SKIP_BUILD" != "1" ]]; then
    say "step 2-4: compile objects and DTBs (reflinked kernel copies)"
    rm -rf "$WORK/src" "$WORK/base-src" "$HERE/out" "$HERE/out-base"
    mkdir -p "$WORK"
    cp -a --reflink=always "$KERNEL_SOURCE" "$WORK/src"
    cp -a --reflink=always "$KERNEL_SOURCE" "$WORK/base-src"

    for p in "${PATCHES[@]}"; do
        ( cd "$WORK/src" && patch -p1 --fuzz=0 --no-backup-if-mismatch \
            -i "$TOPIC/patches/$p" >/dev/null )
    done
    cp "$TOPIC/dt/sm8150-oneplus-guacamole-popup.dtsi" \
       "$WORK/src/arch/arm64/boot/dts/qcom/"
    printf '\n#include "sm8150-oneplus-guacamole-popup.dtsi"\n' \
        >> "$WORK/src/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts"

    mkdir -p "$HERE/out"
    cp "$CONFIG" "$HERE/out/.config"
    printf 'CONFIG_MXM1120=m\nCONFIG_VIDEO_IMX471=m\nCONFIG_VIDEO_QCOM_HOTDOG_POPUP=m\n' \
        >> "$HERE/out/.config"
    mkdir -p "$HERE/out-base"
    cp "$HERE/out/.config" "$HERE/out-base/.config"

    if ! command -v bc >/dev/null; then
        echo "note: no bc(1) on this host; checks/toolshim/bc reimplements" \
            "kernel/time/timeconst.bc and its HZ=1000 output is byte-identical to" \
            "the bc-generated header in .work/rear-camera/output (see README §7)" \
            | tee "$LOGS/bc-shim-note.log"
    fi
    export PATH="$HERE/toolshim:$PATH"

    say "olddefconfig"
    make -C "$WORK/src" O="$HERE/out" ARCH=arm64 LLVM=1 olddefconfig \
        >"$LOGS/olddefconfig.log" 2>&1
    grep -E 'CONFIG_(MXM1120|VIDEO_IMX471|VIDEO_QCOM_HOTDOG_POPUP)=' \
        "$HERE/out/.config" | tee "$LOGS/new-symbols.log"

    say "compile the three new objects with W=1"
    make -C "$WORK/src" O="$HERE/out" ARCH=arm64 LLVM=1 W=1 \
        drivers/iio/magnetometer/mxm1120.o \
        drivers/media/i2c/imx471.o \
        drivers/media/platform/qcom/hotdog-popup-motor.o \
        2>&1 | tee "$LOGS/compile-w1.log" | grep -E 'CC \[M\]|warning|error' || true
    for obj in drivers/iio/magnetometer/mxm1120.o \
               drivers/media/i2c/imx471.o \
               drivers/media/platform/qcom/hotdog-popup-motor.o; do
        [[ -f "$HERE/out/$obj" ]] || { echo "FAIL: $obj not built" >&2; exit 1; }
    done
    # Host tools (scripts/dtc, tools/) can warn on their own; only failures
    # from the kernel sources this patch series adds are interesting.
    if grep -nE '(warning|error):' "$LOGS/compile-w1.log" |
        grep -vE '(/scripts/|/tools/|^[0-9]+:scripts/)' > "$LOGS/compile-diagnostics.log"; then
        cat "$LOGS/compile-diagnostics.log" >&2
        echo "FAIL: compiler diagnostics in the patched sources" >&2
        exit 1
    fi
    rm -f "$LOGS/compile-diagnostics.log"   # only kept when it has content
    echo "compile: 3 objects built, no warnings" | tee -a "$LOGS/compile-w1.log"

    say "build the four guacamole DTBs (patched and baseline)"
    DTBS=(qcom/sm8150-oneplus-guacamole.dtb
          qcom/sm8150-oneplus-guacamole-rear-main.dtb
          qcom/sm8150-oneplus-guacamole-rear-tele.dtb
          qcom/sm8150-oneplus-guacamole-rear-wide.dtb)
    make -C "$WORK/src" O="$HERE/out" ARCH=arm64 LLVM=1 "${DTBS[@]}" \
        2>&1 | tee "$LOGS/dtb-build.log" | grep -E 'DTC|warning|error' || true
    make -C "$WORK/base-src" O="$HERE/out-base" ARCH=arm64 LLVM=1 "${DTBS[@]}" \
        2>&1 | tee -a "$LOGS/dtb-build.log" | grep -E 'DTC|warning|error' || true
    if grep -nE '(warning|error):' "$LOGS/dtb-build.log" |
        grep -vE '(/scripts/|/tools/|^[0-9]+:scripts/)' > "$LOGS/dtb-diagnostics.log"; then
        cat "$LOGS/dtb-diagnostics.log" >&2
        echo "FAIL: dtc diagnostics in $LOGS/dtb-build.log" >&2
        exit 1
    fi
    rm -f "$LOGS/dtb-diagnostics.log"       # only kept when it has content

    say "step 4: DTB regression"
    ALLOW=(
        --allow '/soc@0/geniqup@8c0000/i2c@884000:status'
        --allow '/soc@0/geniqup@8c0000/i2c@884000:dmas'
        --allow '/soc@0/geniqup@8c0000/i2c@884000:dma-names'
        --allow '/soc@0/geniqup@8c0000/i2c@884000:clock-frequency'
        --allow '/soc@0/spmi@c440000/pmic@5/pwm:status'
        --allow '/soc@0/spmi@c440000/pmic@5/pwm:phandle'
        --allow '/soc@0/rsc@18200000/pm8150l-rpmh-regulators/ldo7:phandle'
        --allow '/soc@0/rsc@18200000/pm8150l-rpmh-regulators/ldo8:phandle'
        --assert-no-new-prefix /soc@0/camss@acb3000
        --assert-no-new-prefix /soc@0/cci@ac4b000
        --assert-no-new-prefix /soc@0/cci@ac4a000
    )
    NEW=(
        --allow-new-prefix /camera-popup
        --allow-new-prefix /soc@0/geniqup@8c0000/i2c@884000/magnetometer@c
        --allow-new-prefix /soc@0/geniqup@8c0000/i2c@884000/magnetometer@d
        --allow-new-prefix /soc@0/pinctrl@3100000/cam-mclk2-default-state
        --allow-new-prefix /soc@0/pinctrl@3100000/cam2-reset-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@4/gpio@c000/cam2-vana-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@4/gpio@c000/motor-mode1-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@4/gpio@c000/motor-step-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@4/gpio@c000/motor-step-pwm-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@4/gpio@c000/motor-direction-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@2/gpio@c000/motor-mode0-high-z-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@2/gpio@c000/motor-boost-default-state
        --allow-new-prefix /soc@0/spmi@c440000/pmic@0/gpio@c000/motor-sleep-default-state
        --allow-new-prefix /soc@0/rsc@18200000/pm8009-rpmh-regulators/ldo3
    )
    {
        echo "# full DTB: only popup nodes may appear, rear cameras must not move"
        python3 "$HERE/dtb_regression.py" \
            --base "$HERE/out-base/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb" \
            --new  "$HERE/out/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb" \
            "${ALLOW[@]}" "${NEW[@]}"
        echo
        echo "# the CAMSS async notifier must await exactly the same sensors as rc3:"
        python3 "$HERE/camss_async_audit.py" \
            --base "$HERE/out-base/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb" \
            --new  "$HERE/out/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb"
        echo
        echo "# and the shipped full DTB must have no unbindable connection:"
        python3 "$HERE/camss_async_audit.py" \
            "$HERE/out/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dtb"
        echo
        echo "# rear isolates: popup compiled out, so the DTBs must be identical"
        for v in rear-main rear-tele rear-wide; do
            cmp "$HERE/out/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-$v.dtb" \
                "$HERE/out-base/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole-$v.dtb"
            echo "identical: guacamole-$v.dtb"
        done
    } 2>&1 | tee "$LOGS/dtb-regression.log"
fi

say "step 5: static checks"
python3 "$HERE/static_checks.py" 2>&1 | tee "$LOGS/static-checks.log" | tail -3

say "all checks passed"
