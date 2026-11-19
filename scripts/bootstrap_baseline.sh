#!/usr/bin/env bash
# Recreate the pinned pmaports/pmbootstrap baseline state from public sources.
#
# The rc4 local-storage flow reads a "baseline" through NURA_BASELINE_ROOT:
#   $NURA_BASELINE_ROOT/.work/rear-camera/{pmaports,pmbootstrap,rootfs/pmbootstrap.cfg,
#                                          pmbootstrap-work/config_apk_keys}
# This script builds that state from the commits pinned in
# device/testing/rear-camera/SOURCE_PINS.json, without the author's machine.
#
# It does NOT recreate $NURA_BASELINE_ROOT/build/rear-camera (the historical
# rear1 APKs and records). See notes/docs for the fresh-baseline mode.
#
#   scripts/bootstrap_baseline.sh [--check] [OUTPUT_DIR]     (default .work/baseline)
#
# Network: git fetches from gitlab.postmarketos.org only. No sudo, no chroot.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
PINS="$REPO/device/testing/rear-camera/SOURCE_PINS.json"
PATCH="$REPO/scripts/patches/pmbootstrap-netboot-port.patch"
PMAPORTS_URL="https://gitlab.postmarketos.org/postmarketOS/pmaports.git"
PMBOOTSTRAP_URL="https://gitlab.postmarketos.org/postmarketOS/pmbootstrap.git"

CHECK=0
OUT=""
for arg in "$@"; do
    case "$arg" in
        --check) CHECK=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) [[ -z "$OUT" ]] || { echo "only one OUTPUT_DIR" >&2; exit 2; }; OUT="$arg" ;;
    esac
done
OUT="${OUT:-$REPO/.work/baseline}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd -P)"
STATE="$OUT/.work/rear-camera"

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "==> $*"; }

[[ -f "$PINS" ]] || die "missing $PINS"
[[ -f "$PATCH" ]] || die "missing $PATCH"
pin() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$PINS" "$1"; }
PMAPORTS_COMMIT="$(pin pmaports_commit)"
PMBOOTSTRAP_COMMIT="$(pin pmbootstrap_commit)"
[[ "$PMAPORTS_COMMIT" =~ ^[0-9a-f]{40}$ && "$PMBOOTSTRAP_COMMIT" =~ ^[0-9a-f]{40}$ ]] ||
    die "SOURCE_PINS.json does not hold full commit hashes"

# fetch_pinned URL DIR COMMIT: a detached checkout of exactly COMMIT.
fetch_pinned() {
    local url=$1 dir=$2 commit=$3 head
    if [[ -e "$dir" ]]; then
        [[ -d "$dir/.git" && ! -L "$dir" ]] || die "$dir exists but is not a git checkout"
        head="$(git -C "$dir" rev-parse HEAD)"
        [[ "$head" = "$commit" ]] || die "$dir is at $head, pin is $commit; move it aside"
        note "$(basename "$dir"): already at $commit"
        return 0
    fi
    ((CHECK)) && die "$dir is missing (run without --check to fetch it)"
    note "fetching $(basename "$dir") $commit"
    git init -q "$dir"
    git -C "$dir" remote add origin "$url"
    git -C "$dir" fetch -q --depth 1 origin "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    [[ "$(git -C "$dir" rev-parse HEAD)" = "$commit" ]] || die "fetched $dir is not $commit"
}

mkdir -p "$STATE"
fetch_pinned "$PMAPORTS_URL" "$STATE/pmaports" "$PMAPORTS_COMMIT"
fetch_pinned "$PMBOOTSTRAP_URL" "$STATE/pmbootstrap" "$PMBOOTSTRAP_COMMIT"

# The flow's candidate prep symlinks its own recipes into its pmaports copy
# and refuses to replace a real directory. These three exist upstream at the
# pin, so the baseline drops them, as the original baseline did.
OVERLAID_UPSTREAM=(
    device/testing/device-oneplus-guacamole
    device/testing/linux-postmarketos-qcom-sm8150
    main/postmarketos-mkinitfs-hook-netboot
)
for d in "${OVERLAID_UPSTREAM[@]}"; do
    if [[ -e "$STATE/pmaports/$d" ]]; then
        ((CHECK)) && die "$STATE/pmaports/$d should be removed (flow overlay)"
        rm -rf -- "${STATE:?}/pmaports/$d"
        note "pmaports: removed upstream $d (the flow overlays its own)"
    fi
done
# Otherwise pristine: only those deletions are allowed.
unexpected="$(git -C "$STATE/pmaports" status --porcelain | while read -r st path; do
    ok=0
    for d in "${OVERLAID_UPSTREAM[@]}"; do [[ "$st" = D && "$path" = "$d/"* ]] && ok=1; done
    ((ok)) || echo "$st $path"
done)"
[[ -z "$unexpected" ]] || die "$STATE/pmaports has unexpected changes:"$'\n'"$unexpected"
grep -q '^version=7$' "$STATE/pmaports/pmaports.cfg" || die "pinned pmaports is not version 7"
[[ -f "$STATE/pmaports/temp/libcamera/APKBUILD" ]] || die "pinned pmaports has no temp/libcamera"

# pmbootstrap: the pin plus exactly the netboot-port patch.
pmb="$STATE/pmbootstrap"
if git -C "$pmb" apply --reverse --check "$PATCH" 2>/dev/null; then
    note "pmbootstrap: netboot-port patch already applied"
else
    ((CHECK)) && die "pmbootstrap lacks the netboot-port patch"
    [[ -z "$(git -C "$pmb" status --porcelain)" ]] || die "$pmb has unexpected local changes"
    git -C "$pmb" apply --check "$PATCH" || die "netboot-port patch does not apply to $PMBOOTSTRAP_COMMIT"
    git -C "$pmb" apply "$PATCH"
    note "pmbootstrap: applied netboot-port patch"
fi
expected_files="$(sed -n 's|^diff --git a/\(\S*\) b/.*|\1|p' "$PATCH" | sort)"
[[ "$(git -C "$pmb" diff --name-only | sort)" = "$expected_files" ]] ||
    die "$pmb differs from pin + netboot-port patch"
grep -q '^work_version = 8$' "$pmb/pmb/config/__init__.py" || die "pinned pmbootstrap is not work_version 8"

# Minimal config: device and UI only; the flow writes its own candidate config.
cfg="$STATE/rootfs/pmbootstrap.cfg"
if [[ ! -f "$cfg" ]]; then
    ((CHECK)) && die "missing $cfg"
    mkdir -p "$STATE/rootfs"
    (umask 077; cat > "$cfg" <<EOF
[pmbootstrap]
aports = $STATE/pmaports
device = oneplus-guacamole
service_manager = systemd
ui = phosh
user = user
work = $STATE/pmbootstrap-work

[providers]

[mirrors]
EOF
    )
    note "wrote $cfg"
fi

# Public APK verification keys: the ones pmbootstrap ships (Alpine + bpo).
keys="$STATE/pmbootstrap-work/config_apk_keys"
mkdir -p "$keys"
for key in "$pmb"/pmb/data/keys/*.pub; do
    dest="$keys/$(basename "$key")"
    if [[ -e "$dest" ]]; then
        cmp -s "$key" "$dest" || die "$dest differs from pmbootstrap's copy"
    else
        ((CHECK)) && die "missing $dest"
        install -m 644 "$key" "$dest"
    fi
done
note "APK keys: $(find "$keys" -name '*.pub' | wc -l) public keys"

cat <<EOF

Baseline state ready at $STATE
  pmaports     $PMAPORTS_COMMIT
  pmbootstrap  $PMBOOTSTRAP_COMMIT + netboot-port patch

Use it with:  export NURA_BASELINE_ROOT=$OUT
Not provided: $OUT/build/rear-camera (historical rear1 APKs and records).
EOF
