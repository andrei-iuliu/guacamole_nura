#!/usr/bin/env bash
# Plasma Mobile delivery wrapper: the same verified NBD server as
# camera_serve.sh, with isolated state and build paths.
#
#   bash scripts/plasma_serve.sh --check
#   bash scripts/plasma_serve.sh --cleanup --check
#   bash scripts/plasma_serve.sh --dry-run
#   bash scripts/plasma_serve.sh
#
# This never flashes or reboots the phone. The profile is fixed here so a
# stray --profile cannot mix Plasma with the working Phosh candidate.
# Normal serving clears leftover chroots first; --check alone stays read-only.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)

for argument in "$@"; do
    case $argument in
        --profile|--profile=*)
            printf 'FAIL  plasma_serve.sh: profile is fixed to plasma-mobile; use camera_serve.sh for rear-camera\n' >&2
            exit 2
            ;;
    esac
done

exec bash "$ROOT/scripts/camera_serve.sh" --profile plasma-mobile "$@"
