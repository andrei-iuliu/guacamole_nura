#!/usr/bin/env bash
# Rootless syntax check for the files touched by the suspend-fixes topic.
#
#   checks/fsyntax_check.sh <srcdir-with-patched-files>
#
# Builds the clang -fsyntax-only command line by hand instead of running make,
# so the read-only kernel tree is never written.  It needs a configured kernel
# build output next to the tree ($KERNEL_ROOT/.output with include/generated);
# the rc3 state under flows/local-storage/.work/... has one.  If there is no
# configured output the check reports SKIP and exits 3, so an uncompiled file
# is never mistaken for a compiled one.
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/.." && pwd)
srcdir=${1:?usage: fsyntax_check.sh <srcdir>}

default_root=$root/../../../local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150
KERNEL_ROOT=${KERNEL_ROOT:-$default_root}
[[ -d $KERNEL_ROOT/drivers/soc/qcom ]] || {
	echo "SKIP: no kernel tree at $KERNEL_ROOT (set KERNEL_ROOT=)" >&2
	exit 3
}
OUT=${KERNEL_OUT:-$KERNEL_ROOT/.output}
[[ -f $OUT/include/generated/autoconf.h ]] || {
	echo "SKIP: $OUT is not a configured kernel build (set KERNEL_OUT=)" >&2
	exit 3
}

cc=${CC:-clang}
target=${CROSS_TARGET:-aarch64-linux-gnu}
resource=$("$cc" -print-resource-dir)/include

common=(
	-fsyntax-only -nostdinc -isystem "$resource" -std=gnu11 -fms-extensions
	-Wno-incompatible-pointer-types -Wno-microsoft-anon-tag
	-D__KERNEL__ --target="$target"
	-I"$KERNEL_ROOT/arch/arm64/include"
	-I"$OUT/arch/arm64/include/generated"
	-I"$OUT/arch/arm64/include/generated/uapi"
	-I"$KERNEL_ROOT/include"
	-I"$KERNEL_ROOT/arch/arm64/include/uapi"
	-I"$KERNEL_ROOT/include/uapi"
	-I"$OUT/include"
	-I"$OUT/include/generated/uapi"
	-I"$KERNEL_ROOT/drivers/net/wireless/ath/ath10k"
	-I"$KERNEL_ROOT/drivers/net/wireless/ath"
	-include "$KERNEL_ROOT/include/linux/compiler-version.h"
	-include "$KERNEL_ROOT/include/linux/kconfig.h"
	-include "$KERNEL_ROOT/include/linux/compiler_types.h"
)

fail=0
run() {
	local modname=$1 basename=$2 file=$3 log
	log=$(mktemp)
	if "$cc" "${common[@]}" \
		-DKBUILD_MODNAME="\"$modname\"" \
		-DKBUILD_BASENAME="\"$basename\"" \
		-DKBUILD_MODFILE="\"$file\"" \
		"$srcdir/$file" >"$log" 2>&1; then
		echo "PASS $file"
	else
		echo "FAIL $file"
		grep -m 8 'error:' "$log" || tail -20 "$log"
		fail=1
	fi
	rm -f "$log"
}

run guacamole_mss_vote guacamole_mss_vote drivers/soc/qcom/guacamole_mss_vote.c
run ath10k_core mac drivers/net/wireless/ath/ath10k/mac.c
run ath10k_core core drivers/net/wireless/ath/ath10k/core.c
run ath10k_core wmi-tlv drivers/net/wireless/ath/ath10k/wmi-tlv.c

[[ $fail -eq 0 ]]
