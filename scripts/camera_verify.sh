#!/usr/bin/env bash
# USER RUN ONLY: read-only post-boot verification of the rear-camera candidate.
#
# Run this on the host after the phone has booted build/rear-camera/boot.img
# and SSH answers. It proves which kernel release and CCI module the running
# rootfs contains, then captures the camera probe/media state for a human to
# read. It never loads or unloads modules, starts streams, probes I2C, writes
# regulators/rails or reboots anything.
#
#   bash scripts/camera_verify.sh
#   bash scripts/camera_verify.sh --timeout 180 --sudo
#   bash scripts/camera_verify.sh --dry-run
#   bash scripts/camera_verify.sh --local        # host self-test only
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
MANIFEST="$ROOT/build/rear-camera/DELIVERY.json"
KNOWN_HOSTS="$ROOT/.work/delivery/known_hosts"

EXPECTED_RELEASE="7.0.0-sm8150-rear1"
EXPECTED_CCI_SHA256=""
EXPECTED_CCI_SIZE=""
CCI_RELATIVE_PATH="kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"

HOST="172.16.42.1"
REMOTE_USER="user"
WAIT_SECONDS="120"
COLLECT_DIR=""
USE_SUDO=0
MODE="remote"
SSH_ATTEMPTS=0
SSH_ELAPSED=0

usage() {
    cat <<'EOF'
Usage: bash scripts/camera_verify.sh [options]

Read-only post-boot check for the OnePlus 7 Pro rear-camera candidate.
Run it on the host after the phone has booted build/rear-camera/boot.img and
SSH answers. Nothing on the phone is loaded, unloaded, streamed, probed or
written; the script only captures text.

Options:
  --host ADDRESS     Phone address (default: 172.16.42.1)
  --user NAME        SSH user (default: user)
  --timeout SECONDS  Wait up to this long for SSH (default: 120)
  --collect-dir DIR  Save diagnostics here
                     (default: build/rear-camera/diagnostics/phone-<UTC>/)
                     A relative path is resolved against the current directory.
  --sudo             Allow read-only `sudo -n` for dmesg and the pm_genpd summary
  --no-sudo          Never use sudo (default)
  --dry-run          Print the exact SSH and remote commands; do not connect
  --local            Run the same checks on THIS HOST; clearly labelled as not
                     evidence about the phone (exit status is always 1)
  -h, --help         Show this help

Fail-closed identity: build/rear-camera/DELIVERY.json must exist, name this
checkout as its worktree, release 7.0.0-sm8150-rear1, and record the
i2c_qcom_cci sha256/size produced by this build.  No binary hash is built into
this script.  Refreshed by: bash scripts/camera_build.sh manifest

Exit status:
  0  recorded CCI module installed in the running rootfs, no CCI timeout or oops
  1  STALE MODULE / OOPS / PROBE TIMEOUT / NO SSH
  2  recorded CCI module, but a secondary check failed (netboot cmdline or media graph)
EOF
}

die() {
    printf 'camera_verify: %s\n' "$*" >&2
    exit 2
}

need_value() {
    [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --host) need_value "$@"; HOST=$2; shift 2 ;;
        --user) need_value "$@"; REMOTE_USER=$2; shift 2 ;;
        --timeout) need_value "$@"; WAIT_SECONDS=$2; shift 2 ;;
        --collect-dir) need_value "$@"; COLLECT_DIR=$2; shift 2 ;;
        --sudo) USE_SUDO=1; shift ;;
        --no-sudo) USE_SUDO=0; shift ;;
        --dry-run)
            [[ "$MODE" == "remote" ]] || die "--dry-run and --local are mutually exclusive"
            MODE="dry-run"; shift ;;
        --local)
            [[ "$MODE" == "remote" ]] || die "--dry-run and --local are mutually exclusive"
            MODE="local"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

[[ "$WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "--timeout must be a positive integer, got '$WAIT_SECONDS'"
[[ -n "$HOST" ]] || die "--host must not be empty"
[[ -n "$REMOTE_USER" ]] || die "--user must not be empty"
TARGET="${REMOTE_USER}@${HOST}"
if [[ "$MODE" == "local" ]]; then TARGET_LABEL="this host"; else TARGET_LABEL="the phone"; fi

# ---------------------------------------------------------------- manifest

manifest_missing() {
    printf 'camera_verify: missing delivery manifest: %s\n' "$MANIFEST" >&2
    printf 'camera_verify: build it first: bash %s/scripts/camera_build.sh manifest\n' "$ROOT" >&2
    exit 1
}

load_manifest() {
    [[ -f "$MANIFEST" ]] || manifest_missing
    local output
    if ! output=$(python3 - "$MANIFEST" "$ROOT" "$EXPECTED_RELEASE" \
                            "$CCI_RELATIVE_PATH" <<'PY'
import json
import sys
from pathlib import Path

manifest, root, release, relative_path = sys.argv[1:5]


def fail(message):
    print(f"camera_verify: manifest {manifest}: {message}", file=sys.stderr)
    raise SystemExit(1)


try:
    data = json.loads(Path(manifest).read_text())
except (OSError, json.JSONDecodeError) as exc:
    fail(f"cannot read JSON ({exc})")
if not isinstance(data, dict):
    fail("top level is not a JSON object")

actual_release = data.get("release")
if actual_release != release:
    fail(f"release {actual_release!r} != expected {release!r}; rebuild the candidate")

worktree = data.get("worktree")
if not isinstance(worktree, str) or not worktree:
    fail("missing 'worktree'")
if Path(worktree).resolve() != Path(root).resolve():
    fail(
        f"worktree {worktree!r} != this checkout {root!r}; the manifest belongs to another "
        f"worktree. Rebuild it here: bash {root}/scripts/camera_build.sh manifest"
    )

cci = data.get("i2c_qcom_cci")
if not isinstance(cci, dict):
    fail("missing 'i2c_qcom_cci' object")
cci_sha = cci.get("sha256")
if not isinstance(cci_sha, str) or len(cci_sha) != 64 or set(cci_sha) - set("0123456789abcdef"):
    fail(f"i2c_qcom_cci.sha256 {cci_sha!r} is not 64 lowercase hex characters; rerun camera_build.sh manifest")
try:
    cci_size = int(cci.get("size"))
except (TypeError, ValueError):
    fail("i2c_qcom_cci.size is not an integer")
if cci_size <= 0:
    fail(f"i2c_qcom_cci.size {cci_size} is not positive; rerun camera_build.sh manifest")
cci_path = cci.get("path")
if not isinstance(cci_path, str) or not cci_path.endswith(relative_path):
    fail(f"i2c_qcom_cci.path {cci_path!r} is not an {relative_path} path")

for key in ("boot_img", "image", "kernel_package", "dtb", "initramfs", "rootfs_image"):
    entry = data.get(key)
    if entry is not None and (not isinstance(entry, dict) or not entry.get("sha256")):
        fail(f"'{key}' is not a {{path,sha256,size}} object")

def field(name):
    entry = data.get(name) or {}
    return (str(entry.get("sha256") or "-"), str(entry.get("size") or "-"))

boot_sha, boot_size = field("boot_img")
rootfs_sha, rootfs_size = field("rootfs_image")
for key, value in (
    ("release", release),
    ("worktree", worktree),
    ("created_at", str(data.get("created_at") or "-")),
    ("cci_sha256", cci_sha),
    ("cci_size", str(cci_size)),
    ("cci_path", cci_path),
    ("boot_sha256", boot_sha),
    ("boot_size", boot_size),
    ("rootfs_sha256", rootfs_sha),
    ("rootfs_size", rootfs_size),
):
    print(f"{key}\t{value}")
PY
    ); then
        exit 1
    fi

    MANIFEST_CREATED=""
    BOOT_SHA="-"
    BOOT_SIZE="-"
    ROOTFS_SHA="-"
    ROOTFS_SIZE="-"
    local key value
    while IFS=$'\t' read -r key value; do
        case "$key" in
            created_at) MANIFEST_CREATED=$value ;;
            cci_sha256) EXPECTED_CCI_SHA256=$value ;;
            cci_size) EXPECTED_CCI_SIZE=$value ;;
            boot_sha256) BOOT_SHA=$value ;;
            boot_size) BOOT_SIZE=$value ;;
            rootfs_sha256) ROOTFS_SHA=$value ;;
            rootfs_size) ROOTFS_SIZE=$value ;;
        esac
    done <<<"$output"
}

# ---------------------------------------------------------------- output

SUMMARY_FILE=""

say() {
    printf '%s\n' "$*"
    printf '%s\n' "$*" >>"$SUMMARY_FILE"
}

add_check() {
    local status="$1" name="$2" detail="$3" line
    line=$(printf '%-4s %-24s %s' "$status" "$name" "$detail")
    say "$line"
    case "$status" in
        FAIL) CHECKS_FAILED=$((CHECKS_FAILED + 1)) ;;
    esac
}

prepare_collect_dir() {
    local default_dir
    if [[ -n "$COLLECT_DIR" ]]; then
        mkdir -p -- "$COLLECT_DIR"
        COLLECT_DIR="$(cd -- "$COLLECT_DIR" && pwd -P)"
    else
        case "$MODE" in
            local) default_dir="$ROOT/build/rear-camera/diagnostics/host-$(date -u +%Y%m%dT%H%M%SZ)" ;;
            *) default_dir="$ROOT/build/rear-camera/diagnostics/phone-$(date -u +%Y%m%dT%H%M%SZ)" ;;
        esac
        mkdir -p -- "$default_dir"
        COLLECT_DIR="$default_dir"
    fi
    SUMMARY_FILE="$COLLECT_DIR/summary.txt"
    : >"$SUMMARY_FILE"
    RAW="$COLLECT_DIR/remote-raw.txt"
    RAW_ERR="$COLLECT_DIR/remote-stderr.txt"
    SSH_PROBE_ERR="$COLLECT_DIR/ssh-probe-stderr.txt"
    : >"$SSH_PROBE_ERR"

    say "camera_verify — post-boot candidate verification"
    say "collected_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    say "mode: $MODE"
    say "worktree: $ROOT"
    say "manifest: $MANIFEST (created_at $MANIFEST_CREATED)"
    say "expected_release: $EXPECTED_RELEASE"
    say "expected_cci_sha256: $EXPECTED_CCI_SHA256"
    say "expected_cci_size: $EXPECTED_CCI_SIZE"
    say "expected_cci_path: /usr/lib/modules/$EXPECTED_RELEASE/$CCI_RELATIVE_PATH"
    say "boot_img: sha256 $BOOT_SHA ($BOOT_SIZE bytes)"
    say "rootfs_image: sha256 $ROOTFS_SHA ($ROOTFS_SIZE bytes)"
    say "target: $TARGET"
    say ""
}

write_section() {
    local name="$1" text="$2"
    printf '%s\n' "$text" >"$COLLECT_DIR/$name"
}

# ---------------------------------------------------------------- remote probe

remote_probe_script() {
    cat <<'REMOTE_PROBE'
# Read-only camera/CCI probe. Runs as the normal user on the phone.
# CAMERA_VERIFY_SUDO=1 enables only `sudo -n` for read-only dmesg/pm_genpd.
printf '@@UNAME@@\n'
uname -a
printf '@@RELEASE@@\n'
uname -r
printf '@@CMDLINE@@\n'
cat /proc/cmdline 2>&1
release=$(uname -r)
module="/usr/lib/modules/$release/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst"
printf '@@MODULE@@\n'
if [ -f "$module" ]; then
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$module"
    else
        printf 'NO_SHA256SUM %s\n' "$module"
    fi
    size=$(stat -c '%s' "$module" 2>/dev/null) || size=$(wc -c <"$module")
    printf '%s\n' "$size"
else
    printf 'MISSING %s\n' "$module"
fi
printf '@@LSMOD@@\n'
lsmod 2>/dev/null | grep -E 'i2c_qcom_cci|qcom[_-]camss|imx586|imx481|s5k3m5|ak7375|lc898217xc' || true
printf '@@MODULE_SYSFS@@\n'
if [ -d /sys/module/i2c_qcom_cci ]; then printf 'loaded\n'; else printf 'not-loaded\n'; fi
printf '@@DMESG@@\n'
dmesg_output=$(dmesg 2>&1); dmesg_status=$?
if [ "$dmesg_status" -ne 0 ] && [ "${CAMERA_VERIFY_SUDO:-0}" = 1 ]; then
    dmesg_output=$(sudo -n dmesg 2>&1); dmesg_status=$?
fi
if [ "$dmesg_status" -ne 0 ]; then
    printf 'DMESG_DENIED: %s\n' "$dmesg_output"
else
    printf '%s\n' "$dmesg_output" | grep -iE 'cci|i2c-qcom-cci|camss|imx586|imx481|s5k3m5|ak737|lc898|cci reset timeout|Unable to handle|Oops|kernel BUG|Kernel panic|Internal error' | tail -n 300 || true
fi
printf '@@MEDIA@@\n'
if command -v media-ctl >/dev/null 2>&1; then media_tool=1; else media_tool=0; fi
found=0
for media in /dev/media*; do
    [ -e "$media" ] || continue
    found=1
    printf '== %s\n' "$media"
    if [ "$media_tool" = 1 ]; then
        media-ctl -d "$media" -p 2>&1 || printf 'media-ctl failed on %s\n' "$media"
    else
        printf 'media-ctl not installed\n'
    fi
done
[ "$found" = 1 ] || printf 'NO_MEDIA_DEVICES\n'
printf '@@V4L2@@\n'
if command -v v4l2-ctl >/dev/null 2>&1; then
    v4l2-ctl --list-devices 2>&1 || true
else
    printf 'v4l2-ctl not installed\n'
fi
printf '@@CAMREAR@@\n'
if command -v cam-rear >/dev/null 2>&1; then
    cam-rear -l 2>&1 || true
else
    printf 'cam-rear not installed\n'
fi
printf '@@POWER@@\n'
if [ "${CAMERA_VERIFY_SUDO:-0}" = 1 ]; then
    sudo -n cat /sys/kernel/debug/pm_genpd/pm_genpd_summary 2>&1 || printf 'power summary unavailable\n'
else
    printf 'SKIPPED (rerun with --sudo for the read-only pm_genpd summary)\n'
fi
printf '@@END@@\n'
REMOTE_PROBE
}

section() {
    local name="$1"
    awk -v want="@@$name@@" '
        $0 == want { capture = 1; next }
        capture && /^@@[A-Z0-9_]+@@$/ { exit }
        capture { print }
    ' "$RAW"
}

# ---------------------------------------------------------------- ssh

SSH_OPTIONS=(
    -4
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -o UserKnownHostsFile="$KNOWN_HOSTS"
    -o BatchMode=yes
    -o ServerAliveInterval=10
    -o ServerAliveCountMax=3
    -o LogLevel=ERROR
)

ssh_probe() {
    ssh "${SSH_OPTIONS[@]}" "$TARGET" true </dev/null >/dev/null 2>"$SSH_PROBE_ERR"
}

wait_for_ssh() {
    local started=$SECONDS attempt=0
    while :; do
        attempt=$((attempt + 1))
        if ssh_probe; then
            SSH_ATTEMPTS=$attempt
            SSH_ELAPSED=$((SECONDS - started))
            printf 'SSH ready after %ss (attempt %d).\n' "$SSH_ELAPSED" "$attempt"
            return 0
        fi
        SSH_ATTEMPTS=$attempt
        SSH_ELAPSED=$((SECONDS - started))
        if (( SSH_ELAPSED >= WAIT_SECONDS )); then
            return 1
        fi
        if (( attempt == 1 || attempt % 5 == 0 )); then
            printf 'Waiting for %s (attempt %d, %ss/%ss)...\n' \
                "$TARGET" "$attempt" "$SSH_ELAPSED" "$WAIT_SECONDS"
        fi
        sleep 2
    done
}

diagnose_no_ssh() {
    local diagnosis="$COLLECT_DIR/ssh-diagnosis.txt"
    : >"$diagnosis"
    diag() {
        printf '%s\n' "$*" >>"$diagnosis"
    }

    say ""
    say "NO SSH: $TARGET did not answer after ${SSH_ELAPSED}s ($SSH_ATTEMPTS attempt(s))."
    say ""
    say "Host-side diagnosis (read-only):"
    diag "host-side diagnosis for $TARGET at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

    if command -v ip >/dev/null 2>&1; then
        local addr
        addr=$(ip -o -4 addr show 2>&1 | grep -F '172.16.42.2/24' || true)
        if [[ -n "$addr" ]]; then
            say "  host address: $addr"
            diag "host address: $addr"
        else
            say "  host address: MISSING 172.16.42.2/24 (camera_serve.sh configures this)"
            diag "host address: MISSING 172.16.42.2/24"
        fi
    else
        say "  ip: not installed on the host"
        diag "ip: not installed on the host"
    fi

    local net_dir name oper carrier
    for net_dir in /sys/class/net/*; do
        name=${net_dir##*/}
        case "$name" in
            lo|eth*|wl*) continue ;;
        esac
        oper=$(cat "$net_dir/operstate" 2>/dev/null || printf '?')
        carrier=$(cat "$net_dir/carrier" 2>/dev/null || printf '?')
        say "  interface $name: operstate=$oper carrier=$carrier"
        diag "interface $name: operstate=$oper carrier=$carrier"
    done

    if command -v ip >/dev/null 2>&1; then
        local neigh
        neigh=$(ip neigh show 172.16.42.1 2>&1 || true)
        say "  ip neigh: ${neigh:-<empty>}"
        diag "ip neigh: ${neigh:-<empty>}"
    fi

    if command -v ping >/dev/null 2>&1; then
        local ping_out ping_status
        ping_out=$(ping -c1 -W2 172.16.42.1 2>&1) && ping_status=0 || ping_status=$?
        say "  ping -c1 172.16.42.1: exit $ping_status"
        diag "ping -c1 172.16.42.1: exit $ping_status"
        diag "$ping_out"
    else
        say "  ping: not installed on the host"
        diag "ping: not installed on the host"
    fi

    if command -v nc >/dev/null 2>&1; then
        local nc_out nc_status
        nc_out=$(nc -vz -w2 172.16.42.1 22 2>&1) && nc_status=0 || nc_status=$?
        say "  nc -vz 172.16.42.1 22: exit $nc_status (${nc_out:-no output})"
        diag "nc -vz 172.16.42.1 22: exit $nc_status"
        diag "$nc_out"
    else
        say "  nc: not installed on the host"
        diag "nc: not installed on the host"
    fi

    if grep -qiE 'REMOTE HOST IDENTIFICATION HAS CHANGED|Host key verification failed' "$SSH_PROBE_ERR"; then
        say "  ssh host key mismatch: the served rootfs image (and its SSH host key) changed."
        say "  Remove the stale entry, then rerun this script:"
        say "    ssh-keygen -R 172.16.42.1 -f $KNOWN_HOSTS"
        diag "ssh host key mismatch; remove the stale entry with:"
        diag "  ssh-keygen -R 172.16.42.1 -f $KNOWN_HOSTS"
    fi

    say ""
    say "Diagnosis:"
    diag ""
    diag "Diagnosis:"
    diagnose_step "Is scripts/camera_serve.sh still running in its terminal? The phone rootfs is served over NBD (port 10001); if the server stopped, the phone freezes or resets." "$diagnosis"
    diagnose_step "Is the phone booted into the candidate (build/rear-camera/boot.img)? If it is in fastboot or Android, run: bash $ROOT/scripts/camera_boot.sh" "$diagnosis"
    diagnose_step "Does the host USB interface have 172.16.42.2/24? scripts/camera_serve.sh sets this up." "$diagnosis"
    diagnose_step "When UFW is enabled, allow inbound TCP 22 (and TCP 10001 for NBD) from 172.16.42.1 to 172.16.42.2 on the USB interface; see docs/LIVE_BASELINE.md." "$diagnosis"
    diagnose_step "NetworkManager may take over the USB gadget link; see the route-metric notes in CLAUDE.md." "$diagnosis"
    diagnose_step "SSH runs with BatchMode=yes (no password prompt). If interactive ssh only works with a password, install a key for user@172.16.42.1 first." "$diagnosis"
    diagnose_step "If the phone screen shows a kernel oops/panic, photograph it BEFORE rebooting. Next host-side collection is scripts/ramoops.sh from TWRP (pstore); do not reboot first." "$diagnosis"
    say ""
    say "Do not reboot the phone automatically: this script never reboots anything."
}

diagnose_step() {
    local text="$1" file="$2"
    say "  - $text"
    printf '  - %s\n' "$text" >>"$file"
}

# ---------------------------------------------------------------- checks

section_counts() {
    local text="$1" pattern="$2"
    printf '%s\n' "$text" | grep -icE "$pattern" || true
}

first_match() {
    local text="$1" pattern="$2"
    printf '%s\n' "$text" | grep -iE "$pattern" | head -n 1 || true
}

check_release() {
    local observed
    observed=$(section RELEASE | sed -n '1p')
    if [[ "$observed" == "$EXPECTED_RELEASE" ]]; then
        add_check PASS "kernel release" "$observed"
    else
        add_check FAIL "kernel release" "observed '${observed:-missing}', expected '$EXPECTED_RELEASE'"
        STALE=1
    fi
}

check_cmdline() {
    local cmdline tokens
    cmdline=$(section CMDLINE)
    tokens=$(printf '%s\n' "$cmdline" | grep -oE 'pmos\.[a-z]+=[^ ]+' | tr '\n' ' ' || true)
    if [[ " $tokens " == *" pmos.root=/dev/nbd0p2 "* ]]; then
        add_check PASS "netboot root cmdline" "pmos.root=/dev/nbd0p2"
    else
        add_check FAIL "netboot root cmdline" "missing pmos.root=/dev/nbd0p2 (tokens: ${tokens:-none})"
    fi
}

check_module() {
    local module_text module_first detail module_ok=0
    module_text=$(section MODULE)
    module_first=$(printf '%s\n' "$module_text" | sed -n '1p')
    MODULE_SHA=""
    MODULE_SIZE=""
    MODULE_PATH=""
    if [[ "$module_first" == MISSING* ]]; then
        MODULE_PATH=${module_first#MISSING }
        detail="missing at ${MODULE_PATH:-unknown path}"
    elif [[ "$module_first" == NO_SHA256SUM* ]]; then
        MODULE_PATH=${module_first#NO_SHA256SUM }
        detail="cannot verify: sha256sum is not installed on the phone (${MODULE_PATH:-unknown path})"
    elif [[ -z "$module_first" ]]; then
        detail="no module data returned"
    else
        MODULE_SHA=$(printf '%s\n' "$module_first" | awk '{print tolower($1)}')
        MODULE_PATH=$(printf '%s\n' "$module_first" | awk '{print $2}')
        MODULE_SIZE=$(printf '%s\n' "$module_text" | sed -n '2p' | tr -d '[:space:]')
        if [[ "$MODULE_SHA" == "$EXPECTED_CCI_SHA256" && "$MODULE_SIZE" == "$EXPECTED_CCI_SIZE" ]]; then
            detail="$MODULE_SHA ($MODULE_SIZE bytes)"
            module_ok=1
        else
            detail="observed ${MODULE_SHA:-unknown} (${MODULE_SIZE:-unknown} bytes) at ${MODULE_PATH:-unknown path}"
        fi
    fi
    if (( module_ok == 0 )); then
        STALE=1
    fi

    {
        printf 'expected_sha256: %s\n' "$EXPECTED_CCI_SHA256"
        printf 'expected_size: %s\n' "$EXPECTED_CCI_SIZE"
        printf 'observed_sha256: %s\n' "${MODULE_SHA:-missing}"
        printf 'observed_size: %s\n' "${MODULE_SIZE:-missing}"
        printf 'observed_path: %s\n' "${MODULE_PATH:-missing}"
        printf 'result: %s\n' "$([[ $module_ok == 1 ]] && printf PASS || printf FAIL)"
    } >"$COLLECT_DIR/module-sha256.txt"

    if (( module_ok == 1 )); then
        add_check PASS "cci module identity" "$detail"
    else
        add_check FAIL "cci module identity" "$detail"
    fi
}

check_lsmod() {
    local lsmod_text sysfs loaded present missing module
    lsmod_text=$(section LSMOD)
    sysfs=$(section MODULE_SYSFS | sed -n '1p')
    loaded=no
    if [[ "$sysfs" == "loaded" ]] || grep -qE '^i2c_qcom_cci[[:space:]]' <<<"$lsmod_text"; then
        loaded=yes
    fi
    if [[ "$loaded" == yes ]]; then
        add_check PASS "cci module loaded" "i2c_qcom_cci is loaded"
    else
        add_check WARN "cci module loaded" "not loaded (no /sys/module/i2c_qcom_cci on $TARGET_LABEL); check dmesg and cam-rear -l"
    fi

    present=""
    missing=""
    for module in qcom_camss imx586 imx481 s5k3m5 ak7375 lc898217xc; do
        if grep -qE "^${module}[[:space:]]" <<<"$lsmod_text"; then
            present+="$module "
        else
            missing+="$module "
        fi
    done
    add_check INFO "sensor/camss modules" \
        "loaded: ${present:-none}; not loaded: ${missing:-none}"
}

check_dmesg() {
    local dmesg_text oops_count timeout_count matches oops_first timeout_first
    dmesg_text=$(section DMESG)
    OOPS=0
    TIMEOUT=0

    if [[ "$dmesg_text" == DMESG_DENIED:* ]]; then
        add_check WARN "dmesg camera lines" \
            "dmesg is denied on $TARGET_LABEL; rerun with --sudo, or run scripts/collect_rear_camera.sh on the phone"
        return
    fi

    oops_count=$(section_counts "$dmesg_text" 'Unable to handle kernel|Oops|kernel BUG at|Kernel panic|Internal error')
    timeout_count=$(section_counts "$dmesg_text" 'CCI (reset|halt) timeout|master [0-9]+ queue [0-9]+ timeout')
    matches=$(section_counts "$dmesg_text" 'cci|camss|imx586|imx481|s5k3m5|ak737|lc898')
    oops_first=$(first_match "$dmesg_text" 'Unable to handle kernel|Oops|kernel BUG at|Kernel panic|Internal error')
    timeout_first=$(first_match "$dmesg_text" 'CCI (reset|halt) timeout|master [0-9]+ queue [0-9]+ timeout')

    if (( oops_count > 0 )); then
        OOPS=1
        add_check FAIL "dmesg camera lines" "OOPS/PANIC: ${oops_first:0:160}"
    elif (( timeout_count > 0 )); then
        TIMEOUT=1
        add_check FAIL "dmesg camera lines" "CCI timeout: ${timeout_first:0:160}"
    elif (( matches == 0 )); then
        add_check WARN "dmesg camera lines" \
            "no CCI/CAMSS/sensor lines (a clean probe is silent; CCI logs only CCI HW version at debug level)"
    else
        add_check PASS "dmesg camera lines" "$matches matching line(s); no CCI timeout or oops"
    fi
}

check_media() {
    local media_text devices tool_missing missing_sensors unlinked_sensors sensor lens lenses
    media_text=$(section MEDIA)
    devices=$(printf '%s\n' "$media_text" | grep -c '^== /dev/media' || true)
    tool_missing=no
    if grep -q 'media-ctl not installed' <<<"$media_text"; then
        tool_missing=yes
    fi

    if (( devices == 0 )); then
        add_check FAIL "media topology" "no /dev/media* on $TARGET_LABEL (the CAMSS graph was not created)"
    elif [[ "$tool_missing" == yes ]]; then
        add_check SKIP "media topology" "$devices media device(s); media-ctl is not installed on $TARGET_LABEL"
    else
        missing_sensors=""
        unlinked_sensors=""
        for sensor in imx586 imx481 s5k3m5; do
            if ! grep -qi -- "$sensor" <<<"$media_text"; then
                missing_sensors+="$sensor "
            elif ! grep -qiE "(->|<-).*${sensor}" <<<"$media_text"; then
                unlinked_sensors+="$sensor "
            fi
        done
        lenses=""
        for lens in ak7375 lc898217xc; do
            if grep -qi -- "$lens" <<<"$media_text"; then
                lenses+="$lens "
            fi
        done
        if [[ -z "$missing_sensors" && -z "$unlinked_sensors" ]]; then
            add_check PASS "media topology" \
                "$devices media device(s); imx586, imx481 and s5k3m5 linked; lens entities: ${lenses:-none}"
        else
            add_check FAIL "media topology" \
                "$devices media device(s); missing sensor entities: ${missing_sensors:-none}; not linked: ${unlinked_sensors:-none}"
        fi
    fi
}

check_v4l2() {
    local v4l2_text nodes
    v4l2_text=$(section V4L2)
    if grep -q 'v4l2-ctl not installed' <<<"$v4l2_text"; then
        add_check SKIP "v4l2 devices" "v4l2-ctl is not installed on $TARGET_LABEL"
        return
    fi
    nodes=$(grep -oE '/dev/video[0-9]+' <<<"$v4l2_text" | sort -u | wc -l | tr -d '[:space:]' || true)
    if [[ "$nodes" == "0" ]]; then
        add_check WARN "v4l2 devices" "no /dev/video* nodes listed"
    else
        add_check INFO "v4l2 devices" "$nodes video node(s); see v4l2-devices.txt"
    fi
}

check_cam_rear() {
    local text lines
    text=$(section CAMREAR)
    if grep -q 'cam-rear not installed' <<<"$text"; then
        add_check SKIP "cam-rear -l" "cam-rear is not installed on $TARGET_LABEL"
        return
    fi
    lines=$(printf '%s\n' "$text" | grep -c '[^[:space:]]' || true)
    if (( lines == 0 )); then
        add_check WARN "cam-rear -l" "no output"
    else
        add_check INFO "cam-rear -l" "$lines line(s) captured; a listing is not a stream test (see cam-rear-list.txt)"
    fi
}

check_power() {
    local text rows
    text=$(section POWER)
    if grep -q '^SKIPPED' <<<"$text"; then
        add_check SKIP "pm_genpd summary" "not collected by default; rerun with --sudo"
        return
    fi
    if grep -q 'power summary unavailable' <<<"$text" || [[ -z "${text//[[:space:]]/}" ]]; then
        add_check WARN "pm_genpd summary" "unavailable (passwordless sudo required)"
        return
    fi
    rows=$(grep -ciE 'camcc|camss|mmcx' <<<"$text" || true)
    add_check INFO "pm_genpd summary" "$rows camera/power row(s); see power-summary.txt"
}

collect_sections() {
    write_section uname.txt "$(section UNAME)"
    write_section cmdline.txt "$(section CMDLINE)"
    write_section lsmod.txt "$(section LSMOD)"
    write_section dmesg-grep.txt "$(section DMESG)"
    write_section media-topology.txt "$(section MEDIA)"
    write_section v4l2-devices.txt "$(section V4L2)"
    write_section cam-rear-list.txt "$(section CAMREAR)"
    write_section power-summary.txt "$(section POWER)"
}

# ---------------------------------------------------------------- verdict

stale_banner() {
    say ""
    say "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    say "STALE MODULE — the running system is NOT the candidate recorded in DELIVERY.json."
    say "observed: ${MODULE_SHA:-missing} (${MODULE_SIZE:-unknown} bytes) at ${MODULE_PATH:-unknown}"
    say "expected: $EXPECTED_CCI_SHA256 ($EXPECTED_CCI_SIZE bytes)"
    say ""
    say "The netboot cache is reused unless it is replaced. The stale copy is:"
    say "  $ROOT/.work/rear-camera/pmbootstrap-work/images_netboot/oneplus-guacamole.img"
    say ""
    say "Fix, from this worktree ($ROOT):"
    say "  1. Stop the candidate server (Ctrl-C in its terminal), if it is still running."
    say "  2. bash $ROOT/scripts/camera_serve.sh --replace"
    say "     (refreshes the stopped served image from the current rootfs and verifies the recorded module)"
    say "  3. Reboot the phone to the bootloader and boot the candidate:"
    say "     bash $ROOT/scripts/camera_boot.sh"
    say "  4. After boot: bash $ROOT/scripts/camera_verify.sh"
    say ""
    say "Do not boot build/rear-camera/boot.img while the stale netboot image is still served."
    say "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
}

oops_banner() {
    say ""
    say "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    say "OOPS/PANIC in dmesg — photograph the phone screen BEFORE rebooting."
    say "The panic text may not survive a power loss. Next host-side collection is"
    say "scripts/ramoops.sh from the host while the phone is in TWRP (pstore); it is"
    say "not run by this script."
    say "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
}

final_report() {
    local fixed_detail loaded
    loaded=$(grep -cE '^i2c_qcom_cci[[:space:]]' <<<"$(section LSMOD)" || true)
    if (( STALE != 0 )); then
        VERDICT="STALE MODULE"
    elif (( OOPS != 0 )); then
        VERDICT="OOPS"
    elif (( TIMEOUT != 0 )); then
        VERDICT="PROBE TIMEOUT"
    else
        VERDICT="FIXED MODULE RUNNING"
    fi

    say ""
    if [[ "$VERDICT" == "FIXED MODULE RUNNING" ]]; then
        fixed_detail="loaded=yes"
        if (( loaded == 0 )); then
            fixed_detail="installed but not currently loaded"
        fi
        say "VERDICT: FIXED MODULE RUNNING ($fixed_detail)"
    else
        say "VERDICT: $VERDICT"
    fi
    say "Collected: $COLLECT_DIR"

    if (( STALE != 0 )); then
        stale_banner
    fi
    if (( OOPS != 0 )); then
        oops_banner
    fi
    if [[ "$VERDICT" == "PROBE TIMEOUT" ]]; then
        say ""
        say "PROBE TIMEOUT — the fixed module is installed, but the CCI controller reset timed out."
        say "Keep the session read-only; capture the full log with scripts/ramoops.sh after the"
        say "phone is back in TWRP, then compare against docs/REAR_CAMERA_7_0_BRINGUP.md."
    fi

    if [[ "$MODE" == "local" ]]; then
        say ""
        say "LOCAL MODE ran these checks on THIS HOST, not on the phone; nothing here proves the phone state."
    fi
}

# ---------------------------------------------------------------- main

load_manifest
printf 'Worktree: %s\n' "$ROOT"
printf 'Manifest: %s (created_at %s)\n' "$MANIFEST" "$MANIFEST_CREATED"
printf 'Release:  %s\n' "$EXPECTED_RELEASE"
printf 'boot.img: sha256 %s (%s bytes)\n' "$BOOT_SHA" "$BOOT_SIZE"
printf 'rootfs:   sha256 %s (%s bytes)\n' "$ROOTFS_SHA" "$ROOTFS_SIZE"
printf 'Expected CCI: %s (%s bytes)\n' "$EXPECTED_CCI_SHA256" "$EXPECTED_CCI_SIZE"
printf 'Target:   %s\n' "$TARGET"

REMOTE_SCRIPT=$(remote_probe_script)

if [[ "$MODE" == "dry-run" ]]; then
    printf '\nDRY RUN — no SSH connection, no files written.\n\n'
    printf 'SSH command:\n  ssh'
    printf ' %q' "${SSH_OPTIONS[@]}"
    printf ' %q %s\n\n' "$TARGET" "'CAMERA_VERIFY_SUDO=$USE_SUDO sh -s'"
    printf 'Remote read-only checks (piped to sh -s):\n'
    printf '%s\n' "$REMOTE_SCRIPT"
    exit 0
fi

prepare_collect_dir
printf 'Collecting into: %s\n' "$COLLECT_DIR"
: >"$RAW"
: >"$RAW_ERR"
CHECKS_FAILED=0
STALE=0
OOPS=0
TIMEOUT=0
VERDICT=""
MODULE_SHA=""
MODULE_SIZE=""
MODULE_PATH=""

if [[ "$MODE" == "local" ]]; then
    printf '\nLOCAL MODE — running the read-only checks on THIS HOST, not on the phone.\n'
    say "LOCAL MODE — these results are not evidence about the phone."
    CAMERA_VERIFY_SUDO=$USE_SUDO sh -s <<<"$REMOTE_SCRIPT" >"$RAW" 2>"$RAW_ERR" || true
else
    mkdir -p -- "$(dirname -- "$KNOWN_HOSTS")"
    ssh_status=0
    if ! wait_for_ssh; then
        diagnose_no_ssh
        say ""
        say "VERDICT: NO SSH"
        say "Collected: $COLLECT_DIR"
        exit 1
    fi
    ssh "${SSH_OPTIONS[@]}" "$TARGET" "CAMERA_VERIFY_SUDO=$USE_SUDO sh -s" \
        <<<"$REMOTE_SCRIPT" >"$RAW" 2>"$RAW_ERR" || ssh_status=$?
    if (( ssh_status != 0 )); then
        say "Remote collection failed (ssh exit $ssh_status); see $RAW_ERR"
        say ""
        say "VERDICT: NO SSH (the connection closed during collection)"
        say "Collected: $COLLECT_DIR"
        exit 1
    fi
fi

if ! grep -q '^@@END@@$' "$RAW"; then
    say "Remote collection is incomplete (missing @@END@@); see $RAW and $RAW_ERR"
    say ""
    say "VERDICT: NO SSH (incomplete collection)"
    say "Collected: $COLLECT_DIR"
    exit 1
fi

collect_sections
check_release
check_cmdline
check_module
check_lsmod
check_dmesg
check_media
check_v4l2
check_cam_rear
check_power
final_report

if [[ "$MODE" == "local" ]]; then
    exit 1
fi
if [[ "$VERDICT" == "STALE MODULE" || "$VERDICT" == "OOPS" || "$VERDICT" == "PROBE TIMEOUT" ]]; then
    exit 1
fi
if (( CHECKS_FAILED > 0 )); then
    exit 2
fi
exit 0
