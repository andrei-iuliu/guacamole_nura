#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Pack the local-userdata guacamole boot image from the source rc3 kernel package.
# Host-only: this copied flow script never talks to a phone.  SOURCE_ROOT is
# read-only; OUTPUT_DIR/TEMP_DTB_DIR are flow-local.  Defaults to NETBOOT=0 and
# the actual rc3 release; a future rc4 must pass its real rebuilt package.
#
# The 7.0 Image is larger than the 6.17/7.3.3 Images.  Its arm64 header
# extent (0x2ae0000) ends at 0x2ae8000 with kernel_offset=0x8000, so neither
# the stock DTB address 0x01f00000 nor the discarded 7.3.3 experiment's
# 0x02400000 is safe.  The default below leaves about 5.1 MiB of margin and
# is deliberately marked UNTESTED because no relocation of the DTB has been
# booted on this phone.
set -euo pipefail

FLOW_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SOURCE_ROOT="${NURA_SOURCE_ROOT:-runtime-workspace}"
[[ -d "$SOURCE_ROOT" ]] || { echo "ERROR: missing source checkout: $SOURCE_ROOT" >&2; exit 1; }
OUTPUT_DIR="${OUTPUT_DIR:-${FLOW_DIR}/build/local-userdata/boot}"
RELEASE="${RELEASE:-7.0.0-sm8150-rc3}"
KERNEL_DIR="${KERNEL_DIR:-${SOURCE_ROOT}/build/rc3-kernel/kernel-package}"
BASE_DTB="${BASE_DTB:-${KERNEL_DIR}/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb}"
DTB_MODE="${DTB_MODE:-abl-quarantine}"
TEMP_DTB_DIR="${TEMP_DTB_DIR:-${OUTPUT_DIR}/temporary-dtbo}"
NETBOOT="${NETBOOT:-0}"
DEBUG_CMDLINE=""
case "${NURA_LOCAL_DEBUG_SHELL:-0}" in
    0) ;;
    1) DEBUG_CMDLINE="pmos.debug-shell" ;;
    *) echo 'ERROR: NURA_LOCAL_DEBUG_SHELL must be 0 or 1' >&2; exit 1 ;;
esac

# Stock geometry would be 0x01f00000, but it is inside this Image's extent.
# 0x02400000 (the 7.3.3 full-symbol candidate) is also inside it.  Keep the
# different value visible and explain it in the output; do not copy the old
# default.
DTB_OFFSET="${DTB_OFFSET:-0x03000000}"
KERNEL_OFFSET="${KERNEL_OFFSET:-0x00008000}"
RAMDISK_OFFSET="${RAMDISK_OFFSET:-0x01000000}"

BOOTIMG="${OUTPUT_DIR}/boot.img"
IMAGE="${OUTPUT_DIR}/Image"
LOCAL_BIN="${SOURCE_ROOT}/local/bin"
LOCAL_LIB="${SOURCE_ROOT}/local/lib"
MKBOOTIMG="${SOURCE_ROOT}/local/tools/mkbootimg/mkbootimg.py"
AVBTOOL="${SOURCE_ROOT}/local/tools/avbtool.py"

export PATH="${LOCAL_BIN}:${PATH}"
export LD_LIBRARY_PATH="${LOCAL_LIB}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

[[ "${RELEASE}" =~ ^[0-9A-Za-z._+-]+$ ]] ||
    { echo "ERROR: invalid RELEASE='${RELEASE}'" >&2; exit 1; }
echo "=== Packing OnePlus 7 Pro (guacamole) ${RELEASE} boot.img ==="

mkdir -p "${OUTPUT_DIR}"

check_hash() {
    local file="$1" expected="$2" got
    got="$(sha256sum "${file}" | awk '{print $1}')"
    if [ "${got}" != "${expected}" ]; then
        echo "ERROR: project-local tool hash mismatch: ${file}" >&2
        echo "       expected ${expected}" >&2
        echo "       got      ${got}" >&2
        exit 1
    fi
}
check_hash "${MKBOOTIMG}" "37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
check_hash "${AVBTOOL}" "e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b"
for tool in dtc fdtget fdtput fdtoverlay; do
    [ -x "${LOCAL_BIN}/${tool}" ] || { echo "ERROR: missing ${LOCAL_BIN}/${tool}" >&2; exit 1; }
done

# 1. Uncompressed Image from the same staging tree as the modules.
echo "[1/5] Preparing Image..."
KERNEL_RELEASE_FILE="${KERNEL_DIR}/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
if [ ! -f "${KERNEL_RELEASE_FILE}" ]; then
    echo "ERROR: missing ${KERNEL_RELEASE_FILE}; build/stage the 7.0 kernel first." >&2
    exit 1
fi
KERNEL_RELEASE="$(cat "${KERNEL_RELEASE_FILE}")"
if [ "${KERNEL_RELEASE}" != "${RELEASE}" ]; then
    echo "ERROR: staging release is '${KERNEL_RELEASE}', expected ${RELEASE}." >&2
    echo "       Refusing to mix another kernel with these modules." >&2
    exit 1
fi
if [ -f "${KERNEL_DIR}/boot/vmlinuz" ]; then
    gzip -dc "${KERNEL_DIR}/boot/vmlinuz" > "${IMAGE}"
elif [ -f "${KERNEL_DIR}/boot/Image" ]; then
    cp "${KERNEL_DIR}/boot/Image" "${IMAGE}"
else
    echo "ERROR: no boot/vmlinuz or boot/Image under ${KERNEL_DIR}" >&2
    exit 1
fi
if [ ! -f "${BASE_DTB}" ]; then
    echo "ERROR: missing base DTB: ${BASE_DTB}" >&2
    exit 1
fi
echo "Kernel release: ${KERNEL_RELEASE}"
echo "Base DTB:       ${BASE_DTB}"

# 2. Select the base DTB or the validated stock-DTBO quarantine wrapper.
echo "[2/5] Selecting DTB (mode: ${DTB_MODE})..."
case "${DTB_MODE}" in
    original)
        DTB="${BASE_DTB}"
        echo "WARNING: DTB_MODE=original assumes the active DTBO is absent or zeroed."
        echo "         The stock-DTBO-preserving path is DTB_MODE=abl-quarantine."
        ;;
    abl-quarantine|temporary-wrapper)
        KERNEL_SOURCE="${KERNEL_SOURCE:-$(find "${SOURCE_ROOT}/.work/rc3-kernel" -maxdepth 1 -type d -name 'linux-*' | sort | head -n1)}"
        [[ -f "${TEMP_DTB_DIR}/manifest.json" ]] || python3 "${SOURCE_ROOT}/scripts/prepare_temporary_dtb.py" \
            --base-dtb "${BASE_DTB}" \
            --base-source-dts "${KERNEL_SOURCE}/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts" \
            --patched-dts "${TEMP_DTB_DIR}/base.dts" \
            --out-dir "${TEMP_DTB_DIR}"
        python3 "${SOURCE_ROOT}/scripts/prepare_temporary_dtb.py" \
            --base-dtb "${BASE_DTB}" --out-dir "${TEMP_DTB_DIR}" --verify
        # --verify validates the manifest's stored base path; also make sure
        # the DTB selected for this pack is the same content.
        manifest_base_hash="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["inputs"]["base_dtb"]["sha256"])' "${TEMP_DTB_DIR}/manifest.json")"
        base_hash="$(sha256sum "${BASE_DTB}" | awk '{print $1}')"
        if [ "${manifest_base_hash}" != "${base_hash}" ]; then
            echo "ERROR: wrapper manifest base DTB does not match ${BASE_DTB}" >&2
            echo "       manifest ${manifest_base_hash}" >&2
            echo "       selected ${base_hash}" >&2
            exit 1
        fi
        DTB="${TEMP_DTB_DIR}/custom_guacamole-abl-quarantine.dtb"
        ;;
    *)
        echo "ERROR: unknown DTB_MODE='${DTB_MODE}' (use original or abl-quarantine)" >&2
        exit 1
        ;;
esac
[ -f "${DTB}" ] || { echo "ERROR: selected DTB is missing: ${DTB}" >&2; exit 1; }
echo "Using DTB: ${DTB}"

NETBOOT_CMDLINE=""
if [ "${DTB_MODE}" != "original" ] && [ "${NETBOOT}" != "0" ]; then
    # Point the initramfs at the NBD subpartitions so it cannot fall back to
    # an internal pmOS root while a temporary image is being tested.
    NETBOOT_CMDLINE="pmos.root=/dev/nbd0p2 pmos.boot=/dev/nbd0p1"
fi

# 3. Require an explicit matching initramfs.  There is deliberately no /tmp
#    or 6.17 fallback: the module tree in the initramfs must be 7.0.0-sm8150-rear1.
echo "[3/5] Locating initramfs..."
if [ -z "${INITRAMFS:-}" ]; then
    echo "ERROR: INITRAMFS is not set." >&2
    echo "       Build the 7.0 rootfs/initramfs first (see docs/REAR_CAMERA_7_0_BRINGUP.md)." >&2
    echo "       Refusing to package a stale /tmp or 6.17 ramdisk." >&2
    exit 1
fi
if [ ! -f "${INITRAMFS}" ]; then
    echo "ERROR: initramfs not found: ${INITRAMFS}" >&2
    exit 1
fi
echo "Using initramfs: ${INITRAMFS}"

# Fail closed unless the ramdisk lists exactly the same module release.
# This is a path-level consistency check (listing only), not an integrity
# check; see scripts/check_initramfs_release.py.
python3 "${FLOW_DIR}/scripts/check_initramfs_release.py" \
    --archive "${INITRAMFS}" \
    --release "${KERNEL_RELEASE}"

# 4. Refuse an envelope where the Image extent reaches the DTB address.
# The privileged preparer hashes these genuine inputs after checking port 10001.
# The candidate initramfs may be a newly exported rootfs archive with no prior
# packaging-inputs.json.  The listing/module-payload checks still run; only the
# comparison with a *previous* packaging-inputs.json is opt-in.
CHECK_INPUTS_ARGS=(--initramfs "${INITRAMFS}" --kernel-dir "${KERNEL_DIR}" --release "${RELEASE}")
if [ "${CHECK_RECORDED_INPUTS:-0}" = "1" ]; then
    CHECK_INPUTS_ARGS+=(--manifest "${CHECK_INPUTS_MANIFEST:-${OUTPUT_DIR}/packaging-inputs.json}")
else
    CHECK_INPUTS_ARGS+=(--no-recorded-inputs)
fi
python3 "${FLOW_DIR}/scripts/check_rear_camera_package.py" "${CHECK_INPUTS_ARGS[@]}"
echo "[4/5] Checking boot envelope..."
python3 "${FLOW_DIR}/scripts/check_boot_envelope.py" \
    --image "${IMAGE}" \
    --kernel-offset "${KERNEL_OFFSET}" \
    --dtb-offset "${DTB_OFFSET}" \
    --ramdisk-offset "${RAMDISK_OFFSET}" \
    --ramdisk-size "$(stat -c %s "${INITRAMFS}")"
if [ "${DTB_OFFSET}" != "0x01f00000" ]; then
    echo "WARNING: DTB_OFFSET=${DTB_OFFSET} is a relocation from the stock 0x01f00000."
    echo "         The 7.0 Image extent requires it.  This relocation is UNTESTED"
    echo "         on hardware; verify the address with the ABL/live DTB before booting."
fi

# 5. Pack header v2 and append the AVB hash footer.
echo "[5/5] Packing boot.img (header v2, page 4096)..."
python3 "${MKBOOTIMG}" \
    --header_version 2 \
    --kernel "${IMAGE}" \
    --ramdisk "${INITRAMFS}" \
    --dtb "${DTB}" \
    --base 0x00000000 \
    --kernel_offset "${KERNEL_OFFSET}" \
    --ramdisk_offset "${RAMDISK_OFFSET}" \
    --second_offset 0x00000000 \
    --tags_offset 0x00000100 \
    --dtb_offset "${DTB_OFFSET}" \
    --pagesize 4096 \
    --os_version 10.0.0 \
    --os_patch_level 2021-01 \
    --cmdline "console=tty0 console=ttyMSM0,115200n8 earlycon ramoops.mem_address=0xa9800000 ramoops.mem_size=0x400000 ramoops.record_size=0x40000 ramoops.console_size=0x40000 ramoops.pmsg_size=0x200000 pmOS_loglevel=8 PMOS_NO_OUTPUT_REDIRECT ${NETBOOT_CMDLINE:+${NETBOOT_CMDLINE} }${DEBUG_CMDLINE:+${DEBUG_CMDLINE} }scsi_mod.scan=sync" \
    -o "${BOOTIMG}"

python3 "${AVBTOOL}" add_hash_footer \
    --image "${BOOTIMG}" \
    --dynamic_partition_size \
    --partition_name boot \
    --algorithm NONE

echo "SUCCESS: ${BOOTIMG} ($(stat -c %s "${BOOTIMG}") bytes)"
echo "This image is NOT hardware-tested.  Do not flash it; use a separately"
echo "approved temporary boot."
