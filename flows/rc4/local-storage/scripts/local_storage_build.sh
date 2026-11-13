#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# One user-run entrypoint for the local-userdata trial profile.
#
# It stages an isolated kernel-source tree and guarded-hook recipe, runs the
# patched Plasma rootfs pipeline in local-only "intermediate" mode, then calls
# the local boot-image wrapper and writes DELIVERY.local.json.  It never
# touches the canonical rc4 boot image, manifest, netboot cache or rootfs.
#
# Default is a dry run.  --build executes the user-run/pbootstrap flow.
set -Eeuo pipefail
(( EUID != 0 )) || { echo 'Run as your normal user; pmbootstrap handles privilege.' >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
FLOW_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_ROOT="${NURA_SOURCE_ROOT:-runtime-workspace}"
LOCAL_STATE="$FLOW_DIR/.work/local-userdata"
LOCAL_OUT="$FLOW_DIR/build/local-userdata"
# The rc4 kernel build is produced by flows/rc4/scripts/rc4.sh kernel; this
# entrypoint never builds the kernel itself and copies only its state.
# KERNEL_BUILD is the built output (kernel-package/, Image, kernel-inputs.json);
# KERNEL_SOURCE_STATE is the builder state (linux-*/, output/).  Both are
# overridable so the rootless fake-builder fixture can drive this preflight.
KERNEL_STATE="$LOCAL_STATE/kernel-state"
KERNEL_BUILD="${NURA_KERNEL_BUILD:-$SOURCE_ROOT/flows/rc4/build-source/kernel-output}"
KERNEL_SOURCE_STATE="${NURA_KERNEL_SOURCE_STATE:-$SOURCE_ROOT/flows/rc4/build-source/kernel-state}"
ROOTFS_STATE="$LOCAL_STATE/rootfs-state"
HOOK_STAGE="$LOCAL_STATE/hook-recipe-stage"
HOOK_RECIPE="$HOOK_STAGE/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
FLOW_HOOK_RECIPE="$FLOW_DIR/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
HOOK_VERSION="4.2.1-r5"
RELEASE=""
ROOTFS_SCRIPT="$FLOW_DIR/scripts/rootfs_prepare_local.sh"
TOOL="$FLOW_DIR/scripts/local_storage_tool.py"
CHECKER="$FLOW_DIR/scripts/check_local_candidate.py"
SERVED_RECORD="${NURA_SERVED_RECORD:-$SOURCE_ROOT/.work/delivery-plasma-mobile/served-image.json}"
HELPER_SCRIPTS="$FLOW_DIR/scripts"
BUILD=0
REFRESH_KERNEL=0
AUTHORIZED_KEY=""
SNAP_BEFORE=""

usage() {
    sed -n '2,10p' "$0"
}

die() {
    echo "ERROR: $*" >&2
    exit 1
}

note() {
    printf '  %s\n' "$*"
}

overlap() {
    local left="$1" right="$2"
    [[ "$left" = "$right" ]] && return 0
    case "$left" in "$right"/*) return 0 ;; esac
    case "$right" in "$left"/*) return 0 ;; esac
    return 1
}

while (( $# )); do
    case "$1" in
        --build) BUILD=1 ;;
        --refresh-kernel) REFRESH_KERNEL=1 ;;
        --authorized-key)
            (( $# >= 2 )) || die "--authorized-key needs a public-key path"
            AUTHORIZED_KEY="$(realpath -e -- "$2")" || die "public-key file not found: $2"
            shift ;;
        --authorized-key=*)
            AUTHORIZED_KEY="$(realpath -e -- "${1#*=}")" || die "public-key file not found" ;;

        --release)
            (( $# >= 2 )) || die "--release needs a value"
            RELEASE=$2
            shift
            ;;
        --release=*) RELEASE=${1#*=} ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

[[ -f "$TOOL" ]] || die "missing local tool: $TOOL"
[[ -d "$SOURCE_ROOT" ]] || die "missing source checkout: $SOURCE_ROOT"
[[ "$FLOW_DIR" != "$SOURCE_ROOT" ]] || die "flow directory must not be the source checkout"
[[ -f "$FLOW_DIR/ORIGIN-SHA256.json" ]] || die "missing flow origin manifest: $FLOW_DIR/ORIGIN-SHA256.json"
[[ -f "$ROOTFS_SCRIPT" ]] || die "missing rootfs pipeline: $ROOTFS_SCRIPT"

source_real="$(realpath -m -- "$SOURCE_ROOT")"
flow_real="$(realpath -m -- "$FLOW_DIR")"
case "$flow_real" in
    "$source_real"/*) ;;
    *) case "$source_real" in "$flow_real"/*) die "flow directory must not contain the source checkout" ;; esac ;;
esac
protected_paths=(
    "$source_real/.work"
    "$source_real/build"
    "$source_real/scripts"
    "$source_real/device"
    "$source_real/tests"
    "$source_real/ref"
    "$source_real/local"
)
for path in "$LOCAL_STATE" "$LOCAL_OUT" "$KERNEL_STATE" "$ROOTFS_STATE" "$HOOK_STAGE"; do
    real="$(realpath -m -- "$path")"
    case "$real" in
        "$flow_real"/*) ;;
        *) die "local path must stay inside the flow directory: $path" ;;
    esac
    [[ "$real" != "$flow_real" ]] || die "local path must not be the flow root: $path"
    [[ ! -L "$path" ]] || die "local path must not be a symlink: $path"
    for protected in "${protected_paths[@]}"; do
        overlap "$real" "$protected" &&
            die "local path must not overlap a source/rc3 path (either direction): $path"
    done
done
state_real="$(realpath -m -- "$LOCAL_STATE")"
for path in "$KERNEL_STATE" "$ROOTFS_STATE" "$HOOK_STAGE"; do
    case "$(realpath -m -- "$path")" in
        "$state_real"|"$state_real"/*) ;;
        *) die "local subpath must stay under LOCAL_STATE: $path" ;;
    esac
done

[[ -f "$KERNEL_BUILD/kernel-inputs.json" ]] || die "missing canonical rc4 kernel inputs: $KERNEL_BUILD"
[[ -f "$KERNEL_BUILD/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" ]] ||
    die "missing canonical rc4 kernel package: $KERNEL_BUILD"
if [[ -z "$RELEASE" ]]; then
    RELEASE="$(cat "$KERNEL_BUILD/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release")"
fi
[[ "$RELEASE" =~ ^7\.0\.0-sm8150-(guacamole-rc|rc|rear)[0-9]+$ ]] || die "invalid release: $RELEASE"
[[ "$RELEASE" = "7.0.0-sm8150-guacamole-rc4" ]] ||
    die "this rc4 entrypoint expects the rc4 kernel release 7.0.0-sm8150-guacamole-rc4, got $RELEASE"
[[ "$HOOK_VERSION" = "4.2.1-r5" ]] || die "this local profile expects guarded hook 4.2.1-r5"

canonical_files=(
    "$KERNEL_BUILD/kernel-inputs.json"
    "$KERNEL_SOURCE_STATE/output/include/config/kernel.release"
    "$FLOW_HOOK_RECIPE/netboot.sh"
    "$FLOW_HOOK_RECIPE/APKBUILD"
)
canonical_snapshot() {
    local path
    for path in "${canonical_files[@]}"; do
        [[ -f "$path" && ! -L "$path" ]] ||
            die "missing read-only input: $path (run: flows/rc4/scripts/rc4.sh kernel)"
        printf '%s  %s\n' "$(sha256sum "$path" | awk '{print $1}')" "$path"
    done
}
SNAP_BEFORE="$(canonical_snapshot)"

ensure_local_writable() {
    command -v findmnt >/dev/null 2>&1 || die "findmnt is required before a local build"
    command -v flock >/dev/null 2>&1 || die "flock is required before a local build"
    command -v rsync >/dev/null 2>&1 || die "rsync is required to copy the kernel source without .output"
    if [[ -e "$LOCAL_STATE" && ! -d "$LOCAL_STATE" ]]; then
        die "LOCAL_STATE is not a directory: $LOCAL_STATE"
    fi
    if [[ -e "$LOCAL_OUT" && ! -d "$LOCAL_OUT" ]]; then
        die "LOCAL_OUT is not a directory: $LOCAL_OUT"
    fi
    local mounts target local_real
    mounts="$(findmnt -rn -o TARGET)" || die "findmnt failed; cannot prove the local paths are unmounted"
    for target in "$LOCAL_STATE" "$LOCAL_OUT"; do
        local_real="$(realpath -m -- "$target")"
        while IFS= read -r mounted; do
            [[ -n "$mounted" ]] || continue
            case "${mounted%/}/" in
                "$local_real/"*|"$local_real") die "local path has an active mount: $target" ;;
            esac
        done <<< "$mounts"
    done
    mkdir -p -- "$LOCAL_STATE" "$LOCAL_OUT"
    exec 9>"$LOCAL_STATE/.build.lock"
    flock -n 9 || die "another local-userdata build is already running for $LOCAL_STATE"
}

verify_kernel_copy() {
    local stamp="$KERNEL_STATE/.nura-copy.json"
    [[ -f "$stamp" ]] || return 1
    python3 - "$stamp" "$KERNEL_STATE" "$KERNEL_SOURCE_STATE" "$RELEASE" "$SOURCE_ROOT" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
stamp, state, source, release, canonical = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5]
data = json.loads(stamp.read_text())
if data.get("source") != str(source.resolve()) or data.get("release") != release:
    raise SystemExit(1)
source_tree = data.get("source_tree")
if not source_tree or not (state / source_tree / "Makefile").is_file():
    raise SystemExit(1)
for relative, expected in data.get("files", {}).items():
    path = state / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit(1)
link = state / "output/source"
if link.is_symlink() and os.path.isabs(os.readlink(link)):
    raise SystemExit(1)
if (state / source_tree / ".output").is_symlink():
    raise SystemExit(1)
makefile = state / "output/Makefile"
if makefile.is_file() and canonical and canonical in makefile.read_text(errors="replace"):
    raise SystemExit(1)
PY
}

copy_kernel_state() {
    local source="$KERNEL_SOURCE_STATE" source_dir source_name
    # Same preconditions the copy relies on, checked in one place (read-only):
    # linux-*/Makefile, output/include/config/kernel.release, output/.config and
    # no absolute output/source symlink.  This is what the fake-builder fixture
    # exercises without pmbootstrap.
    python3 "$CHECKER" kernel-copy-check --state "$source" > /dev/null ||
        die "canonical rc4 kernel state is not copyable: $source (run: flows/rc4/scripts/rc4.sh kernel)"
    source_dir="$(find "$source" -maxdepth 1 -type d -name 'linux-*' | sort | head -n1)"
    source_name="${source_dir##*/}"
    [[ -n "$source_dir" && -f "$source_dir/Makefile" ]] || die "missing canonical rc4 source under $source"
    [[ -f "$source/output/include/config/kernel.release" ]] || die "missing canonical rc4 output"
    if [[ -e "$KERNEL_STATE" ]]; then
        if (( REFRESH_KERNEL )); then
            note "refreshing the isolated kernel copy"
            rm -rf -- "$KERNEL_STATE"
        elif verify_kernel_copy; then
            note "reusing verified isolated kernel source/output: $KERNEL_STATE"
            note "source kernel package (read-only): $KERNEL_BUILD"
            return 0
        else
            die "local kernel copy is not verified (incomplete/corrupt): $KERNEL_STATE; re-run with --refresh-kernel"
        fi
    fi
    mkdir -p -- "$KERNEL_STATE/$source_name"
    # Exclude the huge source-local .output; the local output copy below is the
    # only one prepare_envkernel is allowed to use.
    rsync -a --exclude='.output' "$source_dir/" "$KERNEL_STATE/$source_name/" ||
        die "could not copy the kernel source tree"
    cp -a --reflink=auto "$source/output" "$KERNEL_STATE/" ||
        die "could not copy the kernel output"
    # The canonical output/Makefile contains absolute KBUILD_OUTPUT/source
    # paths.  It is not used by prepare_envkernel (which rewrites
    # .output/Makefile), but do not preserve it in the local copy.
    if [[ -f "$KERNEL_STATE/output/Makefile" ]]; then
        printf '# local copy; prepare_envkernel rewrites .output/Makefile\ninclude ../%s/Makefile\n' \
            "$source_name" > "$KERNEL_STATE/output/Makefile"
    fi
    # The canonical output/source is an absolute symlink back into the canonical
    # source tree.  Never leave that link in the local copy.
    if [[ -L "$KERNEL_STATE/output/source" ]]; then
        rm -f -- "$KERNEL_STATE/output/source"
        ln -s "../$source_name" "$KERNEL_STATE/output/source"
    fi
    if [[ -n "$(find "$KERNEL_STATE" -type l -lname "$SOURCE_ROOT/*" -print -quit)" ]]; then
        die "copied kernel state still has an absolute symlink into the canonical checkout"
    fi
    [[ ! -e "$KERNEL_STATE/$source_name/.output" || ! -L "$KERNEL_STATE/$source_name/.output" ]] ||
        die "copied kernel source has an .output symlink"
    for relative in "output/Makefile" "$source_name/Makefile"; do
        if [[ -f "$KERNEL_STATE/$relative" ]] && grep -Fq "$SOURCE_ROOT" "$KERNEL_STATE/$relative"; then
            die "local kernel file contains a canonical absolute path: $relative"
        fi
    done
    [[ -f "$KERNEL_STATE/output/include/config/kernel.release" ]] ||
        die "copied kernel state has no output"
    [[ -n "$(find "$KERNEL_STATE" -maxdepth 1 -type d -name 'linux-*' | head -n1)" ]] ||
        die "copied kernel state has no source tree"
    python3 - "$KERNEL_STATE" "$source" "$source_name" "$RELEASE" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
state, source, source_tree, release = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
files = {}
for relative in ("output/Makefile", "output/include/config/kernel.release", "output/.config", f"{source_tree}/Makefile",
                 f"{source_tree}/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts"):
    path = state / relative
    if path.is_file():
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
data = {"source": str(source.resolve()), "release": release, "source_tree": source_tree, "files": files}
(state / ".nura-copy.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
PY
    note "isolated kernel source/output: $KERNEL_STATE"
    note "source kernel package (read-only): $KERNEL_BUILD"
}

stage_hook_recipe() {
    local source="$FLOW_HOOK_RECIPE"
    [[ -d "$source" && ! -L "$source" ]] || die "missing flow hook recipe: $source"
    rm -rf -- "$HOOK_STAGE"
    mkdir -p -- "$HOOK_STAGE/device/testing/rear-camera"
    cp -a --reflink=auto "$source" "$HOOK_STAGE/device/testing/rear-camera/"
    [[ "$(sed -n 's/^pkgrel=//p' "$HOOK_RECIPE/APKBUILD")" = 5 ]] ||
        die "flow hook recipe is not pkgrel=5"
    grep -Fq 'pmos\.root=/dev/nbd' "$HOOK_RECIPE/netboot.sh" ||
        die "flow hook recipe has no local-root guard"
    note "staged guarded hook recipe: $HOOK_RECIPE"
}

run_rootfs() {
    local -a key_args=()
    [[ -z "$AUTHORIZED_KEY" ]] || key_args=(--authorized-key "$AUTHORIZED_KEY")
    NURA_SOURCE_ROOT="$SOURCE_ROOT" \
    NURA_ROOTFS_STATE="$ROOTFS_STATE" \
    NURA_ROOTFS_OUT="$LOCAL_OUT" \
    NURA_KERNEL_STATE="$KERNEL_STATE" \
    NURA_KERNEL_BUILD="$KERNEL_BUILD" \
    NURA_LOCAL_USERDATA=1 \
    NURA_LOCAL_HOOK_RECIPE="$HOOK_RECIPE" \
    NURA_NETBOOT_HOOK_VERSION="$HOOK_VERSION" \
    NURA_LOCAL_TOOL="$TOOL" \
    NURA_HELPER_SCRIPTS="$HELPER_SCRIPTS" \
    bash "$ROOTFS_SCRIPT" --build "${key_args[@]}"
}

run_boot_image() {
    local initramfs="$ROOTFS_STATE/rootfs/export/initramfs"
    local rootfs_image="$ROOTFS_STATE/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
    local rootfs_tree="$ROOTFS_STATE/pmbootstrap-work/chroot_rootfs_oneplus-guacamole"
    [[ -f "$LOCAL_OUT/ROOTFS-INTERMEDIATE.txt" ]] ||
        die "local rootfs pipeline did not write ROOTFS-INTERMEDIATE.txt"
    [[ -f "$initramfs" && ! -L "$initramfs" ]] || die "missing local initramfs: $initramfs"
    [[ -f "$rootfs_image" && ! -L "$rootfs_image" ]] || die "missing local rootfs image: $rootfs_image"
    [[ -d "$rootfs_tree" && ! -L "$rootfs_tree" ]] || die "missing local rootfs tree: $rootfs_tree"
    NURA_SOURCE_ROOT="$SOURCE_ROOT" \
    NURA_LOCAL_OUT="$LOCAL_OUT" \
    NURA_KERNEL_DIR="$KERNEL_BUILD/kernel-package" \
    NURA_KERNEL_STATE="$KERNEL_STATE" \
    NURA_KERNEL_BUILD="$KERNEL_BUILD" \
    NURA_KERNEL_RELEASE="$RELEASE" \
    NURA_SERVED_RECORD="$SERVED_RECORD" \
    NURA_HELPER_SCRIPTS="$HELPER_SCRIPTS" \
    bash "$FLOW_DIR/scripts/local_bootimg_wrapper.sh" \
        --initramfs "$initramfs" \
        --rootfs-image "$rootfs_image" \
        --rootfs-tree "$rootfs_tree" \
        --build
}

check_ready() {
    local manifest="$LOCAL_OUT/DELIVERY.local.json"
    [[ -f "$manifest" ]] || die "local boot wrapper did not write final manifest: $manifest"
    NURA_HELPER_SCRIPTS="$HELPER_SCRIPTS" python3 "$TOOL" check --manifest "$manifest"
    note "final local manifest: $manifest"
    note "final boot image: $LOCAL_OUT/boot/boot.img"
    note "intermediate rootfs record: $LOCAL_OUT/ROOTFS-INTERMEDIATE.txt (not a boot manifest)"
}

if (( ! BUILD )); then
    echo "=== local-userdata build plan (dry run) ==="
    echo "source checkout:    $SOURCE_ROOT"
    echo "flow directory:     $FLOW_DIR"
    echo "isolated state:     $LOCAL_STATE"
    echo "isolated output:    $LOCAL_OUT"
    echo "kernel source copy: $KERNEL_STATE (source package $KERNEL_BUILD read-only)"
    echo "guarded hook:       $HOOK_VERSION from $FLOW_HOOK_RECIPE"
    echo "release:            $RELEASE"
    echo "rootfs script:      $ROOTFS_SCRIPT (local intermediate mode)"
    echo "final manifest:     $LOCAL_OUT/DELIVERY.local.json"
    echo
    echo "DRY RUN: no files written. Re-run with --build."
    exit 0
fi

ensure_local_writable
copy_kernel_state
stage_hook_recipe
mkdir -p -- "$LOCAL_OUT"
rm -f -- "$LOCAL_OUT/DELIVERY.local.json"
run_rootfs
run_boot_image
check_ready

SNAP_AFTER="$(canonical_snapshot)"
[[ "$SNAP_BEFORE" = "$SNAP_AFTER" ]] ||
    die "canonical rc4 input files changed during the local build; refusing final readiness"
echo "PASS: source rc4 inputs unchanged; local source-ready manifest recorded."
echo "No phone, flash, slot or server action was performed. The rootfs step ran"
echo "the user's pmbootstrap/chroot flow and may therefore have mounted/chowned"
echo "its isolated local work directory; that is not a phone mount."
