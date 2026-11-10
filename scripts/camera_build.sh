#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Build/validation entrypoint for the 7.0.0-sm8150-rear1 rear-camera candidate.
# Every path is anchored at this script's own checkout.  The script never boots
# or flashes the phone, never runs sudo or pmbootstrap, and refuses another
# worktree's artifacts or another kernel release.
#
#   camera_build.sh [check|kernel|libcamera|bootimg|manifest|all|package]
#                   [--dry-run] [--jobs N]
#
# `package` only prints the privileged, user-run rootfs packaging command.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

readonly RELEASE='7.0.0-sm8150-rear1'
readonly NBD_PORT=10001
readonly DEFAULT_DTB_OFFSET='0x03000000'
readonly KERNEL_LOAD_OFFSET='0x00008000'
readonly RAMDISK_LOAD_OFFSET='0x01000000'
readonly RECIPE="$ROOT/device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
readonly -a DTB_PATTERNS=(
    'cci@ac4a000'
    'cci@ac4b000'
    'i2c-bus@1'
    'compatible = "sony,imx586"'
    'compatible = "sony,imx481"'
    'compatible = "samsung,s5k3m5"'
)

STATE="$ROOT/.work/rear-camera"
SOURCE="$STATE/linux-v7.0.0-sm8150"
CCI_SOURCE="$SOURCE/drivers/i2c/busses/i2c-qcom-cci.c"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/build/rear-camera}"
KERNEL_DIR="${KERNEL_DIR:-$OUTPUT_DIR/kernel-package}"
IMAGE="$OUTPUT_DIR/Image"
VMLINUZ="$KERNEL_DIR/boot/vmlinuz"
RELEASE_FILE="$KERNEL_DIR/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
BASE_DTB="${BASE_DTB:-$KERNEL_DIR/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb}"
WRAPPER="$OUTPUT_DIR/temporary-dtbo/custom_guacamole-abl-quarantine.dtb"
WRAPPER_MANIFEST="$OUTPUT_DIR/temporary-dtbo/manifest.json"
BOOTIMG="$OUTPUT_DIR/boot.img"
DELIVERY="$OUTPUT_DIR/DELIVERY.json"
PACKAGING_INPUTS="$OUTPUT_DIR/packaging-inputs.json"
CCI_MODULE="$KERNEL_DIR/usr/lib/modules/$RELEASE/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
EXPORT="$STATE/rootfs/export"
# INITRAMFS is required (never defaulted) for bootimg; check/manifest fall back
# to the export symlink so they can validate an already-packaged candidate.
INITRAMFS_WAS_SET=0
if [[ -n ${INITRAMFS:-} ]]; then
    INITRAMFS_WAS_SET=1
else
    INITRAMFS="$EXPORT/initramfs"
fi
ROOTFS_IMAGE="$STATE/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
LOCK="$OUTPUT_DIR/.camera_build.lock"
DTB_OFFSET="${DTB_OFFSET:-$DEFAULT_DTB_OFFSET}"

DRY_RUN=0
CHECK_MODE=strict
JOBS="${JOBS:-}"
RUN_TMPDIR=''
declare -A SHA_CACHE=()

# Set by packed_dtb_identity() when the boot image's DTB is recognised.
PACKED_DTB_PATH=''
PACKED_DTB_SHA=''
PACKED_DTB_KIND=''

# Counters for run_check().
CHK_PASS=0
CHK_FAIL=0
CHK_WARN=0
CHK_SKIP=0

usage() {
    cat <<EOF
Usage: scripts/camera_build.sh [SUBCOMMAND] [--dry-run] [--jobs N]

Build and validate the $RELEASE rear-camera candidate from this checkout.
Host-only: it never boots the phone, never runs sudo or pmbootstrap, and
never reads artifacts from another worktree.

Subcommands (default: check):
  check       Read-only validation.  Prints PASS/FAIL per check and exits
              non-zero on any FAIL: recipe/patch 0031, patched CCI source,
              the patched CCI source/module, Image/vmlinuz/DTB/boot.img/initramfs/
              rootfs image consistency, CCI DTB nodes, the DTB actually
              packed into boot.img, packaging-inputs.json and DELIVERY.json.
  kernel      Build Image, modules and DTBs with
              scripts/build_rear_camera_kernel.sh (never pmbootstrap).
  libcamera   Cross-build the private libcamera stack with
              scripts/build_rear_camera_libcamera.sh.
  bootimg     Pack build/rear-camera/boot.img with
              scripts/build_bootimg_rear_camera.sh.  Requires INITRAMFS
              (the initramfs exported by the privileged packaging step) and
              refuses to leave a stale boot.img in place.
  manifest    Write build/rear-camera/DELIVERY.json (release, worktree,
              created_at, git_head and {path,sha256,size} for boot_img,
              image, kernel_package, dtb, initramfs, rootfs_image and
              i2c_qcom_cci).  Records the module this build produced after
              proving patch 0031 is applied to the kernel source; it does not
              require one hardcoded binary hash.
  all         check -> kernel -> libcamera -> tests -> bootimg -> manifest.
              Needs INITRAMFS; the initial check tolerates artifacts that
              the following build phases recreate, but not a wrong checkout,
              a missing recipe, or a missing patch 0031.
  package     Print the privileged, user-run rootfs packaging command
              (scripts/rootfs_prepare_rear_camera.sh) and exit.  Never runs
              pmbootstrap, sudo or an image build.

Options:
  --dry-run   Print every command and file that would be written; execute
              nothing, write nothing.  For check this prints the check plan
              without reading or hashing artifacts.
  --jobs N    Export JOBS=N for the kernel and libcamera builds.
  -h, --help  Show this help.

Environment kept from the existing helpers (all must stay inside this
checkout): INITRAMFS, OUTPUT_DIR, KERNEL_DIR, BASE_DTB, DTB_MODE, DTB_OFFSET,
TEMP_DTB_DIR, JOBS.  Any override that resolves outside $ROOT is refused.

Exit status: 0 on success, 1 on refusal or a failed check.
EOF
}

die() {
    printf 'camera_build: %s\n' "$*" >&2
    exit 1
}

section() {
    printf '\n-- %s --\n' "$*"
}

info() {
    printf 'INFO  %s\n' "$*"
}

pass() {
    CHK_PASS=$((CHK_PASS + 1))
    printf 'PASS  %s\n' "$*"
}

fail() {
    CHK_FAIL=$((CHK_FAIL + 1))
    printf 'FAIL  %s\n' "$*"
}

warn() {
    CHK_WARN=$((CHK_WARN + 1))
    printf 'WARN  %s\n' "$*"
}

skip() {
    CHK_SKIP=$((CHK_SKIP + 1))
    printf 'SKIP  %s\n' "$*"
}

# Fatal in a strict check; a warning while `all` is about to rebuild artifacts.
soft_fail() {
    if [[ $CHECK_MODE == prebuild ]]; then
        warn "$*"
    else
        fail "$*"
    fi
}

relative() {
    printf '%s' "${1#"$ROOT"/}"
}

git_head() {
    git -C "$ROOT" rev-parse HEAD 2>/dev/null || printf 'unknown'
}

git_summary() {
    local head changes
    if ! command -v git >/dev/null 2>&1; then
        printf 'git: unavailable\n'
        return 0
    fi
    if ! head=$(git -C "$ROOT" rev-parse --short=12 HEAD 2>/dev/null); then
        printf 'git: not a repository\n'
        return 0
    fi
    changes=$(git -C "$ROOT" status --porcelain 2>/dev/null | wc -l)
    printf 'git head: %s (%s uncommitted paths; candidate work is uncommitted)\n' "$head" "$changes"
}

print_identity() {
    printf 'worktree: %s\n' "$ROOT"
    printf 'release:  %s\n' "$RELEASE"
    git_summary
}

# Create the scratch directory in the calling shell: a trap set inside a
# command substitution would delete the directory before it is used.
ensure_tmpdir() {
    if [[ -z $RUN_TMPDIR ]]; then
        RUN_TMPDIR=$(mktemp -d "${TMPDIR:-/tmp}/camera_build.XXXXXX")
        trap 'rm -rf "$RUN_TMPDIR"' EXIT
    fi
}

# Size of the file a path names, following symlinks (export/* are symlinks).
file_size() {
    stat -Lc %s -- "$1"
}

# Build phases replace artifacts; their hashes must be recomputed.
reset_sha_cache() {
    SHA_CACHE=()
}

sha_cached() {
    local path=$1 size
    if [[ -n ${SHA_CACHE[$path]:-} ]]; then
        printf '%s' "${SHA_CACHE[$path]}"
        return 0
    fi
    size=$(file_size "$path")
    if (( size > 200 * 1024 * 1024 )); then
        printf 'INFO  hashing %s (%s MiB)\n' "$(relative "$path")" "$((size / 1024 / 1024))" >&2
    fi
    SHA_CACHE[$path]=$(sha256sum -- "$path" | cut -d' ' -f1)
    printf '%s' "${SHA_CACHE[$path]}"
}

json_get() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

try:
    with open(sys.argv[1]) as handle:
        value = json.load(handle)
except (OSError, ValueError):
    raise SystemExit(0)
for part in sys.argv[2].split('.'):
    value = value.get(part) if isinstance(value, dict) else None
if value is None:
    value = ''
print(value)
PY
}

dtb_dump() {
    local file=$1
    if [[ -x $ROOT/local/bin/fdtdump ]]; then
        "$ROOT/local/bin/fdtdump" "$file" 2>/dev/null
        return
    fi
    if command -v dtc >/dev/null 2>&1; then
        dtc -I dtb -O dts -q "$file" 2>/dev/null
        return
    fi
    return 127
}

# Print the DTB_PATTERNS missing from a compiled DTB; return 2 if it cannot be read.
dtb_missing_patterns() {
    local file=$1 dump pattern
    if ! dump=$(dtb_dump "$file"); then
        return 2
    fi
    for pattern in "${DTB_PATTERNS[@]}"; do
        if ! grep -qF -- "$pattern" <<<"$dump"; then
            printf '%s\n' "$pattern"
        fi
    done
    return 0
}

# Copy the DTB embedded in an Android v2 boot image to $2.
bootimg_extract_dtb() {
    python3 - "$1" "$2" <<'PY'
import struct
import sys

source, destination = sys.argv[1], sys.argv[2]
with open(source, 'rb') as handle:
    header = handle.read(4096)
    if len(header) < 1660:
        raise SystemExit(f'{source}: shorter than an Android v2 boot header')
    if header[:8] != b'ANDROID!':
        raise SystemExit(f'{source}: not an Android boot image (magic {header[:8]!r})')
    kernel_size = struct.unpack_from('<I', header, 8)[0]
    ramdisk_size = struct.unpack_from('<I', header, 16)[0]
    second_size = struct.unpack_from('<I', header, 24)[0]
    page_size = struct.unpack_from('<I', header, 36)[0]
    header_version = struct.unpack_from('<I', header, 40)[0]
    if header_version < 2:
        raise SystemExit(f'{source}: header v{header_version} carries no DTB')
    recovery_dtbo_size = struct.unpack_from('<I', header, 1632)[0]
    dtb_size = struct.unpack_from('<I', header, 1648)[0]
    if not dtb_size:
        raise SystemExit(f'{source}: header dtb_size is zero')
    if page_size not in (2048, 4096, 8192, 16384):
        raise SystemExit(f'{source}: implausible page size {page_size}')

    def pages(size):
        return (size + page_size - 1) // page_size if size else 0

    offset = page_size * (1 + pages(kernel_size) + pages(ramdisk_size)
                          + pages(second_size) + pages(recovery_dtbo_size))
    handle.seek(offset)
    blob = handle.read(dtb_size)
    if len(blob) != dtb_size or blob[:4] != b'\xd0\x0d\xfe\xed':
        raise SystemExit(f'{source}: no flattened DTB at offset {offset}')
    with open(destination, 'wb') as out:
        out.write(blob)
PY
}

# Resolve which DTB the boot image packs.  Returns non-zero with a reason.
packed_dtb_identity() {
    local packed base_sha wrapper_sha manifest_base
    PACKED_DTB_PATH=''
    PACKED_DTB_SHA=''
    PACKED_DTB_KIND=''
    ensure_tmpdir
    packed=$(mktemp "$RUN_TMPDIR/packed.XXXXXX.dtb")
    if ! bootimg_extract_dtb "$BOOTIMG" "$packed"; then
        return 1
    fi
    PACKED_DTB_SHA=$(sha_cached "$packed")
    base_sha=''
    if [[ -f $BASE_DTB ]]; then
        base_sha=$(sha_cached "$BASE_DTB")
    fi
    if [[ -n $base_sha && $PACKED_DTB_SHA == "$base_sha" ]]; then
        PACKED_DTB_PATH="$BASE_DTB"
        PACKED_DTB_KIND='base DTB'
        return 0
    fi
    if [[ -f $WRAPPER && -f $WRAPPER_MANIFEST ]]; then
        wrapper_sha=$(sha_cached "$WRAPPER")
        if [[ $PACKED_DTB_SHA == "$wrapper_sha" ]]; then
            manifest_base=$(json_get "$WRAPPER_MANIFEST" inputs.base_dtb.sha256)
            if [[ -n $base_sha && $manifest_base == "$base_sha" ]]; then
                PACKED_DTB_PATH="$WRAPPER"
                PACKED_DTB_KIND='stock-DTBO quarantine wrapper of this base DTB'
                return 0
            fi
            printf 'the quarantine wrapper was built from base DTB %s, staged base DTB is %s\n' \
                "${manifest_base:-<unrecorded>}" "${base_sha:-<missing>}" >&2
            return 1
        fi
    fi
    printf 'packed DTB %s matches neither %s nor %s\n' \
        "$PACKED_DTB_SHA" "$(relative "$BASE_DTB")" "$(relative "$WRAPPER")" >&2
    return 1
}

image_matches_vmlinuz() {
    local image_sha unpacked_sha
    image_sha=$(sha_cached "$IMAGE")
    if ! gzip -t "$VMLINUZ" 2>/dev/null; then
        printf '%s is not gzip data\n' "$(relative "$VMLINUZ")" >&2
        return 1
    fi
    if ! unpacked_sha=$(gzip -dc "$VMLINUZ" | sha256sum | cut -d' ' -f1); then
        printf '%s cannot be decompressed\n' "$(relative "$VMLINUZ")" >&2
        return 1
    fi
    if [[ $unpacked_sha != "$image_sha" ]]; then
        printf 'vmlinuz decompresses to %s but %s is %s\n' \
            "$unpacked_sha" "$(relative "$IMAGE")" "$image_sha" >&2
        return 1
    fi
    return 0
}

manifest_artifact_path() {
    case "$1" in
        boot_img) printf '%s' "$BOOTIMG" ;;
        image) printf '%s' "$IMAGE" ;;
        kernel_package) printf '%s' "$VMLINUZ" ;;
        dtb) printf '%s' "$BASE_DTB" ;;
        initramfs) printf '%s' "$INITRAMFS" ;;
        rootfs_image) printf '%s' "$ROOTFS_IMAGE" ;;
        i2c_qcom_cci) printf '%s' "$CCI_MODULE" ;;
        *) return 1 ;;
    esac
}

run() {
    if (( DRY_RUN )); then
        printf 'DRY-RUN  %s\n' "$*"
        return 0
    fi
    "$@"
}

require_candidate_checkout() {
    [[ -f $RECIPE/APKBUILD ]] || die \
        "not the rear-camera candidate checkout: missing $(relative "$RECIPE/APKBUILD")"
    if [[ -f $RELEASE_FILE ]]; then
        local staged
        staged=$(cat "$RELEASE_FILE")
        [[ $staged == "$RELEASE" ]] || die \
            "staged kernel release is '$staged', expected '$RELEASE'; refusing to mix kernels"
    fi
}

reject_foreign_paths() {
    local name value resolved
    for name in OUTPUT_DIR KERNEL_DIR BASE_DTB TEMP_DTB_DIR INITRAMFS; do
        value="${!name:-}"
        [[ -n $value ]] || continue
        resolved=$(realpath -m -- "$value")
        case "$resolved/" in
            "${ROOT%/}"/*) ;;
            *) die "$name=$value resolves outside this checkout ($ROOT); refusing to build or read another tree's artifacts" ;;
        esac
    done
}

take_lock() {
    if (( DRY_RUN )); then
        return 0
    fi
    mkdir -p "$OUTPUT_DIR"
    exec 9>"$LOCK"
    flock -n 9 || die "another camera_build.sh run holds $(relative "$LOCK")"
}

compare_recorded_sha() {
    local label=$1 recorded=$2 path=$3 actual
    if [[ -z $recorded ]]; then
        soft_fail "$label: not recorded"
        return 0
    fi
    actual=$(sha_cached "$path")
    if [[ $recorded == "$actual" ]]; then
        pass "$label: $actual"
    else
        soft_fail "$label mismatch: recorded $recorded, actual $actual"
    fi
}

check_plan() {
    cat <<EOF
WOULD CHECK  candidate checkout: $(relative "$RECIPE")/APKBUILD and release '$RELEASE'
WOULD CHECK  patch 0031 in $(relative "$RECIPE")
WOULD CHECK  all-master init_completion before the child loop in $(relative "$CCI_SOURCE")
WOULD CHECK  $(relative "$CCI_MODULE") exists, is newer than the patched source, and matches
             the i2c_qcom_cci entry in $(relative "$DELIVERY") when that manifest exists
WOULD CHECK  existence and hashes of $(relative "$IMAGE"), $(relative "$VMLINUZ"), $(relative "$BASE_DTB"),
             $(relative "$INITRAMFS"), $(relative "$ROOTFS_IMAGE"), $(relative "$BOOTIMG")
WOULD CHECK  Image equals gunzip(vmlinuz); release '$RELEASE' in $(relative "$RELEASE_FILE")
WOULD CHECK  scripts/check_boot_envelope.py at DTB offset $DTB_OFFSET and scripts/check_rear_camera_package.py
WOULD CHECK  CCI1/CCI0 nodes and the imx586/imx481/s5k3m5 compatibles in the base DTB
WOULD CHECK  the DTB packed into $(relative "$BOOTIMG") is this base DTB or its quarantine wrapper
WOULD CHECK  $(relative "$PACKAGING_INPUTS") against the current initramfs, vmlinuz and rootfs image
WOULD CHECK  $(relative "$DELIVERY") hashes, sizes, release, worktree and git_head
EOF
}

# Prove the fix is present in source, not by comparing one build's binary hash.
# On success sets CCI_FIX_LINE/CCI_CHILD_LINE; on failure sets CCI_PROBLEM.
CCI_PROBLEM=''
CCI_FIX_LINE=''
CCI_CHILD_LINE=''
cci_source_problem() {
    local patch="$RECIPE/0031-i2c-qcom-cci-initialize-all-master-completions.patch"
    local fix_line child_line prefix_line
    CCI_FIX_LINE=''
    CCI_CHILD_LINE=''
    if [[ ! -f $patch ]]; then
        CCI_PROBLEM="patch 0031 missing: $(relative "$patch")"
        return 1
    fi
    if ! grep -qF 'init_completion(&cci->master[i].irq_complete);' "$patch"; then
        CCI_PROBLEM='patch 0031 does not contain the all-master init_completion loop'
        return 1
    fi
    if [[ ! -f $CCI_SOURCE ]]; then
        CCI_PROBLEM="patched kernel source missing: $(relative "$CCI_SOURCE") (run: scripts/camera_build.sh kernel)"
        return 1
    fi
    fix_line=$(grep -nF 'init_completion(&cci->master[i].irq_complete);' "$CCI_SOURCE" | head -n1 | cut -d: -f1 || true)
    child_line=$(grep -nF 'for_each_available_child_of_node(dev->of_node, child)' "$CCI_SOURCE" | head -n1 | cut -d: -f1 || true)
    # Anchor the pre-fix form so reinit_completion(&master->irq_complete) does not match.
    prefix_line=$(grep -n '^[[:space:]]*init_completion(&master->irq_complete);' "$CCI_SOURCE" | head -n1 | cut -d: -f1 || true)
    if [[ -z $fix_line ]]; then
        CCI_PROBLEM='i2c-qcom-cci.c lacks the all-master init_completion loop (patch 0031 not applied)'
        return 1
    fi
    if [[ -z $child_line || $fix_line -ge $child_line ]]; then
        CCI_PROBLEM="i2c-qcom-cci.c initializes completions after the child-node loop (line $fix_line vs ${child_line:-?})"
        return 1
    fi
    if [[ -n $prefix_line ]]; then
        CCI_PROBLEM="i2c-qcom-cci.c still contains the pre-fix init_completion(&master->irq_complete) at line $prefix_line"
        return 1
    fi
    CCI_FIX_LINE=$fix_line
    CCI_CHILD_LINE=$child_line
    return 0
}

run_check() {
    CHK_PASS=0
    CHK_FAIL=0
    CHK_WARN=0
    CHK_SKIP=0

    if (( DRY_RUN )); then
        printf '== camera_build check plan (%s, dry-run) ==\n' "$RELEASE"
        print_identity
        printf '\n'
        check_plan
        printf '\n== dry-run: nothing was read, hashed or written ==\n'
        return 0
    fi

    printf '== camera_build check (%s, mode %s) ==\n' "$RELEASE" "$CHECK_MODE"

    section 'checkout'
    pass "candidate recipe: $(relative "$RECIPE")/APKBUILD"
    local config="$RECIPE/config-postmarketos-qcom-sm8150.aarch64"
    if [[ ! -f $config ]]; then
        fail "recipe config missing: $(relative "$config")"
    elif grep -qx 'CONFIG_LOCALVERSION="-sm8150-rear1"' "$config"; then
        pass "recipe config sets CONFIG_LOCALVERSION=\"-sm8150-rear1\""
    else
        fail "recipe config does not set CONFIG_LOCALVERSION=\"-sm8150-rear1\""
    fi
    if [[ -f $RELEASE_FILE ]]; then
        local staged
        staged=$(cat "$RELEASE_FILE")
        if [[ $staged == "$RELEASE" ]]; then
            pass "staged kernel release: $staged"
        else
            fail "staged kernel release: '$staged' (expected '$RELEASE')"
        fi
    else
        skip "staged kernel release: $(relative "$RELEASE_FILE") missing (run: scripts/camera_build.sh kernel)"
    fi

    section 'source'
    local patch="$RECIPE/0031-i2c-qcom-cci-initialize-all-master-completions.patch"
    if [[ ! -f $patch ]]; then
        fail "patch 0031 missing: $(relative "$patch")"
    elif cci_source_problem; then
        pass "patched CCI source: all-master init_completion at line $CCI_FIX_LINE, before the child loop at line $CCI_CHILD_LINE"
    else
        soft_fail "$CCI_PROBLEM"
    fi

    section 'CCI module (this build)'
    if [[ ! -f $CCI_MODULE ]]; then
        soft_fail "CCI module missing: $(relative "$CCI_MODULE") (run: scripts/camera_build.sh kernel)"
    else
        local module_size module_sha recorded_sha
        module_size=$(file_size "$CCI_MODULE")
        module_sha=$(sha_cached "$CCI_MODULE")
        pass "CCI module: $module_sha ($module_size bytes)"
        recorded_sha=$(json_get "$DELIVERY" i2c_qcom_cci.sha256)
        if [[ -z $recorded_sha ]]; then
            soft_fail "DELIVERY.json has no i2c_qcom_cci.sha256 (run: scripts/camera_build.sh manifest after this build)"
        elif [[ $recorded_sha != "$module_sha" ]]; then
            soft_fail "DELIVERY.json records CCI module $recorded_sha, staged module is $module_sha; rerun manifest"
        else
            pass "DELIVERY.json records this build's CCI module"
        fi
        if [[ -f $CCI_SOURCE ]]; then
            if [[ $CCI_MODULE -nt $CCI_SOURCE ]]; then
                pass "CCI module is newer than the patched source"
            else
                soft_fail "CCI module is not newer than $(relative "$CCI_SOURCE"); rebuild before packaging"
            fi
        else
            skip "CCI module freshness: patched source missing"
        fi
    fi

    section 'artifacts'
    local label path label_path
    for label_path in "Image:$IMAGE" "vmlinuz:$VMLINUZ" "base DTB:$BASE_DTB" "initramfs:$INITRAMFS" \
                     "rootfs image:$ROOTFS_IMAGE" "boot.img:$BOOTIMG"; do
        label=${label_path%%:*}
        path=${label_path#*:}
        if [[ -f $path ]]; then
            pass "$label: $(relative "$path") ($(file_size "$path") bytes)"
        else
            soft_fail "$label missing: $(relative "$path")"
        fi
    done
    if [[ -f $IMAGE && -f $VMLINUZ ]]; then
        if image_matches_vmlinuz; then
            pass "Image equals gunzip(vmlinuz)"
        else
            soft_fail "Image and vmlinuz disagree; rerun scripts/camera_build.sh bootimg"
        fi
    else
        skip "Image/vmlinuz identity: one of them is missing"
    fi
    local export_image="$EXPORT/oneplus-guacamole.img"
    if [[ -e $export_image && -e $ROOTFS_IMAGE ]]; then
        if [[ $(readlink -f -- "$export_image") == "$(readlink -f -- "$ROOTFS_IMAGE")" ]]; then
            pass "export/oneplus-guacamole.img points at the packaged rootfs image"
        else
            soft_fail "export/oneplus-guacamole.img is $(readlink -f -- "$export_image"), expected $(realpath -m -- "$ROOTFS_IMAGE")"
        fi
    fi
    if [[ -f $IMAGE && -f $INITRAMFS ]]; then
        local envelope
        if [[ ! -f $ROOT/scripts/check_boot_envelope.py ]]; then
            soft_fail "helper missing: scripts/check_boot_envelope.py"
        elif envelope=$(python3 "$ROOT/scripts/check_boot_envelope.py" --image "$IMAGE" \
                --kernel-offset "$KERNEL_LOAD_OFFSET" --dtb-offset "$DTB_OFFSET" \
                --ramdisk-offset "$RAMDISK_LOAD_OFFSET" \
                --ramdisk-size "$(file_size "$INITRAMFS")" 2>&1); then
            pass "boot envelope at DTB offset $DTB_OFFSET: $(grep -m1 'DTB' <<<"$envelope")"
        else
            soft_fail "boot envelope check failed: $(tail -n1 <<<"$envelope")"
        fi
    else
        skip "boot envelope: Image or initramfs missing"
    fi

    section 'device tree'
    if [[ -f $BASE_DTB ]]; then
        local missing
        if missing=$(dtb_missing_patterns "$BASE_DTB"); then
            if [[ -z $missing ]]; then
                pass "base DTB has cci@ac4a000, cci@ac4b000, i2c-bus@1 and the imx586/imx481/s5k3m5 compatibles"
            else
                soft_fail "base DTB lacks: $(tr '\n' ' ' <<<"$missing")"
            fi
        else
            soft_fail "cannot dump $(relative "$BASE_DTB"); install dtc or local/bin/fdtdump"
        fi
    else
        skip "base DTB nodes: $(relative "$BASE_DTB") missing"
    fi
    if [[ -f $BOOTIMG ]]; then
        ensure_tmpdir
        if packed_dtb_identity 2>"$RUN_TMPDIR/packed_dtb.err"; then
            pass "boot.img packs the ${PACKED_DTB_KIND} (${PACKED_DTB_SHA})"
            if [[ $PACKED_DTB_PATH != "$BASE_DTB" ]]; then
                local packed_missing
                if packed_missing=$(dtb_missing_patterns "$PACKED_DTB_PATH"); then
                    if [[ -z $packed_missing ]]; then
                        pass "packed DTB has the CCI nodes and the three sensor compatibles"
                    else
                        soft_fail "packed DTB lacks: $(tr '\n' ' ' <<<"$packed_missing")"
                    fi
                else
                    soft_fail "cannot dump the packed DTB $(relative "$PACKED_DTB_PATH")"
                fi
            fi
        else
            soft_fail "boot.img DTB identity: $(tail -n1 "$RUN_TMPDIR/packed_dtb.err")"
        fi
    else
        skip "boot.img DTB identity: $(relative "$BOOTIMG") missing"
    fi

    if [[ $CHECK_MODE == prebuild ]]; then
        section 'packaging inputs'
        skip "prebuild: $(relative "$PACKAGING_INPUTS") is rewritten by the privileged packaging stage"
        section 'delivery manifest'
        skip "prebuild: $(relative "$DELIVERY") is rewritten by scripts/camera_build.sh manifest"
    else
        section 'packaging inputs'
        if [[ ! -f $PACKAGING_INPUTS ]]; then
            soft_fail "packaging inputs missing: $(relative "$PACKAGING_INPUTS") (run: scripts/camera_build.sh package)"
        else
            local recorded_release recorded_port recorded_rootfs
            recorded_release=$(json_get "$PACKAGING_INPUTS" release)
            recorded_port=$(json_get "$PACKAGING_INPUTS" nbd_port)
            recorded_rootfs=$(json_get "$PACKAGING_INPUTS" rootfs)
            if [[ $recorded_release == "$RELEASE" ]]; then
                pass "packaging inputs release: $recorded_release"
            else
                soft_fail "packaging inputs release: '$recorded_release' (expected '$RELEASE')"
            fi
            if [[ $recorded_port == "$NBD_PORT" ]]; then
                pass "packaging inputs netboot port: $recorded_port"
            else
                soft_fail "packaging inputs netboot port: '$recorded_port' (expected '$NBD_PORT')"
            fi
            if [[ -n $recorded_rootfs && $recorded_rootfs != "$ROOT"/* ]]; then
                info "packaging inputs record rootfs $recorded_rootfs; compared by hash only"
            fi
            compare_recorded_sha "packaging inputs initramfs" "$(json_get "$PACKAGING_INPUTS" initramfs_sha256)" "$INITRAMFS"
            compare_recorded_sha "packaging inputs vmlinuz" "$(json_get "$PACKAGING_INPUTS" vmlinuz_sha256)" "$VMLINUZ"
            if [[ -n $(json_get "$PACKAGING_INPUTS" rootfs_image_sha256) ]]; then
                compare_recorded_sha "packaging inputs rootfs image" \
                    "$(json_get "$PACKAGING_INPUTS" rootfs_image_sha256)" "$ROOTFS_IMAGE"
            else
                warn "packaging inputs do not record the rootfs image hash; rerun scripts/camera_build.sh package"
            fi
            if [[ -f $INITRAMFS && -f $VMLINUZ && -f $ROOT/scripts/check_rear_camera_package.py ]]; then
                local helper_output
                if helper_output=$(python3 "$ROOT/scripts/check_rear_camera_package.py" \
                        --initramfs "$INITRAMFS" --kernel-dir "$KERNEL_DIR" 2>&1); then
                    pass "candidate inputs accepted by check_rear_camera_package.py"
                else
                    soft_fail "check_rear_camera_package.py failed: $(tail -n1 <<<"$helper_output")"
                fi
            else
                skip "check_rear_camera_package.py: initramfs, vmlinuz or the helper is missing"
            fi
        fi

        section 'delivery manifest'
        if [[ ! -f $DELIVERY ]]; then
            soft_fail "DELIVERY.json missing: $(relative "$DELIVERY") (run: scripts/camera_build.sh manifest)"
        else
            local manifest_release manifest_worktree manifest_head
            if ! python3 -m json.tool "$DELIVERY" >/dev/null 2>&1; then
                fail "DELIVERY.json is not valid JSON: $(relative "$DELIVERY")"
            else
                manifest_release=$(json_get "$DELIVERY" release)
                manifest_worktree=$(json_get "$DELIVERY" worktree)
                manifest_head=$(json_get "$DELIVERY" git_head)
                if [[ $manifest_release == "$RELEASE" ]]; then
                    pass "DELIVERY.json release: $manifest_release"
                else
                    soft_fail "DELIVERY.json release: '$manifest_release' (expected '$RELEASE')"
                fi
                if [[ $manifest_worktree == "$ROOT" ]]; then
                    pass "DELIVERY.json worktree: $ROOT"
                else
                    warn "DELIVERY.json records worktree ${manifest_worktree:-<unset>}; this checkout is $ROOT (artifacts are verified by hash)"
                fi
                if [[ -n $manifest_head && $manifest_head != "$(git_head)" ]]; then
                    warn "DELIVERY.json git_head $manifest_head differs from current $(git_head); rebuild and rerun manifest if the source changed"
                fi
                local key entry_path entry_sha entry_size actual_sha actual_size
                for key in boot_img image kernel_package dtb initramfs rootfs_image i2c_qcom_cci; do
                    path=$(manifest_artifact_path "$key")
                    entry_sha=$(json_get "$DELIVERY" "$key.sha256")
                    entry_size=$(json_get "$DELIVERY" "$key.size")
                    entry_path=$(json_get "$DELIVERY" "$key.path")
                    if [[ -z $entry_sha || -z $entry_size ]]; then
                        soft_fail "DELIVERY.json $key entry has no sha256/size"
                        continue
                    fi
                    if [[ ! -f $path ]]; then
                        soft_fail "DELIVERY.json $key records a missing file: $path"
                        continue
                    fi
                    actual_sha=$(sha_cached "$path")
                    actual_size=$(file_size "$path")
                    if [[ $entry_sha == "$actual_sha" && $entry_size == "$actual_size" ]]; then
                        pass "DELIVERY.json $key: $actual_sha ($actual_size bytes)"
                    else
                        soft_fail "DELIVERY.json $key hash/size mismatch: recorded $entry_sha/$entry_size, actual $actual_sha/$actual_size"
                    fi
                    if [[ -n $entry_path && $entry_path != "$path" ]]; then
                        warn "DELIVERY.json $key path $entry_path differs from this checkout's $path; verified by hash"
                    fi
                done
            fi
        fi
    fi

    printf '\n== check summary: %d pass, %d warn, %d skip, %d fail ==\n' \
        "$CHK_PASS" "$CHK_WARN" "$CHK_SKIP" "$CHK_FAIL"
    if (( CHK_FAIL > 0 )); then
        return 1
    fi
    return 0
}

cmd_kernel() {
    local builder="$ROOT/scripts/build_rear_camera_kernel.sh"
    [[ -f $builder ]] || die "missing build helper: $(relative "$builder")"
    print_identity
    printf 'kernel source: %s\noutput: %s\nstaging: %s\n' "$SOURCE" "$STATE/output" "$KERNEL_DIR"
    take_lock
    if (( DRY_RUN )); then
        run env JOBS="${JOBS:-12}" bash "$builder"
        return 0
    fi
    env JOBS="${JOBS:-12}" bash "$builder"
    reset_sha_cache
    local staged module_sha
    staged=$(cat "$RELEASE_FILE")
    [[ $staged == "$RELEASE" ]] || die "kernel build produced release '$staged', expected '$RELEASE'"
    if ! cci_source_problem; then
        die "kernel build finished but the source fix is not present: $CCI_PROBLEM"
    fi
    [[ -f $CCI_MODULE ]] || die "kernel build did not produce $CCI_MODULE"
    [[ $CCI_MODULE -nt $CCI_SOURCE ]] || die "$CCI_MODULE is not newer than the patched source; rebuild"
    module_sha=$(sha_cached "$CCI_MODULE")
    printf 'built release: %s\nCCI module: %s (%s bytes, identity recorded by manifest)\n' \
        "$staged" "$module_sha" "$(file_size "$CCI_MODULE")"
}

cmd_libcamera() {
    local builder="$ROOT/scripts/build_rear_camera_libcamera.sh"
    [[ -f $builder ]] || die "missing build helper: $(relative "$builder")"
    print_identity
    printf 'staging: %s\n' "$OUTPUT_DIR/libcamera-stage"
    take_lock
    if (( DRY_RUN )); then
        run env JOBS="${JOBS:-8}" bash "$builder"
        return 0
    fi
    env JOBS="${JOBS:-8}" bash "$builder"
    reset_sha_cache
    local cam="$OUTPUT_DIR/libcamera-stage/opt/nura-rear-camera/bin/cam"
    [[ -f $cam ]] || die "libcamera build did not stage $(relative "$cam")"
    printf 'staged cam: %s (%s bytes, sha256 %s)\n' \
        "$(relative "$cam")" "$(file_size "$cam")" "$(sha_cached "$cam")"
}

cmd_bootimg() {
    local builder="$ROOT/scripts/build_bootimg_rear_camera.sh"
    [[ -f $builder ]] || die "missing build helper: $(relative "$builder")"
    (( INITRAMFS_WAS_SET )) || die \
        "INITRAMFS is not set; export the packaged initramfs first (see: scripts/camera_build.sh package)"
    [[ -f $INITRAMFS ]] || die "initramfs not found: $INITRAMFS"
    [[ -f $VMLINUZ ]] || die "staged kernel missing: $(relative "$VMLINUZ") (run: scripts/camera_build.sh kernel)"
    local staged
    staged=$(cat "$RELEASE_FILE" 2>/dev/null || true)
    [[ $staged == "$RELEASE" ]] || die \
        "staged kernel release is '${staged:-<missing>}', expected '$RELEASE'; refusing to mix kernels"
    print_identity
    printf 'Image: %s\nvmlinuz: %s\nbase DTB: %s\ninitramfs: %s\nboot.img: %s\n' \
        "$(relative "$IMAGE")" "$(relative "$VMLINUZ")" "$(relative "$BASE_DTB")" "$INITRAMFS" "$(relative "$BOOTIMG")"
    if (( DRY_RUN )); then
        run env INITRAMFS="$INITRAMFS" bash "$builder"
        printf 'DRY-RUN  would then require a changed boot.img hash and inputs older than boot.img\n'
        return 0
    fi
    take_lock
    local before_sha='' before_mtime=0 after_sha
    if [[ -f $BOOTIMG ]]; then
        before_sha=$(sha_cached "$BOOTIMG")
        before_mtime=$(stat -c %Y "$BOOTIMG")
    fi
    env INITRAMFS="$INITRAMFS" bash "$builder"
    reset_sha_cache
    [[ -f $BOOTIMG ]] || die "packer did not produce $(relative "$BOOTIMG")"
    after_sha=$(sha256sum -- "$BOOTIMG" | cut -d' ' -f1)
    if [[ $after_sha == "$before_sha" ]]; then
        die "boot.img hash is unchanged ($after_sha); the packer did not rewrite it, refusing to reuse a stale image"
    fi
    local input
    for input in "$IMAGE" "$VMLINUZ" "$INITRAMFS" "$BASE_DTB" "$WRAPPER" "$WRAPPER_MANIFEST"; do
        [[ -e $input ]] || continue
        if [[ $input -nt $BOOTIMG ]]; then
            die "$(relative "$input") is newer than $(relative "$BOOTIMG"); the packed image is stale"
        fi
    done
    ensure_tmpdir
    if ! packed_dtb_identity 2>"$RUN_TMPDIR/packed_dtb.err"; then
        die "packed boot.img DTB cannot be traced to this kernel package: $(tail -n1 "$RUN_TMPDIR/packed_dtb.err")"
    fi
    printf '\nboot.img: %s (%s bytes, sha256 %s)\n' \
        "$(relative "$BOOTIMG")" "$(file_size "$BOOTIMG")" "$after_sha"
    printf 'packed DTB: %s (%s)\n' "$PACKED_DTB_KIND" "$PACKED_DTB_SHA"
    if [[ $before_mtime -ne 0 ]]; then
        printf 'previous boot.img sha256: %s\n' "$before_sha"
    fi
}

cmd_manifest() {
    print_identity
    if (( DRY_RUN )); then
        printf 'DRY-RUN  would require: release %s, patch 0031 applied before the child loop,\n' "$RELEASE"
        printf 'DRY-RUN  a CCI module newer than the patched source, Image == gunzip(vmlinuz),\n'
        printf 'DRY-RUN  boot.img DTB == base DTB or its quarantine wrapper, and every input older than boot.img\n'
        printf 'DRY-RUN  would record the CCI module hash this build produced in %s\n' "$(relative "$DELIVERY")"
        return 0
    fi
    take_lock
    local missing=() path
    for path in "$IMAGE" "$VMLINUZ" "$BASE_DTB" "$INITRAMFS" "$ROOTFS_IMAGE" "$BOOTIMG" "$CCI_MODULE"; do
        if [[ ! -f $path ]]; then
            missing+=("$(relative "$path")")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        die "refusing to write DELIVERY.json; missing: ${missing[*]}"
    fi
    local staged
    staged=$(cat "$RELEASE_FILE" 2>/dev/null || true)
    [[ $staged == "$RELEASE" ]] || die "staged kernel release is '${staged:-<missing>}', expected '$RELEASE'"
    local cci_sha cci_size
    if ! cci_source_problem; then
        die "refusing to write DELIVERY.json: $CCI_PROBLEM"
    fi
    cci_sha=$(sha_cached "$CCI_MODULE")
    cci_size=$(file_size "$CCI_MODULE")
    if [[ $cci_size -le 0 ]]; then
        die "refusing to write DELIVERY.json: CCI module $CCI_MODULE is empty"
    fi
    if [[ $CCI_MODULE -nt $CCI_SOURCE ]]; then
        pass "CCI module is newer than the patched source"
    else
        die "refusing to write DELIVERY.json: $(relative "$CCI_MODULE") is not newer than $(relative "$CCI_SOURCE"); rebuild"
    fi
    local reason
    if ! reason=$(image_matches_vmlinuz 2>&1); then
        die "refusing to write DELIVERY.json: $(tail -n1 <<<"$reason")"
    fi
    ensure_tmpdir
    if ! packed_dtb_identity 2>"$RUN_TMPDIR/packed_dtb.err"; then
        die "refusing to write DELIVERY.json: $(tail -n1 "$RUN_TMPDIR/packed_dtb.err")"
    fi
    local input
    for input in "$VMLINUZ" "$INITRAMFS" "$BASE_DTB" "$WRAPPER" "$WRAPPER_MANIFEST" "$IMAGE"; do
        [[ -e $input ]] || continue
        if [[ $input -nt $BOOTIMG ]]; then
            die "refusing to write DELIVERY.json: $(relative "$input") is newer than $(relative "$BOOTIMG"); repack the boot image"
        fi
    done
    local created head
    created=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    head=$(git_head)
    mkdir -p "$OUTPUT_DIR"
    python3 - "$DELIVERY" "$RELEASE" "$ROOT" "$created" "$head" \
        boot_img "$BOOTIMG" image "$IMAGE" kernel_package "$VMLINUZ" dtb "$BASE_DTB" \
        initramfs "$INITRAMFS" rootfs_image "$ROOTFS_IMAGE" i2c_qcom_cci "$CCI_MODULE" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

destination = Path(sys.argv[1])
release, worktree, created, head = sys.argv[2:6]
document = {
    'release': release,
    'worktree': worktree,
    'created_at': created,
    'git_head': head,
}
entries = sys.argv[6:]
if len(entries) % 2:
    raise SystemExit('internal error: manifest entries must be key/path pairs')
for index in range(0, len(entries), 2):
    key, path = entries[index], Path(entries[index + 1])
    with path.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    document[key] = {'path': str(path), 'sha256': digest, 'size': path.stat().st_size}
temporary = destination.with_name(destination.name + '.tmp')
temporary.write_text(json.dumps(document, indent=2) + '\n')
os.replace(temporary, destination)
PY
    printf '\nDELIVERY manifest: %s\n' "$DELIVERY"
    printf 'release: %s\nworktree: %s\ngit_head: %s\ncreated_at: %s\n' "$RELEASE" "$ROOT" "$head" "$created"
    local key entry_sha entry_size
    for key in boot_img image kernel_package dtb initramfs rootfs_image i2c_qcom_cci; do
        entry_sha=$(json_get "$DELIVERY" "$key.sha256")
        entry_size=$(json_get "$DELIVERY" "$key.size")
        printf '%-15s %s %s\n' "$key:" "$entry_sha" "$entry_size"
    done
}

cmd_package() {
    printf 'bash "%s"\n' "$ROOT/scripts/rootfs_prepare_rear_camera.sh"
    cat <<'EOF'

This command is privileged and user-run; camera_build.sh does not execute it.
It builds/installs packages through pmbootstrap (sudo, mounts, chroots),
rebuilds the candidate rootfs image + initramfs, and rewrites
build/rear-camera/packaging-inputs.json.

After it succeeds run scripts/camera_serve.sh, which refreshes the netboot
cache from that new rootfs image and verifies the served image before READY.
Then scripts/camera_boot.sh. Without the refresh the port-10001 phone boots
the stale pre-fix image.
EOF
}

cmd_all() {
    print_identity
    printf 'phases: check -> kernel -> libcamera -> tests -> bootimg -> manifest\n'
    if (( DRY_RUN )); then
        printf 'DRY-RUN  check (prebuild)\n'
        cmd_kernel
        cmd_libcamera
        printf 'DRY-RUN  python3 -m unittest discover -s %s -p test_*.py\n' "$ROOT/tests"
        cmd_bootimg
        cmd_manifest
        return 0
    fi
    section 'phase 1/6: check'
    CHECK_MODE=prebuild
    if ! run_check; then
        die "source prerequisites failed; nothing was built"
    fi
    CHECK_MODE=strict
    section 'phase 2/6: kernel'
    cmd_kernel
    section 'phase 3/6: libcamera'
    cmd_libcamera
    section 'phase 4/6: tests'
    run_tests
    section 'phase 5/6: bootimg'
    cmd_bootimg
    section 'phase 6/6: manifest'
    cmd_manifest
    printf '\n== camera_build all: complete for %s ==\n' "$RELEASE"
}

run_tests() {
    [[ -d $ROOT/tests ]] || die "tests directory missing: $(relative "$ROOT/tests")"
    ( cd "$ROOT" && python3 -m unittest discover -s tests -p 'test_*.py' )
}

main() {
    local subcommand=''
    while (( $# )); do
        case "$1" in
            check | kernel | libcamera | bootimg | manifest | all | package)
                [[ -z $subcommand ]] || die "only one subcommand may be given ('$subcommand' and '$1')"
                subcommand=$1
                ;;
            --dry-run)
                DRY_RUN=1
                ;;
            --jobs)
                [[ $# -ge 2 ]] || die '--jobs requires a value'
                JOBS=$2
                shift
                ;;
            --jobs=*)
                JOBS=${1#*=}
                ;;
            -h | --help)
                usage
                exit 0
                ;;
            *)
                printf 'camera_build: unknown argument: %s\n\n' "$1" >&2
                usage >&2
                exit 1
                ;;
        esac
        shift
    done
    subcommand=${subcommand:-check}
    if [[ -n $JOBS ]]; then
        [[ $JOBS =~ ^[1-9][0-9]*$ ]] || die "--jobs needs a positive integer, got '$JOBS'"
        export JOBS
    fi
    require_candidate_checkout
    reject_foreign_paths

    case "$subcommand" in
        check)
            local status=0
            run_check || status=$?
            exit "$status"
            ;;
        kernel) cmd_kernel ;;
        libcamera) cmd_libcamera ;;
        bootimg) cmd_bootimg ;;
        manifest) cmd_manifest ;;
        all) cmd_all ;;
        package) cmd_package ;;
    esac
}

main "$@"
