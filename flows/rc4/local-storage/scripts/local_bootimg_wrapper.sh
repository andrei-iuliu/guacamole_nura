#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Build a local-rootfs boot image from the actual rc4 kernel package and a
# separately built local initramfs.  This is a host-only, user-run helper:
# it calls the canonical packer but writes only under the local output
# directory.  It never touches the netboot cache, boot partition or phone.
#
# Default is a dry run.  Add --build to execute the packer.  The initramfs must
# come from a rootfs whose installed netboot hook is guarded; the rc3 netboot
# initramfs is refused.  A hookless initramfs needs a reviewed packer change.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
FLOW_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
# Default: the checkout that contains this flow (flows/rc4/local-storage).
SOURCE_ROOT="${NURA_SOURCE_ROOT:-$(cd "$FLOW_DIR/../../.." && pwd -P)}"
TOOL="$FLOW_DIR/scripts/local_storage_tool.py"
RELEASE="${NURA_KERNEL_RELEASE:-7.0.0-sm8150-guacamole-rc4}"
KERNEL_DIR="${NURA_KERNEL_DIR:-$SOURCE_ROOT/flows/rc4/build-source/kernel-output/kernel-package}"
# NURA_UI=plasma|phosh only changes the defaults below (Phosh: *-phosh dirs).
case "${NURA_UI:-phosh}" in
    plasma) UI_SUFFIX="" ;;
    phosh) UI_SUFFIX="-phosh" ;;
    *) echo "ERROR: NURA_UI must be plasma or phosh" >&2; exit 2 ;;
esac
KERNEL_STATE="${NURA_KERNEL_STATE:-$FLOW_DIR/.work/local-userdata$UI_SUFFIX/kernel-state}"
OUTPUT_DIR="${NURA_LOCAL_OUT:-$FLOW_DIR/build/local-userdata$UI_SUFFIX}"
BOOT_OUT="$OUTPUT_DIR/boot"
TEMP_DTB_DIR="$OUTPUT_DIR/temporary-dtbo"
SERVED_RECORD="${NURA_SERVED_RECORD:-$SOURCE_ROOT/.work/delivery-plasma-mobile/served-image.json}"
INITRAMFS="${NURA_LOCAL_INITRAMFS:-}"
ROOTFS_IMAGE="${NURA_LOCAL_ROOTFS_IMAGE:-}"
ROOTFS_TREE="${NURA_LOCAL_ROOTFS_TREE:-}"
BUILD=0

usage() {
    sed -n '2,11p' "$0"
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

while (( $# )); do
    case "$1" in
        --build) BUILD=1 ;;
        --initramfs)
            (( $# >= 2 )) || die "--initramfs needs a path"
            INITRAMFS=$2
            shift
            ;;
        --initramfs=*) INITRAMFS=${1#*=} ;;
        --rootfs-image)
            (( $# >= 2 )) || die "--rootfs-image needs a path"
            ROOTFS_IMAGE=$2
            shift
            ;;
        --rootfs-image=*) ROOTFS_IMAGE=${1#*=} ;;
        --rootfs-tree)
            (( $# >= 2 )) || die "--rootfs-tree needs a path"
            ROOTFS_TREE=$2
            shift
            ;;
        --rootfs-tree=*) ROOTFS_TREE=${1#*=} ;;
        --output)
            (( $# >= 2 )) || die "--output needs a path"
            OUTPUT_DIR=$2
            BOOT_OUT="$OUTPUT_DIR/boot"
            TEMP_DTB_DIR="$OUTPUT_DIR/temporary-dtbo"
            shift
            ;;
        --output=*)
            OUTPUT_DIR=${1#*=}
            BOOT_OUT="$OUTPUT_DIR/boot"
            TEMP_DTB_DIR="$OUTPUT_DIR/temporary-dtbo"
            ;;
        --release)
            (( $# >= 2 )) || die "--release needs a value"
            RELEASE=$2
            shift
            ;;
        --release=*) RELEASE=${1#*=} ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

[[ -n "$INITRAMFS" ]] || die "--initramfs is required; do not default to the netboot initramfs"
[[ -f "$INITRAMFS" && ! -L "$INITRAMFS" ]] || die "initramfs is missing or a symlink: $INITRAMFS"
if [[ -n "$ROOTFS_TREE" && -z "$ROOTFS_IMAGE" ]]; then
    die "--rootfs-tree needs --rootfs-image; the rootfs identity must be pinned"
fi
[[ -f "$KERNEL_DIR/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" ]] ||
    die "not a kernel package: $KERNEL_DIR"
[[ -f "$KERNEL_STATE/output/.config" ]] || die "missing kernel config: $KERNEL_STATE/output/.config"
[[ -f "$FLOW_DIR/scripts/build_local_bootimg.sh" ]] || die "missing canonical packer"
[[ -f "$SOURCE_ROOT/scripts/prepare_temporary_dtb.py" ]] || die "missing canonical DTB preparer"

actual_release="$(cat "$KERNEL_DIR/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release")"
[[ "$actual_release" = "$RELEASE" ]] ||
    die "kernel package is $actual_release; requested $RELEASE -- build the real future kernel, do not relabel rc3"

case "$(realpath -m "$OUTPUT_DIR")" in
    "$(realpath -m "$SOURCE_ROOT/build/plasma-mobile")"|"$(realpath -m "$SOURCE_ROOT/build/plasma-mobile")"/*|\
    "$(realpath -m "$SOURCE_ROOT/flows/rc4/build-source/kernel-output")"|"$(realpath -m "$SOURCE_ROOT/flows/rc4/build-source/kernel-output")"/*|\
    "$(realpath -m "$SOURCE_ROOT/build/rear-camera")"|"$(realpath -m "$SOURCE_ROOT/build/rear-camera")"/*|\
    "$(realpath -m "$SOURCE_ROOT/.work/plasma-mobile")"|"$(realpath -m "$SOURCE_ROOT/.work/plasma-mobile")"/*|\
    "$(realpath -m "$SOURCE_ROOT/flows/rc4/build-source/kernel-state")"|"$(realpath -m "$SOURCE_ROOT/flows/rc4/build-source/kernel-state")"/*)
        die "output must not be inside a netboot/rc3/rc4 delivery directory: $OUTPUT_DIR"
        ;;
esac

# A separately built initramfs must not be the pinned netboot ramdisk, even if
# someone accidentally guarded it after packaging.
for forbidden in \
    "$SOURCE_ROOT/build/plasma-mobile/boot-initramfs" \
    "$SOURCE_ROOT/.work/plasma-mobile/rootfs/export/initramfs"; do
    if [[ -f "$forbidden" ]] && cmp -s -- "$INITRAMFS" "$forbidden"; then
        die "refusing the netboot initramfs: $forbidden"
    fi
done

KERNEL_SOURCE="$(find "$KERNEL_STATE" -maxdepth 1 -type d -name 'linux-*' | sort | head -n1)"
[[ -n "$KERNEL_SOURCE" && -f "$KERNEL_SOURCE/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts" ]] ||
    die "missing kernel source DTS under $KERNEL_STATE"
[[ -f "$KERNEL_DIR/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" ]] ||
    die "missing kernel package DTB"

echo "=== local-userdata boot image plan ==="
echo "release:        $RELEASE"
echo "kernel package: $KERNEL_DIR"
echo "kernel source:  $KERNEL_SOURCE"
echo "initramfs:      $INITRAMFS"
echo "output:         $OUTPUT_DIR"
echo "dtb:            abl-quarantine (stock DTBO preserved)"
echo "cmdline:        NETBOOT=0 (no pmos.root=/pmos.boot=)"
echo

python3 "$TOOL" check-initramfs --require-hook --archive "$INITRAMFS" --release "$RELEASE"

if (( ! BUILD )); then
    echo
    echo "DRY RUN: no files written. Re-run with --build to call the canonical packer."
    exit 0
fi

[[ -n "$ROOTFS_IMAGE" && -n "$ROOTFS_TREE" ]] ||
    die "--build requires --rootfs-image and --rootfs-tree; a final readiness manifest cannot omit the installed rootfs hook"

mkdir -p -- "$BOOT_OUT" "$TEMP_DTB_DIR"

python3 "$SOURCE_ROOT/scripts/prepare_temporary_dtb.py" \
    --base-dtb "$KERNEL_DIR/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
    --base-source-dts "$KERNEL_SOURCE/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts" \
    --patched-dts "$TEMP_DTB_DIR/base.dts" \
    --out-dir "$TEMP_DTB_DIR"

NETBOOT=0 \
DTB_MODE=abl-quarantine \
RELEASE="$RELEASE" \
KERNEL_DIR="$KERNEL_DIR" \
KERNEL_SOURCE="$KERNEL_SOURCE" \
BASE_DTB="$KERNEL_DIR/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
TEMP_DTB_DIR="$TEMP_DTB_DIR" \
INITRAMFS="$INITRAMFS" \
OUTPUT_DIR="$BOOT_OUT" \
CHECK_RECORDED_INPUTS=0 \
bash "$FLOW_DIR/scripts/build_local_bootimg.sh"

RECORD_ARGS=(
    record
    --boot-img "$BOOT_OUT/boot.img"
    --image "$BOOT_OUT/Image"
    --initramfs "$INITRAMFS"
    --kernel-dir "$KERNEL_DIR"
    --kernel-state "$KERNEL_STATE"
    --dtb-dir "$TEMP_DTB_DIR"
    --release "$RELEASE"
    --workspace "$SOURCE_ROOT"
    --tool "$TOOL"
    --builder "$FLOW_DIR/scripts/build_local_bootimg.sh"
    --dtb-preparer "$SOURCE_ROOT/scripts/prepare_temporary_dtb.py"
    --served-record "$SERVED_RECORD"
    --out "$OUTPUT_DIR/DELIVERY.local.json"
)
if [[ -n "$ROOTFS_IMAGE" ]]; then
    RECORD_ARGS+=(--rootfs-image "$ROOTFS_IMAGE")
fi
if [[ -n "$ROOTFS_TREE" ]]; then
    RECORD_ARGS+=(--rootfs-tree "$ROOTFS_TREE")
fi
python3 "$TOOL" "${RECORD_ARGS[@]}"

echo
echo "Next (read-only):"
echo "  python3 $TOOL check --manifest $OUTPUT_DIR/DELIVERY.local.json \\"
echo "      --rootfs-image <source.img> --rootfs-tree <rootfs-tree> --facts <facts.json>"
echo "  python3 $TOOL probes"
echo "No flash, erase, slot change or phone command was run."
