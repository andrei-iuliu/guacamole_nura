#!/usr/bin/env bash
# Apply patches/0042..0045 to the real rc3-patched 7.0.0-sm8150 sources in a
# scratch copy and prove the result is the reviewed one.
#
#   KERNEL_ROOT=/path/to/linux-v7.0.0-sm8150 checks/patch_apply_check.sh
#
# Nothing is written to the kernel tree: it is only read.  The scratch copy
# holds the touched files plus the ath10k headers (about 2 MB).
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/.." && pwd)
default_root=$root/../../../local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150
KERNEL_ROOT=${KERNEL_ROOT:-$default_root}
[[ -d $KERNEL_ROOT/drivers/soc/qcom ]] || {
	echo "SKIP: no kernel tree at $KERNEL_ROOT (set KERNEL_ROOT=)" >&2
	exit 3
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
base=$work/base
mkdir -p "$base/drivers/soc/qcom" "$base/drivers/net/wireless/ath/ath10k"

# Unpatched-by-this-topic inputs: these are the rc3-patched rc4 baseline files.
cp "$KERNEL_ROOT/drivers/soc/qcom/Kconfig" "$KERNEL_ROOT/drivers/soc/qcom/Makefile" \
	"$base/drivers/soc/qcom/"
cp "$KERNEL_ROOT"/drivers/net/wireless/ath/ath10k/*.h \
	"$KERNEL_ROOT"/drivers/net/wireless/ath/ath10k/{core.c,mac.c,wmi-tlv.c} \
	"$base/drivers/net/wireless/ath/ath10k/"
cp -a "$base" "$work/patched"

fail=0
for patch in "$root"/patches/0044-*.patch "$root"/patches/0045-*.patch; do
	echo "== ${patch##*/}"
	# --verbose prints "(offset N lines)" only when the hunk moved, so the
	# grep below turns an offset into a failure as well as fuzz or rejects.
	if ! out=$(patch -d "$KERNEL_ROOT" -p1 -F0 --dry-run --verbose <"$patch" 2>&1); then
		echo "FAIL: does not apply to $KERNEL_ROOT"
		printf '%s\n' "$out" | tail -20
		fail=1
		continue
	fi
	if printf '%s\n' "$out" | grep -qE 'FAILED|fuzz|\(offset'; then
		echo "FAIL: applied with fuzz or offset"
		printf '%s\n' "$out" | grep -E 'FAILED|fuzz|\(offset'
		fail=1
		continue
	fi
	patch -d "$work/patched" -p1 -F0 --no-backup-if-mismatch <"$patch" >/dev/null
	echo "PASS: applies with -F0, no offset"
done

if (cd "$work/patched" && sha256sum -c --quiet "$here/expected-files.sha256"); then
	echo "PASS: patched files match the reviewed sources"
else
	echo "FAIL: patched files differ from checks/expected-files.sha256"
	fail=1
fi

"$here/fsyntax_check.sh" "$work/patched" || fail=1
"$here/key_state_model.py" "$work/patched" || fail=1

exit "$fail"
