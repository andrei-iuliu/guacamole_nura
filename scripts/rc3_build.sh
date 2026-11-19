#!/usr/bin/env bash
# USER RUN ONLY: the build phase invokes pmbootstrap (sudo, mounts, chroots).
#
# Single normal-user entrypoint for the nura-plasma-rc3 candidate.  It is a
# thin orchestrator around the proven helpers:
#
#   scripts/build_rear_camera_kernel.sh   rootless rc3 kernel/modules/DTB
#   scripts/rootfs_prepare_plasma.sh      privileged rootfs/export/boot/manifest
#   scripts/build_bootimg_rear_camera.sh  packed rc3 boot image
#   scripts/check_plasma_candidate.py     computed identities and read-only checks
#
#   bash scripts/rc3_build.sh check              read-only preflight (default)
#   bash scripts/rc3_build.sh kernel             rootless kernel build only
#   bash scripts/rc3_build.sh build [--authorized-key PATH] [--jobs N]
#                                                 kernel -> APKs -> rootfs ->
#                                                 boot -> manifest -> checks
#
# The kernel is built in .work/rc3-kernel / build/rc3-kernel.  The Plasma
# candidate keeps .work/plasma-mobile / build/plasma-mobile so an existing rc2
# candidate is preserved as rollback by rootfs_prepare_plasma.sh before the
# build touches it.  The rc2 baseline .work/rear-camera / build/rear-camera is
# read-only input for its pinned hook/libcamera/service APKs.  The default in
# this script is a read-only preflight; nothing here flashes, boots or serves.
set -Eeuo pipefail

if (( EUID == 0 )); then
    echo "Run this script as your normal user; pmbootstrap handles privilege." >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

RELEASE="$(python3 scripts/check_plasma_candidate.py kernel-release)" ||
    { echo "ERROR: cannot read the rc3 kernel release" >&2; exit 1; }
RELEASE_CANDIDATE="$(python3 scripts/check_plasma_candidate.py release-candidate)" ||
    { echo "ERROR: cannot read the rc3 candidate label" >&2; exit 1; }

KERNEL_RECIPE="device/testing/rc3/linux-postmarketos-qcom-sm8150"
MODEM_RECIPE="device/testing/rc3/modemmanager"
KERNEL_STATE="$ROOT/.work/rc3-kernel"
KERNEL_OUT="$ROOT/build/rc3-kernel"
BASELINE_STATE="$ROOT/.work/rear-camera"
BASELINE_OUT="$ROOT/build/rear-camera"
CANDIDATE_STATE="$ROOT/.work/plasma-mobile"
CANDIDATE_OUT="$ROOT/build/plasma-mobile"
NBD_PORT=10001
MIN_FREE_KIB=$((24 * 1024 * 1024))
STAMP="$(date +%Y%m%d-%H%M%S)"

MODE=check
AUTHORIZED_KEY=''
JOBS="${JOBS:-12}"
LOG=''

usage() {
    cat <<EOF
Usage: scripts/rc3_build.sh [check|kernel|build] [OPTIONS]

  check     Read-only preflight: rc3 recipes, baseline pins, modem/nura-clock
            sources, disk space, mounts and port $NBD_PORT.  Does not build,
            mount, sudo, serve or touch a phone.  Default.
  kernel    Build Image/modules/DTBs in $KERNEL_STATE and stage
            $KERNEL_OUT/kernel-package, then record every produced hash in
            $KERNEL_OUT/kernel-inputs.json.  Rootless; no sudo/pmbootstrap.
  build     Run check, then kernel, then the user-run rootfs preparation
            (pmbootstrap builds the rc3 kernel APK, device/firmware/UI/modem
            packages, installs, exports, packs the new boot image and writes
            the manifests).  This is the only phase that uses sudo.
  -h, --help
Options for build:
  --authorized-key PATH   Add these explicit OpenSSH PUBLIC keys to the new
                          rootfs (build-only; default adds none).
  --jobs N                Kernel job count (default 12).

The rc2 candidate in $CANDIDATE_OUT is preserved automatically as
rollback-nura-plasma-rc2-<stamp> before the build touches it.  If that
directory is not created, the build stops before changing anything.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
phase() { printf '\n-- %s --\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

while (( $# )); do
    case "$1" in
        check|kernel|build) MODE="$1" ;;
        --authorized-key)
            (( $# >= 2 )) && [[ -n "$2" ]] || die "--authorized-key needs a public-key file"
            AUTHORIZED_KEY="$2"; shift ;;
        --authorized-key=*)
            AUTHORIZED_KEY="${1#*=}"; [[ -n "$AUTHORIZED_KEY" ]] || die "--authorized-key needs a public-key file" ;;
        --jobs)
            (( $# >= 2 )) || die "--jobs needs a value"
            JOBS="$2"; shift ;;
        --jobs=*) JOBS="${1#*=}" ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
    shift
done
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer"

host_commands=(git python3 sha256sum sha512sum realpath stat find awk sed df ss findmnt)
[[ "$MODE" = build ]] && host_commands+=(sudo cpio gzip zstd xz modinfo)
missing=()
for command in "${host_commands[@]}"; do
    command -v "$command" >/dev/null 2>&1 || missing+=("$command")
done
(( ${#missing[@]} == 0 )) || die "missing host commands: ${missing[*]}"

# ---------------------------------------------------------------------------
# Read-only preflight
# ---------------------------------------------------------------------------

port_busy() {
    local output
    if command -v ss >/dev/null 2>&1; then
        output="$(ss -H -ltn "sport = :$NBD_PORT" 2>/dev/null)" ||
            die "ss failed; cannot inspect TCP port $NBD_PORT"
        [[ -n "$output" ]]
        return
    fi
    [[ -r /proc/net/tcp ]] || die "cannot inspect port $NBD_PORT"
    local hex files=(/proc/net/tcp)
    [[ -r /proc/net/tcp6 ]] && files+=(/proc/net/tcp6)
    printf -v hex '%04X' "$NBD_PORT"
    awk -v port=":$hex" '$2 ~ port "$" && $4 == "0A" { found=1 } END { exit !found }' "${files[@]}"
}

mounts_below() {
    local root target response
    root="$(realpath -m -- "$1")"
    command -v findmnt >/dev/null 2>&1 ||
        die "findmnt is required to prove ${1#"$ROOT"/} has no active mounts"
    # A failed findmnt must never look like "no mounts": refuse instead.
    if ! response="$(findmnt -rn -o TARGET)"; then
        die "findmnt failed; cannot prove ${1#"$ROOT"/} has no active mounts"
    fi
    while IFS= read -r target; do
        [[ -n "$target" ]] || continue
        printf -v target '%b' "$target"
        case "${target%/}/" in "$root"/*) return 0 ;; esac
    done <<< "$response"
    return 1
}

refuse_live_state() {
    # Used again after the build phases: the final candidate check must not
    # read/hash a rootfs that a listener or an active mount could be changing.
    local label=$1 path live=''
    if port_busy; then
        die "$label: TCP port $NBD_PORT is in use; stop the server and rerun the check"
    fi
    for path in "$CANDIDATE_STATE" "$KERNEL_STATE"; do
        if mounts_below "$path"; then
            live+="${live:+, }${path#"$ROOT"/}"
        fi
    done
    [[ -z "$live" ]] || die "$label: active mounts below ${live}; clear them before the candidate check"
}

require_file() {
    [[ -f "$1" ]] || die "missing required input: ${1#"$ROOT"/}  ($2)"
}

require_dir() {
    [[ -d "$1" ]] || die "missing required directory: ${1#"$ROOT"/}  ($2)"
}

preflight() {
    local out port_live=0 mount_live=0 live=''
    # The live-state guard must run before any candidate artifact is read or
    # hashed.  A served candidate's rootfs is mutable; a build must fail here,
    # and a read-only check must skip the candidate check instead of hashing a
    # possibly live image.
    phase "preflight: live listener and mounts (before candidate artifacts)"
    if port_busy; then
        port_live=1
    fi
    for path in "$CANDIDATE_STATE" "$KERNEL_STATE"; do
        if mounts_below "$path"; then
            mount_live=1
            live+="${live:+, }${path#"$ROOT"/}"
        fi
    done
    if (( port_live || mount_live )); then
        if [[ "$MODE" = build ]]; then
            (( port_live )) && die "TCP port $NBD_PORT is in use; stop the server and boot the phone out of Linux before a build"
            die "active mounts below ${live}; stop pmbootstrap servers/shells before a build"
        fi
        (( port_live )) && warn "TCP port $NBD_PORT is in use; a build would refuse until the server is stopped"
        (( mount_live )) && warn "active mounts below ${live}; a build would refuse until they are cleared"
        warn "candidate artifact validation is skipped: the candidate may be live, so no rootfs is read or hashed; stop the server and boot the phone out of Linux, then rerun"
    fi

    phase "preflight: rc3 recipes and build inputs"
    require_file "$ROOT/$KERNEL_RECIPE/APKBUILD" "the kernel worker owns this rc3 recipe"
    require_file "$ROOT/$KERNEL_RECIPE/config-postmarketos-qcom-sm8150.aarch64" "rc3 kernel config"
    require_file "$ROOT/$MODEM_RECIPE/APKBUILD" "the modem worker owns this self-contained recipe"
    require_file "$ROOT/device/testing/nura-clock/APKBUILD" "the kernel/device worker owns the clock recipe"
    require_file "$ROOT/device/testing/nura-clock/clock-epoch" "native systemd clock floor"
    require_file "$ROOT/device/testing/device-oneplus-guacamole/APKBUILD" "device package changes"
    require_file "$ROOT/device/testing/device-oneplus-guacamole/deviceinfo" "device package"
    require_dir "$ROOT/device/testing/firmware-oneplus-guacamole" "firmware package"
    require_file "$ROOT/device/testing/firmware-oneplus-guacamole/firmware-oneplus-guacamole-24.0-20261011.tar.gz" \
        "generate it from a LineageOS OTA (see CLAUDE.md)"
    require_file "$ROOT/device/testing/device-oneplus-guacamole/guacamole-sensors-20261102.tar.gz" \
        "generate it with scripts/prepare_sensor_bundle.py"
    require_file "$ROOT/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot/APKBUILD" \
        "pinned port-$NBD_PORT netboot hook recipe"
    require_file "$ROOT/device/testing/rear-camera/libcamera-rear/APKBUILD" "pinned private libcamera 0.7.2"
    require_file "$ROOT/$KERNEL_RECIPE/0031-i2c-qcom-cci-initialize-all-master-completions.patch" \
        "the proven CCI completion fix must be in the rc3 patch series"
    note "rc3 kernel recipe: $KERNEL_RECIPE (release $RELEASE)"
    note "rc3 modem recipe: $MODEM_RECIPE"

    phase "preflight: read-only rc2 baseline and isolated state"
    require_dir "$BASELINE_STATE/pmaports" "pinned pmaports copy"
    require_dir "$BASELINE_STATE/pmbootstrap" "pinned pmbootstrap copy"
    require_file "$BASELINE_STATE/rootfs/pmbootstrap.cfg" "pinned pmbootstrap config"
    require_file "$BASELINE_OUT/DELIVERY.json" "rc2 baseline manifest"
    require_file "$BASELINE_OUT/packaging-inputs.json" "rc2 baseline packaging inputs"
    out="$(python3 - "$BASELINE_OUT/DELIVERY.json" "$RELEASE_CANDIDATE" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except (OSError, ValueError) as error:
    raise SystemExit(f'cannot read baseline manifest: {error}')
if data.get('release') != '7.0.0-sm8150-rear1':
    raise SystemExit(f"baseline release is {data.get('release')!r}, not the rear1 rollback input")
print(data.get('release'))
PY
)" || die "rc2 baseline manifest is not the read-only rear1 pin source"
    note "baseline release: $out (read-only pins only)"
    for path in "$KERNEL_STATE" "$KERNEL_OUT" "$CANDIDATE_STATE" "$CANDIDATE_OUT"; do
        [[ -L "$path" ]] && die "refusing a symlinked state/output path: $path"
    done

    phase "preflight: modem source archive"
    local modem_archive=''
    for candidate in "$ROOT/device/testing/rc3/modemmanager"/ModemManager-*.tar.gz \
                     "${NURA_DISTFILES:-$ROOT/.work/rc3-distfiles}"/ModemManager-*.tar.gz; do
        [[ -f "$candidate" ]] || continue
        modem_archive="$candidate"
        break
    done
    if [[ -n "$modem_archive" ]]; then
        note "verified modem archive available for seeding: ${modem_archive#"$ROOT"/}"
    else
        note "modem archive not seeded; pmbootstrap will fetch the pinned URL and verify its sha512"
        note "offline seed: cp <verified ModemManager-*.tar.gz> .work/rc3-distfiles/"
    fi

    phase "preflight: disk space"
    local available_kb
    available_kb="$(df -Pk -- "$ROOT" | awk 'NR == 2 { print $4 }')"
    [[ "$available_kb" =~ ^[0-9]+$ ]] || die "cannot read free space for $ROOT"
    (( available_kb >= MIN_FREE_KIB )) ||
        die "not enough free space: ${available_kb} KiB available, ${MIN_FREE_KIB} KiB required for a kernel + rootfs + boot + rollback build"
    note "free space: $((available_kb / 1024)) MiB (minimum $((MIN_FREE_KIB / 1024)) MiB)"

    phase "preflight: kernel staging"
    if [[ -f "$KERNEL_OUT/kernel-inputs.json" ]]; then
        python3 scripts/check_plasma_candidate.py source \
            --source-build "$KERNEL_OUT" --source-state "$KERNEL_STATE" \
            --baseline-build "$BASELINE_OUT" --baseline-state "$BASELINE_STATE" \
            > /dev/null ||
            die "existing rc3 kernel staging failed validation; rerun: bash scripts/rc3_build.sh kernel"
        note "recorded rc3 kernel staging: OK"
    else
        note "no rc3 kernel build yet (build creates it)"
    fi

    phase "preflight: current candidate"
    if (( port_live || mount_live )); then
        note "skipped current-candidate artifact validation (live listener/mounts above); nothing was read"
    elif [[ -f "$CANDIDATE_OUT/DELIVERY.json" ]]; then
        out="$(python3 - "$CANDIDATE_OUT/DELIVERY.json" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding='utf-8'))
except (OSError, ValueError) as error:
    raise SystemExit(f'cannot read candidate manifest: {error}')
print(f"{data.get('release_candidate')}|{data.get('release')}")
PY
)" || die "current candidate manifest is unreadable"
        local label="${out%%|*}" release="${out#*|}"
        if [[ "$label" = "$RELEASE_CANDIDATE" && "$release" = "$RELEASE" ]]; then
            python3 scripts/check_plasma_candidate.py check \
                --build "$CANDIDATE_OUT" --state "$CANDIDATE_STATE" \
                --kernel-state "$KERNEL_STATE" > /dev/null ||
                die "built rc3 candidate failed artifact consistency checks"
            note "built rc3 candidate: $CANDIDATE_OUT (checks passed)"
        else
            note "current output is ${label:-unlabeled} (kernel ${release:-unknown}); it will be preserved as rollback"
        fi
    else
        note "no current Plasma candidate output (nothing to roll back)"
    fi

}

# ---------------------------------------------------------------------------
# Build phases
# ---------------------------------------------------------------------------

kernel_phase() {
    phase "kernel: rootless rc3 build (.work/rc3-kernel -> build/rc3-kernel)"
    note "This compiles the whole kernel and can take 30-60+ minutes; output follows."
    NURA_RECIPE_DIR="$ROOT/$KERNEL_RECIPE" \
        NURA_KERNEL_STATE="$KERNEL_STATE" \
        NURA_KERNEL_OUTPUT="$KERNEL_STATE/output" \
        NURA_KERNEL_STAGE="$KERNEL_OUT/kernel-package" \
        NURA_KERNEL_IMAGE="$KERNEL_OUT/Image" \
        NURA_KERNEL_RELEASE="$RELEASE" \
        JOBS="$JOBS" \
        bash scripts/build_rear_camera_kernel.sh
    python3 scripts/check_plasma_candidate.py kernel-record \
        --build "$KERNEL_OUT" --state "$KERNEL_STATE" --recipe "$KERNEL_RECIPE" > /dev/null ||
        die "could not record the rc3 kernel build identity"
    note "recorded $KERNEL_OUT/kernel-inputs.json (computed from this build)"
}

build_phase() {
    preflight
    LOG="$CANDIDATE_STATE/rootfs/$RELEASE_CANDIDATE-pipeline-$STAMP.log"
    mkdir -p "$CANDIDATE_STATE/rootfs"
    exec > >(tee -a "$LOG") 2>&1
    trap 'status=$?; echo "rc3 pipeline failed at line $LINENO (exit $status). Log: $LOG" >&2; exit "$status"' ERR
    echo "rc3 pipeline log: $LOG"
    kernel_phase
    phase "rootfs: pmbootstrap builds, install, export, boot image and manifests"
    note "This is the privileged phase (pmbootstrap sudo/mount/chroot); output follows."
    local -a key_args=()
    [[ -z "$AUTHORIZED_KEY" ]] || key_args=(--authorized-key "$AUTHORIZED_KEY")
    bash scripts/rootfs_prepare_plasma.sh --build "${key_args[@]}"
    phase "checks: built candidate"
    refuse_live_state "candidate check refused"
    python3 scripts/check_plasma_candidate.py check \
        --build "$CANDIDATE_OUT" --state "$CANDIDATE_STATE" \
        --kernel-state "$KERNEL_STATE" ||
        die "built candidate failed the read-only consistency check"
    phase "done"
    cat <<EOF
Built $RELEASE_CANDIDATE from the rc3 kernel build; no phone or server action was performed.
  candidate: $CANDIDATE_OUT
  kernel:    $KERNEL_OUT
  log:       $LOG

Next (user-run, never flash):
  1. Stop both port-$NBD_PORT servers and boot the phone out of Linux.
  2. bash scripts/plasma_serve.sh --check
  3. bash scripts/plasma_serve.sh
  4. bash scripts/plasma_boot.sh boot --dry-run
  5. bash scripts/plasma_boot.sh boot
EOF
}

case "$MODE" in
    check) preflight ;;
    kernel) kernel_phase ;;
    build) build_phase ;;
esac
