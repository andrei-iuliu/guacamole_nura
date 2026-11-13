#!/bin/sh
# Rootless syntax check of the files the displayport series touches.
#
# This is NOT a kernel build and not a substitute for one.  It runs `clang
# -fsyntax-only` on the patched translation units against the real, generated
# kernel headers of an out-of-tree prepare in a scratch directory, so it type
# checks the patched code and everything it includes.  No root, no writes into
# the kernel source tree, no link step.
#
# Preconditions (see README, "How to re-run the checks"):
#   1. KERNEL points at the rc3 7.0.0-sm8150 source tree (read-only).
#   2. PATCHED points at a tree with patches/0046..0050 applied.
#   3. OBJ points at an out-of-tree `make O=... ARCH=arm64 LLVM=1 prepare`
#      directory for that source tree.
#
# usage: KERNEL=... PATCHED=... OBJ=... checks/compile_check.sh
set -u

: "${KERNEL:?set KERNEL to the rc3 kernel source tree}"
: "${PATCHED:?set PATCHED to the patched tree}"
: "${OBJ:?set OBJ to the out-of-tree prepare directory}"
CLANG=${CLANG:-clang}

RES=$("$CLANG" -print-resource-dir)
CFLAGS="-target aarch64-linux-gnu -fsyntax-only -nostdinc
	-isystem $RES/include
	-fms-extensions -fno-strict-aliasing -fno-common -fshort-wchar -fno-PIE
	-D__KERNEL__ -DKBUILD_MODNAME=\"check\" -DKBUILD_BASENAME=\"check\"
	-DKBUILD_MODFILE=\"check\"
	-include $KERNEL/include/linux/kconfig.h
	-I $KERNEL/arch/arm64/include
	-I $OBJ/arch/arm64/include/generated
	-I $KERNEL/arch/arm64/include/uapi
	-I $OBJ/arch/arm64/include/generated/uapi
	-I $KERNEL/include
	-I $OBJ/include
	-I $KERNEL/include/uapi
	-I $OBJ/include/generated/uapi
	-I $KERNEL/sound/soc/qcom
	-I $KERNEL/sound/soc/qcom/qdsp6
	-Wno-unused-value"

rc=0
for f in sound/soc/qcom/sm8150.c \
	 sound/soc/qcom/qdsp6/q6afe.c \
	 sound/soc/qcom/qdsp6/q6afe-dai.c; do
	echo "=== $f"
	# shellcheck disable=SC2086
	if "$CLANG" $CFLAGS "$PATCHED/$f" 2>&1 | grep -E "error:"; then
		echo "    FAILED"
		rc=1
	else
		echo "    ok (no diagnostics of severity error)"
	fi
done
exit $rc
