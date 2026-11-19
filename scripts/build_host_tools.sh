#!/usr/bin/env bash
# Provide the DTB host tools for scripts/prepare_temporary_dtb.py from public
# sources, for checkouts that do not have the original pinned binaries.
#
#   local/bin/{dtc,fdtget,fdtput,fdtoverlay}  -> the system dtc package (>= 1.7)
#   local/lib/libfdt.so.1                     -> the system libfdt
#   local/tools/ufdt/ufdt_overlay_test_app    <- built from AOSP platform/system/libufdt
#                                                at LIBUFDT_COMMIT against the system libfdt
#
# Only missing files are provided; existing ones (for example the original
# pinned binaries) are left alone. What this script provides is recorded with
# its SHA-256 and source in local/TOOLS.json, which prepare_temporary_dtb.py
# accepts next to its original pins. Rerun it after a dtc package update.
#
#   scripts/build_host_tools.sh [--check]
#
# Network: one shallow git fetch from android.googlesource.com. No sudo.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LIBUFDT_URL="https://android.googlesource.com/platform/system/libufdt"
LIBUFDT_COMMIT="657f54c72d9e045f450c62007a05343a2447356e"
RECORD="$REPO/local/TOOLS.json"
CHECK=0
case "${1:-}" in --check) CHECK=1 ;; "") ;; -h|--help) sed -n '2,17p' "$0"; exit 0 ;; *) echo "unknown argument: $1" >&2; exit 2 ;; esac

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "==> $*"; }

bash "$REPO/scripts/check_prereqs.sh" --stage boot --quiet ||
    die "install the missing boot prerequisites above first"

declare -A PROVIDED=()   # relative path -> source description

link_system() {   # relative-path system-path description
    local rel=$1 target=$2 desc=$3 abs="$REPO/$1"
    if [[ -e "$abs" || -L "$abs" ]]; then
        note "$rel: present, left alone"
        return 0
    fi
    ((CHECK)) && die "$rel is missing (run without --check)"
    [[ -e "$target" ]] || die "system file not found: $target"
    mkdir -p "$(dirname "$abs")"
    ln -s "$target" "$abs"
    PROVIDED[$rel]="$desc ($target)"
    note "$rel -> $target"
}

DTC_VERSION="$(dtc --version | sed -n 's/.*DTC v\{0,1\}\([0-9][0-9.]*\).*/\1/p')"
for tool in dtc fdtget fdtput fdtoverlay; do
    link_system "local/bin/$tool" "$(command -v "$tool")" "system dtc $DTC_VERSION"
done
ldcache="$(ldconfig -p 2>/dev/null || true)"
libfdt="$(awk '/[[:space:]]libfdt\.so\.1 /{print $NF}' <<< "$ldcache" | sed -n 1p)"
[[ -n "$libfdt" ]] || libfdt="$(compgen -G '/usr/lib*/libfdt.so.1' | sed -n 1p || true)"
[[ -n "$libfdt" ]] || die "system libfdt.so.1 not found"
link_system "local/lib/libfdt.so.1" "$(readlink -f "$libfdt")" "system libfdt $DTC_VERSION"

app="$REPO/local/tools/ufdt/ufdt_overlay_test_app"
if [[ -e "$app" ]]; then
    note "local/tools/ufdt/ufdt_overlay_test_app: present, left alone"
else
    ((CHECK)) && die "local/tools/ufdt/ufdt_overlay_test_app is missing (run without --check)"
    src="$REPO/.work/host-tools/libufdt"
    if [[ ! -d "$src/.git" ]]; then
        note "fetching libufdt $LIBUFDT_COMMIT"
        git init -q "$src"
        git -C "$src" fetch -q --depth 1 "$LIBUFDT_URL" "$LIBUFDT_COMMIT"
        git -C "$src" -c advice.detachedHead=false checkout -q --detach FETCH_HEAD
    fi
    [[ "$(git -C "$src" rev-parse HEAD)" = "$LIBUFDT_COMMIT" ]] || die "$src is not at $LIBUFDT_COMMIT"
    note "building ufdt_overlay_test_app"
    mkdir -p "$(dirname "$app")"
    cc -O2 -std=gnu11 -w \
        -I "$src/include" -I "$src/sysdeps/include" -I "$src" \
        "$src"/ufdt_convert.c "$src"/ufdt_node.c "$src"/ufdt_node_pool.c \
        "$src"/ufdt_overlay.c "$src"/ufdt_prop_dict.c \
        "$src"/sysdeps/libufdt_sysdeps_posix.c \
        "$src"/tests/src/ufdt_overlay_test_app.c "$src"/tests/src/util.c \
        -lfdt -o "$app"
    install -m 644 "$src/NOTICE" "$(dirname "$app")/NOTICE"
    PROVIDED[local/tools/ufdt/ufdt_overlay_test_app]="AOSP libufdt $LIBUFDT_COMMIT, built with $(cc --version | head -1)"
fi

((CHECK)) && { note "all DTB host tools present"; exit 0; }
((${#PROVIDED[@]})) || { note "nothing to provide; local/TOOLS.json unchanged"; exit 0; }

# Merge into the record (keep earlier entries for files still present).
args=()
for rel in "${!PROVIDED[@]}"; do args+=("$rel" "${PROVIDED[$rel]}"); done
python3 - "$REPO" "$RECORD" "${args[@]}" <<'PY'
import hashlib, json, sys
from pathlib import Path
repo, record = Path(sys.argv[1]), Path(sys.argv[2])
data = json.loads(record.read_text()) if record.is_file() else {"schema": 1, "files": {}}
pairs = sys.argv[3:]
for rel, source in zip(pairs[::2], pairs[1::2]):
    digest = hashlib.sha256((repo / rel).read_bytes()).hexdigest()
    data["files"][rel] = {"sha256": digest, "source": source}
data["files"] = {k: v for k, v in data["files"].items() if (repo / k).exists()}
record.parent.mkdir(parents=True, exist_ok=True)
record.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
print(f"==> recorded {len(pairs)//2} tool(s) in {record.relative_to(repo)}")
PY
