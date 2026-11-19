#!/usr/bin/env bash
# Prepare the exact Nura v6.17.0-sm8150 rootfs for a temporary netboot.
#
# This script is for the USER to run: pmbootstrap needs sudo for its chroots,
# loop devices and image creation. It never flashes, mounts or repartitions the
# phone. The default is a read-only preflight; pass --build to run the
# privileged build stages. See docs/NURA_ROOTFS_BUILD.md.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE="oneplus-guacamole"
DEVICE_DIR="${REPO_ROOT}/device/testing/device-oneplus-guacamole"
FIRMWARE_DIR="${REPO_ROOT}/device/testing/firmware-oneplus-guacamole"
WORK="${REPO_ROOT}/.work/pmbootstrap"
APORTS="${REPO_ROOT}/.work/pmaports"
STATE_DIR="${REPO_ROOT}/.work/rootfs"
CONFIG="${STATE_DIR}/pmbootstrap.cfg"
EXPORT_DIR="${STATE_DIR}/export"
KERNEL_SRC="${REPO_ROOT}/.work/linux-v6.17.0-sm8150"
KERNEL_OUT="${REPO_ROOT}/build/kernel-nura-r25"
KERNEL_PACKAGE_DIR="${REPO_ROOT}/build/nura-kernel-package"
KERNEL_PKGNAME="linux-postmarketos-qcom-sm8150"
KERNEL_RELEASE="6.17.0-sm8150"
SENSOR_TARBALL="guacamole-sensors-20261102.tar.gz"
FIRMWARE_TARBALL="firmware-oneplus-guacamole-24.0-20261011.tar.gz"
PMAPORTS_COMMIT="b2b857484723945088cdbe5f5122c72fec4dc834"
PMBOOTSTRAP_COMMIT="e0dfe2cda7f3a0b70cdcc96f3060100839322aa4"
PMB=(python3 "${REPO_ROOT}/pmbootstrap/pmbootstrap.py" -c "${CONFIG}" -p "${APORTS}" -w "${WORK}")

mode=check
case "${1:-}" in
    "" | --check-only) mode=check ;;
    --build) mode=build ;;
    -h | --help)
        sed -n '2,9p' "$0"
        exit 0
        ;;
    *)
        echo "error: unknown argument: $1 (use --check-only or --build)" >&2
        exit 2
        ;;
esac

die() {
    echo "error: $*" >&2
    exit 1
}

note() {
    echo "  $*"
}

host_commands=(git kpartx losetup openssl ps tar gzip chroot sh python3 debugfs sha512sum)
missing=()
for cmd in "${host_commands[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if ((${#missing[@]})); then
    echo "error: missing host commands: ${missing[*]}" >&2
    cat >&2 <<'EOF'
On Arch/CachyOS the pmbootstrap host prerequisites are:
  sudo pacman -S --needed git openssl tar gzip util-linux procps-ng e2fsprogs multipath-tools python
EOF
    exit 1
fi
note "host commands: OK"

command -v sudo >/dev/null 2>&1 || die "sudo is required for pmbootstrap build/install stages"

[ -f "${REPO_ROOT}/pmbootstrap/pmbootstrap.py" ] ||
    die "pmbootstrap clone is missing; expected ${REPO_ROOT}/pmbootstrap"
[ -d "${APORTS}" ] || die "pmaports clone is missing; expected ${APORTS}"
[ -f "${APORTS}/pmaports.cfg" ] || die "${APORTS}/pmaports.cfg is missing"
CHANNEL="$(sed -n 's/^channel=//p' "${APORTS}/pmaports.cfg" | head -1)"
[ -n "${CHANNEL}" ] || die "cannot read channel from ${APORTS}/pmaports.cfg"

pmaports_head="$(git -C "${APORTS}" rev-parse HEAD 2>/dev/null || true)"
if [ "${pmaports_head}" != "${PMAPORTS_COMMIT}" ]; then
    echo "warning: pmaports HEAD is ${pmaports_head:-unknown}, baseline pin is ${PMAPORTS_COMMIT}" >&2
fi
pmbootstrap_head="$(git -C "${REPO_ROOT}/pmbootstrap" rev-parse HEAD 2>/dev/null || true)"
if [ "${pmbootstrap_head}" != "${PMBOOTSTRAP_COMMIT}" ]; then
    echo "warning: pmbootstrap HEAD is ${pmbootstrap_head:-unknown}, baseline pin is ${PMBOOTSTRAP_COMMIT}" >&2
fi
note "pmaports: ${pmaports_head:-unknown} (channel ${CHANNEL})"
note "pmbootstrap: ${pmbootstrap_head:-unknown}"

# The local recipes must be the ones pmbootstrap reads, not the distro copies.
for pkg in device-oneplus-guacamole firmware-oneplus-guacamole "${KERNEL_PKGNAME}"; do
    link="${APORTS}/device/testing/${pkg}"
    [ -L "${link}" ] || die "${link} is not a symlink to the local recipe"
    [ "$(readlink -f "${link}")" = "${REPO_ROOT}/device/testing/${pkg}" ] ||
        die "${link} does not point at ${REPO_ROOT}/device/testing/${pkg}"
done
for pkg in hexagonrpcd iio-sensor-proxy; do
    link="${APORTS}/main/${pkg}"
    [ -L "${link}" ] || die "${link} is not a symlink to the local recipe"
    [ "$(readlink -f "${link}")" = "${REPO_ROOT}/device/testing/${pkg}" ] ||
        die "${link} does not point at ${REPO_ROOT}/device/testing/${pkg}"
done
note "local device/firmware/kernel/hexagonrpcd/iio recipes: selected"

apkbuild_hash() {
    awk -v name="$2" 'length($1) == 128 && $2 == name { print $1; exit }' "$1"
}

check_file_hash() {
    local file="$1" expected="$2" label="$3"
    [ -f "${file}" ] || die "missing ${label}: ${file}"
    local actual
    actual="$(sha512sum "${file}" | awk '{ print $1 }')"
    [ "${actual}" = "${expected}" ] || die "${label} SHA512 mismatch: ${file}"
    note "${label}: OK (${actual})"
}

# Sensor bundle: public OTA payload, regenerated deterministically if needed.
sensor_expected="$(apkbuild_hash "${DEVICE_DIR}/APKBUILD" "${SENSOR_TARBALL}")"
[ -n "${sensor_expected}" ] || die "no SHA512 entry for ${SENSOR_TARBALL} in device APKBUILD"
sensor_path="${DEVICE_DIR}/${SENSOR_TARBALL}"
if [ "${mode}" = build ] && [ ! -f "${sensor_path}" ]; then
    python3 "${REPO_ROOT}/scripts/prepare_sensor_bundle.py" ||
        die "could not generate ${SENSOR_TARBALL}; see ref/sensors or --vendor-img/--dsp-img"
fi
if [ ! -f "${sensor_path}" ]; then
    die "missing ${sensor_path}; run scripts/prepare_sensor_bundle.py --help"
fi
check_file_hash "${sensor_path}" "${sensor_expected}" "sensor bundle"

firmware_expected="$(apkbuild_hash "${FIRMWARE_DIR}/APKBUILD" "${FIRMWARE_TARBALL}")"
[ -n "${firmware_expected}" ] || die "no SHA512 entry for ${FIRMWARE_TARBALL}"
check_file_hash "${FIRMWARE_DIR}/${FIRMWARE_TARBALL}" "${firmware_expected}" "firmware tarball"

# ADSP blobs are remote sources; pmbootstrap verifies them in its distfiles
# cache. Check the cached copies here when present, so the ADSP addition is
# never silently skipped.
adsp_checked=0
for name in adsp.mdt adsp.b00 adsp.b01 adsp.b02 adsp.b03 adsp.b04 adsp.b05 \
    adsp.b06 adsp.b07 adsp.b08 adsp.b09 adsp.b10 adsp.b11 adsp.b12 adsp.b13 adsp.b14; do
    expected="$(apkbuild_hash "${FIRMWARE_DIR}/APKBUILD" "${name}")"
    [ -n "${expected}" ] || die "no SHA512 entry for ${name}"
    for candidate in "${WORK}/cache_distfiles/${name}" "${REPO_ROOT}/ref/firmware/source-files/${name}"; do
        if [ -f "${candidate}" ]; then
            actual="$(sha512sum "${candidate}" | awk '{ print $1 }')"
            [ "${actual}" = "${expected}" ] || die "ADSP SHA512 mismatch: ${candidate}"
            adsp_checked=$((adsp_checked + 1))
            break
        fi
    done
done
if [ "${adsp_checked}" -eq 16 ]; then
    note "ADSP firmware sources: 16/16 cached and verified"
else
    echo "warning: ${adsp_checked}/16 ADSP sources cached; pmbootstrap will fetch the rest" >&2
fi

# Kernel identity: the boot image, modules and initramfs must all be 6.17.0-sm8150.
[ -f "${KERNEL_PACKAGE_DIR}/boot/vmlinuz" ] ||
    die "missing ${KERNEL_PACKAGE_DIR}/boot/vmlinuz; rebuild the original kernel first"
[ -d "${KERNEL_PACKAGE_DIR}/lib/modules/${KERNEL_RELEASE}" ] ||
    die "missing modules for ${KERNEL_RELEASE} in ${KERNEL_PACKAGE_DIR}"
kernel_image_sha="$(sha256sum "${KERNEL_PACKAGE_DIR}/boot/vmlinuz" | awk '{ print $1 }')"
note "kernel image: ${kernel_image_sha}"

kernel_mode=source
if [ -f "${KERNEL_OUT}/include/config/kernel.release" ] && [ -d "${KERNEL_SRC}" ]; then
    [ "$(cat "${KERNEL_OUT}/include/config/kernel.release")" = "${KERNEL_RELEASE}" ] ||
        die "${KERNEL_OUT} does not contain ${KERNEL_RELEASE}"
    kernel_mode=reuse
    note "kernel build output: reuse ${KERNEL_OUT}"
else
    echo "warning: ${KERNEL_OUT} is absent; the build will compile the kernel from source" >&2
fi

if [ ! -f "${CONFIG}" ]; then
    note "pmbootstrap config: will be written to ${CONFIG}"
else
    grep -q "^device = ${DEVICE}$" "${CONFIG}" ||
        die "${CONFIG} does not select ${DEVICE}; remove it and rerun"
fi
if [ -f "${WORK}/version" ]; then
    [ "$(cat "${WORK}/version")" = "8" ] ||
        die "${WORK}/version is not 8; use a fresh project-local work dir"
elif [ -e "${WORK}" ]; then
    note "pmbootstrap work dir has no version marker; --build will create version 8"
else
    note "pmbootstrap work dir: will be created at ${WORK}"
fi
note "rootfs state: ${STATE_DIR}"

if [ "${mode}" = check ]; then
    cat <<EOF

checks passed; no chroot, build, mount or image command was run.

Next (user-run):
  scripts/rootfs_prepare.sh --build
EOF
    exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
    die "run this script as your normal user; pmbootstrap invokes sudo itself"
fi

mkdir -p "${STATE_DIR}" "${WORK}"
if [ -f "${WORK}/version" ]; then
    [ "$(cat "${WORK}/version")" = "8" ] ||
        die "${WORK}/version is not 8; use a fresh project-local work dir"
else
    if compgen -G "${WORK}/chroot_*" >/dev/null || [ -d "${WORK}/packages" ]; then
        die "${WORK} has chroots or packages but no version marker; refusing to guess"
    fi
    printf '8\n' > "${WORK}/version"
    note "wrote ${WORK}/version (pmbootstrap work folder format 8)"
fi
umask 077
cat > "${CONFIG}" <<EOF
[pmbootstrap]
aports = ${APORTS}
build_pkgs_on_install = false
device = ${DEVICE}
extra_packages = none
hostname = guacamole
keymap = us
locale = en_US.UTF-8
service_manager = systemd
ssh_keys = false
timezone = UTC
ui = phosh
user = user
work = ${WORK}

[providers]

[mirrors]
EOF
chmod 600 "${CONFIG}"
note "wrote ${CONFIG} (Phosh + systemd, project-local work dir)"

if [ "${kernel_mode}" = reuse ]; then
    if [ ! -d "${KERNEL_SRC}/.output" ]; then
        note "creating hardlink clone ${KERNEL_SRC}/.output from ${KERNEL_OUT}"
        cp -al "${KERNEL_OUT}" "${KERNEL_SRC}/.output" ||
            die "could not create ${KERNEL_SRC}/.output"
    fi
    [ "$(cat "${KERNEL_SRC}/.output/include/config/kernel.release")" = "${KERNEL_RELEASE}" ] ||
        die "${KERNEL_SRC}/.output is not a ${KERNEL_RELEASE} build"
    output_image_sha="$(sha256sum "${KERNEL_SRC}/.output/arch/arm64/boot/Image.gz" | awk '{ print $1 }')"
    [ "${output_image_sha}" = "${kernel_image_sha}" ] ||
        die "${KERNEL_SRC}/.output Image.gz does not match ${KERNEL_PACKAGE_DIR}/boot/vmlinuz"
    package_modules="$(find "${KERNEL_PACKAGE_DIR}/lib/modules/${KERNEL_RELEASE}" -name '*.ko' | wc -l)"
    output_modules="$(find "${KERNEL_SRC}/.output" -name '*.ko' | wc -l)"
    [ "${package_modules}" = "${output_modules}" ] ||
        die "module count differs: package ${package_modules}, output ${output_modules}"
    note "kernel modules: ${package_modules} (package and build output agree)"
    # The kernel's O= Makefile contains absolute host paths. --envkernel bind
    # mounts this tree into the chroot, where those paths do not exist; the
    # documented relative stub is what pmbootstrap itself overmounts.
    rm -f "${KERNEL_SRC}/.output/Makefile"
    printf 'include ../Makefile\n' > "${KERNEL_SRC}/.output/Makefile"
    note "packaging the existing kernel output with pmbootstrap build --envkernel"
    (cd "${KERNEL_SRC}" && "${PMB[@]}" build --envkernel "${KERNEL_PKGNAME}")
else
    note "building ${KERNEL_PKGNAME} from the pinned 6.17.0 sources"
    "${PMB[@]}" build --force "${KERNEL_PKGNAME}"
fi

kernel_apks=("${WORK}/packages/${CHANNEL}/aarch64/${KERNEL_PKGNAME}-"*.apk)
((${#kernel_apks[@]})) || die "kernel APK was not produced"
kernel_apk="$(ls -t "${kernel_apks[@]}" | head -1)"
kernel_version="$(basename "${kernel_apk}" .apk)"
kernel_version="${kernel_version#"${KERNEL_PKGNAME}"-}"
case "${kernel_version}" in
    6.17.0*) ;;
    *) die "refusing kernel version ${kernel_version}; expected 6.17.0*" ;;
esac
note "kernel APK: ${kernel_apk} (${kernel_version})"

note "building local recipes (dependencies ignored; kernel is already built)"
"${PMB[@]}" build -i --force \
    firmware-oneplus-guacamole \
    device-oneplus-guacamole \
    hexagonrpcd \
    iio-sensor-proxy
"${PMB[@]}" build -i postmarketos-mkinitfs-hook-netboot

note "creating image with 1024 MiB boot and 4096 MiB extra rootfs space (pmbootstrap will ask for sudo)"
"${PMB[@]}" --boot-size 1024 --extra-space 4096 install \
    --no-split \
    --filesystem ext4 \
    --add "postmarketos-mkinitfs-hook-netboot,${KERNEL_PKGNAME}=${kernel_version}"

rootfs_chroot="${WORK}/chroot_rootfs_${DEVICE}"
rootfs_release="$(cat "${rootfs_chroot}/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" 2>/dev/null || true)"
[ "${rootfs_release}" = "${KERNEL_RELEASE}" ] ||
    die "rootfs kernel is '${rootfs_release:-missing}', expected ${KERNEL_RELEASE}"
[ -d "${rootfs_chroot}/lib/modules/${KERNEL_RELEASE}" ] ||
    die "rootfs is missing modules for ${KERNEL_RELEASE}"
[ -f "${rootfs_chroot}/usr/share/mkinitfs/hooks/netboot.sh" ] ||
    die "rootfs is missing the netboot mkinitfs hook"
note "installed rootfs kernel: ${rootfs_release} (netboot hook present)"

rm -rf "${EXPORT_DIR}"
mkdir -p "${EXPORT_DIR}"
"${PMB[@]}" export "${EXPORT_DIR}" --no-install

[ -e "${EXPORT_DIR}/${DEVICE}.img" ] || die "rootfs image was not exported"
[ -e "${EXPORT_DIR}/initramfs" ] || die "initramfs was not exported"

cat <<EOF

rootfs and initramfs are ready; the phone has not been touched.

  rootfs:     ${EXPORT_DIR}/${DEVICE}.img
  initramfs:  ${EXPORT_DIR}/initramfs
  kernel:     ${KERNEL_PACKAGE_DIR}/boot/vmlinuz

Next (user-run, separate terminals):
  1. ./scripts/rootfs_prepare.sh --check-only
     Before replacing a served image, shut down Linux on the phone and stop
     the old netboot server with Ctrl+C. Never replace an image in use.
  2. python3 ${REPO_ROOT}/pmbootstrap/pmbootstrap.py \\
       -c ${CONFIG} -p ${APORTS} -w ${WORK} netboot serve --replace
  3. Build the temporary boot image with the preserved-DTBO wrapper:
       source scripts/envsetup.sh
       KERNEL_DIR=${KERNEL_PACKAGE_DIR} \\
       INITRAMFS=${EXPORT_DIR}/initramfs \\
       DTB_MODE=abl-quarantine ./scripts/build_bootimg.sh
  4. Boot it with fastboot boot (see docs/TEMPORARY_BOOT.md); do not flash.
     The temporary boot cmdline must include pmos.root=/dev/nbd0p2
     pmos.boot=/dev/nbd0p1 so the NBD subpartitions win over any internal pmOS.
     scripts/build_bootimg.sh adds these tokens by default in wrapper mode.
EOF
