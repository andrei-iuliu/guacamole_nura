#!/usr/bin/env bash
# Plasma Mobile delivery wrapper: identity-checked temporary boot of
# build/plasma-mobile/boot.img through camera_boot.sh.
#
#   bash scripts/plasma_boot.sh status
#   bash scripts/plasma_boot.sh boot --dry-run
#   bash scripts/plasma_boot.sh boot
#
# There is no flash path. The profile is fixed here so a stray --profile cannot
# mix Plasma with the working Phosh candidate.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

for argument in "$@"; do
    case $argument in
        --profile|--profile=*)
            printf 'ERROR: plasma_boot.sh: profile is fixed to plasma-mobile; use camera_boot.sh for rear-camera\n' >&2
            exit 2
            ;;
    esac
done

exec bash "$ROOT/scripts/camera_boot.sh" --profile plasma-mobile "$@"
