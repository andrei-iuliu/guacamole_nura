#!/usr/bin/env bash
set -e

# Flashes or test-boots the generated boot.img to the connected OnePlus 7 Pro.
# Usage:
#   ./scripts/flash_boot.sh test    # Safe: fastboot boot (does not touch flash)
#   ./scripts/flash_boot.sh flash   # Permanent: fastboot flash boot_b (untethered boot)
#
# "flash" writes slot b on purpose: that is the active slot and the one whose
# dtbo partition is zeroed, so ABL applies no stock DTBO overlay to the
# mainline DTB. The stock boot image is in stock_boot_full.img (restore with
# fastboot flash boot_b stock_boot_full.img). Prefer scripts/fastboot_boot.sh
# for trying out a new image; flash once it works.

ACTION="${1:-test}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== OnePlus 7 Pro Fastboot Helper ==="

if ! command -v fastboot &> /dev/null; then
    echo "Notice: host 'fastboot' not found in PATH."
    echo "Using 'pmbootstrap flasher' (runs fastboot inside native chroot)..."
    if [ "$ACTION" == "test" ]; then
        echo "Executing: pmbootstrap flasher boot"
        exec pmbootstrap flasher boot
    elif [ "$ACTION" == "flash" ]; then
        echo "Executing: pmbootstrap flasher flash_kernel"
        exec pmbootstrap flasher flash_kernel
    else
        echo "Invalid option: $ACTION. Use 'test' or 'flash'."
        exit 1
    fi
fi

echo "Checking fastboot devices..."
DEVICE=$(fastboot devices | awk '{print $1}' | head -n 1)
if [ -z "$DEVICE" ]; then
    echo "Error: No fastboot devices found. Please boot phone into bootloader mode."
    exit 1
fi
echo "Connected device: $DEVICE"

# Determine boot.img path
BOOTIMG=""
if [ -f "$REPO_ROOT/build/boot.img" ]; then
    BOOTIMG="$REPO_ROOT/build/boot.img"
elif [ -f "$REPO_ROOT/boot.img" ]; then
    BOOTIMG="$REPO_ROOT/boot.img"
else
    # Check default pmbootstrap chroot output
    CHROOT_BOOTIMG="$(pmbootstrap -q config work)/chroot_rootfs_oneplus-guacamole/boot/boot.img" 2>/dev/null || true
    if [ -f "$CHROOT_BOOTIMG" ]; then
        BOOTIMG="$CHROOT_BOOTIMG"
    fi
fi

if [ -z "$BOOTIMG" ] || [ ! -f "$BOOTIMG" ]; then
    echo "Notice: No boot.img found in repo root or pmbootstrap chroot."
    echo "You can generate one using: pmbootstrap flasher boot"
    echo "Or place your boot.img in the repository root."
    exit 1
fi

echo "Using boot image: $BOOTIMG"

if [ "$ACTION" == "test" ]; then
    echo "Testing boot image via 'fastboot boot $BOOTIMG' (Non-destructive)..."
    fastboot boot "$BOOTIMG"
elif [ "$ACTION" == "flash" ]; then
    echo "Flashing slot b via 'fastboot flash boot_b $BOOTIMG'..."
    fastboot flash boot_b "$BOOTIMG"
else
    echo "Invalid option: $ACTION. Use 'test' or 'flash'."
    exit 1
fi
