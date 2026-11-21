#!/usr/bin/env bash
# Free disk space taken by the build work directories (./nura clean).
#
#   scripts/clean_work.sh                  list what can be removed (read-only)
#   scripts/clean_work.sh --apply          remove the "unused" and "kernel-copy" items
#   scripts/clean_work.sh --apply --rootfs also remove the pmbootstrap work dirs (sudo)
#
# Categories:
#   unused       .work/<name> dirs that no setting points into: old kernel builds,
#                probes. The baseline (.work/baseline) is never listed.
#   kernel-copy  the per-UI copy of the kernel state under the flow's
#                .work/local-userdata*/kernel-state. The next build checks it and
#                copies it again when it is missing.
#   rootfs       (--rootfs only) the per-UI pmbootstrap work dir (chroots, package
#                caches). The next build creates a new one and is slower (it
#                downloads and builds packages again). Refused while anything is
#                mounted under it, or while its signing key is not the project key
#                (run ./nura signing-key adopt WORK first, or the next build
#                signs with a key the installed phone does not trust).
#
# Never touched: build outputs (build/, flows/*/build/), the baseline, the
# project signing key, and every path set in build-env.local.sh (./nura env).
# Sizes on btrfs are "exclusive": the space freed by removing that item alone
# (reflinked copies share most of their extents).
set -euo pipefail

REPO="${NURA_SOURCE_ROOT:-$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd -P)}"
FLOW="$REPO/flows/rc4/local-storage"
KEY_DIR="${NURA_SIGNING_KEY_DIR:-$HOME/.config/nura-guacamole/abuild}"
SUDO="${NURA_SUDO-sudo}"

die() { echo "clean: $*" >&2; exit 1; }

apply=0 rootfs=0
for arg; do
    case "$arg" in
        --apply) apply=1 ;;
        --rootfs) rootfs=1 ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) die "unknown option: $arg (expected --apply, --rootfs)" ;;
    esac
done

# Paths that must survive: every configured setting, resolved.
protected=()
for v in NURA_BASELINE_ROOT RC4_KERNEL_ROOT NURA_KERNEL_BUILD NURA_KERNEL_SOURCE_STATE \
         NURA_KERNEL_DIR NURA_SIGNING_KEY_DIR; do
    [[ -n "${!v:-}" ]] && protected+=("$(realpath -m -- "${!v}")")
done
protected+=("$(realpath -m -- "$REPO/.work/baseline")" "$(realpath -m -- "$KEY_DIR")")

# is_protected DIR: DIR is, contains, or lies inside a configured path
is_protected() {
    local dir p
    dir="$(realpath -m -- "$1")"
    for p in "${protected[@]}"; do
        [[ "$dir" = "$p" || "$p" = "$dir/"* || "$dir" = "$p/"* ]] && return 0
    done
    return 1
}

size_of() {   # DIR: exclusive size on btrfs, apparent size otherwise
    local out
    if [[ "$(stat -f -c %T -- "$1" 2>/dev/null)" = btrfs ]] &&
        out="$(btrfs filesystem du -s --raw -- "$1" 2>/dev/null | awk 'NR==2 {print $2}')" &&
        [[ -n "$out" ]]; then
        numfmt --to=iec --suffix=B -- "$out"
    else
        du -sh --one-file-system -- "$1" 2>/dev/null | awk '{print $1 "B"}' || echo "?"
    fi
}

mounts_under() { findmnt -rn -o TARGET | while read -r m; do m="$(printf '%b' "$m")"
    [[ "$m" = "$1" || "$m" = "$1/"* ]] && echo "$m"; done; true; }

pub_key() {   # DIR: the single *.rsa.pub in DIR, or empty
    local -a k=()
    mapfile -t k < <(find "$1" -maxdepth 1 -name '*.rsa.pub' 2>/dev/null | sort)
    ((${#k[@]} == 1)) && echo "${k[0]}"; true
}

# rootfs_block WORK: why WORK must not be removed now (empty when it may)
rootfs_block() {
    local work=$1 m wk pk
    m="$(mounts_under "$work" | wc -l)"
    ((m == 0)) || { echo "$m mounts under it (run ./nura shutdown)"; return; }
    wk="$(pub_key "$work/config_abuild")"
    [[ -n "$wk" ]] || return 0
    pk="$(pub_key "$KEY_DIR")"
    [[ -n "$pk" ]] || { echo "it holds the only copy of $(basename "${wk%.rsa.pub}") (run ./nura signing-key adopt $work)"; return; }
    cmp -s "$wk" "$pk" || echo "its key $(basename "${wk%.rsa.pub}") is not the project key $(basename "${pk%.rsa.pub}")"
}

items=()   # "category|path|note"
shopt -s nullglob
for d in "$REPO"/.work/*/; do
    d="${d%/}"
    is_protected "$d" && continue
    items+=("unused|$d|")
done
for state in "$FLOW"/.work/local-userdata*/; do
    state="${state%/}"
    [[ -d "$state/kernel-state" ]] && ! is_protected "$state/kernel-state" &&
        items+=("kernel-copy|$state/kernel-state|")
    work="$state/rootfs-state/pmbootstrap-work"
    if [[ -d "$work" ]] && ! is_protected "$work"; then
        items+=("rootfs|$work|$(rootfs_block "$work")")
    fi
done

((${#items[@]})) || { echo "nothing to clean"; exit 0; }

printf '%-12s %8s  %s\n' CATEGORY SIZE PATH
for it in "${items[@]}"; do
    IFS='|' read -r cat path note <<< "$it"
    flag=""
    [[ "$cat" = rootfs && $rootfs -eq 0 ]] && flag="  (only with --rootfs)"
    [[ -n "$note" ]] && flag="  KEPT: $note"
    printf '%-12s %8s  %s%s\n' "$cat" "$(size_of "$path")" "${path#"$REPO"/}" "$flag"
done
if ((!apply)); then
    echo
    echo "read-only; remove with: ./nura clean --apply   (add --rootfs for the pmbootstrap work dirs)"
    exit 0
fi

# Hold each flow state's build lock while removing from it.
lock_state() {
    local lock="$1/.build.lock"
    [[ -e "$lock" ]] || return 0
    exec 8>"$lock"
    flock -n 8 || die "a build is running in ${1#"$REPO"/}; nothing removed there"
}

echo
for it in "${items[@]}"; do
    IFS='|' read -r cat path note <<< "$it"
    case "$cat" in
        unused|kernel-copy)
            [[ "$cat" = kernel-copy ]] && lock_state "$(dirname "$path")"
            [[ -z "$(mounts_under "$path")" ]] || { echo "skip    ${path#"$REPO"/}: mounted"; continue; }
            if rm -rf --one-file-system -- "$path" 2>/dev/null; then
                echo "removed ${path#"$REPO"/}"
            else
                echo "partly  ${path#"$REPO"/}: files not owned by you remain (re-run with --rootfs, or remove with sudo)"
            fi
            ;;
        rootfs)
            ((rootfs)) || continue
            [[ -z "$note" ]] || { echo "kept    ${path#"$REPO"/}: $note"; continue; }
            lock_state "$(dirname "$(dirname "$path")")"
            note="$(rootfs_block "$path")"   # re-check under the lock
            [[ -z "$note" ]] || { echo "kept    ${path#"$REPO"/}: $note"; continue; }
            $SUDO rm -rf --one-file-system -- "$path"
            echo "removed ${path#"$REPO"/}"
            ;;
    esac
done
