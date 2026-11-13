#!/bin/sh
# Read-only recipe check for the rc4 fingerprint additions.
#
# usage: check_recipe.sh <recipe-dir>
#
# <recipe-dir> must contain APKBUILD and
# config-postmarketos-qcom-sm8150.aarch64.  The script does not write or run
# abuild; it checks the literal source/sha512sums lines and config symbols.
set -u

recipe=${1:-}
if [ -z "$recipe" ]; then
	echo "usage: $0 <recipe-dir>" >&2
	exit 2
fi

apkbuild=$recipe/APKBUILD
config=$recipe/config-postmarketos-qcom-sm8150.aarch64
fail=0

check_patch() {
	name=$1
	hash=$2

	if ! grep -Eq "^[[:space:]]*${name}[[:space:]]*$" "$apkbuild"; then
		echo "MISSING source line: $name" >&2
		fail=1
	fi

	actual=$(awk -v n="$name" '$2 == n { print $1; exit }' "$apkbuild")
	if [ -z "$actual" ]; then
		echo "MISSING sha512sums line: $name" >&2
		fail=1
	elif [ "$actual" != "$hash" ]; then
		echo "BAD sha512 for $name:" >&2
		echo "  expected $hash" >&2
		echo "  found    $actual" >&2
		fail=1
	else
		echo "OK $name"
	fi
}

check_sym() {
	line=$1
	if grep -qxF "$line" "$config"; then
		echo "OK $line"
	else
		echo "MISSING config line: $line" >&2
		fail=1
	fi
}

if [ ! -f "$apkbuild" ]; then
	echo "not found: $apkbuild" >&2
	exit 2
fi
if [ ! -f "$config" ]; then
	echo "not found: $config" >&2
	exit 2
fi

check_patch 0051-input-misc-add-goodix-fp.patch \
	5feb1d6208f4f6d4bc239f4286f163266370dbd7024de3c063ee6cc29dbddb1e8235a800ce2aa78c066c74a1da6d1e4881f171873d9581107dd7596b4ce862d9
check_patch 0055-arm64-dts-guacamole-fingerprint.patch \
	74d290d5ccf5eedbbe4e742384087d2807f3dd0be03a6e7c0c6209df28f749127eadca19b2551ea25af3ee9d2edd6de72ea09d3904d62c5ec5b9ab2de634a3d5

check_sym 'CONFIG_TEE=y'
check_sym 'CONFIG_QCOMTEE=m'
check_sym 'CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y'
check_sym '# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set'
check_sym 'CONFIG_INPUT_GOODIX_FP=m'
check_sym 'CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"'

if [ "$fail" -ne 0 ]; then
	echo "FAIL: recipe is not ready for rc4 fingerprint" >&2
	exit 1
fi

echo "PASS: recipe has the fingerprint patches, hashes and config"
