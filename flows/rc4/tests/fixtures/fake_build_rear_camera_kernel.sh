#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Fake `build_rear_camera_kernel.sh` for the rc4 contract tests.
#
# It is a drop-in replacement for the real rootless builder: it consumes exactly
# the NURA_KERNEL_{STATE,OUTPUT,STAGE,IMAGE,RELEASE} contract and writes the
# file layout that flows/rc4/local-storage/scripts/{local_storage_build.sh,
# check_local_candidate.py,rootfs_prepare_local.sh} read:
#
#   $NURA_KERNEL_STATE/linux-v7.0.0-sm8150/{Makefile,arch/.../guacamole.dts}
#   $NURA_KERNEL_OUTPUT/include/config/kernel.release
#   $NURA_KERNEL_OUTPUT/source -> absolute link to $STATE/linux-v7.0.0-sm8150
#   $NURA_KERNEL_STAGE/boot/vmlinuz
#   $NURA_KERNEL_STAGE/boot/dtbs/qcom/sm8150-oneplus-guacamole*.dtb
#   $NURA_KERNEL_STAGE/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release
#   $NURA_KERNEL_STAGE/usr/lib/modules/$RELEASE/.../*.ko.zst
#   $NURA_KERNEL_IMAGE
#
# It deliberately does NOT write kernel-inputs.json or DELIVERY.json: the real
# builder does not either.  `rc4.sh kernel` must run `kernel-record` afterwards,
# and the rootfs phase must write DELIVERY.json, which is exactly what the
# contract test checks.
#
# Nothing here is compiled.  DTBs are copied from the reviewed build evidence
# ($RC4_FAKE_DTB_DIR) so the staged tree looks like the real one.
set -Eeuo pipefail

need() {
    [[ -n "${!1:-}" ]] || { echo "fake builder: $1 is not set" >&2; exit 2; }
    printf '  fake builder: %s=%s\n' "$1" "${!1}"
}

for var in NURA_KERNEL_STATE NURA_KERNEL_OUTPUT NURA_KERNEL_STAGE \
           NURA_KERNEL_IMAGE NURA_KERNEL_RELEASE NURA_RECIPE_DIR; do
    need "$var"
done

STATE="$NURA_KERNEL_STATE"
OUTPUT="$NURA_KERNEL_OUTPUT"
STAGE="$NURA_KERNEL_STAGE"
IMAGE="$NURA_KERNEL_IMAGE"
RELEASE="$NURA_KERNEL_RELEASE"
SRC="$STATE/linux-v7.0.0-sm8150"

# The real builder refuses an output outside the checkout and an unwritable
# target; mirror the "state and output are siblings under one root" shape so a
# caller that points OUTPUT somewhere else fails here, not three steps later.
[[ "$(realpath -m "$OUTPUT")" == "$(realpath -m "$STATE")"/* ]] || {
    echo "fake builder: NURA_KERNEL_OUTPUT must live inside NURA_KERNEL_STATE" >&2
    exit 3
}

mkdir -p "$SRC/arch/arm64/boot/dts/qcom" "$OUTPUT/include/config" \
         "$OUTPUT/arch/arm64/boot" "$STAGE/boot/dtbs/qcom" \
         "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150" \
         "$(dirname "$IMAGE")"

# --- source tree -----------------------------------------------------------
printf '# fake kbuild makefile (contract test)\n' > "$SRC/Makefile"
printf '// SPDX-License-Identifier: GPL-2.0-only\n/dts-v1/;\n' \
    > "$SRC/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts"
printf 'CONFIG_LOCALVERSION="-sm8150-guacamole-rc4"\n' > "$OUTPUT/.config"

# --- kernel image ----------------------------------------------------------
# Minimal arm64 Image header (magic at 0x38, text_offset/size), same shape the
# rc3 harness used, so the boot-envelope helpers accept it.
image="$OUTPUT/arch/arm64/boot/Image"
python3 - "$image" <<'PY'
import struct, sys
from pathlib import Path
data = bytearray(128)
data[0x38:0x3C] = b"ARM\x64"
struct.pack_into("<Q", data, 8, 0)
struct.pack_into("<Q", data, 16, len(data))
Path(sys.argv[1]).write_bytes(bytes(data))
PY
gzip -c "$image" > "$OUTPUT/arch/arm64/boot/Image.gz"

# kbuild's real `make O=<dir>` shape: output/source is an absolute symlink to
# the exact source tree, and output/build points back at the output dir.  The
# contract check must accept the first one and copy_kernel_state rewrites it.
ln -sfn "$(realpath -m "$SRC")" "$OUTPUT/source"
ln -sfn . "$OUTPUT/build"
cp "$OUTPUT/arch/arm64/boot/Image.gz" "$STAGE/boot/vmlinuz"
cp "$image" "$IMAGE"

# --- release identity ------------------------------------------------------
printf '%s\n' "$RELEASE" > "$OUTPUT/include/config/kernel.release"
printf '%s\n' "$RELEASE" > "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"

# --- DTBs ------------------------------------------------------------------
dtb_dir="${RC4_FAKE_DTB_DIR:-}"
if [[ -z "$dtb_dir" || ! -d "$dtb_dir" ]]; then
    echo "fake builder: RC4_FAKE_DTB_DIR with the reviewed DTBs is required" >&2
    exit 4
fi
for dtb in "$dtb_dir"/sm8150-oneplus-guacamole*.dtb; do
    cp "$dtb" "$STAGE/boot/dtbs/qcom/"
done

# --- module payload --------------------------------------------------------
mod="$STAGE/usr/lib/modules/$RELEASE"
mkdir -p "$mod/kernel/drivers/i2c/busses" "$mod/kernel/drivers/input/misc" \
         "$mod/kernel/drivers/media/i2c"
printf 'fake cci module\n'   > "$mod/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
printf 'fake goodix module\n' > "$mod/kernel/drivers/input/misc/goodix_fp.ko.zst"
printf 'fake imx471 module\n' > "$mod/kernel/drivers/media/i2c/imx471.ko.zst"
printf '%s\n' "kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst:" > "$mod/modules.dep"

echo "fake builder: staged $RELEASE under $STAGE"
echo "fake builder: no kernel-inputs.json / DELIVERY.json written (correct)"
