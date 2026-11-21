#!/usr/bin/env bash
# Fetch and verify the ignored host inputs listed in docs/BUILD_ENVIRONMENT.md.
#
#   scripts/fetch_inputs.sh                      fetch what is missing, verify everything
#   scripts/fetch_inputs.sh --check              verify only; download nothing
#   scripts/fetch_inputs.sh --lineage-ota ZIP    also extract the stock dtbo.img/boot.img
#                                                references from any Lineage 24 guacamole OTA
#
# Host prerequisites are checked first (scripts/check_prereqs.sh) and missing
# packages are reported with the install command for this distro.
#
# Every download is checked against a pinned SHA-256; the pins are the same
# ones the build scripts enforce, so a fetched file is accepted by them.
#   platform-tools  dl.google.com platform-tools_r37.0.1-linux.zip
#   mkbootimg       android.googlesource.com platform/system/tools/mkbootimg @64dc2d4
#   avbtool.py      android.googlesource.com platform/external/avb @0cf9e8f
#   bc              the system bc if installed, else flows/rc4/scripts/toolshim/bc
#   kernel source   local/sources/linux-*.tar.gz (APKBUILD source URL, SHA-512 pin)
#   apk.static      local/sources/apk-tools-static-3.0.9-r0.apk (Alpine v3.23; apk-tools#11231)
#   DTB tools       scripts/build_host_tools.sh (system dtc + libufdt test app)
#   dtbo.img        from --lineage-ota, accepted only if its entry 0 is the pinned one
# No sudo, no phone access. Existing files are never overwritten: a file with
# the wrong hash is reported, not replaced.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CHECK=0
OTA=""
while (($#)); do
    case "$1" in
        --check) CHECK=1 ;;
        --lineage-ota) OTA="$(realpath -e -- "${2:?--lineage-ota needs a zip}")"; shift ;;
        -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

echo "== prerequisites"
bash "$REPO/scripts/check_prereqs.sh" ||
    echo "  (continuing; steps that need the missing packages will fail later)" >&2

PT_URL="https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip"
PT_ZIP_SHA="d230f13842f60f782a8645f9c813f8f845bf36089ea7289f28c48f17979313f1"
FASTBOOT_SHA="a686e2c7e8dc9cf4cba0cb8a2eef05f7b2bd682c925abd032fe203215d80b618"
GITILES="https://android.googlesource.com"
MKBOOTIMG_COMMIT="64dc2d4f9da59a4c3e01e7178640a2f435a93c13"
AVB_COMMIT="0cf9e8f7384fbcfb0fd5890d1cc03738bf985d75"
# path in repo | upstream project | path upstream | commit | sha256
AOSP_FILES=(
    "local/tools/mkbootimg/mkbootimg.py|platform/system/tools/mkbootimg|mkbootimg.py|$MKBOOTIMG_COMMIT|37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c"
    "local/tools/mkbootimg/unpack_bootimg.py|platform/system/tools/mkbootimg|unpack_bootimg.py|$MKBOOTIMG_COMMIT|06b54dd9a07c5281778e29e234e76f6e3faee8bf0c904a5ef88fdee30eeed12e"
    "local/tools/mkbootimg/gki/generate_gki_certificate.py|platform/system/tools/mkbootimg|gki/generate_gki_certificate.py|$MKBOOTIMG_COMMIT|1bb1feec68a13da18d581aa2c631798f86f6bc10b55d587b2dd31446a0f8a203"
    "local/tools/avbtool.py|platform/external/avb|avbtool.py|$AVB_COMMIT|e5a664a38db623da00f080219bc0ee60a640a9dc4a872803616fae4938ac749b"
)

FAILED=0
ok() { printf '  ok      %s\n' "$*"; }
bad() { printf '  FAIL    %s\n' "$*" >&2; FAILED=1; }
got() { printf '  fetched %s\n' "$*"; }
sha() { sha256sum "$1" | cut -d' ' -f1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# verify_or_fetch PATH SHA256 FETCH_FUNCTION...
verify_or_fetch() {
    local path=$1 want=$2; shift 2
    local abs="$REPO/$path"
    if [[ -e "$abs" ]]; then
        [[ "$(sha "$abs")" = "$want" ]] && ok "$path" || bad "$path: hash differs from the pin (left untouched)"
        return 0
    fi
    if ((CHECK)); then bad "$path: missing"; return 0; fi
    "$@" "$TMP/fetched" || { bad "$path: download failed"; return 0; }
    [[ "$(sha "$TMP/fetched")" = "$want" ]] || { bad "$path: downloaded file does not match the pin"; return 0; }
    mkdir -p "$(dirname "$abs")"
    install -m "$( [[ $path == *.py || $path == */bin/* || $path == *fastboot ]] && echo 755 || echo 644)" \
        "$TMP/fetched" "$abs"
    got "$path"
}

gitiles_file() {   # project path commit -> out
    local project=$1 file=$2 commit=$3 out=$4
    curl -sfL "$GITILES/$project/+/$commit/$file?format=TEXT" | base64 -d > "$out"
}

echo "== platform-tools"
if [[ -e "$REPO/platform-tools/fastboot" ]]; then
    [[ "$(sha "$REPO/platform-tools/fastboot")" = "$FASTBOOT_SHA" ]] &&
        ok "platform-tools/fastboot (r37.0.1)" || bad "platform-tools/fastboot: hash differs (left untouched)"
elif ((CHECK)); then
    bad "platform-tools/fastboot: missing"
else
    curl -sfLo "$TMP/pt.zip" "$PT_URL" || bad "platform-tools download failed"
    if [[ -f "$TMP/pt.zip" && "$(sha "$TMP/pt.zip")" = "$PT_ZIP_SHA" ]]; then
        python3 -I -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])' \
            "$TMP/pt.zip" "$TMP/pt"
        chmod 755 "$TMP/pt/platform-tools/fastboot" "$TMP/pt/platform-tools/adb"
        if [[ "$(sha "$TMP/pt/platform-tools/fastboot")" = "$FASTBOOT_SHA" ]]; then
            mkdir -p "$REPO/platform-tools"
            cp -a "$TMP/pt/platform-tools/." "$REPO/platform-tools/"
            got "platform-tools r37.0.1"
        else
            bad "fastboot inside the zip does not match the pin"
        fi
    else
        bad "platform-tools zip does not match the pin"
    fi
fi

echo "== mkbootimg / avbtool (AOSP)"
for entry in "${AOSP_FILES[@]}"; do
    IFS='|' read -r path project upstream commit want <<< "$entry"
    verify_or_fetch "$path" "$want" gitiles_file "$project" "$upstream" "$commit"
done

echo "== bc"
if [[ -e "$REPO/local/bin/bc" ]]; then
    [[ -x "$REPO/local/bin/bc" ]] && ok "local/bin/bc" || bad "local/bin/bc is not executable"
elif ((CHECK)); then
    bad "local/bin/bc: missing"
elif command -v bc >/dev/null; then
    mkdir -p "$REPO/local/bin"; ln -s "$(command -v bc)" "$REPO/local/bin/bc"; got "local/bin/bc -> $(command -v bc)"
else
    mkdir -p "$REPO/local/bin"
    install -m 755 "$REPO/flows/rc4/scripts/toolshim/bc" "$REPO/local/bin/bc"
    got "local/bin/bc (toolshim: kernel timeconst.bc only; install bc for the real one)"
fi

echo "== kernel source archive"
# Pinned by SHA-512 in the rc4 kernel APKBUILD; the kernel builder looks for it
# in local/sources/ when its state directory has no copy.
KRECIPE="$REPO/flows/rc4/device/testing/linux-postmarketos-qcom-sm8150"
apkvar() { sh -c '. "$1"; eval "printf \"%s\\n\" \"\$$2\""' sh "$KRECIPE/APKBUILD" "$1"; }
read -r ksha kname < <(apkvar sha512sums | awk '$2 ~ /^linux-.*\.tar\./ {print; exit}')
kurl=$(apkvar source | awk -v n="$kname" 'index($1, n "::") == 1 {sub(/^[^:]*::/, "", $1); print $1; exit}')
kdest="local/sources/$kname"
sha512() { sha512sum "$1" | cut -d' ' -f1; }
if [[ -e "$REPO/$kdest" ]]; then
    [[ "$(sha512 "$REPO/$kdest")" = "$ksha" ]] && ok "$kdest" || bad "$kdest: hash differs from the APKBUILD pin (left untouched)"
elif ((CHECK)); then
    bad "$kdest: missing"
else
    # Reuse a verified copy from an earlier kernel build before downloading ~250 MB.
    src=""
    for c in "${NURA_KERNEL_SOURCE_STATE:-}/$kname" "$REPO"/.work/*/kernel-state/"$kname" \
             "$REPO/flows/rc4/build-source/kernel-state/$kname"; do
        [[ -f "$c" && "$(sha512 "$c")" = "$ksha" ]] && { src="$c"; break; }
    done
    mkdir -p "$REPO/local/sources"
    if [[ -n "$src" ]]; then
        cp --reflink=auto -- "$src" "$REPO/$kdest"; got "$kdest (copied from ${src#"$REPO"/})"
    elif curl -fL --progress-bar -o "$TMP/kernel.tar" "$kurl" && [[ "$(sha512 "$TMP/kernel.tar")" = "$ksha" ]]; then
        install -m 644 "$TMP/kernel.tar" "$REPO/$kdest"; got "$kdest"
    else
        bad "$kdest: download from $kurl failed or does not match the pin"
    fi
fi

echo "== apk-tools-static (pinned for pmbootstrap)"
# apk-tools 3.0.10 fails to commit systemd files on fresh installs
# (alpine/apk-tools#11231); the rootfs flow makes pmbootstrap use this 3.0.9
# package. Same pin as APK_STATIC_* in rootfs_prepare_local.sh.
APK_STATIC_FILE="apk-tools-static-3.0.9-r0.apk"
apk_static_get() { curl -sfL -o "$1" "https://dl-cdn.alpinelinux.org/alpine/v3.23/main/x86_64/$APK_STATIC_FILE"; }
verify_or_fetch "local/sources/$APK_STATIC_FILE" \
    ea77b2533566e70c9566c03748199b79348010c3199ba02d97e0634cf0fa9ef7 apk_static_get

echo "== DTB host tools"
if ((CHECK)); then
    bash "$REPO/scripts/build_host_tools.sh" --check | sed 's/^/  /' || bad "DTB host tools incomplete"
else
    bash "$REPO/scripts/build_host_tools.sh" | sed 's/^/  /' || bad "DTB host tools could not be provided"
fi

echo "== Lineage stock references (dtbo.img, optional boot.img)"
ref_dir=""
for d in lineage-24.0-20261102 lineage-24.0-20261011 from-ota; do
    [[ -f "$REPO/ref/lineageos/$d/dtbo.img" ]] && { ref_dir="ref/lineageos/$d"; break; }
done
if [[ -n "$OTA" && -z "$ref_dir" && $CHECK -eq 0 ]]; then
    out="$REPO/ref/lineageos/from-ota"
    mkdir -p "$out"
    python3 -I - "$REPO/scripts" "$OTA" "$TMP" <<'PY' || bad "could not extract dtbo/boot from $OTA"
import sys
sys.path.insert(0, sys.argv[1])
from extract_lineage_firmware import extract_partition
for name in ("dtbo", "boot"):
    extract_partition(sys.argv[2], name, f"{sys.argv[3]}/{name}.img")
PY
    if [[ -f "$TMP/dtbo.img" ]] && python3 - "$REPO" "$TMP" <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("ptd", Path(sys.argv[1]) / "scripts/prepare_temporary_dtb.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.configure_tools()
m.select_stock_entry(Path(sys.argv[2]) / "dtbo.img", Path(sys.argv[2]))
PY
    then
        install -m 644 "$TMP/dtbo.img" "$TMP/boot.img" "$out/"
        printf '%s
' "$(basename "$OTA")" > "$out/SOURCE"
        got "ref/lineageos/from-ota/{dtbo,boot}.img from $(basename "$OTA") (entry 0 matches the pin)"
        ref_dir="ref/lineageos/from-ota"
    else
        bad "dtbo.img in $(basename "$OTA") does not carry the pinned entry 0; not installed"
    fi
fi
if [[ -n "$ref_dir" ]]; then
    ok "$ref_dir/dtbo.img"
else
    bad "no stock dtbo.img: rerun with --lineage-ota <lineage-24.0-*-guacamole-signed.zip>"
fi

echo
if ((FAILED)); then
    echo "some inputs are missing or do not match their pins (see FAIL above)" >&2
    exit 1
fi
echo "all inputs above are present and verified"
