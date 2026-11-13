#!/usr/bin/env bash
# USER RUN: pack a separate debug image; never boots/flashes or changes userdata.
set -Eeuo pipefail
FLOW=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
STATE="$FLOW/.work/local-userdata/rootfs-state"
[[ $# == 0 ]] || { echo 'Usage: local_storage_debug_bootimg.sh' >&2; exit 2; }
NURA_LOCAL_DEBUG_SHELL=1 bash "$FLOW/scripts/local_bootimg_wrapper.sh" \
    --initramfs "$STATE/rootfs/export/initramfs" \
    --rootfs-image "$STATE/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img" \
    --rootfs-tree "$STATE/pmbootstrap-work/chroot_rootfs_oneplus-guacamole" \
    --output "$FLOW/build/local-debug" --build
