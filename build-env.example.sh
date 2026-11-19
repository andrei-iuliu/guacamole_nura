# Copy to build-env.local.sh, edit the baseline/key paths, then source in Bash.
export NURA_SOURCE_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export NURA_BASELINE_ROOT="/path/to/preserved-runtime"
export NURA_SSH_PUBLIC_KEY="$HOME/.ssh/id_ed25519.pub"
export NURA_UI=phosh
export NURA_FASTBOOT="$NURA_SOURCE_ROOT/platform-tools/fastboot"

# One fresh kernel build directory per candidate; never reuse an old one.
export RC4_KERNEL_ROOT="$NURA_SOURCE_ROOT/.work/rc4n-build"
export NURA_KERNEL_BUILD="$RC4_KERNEL_ROOT/kernel-output"
export NURA_KERNEL_SOURCE_STATE="$RC4_KERNEL_ROOT/kernel-state"
export NURA_KERNEL_DIR="$NURA_KERNEL_BUILD/kernel-package"

# Boot-image-only retry inputs, after local_storage_build.sh creates the rootfs.
export NURA_LOCAL_INITRAMFS="$NURA_SOURCE_ROOT/flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/rootfs/export/initramfs"
export NURA_LOCAL_ROOTFS_IMAGE="$NURA_SOURCE_ROOT/flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/chroot_native/home/pmos/rootfs/oneplus-guacamole.img"
export NURA_LOCAL_ROOTFS_TREE="$NURA_SOURCE_ROOT/flows/rc4/local-storage/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work/chroot_rootfs_oneplus-guacamole"
