#!/usr/bin/env bash
# Rootless kernel/DT/modules build only. Never invokes pmbootstrap or a phone.
#
# The historical rear-camera defaults are unchanged.  A later candidate (rc3)
# passes NURA_KERNEL_RELEASE, NURA_RECIPE_DIR, NURA_KERNEL_STATE,
# NURA_KERNEL_OUTPUT, NURA_KERNEL_STAGE and NURA_KERNEL_IMAGE so its kernel is
# built in its own state/output and the proven baseline tree is never written.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RECIPE="${NURA_RECIPE_DIR:-$ROOT/device/testing/rear-camera/linux-postmarketos-qcom-sm8150}"
STATE="${NURA_KERNEL_STATE:-$ROOT/.work/rear-camera}"
OUTPUT="${NURA_KERNEL_OUTPUT:-$STATE/output}"
STAGE="${NURA_KERNEL_STAGE:-$ROOT/build/rear-camera/kernel-package}"
IMAGE_OUT="${NURA_KERNEL_IMAGE:-$ROOT/build/rear-camera/Image}"
RELEASE="${NURA_KERNEL_RELEASE:-7.0.0-sm8150-rear1}"
SOURCE="${NURA_KERNEL_SOURCE:-}"
# Optional kernel sets (flows/rc4/optional/<name>): extra patches, and
# optionally firmware, applied only on request.  Empty means the plain recipe.
OPTIONAL_DIR="${NURA_KERNEL_OPTIONAL_DIR:-}"
OPTIONAL="${NURA_KERNEL_OPTIONAL:-}"

for path in "$RECIPE/APKBUILD" "$STATE" "$OUTPUT" "$STAGE"; do
    [[ "$(realpath -m -- "$path")" == "$ROOT"/* ]] ||
        { echo "Refusing a path outside this checkout: $path" >&2; exit 1; }
done
[[ "$(realpath -m -- "$IMAGE_OUT")" == "$ROOT"/* ]] ||
    { echo "Refusing an image path outside this checkout: $IMAGE_OUT" >&2; exit 1; }
[[ "$RELEASE" =~ ^[0-9A-Za-z._+-]+$ ]] ||
    { echo "Invalid kernel release name: $RELEASE" >&2; exit 1; }

export KBUILD_BUILD_VERSION=2
export KBUILD_BUILD_TIMESTAMP='2026-11-10 00:00:00 UTC'
export KBUILD_BUILD_USER=nura
export KBUILD_BUILD_HOST=rear-camera
export PATH="$ROOT/local/bin:$PATH"
export LD_LIBRARY_PATH="$ROOT/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
mkdir -p "$STATE" "$OUTPUT" "$STAGE" "$(dirname -- "$IMAGE_OUT")"

# Resolve every source ingredient from the recipe's sha512sums.  The kernel
# tarball is looked up in the isolated state first, then beside the recipe, then
# in the read-only rear-camera baseline (copied in, never modified).
TARBALL=''
while read -r expected name; do
    [[ -n "$expected" ]] || continue
    path="$RECIPE/$name"
    if [[ "$name" == linux-*.tar.* ]]; then
        if [[ -n "${NURA_SOURCE_TARBALL:-}" ]]; then
            path="$NURA_SOURCE_TARBALL"
        else
            for candidate in "$STATE/$name" "$RECIPE/$name" "$ROOT/local/sources/$name" \
                             "$ROOT/.work/rear-camera/$name"; do
                if [[ -f "$candidate" ]]; then
                    path="$candidate"
                    break
                fi
            done
            if [[ "$path" != "$STATE/$name" && -f "$path" ]]; then
                cp --reflink=auto -- "$path" "$STATE/$name" ||
                    { echo "Cannot stage the kernel source archive into $STATE" >&2; exit 1; }
                path="$STATE/$name"
            fi
        fi
        TARBALL="$name"
    fi
    [[ -f "$path" ]] || { echo "Missing input: $path" >&2; exit 1; }
    [[ "$(sha512sum "$path" | cut -d' ' -f1)" == "$expected" ]] ||
        { echo "Bad input: $path" >&2; exit 1; }
done < <(sh -c '. "$1"; printf "%s\n" "$sha512sums"' sh "$RECIPE/APKBUILD")
[[ -n "$TARBALL" ]] ||
    { echo "Recipe has no linux-*.tar.* source: $RECIPE/APKBUILD" >&2; exit 1; }
[[ -n "$SOURCE" ]] || SOURCE="$STATE/${TARBALL%.tar.*}"
[[ "$(realpath -m -- "$SOURCE")" == "$STATE"/* ]] ||
    { echo "Kernel source must stay inside $STATE: $SOURCE" >&2; exit 1; }

# Verify every requested optional set against its SHA512SUMS before use.
OPTIONAL_SETS=()
for set_name in $OPTIONAL; do
    [[ "$set_name" =~ ^[a-z0-9][a-z0-9-]*$ ]] ||
        { echo "Invalid optional set name: $set_name" >&2; exit 1; }
    [[ -n "$OPTIONAL_DIR" ]] ||
        { echo "NURA_KERNEL_OPTIONAL needs NURA_KERNEL_OPTIONAL_DIR" >&2; exit 1; }
    set_dir="$OPTIONAL_DIR/$set_name"
    [[ -f "$set_dir/SHA512SUMS" && -f "$set_dir/set.conf" ]] ||
        { echo "Unknown optional set: $set_name ($set_dir)" >&2; exit 1; }
    (cd "$set_dir" && sha512sum -c --quiet SHA512SUMS) ||
        { echo "Optional set $set_name does not match its SHA512SUMS" >&2; exit 1; }
    OPTIONAL_SETS+=("$set_name")
done

identity="$(sha256sum "$RECIPE/APKBUILD" | cut -d' ' -f1)"
if (( ${#OPTIONAL_SETS[@]} )); then
    # A source tree with optional patches must never be reused for a plain
    # build (or another set), and the other way round.
    identity="$identity+$(for set_name in "${OPTIONAL_SETS[@]}"; do
        printf '%s %s\n' "$set_name" "$(sha256sum "$OPTIONAL_DIR/$set_name/SHA512SUMS" | cut -d' ' -f1)"
    done | sha256sum | cut -d' ' -f1)"
fi
if [[ ! -d "$SOURCE" ]]; then
    tar xzf "$STATE/$TARBALL" -C "$STATE"
    for patch in "$RECIPE"/0*.patch; do
        flags=()
        [[ "${patch##*/}" < 0028 ]] || flags=(--fuzz=0)
        patch -d "$SOURCE" -p1 --forward --no-backup-if-mismatch "${flags[@]}" -i "$patch"
    done
    for set_name in "${OPTIONAL_SETS[@]}"; do
        for patch in "$OPTIONAL_DIR/$set_name"/patches/*.patch; do
            patch -d "$SOURCE" -p1 --forward --no-backup-if-mismatch --fuzz=0 -i "$patch"
        done
    done
    printf '%s\n' "$identity" > "$SOURCE/.rear-source.sha256"
fi
[[ "$(cat "$SOURCE/.rear-source.sha256" 2>/dev/null || true)" == "$identity" ]] ||
    { echo "Source recipe or optional sets changed: remove $SOURCE (a fresh tree is" \
           "extracted and patched on the next run) before rebuilding." >&2; exit 1; }

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
cp "$OUTPUT/arch/arm64/boot/Image" "$IMAGE_OUT"
cp "$OUTPUT/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole"*.dtb "$STAGE/boot/dtbs/qcom/"
cp "$OUTPUT/include/config/kernel.release" "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/"

# Optional sets: the marker and any firmware go into the staged package and into
# $OUTPUT/nura-optional, which the kernel APKBUILD installs when it exists
# (pmbootstrap --envkernel packages a copy of $OUTPUT).  Clear old state first
# so a plain build never carries a previous set's files.
rm -rf -- "$OUTPUT/nura-optional" "$STAGE/usr/lib/firmware" \
    "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/optional-sets"
if (( ${#OPTIONAL_SETS[@]} )); then
    mkdir -p "$OUTPUT/nura-optional"
    for set_name in "${OPTIONAL_SETS[@]}"; do
        set_dir="$OPTIONAL_DIR/$set_name"
        conf() { sed -n "s/^$1=//p" "$set_dir/set.conf" | head -n1; }
        printf '%s %s %s\n' "$set_name" "$(conf status)" \
            "$(sha256sum "$set_dir/SHA512SUMS" | cut -d' ' -f1)" >> "$OUTPUT/nura-optional/optional-sets"
        fw="$(conf firmware)"
        if [[ -n "$fw" ]]; then
            dest="$(conf firmware_dest)"
            [[ -f "$set_dir/$fw" && "$dest" =~ ^[A-Za-z0-9._/-]+$ && "$dest" != *..* ]] ||
                { echo "Optional set $set_name: bad firmware entry ($fw -> $dest)" >&2; exit 1; }
            fw_tmp="$(mktemp -d)"
            tar -xzf "$set_dir/$fw" -C "$fw_tmp" --no-same-owner
            mkdir -p "$OUTPUT/nura-optional/firmware/$dest" "$STAGE/usr/lib/firmware/$dest"
            find "$fw_tmp" -type f -exec install -m644 -t "$OUTPUT/nura-optional/firmware/$dest" {} +
            find "$fw_tmp" -type f -exec install -m644 -t "$STAGE/usr/lib/firmware/$dest" {} +
            rm -rf -- "$fw_tmp"
        fi
        echo "optional set: $set_name ($(conf status)) included"
    done
    install -m644 "$OUTPUT/nura-optional/optional-sets" \
        "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/optional-sets"
fi
python3 - "$STAGE" "$RELEASE" <<'PY'
from pathlib import Path
import sys
for name in ['build', 'source']:
    path = Path(sys.argv[1]) / 'usr/lib/modules' / sys.argv[2] / name
    if path.is_symlink():
        path.unlink()
PY
staged="$(cat "$STAGE/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release")"
[[ "$staged" == "$RELEASE" ]] ||
    { echo "Built release '$staged', expected '$RELEASE'; refusing to stage the wrong kernel." >&2; exit 1; }
python3 scripts/check_boot_envelope.py --image "$IMAGE_OUT" \
    --kernel-offset 0x8000 --dtb-offset 0x03000000 --ramdisk-offset 0x01000000
