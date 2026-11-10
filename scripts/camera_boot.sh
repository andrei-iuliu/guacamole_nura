#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# camera_boot.sh - prove which rear-camera candidate will be loaded, then hand
# that one boot image to the phone once.
#
# There is deliberately no flash path here.  The only fastboot verbs this
# script can run are devices/getvar/boot/reboot, and every invocation is
# guarded.  It fails closed unless the boot image, DTB, initramfs, kernel
# release, pinned rootfs image, served NBD image and device identity all belong
# to the 7.0.0-sm8150-rear1 candidate in *this* checkout.
#
# Companions from the same checkout:
#   scripts/camera_build.sh   writes build/rear-camera/DELIVERY.json
#   scripts/camera_serve.sh   serves the verified rootfs image on port 10001
#   scripts/camera_verify.sh  proves the fixed CCI module is running afterwards
#
# References: docs/REAR_CAMERA_7_0_BRINGUP.md (temporary boot/rollback),
# docs/TEMPORARY_BOOT.md (no DTBO write), scripts/fastboot_boot.sh (the proven
# OnePlus ABL USB-reset recovery), CLAUDE.md (never flash the boot partition).
set -Eeuo pipefail

readonly RELEASE='7.0.0-sm8150-rear1'
readonly NBD_PORT=10001
# Expected CCI identity is read from DELIVERY.json for this build.  The env
# overrides exist only as a host-testing seam and are never a built-in hash.
# Known non-candidate images: the 6.17 baseline and the working 7.0 baseline.
readonly -a BASELINE_BOOT_SHA256=(
    a449a4dd8d8ed6bdc76e4417c66e5633d273afc58548ef963bdcc84c190c8860
    6177a2eae478699b2f1e74c04945565df3ffc2c9a3312c20d4bebf06ad2ce4cb
)

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
ROOT_REAL="$(readlink -m -- "$ROOT")"
SERVED_RECORD="${NURA_STATE_DIR:-$ROOT/.work/delivery}/served-image.json"

MANIFEST="$ROOT/build/rear-camera/DELIVERY.json"
PACKAGING_INPUTS="$ROOT/build/rear-camera/packaging-inputs.json"
BOOT_IMG="$ROOT/build/rear-camera/boot.img"
KERNEL_DIR="$ROOT/build/rear-camera/kernel-package"
KERNEL_RELEASE_FILE="$KERNEL_DIR/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release"
BASE_DTB="$KERNEL_DIR/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb"
WRAPPER_DTB="$ROOT/build/rear-camera/temporary-dtbo/custom_guacamole-abl-quarantine.dtb"
WRAPPER_MANIFEST="$ROOT/build/rear-camera/temporary-dtbo/manifest.json"
KERNEL_IMAGE="$ROOT/build/rear-camera/Image"
VMLINUZ="$KERNEL_DIR/boot/vmlinuz"
CCI_MODULE="$KERNEL_DIR/usr/lib/modules/$RELEASE/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
SERVED_IMAGE="$ROOT/.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img"
SOURCE_IMAGE="$ROOT/.work/rear-camera/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
EXPORT_INITRAMFS="$ROOT/.work/rear-camera/rootfs/export/initramfs"
LOG_DIR="$ROOT/build/rear-camera/logs"
LAST_BOOT="$ROOT/build/rear-camera/last-boot.json"

SUBCOMMAND=boot
DRY_RUN=0
ASSUME_YES=0
SERIAL=''
SERVER_CHECK=1
WAIT_TIMEOUT=90
RETRIES=0
ACCEPT_CURRENT=0
BOOT_ATTEMPT_TIMEOUT="${CAMERA_BOOT_ATTEMPT_SECONDS:-120}"
EXPECTED_CCI_SHA256="${CAMERA_BOOT_CCI_SHA256:-}"
EXPECTED_CCI_SIZE="${CAMERA_BOOT_CCI_SIZE:-}"

ADB=''
FASTBOOT=''
SSH_HINT=''

declare -a PROBLEMS=()
declare -A MAN=()
MAN_LOADED=0
ROOTFS_PIN_SHA=''
ROOTFS_PIN_SIZE=''
INITRAMFS_PATH=''
CCI_PIN_SHA=''
CCI_PIN_SIZE=''
PACKED_DTB=''
PACKED_DTB_LABEL=''
BOOT_SHA=''
BOOT_SIZE=''
DTB_OFFSET=''
INITRAMFS_OFFSET=''
SERVED_SHA=''
SERVED_SIZE=''
TARGET_STATE=''
TARGET_SERIAL=''
IDENTITY_VERDICT=''
IDENTITY_PRODUCT='unknown'
IDENTITY_SLOT=''
IDENTITY_EXTRA=''

# --- output helpers -------------------------------------------------------
say()  { printf '%s\n' "$*"; }
pass_check() { printf 'PASS  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*" >&2; }
fail_check() { printf 'FAIL  %s\n' "$*" >&2; PROBLEMS+=("$*"); }
die()  { local code=$1; shift; printf 'ERROR: %s\n' "$*" >&2; exit "$code"; }
state() { printf '      %s\n' "$*" >&2; }

usage() {
    cat <<EOF
Usage: $(basename "$0") [SUBCOMMAND] [OPTIONS]

Proof-of-identity launcher for the Nura rear-camera candidate in this
checkout.  It temporarily boots build/rear-camera/boot.img only; it never
flashes, erases, formats or writes a partition, DTBO or vbmeta.

Subcommands:
  boot            Temporary boot of the candidate (default).
  bootloader      Reboot the phone into the bootloader (adb or fastboot).
  android         Reboot the phone into its installed Android.
  status          Print devices, port $NBD_PORT and the pinned artifact
                  identity; exit 3 when a check fails.

Options:
  --dry-run            Run every read-only check, query the device lists, and
                       print the exact adb/fastboot commands without running
                       them.  No log, last-boot.json or device change.
  --yes, -y            Continue when the device identity is unavailable or
                       unexpected.  Never overrides hashes, releases or paths.
  --serial SERIAL      Use this device when several are attached.
  --no-server-check    Skip only the port-$NBD_PORT listener check; the served
                       image identity and manifest CCI module are still checked.
  --timeout SECONDS    Seconds to wait for fastboot after a reboot (default 90).
  --retries N          Extra boot attempts after a stall or Load Error, each
                       preceded by the scripts/fastboot_boot.sh USB reset and
                       'fastboot reboot bootloader' (default 0, maximum 3).
  --accept-current     Boot the current on-disk artifacts when DELIVERY.json is
                       missing or unreadable (warning; nothing is pinned from
                       it).  A present manifest whose hashes do not match the
                       artifacts is always refused: re-run
                       camera_build.sh manifest instead.
  --help, -h           This text.

Identity that is verified before any device command:
  * ROOT comes from BASH_SOURCE and must be a rear-camera candidate checkout,
    never the nura-sm8150-7-0 baseline or the 6.17 tree;
  * DELIVERY.json release is $RELEASE and its worktree is this checkout;
  * boot.img sha256/size equal the manifest pin and are not a baseline image;
  * the manifest DTB equals the kernel-package DTB and is embedded in boot.img;
  * the initramfs is the pinned file and is embedded in boot.img;
  * kernel.release is $RELEASE and vmlinuz decompresses to the pinned Image;
  * the manifest CCI module is the i2c_qcom_cci sha256/size recorded in
    DELIVERY.json for this build;
  * pre-boot, images_netboot/oneplus-guacamole.img equals the pinned rootfs
    image and contains the manifest CCI module bytes (port $NBD_PORT is only
    warned about pre-boot because camera_serve.sh starts nbd-server after the
    USB gadget appears); post-boot status uses the pre-exposure record and does
    not rehash the phone-mutable image;
  * Android reports ro.product.device=guacamole (or GM1911/OnePlus 7 Pro), or
    fastboot reports product=guacamole, and exactly one device is attached.

Exit codes:
  0 success   2 usage   3 identity/served-image check failed
  4 device or identity problem   5 boot attempt failed

Testing seam: CAMERA_BOOT_CCI_SHA256 (and CAMERA_BOOT_CCI_SIZE) override the
CCI identity only when DELIVERY.json is absent; with a manifest they are
ignored with a warning.  They exist for host-only unit tests; do not use them
for a real boot.  CAMERA_BOOT_ATTEMPT_SECONDS overrides the 120 s boot timeout.
EOF
}

usage_die() { printf 'ERROR: %s\n' "$*" >&2; printf 'Try: %s --help\n' "$(basename "$0")" >&2; exit 2; }

# --- generic helpers ------------------------------------------------------
file_sha256() { sha256sum -- "$1" | awk '{print $1}'; }
file_size() { stat -c %s -- "$1"; }
file_mtime() { stat -c %y -- "$1" | cut -d. -f1; }

repo_path() {
    local path=$1
    case "$path" in
        /*) ;;
        *) path="$ROOT/$path" ;;
    esac
    readlink -m -- "$path"
}

baseline_component() {
    case "$1" in
        */nura-sm8150-7-0|*/nura-sm8150-7-0/*|*/linux-v6.17.0-sm8150|*/linux-v6.17.0-sm8150/*) return 0 ;;
    esac
    return 1
}

inside_root() {
    case "$(readlink -m -- "$1")" in
        "$ROOT_REAL"/*) return 0 ;;
    esac
    return 1
}

need_tool() {
    local name=$1 candidate="$ROOT/platform-tools/$1"
    if [[ -x "$candidate" ]]; then printf '%s' "$candidate"; return 0; fi
    candidate="$(command -v -- "$name" 2>/dev/null || true)"
    if [[ -n "$candidate" && -x "$candidate" ]]; then printf '%s' "$candidate"; return 0; fi
    return 1
}

guard_uint() {
    local label=$1 value=$2
    [[ "$value" =~ ^[0-9]+$ ]] || usage_die "$label needs a non-negative integer, got '$value'"
}

guard_fastboot_verb() {
    case "$1" in
        devices|getvar|boot|reboot|version|--version) return 0 ;;
        *) die 3 "refusing fastboot '$1': camera_boot.sh never writes to the phone" ;;
    esac
}

guard_adb_verb() {
    case "$1" in
        devices|shell|reboot) return 0 ;;
        *) die 3 "refusing adb '$1': camera_boot.sh never writes to the phone" ;;
    esac
}

# --- manifest -------------------------------------------------------------
read_manifest() {
    python3 - "$MANIFEST" <<'PY'
import json
import re
import sys

path = sys.argv[1]
try:
    with open(path, encoding='utf-8') as stream:
        doc = json.load(stream)
except FileNotFoundError:
    sys.stderr.write('manifest missing: %s\n' % path)
    sys.exit(3)
except (OSError, ValueError) as error:
    sys.stderr.write('manifest unreadable: %s: %s\n' % (path, error))
    sys.exit(3)
if not isinstance(doc, dict):
    sys.stderr.write('manifest is not a JSON object: %s\n' % path)
    sys.exit(3)

errors = []
rows = []

def text(name, required=True):
    value = doc.get(name)
    if value is None:
        if required:
            errors.append('missing %r' % name)
        return None
    if not isinstance(value, str) or not value:
        errors.append('%r must be a non-empty string' % name)
        return None
    return value

def artifact(name, aliases=()):
    key = name if name in doc else next((alias for alias in aliases if alias in doc), None)
    if key is None:
        errors.append('missing %r' % name)
        return None
    value = doc[key]
    if not isinstance(value, dict):
        errors.append('%r must be an object with path/sha256/size' % key)
        return None
    path = value.get('path')
    digest = value.get('sha256')
    size = value.get('size')
    if not isinstance(path, str) or not path:
        errors.append('%s.path must be a non-empty string' % key)
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest or ''):
        errors.append('%s.sha256 must be 64 lowercase hex characters' % key)
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        errors.append('%s.size must be a non-negative integer' % key)
    return key, path, digest, size

release = text('release')
if release is not None:
    rows.append(('release', release))
for name in ('worktree', 'created_at'):
    value = text(name)
    if value is not None:
        rows.append((name, value))
value = text('git_head', required=False)
if value is not None:
    rows.append(('git_head', value))
if 'nbd_port' in doc:
    port = doc['nbd_port']
    if not isinstance(port, int) or isinstance(port, bool):
        errors.append("'nbd_port' must be an integer")
    else:
        rows.append(('nbd_port', str(port)))

for name, aliases in (
    ('boot_img', ()),
    ('image', ()),
    ('kernel_package', ('vmlinuz',)),
    ('dtb', ()),
    ('initramfs', ()),
    ('rootfs_image', ()),
    ('i2c_qcom_cci', ('cci_module',)),
):
    entry = artifact(name, aliases)
    if entry is None:
        continue
    key, entry_path, entry_sha, entry_size = entry
    rows.append((name + '.key', key))
    rows.append((name + '.path', entry_path))
    rows.append((name + '.sha256', entry_sha))
    rows.append((name + '.size', str(entry_size)))

if errors:
    for error in errors:
        sys.stderr.write('manifest error: %s\n' % error)
    sys.exit(3)
for key, value in rows:
    sys.stdout.write('%s\t%s\n' % (key, value))
PY
}

load_manifest() {
    MAN=()
    MAN_LOADED=0
    if [[ ! -f "$MANIFEST" ]]; then
        if (( ACCEPT_CURRENT )); then
            warn "--accept-current: $MANIFEST is missing; expectations come from the on-disk artifacts and packaging-inputs.json"
            return 0
        fi
        fail_check "no delivery manifest: $MANIFEST"
        state "run: bash $(printf '%q' "$ROOT/scripts/camera_build.sh") manifest"
        state "or re-run with --accept-current to boot the current artifacts"
        return 1
    fi
    local out
    if ! out="$(read_manifest)"; then
        if (( ACCEPT_CURRENT )); then
            warn "--accept-current: $MANIFEST is invalid; expectations come from the on-disk artifacts"
            return 0
        fi
        fail_check "invalid delivery manifest: $MANIFEST"
        state "re-run: bash $(printf '%q' "$ROOT/scripts/camera_build.sh") manifest"
        return 1
    fi
    local key value
    while IFS=$'\t' read -r key value; do
        [[ -n "$key" ]] || continue
        MAN["$key"]="$value"
    done <<<"$out"
    MAN_LOADED=1
    return 0
}

# --- hashing / pinning ----------------------------------------------------
record_hash() {
    local path=$1
    [[ -f "$path" ]] || return 1
    HASH="$(file_sha256 "$path")"
    SIZE="$(file_size "$path")"
    return 0
}

compare_pin() {
    local label=$1 got_sha=$2 got_size=$3 want_sha=$4 want_size=$5
    local failed=0
    if [[ -n "$want_size" && "$got_size" != "$want_size" ]]; then
        fail_check "$label size $got_size != pinned $want_size"
        failed=1
    fi
    if [[ -n "$want_sha" && "$got_sha" != "$want_sha" ]]; then
        fail_check "$label sha256 $got_sha != pinned $want_sha"
        failed=1
    fi
    if (( failed )); then return 1; fi
    return 0
}

check_pin_file() {
    local label=$1 path=$2 want_sha=${3:-} want_size=${4:-}
    if ! record_hash "$path"; then
        fail_check "$label missing: $path"
        return 1
    fi
    compare_pin "$label ($path)" "$HASH" "$SIZE" "$want_sha" "$want_size" || return 1
    pass_check "$label sha256 $HASH size $SIZE -> $path"
    return 0
}

embedded_offset() {
    python3 - "$1" "$2" <<'PY'
import sys

image, needle = sys.argv[1], sys.argv[2]
with open(needle, 'rb') as stream:
    want = stream.read()
if not want:
    sys.exit(1)
found = None
overlap = max(len(want) - 1, 0)
with open(image, 'rb') as stream:
    base = 0
    previous = b''
    while True:
        chunk = stream.read(8 << 20)
        if not chunk:
            break
        window = previous + chunk
        position = window.find(want)
        if position >= 0:
            found = base - len(previous) + position
            break
        previous = window[-overlap:] if overlap else b''
        base += len(chunk)
if found is None:
    sys.exit(1)
print(found)
PY
}

check_embedded() {
    local image=$1 needle=$2 label=$3 offset=''
    if [[ ! -f "$image" ]]; then
        fail_check "$label: boot image missing: $image"
        return 1
    fi
    if [[ ! -f "$needle" ]]; then
        fail_check "$label: file missing: $needle"
        return 1
    fi
    if ! offset="$(embedded_offset "$image" "$needle")"; then
        fail_check "$label is not embedded in $(basename "$image"): the boot image was packed from different bytes"
        return 1
    fi
    if (( offset % 4096 != 0 )); then
        fail_check "$label found at unaligned offset $offset in $(basename "$image")"
        return 1
    fi
    pass_check "$label embedded at offset $offset in $(basename "$image")"
    EMBEDDED_OFFSET=$offset
    return 0
}

probe_module_in_image() {
    python3 - "$1" "$2" <<'PY'
"""Locate the fixed CCI module inside a pmOS GPT/ext4 rootfs image.

The module is a small regular file, so each complete filesystem block of it
(4 KiB) appears verbatim in the image no matter how the extents are laid out.
That makes a block-level needle search an exact content check without mounting
the image or parsing ext4.
"""
import sys

image, module = sys.argv[1], sys.argv[2]
with open(module, 'rb') as stream:
    data = stream.read()
if not data:
    sys.exit(1)
chunks = [data[0:4096]]
if len(data) > 4096:
    chunks.append(data[4096:8192])
found = [None] * len(chunks)
remaining = len(chunks)
with open(image, 'rb') as stream:
    base = 0
    previous = b''
    while remaining:
        chunk = stream.read(8 << 20)
        if not chunk:
            break
        window = previous + chunk
        for index, needle in enumerate(chunks):
            if found[index] is not None:
                continue
            position = window.find(needle)
            if position >= 0:
                found[index] = base - len(previous) + position
                remaining -= 1
        previous = window[-4095:]
        base += len(chunk)
print(' '.join('-' if position is None else str(position) for position in found))
sys.exit(0 if remaining == 0 else 1)
PY
}

# --- artifact identity ----------------------------------------------------
check_root_markers() {
    if baseline_component "$ROOT_REAL"; then
        die 3 "refusing to run from the baseline tree: $ROOT_REAL"
    fi
    [[ -d "$ROOT/device/testing/rear-camera" ]] ||
        die 3 "not a rear-camera candidate checkout (missing device/testing/rear-camera): $ROOT_REAL"
    [[ -f "$ROOT/scripts/build_bootimg_rear_camera.sh" ]] ||
        die 3 "not a rear-camera candidate checkout (missing scripts/build_bootimg_rear_camera.sh): $ROOT_REAL"
    [[ -d "$ROOT/build/rear-camera" ]] ||
        die 3 "missing candidate build directory: $ROOT/build/rear-camera"
}

check_release() {
    if [[ ! -f "$KERNEL_RELEASE_FILE" ]]; then
        fail_check "kernel release file missing: $KERNEL_RELEASE_FILE"
        return 1
    fi
    local release
    release="$(tr -d '[:space:]' <"$KERNEL_RELEASE_FILE")"
    if [[ "$release" != "$RELEASE" ]]; then
        fail_check "staged kernel release '$release' != '$RELEASE' ($KERNEL_RELEASE_FILE)"
        return 1
    fi
    pass_check "staged kernel release $release"
    return 0
}

check_manifest_identity() {
    local ok=0
    if [[ "${MAN[release]:-}" != "$RELEASE" ]]; then
        fail_check "manifest release '${MAN[release]:-}' != '$RELEASE'"
        ok=1
    else
        pass_check "manifest release $RELEASE"
    fi
    local manifest_worktree
    manifest_worktree="$(repo_path "${MAN[worktree]:-}")"
    if [[ "$manifest_worktree" != "$ROOT_REAL" ]]; then
        fail_check "manifest worktree '$manifest_worktree' is not this checkout '$ROOT_REAL'"
        state "re-run camera_build.sh manifest in this worktree"
        ok=1
    else
        pass_check "manifest worktree $manifest_worktree"
    fi
    if [[ -n "${MAN[nbd_port]:-}" && "${MAN[nbd_port]:-}" != "$NBD_PORT" ]]; then
        fail_check "manifest nbd_port '${MAN[nbd_port]:-}' != $NBD_PORT"
        ok=1
    fi
    if [[ -n "${MAN[git_head]:-}" ]]; then
        local head
        head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
        if [[ -n "$head" && "$head" != "${MAN[git_head]:-}" ]]; then
            warn "manifest git_head ${MAN[git_head]:-} differs from HEAD $head (artifacts are not from this commit exactly)"
        fi
    fi
    if (( ok )); then return 1; fi
    return 0
}

check_boot_image() {
    if ! record_hash "$BOOT_IMG"; then
        fail_check "candidate boot image missing: $BOOT_IMG"
        return 1
    fi
    BOOT_SHA="$HASH"
    BOOT_SIZE="$SIZE"
    local known failed=0
    for known in "${BASELINE_BOOT_SHA256[@]}"; do
        if [[ "$BOOT_SHA" == "$known" ]]; then
            fail_check "boot image is the known baseline $known; refusing to boot $BOOT_IMG"
            failed=1
        fi
        if [[ "${MAN[boot_img.sha256]:-}" == "$known" ]]; then
            fail_check "manifest pins the known baseline boot image $known; re-run camera_build.sh manifest"
            failed=1
        fi
    done
    if (( failed )); then return 1; fi
    if (( MAN_LOADED )); then
        local want_path
        want_path="$(repo_path "${MAN[boot_img.path]:-}")"
        if ! inside_root "$want_path"; then
            fail_check "manifest boot_img.path escapes this checkout: $want_path"
            return 1
        fi
        if [[ "$want_path" != "$(readlink -m -- "$BOOT_IMG")" ]]; then
            fail_check "manifest boot_img.path '$want_path' is not the candidate path $BOOT_IMG"
            return 1
        fi
        if ! compare_pin "boot image ($BOOT_IMG)" "$BOOT_SHA" "$BOOT_SIZE" \
            "${MAN[boot_img.sha256]:-}" "${MAN[boot_img.size]:-}"; then
            return 1
        fi
    fi
    pass_check "boot image sha256 $BOOT_SHA size $BOOT_SIZE -> $BOOT_IMG"
    return 0
}

check_packed_dtb() {
    if ! record_hash "$BASE_DTB"; then
        fail_check "kernel-package DTB missing: $BASE_DTB"
        return 1
    fi
    local base_sha="$HASH"
    local base_size="$SIZE"
    if (( MAN_LOADED )); then
        if ! check_pin_file "manifest DTB" \
            "$(repo_path "${MAN[dtb.path]:-}")" "${MAN[dtb.sha256]:-}" "${MAN[dtb.size]:-}"; then
            return 1
        fi
        if ! compare_pin "manifest DTB vs kernel-package DTB" "$base_sha" "$base_size" \
            "${MAN[dtb.sha256]:-}" "${MAN[dtb.size]:-}"; then
            return 1
        fi
    fi
    pass_check "base DTB sha256 $base_sha size $base_size -> $BASE_DTB"

    PACKED_DTB="$BASE_DTB"
    PACKED_DTB_LABEL='base DTB'
    if [[ -f "$WRAPPER_DTB" ]]; then
        PACKED_DTB="$WRAPPER_DTB"
        PACKED_DTB_LABEL='DTBO quarantine wrapper'
        if [[ -f "$WRAPPER_MANIFEST" ]]; then
            local wrapper_base
            wrapper_base="$(python3 - "$WRAPPER_MANIFEST" <<'PY'
import json
import sys
try:
    doc = json.load(open(sys.argv[1], encoding='utf-8'))
    print(doc['inputs']['base_dtb']['sha256'])
except Exception:
    sys.exit(1)
PY
)" || wrapper_base=''
            if [[ -n "$wrapper_base" && "$wrapper_base" != "$base_sha" ]]; then
                fail_check "temporary-dtbo wrapper was built from base DTB $wrapper_base, not $base_sha"
                return 1
            fi
        else
            warn "temporary-dtbo/manifest.json missing; cannot prove the wrapper's base DTB"
        fi
    else
        warn "DTBO quarantine wrapper missing ($WRAPPER_DTB); assuming the boot image was packed from the base DTB"
    fi
    if ! record_hash "$PACKED_DTB"; then
        fail_check "packed DTB missing: $PACKED_DTB"
        return 1
    fi
    pass_check "packed DTB ($PACKED_DTB_LABEL) sha256 $HASH size $SIZE -> $PACKED_DTB"
    if ! check_embedded "$BOOT_IMG" "$PACKED_DTB" "$PACKED_DTB_LABEL"; then
        return 1
    fi
    DTB_OFFSET="$EMBEDDED_OFFSET"
    return 0
}

check_initramfs() {
    INITRAMFS_PATH=''
    local want_sha='' want_size=''
    if (( MAN_LOADED )); then
        INITRAMFS_PATH="$(repo_path "${MAN[initramfs.path]:-}")"
        want_sha="${MAN[initramfs.sha256]:-}"
        want_size="${MAN[initramfs.size]:-}"
    elif [[ -f "$PACKAGING_INPUTS" ]]; then
        INITRAMFS_PATH="$(readlink -m -- "$EXPORT_INITRAMFS")"
        want_sha="$(python3 - "$PACKAGING_INPUTS" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding='utf-8')).get('initramfs_sha256', ''))
except Exception:
    sys.exit(1)
PY
)" || want_sha=''
    else
        INITRAMFS_PATH="$(readlink -m -- "$EXPORT_INITRAMFS")"
        warn "no manifest and no packaging-inputs.json: initramfs hash is not pinned"
    fi
    if [[ -z "$INITRAMFS_PATH" ]]; then
        fail_check "manifest initramfs.path is empty"
        return 1
    fi
    if ! check_pin_file "initramfs" "$INITRAMFS_PATH" "$want_sha" "$want_size"; then
        return 1
    fi
    if ! check_embedded "$BOOT_IMG" "$INITRAMFS_PATH" "initramfs"; then
        return 1
    fi
    INITRAMFS_OFFSET="$EMBEDDED_OFFSET"
    return 0
}

check_kernel_image() {
    local want_vmlinuz_sha='' want_vmlinuz_size='' want_image_sha='' want_image_size=''
    if (( MAN_LOADED )); then
        local vmlinuz_path
        vmlinuz_path="$(repo_path "${MAN[kernel_package.path]:-}")"
        if [[ -d "$vmlinuz_path" ]]; then
            vmlinuz_path="$vmlinuz_path/boot/vmlinuz"
        fi
        want_vmlinuz_sha="${MAN[kernel_package.sha256]:-}"
        want_vmlinuz_size="${MAN[kernel_package.size]:-}"
        if ! check_pin_file "kernel-package vmlinuz" "$vmlinuz_path" "$want_vmlinuz_sha" "$want_vmlinuz_size"; then
            return 1
        fi
        if [[ "$(readlink -m -- "$vmlinuz_path")" != "$(readlink -m -- "$VMLINUZ")" ]]; then
            fail_check "manifest kernel_package '$vmlinuz_path' is not the staged vmlinuz $VMLINUZ"
            return 1
        fi
        want_image_sha="${MAN[image.sha256]:-}"
        want_image_size="${MAN[image.size]:-}"
    fi
    if [[ ! -f "$VMLINUZ" ]]; then
        fail_check "kernel-package vmlinuz missing: $VMLINUZ"
        return 1
    fi
    if [[ ! -f "$KERNEL_IMAGE" ]]; then
        fail_check "uncompressed Image missing: $KERNEL_IMAGE"
        return 1
    fi
    local image_sha image_size vmlinuz_image_sha
    image_sha="$(file_sha256 "$KERNEL_IMAGE")"
    image_size="$(file_size "$KERNEL_IMAGE")"
    if ! compare_pin "kernel Image ($KERNEL_IMAGE)" "$image_sha" "$image_size" "$want_image_sha" "$want_image_size"; then
        return 1
    fi
    if ! vmlinuz_image_sha="$(gzip -dc -- "$VMLINUZ" | sha256sum | awk '{print $1}')"; then
        fail_check "cannot decompress kernel-package vmlinuz: $VMLINUZ"
        return 1
    fi
    if [[ "$vmlinuz_image_sha" != "$image_sha" ]]; then
        fail_check "vmlinuz decompresses to $vmlinuz_image_sha, not the staged Image $image_sha"
        return 1
    fi
    pass_check "kernel Image sha256 $image_sha size $image_size (vmlinuz decompresses to it)"
    return 0
}

check_cci_module() {
    local want_sha="$EXPECTED_CCI_SHA256" want_size="$EXPECTED_CCI_SIZE" origin='CAMERA_BOOT_CCI_* override'
    if (( MAN_LOADED )); then
        want_sha="${MAN[i2c_qcom_cci.sha256]:-}"
        want_size="${MAN[i2c_qcom_cci.size]:-}"
        origin='DELIVERY.json'
        if [[ -n "$EXPECTED_CCI_SHA256" && "$EXPECTED_CCI_SHA256" != "$want_sha" ]]; then
            warn "CAMERA_BOOT_CCI_SHA256 is ignored: DELIVERY.json is authoritative for this build"
        fi
        if [[ -z "$want_sha" ]]; then
            fail_check "manifest has no i2c_qcom_cci.sha256"
            return 1
        fi
        local module_path
        module_path="$(repo_path "${MAN[i2c_qcom_cci.path]:-}")"
        if ! check_pin_file "manifest CCI module" "$module_path" "$want_sha" "$want_size"; then
            return 1
        fi
    elif [[ -n "$want_sha" ]]; then
        warn "$origin pins the CCI module (host-only testing seam)"
    else
        warn "no manifest and no CAMERA_BOOT_CCI_SHA256: CCI module hash is not pinned"
    fi
    if ! check_pin_file "kernel-package CCI module" "$CCI_MODULE" "$want_sha" "$want_size"; then
        return 1
    fi
    CCI_PIN_SHA="$want_sha"
    CCI_PIN_SIZE="$(file_size "$CCI_MODULE")"
    if [[ -n "$want_sha" ]]; then
        pass_check "CCI module matches $origin ($want_sha, $CCI_PIN_SIZE bytes)"
    fi
    return 0
}

check_rootfs_pin() {
    ROOTFS_PIN_SHA=''
    ROOTFS_PIN_SIZE=''
    if (( MAN_LOADED )); then
        ROOTFS_PIN_SHA="${MAN[rootfs_image.sha256]:-}"
        ROOTFS_PIN_SIZE="${MAN[rootfs_image.size]:-}"
        local rootfs_path
        rootfs_path="$(repo_path "${MAN[rootfs_image.path]:-}")"
        if ! check_pin_file "rootfs image" "$rootfs_path" "$ROOTFS_PIN_SHA" "$ROOTFS_PIN_SIZE"; then
            return 1
        fi
        return 0
    fi
    if [[ -f "$PACKAGING_INPUTS" ]]; then
        ROOTFS_PIN_SHA="$(python3 - "$PACKAGING_INPUTS" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding='utf-8')).get('rootfs_image_sha256', ''))
except Exception:
    sys.exit(1)
PY
)" || ROOTFS_PIN_SHA=''
        if [[ -n "$ROOTFS_PIN_SHA" ]]; then
            pass_check "rootfs pin $ROOTFS_PIN_SHA from packaging-inputs.json (accept-current)"
        else
            warn "packaging-inputs.json has no rootfs_image_sha256; served image hash is not pinned"
        fi
        return 0
    fi
    warn "no manifest and no packaging-inputs.json: served image hash is not pinned"
    return 0
}

print_identity() {
    local head dirty
    head="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    dirty="$(git -C "$ROOT" status --porcelain 2>/dev/null | awk 'END {print NR}' || true)"
    say ''
    say '== candidate identity =='
    printf '  %-12s %s\n' worktree "$ROOT_REAL"
    printf '  %-12s %s\n' release "$RELEASE"
    printf '  %-12s %s (dirty files: %s)\n' git_head "$head" "$dirty"
    if (( MAN_LOADED )); then
        printf '  %-12s %s (created_at %s)\n' manifest "$MANIFEST" "${MAN[created_at]:-unknown}"
        if [[ -n "${MAN[git_head]:-}" ]]; then
            printf '  %-12s %s\n' manifest_git "${MAN[git_head]:-}"
        fi
    elif (( ACCEPT_CURRENT )); then
        printf '  %-12s %s (--accept-current: no pinned manifest)\n' manifest 'none'
    fi
    printf '  %-12s %s\n' boot.img "$BOOT_IMG"
    printf '  %-12s sha256 %s size %s mtime %s\n' '' "${BOOT_SHA:-unknown}" "${BOOT_SIZE:-unknown}" "$(file_mtime "$BOOT_IMG" 2>/dev/null || echo unknown)"
    printf '  %-12s %s\n' packed_dtb "${PACKED_DTB:-unknown} ($PACKED_DTB_LABEL)"
    printf '  %-12s sha256 %s embedded_at %s\n' '' "$(file_sha256 "${PACKED_DTB:-/nonexistent}" 2>/dev/null || echo unknown)" "${DTB_OFFSET:-unknown}"
    printf '  %-12s %s embedded_at %s\n' initramfs "${INITRAMFS_PATH:-unknown}" "${INITRAMFS_OFFSET:-unknown}"
    printf '  %-12s %s\n' kernel "$VMLINUZ"
    printf '  %-12s %s\n' cci_module "${CCI_MODULE} (sha256 ${CCI_PIN_SHA:-unknown})"
    printf '  %-12s %s\n' rootfs_pin "${ROOTFS_PIN_SHA:-unpinned} (source ${SOURCE_IMAGE})"
    say ''
}

# --- served image ---------------------------------------------------------
# The documented order is camera_serve.sh first, camera_boot.sh second.  The
# serve script waits for the USB gadget and only then starts nbd-server, so
# port 10001 is normally still closed while this script runs.  Pre-boot the
# listener is therefore advisory; the pre-exposure identity is verified.
collect_listener_check() {
    local mode=$1  # pre-boot | status
    if (( ! SERVER_CHECK )); then
        warn "--no-server-check: skipped the port-$NBD_PORT listener check (the served image identity is still checked)"
        return 0
    fi
    if ! command -v ss >/dev/null 2>&1; then
        fail_check "ss (iproute2) not found; cannot confirm port $NBD_PORT"
        return 1
    fi
    if ss -ltnH "sport = :$NBD_PORT" 2>/dev/null | grep -q .; then
        pass_check "port $NBD_PORT is listening"
        return 0
    fi
    if [[ "$mode" == pre-boot ]]; then
        warn "port $NBD_PORT is not listening yet (expected: camera_serve.sh waits for the USB gadget and starts nbd-server while the phone boots)"
        state "leave the serve terminal running: bash $(printf '%q' "$ROOT/scripts/camera_serve.sh")"
        return 0
    fi
    fail_check "nothing is listening on port $NBD_PORT (a temporary boot without NBD cannot reach userspace)"
    state "start it in another terminal: bash $(printf '%q' "$ROOT/scripts/camera_serve.sh")"
    return 1
}

# Post-boot the served image is a mutable NBD backing file: the phone mounts
# it read-write, so a whole-image hash taken now races legitimate writes. Use
# the identity camera_serve.sh verified before exposure instead.
collect_served_status_identity() {
    local recorded
    if [[ ! -f "$SERVED_IMAGE" ]]; then
        fail_check "served netboot image missing: $SERVED_IMAGE"
        return 1
    fi
    if [[ ! -f "$SERVED_RECORD" ]]; then
        warn "no pre-exposure identity record ($SERVED_RECORD); not hashing a possibly booted, mutable image"
        state "run camera_serve.sh to refresh and record the next served image"
        return 0
    fi
    recorded="$(python3 - "$SERVED_RECORD" "$SERVED_IMAGE" "$ROOTFS_PIN_SHA" <<'PY'
import json
import sys
from pathlib import Path

record, image, pin = sys.argv[1:4]
try:
    doc = json.load(open(record, encoding='utf-8'))
except (OSError, ValueError) as exc:
    print(f'unreadable record: {exc}', file=sys.stderr)
    raise SystemExit(1)
if str(Path(doc.get('path', '')).resolve()) != str(Path(image).resolve()):
    print('record belongs to a different image path', file=sys.stderr)
    raise SystemExit(1)
if pin and doc.get('source_sha256') != pin:
    print('record source sha256 differs from the manifest rootfs pin', file=sys.stderr)
    raise SystemExit(1)
print('%s %s' % (doc.get('served_sha256', '-'), doc.get('generation', '-')))
PY
)" || {
        warn "served-image record does not match this checkout; not rehashing a mutable post-boot image"
        return 0
    }
    SERVED_SHA="${recorded%% *}"
    if [[ -n "${recorded#* }" && "${recorded#* }" != '-' ]]; then
        local current
        current=$(stat -c '%d:%i:%s' -- "$SERVED_IMAGE") || current=
        if [[ "$current" != "${recorded#* }" ]]; then
            fail_check "served cache file changed after exposure (recorded ${recorded#* }, now ${current:-missing})"
            return 1
        fi
    fi
    pass_check "served image was verified before exposure ($SERVED_SHA, ${recorded#* }); post-boot bytes are mutable and were not rehashed"
    return 0
}

collect_served_image_checks() {
    local mode=${1:-pre-boot}
    local problems_before=${#PROBLEMS[@]}
    local serve_command
    serve_command="bash $(printf '%q' "$ROOT/scripts/camera_serve.sh")"
    if [[ "$mode" == status ]]; then
        collect_served_status_identity
        if [[ ! -f "$CCI_MODULE" ]]; then
            fail_check "CCI module missing: $CCI_MODULE"
            return 1
        fi
        return 0
    fi
    if [[ ! -f "$SERVED_IMAGE" ]]; then
        fail_check "served netboot image missing: $SERVED_IMAGE"
        state "run: $serve_command"
        return 1
    fi
    say "      hashing the served rootfs image ($(file_size "$SERVED_IMAGE") bytes) ..."
    SERVED_SHA="$(file_sha256 "$SERVED_IMAGE")"
    SERVED_SIZE="$(file_size "$SERVED_IMAGE")"
    if [[ -n "$ROOTFS_PIN_SHA" ]]; then
        if ! compare_pin "served image ($SERVED_IMAGE)" "$SERVED_SHA" "$SERVED_SIZE" "$ROOTFS_PIN_SHA" "$ROOTFS_PIN_SIZE"; then
            state "the netboot cache is not the pinned rootfs image; run: $serve_command"
        else
            pass_check "served image sha256 $SERVED_SHA size $SERVED_SIZE matches the pinned rootfs image"
        fi
    else
        warn "served image hash is not pinned; only the CCI module content is checked"
    fi
    if [[ ! -f "$CCI_MODULE" ]]; then
        fail_check "CCI module missing: $CCI_MODULE"
        return 1
    fi
    local offsets
    if ! offsets="$(probe_module_in_image "$SERVED_IMAGE" "$CCI_MODULE")"; then
        fail_check "served image does not contain the manifest CCI module ${CCI_PIN_SHA:-<unpinned>}"
        state "the netboot cache is a pre-fix rootfs image; run: $serve_command"
    else
        pass_check "manifest CCI module bytes present in the served image at $offsets"
    fi
    if (( ${#PROBLEMS[@]} > problems_before )); then return 1; fi
    return 0
}

# --- devices --------------------------------------------------------------
adb_devices() {
    local raw
    raw="$(timeout 20 "$ADB" devices 2>/dev/null || true)"
    printf '%s\n' "$raw" | tr -d '\r' |
        awk '/^[^[:space:]]+[[:space:]]+(device|unauthorized|offline|recovery|sideload|bootloader)$/ {print $1 "\t" $2}'
}

fastboot_devices() {
    local raw
    raw="$(timeout 20 "$FASTBOOT" devices 2>/dev/null || true)"
    printf '%s\n' "$raw" | tr -d '\r' |
        awk '/^[^[:space:]]+[[:space:]]+fastboot$/ {print $1 "\tfastboot"}'
}

android_getprop() {
    local serial=$1 prop=$2 value=''
    guard_adb_verb shell
    value="$(timeout 20 "$ADB" -s "$serial" shell getprop "$prop" 2>/dev/null | tr -d '\r' | head -n1)" || value=''
    printf '%s' "${value%"${value##*[![:space:]]}"}"
}

fastboot_getvar() {
    local serial=$1 name=$2 value=''
    guard_fastboot_verb getvar
    value="$(timeout 20 "$FASTBOOT" -s "$serial" getvar "$name" 2>&1 | tr -d '\r' |
        sed -n "s/^${name}:[[:space:]]*//p" | head -n1)" || value=''
    printf '%s' "$value"
}

matches_guacamole() {
    local value="${1,,}"
    [[ -n "$value" ]] || return 1
    [[ "$value" == *guacamole* ]] && return 0
    [[ "$value" == *"oneplus 7 pro"* ]] && return 0
    [[ "$value" =~ gm191[0-9] ]] && return 0
    return 1
}

identify_android() {
    local serial=$1 device model slot verified
    device="$(android_getprop "$serial" ro.product.device)"
    model="$(android_getprop "$serial" ro.product.model)"
    slot="$(android_getprop "$serial" ro.boot.slot_suffix)"
    verified="$(android_getprop "$serial" ro.boot.verifiedbootstate)"
    IDENTITY_PRODUCT="${device:-unknown}${model:+ ($model)}"
    IDENTITY_SLOT="$slot"
    IDENTITY_EXTRA="verifiedbootstate=${verified:-unknown}"
    if [[ "$device" == guacamole ]] || matches_guacamole "$model"; then
        IDENTITY_VERDICT=ok
    elif [[ -z "$device" && -z "$model" ]]; then
        IDENTITY_VERDICT=unverified
    else
        IDENTITY_VERDICT=mismatch
    fi
}

identify_fastboot() {
    local serial=$1 product slot
    product="$(fastboot_getvar "$serial" product)"
    slot="$(fastboot_getvar "$serial" current-slot)"
    IDENTITY_PRODUCT="${product:-unknown}"
    IDENTITY_SLOT="$slot"
    IDENTITY_EXTRA=''
    if matches_guacamole "$product"; then
        IDENTITY_VERDICT=ok
    elif [[ -z "$product" ]]; then
        IDENTITY_VERDICT=unverified
    else
        IDENTITY_VERDICT=mismatch
    fi
}

no_device_hint() {
    state "connect the phone; Android must allow USB debugging, or use 'adb reboot bootloader'"
    state "diagnose by hand: $(printf '%q' "$ROOT/platform-tools/adb") devices ; $(printf '%q' "$ROOT/platform-tools/fastboot") devices"
    state "'no permissions' in fastboot output means missing udev rules on this host"
    state "USB-reset recovery for a stuck ABL is in scripts/fastboot_boot.sh (18d1:d00d)"
}

select_target() {
    TARGET_STATE=''
    TARGET_SERIAL=''
    IDENTITY_VERDICT=''
    local -a adb_lines=() fastboot_lines=() candidates=() selected=()
    mapfile -t adb_lines < <(adb_devices)
    mapfile -t fastboot_lines < <(fastboot_devices)
    local line
    for line in ${fastboot_lines[@]+"${fastboot_lines[@]}"}; do candidates+=("$line"); done
    for line in ${adb_lines[@]+"${adb_lines[@]}"}; do candidates+=("$line"); done

    if (( ${#candidates[@]} == 0 )); then
        if (( DRY_RUN )); then
            warn 'no adb/fastboot device attached; --dry-run continues with a placeholder serial'
            return 0
        fi
        fail_check "no adb or fastboot device found"
        no_device_hint
        return 4
    fi

    local serial state serials=''
    for line in "${candidates[@]}"; do
        serial="${line%%$'\t'*}"
        state="${line#*$'\t'}"
        printf '      device %s\t%s\n' "$serial" "$state" >&2
        case ",$serials," in
            *,"$serial",*) ;;
            *) serials="$serials${serials:+,}$serial" ;;
        esac
    done

    if [[ -n "$SERIAL" ]]; then
        for line in "${candidates[@]}"; do
            [[ "${line%%$'\t'*}" == "$SERIAL" ]] && selected+=("$line")
        done
        if (( ${#selected[@]} == 0 )); then
            fail_check "--serial $SERIAL is not attached (attached: ${serials:-none})"
            return 4
        fi
    else
        if [[ "$serials" == *,* ]]; then
            fail_check "several devices attached (${serials}); pass --serial"
            return 4
        fi
        selected=("${candidates[@]}")
    fi

    # fastboot entries were collected first, so it wins if a serial is in both.
    local entry
    for entry in "${selected[@]}"; do
        state="${entry#*$'\t'}"
        if [[ "$state" == fastboot ]]; then
            TARGET_STATE=fastboot
            TARGET_SERIAL="${entry%%$'\t'*}"
            break
        fi
    done
    if [[ -z "$TARGET_STATE" ]]; then
        for entry in "${selected[@]}"; do
            state="${entry#*$'\t'}"
            if [[ "$state" == device ]]; then
                TARGET_STATE=android
                TARGET_SERIAL="${entry%%$'\t'*}"
                break
            fi
        done
    fi
    if [[ -z "$TARGET_STATE" ]]; then
        local states=''
        for entry in "${selected[@]}"; do states="$states ${entry#*$'\t'}"; done
        fail_check "attached device is not bootable from here (state:${states})"
        state "unauthorized/offline: accept the USB debugging prompt on the phone"
        state "recovery/sideload/bootloader: reboot the phone to Android or the bootloader first"
        return 4
    fi

    if [[ "$TARGET_STATE" == android ]]; then
        identify_android "$TARGET_SERIAL"
    else
        identify_fastboot "$TARGET_SERIAL"
    fi
    case "$IDENTITY_VERDICT" in
        ok) return 0 ;;
        unverified)
            if (( ASSUME_YES )); then
                warn "device identity is unavailable; continuing because --yes was given"
                return 0
            fi
            fail_check "device identity is unavailable (state $TARGET_STATE, serial $TARGET_SERIAL)"
            state "re-run with --yes only if this is the OnePlus 7 Pro GM1911"
            return 4
            ;;
        mismatch)
            if (( ASSUME_YES )); then
                warn "device identity '$IDENTITY_PRODUCT' does not identify guacamole; continuing because --yes was given"
                return 0
            fi
            fail_check "device identity '$IDENTITY_PRODUCT' does not identify guacamole/GM1911/OnePlus 7 Pro"
            state "re-run with --yes only if this is the OnePlus 7 Pro GM1911"
            return 4
            ;;
    esac
}

print_target() {
    if [[ -z "$TARGET_STATE" ]]; then
        printf '  %-12s %s\n' target 'none (no device attached)'
        return 0
    fi
    printf '  %-12s %s %s product=%s slot=%s %s\n' target "$TARGET_STATE" "$TARGET_SERIAL" \
        "$IDENTITY_PRODUCT" "${IDENTITY_SLOT:-unknown}" "${IDENTITY_EXTRA}"
}

status_devices() {
    local -a adb_lines=() fastboot_lines=()
    if [[ -z "$ADB" || -z "$FASTBOOT" ]]; then
        warn 'adb/fastboot not found; skipping the device section'
        return 0
    fi
    mapfile -t adb_lines < <(adb_devices)
    mapfile -t fastboot_lines < <(fastboot_devices)
    local -a lines=()
    local line
    for line in ${fastboot_lines[@]+"${fastboot_lines[@]}"}; do lines+=("$line"); done
    for line in ${adb_lines[@]+"${adb_lines[@]}"}; do lines+=("$line"); done
    if (( ${#lines[@]} == 0 )); then
        say 'INFO  no adb/fastboot device attached'
        return 0
    fi
    local serial state
    for line in "${lines[@]}"; do
        serial="${line%%$'\t'*}"
        state="${line#*$'\t'}"
        if [[ "$state" == fastboot ]]; then
            identify_fastboot "$serial"
            printf 'INFO  fastboot %s product=%s slot=%s verdict=%s\n' \
                "$serial" "$IDENTITY_PRODUCT" "${IDENTITY_SLOT:-unknown}" "$IDENTITY_VERDICT"
        elif [[ "$state" == device ]]; then
            identify_android "$serial"
            printf 'INFO  android  %s product=%s slot=%s %s verdict=%s\n' \
                "$serial" "$IDENTITY_PRODUCT" "${IDENTITY_SLOT:-unknown}" "$IDENTITY_EXTRA" "$IDENTITY_VERDICT"
        else
            printf 'INFO  adb %s state=%s (not bootable; accept the debugging prompt or reboot)\n' "$serial" "$state"
        fi
    done
    return 0
}

# --- logging / boot -------------------------------------------------------
log_path() {
    local kind=$1
    printf '%s/%s-%s.log' "$LOG_DIR" "$kind" "$(date -u +%Y%m%dT%H%M%SZ)"
}

append_log() {
    local log=$1
    shift
    printf '%s\n' "$*" >>"$log"
}

usb_reset_18d1d00d() {
    # Same recovery as scripts/fastboot_boot.sh: the ABL can stall while the
    # gadget still enumerates as 18d1:d00d; reset that USB device from the host.
    python3 - <<'PY'
import fcntl
import glob

for dev in glob.glob('/dev/bus/usb/*/*'):
    try:
        with open(dev, 'rb+') as handle:
            data = handle.read(18)
            if (data[8] | data[9] << 8, data[10] | data[11] << 8) == (0x18d1, 0xd00d):
                fcntl.ioctl(handle, 0x5514)  # USBDEVFS_RESET
                print('reset', dev)
    except OSError:
        pass
PY
}

wait_for_fastboot() {
    local serial=$1 seconds=$2
    local deadline=$((SECONDS + seconds))
    while (( SECONDS < deadline )); do
        if fastboot_devices | awk -F'\t' -v want="$serial" '$1 == want {found=1} END {exit !found}'; then
            return 0
        fi
        sleep 2
    done
    return 1
}

write_last_boot() {
    local result=$1 detail=$2 attempts=$3 log=$4 started=$5
    CAMERA_LAST_BOOT_ROOT="$ROOT_REAL" \
    CAMERA_LAST_BOOT_RELEASE="$RELEASE" \
    CAMERA_LAST_BOOT_IMAGE="$BOOT_IMG" \
    CAMERA_LAST_BOOT_IMAGE_SHA="$BOOT_SHA" \
    CAMERA_LAST_BOOT_IMAGE_SIZE="$BOOT_SIZE" \
    CAMERA_LAST_BOOT_DTB="$PACKED_DTB" \
    CAMERA_LAST_BOOT_DTB_SHA="$(file_sha256 "$PACKED_DTB" 2>/dev/null || true)" \
    CAMERA_LAST_BOOT_SERIAL="$TARGET_SERIAL" \
    CAMERA_LAST_BOOT_STATE="$TARGET_STATE" \
    CAMERA_LAST_BOOT_PRODUCT="$IDENTITY_PRODUCT" \
    CAMERA_LAST_BOOT_SLOT="$IDENTITY_SLOT" \
    CAMERA_LAST_BOOT_ATTEMPTS="$attempts" \
    CAMERA_LAST_BOOT_RESULT="$result" \
    CAMERA_LAST_BOOT_DETAIL="$detail" \
    CAMERA_LAST_BOOT_LOG="$log" \
    CAMERA_LAST_BOOT_STARTED="$started" \
    CAMERA_LAST_BOOT_FINISHED="$(date -u +%FT%TZ)" \
    python3 - "$LAST_BOOT" <<'PY'
import json
import os
import sys

path = sys.argv[1]
doc = {
    'release': os.environ['CAMERA_LAST_BOOT_RELEASE'],
    'worktree': os.environ['CAMERA_LAST_BOOT_ROOT'],
    'image': os.environ['CAMERA_LAST_BOOT_IMAGE'],
    'image_sha256': os.environ['CAMERA_LAST_BOOT_IMAGE_SHA'],
    'image_size': int(os.environ['CAMERA_LAST_BOOT_IMAGE_SIZE']),
    'dtb': os.environ['CAMERA_LAST_BOOT_DTB'],
    'dtb_sha256': os.environ['CAMERA_LAST_BOOT_DTB_SHA'],
    'device': {
        'state': os.environ['CAMERA_LAST_BOOT_STATE'],
        'serial': os.environ['CAMERA_LAST_BOOT_SERIAL'],
        'product': os.environ['CAMERA_LAST_BOOT_PRODUCT'],
        'slot': os.environ['CAMERA_LAST_BOOT_SLOT'],
    },
    'attempts': int(os.environ['CAMERA_LAST_BOOT_ATTEMPTS']),
    'result': os.environ['CAMERA_LAST_BOOT_RESULT'],
    'detail': os.environ['CAMERA_LAST_BOOT_DETAIL'],
    'log': os.environ['CAMERA_LAST_BOOT_LOG'],
    'started_at': os.environ['CAMERA_LAST_BOOT_STARTED'],
    'finished_at': os.environ['CAMERA_LAST_BOOT_FINISHED'],
}
os.makedirs(os.path.dirname(path), exist_ok=True)
temporary = path + '.tmp'
with open(temporary, 'w', encoding='utf-8') as stream:
    json.dump(doc, stream, indent=2, sort_keys=True)
    stream.write('\n')
os.replace(temporary, path)
PY
}

fastboot_boot_attempt() {
    local log=$1 attempt=$2 tmp rc=0
    tmp="$(mktemp)"
    guard_fastboot_verb boot
    say "      fastboot -s $TARGET_SERIAL boot $BOOT_IMG (timeout ${BOOT_ATTEMPT_TIMEOUT}s)"
    set +e
    timeout "$BOOT_ATTEMPT_TIMEOUT" "$FASTBOOT" -s "$TARGET_SERIAL" boot "$BOOT_IMG" >"$tmp" 2>&1
    rc=$?
    set -e
    ATTEMPT_OUTPUT="$(cat "$tmp")"
    ATTEMPT_RC="$rc"
    {
        printf '=== %s attempt %s: fastboot -s %s boot %s (exit %s) ===\n' \
            "$(date -u +%FT%TZ)" "$attempt" "$TARGET_SERIAL" "$BOOT_IMG" "$rc"
        cat "$tmp"
    } >>"$log"
    cat "$tmp"
    rm -f "$tmp"
    return 0
}

dry_run_boot() {
    local log=$1
    say '[dry-run] no adb/fastboot command will be executed and no file will be written'
    case "$TARGET_STATE" in
        android)
            printf '[dry-run]   timeout 30 %s -s %s reboot bootloader\n' "$ADB" "$TARGET_SERIAL"
            printf '[dry-run]   wait for "%s" in fastboot devices (timeout %ss)\n' "$TARGET_SERIAL" "$WAIT_TIMEOUT"
            ;;
        '')
            printf '[dry-run]   (no device attached) timeout 30 %s -s <serial> reboot bootloader\n' "$ADB"
            ;;
    esac
    printf '[dry-run]   timeout %s %s -s %s boot %s\n' \
        "$BOOT_ATTEMPT_TIMEOUT" "$FASTBOOT" "${TARGET_SERIAL:-<serial>}" "$BOOT_IMG"
    printf '[dry-run]   log would be written to %s\n' "$log"
    printf '[dry-run]   result would be written to %s\n' "$LAST_BOOT"
    printf '[dry-run]   on success: bash %q ; %s\n' "$ROOT/scripts/camera_verify.sh" "$SSH_HINT"
    return 0
}

cmd_boot() {
    report_identity || die 3 'candidate identity checks failed; nothing was sent to the phone'
    print_identity
    collect_listener_check pre-boot || true
    collect_served_image_checks || die 3 "the netboot cache is not the pinned candidate rootfs image; nothing was sent to the phone (run: bash $(printf '%q' "$ROOT/scripts/camera_serve.sh"))"
    select_target || exit 4
    print_target

    local log started
    log="$(log_path boot)"
    started="$(date -u +%FT%TZ)"

    if (( DRY_RUN )); then
        dry_run_boot "$log"
        return 0
    fi
    mkdir -p "$LOG_DIR"
    {
        printf '=== camera_boot.sh %s ===\n' "$started"
        printf 'worktree %s\nrelease %s\nboot.img %s\n' "$ROOT_REAL" "$RELEASE" "$BOOT_IMG"
        printf 'sha256 %s size %s\n' "$BOOT_SHA" "$BOOT_SIZE"
        printf 'dtb %s sha256 %s\n' "$PACKED_DTB" "$(file_sha256 "$PACKED_DTB")"
        printf 'device %s %s product=%s slot=%s\n' "$TARGET_STATE" "$TARGET_SERIAL" "$IDENTITY_PRODUCT" "$IDENTITY_SLOT"
    } >"$log"

    if [[ "$TARGET_STATE" == android ]]; then
        say "Android detected ($TARGET_SERIAL); requesting one reboot to the bootloader"
        guard_adb_verb reboot
        local rc=0
        set +e
        timeout 30 "$ADB" -s "$TARGET_SERIAL" reboot bootloader 2>&1 | tee -a "$log"
        rc=${PIPESTATUS[0]}
        set -e
        if (( rc != 0 )); then
            write_last_boot failed "adb reboot bootloader exited $rc" 0 "$log" "$started"
            die 5 "adb reboot bootloader failed; reboot the phone to the bootloader by hand and re-run $0 boot"
        fi
        if ! wait_for_fastboot "$TARGET_SERIAL" "$WAIT_TIMEOUT"; then
            write_last_boot failed "no fastboot device after ${WAIT_TIMEOUT}s" 0 "$log" "$started"
            die 5 "no fastboot device '$TARGET_SERIAL' after ${WAIT_TIMEOUT}s; check the cable and the phone screen"
        fi
        TARGET_STATE=fastboot
    fi

    local attempt=0 maximum=$((RETRIES + 1)) result='' detail=''
    while (( attempt < maximum )); do
        attempt=$((attempt + 1))
        say "temporary boot attempt $attempt/$maximum"
        fastboot_boot_attempt "$log" "$attempt"
        if [[ "$ATTEMPT_OUTPUT" == *Booting*OKAY* ]]; then
            result=success
            detail="fastboot boot OKAY"
            say ''
            say "candidate $RELEASE handed to the phone: $BOOT_IMG"
            write_last_boot "$result" "$detail" "$attempt" "$log" "$started"
            say ''
            say 'next steps:'
            say "  1. wait for userspace, then: $SSH_HINT"
            printf '  2. prove the fixed CCI module is running: bash %q\n' "$ROOT/scripts/camera_verify.sh"
            say '  3. photograph the phone screen before ANY reboot/panic recovery; the kernel is temporary and the rootfs comes from this host'
            say '  4. rollback: leave boot mode with fastboot reboot, and stop only the candidate port-10001 server'
            say "     log: $log"
            return 0
        fi
        if [[ "$ATTEMPT_RC" == 124 ]]; then
            detail="stalled (timeout ${BOOT_ATTEMPT_TIMEOUT}s)"
        elif [[ "$ATTEMPT_OUTPUT" == *'Load Error'* || "$ATTEMPT_OUTPUT" == *FAILED* ]]; then
            detail="Load Error from the ABL (exit $ATTEMPT_RC)"
        else
            detail="fastboot boot failed (exit $ATTEMPT_RC)"
        fi
        say "attempt $attempt failed: $detail"
        if (( attempt >= maximum )); then break; fi
        say "resetting the USB device and restarting the bootloader (scripts/fastboot_boot.sh recovery)"
        append_log "$log" "recovery: USBDEVFS_RESET on 18d1:d00d, then fastboot reboot bootloader"
        usb_reset_18d1d00d | tee -a "$log" || true
        sleep 2
        timeout 20 "$FASTBOOT" -s "$TARGET_SERIAL" reboot bootloader >>"$log" 2>&1 || true
        if ! wait_for_fastboot "$TARGET_SERIAL" "$WAIT_TIMEOUT"; then
            detail="no fastboot device after USB reset and reboot bootloader"
            break
        fi
        sleep 2
    done
    write_last_boot failed "$detail" "$attempt" "$log" "$started"
    say ''
    say 'the phone was NOT booted and nothing was flashed'
    say "recovery steps:"
    say '  * photograph the phone screen before rebooting if it shows a crash'
    say '  * check the cable/USB port, then reboot the phone to the bootloader by hand'
    say '  * do not power-cycle in a loop; inspect the log first'
    say "  * log: $log"
    exit 5
}

cmd_bootloader() {
    report_identity 0 || die 3 'candidate identity checks failed; nothing was sent to the phone'
    print_identity
    select_target || exit 4
    print_target
    local log
    log="$(log_path reboot)"
    if (( DRY_RUN )); then
        if [[ "$TARGET_STATE" == android ]]; then
            printf '[dry-run]   timeout 30 %s -s %s reboot bootloader\n' "$ADB" "$TARGET_SERIAL"
        elif [[ "$TARGET_STATE" == fastboot ]]; then
            printf '[dry-run]   timeout 30 %s -s %s reboot bootloader\n' "$FASTBOOT" "$TARGET_SERIAL"
        else
            printf '[dry-run]   (no device attached) adb/fastboot -s <serial> reboot bootloader\n'
        fi
        printf '[dry-run]   log would be written to %s\n' "$log"
        return 0
    fi
    mkdir -p "$LOG_DIR"
    printf '=== camera_boot.sh bootloader %s ===\n' "$(date -u +%FT%TZ)" >"$log"
    append_log "$log" "device $TARGET_STATE $TARGET_SERIAL product=$IDENTITY_PRODUCT slot=$IDENTITY_SLOT"
    if [[ "$TARGET_STATE" == android ]]; then
        guard_adb_verb reboot
        timeout 30 "$ADB" -s "$TARGET_SERIAL" reboot bootloader 2>&1 | tee -a "$log"
        if wait_for_fastboot "$TARGET_SERIAL" "$WAIT_TIMEOUT"; then
            pass_check "fastboot device $TARGET_SERIAL is ready"
        else
            fail_check "no fastboot device after ${WAIT_TIMEOUT}s"
            exit 3
        fi
    else
        guard_fastboot_verb reboot
        timeout 30 "$FASTBOOT" -s "$TARGET_SERIAL" reboot bootloader 2>&1 | tee -a "$log"
        pass_check "requested bootloader restart on $TARGET_SERIAL"
    fi
    say "log: $log"
    return 0
}

cmd_android() {
    report_identity 0 || die 3 'candidate identity checks failed; nothing was sent to the phone'
    print_identity
    select_target || exit 4
    print_target
    local log
    log="$(log_path reboot)"
    if (( DRY_RUN )); then
        if [[ "$TARGET_STATE" == fastboot ]]; then
            printf '[dry-run]   timeout 30 %s -s %s reboot\n' "$FASTBOOT" "$TARGET_SERIAL"
        else
            printf '[dry-run]   timeout 30 %s -s %s reboot\n' "$ADB" "${TARGET_SERIAL:-<serial>}"
        fi
        printf '[dry-run]   log would be written to %s\n' "$log"
        return 0
    fi
    mkdir -p "$LOG_DIR"
    printf '=== camera_boot.sh android %s ===\n' "$(date -u +%FT%TZ)" >"$log"
    append_log "$log" "device $TARGET_STATE $TARGET_SERIAL product=$IDENTITY_PRODUCT slot=$IDENTITY_SLOT"
    if [[ "$TARGET_STATE" == fastboot ]]; then
        guard_fastboot_verb reboot
        timeout 30 "$FASTBOOT" -s "$TARGET_SERIAL" reboot 2>&1 | tee -a "$log"
    else
        guard_adb_verb reboot
        timeout 30 "$ADB" -s "$TARGET_SERIAL" reboot 2>&1 | tee -a "$log"
    fi
    say "reboot requested on $TARGET_SERIAL"
    say "log: $log"
    return 0
}

cmd_status() {
    report_identity || true
    print_identity
    collect_listener_check status || true
    collect_served_image_checks status || true
    status_devices || true
    if (( ${#PROBLEMS[@]} )); then
        say ''
        printf '%d check(s) FAILED; the phone must not be booted until they pass\n' "${#PROBLEMS[@]}"
        return 3
    fi
    say 'all identity, served-image and device checks passed'
    return 0
}

report_identity() {
    local with_rootfs=${1:-1}
    PROBLEMS=()
    check_root_markers
    check_release || true
    load_manifest || true
    if (( MAN_LOADED )); then
        check_manifest_identity || true
    fi
    check_boot_image || true
    if [[ -f "$BOOT_IMG" ]]; then
        check_packed_dtb || true
        check_initramfs || true
    fi
    check_kernel_image || true
    check_cci_module || true
    if (( with_rootfs )); then
        check_rootfs_pin || true
    fi
    if (( ${#PROBLEMS[@]} )); then
        return 1
    fi
    return 0
}

require_tools() {
    if ! ADB="$(need_tool adb)"; then
        die 3 "adb not found (expected $ROOT/platform-tools/adb or PATH)"
    fi
    if ! FASTBOOT="$(need_tool fastboot)"; then
        die 3 "fastboot not found (expected $ROOT/platform-tools/fastboot or PATH)"
    fi
}

main() {
    local positional_seen=0
    while (( $# )); do
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --dry-run) DRY_RUN=1 ;;
            --yes|-y) ASSUME_YES=1 ;;
            --no-server-check) SERVER_CHECK=0 ;;
            --accept-current) ACCEPT_CURRENT=1 ;;
            --serial)
                (( $# >= 2 )) || usage_die '--serial needs a value'
                SERIAL="$2"; shift ;;
            --serial=*) SERIAL="${1#*=}" ;;
            --timeout)
                (( $# >= 2 )) || usage_die '--timeout needs a value'
                WAIT_TIMEOUT="$2"; shift ;;
            --timeout=*) WAIT_TIMEOUT="${1#*=}" ;;
            --retries)
                (( $# >= 2 )) || usage_die '--retries needs a value'
                RETRIES="$2"; shift ;;
            --retries=*) RETRIES="${1#*=}" ;;
            --) shift; while (( $# )); do set_subcommand "$1"; shift; done; continue ;;
            -*) usage_die "unknown option: $1" ;;
            *) set_subcommand "$1" ;;
        esac
        shift
    done
    guard_uint --timeout "$WAIT_TIMEOUT"
    guard_uint --retries "$RETRIES"
    if (( WAIT_TIMEOUT < 1 )); then usage_die '--timeout must be at least 1'; fi
    if (( RETRIES > 3 )); then usage_die '--retries is capped at 3; do not power-cycle the ABL in a loop'; fi
    if [[ -n "$SERIAL" && ! "$SERIAL" =~ ^[A-Za-z0-9._:-]+$ ]]; then
        usage_die "--serial '$SERIAL' does not look like a device serial"
    fi
    if [[ -n "$EXPECTED_CCI_SHA256" && ! "$EXPECTED_CCI_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
        usage_die 'CAMERA_BOOT_CCI_SHA256 must be 64 lowercase hex characters'
    fi
    if [[ -n "$EXPECTED_CCI_SIZE" && ! "$EXPECTED_CCI_SIZE" =~ ^[1-9][0-9]*$ ]]; then
        usage_die 'CAMERA_BOOT_CCI_SIZE must be a positive integer'
    fi
    SSH_HINT="ssh -4 -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$(printf '%q' "$ROOT/.work/delivery/known_hosts") user@172.16.42.1"

    case "$SUBCOMMAND" in
        boot|bootloader|android) require_tools ;;
        status) ADB="$(need_tool adb || true)"; FASTBOOT="$(need_tool fastboot || true)" ;;
    esac

    case "$SUBCOMMAND" in
        boot) cmd_boot ;;
        bootloader) cmd_bootloader ;;
        android) cmd_android ;;
        status) cmd_status ;;
    esac
}

set_subcommand() {
    if (( positional_seen )); then
        usage_die "unexpected argument: $1 (one subcommand only; this script never flashes)"
    fi
    case "$1" in
        boot|bootloader|android|status) SUBCOMMAND="$1" ;;
        flash|erase|format|update|sideload)
            usage_die "'$1' is not supported: camera_boot.sh only temporarily boots (fastboot boot); it never flashes or erases"
            ;;
        *) usage_die "unknown subcommand: $1" ;;
    esac
    positional_seen=1
}

main "$@"
