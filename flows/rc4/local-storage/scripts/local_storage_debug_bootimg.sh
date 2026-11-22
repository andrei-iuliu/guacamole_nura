#!/usr/bin/env bash
# USER RUN: pack a separate debug image; never boots/flashes or changes userdata.
# NURA_UI=plasma|phosh picks the matching local-userdata state (default phosh since 2026-11-14);
# NURA_PMAPORTS_REF (./nura build --pmaports-ref REF) picks the -nura state.
set -Eeuo pipefail
FLOW=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
case "${NURA_UI:-phosh}" in
    plasma) SUFFIX="" ;;
    phosh) SUFFIX="-phosh" ;;
    *) echo "NURA_UI must be plasma or phosh" >&2; exit 2 ;;
esac
# A --pmaports-ref build keeps its own state and output (-nura suffix).
[[ -z "${NURA_PMAPORTS_REF:-}" ]] || SUFFIX="$SUFFIX-nura"
STATE="$FLOW/.work/local-userdata$SUFFIX/rootfs-state"
[[ $# == 0 ]] || { echo 'Usage: local_storage_debug_bootimg.sh' >&2; exit 2; }
NURA_LOCAL_DEBUG_SHELL=1 bash "$FLOW/scripts/local_bootimg_wrapper.sh" \
    --initramfs "$STATE/rootfs/export/initramfs" \
    --rootfs-image "$STATE/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img" \
    --rootfs-tree "$STATE/pmbootstrap-work/chroot_rootfs_oneplus-guacamole" \
    --output "$FLOW/build/local-debug$SUFFIX" --build
