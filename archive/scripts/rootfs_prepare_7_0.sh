#!/usr/bin/env bash
# User-run 7.0 rootfs/initramfs build and boot-image packaging; no phone actions.
# pmbootstrap invokes sudo for its isolated chroots and image creation.
set -Eeuo pipefail

if (( EUID == 0 )); then
    echo "Run this script as your normal user; pmbootstrap invokes sudo itself." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/.work/7.0/rootfs"
LOG="$ROOT/.work/7.0/rootfs/build-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1
trap 'status=$?; if (( BASH_SUBSHELL == 0 )); then echo "Build failed at line $LINENO (exit $status). Log: $LOG" >&2; fi; exit "$status"' ERR
echo "Build log: $LOG"

# Confirm pmaports sees the local port-10000 hook, not the 9999 repository one.
test "$(readlink -f "$ROOT/.work/7.0/pmaports/main/postmarketos-mkinitfs-hook-netboot")" = \
     "$ROOT/device/testing/7.0/postmarketos-mkinitfs-hook-netboot"
grep -q '^NBD_PORT=10000$' \
     "$ROOT/.work/7.0/pmaports/main/postmarketos-mkinitfs-hook-netboot/netboot.sh"

# Match the pinned pmbootstrap's init layout without changing the prepared config.
WORK="$ROOT/.work/7.0/pmbootstrap-work"
if [ -f "$WORK/version" ]; then
    [ "$(cat "$WORK/version")" = 8 ] || {
        echo "Unsupported pmbootstrap work version; refusing to migrate $WORK" >&2
        exit 1
    }
elif [ -d "$WORK" ] && [ -n "$(ls -A "$WORK")" ]; then
    echo "Nonempty pmbootstrap work directory has no version marker: $WORK" >&2
    exit 1
fi
mkdir -p -m 700 "$WORK" "$WORK/cache_git"
if [ ! -f "$WORK/version" ]; then
    printf '8\n' > "$WORK/version"
fi

PMB=(python3 "$ROOT/.work/7.0/pmbootstrap/pmbootstrap.py" \
  -c "$ROOT/.work/7.0/rootfs/pmbootstrap.cfg" \
  -p "$ROOT/.work/7.0/pmaports" \
  -w "$WORK")

# 1. Package the already-built 7.0 Image/modules; no second compile.
(cd "$ROOT/.work/7.0/build-src" && "${PMB[@]}" build --envkernel \
  linux-postmarketos-qcom-sm8150)

# 2. Build the local user-space recipes, forcing the port-10000 hook.
"${PMB[@]}" build --arch aarch64 -i --force \
  firmware-oneplus-guacamole device-oneplus-guacamole \
  hexagonrpcd iio-sensor-proxy
"${PMB[@]}" build --arch aarch64 -i --force postmarketos-mkinitfs-hook-netboot

# 3. Read the exact local APK versions (envkernel builds may add _p...).
kernel_apk="$(find "$ROOT/.work/7.0/pmbootstrap-work/packages/edge/aarch64" \
  -maxdepth 1 -type f -name 'linux-postmarketos-qcom-sm8150-*.apk' \
  -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p')"
[ -n "$kernel_apk" ] || { echo "kernel APK not found" >&2; exit 1; }
kernel_version="$(basename "$kernel_apk" .apk)"
kernel_version="${kernel_version#linux-postmarketos-qcom-sm8150-}"
case "$kernel_version" in
  7.0.0*) ;;
  *) echo "refusing kernel version $kernel_version" >&2; exit 1 ;;
esac

hook_apk="$(find "$ROOT/.work/7.0/pmbootstrap-work/packages/edge/aarch64" \
  -maxdepth 1 -type f -name 'postmarketos-mkinitfs-hook-netboot-*.apk' \
  -printf '%T@ %p\n' | sort -nr | sed -n '1s/^[^ ]* //p')"
[ -n "$hook_apk" ] || { echo "port-10000 hook APK not found" >&2; exit 1; }
hook_version="$(basename "$hook_apk" .apk)"
hook_version="${hook_version#postmarketos-mkinitfs-hook-netboot-}"
case "$hook_version" in
  4.2.1-r3*) ;;
  *) echo "refusing hook version $hook_version; expected local 4.2.1-r3" >&2; exit 1 ;;
esac
echo "kernel APK: $kernel_apk ($kernel_version)"
echo "netboot hook APK: $hook_apk ($hook_version)"

# 4. Install, pinning both exact local APKs so a repo package cannot win.
"${PMB[@]}" --boot-size 1024 --extra-space 4096 install \
  --no-split --filesystem ext4 \
  --add "postmarketos-mkinitfs-hook-netboot=${hook_version},linux-postmarketos-qcom-sm8150=${kernel_version}"

# 5. Check the installed rootfs BEFORE exporting it.
rootfs_chroot="$ROOT/.work/7.0/pmbootstrap-work/chroot_rootfs_oneplus-guacamole"
[ "$(cat "$rootfs_chroot/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" 2>/dev/null)" = "7.0.0-sm8150" ] || \
  { echo "rootfs kernel release mismatch" >&2; exit 1; }
{ [ -d "$rootfs_chroot/usr/lib/modules/7.0.0-sm8150" ] || \
  [ -d "$rootfs_chroot/lib/modules/7.0.0-sm8150" ]; } || \
  { echo "rootfs is missing 7.0.0-sm8150 modules" >&2; exit 1; }
grep -q '^NBD_PORT=10000$' "$rootfs_chroot/usr/share/mkinitfs/hooks/netboot.sh" || \
  { echo "installed netboot hook is not port 10000" >&2; exit 1; }

# 6. Export the matching initramfs and rootfs.
"${PMB[@]}" export "$ROOT/.work/7.0/rootfs/export" --no-install

# 7. Build the temporary boot image with the 7.0 DTB and wrapper.
KERNEL_DIR="$ROOT/build/7.0/kernel-package" \
INITRAMFS="$ROOT/.work/7.0/rootfs/export/initramfs" \
DTB_MODE=abl-quarantine \
"$ROOT/scripts/build_bootimg_7_0.sh"

echo "Build complete: $ROOT/build/7.0/boot.img"
echo "Rootfs and initramfs: $ROOT/.work/7.0/rootfs/export"
echo "Log: $LOG"
echo "No phone boot or netboot server was started; hardware remains untested."
