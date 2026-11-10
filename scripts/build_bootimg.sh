#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHROOT_ROOTFS="${CHROOT_ROOTFS:-${HOME}/.local/var/pmbootstrap/chroot_rootfs_oneplus-guacamole}"
OUTPUT_DIR="${WORKSPACE_DIR}/build"
BOOTIMG="${OUTPUT_DIR}/boot.img"
DTB_MODE="${DTB_MODE:-original}"
TEMP_DTB_DIR="${TEMP_DTB_DIR:-${OUTPUT_DIR}/temporary-dtbo}"
# The temporary wrapper image is the netboot first boot by default.  Set
# NETBOOT=0 when using the wrapper with an internal-rootfs initramfs.
NETBOOT="${NETBOOT:-1}"
# The 6.17 baseline uses the stock DTB address.  Do not copy the discarded
# 7.3.3 experiment's 0x02400000 default here: the guard below rejects any
# Image whose extent reaches the selected address, and 7.0 needs its own
# measured value in scripts/build_bootimg_7_0.sh.
DTB_OFFSET="${DTB_OFFSET:-0x01f00000}"
LOCAL_BIN="${WORKSPACE_DIR}/local/bin"
LOCAL_LIB="${WORKSPACE_DIR}/local/lib"
MKBOOTIMG="${WORKSPACE_DIR}/local/tools/mkbootimg/mkbootimg.py"
AVBTOOL="${WORKSPACE_DIR}/local/tools/avbtool.py"

# Use the copied, hash-pinned host tools instead of whatever is on the host
# PATH.  libfdt comes from local/lib so fdtoverlay/fdtget have one ABI.
export PATH="${LOCAL_BIN}:${PATH}"
export LD_LIBRARY_PATH="${LOCAL_LIB}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

echo "=== Building OnePlus 7 Pro (guacamole) Mainline boot.img ==="

mkdir -p "${OUTPUT_DIR}"

for tool in dtc fdtget fdtput fdtoverlay; do
    if [ ! -x "${LOCAL_BIN}/${tool}" ]; then
        echo "ERROR: missing project-local host tool ${LOCAL_BIN}/${tool}" >&2
        exit 1
    fi
done
if [ ! -f "${MKBOOTIMG}" ]; then
    echo "ERROR: missing project-local mkbootimg: ${MKBOOTIMG}" >&2
    exit 1
fi
if [ ! -f "${AVBTOOL}" ]; then
    echo "ERROR: missing project-local avbtool: ${AVBTOOL}" >&2
    exit 1
fi
check_hash() {
    local file="$1" expected="$2" got
    got="$(sha256sum "${file}" | awk '{print $1}')"
    if [ "${got}" != "${expected}" ]; then
        echo "ERROR: copied tool hash mismatch: ${file}" >&2
        echo "       expected ${expected}" >&2
        echo "       got      ${got}" >&2
        exit 1
    fi
}
check_hash "${MKBOOTIMG}" "37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
check_hash "${AVBTOOL}" "e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b"

# 1. Prepare uncompressed kernel
echo "[1/5] Decompressing kernel Image..."
# KERNEL_DIR selects an extracted kernel package (default: build/kernel_r6).
# Its modules must match the ones installed on the phone's rootfs.  For the
# current Nura build use KERNEL_DIR=build/nura-kernel-package.
KERNEL_DIR="${KERNEL_DIR:-${WORKSPACE_DIR}/build/kernel_r6}"
if [ -f "${KERNEL_DIR}/boot/vmlinuz" ]; then
    echo "Using kernel from ${KERNEL_DIR}"
    zcat "${KERNEL_DIR}/boot/vmlinuz" > "${OUTPUT_DIR}/Image"
elif [ -f "${CHROOT_ROOTFS}/boot/vmlinuz" ]; then
    zcat "${CHROOT_ROOTFS}/boot/vmlinuz" > "${OUTPUT_DIR}/Image"
elif [ -f "${CHROOT_ROOTFS}/boot/Image" ]; then
    cp "${CHROOT_ROOTFS}/boot/Image" "${OUTPUT_DIR}/Image"
else
    echo "ERROR: Kernel not found!" >&2
    exit 1
fi

# 2. Patch and compile DTB with Qualcomm board IDs and ramoops, then select
#    either the original DTB or the verified stock-DTBO quarantine wrapper.
echo "[2/5] Preparing DTB (mode: ${DTB_MODE})..."
python3 "${WORKSPACE_DIR}/scripts/patch_dtb.py"
case "${DTB_MODE}" in
    original)
        DTB="${OUTPUT_DIR}/custom_guacamole.dtb"
        echo "WARNING: DTB_MODE=original requires the active DTBO to be absent or zeroed."
        echo "         Use DTB_MODE=abl-quarantine to preserve the stock DTBO for a temporary boot."
        ;;
    abl-quarantine|temporary-wrapper)
        # The manifest pins the base DTB hash.  If patch_dtb.py changed the
        # base, this fails closed and asks for prepare_temporary_dtb.py again.
        python3 "${WORKSPACE_DIR}/scripts/prepare_temporary_dtb.py" \
            --out-dir "${TEMP_DTB_DIR}" --verify
        DTB="${TEMP_DTB_DIR}/custom_guacamole-abl-quarantine.dtb"
        ;;
    *)
        echo "ERROR: unknown DTB_MODE='${DTB_MODE}' (use original or abl-quarantine)" >&2
        exit 1
        ;;
esac
if [ ! -f "${DTB}" ]; then
    echo "ERROR: selected DTB is missing: ${DTB}" >&2
    exit 1
fi
echo "Using DTB: ${DTB}"

NETBOOT_CMDLINE=""
if [ "${DTB_MODE}" != "original" ] && [ "${NETBOOT}" != "0" ]; then
    # Select the NBD subpartitions directly so the initramfs cannot fall back
    # to an internal pmOS root while the temporary image is being tested.
    NETBOOT_CMDLINE="pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1"
    echo "Netboot cmdline: ${NETBOOT_CMDLINE}"
fi

# 3. Locate initramfs.  The old /tmp/device_initramfs_r4 fallback was removed
#    because it could silently package a stale ramdisk from another build.
echo "[3/5] Locating initramfs..."
if [ -n "${INITRAMFS:-}" ]; then
    echo "Using initramfs override ${INITRAMFS}"
elif [ -f "${CHROOT_ROOTFS}/boot/initramfs" ]; then
    INITRAMFS="${CHROOT_ROOTFS}/boot/initramfs"
else
    echo "ERROR: initramfs not found. Set INITRAMFS explicitly." >&2
    echo "       The stale /tmp/device_initramfs_r4 fallback has been removed." >&2
    exit 1
fi
if [ ! -f "${INITRAMFS}" ]; then
    echo "ERROR: initramfs not found at ${INITRAMFS}!" >&2
    exit 1
fi
echo "Using initramfs: ${INITRAMFS}"

# The arm64 Image header image_size includes BSS; refuse to pack an image
# whose extent reaches the DTB address.  This is the regression check for the
# first 7.3.3 candidate, kept for the 6.17 path as well.
python3 "${WORKSPACE_DIR}/scripts/check_boot_envelope.py" \
    --image "${OUTPUT_DIR}/Image" \
    --kernel-offset 0x8000 \
    --dtb-offset "${DTB_OFFSET}" \
    --ramdisk-size "$(stat -c %s "${INITRAMFS}")"

# 4. Generate boot.img with mkbootimg (Header Version 2)
echo "[4/5] Packing boot.img (Header Version 2)..."
python3 "${MKBOOTIMG}" \
    --header_version 2 \
    --kernel "${OUTPUT_DIR}/Image" \
    --ramdisk "${INITRAMFS}" \
    --dtb "${DTB}" \
    --base 0x00000000 \
    --kernel_offset 0x00008000 \
    --ramdisk_offset 0x01000000 \
    --second_offset 0x00000000 \
    --tags_offset 0x00000100 \
    --dtb_offset "${DTB_OFFSET}" \
    --pagesize 4096 \
    --os_version 10.0.0 \
    --os_patch_level 2021-01 \
    --cmdline "console=tty0 console=ttyMSM0,115200n8 earlycon ramoops.mem_address=0xa9800000 ramoops.mem_size=0x400000 ramoops.record_size=0x40000 ramoops.console_size=0x40000 ramoops.pmsg_size=0x200000 pmOS_loglevel=8 PMOS_NO_OUTPUT_REDIRECT ${NETBOOT_CMDLINE:+${NETBOOT_CMDLINE} }scsi_mod.scan=sync" \
    -o "${BOOTIMG}"

# 5. Append AVB footer for Qualcomm ABL / libavb authentication
echo "[5/5] Appending AVB hash footer..."
python3 "${AVBTOOL}" add_hash_footer \
    --image "${BOOTIMG}" \
    --dynamic_partition_size \
    --partition_name boot \
    --algorithm NONE

echo "SUCCESS! Generated: ${BOOTIMG} ($(ls -lh "${BOOTIMG}" | awk '{print $5}'))"
