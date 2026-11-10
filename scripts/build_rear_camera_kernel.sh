#!/usr/bin/env bash
# Rootless kernel/DT/modules build only. Never invokes pmbootstrap or a phone.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RECIPE="$ROOT/device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
STATE="$ROOT/.work/rear-camera"
SOURCE="$STATE/linux-v7.0.0-sm8150"
OUTPUT="$STATE/output"
STAGE="$ROOT/build/rear-camera/kernel-package"
export KBUILD_BUILD_VERSION=2
export KBUILD_BUILD_TIMESTAMP='2026-11-10 00:00:00 UTC'
export KBUILD_BUILD_USER=nura
export KBUILD_BUILD_HOST=rear-camera
export PATH="$ROOT/local/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$STATE" "$OUTPUT" "$ROOT/build/rear-camera"
while read -r expected name; do
    [[ -n "$expected" ]] || continue
    path="$RECIPE/$name"
    [[ "$name" != linux-*.tar.gz ]] || path="$STATE/$name"
    [[ "$(sha512sum "$path" | cut -d' ' -f1)" == "$expected" ]] || { echo "Bad input: $path" >&2; exit 1; }
done < <(sh -c '. "$1"; printf "%s\n" "$sha512sums"' sh "$RECIPE/APKBUILD")
identity="$(sha256sum "$RECIPE/APKBUILD" | cut -d' ' -f1)"
if [[ ! -d "$SOURCE" ]]; then
    tar xzf "$STATE/linux-v7.0.0-sm8150.tar.gz" -C "$STATE"
    for patch in "$RECIPE"/0*.patch; do
        flags=()
        [[ "${patch##*/}" < 0028 ]] || flags=(--fuzz=0)
        patch -d "$SOURCE" -p1 --forward --no-backup-if-mismatch "${flags[@]}" -i "$patch"
    done
    printf '%s\n' "$identity" > "$SOURCE/.rear-source.sha256"
fi
[[ "$(cat "$SOURCE/.rear-source.sha256")" == "$identity" ]] || { echo 'Source recipe changed: prepare a fresh local source tree before rebuilding.' >&2; exit 1; }
cp "$RECIPE/config-postmarketos-qcom-sm8150.aarch64" "$OUTPUT/.config"
make -C "$SOURCE" O="$OUTPUT" ARCH=arm64 LLVM=1 olddefconfig
make -C "$SOURCE" O="$OUTPUT" ARCH=arm64 LLVM=1 -j"${JOBS:-12}" \
    Image.gz modules qcom/sm8150-oneplus-guacamole.dtb \
    qcom/sm8150-oneplus-guacamole-rear-main.dtb \
    qcom/sm8150-oneplus-guacamole-rear-tele.dtb \
    qcom/sm8150-oneplus-guacamole-rear-wide.dtb
make -C "$SOURCE" O="$OUTPUT" ARCH=arm64 LLVM=1 -j"${JOBS:-12}" \
    modules_install INSTALL_MOD_PATH="$STAGE/usr" INSTALL_MOD_STRIP=1
mkdir -p "$STAGE/boot/dtbs/qcom" "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150"
cp "$OUTPUT/arch/arm64/boot/Image.gz" "$STAGE/boot/vmlinuz"
cp "$OUTPUT/arch/arm64/boot/Image" "$ROOT/build/rear-camera/Image"
cp "$OUTPUT/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole"*.dtb "$STAGE/boot/dtbs/qcom/"
cp "$OUTPUT/include/config/kernel.release" "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/"
python3 - "$STAGE" <<'PY'
from pathlib import Path
import sys
for name in ['build','source']:
    path=Path(sys.argv[1])/'usr/lib/modules/7.0.0-sm8150-rear1'/name
    if path.is_symlink():path.unlink()
PY
python3 scripts/check_boot_envelope.py --image build/rear-camera/Image \
    --kernel-offset 0x8000 --dtb-offset 0x03000000 --ramdisk-offset 0x01000000
