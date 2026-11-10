#!/usr/bin/env bash
# USER RUN ONLY: Android may reboot to fastboot; only temporarily boots, never flashes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="$ROOT/build/rear-camera/boot.img"
ADB="$ROOT/platform-tools/adb"
FASTBOOT="$ROOT/platform-tools/fastboot"
if [[ ! -x "$FASTBOOT" ]]; then FASTBOOT="$(command -v fastboot || true)"; fi
if [[ ! -x "$ADB" ]]; then ADB="$(command -v adb || true)"; fi
[[ -f "$IMAGE" ]] || { echo "Missing candidate boot image: $IMAGE" >&2; exit 1; }
[[ -x "$FASTBOOT" ]] || { echo "fastboot not found (expected $ROOT/platform-tools/fastboot or PATH)" >&2; exit 1; }
[[ -x "$ADB" ]] || { echo "adb not found (expected $ROOT/platform-tools/adb or PATH)" >&2; exit 1; }

FASTBOOT_DEVICES="$("$FASTBOOT" devices)"
if ! grep -q '[[:space:]]fastboot' <<<"$FASTBOOT_DEVICES"; then
    echo "Phone not in fastboot; requesting Android reboot to bootloader with bundled adb."
    "$ADB" reboot bootloader
fi
echo "Temporarily booting candidate image once: $IMAGE"
exec "$FASTBOOT" boot "$IMAGE"
