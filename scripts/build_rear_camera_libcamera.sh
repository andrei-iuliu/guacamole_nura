#!/usr/bin/env bash
# Rootless aarch64 musl cross-build using this checkout's prepared sysroot/venv.
# See the bring-up report for sysroot provenance; no packages are installed here.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
STATE="$ROOT/.work/rear-camera"
RECIPE="$ROOT/device/testing/rear-camera/libcamera-rear"
export PATH="$STATE/venv/bin:$PATH"
test -f "$STATE/aarch64-musl.ini"
while read -r expected name; do
    [[ -n "$expected" ]] || continue
    path="$RECIPE/$name"
    [[ "$name" != *.tar.gz ]] || path="$STATE/$name"
    [[ "$(sha512sum "$path" | cut -d' ' -f1)" == "$expected" ]] || { echo "Bad input: $path" >&2; exit 1; }
done < <(sh -c '. "$1"; printf "%s\n" "$sha512sums"' sh "$RECIPE/APKBUILD")
SOURCE="$STATE/libcamera-v0.7.2"
identity="$(sha256sum "$RECIPE/APKBUILD" | cut -d' ' -f1)"
if [[ ! -d "$SOURCE" ]]; then
    tar xzf "$STATE/libcamera-v0.7.2.tar.gz" -C "$STATE"
    for patch in "$RECIPE"/*.patch; do
        patch -d "$SOURCE" -p1 --fuzz=0 --no-backup-if-mismatch -i "$patch"
    done
    printf '%s\n' "$identity" > "$SOURCE/.rear-source.sha256"
fi
[[ "$(cat "$SOURCE/.rear-source.sha256")" == "$identity" ]] || { echo 'Source recipe changed: prepare a fresh local libcamera source tree.' >&2; exit 1; }
meson setup --reconfigure "$STATE/libcamera-cross" "$SOURCE" \
    --cross-file "$STATE/aarch64-musl.ini" --prefix=/opt/nura-rear-camera --libdir=lib \
    --buildtype=release -Dpipelines=simple -Dipas=simple -Dsoftisp-gpu=enabled \
    -Dcam=enabled -Dcam-output-kms=disabled -Dcam-output-sdl2=disabled \
    -Dcam-jpeg=disabled -Dapps-output-dng=disabled -Dgstreamer=disabled \
    -Dqcam=disabled -Dpycamera=disabled -Ddocumentation=disabled \
    -Dlc-compliance=disabled -Dtracing=disabled -Dv4l2=false -Dtest=false \
    -Dlibdw=disabled -Dlibunwind=disabled -Dwerror=false
meson compile -C "$STATE/libcamera-cross" -j "${JOBS:-8}"
meson install --no-rebuild -C "$STATE/libcamera-cross" \
    --destdir "$ROOT/build/rear-camera/libcamera-stage"
PREFIX="$ROOT/build/rear-camera/libcamera-stage/opt/nura-rear-camera"
cp "$RECIPE"/*.yaml "$PREFIX/share/libcamera/ipa/simple/"
openssl pkey -in "$STATE/libcamera-cross/src/ipa-priv-key.pem" -pubout \
    -out "$ROOT/build/rear-camera/ipa-public.pem"
openssl dgst -sha256 -verify "$ROOT/build/rear-camera/ipa-public.pem" \
    -signature "$PREFIX/lib/libcamera/ipa/ipa_soft_simple.so.sign" \
    "$PREFIX/lib/libcamera/ipa/ipa_soft_simple.so"
