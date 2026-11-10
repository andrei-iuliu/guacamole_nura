#!/usr/bin/env bash
# USER RUN ONLY: pmbootstrap invokes sudo/mount/chroots. Never run by an agent.
set -Eeuo pipefail
(( EUID != 0 )) || { echo 'Run as your normal user; pmbootstrap handles privilege.' >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
STATE="$ROOT/.work/rear-camera"
WORK="$STATE/pmbootstrap-work"
NBD_PORT=10001
STAMP=$(date +%Y%m%d-%H%M%S)
PARTIAL_BACKUP=""
mkdir -p "$STATE/rootfs"
exec > >(tee -a "$STATE/rootfs/package-$(date +%Y%m%d-%H%M%S).log") 2>&1
trap 'status=$?; echo "Candidate packaging failed at line $LINENO (exit $status)" >&2; exit "$status"' ERR

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

check_link() {
    local relative=$1 expected=$2 actual
    actual=$(readlink -f "$STATE/pmaports/$relative" 2>/dev/null || true)
    [[ $actual == "$expected" ]] || fail \
        "pmaports link $relative resolves to '${actual:-<missing>}', expected $expected"
}

mounted_below() {
    local root="${1%/}" target
    command -v findmnt >/dev/null 2>&1 || {
        echo "ERROR: findmnt is required to verify that $root has no active mounts." >&2
        return 2
    }
    while IFS= read -r target; do
        [[ -n $target ]] || continue
        case "${target%/}/" in
            "$root"/*) return 0 ;;
        esac
    done < <(findmnt -rn -o TARGET 2>/dev/null || true)
    return 1
}

port_busy() {
    local output hex
    if command -v ss >/dev/null 2>&1; then
        if output=$(ss -H -ltn "sport = :$NBD_PORT" 2>/dev/null); then
            [[ -n $output ]]
            return
        fi
    fi
    printf -v hex '%04X' "$NBD_PORT"
    awk -v port=":$hex" '$2 ~ port "$" && $4 == "0A" { found=1 } END { exit !found }' \
        /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

refuse_live_candidate() {
    if port_busy; then
        printf 'ERROR: TCP port %s is already listening; stop the candidate server first.\n' \
            "$NBD_PORT" >&2
        command -v ss >/dev/null 2>&1 && ss -H -ltn "sport = :$NBD_PORT" >&2 || true
        exit 1
    fi
}

check_pmaports_remote() {
    local remote url
    while IFS=$'\t' read -r remote url; do
        case "${url,,}" in
            *https://gitlab.postmarketos.org/postmarketos/pmaports.git* | \
            *git@gitlab.postmarketos.org:postmarketos/pmaports.git* | \
            *https://gitlab.com/postmarketos/pmaports.git* | \
            *git@gitlab.com:postmarketos/pmaports.git*)
                return 0
                ;;
        esac
    done < <(git -C "$STATE/pmaports" remote -v 2>/dev/null || true)
    fail "pmaports has no official upstream remote; pmbootstrap cannot resolve origin/main. Fix: git -C '$STATE/pmaports' remote set-url origin https://gitlab.postmarketos.org/postmarketOS/pmaports.git"
}

preserve_previous_candidate() {
    local name rollback
    local -a files=()
    for name in boot.img DELIVERY.json packaging-inputs.json ARTIFACTS.json \
                SHA256SUMS APK-SHA256SUMS; do
        [[ -f "$ROOT/build/rear-camera/$name" ]] && files+=("$name")
    done
    (( ${#files[@]} )) || return 0
    rollback="$ROOT/build/rear-camera/rollback-$STAMP"
    [[ ! -e $rollback ]] || fail "rollback path already exists: $rollback"
    mkdir -p "$rollback"
    for name in "${files[@]}"; do
        cp -a --reflink=auto "$ROOT/build/rear-camera/$name" "$rollback/$name"
    done
    (cd "$rollback" && sha256sum "${files[@]}" > ROLLBACK-SHA256SUMS)
    printf 'Previous candidate boot/manifests preserved in %s\n' "$rollback"
}

prepare_work() {
    local status=0 version backup
    if [[ -d $WORK ]]; then
        mounted_below "$WORK" || status=$?
        if (( status == 2 )); then
            exit 1
        elif (( status == 0 )); then
            printf 'ERROR: refusing to use or move %s while mounts are active:\n' "$WORK" >&2
            findmnt -rn -o TARGET --submounts "$WORK" >&2 || true
            printf 'Stop pmbootstrap or unmount them, then retry. The old tree is not touched.\n' >&2
            exit 1
        fi
    fi
    if [[ -f "$WORK/version" ]]; then
        version=$(cat "$WORK/version")
        [[ $version == 8 ]] || fail \
            "pmbootstrap work version '$version' in $WORK is not supported; refusing to migrate or delete it"
    elif [[ -d $WORK && -n "$(ls -A "$WORK")" ]]; then
        backup="$WORK.partial-$STAMP"
        [[ ! -e $backup ]] || fail "partial-work backup path already exists: $backup"
        mv -- "$WORK" "$backup"
        PARTIAL_BACKUP=$backup
        printf 'Preserved incomplete pmbootstrap work directory at %s\n' "$backup"
        printf 'The old tree was not deleted, migrated, chowned or entered.\n'
    fi
    mkdir -p -m 700 "$WORK/cache_git"
    [[ -f "$WORK/version" ]] || printf '8\n' > "$WORK/version"
}

prepare_export() {
    local export="$STATE/rootfs/export" archive link target
    local -a links=()
    [[ -d $export ]] || return 0
    archive="$STATE/rootfs/export.before-$STAMP"
    [[ ! -e $archive ]] || fail "export backup path already exists: $archive"
    mv -- "$export" "$archive"
    if [[ -n $PARTIAL_BACKUP ]]; then
        mapfile -t links < <(find "$archive" -type l)
        for link in "${links[@]}"; do
            target=$(readlink "$link")
            case "$target" in
                ../../pmbootstrap-work/*)
                    ln -sfn -- "../../$(basename "$PARTIAL_BACKUP")/${target#../../pmbootstrap-work/}" "$link"
                    ;;
            esac
        done
    fi
    (cd "$archive" && find . -type l -printf '%p -> %l\n' > EXPORT-TARGETS.txt)
    printf 'Previous export preserved in %s\n' "$archive"
}

# This is the same work-folder version pmbootstrap 3.12.1 writes in init.py.
grep -q '^work_version = 8$' "$STATE/pmbootstrap/pmb/config/__init__.py" 2>/dev/null || \
    fail "pmbootstrap clone is missing the expected work_version = 8 source"
[[ -f "$STATE/pmbootstrap/pmbootstrap.py" ]] || fail "pmbootstrap clone is incomplete: $STATE/pmbootstrap"
[[ -f "$STATE/rootfs/pmbootstrap.cfg" ]] || fail "missing explicit pmbootstrap config: $STATE/rootfs/pmbootstrap.cfg"
grep -q '^device = oneplus-guacamole$' "$STATE/rootfs/pmbootstrap.cfg" || \
    fail "pmbootstrap config does not select oneplus-guacamole"
grep -q '^version=7$' "$STATE/pmaports/pmaports.cfg" 2>/dev/null || \
    fail "pmaports clone is not the expected version 7 recipe tree"

# These paths must all stay in this checkout, apart from read-only Git metadata.
check_link device/testing/device-oneplus-guacamole "$ROOT/device/testing/device-oneplus-guacamole"
check_link device/testing/firmware-oneplus-guacamole "$ROOT/device/testing/firmware-oneplus-guacamole"
check_link device/testing/linux-postmarketos-qcom-sm8150 "$ROOT/device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
check_link main/hexagonrpcd "$ROOT/device/testing/hexagonrpcd"
check_link main/iio-sensor-proxy "$ROOT/device/testing/iio-sensor-proxy"
check_link main/postmarketos-mkinitfs-hook-netboot "$ROOT/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
check_link temp/libcamera-rear "$ROOT/device/testing/rear-camera/libcamera-rear"
check_pmaports_remote

for archive in \
    device/testing/firmware-oneplus-guacamole/firmware-oneplus-guacamole-24.0-20261011.tar.gz \
    device/testing/device-oneplus-guacamole/guacamole-sensors-20261102.tar.gz; do
    [[ -f "$ROOT/$archive" ]] || fail "missing verified local package archive: $archive"
done
[[ -f "$STATE/output/include/config/kernel.release" ]] || \
    fail "missing kernel build output: $STATE/output/include/config/kernel.release"
test "$(cat "$STATE/output/include/config/kernel.release")" = '7.0.0-sm8150-rear1' || \
    fail "kernel build output is not 7.0.0-sm8150-rear1"
[[ -f "$STATE/linux-v7.0.0-sm8150/Makefile" ]] || \
    fail "missing envkernel source tree: $STATE/linux-v7.0.0-sm8150"
[[ -f "$ROOT/build/rear-camera/kernel-package/boot/vmlinuz" ]] || \
    fail "missing staged kernel package: build/rear-camera/kernel-package/boot/vmlinuz"
grep -q '^NBD_PORT=10001$' "$ROOT/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot/netboot.sh" || \
    fail "netboot hook is not port 10001"

refuse_live_candidate
prepare_work
preserve_previous_candidate
prepare_export

# Refresh the local envkernel copy, never baseline output or a symlink outside /mnt/linux.
python3 - "$STATE" <<'PY'
from pathlib import Path
import os, shutil, subprocess, sys
state=Path(sys.argv[1]); target=state/'linux-v7.0.0-sm8150/.output'
if target.is_symlink():
    raise SystemExit('envkernel .output must be a real worktree-local directory')
subprocess.run(['cp','-a','--reflink=auto',str(state/'output')+'/.',str(target)],check=True)
(target/'Makefile').write_text('include ../Makefile\n')
link=target/'source'
if link.is_symlink():link.unlink()
link.symlink_to('..')
# envkernel creates a timestamped APK on every retry; retain older builds off-index.
packages=state/'pmbootstrap-work/packages/edge/aarch64'
for apk in packages.glob('linux-postmarketos-qcom-sm8150-[0-9]*.apk'):
    archive=state/'rootfs/previous-kernels'
    archive.mkdir(exist_ok=True)
    destination=archive/apk.name
    if destination.exists():
        raise SystemExit(f'Refusing to overwrite archived APK: {destination}')
    if os.access(packages, os.W_OK):
        apk.rename(destination)
    else:
        print(f'Archiving previous candidate APK (sudo required): {apk}', flush=True)
        subprocess.run(['sudo', 'mv', '--', str(apk), str(destination)], check=True)
PY
PMB=(python3 "$STATE/pmbootstrap/pmbootstrap.py" -c "$STATE/rootfs/pmbootstrap.cfg" -p "$STATE/pmaports" -w "$WORK")
(cd "$STATE/linux-v7.0.0-sm8150" && "${PMB[@]}" build --envkernel linux-postmarketos-qcom-sm8150)
"${PMB[@]}" build --arch aarch64 -i --force firmware-oneplus-guacamole device-oneplus-guacamole hexagonrpcd iio-sensor-proxy
"${PMB[@]}" build --arch aarch64 -i --force postmarketos-mkinitfs-hook-netboot libcamera-rear
# Fresh isolated work directory: refuse ambiguous/stale packages instead of choosing newest.
version() {
    local name="$1" files
    mapfile -t files < <(find "$WORK/packages/edge/aarch64" -maxdepth 1 -type f -name "${name}-[0-9]*.apk")
    (( ${#files[@]} == 1 )) || { echo "Expected exactly one $name APK" >&2; return 1; }
    local value="${files[0]##*/}"
    value="${value#"$name"-}"
    printf '%s' "${value%.apk}"
}
kernel_version="$(version linux-postmarketos-qcom-sm8150)"
hook_version="$(version postmarketos-mkinitfs-hook-netboot)"
libcamera_version="$(version libcamera-rear)"
[[ "$kernel_version" == 7.0.0_p*-r0 ]]
[[ "$hook_version" == 4.2.1-r4 && "$libcamera_version" == 0.7.2-r0 ]]
"${PMB[@]}" --boot-size 1024 --extra-space 4096 install --no-split --filesystem ext4 \
    --add "linux-postmarketos-qcom-sm8150=$kernel_version,postmarketos-mkinitfs-hook-netboot=$hook_version,libcamera-rear=$libcamera_version"
"${PMB[@]}" export "$STATE/rootfs/export" --no-install
python3 scripts/check_rear_camera_package.py --initramfs "$STATE/rootfs/export/initramfs" \
    --record-rootfs "$WORK/chroot_rootfs_oneplus-guacamole"
python3 - "$STATE" "$kernel_version" "$hook_version" "$libcamera_version" <<'PY'
import hashlib,json,sys
from pathlib import Path
state=Path(sys.argv[1]); manifest=Path('build/rear-camera/packaging-inputs.json'); data=json.loads(manifest.read_text())
p=state/'rootfs/export/oneplus-guacamole.img'
with p.open('rb') as f:data['rootfs_image_sha256']=hashlib.file_digest(f,'sha256').hexdigest()
data['rootfs_image']=str(p.resolve())
apk_dir=state/'pmbootstrap-work/packages/edge/aarch64'
data['apks']={}
for name,version in zip(('linux-postmarketos-qcom-sm8150','postmarketos-mkinitfs-hook-netboot','libcamera-rear'),sys.argv[2:]):
    apk=apk_dir/f'{name}-{version}.apk'
    if not apk.is_file(): raise SystemExit(f'missing built APK: {apk}')
    with apk.open('rb') as f:data['apks'][name]={'path':str(apk.resolve()),'sha256':hashlib.file_digest(f,'sha256').hexdigest(),'size':apk.stat().st_size}
manifest.write_text(json.dumps(data,indent=2)+'\n')
PY
INITRAMFS="$STATE/rootfs/export/initramfs" bash scripts/build_bootimg_rear_camera.sh
sha256sum "$WORK"/packages/edge/aarch64/*.apk > build/rear-camera/APK-SHA256SUMS
# Record the final delivery identity and fail before announcing a candidate.
bash scripts/camera_build.sh manifest
bash scripts/camera_build.sh check
printf 'Candidate prepared and identity checks passed. No phone or server action performed. See docs/REAR_CAMERA_7_0_BRINGUP.md\n'
