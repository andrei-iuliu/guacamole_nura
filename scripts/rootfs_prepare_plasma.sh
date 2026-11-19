#!/usr/bin/env bash
# USER RUN ONLY: pmbootstrap invokes sudo/mount/chroots. Never run by an agent.
#
# Build the isolated Plasma Mobile rc3 rootfs/initramfs from the rc3 kernel
# build in .work/rc3-kernel / build/rc3-kernel.  The default is a read-only
# preflight; --build performs the privileged packaging.  --restore stages
# unprivileged and uses sudo only to preserve/install the rootfs image in
# pmbootstrap's protected parent directory.
#
#   bash scripts/rootfs_prepare_plasma.sh --check-only
#   bash scripts/rootfs_prepare_plasma.sh --build [--authorized-key PATH]
#   bash scripts/rootfs_prepare_plasma.sh --restore <rollback-dir>
#
# --authorized-key is an explicit, build-only opt-in: PATH must contain one or
# more plain OpenSSH PUBLIC keys (an existing authorized_keys file is fine).
# They are installed in the future rootfs for the configured build user by
# pmbootstrap itself.  Private keys are rejected and the default build adds no
# key.  No key is read outside that user-run --build.
#
# The rc2 baseline (.work/rear-camera, build/rear-camera, .work/delivery) is
# read-only input for its pinned hook/private-libcamera/system-libcamera APKs
# and known-good rootfs.  The rc3 kernel state is .work/rc3-kernel with output
# build/rc3-kernel.  The candidate state remains .work/plasma-mobile and its
# output build/plasma-mobile, so the rc2 rollback path is unchanged.
set -Eeuo pipefail

if (( EUID == 0 )); then
    echo "Run this script as your normal user; pmbootstrap handles privilege." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

STATE="$ROOT/.work/plasma-mobile"
WORK="$STATE/pmbootstrap-work"
# Read-only rc2 baseline: pinned hook/libcamera-rear/system libcamera inputs and
# the known-good rootfs used for the libcamera ABI comparison.
SRC_STATE="$ROOT/.work/rear-camera"
SRC_OUT="$ROOT/build/rear-camera"
# Isolated rc3 kernel build (never the rear-camera defaults).
KERNEL_STATE="${NURA_KERNEL_STATE:-$ROOT/.work/rc3-kernel}"
KERNEL_OUT="${NURA_KERNEL_BUILD:-$ROOT/build/rc3-kernel}"
KERNEL_RECIPE="${NURA_KERNEL_RECIPE:-device/testing/rc3/linux-postmarketos-qcom-sm8150}"
OUT="$ROOT/build/plasma-mobile"
BOOT_INITRAMFS="$OUT/boot-initramfs"
NBD_PORT=10001
RELEASE=""
RELEASE_CANDIDATE=""
ARTIFACT_NAME=""
AUTHORIZED_KEY=""
AUTHORIZED_KEYS=""
INSTALL_CONFIG=""
MODEM_VERSION=""
LIMM_GLIB_VERSION=""
NURA_CLOCK_VERSION=""
KERNEL_SOURCE=""
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG=""
TMP="$(mktemp -d "${TMPDIR:-/tmp}/plasma-prepare.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

MODE=check
RESTORE_FROM=""
while (( $# )); do
    case "$1" in
        --check-only) MODE=check ;;
        --build) MODE=build ;;
        --authorized-key)
            (( $# >= 2 )) && [[ -n "$2" ]] || {
                echo "error: --authorized-key needs an OpenSSH public-key file" >&2
                exit 2
            }
            AUTHORIZED_KEY=$2
            shift
            ;;
        --authorized-key=*)
            AUTHORIZED_KEY=${1#*=}
            [[ -n "$AUTHORIZED_KEY" ]] || {
                echo "error: --authorized-key needs an OpenSSH public-key file" >&2
                exit 2
            }
            ;;
        --restore)
            (( $# >= 2 )) || {
                echo "error: --restore needs a rollback directory" >&2
                exit 2
            }
            MODE=restore
            RESTORE_FROM=$2
            shift
            ;;
        --restore=*)
            MODE=restore
            RESTORE_FROM=${1#*=}
            ;;
        -h | --help)
            sed -n '2,19p' "$0"
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1 (use --check-only, --build [--authorized-key PATH] or --restore DIR)" >&2
            exit 2
            ;;
    esac
    shift
done

if [[ -n "$AUTHORIZED_KEY" ]]; then
    [[ "$MODE" = build ]] || {
        echo "error: --authorized-key is only valid with --build" >&2
        exit 2
    }
fi

die() {
    echo "ERROR: $*" >&2
    exit 1
}

# Refuse a symlinked top-level state/output before any write.  The deeper
# checks remain, but this is the earliest possible isolation guard.
for path in "$STATE" "$OUT"; do
    if [[ -L "$path" ]]; then
        die "refusing a symlinked Plasma path: $path -> $(readlink -- "$path")"
    fi
done

note() {
    echo "  $*"
}

warn() {
    echo "WARNING: $*" >&2
}

sha256() {
    sha256sum "$1" | awk '{ print $1 }'
}

json_get() {
    python3 - "$TMP/source.json" "$1" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))[sys.argv[2]])
PY
}

host_commands=(git findmnt sha256sum sha512sum python3 cpio gzip xz zstd realpath stat install cp find awk sed df)
missing=()
for command in "${host_commands[@]}"; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
if (( ${#missing[@]} )); then
    die "missing host commands: ${missing[*]}"
fi
if [[ "$MODE" = build ]]; then
    command -v sudo >/dev/null 2>&1 || die "sudo is required for pmbootstrap build/install"
    command -v modinfo >/dev/null 2>&1 || die "modinfo is required to verify installed modules"
elif [[ "$MODE" = restore ]]; then
    command -v sudo >/dev/null 2>&1 || die "sudo is required to install the restored rootfs image"
fi
note "host commands: OK"

RELEASE_CANDIDATE="$(python3 "$ROOT/scripts/check_plasma_candidate.py" release-candidate)" ||
    die "cannot read the release-candidate label from check_plasma_candidate.py"
[[ "$RELEASE_CANDIDATE" =~ ^nura-plasma-rc[0-9]+$ ]] ||
    die "invalid release-candidate label: $RELEASE_CANDIDATE"
RELEASE="$(python3 "$ROOT/scripts/check_plasma_candidate.py" kernel-release)" ||
    die "cannot read the kernel release from check_plasma_candidate.py"
[[ "$RELEASE" =~ ^7\.0\.0-sm8150-(rc|rear)[0-9]+$ ]] ||
    die "invalid kernel release: $RELEASE"
ARTIFACT_NAME="$RELEASE_CANDIDATE-oneplus-guacamole"
LOG="$STATE/rootfs/$RELEASE_CANDIDATE-package-$STAMP.log"

if [[ -n "$AUTHORIZED_KEY" ]]; then
    AUTHORIZED_KEYS="$TMP/authorized_keys"
    python3 "$ROOT/scripts/check_plasma_candidate.py" authorized-key \
        --public-key "$AUTHORIZED_KEY" --output "$AUTHORIZED_KEYS" \
        > "$TMP/authorized-key.json" ||
        die "authorized public key validation failed; nothing was prepared"
    note "authorized public key(s): $(python3 - "$TMP/authorized-key.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding='utf-8'))['key_count'])
PY
) validated; pmbootstrap install will add them to the new rootfs"
fi

# ---------------------------------------------------------------------------
# Read-only guards
# ---------------------------------------------------------------------------

refuse_active_mounts() {
    local root label mounts target
    root="$(realpath -m -- "$1")"
    label="$2"
    if ! mounts="$(findmnt -rn -o TARGET)"; then
        die "findmnt failed; cannot prove $label has no active mounts"
    fi
    while IFS= read -r target; do
        [[ -n "$target" ]] || continue
        case "${target%/}/" in
            "$root" | "$root"/*)
                printf 'ERROR: refusing to use %s while mounts are active:\n' "$label" >&2
                findmnt -rn -o TARGET --submounts "$root" >&2 || true
                exit 1
                ;;
        esac
    done <<< "$mounts"
}

port_busy() {
    local output
    if command -v ss >/dev/null 2>&1; then
        output="$(ss -H -ltn "sport = :$NBD_PORT" 2>/dev/null)" ||
            die "ss failed; cannot inspect TCP port $NBD_PORT"
        [[ -n "$output" ]]
        return
    fi
    [[ -r /proc/net/tcp ]] || die "neither ss nor /proc/net/tcp can inspect port $NBD_PORT"
    local hex files=(/proc/net/tcp)
    [[ -r /proc/net/tcp6 ]] && files+=(/proc/net/tcp6)
    printf -v hex '%04X' "$NBD_PORT"
    awk -v port=":$hex" '$2 ~ port "$" && $4 == "0A" { found=1 } END { exit !found }' \
        "${files[@]}" 2>/dev/null
}

if port_busy; then
    warn "TCP port $NBD_PORT is already listening; cleanup requires this phone in Android/fastboot"
fi
if [[ "$MODE" != check ]]; then
    bash "$ROOT/scripts/plasma_shutdown.sh" || die "could not clear leftover Plasma mounts"
fi
refuse_active_mounts "$STATE" "Plasma candidate state"
refuse_active_mounts "$OUT" "Plasma candidate output"

# ---------------------------------------------------------------------------
# Previous-candidate preservation and rollback
# ---------------------------------------------------------------------------
# pmbootstrap install replaces the chroot rootfs image in place.  Preserving
# only boot.img/manifests is not a rollback: the old rootfs and the initramfs
# that serves it would be gone.  This function copies a coherent, servable
# snapshot before any build step can touch the work/export/output paths.

rootfs_image_path() {
    printf '%s' "$WORK/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
}

rootfs_export_path() {
    printf '%s' "$STATE/rootfs/export"
}

export_inventory() {
    python3 - "$1" <<'PY'
import hashlib, json, os, sys
root = sys.argv[1]
entries = []
for directory, dirs, files in os.walk(root, followlinks=False):
    dirs.sort()
    files.sort()
    for name in dirs + files:
        path = os.path.join(directory, name)
        relative = os.path.relpath(path, root)
        if os.path.islink(path):
            entries.append([relative, "link", os.readlink(path)])
        elif os.path.isfile(path):
            with open(path, "rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            entries.append([relative, "file", digest])
        elif os.path.isdir(path):
            entries.append([relative, "directory", ""])
        else:
            raise SystemExit(f"unsupported export entry: {path}")
print(json.dumps(entries, separators=(",", ":")))
PY
}

# A rollback belongs to the candidate being replaced, so its module tree is
# named by that candidate's own kernel.release, never by the new global RELEASE.
# Copies keep a failed output replacement from orphaning the old vmlinuz/DTB/
# CCI/wrapper; full module trees are not needed to serve or boot.
rollback_key_files() {
    local release="$1"
    [[ "$release" =~ ^7\.0\.0-sm8150-(rc|rear)[0-9]+$ ]] ||
        { echo "Invalid rollback kernel release: $release" >&2; return 1; }
    printf '%s\n' \
        "Image" \
        "kernel-package/boot/vmlinuz" \
        "kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
        "kernel-package/usr/lib/modules/$release/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst" \
        "kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" \
        "temporary-dtbo/custom_guacamole-abl-quarantine.dtb" \
        "temporary-dtbo/manifest.json"
}

# Read the release a candidate directory describes from its own self-describing
# files.  Only the explicit --restore path consumes an old manifest; normal
# builds always use the current rc3 constants.
candidate_release() {
    local dir="$1" release=''
    if [[ -f "$dir/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" ]]; then
        release="$(tr -d '[:space:]' < "$dir/kernel-package/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release")"
    fi
    if [[ -z "$release" ]]; then
        release="$(python3 - "$dir" <<'PY'
import json, sys
from pathlib import Path
directory = Path(sys.argv[1])
value = None
for name in ("ROLLBACK.json", "DELIVERY.json", "packaging-inputs.json"):
    path = directory / name
    if not path.is_file():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read {path}: {error}")
    candidate = data.get("release")
    if candidate is None:
        continue
    value = candidate
    break
print(value or "")
PY
)"
    fi
    [[ "$release" =~ ^7\.0\.0-sm8150-(rc|rear)[0-9]+$ ]] ||
        { echo "Cannot determine a valid kernel release for $dir (got '${release:-none}')" >&2; return 1; }
    printf '%s' "$release"
}

previous_candidate_label() {
    # The rollback belongs to the candidate that is being replaced, not to the
    # candidate this build is producing.  Read its delivered label; never
    # rename an old rc1 output as rc2 just because the build constant changed.
    python3 - "$OUT" <<'PY'
import json, re, sys
from pathlib import Path
out = Path(sys.argv[1])
label = None
for name in ("DELIVERY.json", "packaging-inputs.json"):
    path = out / name
    if not path.is_file():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read previous candidate label from {path}: {error}")
    value = data.get("release_candidate")
    if value is None:
        continue
    if not isinstance(value, str) or not re.fullmatch(r"nura-plasma-rc[0-9]+", value):
        raise SystemExit(f"previous candidate label in {path} is invalid: {value!r}")
    label = value
    break
print(label or "unlabeled")
PY
}

preserve_previous_output() {
    local name rollback staging rootfs_image export_dir export_initramfs path
    local live_before live_after export_before export_after prior_candidate prior_artifact prior_release
    local total=0 size required_kb available_kb
    local copy_failed=0
    local -a files=() checksum_files=() key_files=()
    ROLLBACK_DIR=""
    [[ -e "$OUT" ]] || return 0
    [[ -L "$OUT" ]] && die "candidate output must not be a symlink: $OUT"
    [[ -d "$OUT" ]] || die "candidate output is not a directory: $OUT"
    prior_candidate="$(previous_candidate_label)" ||
        die "cannot determine the previous candidate label; no file was changed"
    [[ "$prior_candidate" = unlabeled ]] && prior_artifact="" || prior_artifact="$prior_candidate-oneplus-guacamole"
    prior_release="$(candidate_release "$OUT")" ||
        die "cannot determine the previous kernel release; no file was changed"
    mapfile -t key_files < <(rollback_key_files "$prior_release")
    rootfs_image="$(rootfs_image_path)"
    export_dir="$(rootfs_export_path)"
    export_initramfs="$export_dir/initramfs"

    for name in boot.img boot-initramfs DELIVERY.json packaging-inputs.json APK-SHA256SUMS; do
        [[ -L "$OUT/$name" ]] && die "previous candidate file must not be a symlink: $OUT/$name"
        [[ -f "$OUT/$name" ]] && files+=("$name")
    done
    for name in "${key_files[@]}"; do
        [[ -L "$OUT/$name" ]] && die "previous candidate file must not be a symlink: $OUT/$name"
        [[ -f "$OUT/$name" ]] && files+=("$name")
    done
    for path in "$rootfs_image" "$export_dir" "$export_initramfs"; do
        [[ -L "$path" ]] && die "previous candidate path must not be a symlink: $path"
    done

    # A failed packaging run may zap the old export after saving its rollback.
    # Reuse only a complete, verified snapshot of these same published files.
    if [[ -f "$OUT/DELIVERY.json" && ( ! -f "$rootfs_image" || ! -f "$export_initramfs" ) ]]; then
        for rollback in "$OUT"/rollback-"$prior_candidate"-*; do
            [[ -d "$rollback" && ! -L "$rollback" ]] || continue
            [[ -f "$rollback/ROLLBACK-SHA256SUMS" &&
               -f "$rollback/rootfs.img" && ! -L "$rollback/rootfs.img" &&
               -f "$rollback/rootfs-initramfs" && ! -L "$rollback/rootfs-initramfs" ]] || continue
            copy_failed=0
            for name in "${files[@]}"; do
                cmp -s -- "$OUT/$name" "$rollback/$name" || { copy_failed=1; break; }
            done
            (( copy_failed == 0 )) || continue
            # Require the essential payloads to be covered, not merely present.
            for name in rootfs.img rootfs-initramfs DELIVERY.json boot.img; do
                awk -v name="$name" '$2 == name { found=1 } END { exit !found }' \
                    "$rollback/ROLLBACK-SHA256SUMS" || { copy_failed=1; break; }
            done
            (( copy_failed == 0 )) || continue
            (cd "$rollback" && sha256sum -c ROLLBACK-SHA256SUMS >/dev/null 2>&1) || continue
            if [[ -d "$rollback/rootfs-export" ]]; then
                [[ -f "$rollback/ROOTFS-EXPORT-MANIFEST" ]] || continue
                [[ "$(export_inventory "$rollback/rootfs-export")" = "$(cat -- "$rollback/ROOTFS-EXPORT-MANIFEST")" ]] || continue
            fi
            ROLLBACK_DIR="$rollback"
            note "Reusing verified previous $prior_candidate rollback: $rollback"
            return 0
        done
    fi

    # A previous DELIVERY.json claims to be bootable.  Do not start a build
    # that would overwrite its only rootfs/initramfs without a servable copy.
    if [[ -f "$OUT/DELIVERY.json" ]]; then
        [[ -f "$rootfs_image" ]] ||
            die "previous candidate manifest exists but its rootfs image is missing: $rootfs_image; refusing to overwrite the only rollback"
        [[ -f "$export_initramfs" ]] ||
            die "previous candidate manifest exists but its rootfs initramfs is missing: $export_initramfs; refusing to overwrite the only rollback"
    fi
    if (( ${#files[@]} == 0 )) && [[ ! -f "$rootfs_image" ]] && [[ ! -e "$export_dir" ]]; then
        return 0
    fi

    # Conservative free-space check: worst case is a full copy even when the
    # filesystem can reflink.  Fail before creating anything if it cannot fit.
    for name in "${files[@]}"; do
        total=$((total + $(stat -c %s -- "$OUT/$name")))
    done
    for path in "$rootfs_image" "$export_initramfs"; do
        [[ -f "$path" ]] && total=$((total + $(stat -c %s -- "$path")))
    done
    if [[ -d "$export_dir" ]]; then
        while IFS= read -r size; do
            total=$((total + size))
        done < <(find "$export_dir" -type f -printf '%s\n')
    fi
    required_kb=$(( (total + 1023) / 1024 + 65536 ))
    available_kb="$(df -Pk -- "$OUT" | awk 'NR == 2 { print $4 }')"
    [[ "$available_kb" =~ ^[0-9]+$ ]] ||
        die "cannot read free space for $OUT; no file was changed"
    (( available_kb >= required_kb )) ||
        die "not enough free space for a rollback copy: need at least ${required_kb} KiB, ${available_kb} KiB available; no file was changed"

    rollback="$OUT/rollback-$prior_candidate-$STAMP"
    staging="$OUT/.rollback-$prior_candidate-$STAMP.partial"
    [[ ! -e "$rollback" && ! -L "$rollback" ]] || die "rollback path already exists: $rollback"
    [[ ! -e "$staging" && ! -L "$staging" ]] || die "partial rollback path already exists: $staging"
    live_before="$(stat -c '%d:%i:%s:%y' -- "$rootfs_image" 2>/dev/null || true)"
    export_before="$(stat -c '%d:%i:%s:%y' -- "$export_initramfs" 2>/dev/null || true)"
    mkdir -m 700 -- "$staging" || die "could not create rollback staging directory: $staging"

    for name in "${files[@]}"; do
        mkdir -p -- "$(dirname -- "$staging/$name")" || { copy_failed=1; break; }
        cp -a --reflink=auto -- "$OUT/$name" "$staging/$name" || { copy_failed=1; break; }
        checksum_files+=("$name")
    done
    if (( ! copy_failed )) && [[ -f "$rootfs_image" ]]; then
        cp -a --reflink=auto -- "$rootfs_image" "$staging/rootfs.img" || copy_failed=1
        checksum_files+=("rootfs.img")
    fi
    if (( ! copy_failed )) && [[ -f "$export_initramfs" ]]; then
        cp -a --reflink=auto -- "$export_initramfs" "$staging/rootfs-initramfs" || copy_failed=1
        checksum_files+=("rootfs-initramfs")
    fi
    if (( ! copy_failed )) && [[ -d "$export_dir" ]]; then
        cp -a --reflink=auto -- "$export_dir" "$staging/rootfs-export" || copy_failed=1
        if [[ -f "$staging/rootfs-export/initramfs" ]]; then
            checksum_files+=("rootfs-export/initramfs")
        fi
        if (( ! copy_failed )); then
            export_inventory "$staging/rootfs-export" > "$staging/ROOTFS-EXPORT-MANIFEST" || copy_failed=1
            checksum_files+=("ROOTFS-EXPORT-MANIFEST")
        fi
    fi
    if (( ! copy_failed )) && [[ -f "$STATE/rootfs/candidate-state.json" ]]; then
        cp -a --reflink=auto -- "$STATE/rootfs/candidate-state.json" \
            "$staging/candidate-state.json" || copy_failed=1
        checksum_files+=("candidate-state.json")
    fi
    if (( copy_failed )); then
        rm -rf -- "$staging"
        die "could not copy the previous candidate into $staging; no existing file was changed"
    fi
    if [[ -f "$rootfs_image" ]]; then
        live_after="$(stat -c '%d:%i:%s:%y' -- "$rootfs_image" 2>/dev/null || true)"
        export_after="$(stat -c '%d:%i:%s:%y' -- "$export_initramfs" 2>/dev/null || true)"
        if [[ "$live_before" != "$live_after" || "$export_before" != "$export_after" ]]; then
            rm -rf -- "$staging"
            die "previous rootfs or rootfs initramfs changed while it was being preserved; stop all writers and retry"
        fi
    fi

    python3 - "$staging/ROLLBACK.json" "$prior_release" "$prior_candidate" \
        "$prior_artifact" "$RELEASE_CANDIDATE" "$STAMP" "$rootfs_image" "$export_initramfs" "$OUT" <<'PY'
import json, sys
out, release, candidate, artifact, replacing, stamp, rootfs, initramfs, output = sys.argv[1:]
with open(out, "w", encoding="utf-8") as stream:
    json.dump({
        "release": release,
        "kernel_release": release,
        "release_candidate": None if candidate == "unlabeled" else candidate,
        "artifact_name": artifact or None,
        "replaced_by_release_candidate": replacing,
        "created_at": stamp,
        "rootfs_image": rootfs,
        "rootfs_initramfs": initramfs,
        "output": output,
        "restore": f'bash scripts/rootfs_prepare_plasma.sh --restore "{output}/rollback-{candidate}-{stamp}"',
    }, stream, indent=2)
    stream.write("\n")
PY
    checksum_files+=("ROLLBACK.json")
    cat > "$staging/RESTORE.txt" <<EOF
Previous $prior_candidate candidate preserved before a $RELEASE_CANDIDATE build.
Restore it as your normal user, with the NBD server stopped. The script uses
sudo only for the rootfs image in pmbootstrap's protected parent directory:
  bash scripts/rootfs_prepare_plasma.sh --restore "$rollback"
Then validate and serve the restored rootfs:
  bash scripts/plasma_serve.sh --check
  bash scripts/plasma_serve.sh --replace
EOF
    checksum_files+=("RESTORE.txt")
    (cd "$staging" && sha256sum "${checksum_files[@]}" > ROLLBACK-SHA256SUMS) || {
        rm -rf -- "$staging"
        die "could not record rollback checksums; no existing file was changed"
    }
    mv -- "$staging" "$rollback" || die "could not publish rollback directory: $rollback"
    ROLLBACK_DIR="$rollback"
    printf 'Previous %s candidate preserved in %s\n' "$prior_candidate" "$rollback"
    printf '  rootfs:            %s\n' "$rootfs_image"
    printf '  rootfs initramfs:  %s\n' "$export_initramfs"
}

restore_previous() {
    local requested="$1" rollback rootfs_image rootfs_parent export_dir name restored_candidate
    local restore_release
    local -a key_files=()
    local stage_out stage_rootfs stage_export aside rootfs_install rootfs_before rootfs_after
    local rootfs_mode rootfs_uid rootfs_gid rootfs_size current_size state_device parent_device
    local state_required parent_required available_kb state_fs_path
    [[ -n "$requested" ]] || die "--restore needs a rollback directory"
    rollback="$(realpath -e -- "$requested")" || die "rollback directory does not exist: $requested"
    [[ -d "$rollback" && ! -L "$rollback" ]] || die "rollback must be a real directory: $rollback"
    [[ -f "$rollback/ROLLBACK-SHA256SUMS" ]] || die "rollback has no ROLLBACK-SHA256SUMS: $rollback"
    (cd "$rollback" && sha256sum -c ROLLBACK-SHA256SUMS >/dev/null) ||
        die "rollback checksum verification failed; nothing was changed"
    # The old manifest is self-describing; accept it only on this explicit path.
    restore_release="$(candidate_release "$rollback")" ||
        die "rollback does not describe a valid kernel release; nothing was changed"
    mapfile -t key_files < <(rollback_key_files "$restore_release")
    if [[ -d "$rollback/rootfs-export" ]]; then
        [[ -f "$rollback/ROOTFS-EXPORT-MANIFEST" ]] ||
            die "rollback has no rootfs-export inventory: $rollback"
        [[ "$(export_inventory "$rollback/rootfs-export")" = "$(cat -- "$rollback/ROOTFS-EXPORT-MANIFEST")" ]] ||
            die "rollback rootfs export differs from its inventory; nothing was changed"
    fi
    [[ -f "$rollback/rootfs.img" && ! -L "$rollback/rootfs.img" ]] ||
        die "rollback has no real rootfs.img: $rollback"
    [[ -f "$rollback/rootfs-initramfs" && ! -L "$rollback/rootfs-initramfs" ]] ||
        die "rollback has no real rootfs-initramfs: $rollback"
    for name in boot.img boot-initramfs DELIVERY.json; do
        [[ -f "$rollback/$name" && ! -L "$rollback/$name" ]] ||
            die "rollback is incomplete, missing $name: $rollback"
    done
    [[ -L "$OUT" ]] && die "candidate output must not be a symlink: $OUT"
    rootfs_image="$(rootfs_image_path)"
    export_dir="$(rootfs_export_path)"
    [[ -L "$rootfs_image" || -L "$export_dir" ]] &&
        die "restore target must not be a symlink: $rootfs_image or $export_dir"
    rootfs_parent="$(dirname -- "$rootfs_image")"
    [[ -d "$rootfs_parent" && "$(realpath -e -- "$rootfs_parent")" = "$rootfs_parent" ]] ||
        die "restore rootfs parent is missing or resolves through a symlink: $rootfs_parent"
    [[ ! -e "$rootfs_image" || -f "$rootfs_image" ]] ||
        die "restore rootfs target is not a regular file: $rootfs_image"

    mkdir -p -- "$OUT" "$STATE/rootfs" ||
        die "could not create restore target directories"
    stage_out="$OUT/.restore-$STAMP"
    stage_rootfs="$STATE/rootfs/.oneplus-guacamole.img.restore-$STAMP"
    stage_export="$STATE/rootfs/.export.restore-$STAMP"
    aside="$STATE/rootfs/restore-before-$STAMP"
    rootfs_install="$rootfs_parent/.oneplus-guacamole.img.restore-$STAMP"
    for path in "$stage_out" "$stage_rootfs" "$stage_export" "$aside"; do
        [[ ! -e "$path" && ! -L "$path" ]] || die "restore staging path already exists: $path"
    done
    [[ ! -e "$rootfs_install" && ! -L "$rootfs_install" ]] ||
        die "privileged rootfs staging path already exists: $rootfs_install"
    rootfs_size="$(stat -c %s -- "$rollback/rootfs.img")"
    current_size=0
    [[ ! -f "$rootfs_image" ]] || current_size="$(stat -c %s -- "$rootfs_image")"
    state_required=$(( (rootfs_size + current_size + 65536 * 1024 + 1023) / 1024 ))
    parent_required=$(( (rootfs_size + 65536 * 1024 + 1023) / 1024 ))
    state_fs_path="$STATE/rootfs"
    [[ -d "$state_fs_path" ]] || state_fs_path="$STATE"
    state_device="$(stat -c %d -- "$state_fs_path")"
    parent_device="$(stat -c %d -- "$rootfs_parent")"
    if [[ "$state_device" = "$parent_device" ]]; then
        state_required=$((state_required + parent_required))
    fi
    for path in "$state_fs_path" "$rootfs_parent"; do
        available_kb="$(df -Pk -- "$path" | awk 'NR == 2 { print $4 }')"
        [[ "$available_kb" =~ ^[0-9]+$ ]] || die "cannot read free space for restore target: $path"
        if [[ "$path" = "$state_fs_path" ]]; then
            (( available_kb >= state_required )) ||
                die "not enough space to stage and preserve rootfs for restore in $path; nothing was changed"
        else
            (( available_kb >= parent_required )) ||
                die "not enough space to install restored rootfs in $path; nothing was changed"
        fi
    done
    mkdir -m 700 -- "$stage_out" || die "could not create restore output staging"
    for name in boot.img boot-initramfs DELIVERY.json packaging-inputs.json APK-SHA256SUMS candidate-state.json "${key_files[@]}"; do
        [[ -f "$rollback/$name" ]] || continue
        mkdir -p -- "$(dirname -- "$stage_out/$name")" || {
            rm -rf -- "$stage_out"
            die "could not create staging directory for $name"
        }
        cp -a --reflink=auto -- "$rollback/$name" "$stage_out/$name" || {
            rm -rf -- "$stage_out"
            die "could not stage $name from the rollback"
        }
    done
    cp -a --reflink=auto -- "$rollback/rootfs.img" "$stage_rootfs" || {
        rm -rf -- "$stage_out" "$stage_rootfs"
        die "could not stage the rollback rootfs image"
    }
    if [[ -d "$rollback/rootfs-export" ]]; then
        cp -a --reflink=auto -- "$rollback/rootfs-export" "$stage_export" || {
            rm -rf -- "$stage_out" "$stage_rootfs" "$stage_export"
            die "could not stage the rollback export directory"
        }
    else
        mkdir -m 700 -- "$stage_export" || die "could not create restore export staging"
        cp -a --reflink=auto -- "$rollback/rootfs-initramfs" "$stage_export/initramfs" || {
            rm -rf -- "$stage_out" "$stage_rootfs" "$stage_export"
            die "could not stage the rollback rootfs initramfs"
        }
    fi

    mkdir -p -- "$aside/out" || die "could not create the current-attempt backup: $aside"
    if [[ -e "$rootfs_image" ]]; then
        [[ -f "$rootfs_image" && ! -L "$rootfs_image" &&
            "$(realpath -e -- "$rootfs_parent")" = "$rootfs_parent" ]] ||
            die "current rootfs path changed during restore"
        rootfs_before="$(stat -c '%d:%i:%s:%y' -- "$rootfs_image")" ||
            die "cannot identify the current rootfs image"
        sudo cp -a -- "$rootfs_image" "$aside/oneplus-guacamole.img" ||
            die "could not preserve the current rootfs image in $aside"
        [[ -f "$aside/oneplus-guacamole.img" && ! -L "$aside/oneplus-guacamole.img" ]] ||
            die "current rootfs backup is not a regular file: $aside/oneplus-guacamole.img"
        rootfs_after="$(stat -c '%d:%i:%s:%y' -- "$rootfs_image")" ||
            die "cannot recheck the current rootfs image"
        [[ "$rootfs_before" = "$rootfs_after" ]] ||
            die "current rootfs changed while being preserved; nothing was replaced"
        rootfs_mode="$(stat -c '%a' -- "$rootfs_image")"
        rootfs_uid="$(stat -c '%u' -- "$rootfs_image")"
        rootfs_gid="$(stat -c '%g' -- "$rootfs_image")"
    else
        rootfs_mode=644
        rootfs_uid=0
        rootfs_gid="$(stat -c '%g' -- "$rootfs_parent")"
    fi
    for name in boot.img boot-initramfs DELIVERY.json packaging-inputs.json APK-SHA256SUMS "${key_files[@]}"; do
        [[ -e "$OUT/$name" ]] || continue
        mkdir -p -- "$aside/out/$(dirname -- "$name")"
        mv -- "$OUT/$name" "$aside/out/$name"
    done
    [[ -e "$export_dir" ]] && mv -- "$export_dir" "$aside/export"
    [[ -e "$STATE/rootfs/candidate-state.json" ]] &&
        mv -- "$STATE/rootfs/candidate-state.json" "$aside/candidate-state.json"

    for name in boot.img boot-initramfs DELIVERY.json packaging-inputs.json APK-SHA256SUMS "${key_files[@]}"; do
        [[ -f "$stage_out/$name" ]] || continue
        mkdir -p -- "$(dirname -- "$OUT/$name")"
        mv -- "$stage_out/$name" "$OUT/$name"
    done
    [[ "$(realpath -e -- "$rootfs_parent")" = "$rootfs_parent" && ! -e "$rootfs_install" && ! -L "$rootfs_install" ]] ||
        die "protected rootfs staging path changed during restore"
    if ! sudo install -m "$rootfs_mode" -o "$rootfs_uid" -g "$rootfs_gid" \
        -- "$stage_rootfs" "$rootfs_install"; then
        die "could not stage restored rootfs in its protected parent; current attempt is in $aside"
    fi
    [[ "$(stat -c '%s' -- "$rootfs_install")" = "$(stat -c '%s' -- "$stage_rootfs")" ]] || {
        sudo rm -f -- "$rootfs_install"
        die "privileged rootfs staging size differs; current attempt is in $aside"
    }
    [[ "$(realpath -e -- "$rootfs_parent")" = "$rootfs_parent" && ! -L "$rootfs_image" ]] || {
        sudo rm -f -- "$rootfs_install"
        die "protected rootfs install target changed during restore"
    }
    if ! sudo mv -f -- "$rootfs_install" "$rootfs_image"; then
        sudo rm -f -- "$rootfs_install"
        die "could not install the restored rootfs image; the current attempt is in $aside"
    fi
    rm -f -- "$stage_rootfs"
    if ! mv -- "$stage_export" "$export_dir"; then
        [[ -e "$aside/export" && ! -e "$export_dir" ]] && mv -- "$aside/export" "$export_dir"
        die "could not install the restored export directory; the current attempt is in $aside"
    fi
    if [[ -f "$stage_out/candidate-state.json" ]]; then
        mv -- "$stage_out/candidate-state.json" "$STATE/rootfs/candidate-state.json"
    fi
    for name in "${key_files[@]}"; do
        [[ -f "$rollback/$name" ]] || continue
        [[ -f "$OUT/$name" ]] || die "restore did not install $OUT/$name"
    done
    rm -rf -- "$stage_out"
    restored_candidate="$(python3 - "$rollback" <<'PY'
import json, re, sys
from pathlib import Path
rollback = Path(sys.argv[1])
label = None
for name in ("ROLLBACK.json", "DELIVERY.json", "packaging-inputs.json"):
    path = rollback / name
    if not path.is_file():
        continue
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("release_candidate")
    except (OSError, ValueError) as error:
        raise SystemExit(f"cannot read restored candidate label from {path}: {error}")
    if value is None:
        continue
    if not isinstance(value, str) or not re.fullmatch(r"nura-plasma-rc[0-9]+", value):
        raise SystemExit(f"restored candidate label in {path} is invalid: {value!r}")
    label = value
    break
print(label or "previous candidate")
PY
)" || die "cannot read the restored candidate label"
    printf 'Restored %s from %s\n' "$restored_candidate" "$rollback"
    printf 'Current attempt preserved in %s\n' "$aside"
    printf 'Next: bash scripts/plasma_serve.sh --check; bash scripts/plasma_serve.sh --replace\n'
}

ROLLBACK_DIR=""
if [[ "$MODE" = restore ]]; then
    restore_previous "$RESTORE_FROM"
    exit 0
fi

refuse_active_mounts "$SRC_STATE/pmaports" "read-only pmaports source"
refuse_active_mounts "$SRC_STATE/pmbootstrap" "read-only pmbootstrap source"
refuse_active_mounts "$KERNEL_STATE" "rc3 kernel state"
refuse_active_mounts "$KERNEL_OUT" "rc3 kernel output"

# ---------------------------------------------------------------------------
# Source identity: the rc3 kernel build plus the read-only rc2 APK baseline
# ---------------------------------------------------------------------------

[[ -d "$KERNEL_OUT" ]] ||
    die "missing rc3 kernel build output: $KERNEL_OUT (run: bash scripts/rc3_build.sh kernel)"
[[ -f "$KERNEL_OUT/kernel-inputs.json" ]] ||
    die "missing $KERNEL_OUT/kernel-inputs.json; run: bash scripts/rc3_build.sh kernel"
[[ -d "$KERNEL_STATE" ]] || die "missing rc3 kernel state: $KERNEL_STATE"
compgen -G "$KERNEL_STATE/linux-*/Makefile" >/dev/null ||
    die "missing extracted rc3 kernel source under $KERNEL_STATE; run: bash scripts/rc3_build.sh kernel"
[[ -f "$KERNEL_OUT/kernel-package/boot/vmlinuz" ]] ||
    die "missing rc3 kernel package staging: $KERNEL_OUT/kernel-package"
[[ -d "$SRC_OUT" ]] || die "missing rc2 APK baseline output: $SRC_OUT"
[[ -f "$SRC_STATE/rootfs/pmbootstrap.cfg" ]] ||
    die "missing pinned pmbootstrap config: $SRC_STATE/rootfs/pmbootstrap.cfg"
[[ -d "$SRC_STATE/pmaports" ]] || die "missing pinned pmaports: $SRC_STATE/pmaports"
[[ -d "$SRC_STATE/pmbootstrap" ]] || die "missing pinned pmbootstrap: $SRC_STATE/pmbootstrap"
[[ -f "$SRC_STATE/pmaports/pmaports.cfg" ]] || die "missing pmaports.cfg"
grep -q '^version=7$' "$SRC_STATE/pmaports/pmaports.cfg" ||
    die "pinned pmaports is not version 7"
[[ -f "$SRC_STATE/pmbootstrap/pmbootstrap.py" ]] || die "pinned pmbootstrap is incomplete"
grep -q '^work_version = 8$' "$SRC_STATE/pmbootstrap/pmb/config/__init__.py" 2>/dev/null ||
    die "pinned pmbootstrap does not use work_version 8"
[[ -f "$ROOT/device/testing/rc3/modemmanager/APKBUILD" ]] ||
    die "missing rc3 modemmanager recipe: device/testing/rc3/modemmanager/APKBUILD (wait for the modem worker report)"
[[ -f "$ROOT/$KERNEL_RECIPE/APKBUILD" ]] ||
    die "missing rc3 kernel recipe: $KERNEL_RECIPE/APKBUILD (wait for the kernel worker report)"

python3 "$ROOT/scripts/check_plasma_candidate.py" source \
    --source-build "$KERNEL_OUT" --source-state "$KERNEL_STATE" \
    --baseline-build "$SRC_OUT" --baseline-state "$SRC_STATE" > "$TMP/source.json" ||
    die "rc3 kernel build or rc2 baseline pins failed validation"
note "rc3 kernel recipe/staging and rc2 APK baseline pins: OK"
note "rc3 kernel release: $RELEASE"
note "rc3 kernel package: $(json_get vmlinuz_sha256)"
note "rc3 module payload: $(json_get module_payload_sha256) ($(json_get module_count) modules)"
note "pinned netboot hook/libcamera-rear/system libcamera APKs: OK"

if [[ "$MODE" = check ]]; then
    boot_line="not packed yet (run --build)"
    if [[ -f "$KERNEL_OUT/DELIVERY.json" ]]; then
        boot_line="$(json_get boot_img_sha256)"
    fi
    cat <<EOF

checks passed; no chroot, build, mount or image command was run.
The rc3 Plasma candidate builds a new boot chain on TCP port $NBD_PORT:

  release candidate: $RELEASE_CANDIDATE (kernel $RELEASE)
  artifact:  $ARTIFACT_NAME
  boot:      $boot_line
  kernel:    $(json_get vmlinuz_sha256)
  DTB:       $(json_get dtb_sha256)
  rollback:  the current build/plasma-mobile candidate is preserved before --build

Next (user-run):
  bash scripts/rc3_build.sh check      # full rc3 preflight (recipes, disk, inputs)
  bash scripts/rc3_build.sh build      # kernel + packages + rootfs + boot + manifest
EOF
    exit 0
fi

# ---------------------------------------------------------------------------
# Build-only preparation
# ---------------------------------------------------------------------------

mkdir -p "$STATE/rootfs"
exec > >(tee -a "$LOG") 2>&1
trap 'status=$?; echo "Plasma packaging failed at line $LINENO (exit $status). Log: $LOG" >&2; exit "$status"' ERR
echo "Build log: $LOG"

[[ -f "$SRC_STATE/pmbootstrap/pmbootstrap.py" ]] || die "pinned pmbootstrap disappeared"
[[ -f "$SRC_STATE/pmaports/temp/libcamera/APKBUILD" ]] ||
    die "pinned pmaports has no system libcamera 0.8 recipe"

prepare_work() {
    local version backup
    if [[ -L "$WORK" ]]; then
        die "candidate work dir must not be a symlink: $WORK"
    fi
    if [[ -d "$WORK" ]]; then
        refuse_active_mounts "$WORK" "Plasma pmbootstrap work dir"
        if [[ -f "$WORK/version" ]]; then
            version="$(cat "$WORK/version")"
            [[ "$version" = 8 ]] ||
                die "pmbootstrap work version '$version' in $WORK is not 8; refusing to migrate or delete it"
        elif [[ -n "$(ls -A "$WORK")" ]]; then
            backup="$WORK.partial-$STAMP"
            [[ ! -e "$backup" ]] || die "partial-work backup path already exists: $backup"
            mv -- "$WORK" "$backup"
            printf 'Preserved incomplete pmbootstrap work directory at %s\n' "$backup"
            printf 'The old tree was not deleted, migrated, chowned or entered.\n'
        fi
    fi
    mkdir -p -m 700 "$WORK/cache_git"
    [[ -f "$WORK/version" ]] || printf '8\n' > "$WORK/version"
}

prepare_pmaports_copy() {
    [[ -L "$STATE/pmaports" ]] && die "candidate pmaports must not be a symlink"
    if [[ -e "$STATE/pmaports" ]]; then
        [[ -d "$STATE/pmaports" ]] || die "candidate pmaports is not a directory"
        [[ -f "$STATE/rootfs/candidate-state.json" ]] ||
            die "candidate pmaports exists without a preparation receipt; move it aside instead of reusing a partial copy"
        return 0
    fi
    note "copying pinned pmaports into the isolated Plasma state"
    cp -a --reflink=auto "$SRC_STATE/pmaports" "$STATE/pmaports" ||
        die "could not copy pmaports; the source tree was not modified"
}

prepare_pmbootstrap_copy() {
    [[ -L "$STATE/pmbootstrap" ]] && die "candidate pmbootstrap must not be a symlink"
    if [[ -e "$STATE/pmbootstrap" ]]; then
        [[ -d "$STATE/pmbootstrap" ]] || die "candidate pmbootstrap is not a directory"
    else
        note "copying pinned pmbootstrap into the isolated Plasma state"
        cp -a --reflink=auto "$SRC_STATE/pmbootstrap" "$STATE/pmbootstrap" ||
            die "could not copy pmbootstrap; the source tree was not modified"
    fi
    [[ -f "$STATE/pmbootstrap/pmbootstrap.py" ]] || die "candidate pmbootstrap is incomplete"
    if [[ -d "$STATE/pmbootstrap/.git" ]]; then
        git -C "$STATE/pmbootstrap" remote set-url origin \
            https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git 2>/dev/null ||
            warn "could not set the candidate pmbootstrap origin; it is not used for builds"
    fi
}

prepare_export() {
    local export="$STATE/rootfs/export" archive link target
    local -a links=()
    [[ -e "$export" ]] || return 0
    [[ -L "$export" ]] && die "candidate export must not be a symlink: $export"
    archive="$STATE/rootfs/export.before-$STAMP"
    [[ ! -e "$archive" ]] || die "export backup path already exists: $archive"
    mv -- "$export" "$archive"
    mapfile -t links < <(find "$archive" -type l)
    for link in "${links[@]}"; do
        target="$(readlink "$link")"
        printf '%s -> %s\n' "$link" "$target" >> "$archive/EXPORT-TARGETS.txt"
    done
    printf 'Previous Plasma export preserved in %s\n' "$archive"
}

replace_dir() {
    local source="$1" destination="$2" label="$3" staging old
    [[ -d "$source" ]] || die "missing $label source: $source"
    [[ -L "$destination" ]] && die "$label destination must not be a symlink: $destination"
    staging="$destination.new.$$"
    old="$destination.old.$$"
    mkdir -p -- "$(dirname -- "$destination")"
    rm -rf -- "$staging"
    cp -a --reflink=auto "$source" "$staging" || die "could not copy $label"
    if [[ -e "$destination" ]]; then
        mv -- "$destination" "$old" || die "could not move previous $label aside"
    fi
    mv -- "$staging" "$destination" || die "could not install $label"
    rm -rf -- "$old"
}

copy_file() {
    local source="$1" destination="$2" label="$3" staging
    [[ -f "$source" ]] || die "missing $label source: $source"
    [[ -L "$destination" ]] && die "$label destination must not be a symlink: $destination"
    [[ -d "$destination" ]] && die "$label destination is a directory: $destination"
    staging="$destination.new.$$"
    mkdir -p -- "$(dirname -- "$destination")"
    cp -a --reflink=auto "$source" "$staging" || die "could not copy $label"
    mv -- "$staging" "$destination" || die "could not install $label"
}

KERNEL_PKG="linux-postmarketos-qcom-sm8150"
CHANNEL="$(sed -n 's/^channel=//p' "$SRC_STATE/pmaports/pmaports.cfg" | head -1)"
[[ -n "$CHANNEL" ]] || die "cannot read channel from pinned pmaports"
[[ "$CHANNEL" =~ ^[A-Za-z0-9._-]+$ ]] || die "unsafe pmaports channel name: $CHANNEL"
PKGDIR="$WORK/packages/$CHANNEL/aarch64"

seed_known_apk() {
    local name="$1" source="$2" expected="$3" version="$4" destination
    destination="$PKGDIR/$name-$version.apk"
    [[ ! -L "$destination" ]] || die "candidate APK must not be a symlink: $destination"
    if [[ -d "$PKGDIR" ]]; then
        local -a matches=()
        mapfile -t matches < <(find "$PKGDIR" -maxdepth 1 -type f -name "$name-[0-9]*.apk")
        if [[ "$name" = linux-postmarketos-qcom-sm8150 ]]; then
            local extra backup="$WORK/previous-apks/$STAMP"
            for extra in "${matches[@]}"; do
                [[ "$extra" = "$destination" ]] && continue
                [[ ! -L "$WORK/previous-apks" && ! -L "$backup" ]] || die "APK backup directory must not be a symlink"
                note "preserving unselected candidate kernel APK: ${extra##*/}"
                sudo install -d -m 755 "$backup"
                [[ ! -e "$backup/${extra##*/}" ]] || die "APK backup already exists"
                sudo mv -- "$extra" "$backup/" || die "could not preserve unselected kernel APK"
            done
            matches=()
            [[ ! -f "$destination" ]] || matches=("$destination")
        fi
        if (( ${#matches[@]} > 1 )); then
            die "multiple $name APKs already exist in $PKGDIR; preserve or remove them deliberately"
        fi
        if (( ${#matches[@]} == 1 )) && [[ "${matches[0]}" != "$destination" ]]; then
            die "a different $name APK already exists: ${matches[0]}"
        fi
    fi
    if [[ -f "$destination" ]]; then
        if [[ "$(sha256 "$destination")" = "$expected" ]]; then
            return 0
        fi
        case "$name" in
            libcamera|libcamera-ipa)
                python3 "$ROOT/scripts/check_plasma_candidate.py" apk-resign \
                    --source "$source" --sha256 "$expected" --candidate "$destination" ||
                    die "existing candidate $name APK payload differs from the pinned original"
                return 0
                ;;
            libcamera-rear|postmarketos-mkinitfs-hook-netboot)
                # Earlier Plasma preparation rebuilt these instead of reusing the proven APKs.
                local backup="$WORK/previous-apks/$STAMP"
                sudo install -d -m 755 "$backup" || die "could not preserve rebuilt candidate APK"
                sudo install -m 644 "$destination" "$backup/${destination##*/}" ||
                    die "could not preserve rebuilt candidate APK"
                ;;
            *) die "existing candidate $name APK differs from the known-good APK: $destination" ;;
        esac
    fi
    [[ -f "$source" ]] || die "missing known-good $name APK: $source"
    note "seeding known-good $name APK into the candidate package repo"
    sudo install -m 644 "$source" "$destination" ||
        die "could not install the known-good $name APK"
    [[ "$(sha256 "$destination")" = "$expected" ]] ||
        die "seeded $name APK hash mismatch"
}

seed_public_keys() {
    local source_dir="$SRC_STATE/pmbootstrap-work/config_apk_keys"
    local destination_dir="$WORK/config_apk_keys" source destination expected
    local -a keys=()
    [[ ! -L "$destination_dir" ]] || die "candidate APK key directory must not be a symlink"
    mapfile -t keys < <(find "$source_dir" -maxdepth 1 -type f -name '*.pub' | sort)
    (( ${#keys[@]} )) || die "missing known-good APK public verification keys: $source_dir"
    sudo install -d -m 755 "$destination_dir" || die "could not create candidate APK key directory"
    # Reused APKs retain the original workspace's signatures; copy public trust only.
    for source in "${keys[@]}"; do
        destination="$destination_dir/${source##*/}"
        expected="$(sha256 "$source")"
        [[ ! -L "$destination" ]] || die "candidate APK public key must not be a symlink: $destination"
        if [[ -e "$destination" ]]; then
            [[ -f "$destination" && "$(sha256 "$destination")" = "$expected" ]] ||
                die "candidate APK public key conflicts with known-good key: $destination"
        else
            sudo install -m 644 "$source" "$destination" || die "could not copy APK public verification key"
            [[ "$(sha256 "$destination")" = "$expected" ]] || die "copied APK public key hash mismatch"
        fi
    done
}

seed_known_apks() {
    seed_public_keys
    [[ -L "$WORK/packages" || -L "$WORK/packages/$CHANNEL" || -L "$PKGDIR" ]] &&
        die "candidate package repository must not be a symlink: $PKGDIR"
    sudo install -d -m 755 -o 12345 -g 12345 \
        "$WORK/packages" "$WORK/packages/$CHANNEL" "$PKGDIR" ||
        die "could not create the candidate package directory with uid 12345"
    while IFS=$'\t' read -r name source expected version; do
        [[ -n "$name" ]] || continue
        seed_known_apk "$name" "$source" "$expected" "$version"
    done < <(python3 - "$TMP/source.json" <<'PY'
import json, sys

data = json.load(open(sys.argv[1], encoding='utf-8'))
# The rc3 kernel APK is rebuilt from the rc3 recipe below; only the unchanged
# baseline hook and private libcamera APKs are reused byte-for-byte.
for name in ('postmarketos-mkinitfs-hook-netboot', 'libcamera-rear'):
    entry = data['apks'][name]
    print('\t'.join((name, entry['path'], entry['sha256'], entry['version'])))
for name, entry in data['system_libcamera'].items():
    print('\t'.join((name, entry['path'], entry['sha256'], entry['version'])))
PY
    )
}

clear_stale_kernel_apks() {
    # The reused pmbootstrap work directory may still hold the rc2/rear1 kernel
    # APK.  Move it to the deliberate backup directory before the rc3 envkernel
    # build, so exactly one kernel package can be selected.
    local -a matches=()
    local backup="$WORK/previous-apks/$STAMP" extra
    [[ -L "$WORK/previous-apks" || -L "$backup" ]] &&
        die "APK backup directory must not be a symlink"
    mapfile -t matches < <(find "$PKGDIR" -maxdepth 1 -type f -name "$KERNEL_PKG-[0-9]*.apk")
    ((${#matches[@]})) || return 0
    sudo install -d -m 755 "$backup" || die "could not create the APK backup directory"
    for extra in "${matches[@]}"; do
        note "preserving stale kernel APK: ${extra##*/}"
        [[ ! -e "$backup/${extra##*/}" ]] || die "APK backup already exists: $extra"
        sudo mv -- "$extra" "$backup/" || die "could not preserve the stale kernel APK"
    done
}

sign_system_apks() {
    local -a PMB=(python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "$STATE/rootfs/pmbootstrap.cfg" -p "$STATE/pmaports" -w "$WORK")
    local inside=/home/pmos/nura-apk-signing
    local staging="$WORK/chroot_native$inside" name source expected version
    note "re-signing hash-pinned system libcamera APKs with the candidate workspace key"
    "${PMB[@]}" chroot --add abuild --user -- sh -ec \
        'abuild-sign -e || PACKAGER="pmos <pmos@local>" abuild-keygen -n -q -a'
    "${PMB[@]}" chroot -- sh -ec \
        'for key in /mnt/pmbootstrap/abuild-config/*.pub; do cp "$key" /etc/apk/keys/; done'
    [[ ! -L "$staging" ]] || die "APK signing staging directory must not be a symlink"
    sudo install -d -m 755 -o 12345 -g 12345 "$staging"
    while IFS=$'\t' read -r name source expected version; do
        python3 "$ROOT/scripts/check_plasma_candidate.py" apk-resign \
            --source "$source" --sha256 "$expected" --control-output "$TMP/$name.control.tar.gz"
        sudo install -m 644 -o 12345 -g 12345 "$TMP/$name.control.tar.gz" "$staging/$name.control.tar.gz"
        "${PMB[@]}" chroot --user -- abuild-sign "$inside/$name.control.tar.gz"
        python3 "$ROOT/scripts/check_plasma_candidate.py" apk-resign \
            --source "$source" --sha256 "$expected" \
            --signed-control "$staging/$name.control.tar.gz" --output "$TMP/$name-$version.apk"
        sudo install -m 644 "$TMP/$name-$version.apk" "$PKGDIR/$name-$version.apk"
        "${PMB[@]}" chroot -- apk verify "/mnt/pmbootstrap/packages/$CHANNEL/aarch64/$name-$version.apk"
    done < <(python3 - "$TMP/source.json" <<'PY'
import json, sys
for name, entry in json.load(open(sys.argv[1], encoding='utf-8'))['system_libcamera'].items():
    print('\t'.join((name, entry['path'], entry['sha256'], entry['version'])))
PY
    )
}

package_version() {
    local name="$1" value
    local -a files=()
    local noarch="$WORK/packages/$CHANNEL/noarch"
    mapfile -t files < <(
        find "$PKGDIR" "$noarch" -maxdepth 1 -type f -name "${name}-[0-9]*.apk" 2>/dev/null
    )
    (( ${#files[@]} == 1 )) ||
        die "expected exactly one built $name APK in $PKGDIR or $noarch, found ${#files[@]}"
    value="${files[0]##*/}"
    value="${value#"$name"-}"
    printf '%s' "${value%.apk}"
}

seed_modem_distfile() {
    # The pinned ModemManager archive is not in the reused pmbootstrap cache.
    # Seed a locally verified copy when one exists; otherwise let pmbootstrap
    # fetch the pinned URL and verify the recipe's sha512 itself.
    local recipe="$STATE/pmaports/main/modemmanager/APKBUILD"
    local name expected source_copy cache
    cache="$WORK/cache_distfiles"
    [[ -f "$recipe" ]] || die "missing linked modemmanager recipe: $recipe"
    name="$(python3 - "$recipe" <<'PY'
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
match = re.search(r'ModemManager-[0-9a-f]{40}\.tar\.gz', text)
print(match.group(0) if match else '')
PY
)" || true
    [[ -n "$name" ]] || die "cannot identify the pinned ModemManager archive in $recipe"
    expected="$(python3 - "$recipe" "$name" <<'PY'
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
match = re.search(r'^([0-9a-f]{128})  ' + re.escape(sys.argv[2]) + r'$', text, re.M)
print(match.group(1) if match else '')
PY
)" || true
    [[ -n "$expected" ]] || die "cannot read the pinned $name sha512 from $recipe"
    [[ -L "$cache" ]] && die "cache_distfiles must not be a symlink: $cache"
    if [[ -f "$cache/$name" ]]; then
        [[ "$(sha512sum "$cache/$name" | cut -d' ' -f1)" = "$expected" ]] ||
            die "cached $name does not match the recipe sha512; move it away deliberately"
        note "modem source archive already seeded and verified: $cache/$name"
        return 0
    fi
    for source_copy in \
        "${NURA_DISTFILES:-$ROOT/.work/rc3-distfiles}/$name" \
        "$ROOT/device/testing/rc3/modemmanager/$name"; do
        [[ -f "$source_copy" ]] || continue
        [[ "$(sha512sum "$source_copy" | cut -d' ' -f1)" = "$expected" ]] ||
            die "local modem archive $source_copy does not match the recipe sha512"
        [[ -d "$cache" ]] || sudo install -d -m 775 -o 12345 -g 12345 "$cache" ||
            die "could not create $cache"
        sudo install -m 644 -o 12345 -g 12345 "$source_copy" "$cache/$name" ||
            die "could not seed the verified modem archive"
        [[ "$(sha512sum "$cache/$name" | cut -d' ' -f1)" = "$expected" ]] ||
            die "seeded modem archive hash mismatch"
        note "seeded verified modem source archive: $cache/$name"
        return 0
    done
    warn "modem source archive is not seeded locally; pmbootstrap will fetch $name from the pinned URL and verify its sha512"
    warn "to seed offline: cp <verified $name> .work/rc3-distfiles/  (sha512 $expected)"
}

install_add_string() {
    local kernel_version="$1" hook_version="$2" libcamera_version="$3" ui_version="$4"
    local -a add=(
        "$KERNEL_PKG=$kernel_version"
        "postmarketos-mkinitfs-hook-netboot=$hook_version"
        "libcamera-rear=$libcamera_version"
        "postmarketos-ui-plasma-mobile=$ui_version"
        "plasma-camera"
        "xwayland"
        "libcamera=99990.8.0-r1"
        "libcamera-ipa=99990.8.0-r1"
        "powerdevil=6.8.2-r0"
        "pipewire-spa-libcamera"
        "postmarketos-base-ui-audio-backend-pulseaudio"
        "modemmanager=$MODEM_VERSION"
        "libmm-glib=$LIMM_GLIB_VERSION"
        "modemmanager-systemd=$MODEM_VERSION"
        "modemmanager-udev=$MODEM_VERSION"
        "modemmanager-lang=$MODEM_VERSION"
    )
    [[ -n "$NURA_CLOCK_VERSION" ]] && add+=("nura-clock=$NURA_CLOCK_VERSION")
    local IFS=,
    printf '%s' "${add[*]}"
}

prepare_envkernel() {
    # pmbootstrap --envkernel builds the APK from the same rootless output that
    # produced the staged modules/DTB.  Copy that output into the extracted
    # source tree, never into the read-only baseline.
    local source_dir target
    source_dir="$(find "$KERNEL_STATE" -maxdepth 1 -type d -name 'linux-*' | sort | head -n1)"
    [[ -n "$source_dir" && -f "$source_dir/Makefile" ]] ||
        die "missing extracted rc3 kernel source under $KERNEL_STATE"
    KERNEL_SOURCE="$source_dir"
    target="$source_dir/.output"
    [[ -L "$target" ]] && die "envkernel .output must be a real worktree-local directory"
    note "synchronising the rootless kernel output into $target"
    # This is a derived copy of .work/rc3-kernel/output.  Remove the previous
    # copy so an old module/config cannot survive into the rc3 kernel APK.
    if [[ -e "$target" ]]; then
        rm -rf -- "$target" || die "could not clear the previous envkernel .output copy"
    fi
    mkdir -p "$target"
    cp -a --reflink=auto "$KERNEL_STATE/output/." "$target/"
    printf 'include ../Makefile\n' > "$target/Makefile"
    if [[ -L "$target/source" ]]; then rm -f "$target/source"; fi
    ln -s .. "$target/source"
    [[ -f "$KERNEL_STATE/output/include/config/kernel.release" ]] &&
        [[ "$(cat "$KERNEL_STATE/output/include/config/kernel.release")" = "$RELEASE" ]] ||
        die "rootless kernel output is not $RELEASE"
}

build_kernel_apk() {
    local -a PMB=(python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "$STATE/rootfs/pmbootstrap.cfg" -p "$STATE/pmaports" -w "$WORK")
    prepare_envkernel
    clear_stale_kernel_apks
    note "building the rc3 kernel APK through pmbootstrap --envkernel"
    (cd "$KERNEL_SOURCE" && "${PMB[@]}" build --envkernel "$KERNEL_PKG")
}

build_optional_recipe() {
    # Only linked recipes are built; missing optional topics are reported
    # instead of being invented.  modemmanager is required and checked earlier.
    local dir="$1" package="$2"
    local -a PMB=(python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "$STATE/rootfs/pmbootstrap.cfg" -p "$STATE/pmaports" -w "$WORK")
    [[ -d "$STATE/pmaports/$dir" ]] || return 0
    note "building local recipe $package"
    "${PMB[@]}" build --arch aarch64 --ignore-depends --no-depends --force "$package"
}

run_pmbootstrap_builds() {
    local PMB=(
        python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "$STATE/rootfs/pmbootstrap.cfg"
        -p "$STATE/pmaports"
        -w "$WORK"
    )
    seed_modem_distfile
    note "building local recipes individually; dependency builds are forbidden"
    local package
    for package in firmware-oneplus-guacamole device-oneplus-guacamole hexagonrpcd \
        iio-sensor-proxy postmarketos-ui-plasma-mobile modemmanager; do
        "${PMB[@]}" build --arch aarch64 --ignore-depends --no-depends --force "$package"
    done
    build_optional_recipe main/nura-clock nura-clock
}

prepare_install_config() {
    # --authorized-key is opt-in and build-only.  pmbootstrap's own
    # copy_ssh_keys() installs the PUBLIC key file into the new image's user
    # home (mode 700 directory, 10000:10000 ownership from the existing
    # config.user).  A throw-away config keeps the candidate config at
    # ssh_keys=false, so the default build adds no access.
    [[ -n "$AUTHORIZED_KEY" ]] || return 0
    local source="$STATE/rootfs/pmbootstrap.cfg"
    local output="$TMP/pmbootstrap-authorized-key.cfg"
    local skel="$WORK/chroot_rootfs_oneplus-guacamole/etc/skel/.ssh"
    [[ -f "$source" && ! -L "$source" ]] ||
        die "candidate pmbootstrap config is missing or a symlink: $source"
    [[ -f "$AUTHORIZED_KEYS" && ! -L "$AUTHORIZED_KEYS" ]] ||
        die "validated authorized-key file disappeared: $AUTHORIZED_KEYS"
    if [[ -e "$skel" || -L "$skel" ]]; then
        die "candidate rootfs skel already contains $skel; refusing to overwrite authorized_keys"
    fi
    python3 "$ROOT/scripts/check_plasma_candidate.py" authorized-key-config \
        --source-config "$source" \
        --authorized-keys "$AUTHORIZED_KEYS" \
        --output-config "$output" > "$TMP/authorized-key-config.json" ||
        die "could not prepare the pmbootstrap authorized-key config"
    INSTALL_CONFIG="$output"
    note "authorized key: pmbootstrap install will add the validated public key(s) to the new rootfs user"
}

run_install() {
    local PMB=(
        python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "${INSTALL_CONFIG:-$STATE/rootfs/pmbootstrap.cfg}"
        -p "$STATE/pmaports"
        -w "$WORK"
    )
    local kernel_version hook_version libcamera_version ui_version add
    kernel_version="$(package_version "$KERNEL_PKG")"
    hook_version="$(package_version postmarketos-mkinitfs-hook-netboot)"
    libcamera_version="$(package_version libcamera-rear)"
    ui_version="$(package_version postmarketos-ui-plasma-mobile)"
    MODEM_VERSION="$(package_version modemmanager)"
    LIMM_GLIB_VERSION="$(package_version libmm-glib)"
    local modem_systemd modem_udev modem_lang
    modem_systemd="$(package_version modemmanager-systemd)"
    modem_udev="$(package_version modemmanager-udev)"
    modem_lang="$(package_version modemmanager-lang)"
    [[ "$modem_systemd" = "$MODEM_VERSION" && "$modem_udev" = "$MODEM_VERSION" && "$modem_lang" = "$MODEM_VERSION" ]] ||
        die "modemmanager noarch subpackages ($modem_systemd/$modem_udev/$modem_lang) do not match $MODEM_VERSION; install them as one set"
    NURA_CLOCK_VERSION=""
    if [[ -d "$STATE/pmaports/main/nura-clock" ]]; then
        NURA_CLOCK_VERSION="$(package_version nura-clock)"
    fi
    [[ "$kernel_version" == 7.0.0* ]] || die "refusing kernel version $kernel_version"
    [[ "$hook_version" = 4.2.1-r4 ]] || die "refusing netboot hook version $hook_version"
    [[ "$libcamera_version" = 0.7.2-r0 ]] || die "refusing libcamera-rear version $libcamera_version"
    [[ "$MODEM_VERSION" == 1.25.95* ]] || die "refusing modemmanager version $MODEM_VERSION"
    [[ "$LIMM_GLIB_VERSION" = "$MODEM_VERSION" ]] ||
        die "libmm-glib $LIMM_GLIB_VERSION does not match modemmanager $MODEM_VERSION"
    add="$(install_add_string "$kernel_version" "$hook_version" "$libcamera_version" "$ui_version")"
    note "kernel: $kernel_version (release $RELEASE)"
    note "netboot hook: $hook_version"
    note "libcamera-rear: $libcamera_version"
    note "Plasma UI: $ui_version"
    note "modemmanager: $MODEM_VERSION (libmm-glib $LIMM_GLIB_VERSION)"
    [[ -z "$NURA_CLOCK_VERSION" ]] || note "nura-clock: $NURA_CLOCK_VERSION"
    # pmbootstrap reuses chroot_rootfs_* and only adds packages, so a rootfs
    # built before this change keeps GNOME Snapshot until it is removed once.
    # plasma-camera is installed below; that also keeps the pinned libcamera
    # 0.8 ABI, PipeWire and its libcamera SPA plugin in place.
    if [[ -d "$WORK/chroot_rootfs_oneplus-guacamole" ]]; then
        note "removing the previous GNOME Snapshot app from the reused candidate rootfs"
        "${PMB[@]}" chroot --rootfs -- sh -ec \
            'if grep -q "^P:snapshot$" /lib/apk/db/installed 2>/dev/null; then apk del snapshot; fi'
    fi
    note "install packages: $add"
    "${PMB[@]}" --boot-size 1024 --extra-space 4096 install \
        --no-split --filesystem ext4 --add "$add"
}

run_export() {
    local PMB=(
        python3 "$STATE/pmbootstrap/pmbootstrap.py"
        -c "$STATE/rootfs/pmbootstrap.cfg"
        -p "$STATE/pmaports"
        -w "$WORK"
    )
    local export="$STATE/rootfs/export" target staging
    rm -rf -- "$export"
    "${PMB[@]}" export "$export" --no-install
    # pmbootstrap export writes absolute symlinks for the chroot artifacts.
    # The launch verifier compares resolved candidate paths, so materialize the
    # initramfs inside the Plasma state instead of leaving a symlink behind.
    if [[ -L "$export/initramfs" ]]; then
        target="$(readlink -f -- "$export/initramfs")" || die "cannot resolve exported initramfs"
        [[ -f "$target" ]] || die "exported initramfs target is missing: $target"
        staging="$export/initramfs.real.$$"
        cp -a --reflink=auto "$target" "$staging" || die "could not materialize exported initramfs"
        rm -f -- "$export/initramfs"
        mv -- "$staging" "$export/initramfs" || die "could not install materialized initramfs"
    fi
    [[ -f "$export/initramfs" ]] || die "pmbootstrap export did not produce initramfs"
    [[ ! -L "$export/initramfs" ]] || die "exported initramfs must be a real file"
}

verify_installed() {
    local ui_version="$1"
    local rootfs="$WORK/chroot_rootfs_oneplus-guacamole"
    local -a clock_args=()
    [[ -z "$NURA_CLOCK_VERSION" ]] || clock_args=(--nura-clock-version "$NURA_CLOCK_VERSION")
    python3 "$ROOT/scripts/check_plasma_candidate.py" verify \
        --release "$RELEASE" \
        --kernel-dir "$KERNEL_OUT/kernel-package" \
        --rootfs "$rootfs" \
        --initramfs "$STATE/rootfs/export/initramfs" \
        --known-good-rootfs "$(json_get rootfs)" \
        --expected-vmlinuz-sha "$(json_get vmlinuz_sha256)" \
        --expected-dtb-sha "$(json_get dtb_sha256)" \
        --expected-module-payload-sha "$(json_get module_payload_sha256)" \
        --expected-module-count "$(json_get module_count)" \
        --ui-version "$ui_version" \
        --modemmanager-version "$MODEM_VERSION" \
        --libmm-glib-version "$LIMM_GLIB_VERSION" \
        "${clock_args[@]}" > "$TMP/installed.json" ||
        die "installed Plasma rootfs failed identity checks"
    note "installed rc3 kernel/DTB/modules/rootfs-initramfs: OK"
    note "installed Plasma UI, Camera, system libcamera 0.8, PulseAudio, PowerDevil: OK"
    note "installed modemmanager $MODEM_VERSION with libmm-glib $LIMM_GLIB_VERSION: OK"
    [[ -z "$NURA_CLOCK_VERSION" ]] || note "installed nura-clock $NURA_CLOCK_VERSION and clock floor: OK"
}

pack_boot_image() {
    # The new boot image must carry the rc3 kernel package and the *exported*
    # rc3 rootfs initramfs (its own modules + port-10001 hook).  A reused rc2
    # initramfs would fail the release check inside the packer.
    local initramfs="$STATE/rootfs/export/initramfs"
    [[ -f "$initramfs" && ! -L "$initramfs" ]] ||
        die "missing exported rc3 rootfs initramfs: $initramfs"
    note "preparing the ABL quarantine wrapper from the rc3 DTB"
    python3 "$ROOT/scripts/prepare_temporary_dtb.py" \
        --base-dtb "$KERNEL_OUT/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
        --base-source-dts "$KERNEL_SOURCE/arch/arm64/boot/dts/qcom/sm8150-oneplus-guacamole.dts" \
        --patched-dts "$KERNEL_OUT/temporary-dtbo/base.dts" \
        --out-dir "$KERNEL_OUT/temporary-dtbo"
    rm -f -- "$KERNEL_OUT/boot.img"
    note "packing the rc3 boot image from the rc3 kernel package and exported rootfs initramfs"
    RELEASE="$RELEASE" \
        OUTPUT_DIR="$KERNEL_OUT" \
        KERNEL_DIR="$KERNEL_OUT/kernel-package" \
        BASE_DTB="$KERNEL_OUT/kernel-package/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
        TEMP_DTB_DIR="$KERNEL_OUT/temporary-dtbo" \
        INITRAMFS="$initramfs" \
        bash "$ROOT/scripts/build_bootimg_rear_camera.sh"
    [[ -f "$KERNEL_OUT/boot.img" ]] || die "boot packer did not produce $KERNEL_OUT/boot.img"
    [[ "$(sha256 "$KERNEL_OUT/Image")" = "$(json_get image_sha256)" ]] ||
        die "boot packer regenerated an Image that differs from the kernel build"
}

write_kernel_manifest() {
    local rootfs_image="$WORK/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
    [[ -f "$rootfs_image" && ! -L "$rootfs_image" ]] ||
        die "missing candidate rootfs image: $rootfs_image"
    python3 "$ROOT/scripts/check_plasma_candidate.py" kernel-manifest \
        --build "$KERNEL_OUT" \
        --state "$KERNEL_STATE" \
        --recipe "$KERNEL_RECIPE" \
        --boot-img "$KERNEL_OUT/boot.img" \
        --initramfs "$STATE/rootfs/export/initramfs" \
        --rootfs-image "$rootfs_image" \
        --rootfs-tree "$WORK/chroot_rootfs_oneplus-guacamole" \
        --apk-dir "$PKGDIR" > "$TMP/kernel-delivery.json" ||
        die "rc3 kernel delivery checks failed"
    note "wrote $KERNEL_OUT/DELIVERY.json and $KERNEL_OUT/packaging-inputs.json"
}

copy_boot_initramfs() {
    local source="$STATE/rootfs/export/initramfs" expected staging actual
    expected="$(sha256 "$source")"
    staging="$OUT/.boot-initramfs.new.$$"
    cp -a --reflink=auto "$source" "$staging" ||
        die "could not copy the rc3 rootfs initramfs"
    actual="$(sha256 "$staging")"
    if [[ "$actual" != "$expected" ]]; then
        rm -f -- "$staging"
        die "copied boot initramfs hash mismatch: $actual != $expected"
    fi
    python3 "$ROOT/scripts/check_initramfs_release.py" \
        --archive "$staging" --release "$RELEASE" ||
        { rm -f -- "$staging"; die "copied boot initramfs is not a $RELEASE archive"; }
    mv -- "$staging" "$BOOT_INITRAMFS" || die "could not install the boot initramfs copy"
    [[ "$(sha256 "$BOOT_INITRAMFS")" = "$expected" ]] || die "installed boot initramfs hash mismatch"
}

copy_boot_image() {
    local staging actual expected
    expected="$(sha256 "$KERNEL_OUT/boot.img")"
    staging="$OUT/.boot.img.new.$$"
    cp -a --reflink=auto "$KERNEL_OUT/boot.img" "$staging" ||
        die "could not copy the rc3 boot image"
    actual="$(sha256 "$staging")"
    if [[ "$actual" != "$expected" ]]; then
        rm -f -- "$staging"
        die "copied boot image hash mismatch: $actual != $expected"
    fi
    mv -- "$staging" "$OUT/boot.img" || die "could not install the candidate boot image"
    [[ "$(sha256 "$OUT/boot.img")" = "$expected" ]] || die "installed boot image hash mismatch"
}

write_manifest() {
    local rootfs_image="$WORK/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
    local -a clock_args=()
    [[ -f "$rootfs_image" ]] || die "missing candidate rootfs image: $rootfs_image"
    [[ ! -L "$rootfs_image" ]] || die "candidate rootfs image must not be a symlink"
    [[ -z "$NURA_CLOCK_VERSION" ]] || clock_args=(--nura-clock-version "$NURA_CLOCK_VERSION")
    python3 "$ROOT/scripts/check_plasma_candidate.py" manifest \
        --source-build "$KERNEL_OUT" \
        --source-state "$KERNEL_STATE" \
        --baseline-build "$SRC_OUT" \
        --baseline-state "$SRC_STATE" \
        --kernel-dir "$OUT/kernel-package" \
        --image "$OUT/Image" \
        --boot-img "$OUT/boot.img" \
        --boot-initramfs "$BOOT_INITRAMFS" \
        --rootfs-initramfs "$STATE/rootfs/export/initramfs" \
        --rootfs-image "$rootfs_image" \
        --rootfs-tree "$WORK/chroot_rootfs_oneplus-guacamole" \
        --temporary-dtbo "$OUT/temporary-dtbo" \
        --apk-dir "$PKGDIR" \
        --modemmanager-version "$MODEM_VERSION" \
        --libmm-glib-version "$LIMM_GLIB_VERSION" \
        "${clock_args[@]}" \
        --out "$OUT" > "$TMP/delivery.json" ||
        die "candidate manifest checks failed"
    note "wrote $OUT/DELIVERY.json and $OUT/packaging-inputs.json"
}

print_next_steps() {
    cat <<EOF

Plasma Mobile $RELEASE_CANDIDATE candidate prepared; no phone or server action
was performed.

  release candidate: $RELEASE_CANDIDATE (kernel $RELEASE)
  artifact:    $ARTIFACT_NAME
  boot image:  $OUT/boot.img ($(sha256 "$OUT/boot.img"))
  rootfs:      $WORK/chroot_native/home/pmos/rootfs/oneplus-guacamole.img
  boot initramfs: $BOOT_INITRAMFS (new rc3 rootfs initramfs, own modules + port $NBD_PORT)
  rootfs initramfs: $STATE/rootfs/export/initramfs (candidate rootfs)
  kernel build: $KERNEL_OUT/DELIVERY.json
  manifest:    $OUT/DELIVERY.json
  rollback:    ${ROLLBACK_DIR:-none (no previous candidate)}
  log:         $LOG

The rc3 boot chain was rebuilt from the rc3 kernel recipe: new Image, vmlinuz,
modules, DTB, ABL wrapper and boot initramfs.  It is not the rc2 rear1 boot.

First boot is intentionally unlocked (candidate-only LockOnStart=false in
$STATE/pmaports/main/postmarketos-ui-plasma-mobile/kscreenlockerrc).  To restore
the upstream lock policy, set LockOnStart=true there, rebuild the UI package and
rerun this script.  The setting is recorded in $STATE/rootfs/candidate-state.json.

Automatic suspend is disabled in the candidate rootfs: logind idle/suspend-key
actions are ignored (50-nura-plasma-nbd.conf) and PowerDevil AC/Battery/LowBattery
AutoSuspendAction is NoAction (powerdevilrc, PowerDevil 6.8.2 schema).  Manual
reboot/poweroff and explicit systemctl commands remain available.  Keep the NBD
cable/server attached; PowerDevil is not globally masked.

Next (user-run, never flash):
  1. Stop both port-$NBD_PORT servers and boot the phone out of Linux.
  2. bash scripts/plasma_serve.sh --check
  3. bash scripts/plasma_serve.sh
  4. bash scripts/plasma_boot.sh boot --dry-run
  5. bash scripts/plasma_boot.sh boot
EOF
}

preserve_previous_output
prepare_work
prepare_pmaports_copy
prepare_pmbootstrap_copy
prepare_export

note "preparing isolated pmaports links, config, lock and PowerDevil policies"
python3 "$ROOT/scripts/check_plasma_candidate.py" prepare \
    --source-pmaports "$SRC_STATE/pmaports" \
    --candidate-pmaports "$STATE/pmaports" \
    --source-pmbootstrap "$SRC_STATE/pmbootstrap" \
    --candidate-pmbootstrap "$STATE/pmbootstrap" \
    --source-config "$SRC_STATE/rootfs/pmbootstrap.cfg" \
    --candidate-config "$STATE/rootfs/pmbootstrap.cfg" \
    --work "$WORK" \
    --state "$STATE" \
    --receipt "$STATE/rootfs/candidate-state.json" > "$TMP/prepare.json" ||
    die "could not prepare the isolated Plasma state"
prepare_install_config

seed_known_apks
sign_system_apks
build_kernel_apk
run_pmbootstrap_builds
run_install
run_export
UI_VERSION="$(package_version postmarketos-ui-plasma-mobile)"
verify_installed "$UI_VERSION"
pack_boot_image
write_kernel_manifest

note "copying the rc3 kernel package, Image and temporary-DTBO wrapper into the candidate output"
replace_dir "$KERNEL_OUT/kernel-package" "$OUT/kernel-package" "rc3 kernel package"
copy_file "$KERNEL_OUT/Image" "$OUT/Image" "rc3 kernel Image"
replace_dir "$KERNEL_OUT/temporary-dtbo" "$OUT/temporary-dtbo" "rc3 temporary-DTBO wrapper"

copy_boot_initramfs
copy_boot_image
write_manifest
print_next_steps
