#!/usr/bin/env bash
# USER RUN ONLY: pmbootstrap invokes sudo/mount/chroots. Never run by an agent.
set -Eeuo pipefail
(( EUID != 0 )) || { echo 'Run as your normal user; pmbootstrap handles privilege.' >&2; exit 1; }
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
STATE="$ROOT/.work/rear-camera"
WORK="$STATE/pmbootstrap-work"
mkdir -p "$STATE/rootfs"
exec > >(tee -a "$STATE/rootfs/package-$(date +%Y%m%d-%H%M%S).log") 2>&1
trap 'status=$?; echo "Candidate packaging failed at line $LINENO (exit $status)" >&2; exit "$status"' ERR
# These paths must all stay in this checkout, apart from read-only Git metadata.
test "$(readlink -f "$STATE/pmaports/device/testing/linux-postmarketos-qcom-sm8150")" = "$ROOT/device/testing/rear-camera/linux-postmarketos-qcom-sm8150"
test "$(readlink -f "$STATE/pmaports/main/postmarketos-mkinitfs-hook-netboot")" = "$ROOT/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot"
test "$(cat "$STATE/output/include/config/kernel.release")" = '7.0.0-sm8150-rear1'
grep -q '^NBD_PORT=10001$' "$ROOT/device/testing/rear-camera/postmarketos-mkinitfs-hook-netboot/netboot.sh"
# Refuse a migrated/shared work directory.
if [[ -f "$WORK/version" ]]; then
    test "$(cat "$WORK/version")" = 8
elif [[ -d "$WORK" && -n "$(ls -A "$WORK")" ]]; then
    echo "Nonempty work directory without a version: $WORK" >&2; exit 1
else
    mkdir -p -m 700 "$WORK/cache_git"
    printf '8\n' > "$WORK/version"
fi
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
python3 - "$STATE" <<'PY'
import hashlib,json,sys
from pathlib import Path
state=Path(sys.argv[1]); manifest=Path('build/rear-camera/packaging-inputs.json'); data=json.loads(manifest.read_text())
p=state/'rootfs/export/oneplus-guacamole.img'
with p.open('rb') as f:data['rootfs_image_sha256']=hashlib.file_digest(f,'sha256').hexdigest()
data['rootfs_image']=str(p.resolve())
manifest.write_text(json.dumps(data,indent=2)+'\n')
PY
INITRAMFS="$STATE/rootfs/export/initramfs" bash scripts/build_bootimg_rear_camera.sh
sha256sum "$WORK"/packages/edge/aarch64/*.apk > build/rear-camera/APK-SHA256SUMS
printf 'Candidate prepared. No phone or server action performed. See docs/REAR_CAMERA_7_0_BRINGUP.md\n'
