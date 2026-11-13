#!/usr/bin/env bash
# Run ON THE PHONE, after installing the isolated libcamera-rear APK.
set -euo pipefail
PREFIX=/opt/nura-rear-camera
export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$PREFIX/bin/cam" "$@"
