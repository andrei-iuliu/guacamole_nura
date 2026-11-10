#!/usr/bin/env bash
# Serve the *current* rear-camera rootfs over NBD on the candidate port.
#
# The candidate owns port 10001 and its own pmbootstrap work directory. The
# working baseline (port 9999/10000) is only ever inspected, never stopped,
# replaced or killed.
#
# This entrypoint exists because `pmbootstrap netboot serve` reuses
# <work>/images_netboot/<device>.img silently unless it is missing or
# --replace is passed, which served a pre-fix rootfs (8920-byte
# i2c-qcom-cci.ko.zst) instead of the fixed one (8950 bytes). It:
#   1. reads build/rear-camera/DELIVERY.json and prints the identity,
#   2. reads the chroot rootfs image (GPT 4K + debugfs, read-only) and proves
#      it carries release 7.0.0-sm8150-rear1, exactly one module tree, the
#      build's manifest CCI module, the manifest DTB and the port-10001 hook,
#   3. compares the served cache with that image; when it differs, refuses to
#      touch a cache that is currently being served (port 10001 in use), copies
#      to a sibling temporary file, verifies that file and only then renames it
#      over the served cache (no truncation of a live NBD backing file),
#   4. configures the USB host address 172.16.42.2/24 through
#      camera_usb_net.sh (finite wait, idempotent),
#   5. starts the server and proves the cache file that was verified before
#      exposure is still the same inode; it never re-hashes a whole image after
#      the phone can write to it through NBD.
#
# The expected i2c-qcom-cci sha256/size comes from build/rear-camera/DELIVERY.json
# for every build; no single binary hash is baked into this script.
#
#   scripts/camera_serve.sh --check          # validate only, no server
#   scripts/camera_serve.sh                 # serve on 10001 (auto refresh)
#   scripts/camera_serve.sh --dry-run       # print every command, run none
set -Eeuo pipefail

PROG=${0##*/}
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
RELEASE=7.0.0-sm8150-rear1
DEFAULT_PORT=10001
DEVICE=oneplus-guacamole

STATE_ROOT=$ROOT/.work/rear-camera
WORK=$STATE_ROOT/pmbootstrap-work
SOURCE_IMAGE=$WORK/chroot_native/home/pmos/rootfs/$DEVICE.img
SERVED_IMAGE=$WORK/images_netboot/$DEVICE.img
PMB=$STATE_ROOT/pmbootstrap/pmbootstrap.py
CFG=$STATE_ROOT/rootfs/pmbootstrap.cfg
APORTS=$STATE_ROOT/pmaports
USB_NET=$ROOT/scripts/camera_usb_net.sh
MANIFEST=$ROOT/build/rear-camera/DELIVERY.json
BOOT_IMG=$ROOT/build/rear-camera/boot.img

DELIVERY_DIR=${NURA_STATE_DIR:-$ROOT/.work/delivery}
SCRATCH=$DELIVERY_DIR/H
IDENTITY=$SCRATCH/identity.json
SERVED_RECORD=$DELIVERY_DIR/served-image.json
LOG=$SCRATCH/serve-netboot.log
KNOWN_HOSTS=$DELIVERY_DIR/known_hosts

PORT=$DEFAULT_PORT
IFACE_ARG=${NURA_USB_IFACE:-}
TIMEOUT=180
REPLACE=auto
ASSUME_YES=0
DRY_RUN=0
CHECK=0
RECHECK=0
WATCH=1
REFRESH=no
PRECOPIED=no
EXPECTED_CCI_SHA=
EXPECTED_CCI_SIZE=
SERVED_SHA=
SERVED_GENERATION=
REFRESH_TMP=
SERVER_PID=
WATCHER_PID=
TMP=

usage() {
    cat <<'EOF'
Usage: camera_serve.sh [options]

Serves the rear-camera rootfs from this checkout over NBD on port 10001.
Refreshes <work>/images_netboot/oneplus-guacamole.img from the current chroot
rootfs image when they differ and refuses to print READY unless the image that
is really served contains the i2c-qcom-cci module recorded in DELIVERY.json.
A cache that is being served (port 10001 in use) is never replaced.

Options:
  --check          validate manifest, image, cache and ports; start nothing
  --dry-run        print every command without running sudo/ip/pmbootstrap
  --replace        always refresh the served cache (refused while port 10001 is in use)
  --no-replace     never refresh; fail if the served cache is stale
  --port N         NBD port (default 10001; 9999 and 10000 are refused)
  --iface NAME     USB network interface (default: auto-detect, NURA_USB_IFACE)
  --timeout SECS   interface wait timeout, never infinite (default 180)
  --yes            confirm the cache replacement and stale-port cleanup
  --no-watch       do not run the USB/IP reconnection watcher
  --rehash         ignore cached sha256 results and hash again
  -h, --help       show this help

Exit status: 0 ready/served, 1 failed or refused.
EOF
}

die() { printf 'FAIL  %s: %s\n' "$PROG" "$*" >&2; exit 1; }
note() { printf '%s\n' "$*"; }
info() { printf '%s\n' "$*" >&2; }
pass() { printf 'PASS  %s\n' "$*"; }
warn() { printf 'WARN  %s\n' "$*" >&2; }
action() { printf 'ACTION  %s\n' "$*"; }

cmd_string() {
    local out='' word
    for word in "$@"; do out+=${out:+ }$(printf '%q' "$word"); done
    printf '%s' "$out"
}

# Run a privileged command (sudo when needed), or print it under --dry-run.
run() {
    local -a privileged=(sudo)
    (( EUID == 0 )) && privileged=()
    if (( DRY_RUN )); then
        printf 'DRY-RUN: %s\n' "$(cmd_string "${privileged[@]}" "$@")"
        return 0
    fi
    info "+ $(cmd_string "${privileged[@]}" "$@")"
    "${privileged[@]}" "$@"
}

confirm() {
    local prompt=$1 answer
    if (( ASSUME_YES )); then
        info "$prompt -> yes (--yes)"
        return 0
    fi
    if [[ ! -t 0 ]]; then
        die "confirmation required in a non-interactive shell: $prompt (rerun with --yes)"
    fi
    read -r -p "$prompt [y/N] " answer
    [[ $answer == [yY]* ]]
}

json_get() {
    python3 -c 'import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    value = value[key]
print(value)' "$IDENTITY" "$@"
}

on_error() {
    local status=$? line=$1
    printf 'ERROR %s failed at line %s (status %s)\n' "$PROG" "$line" "$status" >&2
    exit "$status"
}
trap 'on_error $LINENO' ERR

# ---------------------------------------------------------------- artifacts

# sha256 of a file, cached by (size, mtime, inode) under .work/delivery/H.
hash_cached() {
    local path=$1 size mtime inode fingerprint state sha
    size=$(stat -c %s -- "$path")
    mtime=$(stat -c %Y -- "$path")
    inode=$(stat -c %i -- "$path")
    fingerprint="$size:$mtime:$inode"
    state=$SCRATCH/hashes/$(printf '%s' "$path" | sha256sum | cut -c1-16).state
    if (( ! RECHECK )) && [[ -r $state ]]; then
        sha=$(sed -n 's/^SHA256=//p' "$state" | head -n1)
        if [[ $(sed -n 's/^FINGERPRINT=//p' "$state" | head -n1) == "$fingerprint" ]]; then
            info "sha256 $path (cached: size, mtime, inode unchanged)"
            printf '%s' "$sha"
            return 0
        fi
    fi
    info "hashing $path ($size bytes); this reads the whole file"
    sha=$(sha256sum -- "$path" | cut -d' ' -f1)
    mkdir -p -- "$SCRATCH/hashes"
    printf 'PATH=%s\nFINGERPRINT=%s\nSHA256=%s\n' "$path" "$fingerprint" "$sha" >"$state"
    printf '%s' "$sha"
}

sha_of() { sha256sum -- "$1" | cut -d' ' -f1; }

# Validate DELIVERY.json and normalise it into $IDENTITY for the shell.
validate_manifest() {
    python3 - "$MANIFEST" "$ROOT" "$RELEASE" \
        "$SOURCE_IMAGE" "$PORT" "$IDENTITY" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

(manifest_arg, root_arg, release, source_arg, port_arg, out_arg) = sys.argv[1:7]
root = Path(root_arg)
source = Path(source_arg).resolve()
port = int(port_arg)
hexdigits = set('0123456789abcdef')
failures = []
identity = {}


def fail(message):
    failures.append(message)
    print(f'FAIL  {message}', file=sys.stderr)


def digest(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 << 20), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


try:
    data = json.loads(Path(manifest_arg).read_text())
except OSError as exc:
    print(f'FAIL  cannot read {manifest_arg}: {exc}')
    raise SystemExit(1)
except json.JSONDecodeError as exc:
    print(f'FAIL  {manifest_arg} is not valid JSON: {exc}')
    raise SystemExit(1)

if data.get('release') != release:
    fail(f"release {data.get('release')!r} != {release!r}")
else:
    print(f'PASS  release {release}')
    identity['release'] = release

for field in ('worktree', 'created_at', 'git_head'):
    if field in data:
        identity[field] = data[field]

declared_worktree = data.get('worktree')
if not isinstance(declared_worktree, str) or not declared_worktree:
    fail('manifest has no worktree')
else:
    candidates = {Path(declared_worktree), Path(declared_worktree).resolve()}
    if root in candidates or root.resolve() in candidates:
        print(f'PASS  worktree {declared_worktree}')
    else:
        fail(f'manifest worktree {declared_worktree} is not this checkout {root}')

if 'nbd_port' in data:
    if int(data['nbd_port']) != port:
        fail(f"manifest nbd_port {data['nbd_port']} != requested {port}")
    else:
        print(f'PASS  nbd_port {port}')
        identity['nbd_port'] = port

REQUIRED = {
    'boot_img': ('boot_img',),
    'image': ('image',),
    'kernel_package': ('kernel_package', 'vmlinuz'),
    'dtb': ('dtb',),
    'initramfs': ('initramfs',),
    'rootfs_image': ('rootfs_image',),
    'i2c_qcom_cci': ('i2c_qcom_cci',),
}


def pick(*names):
    for name in names:
        value = data.get(name)
        if isinstance(value, dict):
            return name, value
        if isinstance(value, str) and name == 'i2c_qcom_cci':
            return name, {'sha256': value}
    return names[0], None


for key, names in REQUIRED.items():
    label, value = pick(*names)
    if value is None:
        fail(f'manifest entry {key} missing (looked for {"/".join(names)})')
        continue
    sha = value.get('sha256')
    size = value.get('size')
    if not isinstance(sha, str) or len(sha) != 64 or set(sha) - hexdigits:
        fail(f'{key}: sha256 missing or malformed')
        continue
    if not isinstance(size, int) or size <= 0:
        fail(f'{key}: size missing or invalid')
        continue
    declared_path = value.get('path')
    if not isinstance(declared_path, str) or not declared_path:
        fail(f'{key}: path missing (entry {label})')
        continue
    path = Path(declared_path)
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        fail(f'{key}: {path} does not exist')
        continue
    actual_size = path.stat().st_size
    if actual_size != size:
        fail(f'{key}: {path} is {actual_size} bytes, manifest says {size}')
        continue
    if key == 'rootfs_image':
        print(f'PASS  {key} {path} {size} bytes (sha256 verified by the caller)')
    else:
        actual_sha = digest(path)
        if actual_sha != sha:
            fail(f'{key}: {path} sha256 {actual_sha} != {sha}')
            continue
        print(f'PASS  {key} {actual_sha} {size} bytes')
    identity[key] = {'path': str(path), 'sha256': sha, 'size': size}

cci = identity.get('i2c_qcom_cci')
if cci is not None:
    if not cci['path'].endswith('/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst'):
        fail(f"i2c_qcom_cci path {cci['path']} is not an i2c-qcom-cci.ko.zst module")
    else:
        print(f"PASS  i2c_qcom_cci {cci['sha256']} ({cci['size']} bytes) is this build's module")

rootfs = identity.get('rootfs_image')
if rootfs is not None:
    if Path(rootfs['path']).resolve() != source:
        fail(f"rootfs_image {rootfs['path']} is not the current chroot image {source}")
    else:
        print(f'PASS  rootfs_image is the current chroot image')

if failures:
    print(f'FAIL  {len(failures)} manifest check(s) failed', file=sys.stderr)
    raise SystemExit(1)

out = Path(out_arg)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(identity, indent=2, sort_keys=True) + '\n')
print(f'PASS  wrote {out}')
PY
}

# --------------------------------------------------------- rootfs inspection
# Read-only: GPT (4 KiB or 512 B sectors) -> partition offsets, then debugfs
# on "<image>?offset=N" so nothing is mounted, looped or copied.

gpt_sector_size() {
    local image=$1 sector signature
    for sector in 4096 512; do
        signature=$(od -An -tx1 -j "$sector" -N 8 -- "$image" | tr -d ' \n')
        if [[ $signature == 4546492050415254 ]]; then
            printf '%s' "$sector"
            return 0
        fi
    done
    return 1
}

gpt_partitions() {
    local image=$1 sector=$2 entry_lba entries entry_size index offset type first last
    entry_lba=$(od -An -tu8 -j "$((sector + 72))" -N 8 -- "$image" | tr -d ' \n')
    entries=$(od -An -tu4 -j "$((sector + 80))" -N 4 -- "$image" | tr -d ' \n')
    entry_size=$(od -An -tu4 -j "$((sector + 84))" -N 4 -- "$image" | tr -d ' \n')
    [[ $entry_lba =~ ^[0-9]+$ && $entries =~ ^[0-9]+$ && $entry_size =~ ^[0-9]+$ ]] \
        || return 1
    for ((index = 0; index < entries; index++)); do
        offset=$((entry_lba * sector + index * entry_size))
        type=$(od -An -tx1 -j "$offset" -N 16 -- "$image" | tr -d ' \n')
        [[ $type == 00000000000000000000000000000000 ]] && continue
        first=$(od -An -tu8 -j "$((offset + 32))" -N 8 -- "$image" | tr -d ' \n')
        last=$(od -An -tu8 -j "$((offset + 40))" -N 8 -- "$image" | tr -d ' \n')
        printf '%s %s\n' "$((first * sector))" "$(((last - first + 1) * sector))"
    done
}

image_open() { printf '%s?offset=%s' "$1" "$2"; }

image_ls() {
    debugfs -R "ls -p $3" "$(image_open "$1" "$2")" 2>/dev/null
}

image_entries() {
    image_ls "$1" "$2" "$3" | awk -F/ 'NF >= 7 && $6 != "." && $6 != ".." {print $6}'
}

# Dump one file out of an ext4 partition; debugfs -R cat reports a missing file
# on stdout, so the destination file is the trustworthy signal.
image_dump() {
    local image=$1 offset=$2 source=$3 destination=$4
    rm -f -- "$destination"
    debugfs -R "dump $source $destination" "$(image_open "$image" "$offset")" \
        >/dev/null 2>&1 || true
    [[ -f $destination ]]
}

find_partition() {
    local image=$1 want=$2 sector offset size
    sector=$(gpt_sector_size "$image") || return 1
    while read -r offset size; do
        [[ -n $offset ]] || continue
        if [[ $want == root ]]; then
            if [[ -n $(image_ls "$image" "$offset" /usr/lib/modules) ]]; then
                printf '%s' "$offset"
                return 0
            fi
        elif [[ -n $(image_entries "$image" "$offset" / | grep -x vmlinuz) ]]; then
            printf '%s' "$offset"
            return 0
        fi
    done < <(gpt_partitions "$image" "$sector")
    return 1
}

# Prove one image is this release: module tree, manifest CCI module, DTB, boot
# artifacts and the netboot hook port. Runs on the chroot image and once on the
# served cache/staging copy before it is exposed to the phone.
inspect_image() {
    local label=$1 image=$2 root_offset boot_offset modules tree module dtb
    local vmlinuz initramfs hook expected
    [[ -f $image ]] || die "$label: $image does not exist"
    root_offset=$(find_partition "$image" root) \
        || die "$label: no ext4 root partition with /usr/lib/modules in $image"
    boot_offset=$(find_partition "$image" boot) \
        || die "$label: no ext4 boot partition with /vmlinuz in $image"
    pass "$label: GPT partitions parsed read-only (root offset $root_offset, boot offset $boot_offset)"

    tree=$(image_entries "$image" "$root_offset" /usr/lib/modules | paste -sd, -)
    [[ $tree == "$RELEASE" ]] \
        || die "$label: module trees are '${tree:-none}', expected exactly '$RELEASE'"
    pass "$label: exactly one module tree ($RELEASE)"

    module=/usr/lib/modules/$RELEASE/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst
    image_dump "$image" "$root_offset" "$module" "$TMP/module" \
        || die "$label: $module is missing"
    local module_sha module_size
    module_sha=$(sha_of "$TMP/module")
    module_size=$(stat -c %s -- "$TMP/module")
    if [[ $module_sha != "$EXPECTED_CCI_SHA" || $module_size != "$EXPECTED_CCI_SIZE" ]]; then
        die "$label: STALE MODULE i2c-qcom-cci $module_sha ($module_size bytes), this build's manifest says $EXPECTED_CCI_SHA ($EXPECTED_CCI_SIZE bytes); rebuild and rerun camera_build.sh manifest"
    fi
    pass "$label: i2c-qcom-cci $module_sha ($module_size bytes)"

    image_dump "$image" "$root_offset" /usr/share/mkinitfs/hooks/netboot.sh "$TMP/hook" \
        || die "$label: /usr/share/mkinitfs/hooks/netboot.sh is missing"
    grep -Eq "^NBD_PORT=$PORT$" "$TMP/hook" \
        || die "$label: netboot hook does not set NBD_PORT=$PORT"
    pass "$label: netboot hook sets NBD_PORT=$PORT"

    expected=$(json_get dtb sha256)
    image_dump "$image" "$boot_offset" /sm8150-oneplus-guacamole.dtb "$TMP/dtb" \
        || die "$label: boot partition has no sm8150-oneplus-guacamole.dtb"
    [[ $(sha_of "$TMP/dtb") == "$expected" ]] \
        || die "$label: DTB differs from the manifest dtb ($expected)"
    pass "$label: DTB $expected"

    expected=$(json_get kernel_package sha256)
    image_dump "$image" "$boot_offset" /vmlinuz "$TMP/vmlinuz" \
        || die "$label: boot partition has no vmlinuz"
    [[ $(sha_of "$TMP/vmlinuz") == "$expected" ]] \
        || die "$label: vmlinuz differs from the manifest kernel_package"
    pass "$label: vmlinuz $expected"

    expected=$(json_get initramfs sha256)
    image_dump "$image" "$boot_offset" /initramfs "$TMP/initramfs" \
        || die "$label: boot partition has no initramfs"
    [[ $(sha_of "$TMP/initramfs") == "$expected" ]] \
        || die "$label: initramfs differs from the manifest initramfs"
    pass "$label: initramfs $expected"
}

# Report the CCI module hash found in an image without failing; used to make a
# stale served cache unmistakable in the refresh message.
report_image_module() {
    local label=$1 image=$2 offset
    offset=$(find_partition "$image" root) || {
        warn "$label: cannot parse a root partition in $image"
        return 0
    }
    if image_dump "$image" "$offset" \
        "/usr/lib/modules/$RELEASE/kernel/drivers/i2c/busses/i2c-qcom-cci.ko.zst" "$TMP/report"; then
        local sha size
        sha=$(sha_of "$TMP/report")
        size=$(stat -c %s -- "$TMP/report")
        if [[ $sha == "$EXPECTED_CCI_SHA" && $size == "$EXPECTED_CCI_SIZE" ]]; then
            pass "$label: i2c-qcom-cci $sha ($size bytes), this build's manifest"
        else
            warn "$label: i2c-qcom-cci $sha ($size bytes) is NOT this build's module $EXPECTED_CCI_SHA ($EXPECTED_CCI_SIZE bytes)"
        fi
    else
        warn "$label: no i2c-qcom-cci.ko.zst for $RELEASE in $image"
    fi
}

# ------------------------------------------------------------------- network

usb_net() { bash "$USB_NET" "$@"; }

usb_net_with_iface() {
    local -a arguments=("$1")
    shift
    if [[ -n $IFACE_ARG ]]; then
        arguments+=(--iface "$IFACE_ARG")
    fi
    arguments+=("$@")
    usb_net "${arguments[@]}"
}

detect_iface() {
    local output
    output=$(usb_net_with_iface detect 2>&1) || true
    printf '%s\n' "$output" >&2
    printf '%s\n' "$output" | sed -n 's/^IFACE=//p' | head -n1
}

# ------------------------------------------------------------------- checks

print_firewall_help() {
    local iface=${1:-IFACE}
    info "  sudo ufw allow in on $iface proto tcp from 172.16.42.1 to 172.16.42.2 port $PORT comment 'candidate NBD'"
    info "  sudo ufw allow in on $iface proto tcp from 172.16.42.1 to 172.16.42.2 port 22 comment 'netboot SSH'"
    info "  remove them later with 'sudo ufw status numbered' + 'sudo ufw delete N' (docs/LIVE_BASELINE.md)"
}

check_firewall() {
    local iface=$1 status
    if (( DRY_RUN )); then
        printf 'DRY-RUN: sudo -n ufw status\n'
        info "  if NBD or SSH is blocked, these are the rules to add:"
        print_firewall_help "$iface"
        return 0
    fi
    if status=$(sudo -n ufw status 2>/dev/null); then
        if grep -q '^Status: active' <<<"$status"; then
            if grep -Eq "(^|[ /:])$PORT([ /]|$)" <<<"$status"; then
                pass "ufw is active and mentions port $PORT"
            else
                warn "ufw is active but no rule mentions port $PORT; NBD may be blocked"
                print_firewall_help "$iface"
            fi
        else
            pass "ufw is inactive"
        fi
    else
        warn "cannot read ufw status without a password; if NBD times out, allow the port:"
        print_firewall_help "$iface"
    fi
    if sudo -n iptables -S INPUT 2>/dev/null | grep -Eq '^-A INPUT .*(-j (DROP|REJECT))'; then
        warn "iptables INPUT contains DROP/REJECT rules; inspect with: sudo iptables -S INPUT"
    fi
}

port_listeners() { ss -H -ltnp "( sport = :$1 )" 2>/dev/null || true; }

report_baseline_ports() {
    local port
    for port in 9999 10000; do
        if [[ -n $(port_listeners "$port") ]]; then
            pass "baseline port $port is listening (never touched by this script)"
        else
            info "baseline port $port is not listening"
        fi
    done
}

# Free the candidate port, but only ever for a candidate nbd-server/pmbootstrap.
free_port() {
    local allow_stop=$1 lines pids pid command
    lines=$(port_listeners "$PORT")
    if [[ -z $lines ]]; then
        pass "port $PORT is free"
        return 0
    fi
    warn "port $PORT is already in use:"
    printf '%s\n' "$lines" >&2
    pids=$(grep -o 'pid=[0-9]*' <<<"$lines" | cut -d= -f2 | sort -u || true)
    if [[ -z $pids ]]; then
        warn "cannot identify the owner of port $PORT without root: sudo ss -ltnp '( sport = :$PORT )'"
        [[ $allow_stop == no ]] && action "a serve run would stop a stale candidate server on port $PORT"
        return 1
    fi
    for pid in $pids; do
        command=$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null) || command=
        info "  pid $pid: ${command:-<unreadable>}"
        case $command in
            *nbd-server*|*pmbootstrap*)
                if [[ $allow_stop == no ]]; then
                    action "a serve run would stop this candidate server after confirmation"
                    continue
                fi
                confirm "stop the stale candidate server (pid $pid: $command)?" || return 1
                run kill "$pid"
                ;;
            *)
                warn "port $PORT is held by an unrelated process (pid $pid: ${command:-unknown}); stop it yourself"
                return 1
                ;;
        esac
    done
    if [[ $allow_stop == no ]]; then
        return 0
    fi
    for _ in $(seq 1 20); do
        if [[ -z $(port_listeners "$PORT") ]]; then
            pass "port $PORT released"
            return 0
        fi
        sleep 0.5
    done
    warn "port $PORT is still in use; nbd-server was started as root, try: sudo kill $pids"
    return 1
}

# Refresh policy: never write the cache while port 10001 is in use. A running
# nbd-server reads the cache as its backing file, so a plain cp truncates the
# image under a live phone. Refresh a sibling temporary file, verify it, then
# atomically rename it over the stopped cache.
refuse_live_refresh() {
    local lines
    if ! command -v ss >/dev/null 2>&1; then
        die "ss (iproute2) is required to prove the candidate cache is not being served"
    fi
    lines=$(port_listeners "$PORT")
    if [[ -n $lines ]]; then
        warn "port $PORT is in use; the served cache may be a live NBD backing file:"
        printf '%s\n' "$lines" >&2
        die "refusing to replace $SERVED_IMAGE while the candidate is in use; stop the port-$PORT server and reboot the phone out of the candidate, then rerun"
    fi
    pass "candidate port $PORT is not in use; the cache may be refreshed"
}

served_generation() { stat -c '%d:%i:%s' -- "$1"; }

# Record the exact file identity that was verified before the server starts.
# Post-boot consumers (camera_boot.sh status) use this instead of re-hashing a
# rootfs the phone may already have written through NBD.
write_served_record() {
    local sha=$1 expected=$2 how=$3 generation
    generation=$(served_generation "$SERVED_IMAGE")
    mkdir -p -- "$DELIVERY_DIR"
    python3 - "$SERVED_RECORD" "$SERVED_IMAGE" "$sha" "$expected" "$generation" \
        "$RELEASE" "$PORT" "$how" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

record, image, served_sha, expected_sha, generation, release, port, how = sys.argv[1:9]
path = Path(record)
document = {
    'path': str(Path(image)),
    'served_sha256': served_sha,
    'source_sha256': expected_sha,
    'generation': generation,
    'release': release,
    'nbd_port': int(port),
    'verified': how,
    'verified_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
}
temporary = path.with_name(path.name + '.tmp')
temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
os.replace(temporary, path)
print(f'{path}: {served_sha} ({generation})')
PY
    SERVED_GENERATION=$generation
}

# Copy the verified image into the served cache *before* waiting for the USB
# interface, so the boot script can prove the cache hash before fastboot boot.
# pmbootstrap would otherwise copy only after the phone already appears.
precopy_cache() {
    local expected=$1 sha
    [[ $REFRESH == yes ]] || return 0
    if [[ $REPLACE == no ]]; then
        die "--no-replace was given but the served cache is stale; remove $SERVED_IMAGE or drop --no-replace"
    fi
    refuse_live_refresh
    if (( DRY_RUN )); then
        printf 'DRY-RUN: %s\n' "$(cmd_string sudo cp --reflink=auto -- "$SOURCE_IMAGE" "$SERVED_IMAGE.refresh")"
        printf 'DRY-RUN: verify the temporary copy (%s) and its DTB/module contents, then: %s\n' \
            "$expected" "$(cmd_string sudo mv -f -- "$SERVED_IMAGE.refresh" "$SERVED_IMAGE")"
        REFRESH=no
        PRECOPIED=yes
        SERVED_SHA=$expected
        return 0
    fi
    confirm "refresh the stopped served cache $SERVED_IMAGE from the current rootfs image?" \
        || die "cache refresh declined; nothing was served"
    action "staging the verified rootfs image next to the stopped served cache"
    REFRESH_TMP=$SERVED_IMAGE.refresh.$$
    run mkdir -p -- "$(dirname -- "$SERVED_IMAGE")" \
        || die "failed to create $(dirname -- "$SERVED_IMAGE")"
    run cp --reflink=auto -- "$SOURCE_IMAGE" "$REFRESH_TMP" \
        || die "failed to copy $SOURCE_IMAGE -> $REFRESH_TMP"
    sha=$(sha_of "$REFRESH_TMP")
    [[ $sha == "$expected" ]] \
        || die "staged cache sha256 $sha != verified rootfs image $expected"
    inspect_image "staged served cache (pre-exposure)" "$REFRESH_TMP"
    run mv -f -- "$REFRESH_TMP" "$SERVED_IMAGE" \
        || die "failed to move the verified copy into $SERVED_IMAGE"
    REFRESH_TMP=''
    SERVED_SHA=$sha
    write_served_record "$sha" "$expected" refreshed
    pass "served cache refreshed and verified before exposure ($sha)"
    REFRESH=no
    PRECOPIED=yes
}

# The cache already equals the chroot image. Verify it (and its contents) while
# no server is reading it, then record the pre-exposure identity.
verify_served_before_exposure() {
    local expected=$1 sha mounted=$WORK/chroot_native/mnt/pmbootstrap/netboot/$DEVICE.img
    (( DRY_RUN )) && return 0
    [[ -f $SERVED_IMAGE ]] || die "pmbootstrap did not create $SERVED_IMAGE"
    sha=$(hash_cached "$SERVED_IMAGE")
    [[ $sha == "$expected" ]] \
        || die "served cache $SERVED_IMAGE sha256 $sha differs from the rootfs image; refusing to let the phone boot the wrong rootfs"
    inspect_image "served cache (pre-exposure)" "$SERVED_IMAGE"
    SERVED_SHA=$sha
    write_served_record "$sha" "$expected" already-matching
    if [[ -e $mounted ]]; then
        if [[ $(stat -c '%d:%i' -- "$mounted") == "$(stat -c '%d:%i' -- "$SERVED_IMAGE")" ]]; then
            pass "chroot path $mounted is the same file as the served cache"
        else
            warn "chroot path $mounted is a different file; check the pmbootstrap bind mount"
        fi
    fi
}

# ------------------------------------------------------------ serve lifecycle

start_server() {
    local -a command
    command=(python3 "$PMB" -c "$CFG" -p "$APORTS" -w "$WORK" netboot serve --port "$PORT")
    if [[ $PRECOPIED != yes ]]; then
        if [[ $REFRESH == yes || $REPLACE == yes ]]; then
            command=(python3 "$PMB" -y -c "$CFG" -p "$APORTS" -w "$WORK" netboot serve --port "$PORT" --replace)
        fi
    fi
    if (( DRY_RUN )); then
        printf 'DRY-RUN: %s\n' "$(cmd_string "${command[@]}")"
        return 0
    fi
    mkdir -p -- "$SCRATCH"
    : >"$LOG"
    info "+ $(cmd_string "${command[@]}")"
    info "  log: $LOG"
    "${command[@]}" > >(tee -a "$LOG") 2>&1 &
    SERVER_PID=$!
}

# kill -0 also succeeds for an unreaped zombie child, so read /proc state too.
process_running() {
    local pid=$1 stat rest
    kill -0 "$pid" 2>/dev/null || return 1
    stat=$(cat "/proc/$pid/stat" 2>/dev/null) || return 0
    rest=${stat##*) }
    [[ ${rest%% *} != Z ]]
}

wait_for_log() {
    local pattern=$1 timeout=$2 start=$SECONDS status
    while (( SECONDS - start < timeout )); do
        if grep -Fq -- "$pattern" "$LOG" 2>/dev/null; then
            return 0
        fi
        if [[ -n $SERVER_PID ]] && ! process_running "$SERVER_PID"; then
            wait "$SERVER_PID" && status=0 || status=$?
            tail -n 20 -- "$LOG" >&2 || true
            die "pmbootstrap exited (status $status) before '$pattern'; full log: $LOG"
        fi
        sleep 1
    done
    tail -n 20 -- "$LOG" >&2 || true
    die "timed out after ${timeout}s waiting for '$pattern' in $LOG"
}

# Never re-hash the whole image after the server starts: the phone mounts it
# read-write through NBD, so bytes legitimately change. The pre-exposure record
# identifies the exact file (device:inode:size) that was verified.
verify_served_still_exposed() {
    local generation
    [[ -f $SERVED_IMAGE ]] || die "pmbootstrap did not create $SERVED_IMAGE"
    [[ -n $SERVED_GENERATION ]] || die "internal error: served cache was not verified before exposure"
    generation=$(served_generation "$SERVED_IMAGE") \
        || die "served cache disappeared after the server started: $SERVED_IMAGE"
    [[ $generation == "$SERVED_GENERATION" ]] \
        || die "served cache was replaced after verification (was $SERVED_GENERATION, now $generation); stop the phone/server and rerun"
    pass "server still serves the pre-verified cache file ($generation); not rehashed after exposure"
}

print_ssh_command() {
    note "SSH:            ssh -4 -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=\"$KNOWN_HOSTS\" user@172.16.42.1"
    note "                (delete $KNOWN_HOSTS to forget a changed host key)"
}

print_ready() {
    local iface=$1 driver=$2 verified_note
    if (( DRY_RUN )); then
        verified_note='planned: staged next to the stopped cache, verified, then renamed into place'
    else
        verified_note='verified before exposure; mutable after the phone mounts it'
    fi
    note ''
    note '================ READY: candidate rootfs is served ================'
    note "worktree:       $ROOT"
    note "release:        $RELEASE"
    note "boot.img:       $BOOT_IMG"
    note "                sha256 $(json_get boot_img sha256) ($(json_get boot_img size) bytes)"
    note "rootfs image:   $SOURCE_IMAGE"
    note "                sha256 $SERVED_SHA"
    note "served image:   $SERVED_IMAGE"
    note "                sha256 $SERVED_SHA ($verified_note)"
    note "i2c-qcom-cci:   $EXPECTED_CCI_SHA ($EXPECTED_CCI_SIZE bytes, verified inside the served image)"
    note "                this build's identity; read from DELIVERY.json, not a built-in hash"
    note "interface:      $iface${driver:+ ($driver)}"
    note "host address:   172.16.42.2/24"
    note "NBD port:       $PORT"
    print_ssh_command
    note "next:           bash \"$ROOT/scripts/camera_boot.sh\"   (temporary boot, never flashes)"
    note '=================================================================='
}

# Re-add the host address when the gadget re-enumerates and report NBD clients.
start_watcher() {
    local iface=$1
    (
        set +e
        local present=1 clients=0 previous=0
        while :; do
            if [[ -e ${NURA_SYSFS_ROOT:-/sys}/class/net/$iface ]]; then
                if (( present == 0 )); then
                    note "watcher: $iface is back"
                    present=1
                fi
                if ! ip -o -4 address show dev "$iface" 2>/dev/null | grep -Fq " inet 172.16.42.2/24 "; then
                    if sudo -n ip addr replace 172.16.42.2/24 dev "$iface" >/dev/null 2>&1; then
                        note "watcher: re-added 172.16.42.2/24 on $iface"
                    else
                        note "watcher: 172.16.42.2/24 missing on $iface; run: sudo ip addr replace 172.16.42.2/24 dev $iface"
                    fi
                fi
            elif (( present == 1 )); then
                note "watcher: $iface disappeared (phone rebooting or cable removed)"
                present=0
            fi
            clients=$(ss -H -tn state established "( dport = :$PORT )" 2>/dev/null | wc -l)
            if (( clients > previous )); then
                note "watcher: phone connected to port $PORT"
            elif (( clients < previous )); then
                note "watcher: phone disconnected; pmbootstrap keeps waiting and restarts nbd-server"
            fi
            previous=$clients
            sleep 5
        done
    ) &
    WATCHER_PID=$!
}

stop_watcher() {
    [[ -n $WATCHER_PID ]] || return 0
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
    WATCHER_PID=
}

cleanup() {
    local status=$?
    trap - EXIT INT TERM ERR
    stop_watcher
    if [[ -n $SERVER_PID ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        info "stopping the netboot server (pid $SERVER_PID)"
        kill "$SERVER_PID" 2>/dev/null || true
        sleep 1
        kill -0 "$SERVER_PID" 2>/dev/null && kill -KILL "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ -n $(ss -H -ltn "( sport = :$PORT )" 2>/dev/null || true) ]]; then
        warn "port $PORT is still bound (nbd-server runs as root in chroot_native); free it with:"
        info "  sudo ss -ltnp '( sport = :$PORT )'"
        info "  sudo pkill -f 'nbd-server 172.16.42.2@$PORT'"
    fi
    [[ -n $TMP ]] && rm -rf -- "$TMP"
    [[ -n $REFRESH_TMP ]] && rm -f -- "$REFRESH_TMP"
    exit "$status"
}

# ---------------------------------------------------------------------- main

parse_args() {
    while (( $# )); do
        case $1 in
            -h|--help) usage; exit 0 ;;
            --dry-run) DRY_RUN=1 ;;
            --check) CHECK=1 ;;
            --replace) REPLACE=yes ;;
            --no-replace) REPLACE=no ;;
            --port) [[ $# -ge 2 ]] || die "--port needs a value"; PORT=$2; shift ;;
            --iface) [[ $# -ge 2 ]] || die "--iface needs a value"; IFACE_ARG=$2; shift ;;
            --timeout) [[ $# -ge 2 ]] || die "--timeout needs a value"; TIMEOUT=$2; shift ;;
            --yes|-y) ASSUME_YES=1 ;;
            --no-watch) WATCH=0 ;;
            --rehash) RECHECK=1 ;;
            *) die "unknown argument: $1 (try --help)" ;;
        esac
        shift
    done
    [[ $PORT =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) \
        || die "--port must be an integer between 1024 and 65535"
    case " 9999 10000 " in
        *" $PORT "*) die "refusing to serve on baseline port $PORT; the candidate uses $DEFAULT_PORT, the working setup keeps 10000" ;;
    esac
    [[ $TIMEOUT =~ ^[0-9]+$ ]] && (( TIMEOUT > 0 )) || die "--timeout must be a positive integer"
    if (( CHECK && DRY_RUN )); then
        die "--check and --dry-run are separate modes"
    fi
}

preflight() {
    info "camera_serve: worktree $ROOT"
    case $ROOT in
        *nura-sm8150-7-0*) die "refusing to serve from the baseline checkout $ROOT" ;;
    esac
    [[ -f $USB_NET ]] || die "missing helper $USB_NET"
    [[ -f $PMB ]] || die "missing pmbootstrap: $PMB"
    [[ -f $CFG ]] || die "missing pmbootstrap config: $CFG"
    [[ -d $APORTS ]] || die "missing pmaports: $APORTS"
    [[ -d $WORK ]] || die "missing pmbootstrap work dir: $WORK"
    [[ -f $SOURCE_IMAGE ]] || die "missing chroot rootfs image: $SOURCE_IMAGE"
    [[ -f $MANIFEST ]] || die "missing $MANIFEST; run: bash \"$ROOT/scripts/camera_build.sh\" manifest"
    command -v debugfs >/dev/null 2>&1 || die "debugfs is required (install e2fsprogs)"
    command -v od >/dev/null 2>&1 || die "od is required (coreutils)"
    command -v python3 >/dev/null 2>&1 || die "python3 is required"
    mkdir -p -- "$SCRATCH"
    TMP=$(mktemp -d "${TMPDIR:-/tmp}/camera-serve.XXXXXX")
    case $TMP in
        *[[:space:]]*) die "temporary path '$TMP' contains whitespace and debugfs cannot quote it; set TMPDIR to a path without spaces" ;;
    esac
    info "scratch: $SCRATCH"
    if [[ -f $CFG ]] && grep -Eq "^work = .*" "$CFG"; then
        local declared
        declared=$(sed -n 's/^work = //p' "$CFG" | head -n1)
        if [[ $declared != "$WORK" ]]; then
            warn "pmbootstrap.cfg work= is $declared; this run pins -w $WORK"
        fi
    fi
}

main() {
    parse_args "$@"
    trap cleanup EXIT INT TERM
    preflight

    # 1. Manifest identity (fails closed; prints boot.img/rootfs/module hashes).
    validate_manifest || die "DELIVERY.json validation failed (see the FAIL lines above)"
    local expected_rootfs source_sha
    EXPECTED_CCI_SHA=$(json_get i2c_qcom_cci sha256)
    EXPECTED_CCI_SIZE=$(json_get i2c_qcom_cci size)
    expected_rootfs=$(json_get rootfs_image sha256)
    source_sha=$(hash_cached "$SOURCE_IMAGE")
    [[ $source_sha == "$expected_rootfs" ]] \
        || die "rootfs image $SOURCE_IMAGE sha256 $source_sha != manifest $expected_rootfs; rebuild and rerun camera_build.sh manifest"
    pass "rootfs image matches the manifest ($source_sha)"
    [[ -f $BOOT_IMG ]] || die "missing boot image: $BOOT_IMG"

    # 2. The chroot image itself must be the candidate.
    inspect_image "rootfs image" "$SOURCE_IMAGE"

    # 3. Served cache: it must be the same bytes as the chroot image.
    if [[ -f $SERVED_IMAGE ]]; then
        local served_sha
        served_sha=$(hash_cached "$SERVED_IMAGE")
        if [[ $served_sha == "$source_sha" ]]; then
            pass "served cache already equals the rootfs image ($served_sha)"
            REFRESH=no
        else
            REFRESH=yes
            warn "served cache $SERVED_IMAGE sha256 $served_sha != rootfs image $source_sha"
            report_image_module "served cache (pre-refresh)" "$SERVED_IMAGE"
            action "the cache is refreshed from $SOURCE_IMAGE before READY"
        fi
    else
        REFRESH=yes
        warn "served cache is missing: $SERVED_IMAGE"
    fi
    if [[ $REPLACE == yes ]]; then
        REFRESH=yes
        action "--replace requested: the served cache is copied again"
    fi

    # 3b. Make the pre-boot cache correct. This must happen before the USB
    # interface wait: camera_boot.sh checks this file before fastboot boot, and
    # pmbootstrap's own copy happens only after the phone already appears.
    if (( !CHECK )); then
        precopy_cache "$expected_rootfs"
    fi

    # 4. USB interface.
    local iface='' iface_detected=0
    if (( CHECK )); then
        iface=$(detect_iface)
        if [[ -n $iface ]]; then
            iface_detected=1
        else
            warn "no USB network interface yet (expected before the phone boots)"
        fi
    elif (( DRY_RUN )); then
        iface=$(detect_iface)
        printf 'DRY-RUN: bash %s wait --timeout %s%s\n' "$USB_NET" "$TIMEOUT" "${IFACE_ARG:+ --iface $IFACE_ARG}"
        if [[ -n $iface ]]; then
            usb_net up --dry-run --iface "$iface"
        else
            printf 'DRY-RUN: bash %s up --iface USB-IFACE\n' "$USB_NET"
        fi
    else
        iface=$(usb_net_with_iface wait --timeout "$TIMEOUT" \
            | sed -n 's/^IFACE=//p' | head -n1 || true)
        [[ -n $iface ]] || die "camera_usb_net.sh did not report an interface"
        usb_net up --iface "$iface"
    fi
    if [[ -n $iface ]]; then
        iface_detected=1
    fi

    # 5. Ports and baseline.
    local blocked=0
    if (( DRY_RUN )); then
        printf 'DRY-RUN: ss -H -ltnp "( sport = :%s )"\n' "$PORT"
        printf 'DRY-RUN: ss -H -ltn "( sport = :9999 )"\n'
        printf 'DRY-RUN: ss -H -ltn "( sport = :10000 )"\n'
        check_firewall "${iface:-usb0}"
    else
        report_baseline_ports
        if (( CHECK )); then
            free_port no || blocked=1
        else
            free_port yes || die "port $PORT is not available"
            check_firewall "${iface:-usb0}"
        fi
    fi

    # 6. Refresh decision.
    if [[ $PRECOPIED == yes ]]; then
        pass "served cache pre-copied and verified before the phone boots"
    elif [[ $REFRESH == yes ]]; then
        action "refresh needed: the cache is pre-copied before the USB wait, then pmbootstrap serves it"
    else
        pass "no cache refresh needed"
    fi

    if (( CHECK )); then
        note ''
        if [[ $REFRESH == yes && $REPLACE == no ]]; then
            warn "--no-replace was given but the served cache is stale; remove $SERVED_IMAGE or drop --no-replace"
            exit 1
        fi
        if [[ $REFRESH == yes ]]; then
            warn "REFRESH NEEDED: serving would copy the rootfs image over the stale cache"
        fi
        if [[ $iface_detected == 1 ]]; then
            pass "USB interface $iface"
        else
            warn "USB interface: wait for the phone, then serve"
        fi
        if (( blocked )); then
            note "CHECK FAILED: port $PORT is not available"
            exit 1
        fi
        note "CHECK OK: artifacts, image contents and ports were validated; nothing was started"
        exit 0
    fi

    # 7. Start the server and prove what it serves. A cache that already
    # matched was verified before exposure only now, after free_port stopped
    # any stale candidate and before nbd-server opens it.
    if (( DRY_RUN )); then
        SERVED_SHA=$source_sha
        start_server
        printf 'DRY-RUN: wait for pmbootstrap to print "Running nbd server"\n'
        printf 'DRY-RUN: verify the staged cache before exposure and record dev:inode:size; do not rehash it afterwards\n'
        printf 'DRY-RUN: debugfs (GPT + 4 KiB sectors) to re-verify the manifest i2c-qcom-cci and DTB in the staged cache\n'
        print_ready "${iface:-<auto>}" ""
        exit 0
    fi

    if [[ $PRECOPIED != yes ]]; then
        verify_served_before_exposure "$expected_rootfs"
    fi
    start_server
    wait_for_log 'Running nbd server' 900
    verify_served_still_exposed
    local driver=''
    if [[ -n $iface ]]; then
        driver=$(bash "$USB_NET" status --iface "$iface" 2>/dev/null \
            | sed -n 's/^DRIVER=//p' | head -n1 || true)
    fi
    print_ready "${iface:-unknown}" "$driver"
    if (( WATCH )); then
        start_watcher "$iface"
    fi
    local status=0
    wait "$SERVER_PID" || status=$?
    info "pmbootstrap netboot serve exited (status $status)"
    exit "$status"
}

main "$@"
