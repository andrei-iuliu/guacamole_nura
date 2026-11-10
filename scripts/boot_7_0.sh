#!/usr/bin/env bash
# User-run: reboot Android into fastboot, then temporarily boot the 7.0 image.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$ROOT/platform-tools:$PATH"
IMAGE="$ROOT/build/7.0/boot.img"
[ -f "$IMAGE" ] || { echo "Missing boot image: $IMAGE" >&2; exit 1; }
if ! fastboot devices | grep -q '[[:space:]]fastboot'; then
    adb reboot bootloader
fi
exec bash "$ROOT/scripts/fastboot_boot.sh" "$IMAGE"
