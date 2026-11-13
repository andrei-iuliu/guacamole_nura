#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# rc4 wrapper: a strictly read-only check and the three user-run build steps.
#
#   flows/rc4/scripts/rc4.sh check            read-only verification, no writes
#   flows/rc4/scripts/rc4.sh contract         fake-builder kernel->rootfs contract test
#   flows/rc4/scripts/rc4.sh kernel           user-run rootless kernel package
#   flows/rc4/scripts/rc4.sh rootfs [--authorized-key FILE.pub]
#                                             user-run rootfs/initramfs (local root)
#   flows/rc4/scripts/rc4.sh bootimg          user-run boot image repack
#
# ONE kernel build contract (mirrors the reviewed rc3 layout):
#
#   flows/rc4/build-source/kernel-state/            NURA_KERNEL_STATE
#       linux-v7.0.0-sm8150/                        extracted source
#       output/                                     NURA_KERNEL_OUTPUT  (kbuild O=)
#           include/config/kernel.release
#   flows/rc4/build-source/kernel-output/           NURA_KERNEL_OUTPUT's consumer
#       kernel-package/                             NURA_KERNEL_STAGE
#           boot/vmlinuz, boot/dtbs/qcom/*.dtb,
#           usr/share/kernel/postmarketos-qcom-sm8150/kernel.release,
#           usr/lib/modules/<release>/...
#       Image                                       NURA_KERNEL_IMAGE
#       kernel-inputs.json                          written by `kernel-record`
#       DELIVERY.json                               written by the rootfs phase
#                                                   (`kernel-manifest`)
#
# The rootfs flow reads kernel-state/output (for the kernel.release snapshot) and
# kernel-output/{kernel-package,Image,kernel-inputs.json}; it writes
# kernel-output/DELIVERY.json after the rootfs export and boot pack.  The kernel
# phase therefore has to finish with `kernel-record`, exactly as rc3_build.sh
# did; the plain builder does not write kernel-inputs.json.
#
# Everything the build steps write stays inside flows/rc4:
#   flows/rc4/build-source/   kernel state/output/staging (kernel step)
#   flows/rc4/local-storage/  rootfs state + boot image (rootfs/bootimg steps)
#
# There is deliberately no command that touches the phone, the partition table,
# mounts or running services: those stay manual and are described in
# docs/NURA_RC4.md.  The kernel step reuses the reviewed parameterized
# scripts/build_rear_camera_kernel.sh through its NURA_* interface.
#
# Exit status: 0 on success, non-zero on the first failure.
set -Eeuo pipefail

# Every python3 run below is a read-only or build step; never leave __pycache__
# behind in the source tree (and `check` must not write anything at all).
export PYTHONDONTWRITEBYTECODE=1

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
RC4_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_ROOT="${NURA_SOURCE_ROOT:-runtime-workspace}"
RECIPE="$RC4_DIR/device/testing/linux-postmarketos-qcom-sm8150"
DEVICE_RECIPE="$RC4_DIR/device/testing/device-oneplus-guacamole"
# RC4_KERNEL_ROOT exists for the rootless fake-builder fixture only: it moves
# the whole kernel state/output pair (never a single path) so the test can run
# the real wrapper without touching a real build.
KERNEL_ROOT="${RC4_KERNEL_ROOT:-$RC4_DIR/build-source}"
KERNEL_STATE="$KERNEL_ROOT/kernel-state"
KERNEL_OUT="$KERNEL_ROOT/kernel-output"
KERNEL_BUILD="$KERNEL_STATE/output"          # NURA_KERNEL_OUTPUT
KERNEL_STAGE="$KERNEL_OUT/kernel-package"    # NURA_KERNEL_STAGE
KERNEL_IMAGE="$KERNEL_OUT/Image"             # NURA_KERNEL_IMAGE
KERNEL_RECIPE_REL="flows/rc4/device/testing/linux-postmarketos-qcom-sm8150"
RELEASE="7.0.0-sm8150-guacamole-rc4"
BUILDER="${NURA_KERNEL_BUILDER:-$SOURCE_ROOT/scripts/build_rear_camera_kernel.sh}"
LOCAL_FLOW="$RC4_DIR/local-storage/scripts/local_storage_build.sh"
LOCAL_WRAPPER="$RC4_DIR/local-storage/scripts/local_bootimg_wrapper.sh"
CHECKER="$RC4_DIR/local-storage/scripts/check_local_candidate.py"

die() { echo "ERROR: $*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

usage() { sed -n '2,13p' "$0"; }

dtb_candidates() {
    printf '%s\n' \
        "$KERNEL_STAGE/boot/dtbs/qcom/sm8150-oneplus-guacamole.dtb" \
        "$RC4_DIR/build-source/artifacts/dtb/sm8150-oneplus-guacamole.dtb"
}

check() {
    echo "== rc4 source check (read-only: no files are written) =="
    python3 "$SCRIPT_DIR/check_recipe.py" --recipe "$RECIPE" --device "$DEVICE_RECIPE" \
        --local-flow "$RC4_DIR/local-storage" --release "$RELEASE"
    local dtb=""
    while IFS= read -r candidate; do
        if [[ -f "$candidate" ]]; then dtb="$candidate"; break; fi
    done < <(dtb_candidates)
    if [[ -n "$dtb" ]]; then
        note "DTB gates: $dtb"
        python3 "$SCRIPT_DIR/dt_facts.py" "$dtb" --require-rc4-gates
    else
        note "no built DTB yet; DT gates not evaluated (run: rc4.sh kernel)"
    fi
    if [[ ! -d "$KERNEL_STATE" ]]; then
        note "no full kernel build yet; run: flows/rc4/scripts/rc4.sh kernel"
    fi
    # Only write-free suites run here.  The fake-builder contract test creates
    # temporary trees under flows/rc4/build-source and is `rc4.sh contract`.
    echo "== rootless integration tests (read-only) =="
    python3 -m unittest discover -s "$RC4_DIR/tests" -p 'test_rc4_integration.py' -v
    python3 -m unittest discover -s "$RC4_DIR/tests" -p 'test_rc4_flow_guards.py' -v
    echo "== copied local-storage unit tests (public key + storage rehearsal) =="
    python3 -m unittest discover -s "$RC4_DIR/local-storage/tests" -p 'test_*.py'
    echo "== rc4 check: PASS (contract fixture: bash flows/rc4/scripts/rc4.sh contract) =="
}

contract() {
    # Executable fixture: a fake builder that consumes the documented NURA_*
    # contract, then the real wrapper, the rootfs dry-run/check-only preflight
    # and the checker handoff.  Writes only inside flows/rc4/build-source and
    # removes its temporary trees; no sudo, mount, pmbootstrap or phone.
    echo "== rc4 kernel->rootfs contract (fake builder, rootless) =="
    python3 -m unittest discover -s "$RC4_DIR/tests" -p 'test_rc4_kernel_contract.py' -v
    echo "== rc4 contract: PASS =="
}

# The kernel phase must leave exactly what the rootfs flow reads.  Fail loudly
# here instead of three steps later in the rootfs preflight.
verify_kernel_contract() {
    [[ -f "$KERNEL_BUILD/include/config/kernel.release" ]] ||
        die "kernel build did not produce $KERNEL_BUILD/include/config/kernel.release;" \
            "NURA_KERNEL_OUTPUT must be \$KERNEL_STATE/output"
    local built staged
    built="$(cat "$KERNEL_BUILD/include/config/kernel.release")"
    [[ "$built" = "$RELEASE" ]] ||
        die "kernel output release is '$built', expected '$RELEASE'"
    [[ -f "$KERNEL_STAGE/boot/vmlinuz" ]] ||
        die "kernel stage is incomplete: $KERNEL_STAGE/boot/vmlinuz"
    staged="$(cat "$KERNEL_STAGE/usr/share/kernel/postmarketos-qcom-sm8150/kernel.release" 2>/dev/null || true)"
    [[ "$staged" = "$RELEASE" ]] ||
        die "staged kernel release is '$staged', expected '$RELEASE'"
    [[ -f "$KERNEL_IMAGE" ]] || die "missing kernel Image: $KERNEL_IMAGE"
    [[ -f "$KERNEL_OUT/kernel-inputs.json" ]] ||
        die "kernel-record did not write $KERNEL_OUT/kernel-inputs.json"
    local dtb
    for dtb in "$KERNEL_STAGE"/boot/dtbs/qcom/sm8150-oneplus-guacamole*.dtb; do
        [[ -f "$dtb" ]] || die "missing staged DTB: $dtb"
    done
    note "contract ok: state/output, kernel-package, Image, kernel-inputs.json"
}

build_kernel() {
    [[ -e "$BUILDER" ]] || die "missing kernel builder: $BUILDER"
    [[ -f "$RECIPE/APKBUILD" ]] || die "missing rc4 recipe: $RECIPE/APKBUILD"
    mkdir -p "$KERNEL_STATE" "$KERNEL_BUILD" "$KERNEL_STAGE" "$(dirname "$KERNEL_IMAGE")"
    echo "== rc4 kernel build (rootless, isolated) =="
    note "recipe:  $RECIPE"
    note "state:   $KERNEL_STATE"
    note "output:  $KERNEL_BUILD  (NURA_KERNEL_OUTPUT)"
    note "stage:   $KERNEL_STAGE  (NURA_KERNEL_STAGE)"
    note "image:   $KERNEL_IMAGE  (NURA_KERNEL_IMAGE)"
    note "release: $RELEASE"
    note "root:    $KERNEL_ROOT"
    note "tarball: $KERNEL_STATE/linux-v7.0.0-sm8150.tar.gz (staged from the"
    note "         read-only .work/rear-camera copy unless NURA_SOURCE_TARBALL is set)"
    NURA_RECIPE_DIR="$RECIPE" \
    NURA_KERNEL_RELEASE="$RELEASE" \
    NURA_KERNEL_STATE="$KERNEL_STATE" \
    NURA_KERNEL_OUTPUT="$KERNEL_BUILD" \
    NURA_KERNEL_STAGE="$KERNEL_STAGE" \
    NURA_KERNEL_IMAGE="$KERNEL_IMAGE" \
        bash "$BUILDER" "$@"
    echo "== recording the kernel build identity (kernel-record) =="
    python3 "$CHECKER" kernel-record \
        --build "$KERNEL_OUT" --state "$KERNEL_STATE" --recipe "$KERNEL_RECIPE_REL"
    verify_kernel_contract
    echo "== rc4 kernel build: PASS =="
    echo "next: bash flows/rc4/scripts/rc4.sh rootfs --authorized-key /path/to/key.pub"
}

build_rootfs() {
    [[ -f "$LOCAL_FLOW" ]] || die "missing local-storage entrypoint: $LOCAL_FLOW"
    [[ -f "$KERNEL_OUT/kernel-inputs.json" ]] ||
        die "missing $KERNEL_OUT/kernel-inputs.json; run: flows/rc4/scripts/rc4.sh kernel"
    echo "== rc4 rootfs/initramfs (user-run, local root, no NBD) =="
    bash "$LOCAL_FLOW" --build "$@"
}

build_bootimg() {
    [[ -f "$LOCAL_WRAPPER" ]] || die "missing boot-image wrapper: $LOCAL_WRAPPER"
    echo "== rc4 boot image repack (user-run) =="
    bash "$LOCAL_WRAPPER" --build "$@"
}

action="${1:-check}"
(( $# )) && shift
case "$action" in
    check) check ;;
    contract) contract ;;
    kernel) build_kernel "$@" ;;
    rootfs) build_rootfs "$@" ;;
    bootimg) build_bootimg "$@" ;;
    -h|--help|help) usage ;;
    *) die "unknown action: $action (expected check|contract|kernel|rootfs|bootimg)" ;;
esac
