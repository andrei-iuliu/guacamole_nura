#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
#
# USER RUN ONLY (pmbootstrap uses sudo/chroots). Build the local Phosh
# 9999 0.58.0-r100 (flows/rc4/device/testing/phosh, lockscreen shield fix)
# with the Phosh local-userdata flow's existing pmbootstrap state, without
# rebuilding the rootfs.
#
# The flow's pmaports already carries postmarketOS' extra-repos/systemd/phosh
# (r0). pmbootstrap refuses duplicate package names, so this script swaps
# that directory for a symlink to the local recipe for the duration of the
# build and always restores it. Output APKs land in the flow's package dir;
# the script prints them. Install on the phone with `sudo apk add` (see the
# end of the output). It never touches a phone, partition or image.
set -Eeuo pipefail

FLOW=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
ROOT=$(cd -- "$FLOW/../../.." && pwd -P)
STATE="$FLOW/.work/local-userdata-phosh/rootfs-state"
RECIPE="$ROOT/flows/rc4/device/testing/phosh"
TREE="$STATE/pmaports/extra-repos/systemd/phosh"
BACKUP="$STATE/pmaports-phosh-r0.orig"
PMB=(python3 "$STATE/pmbootstrap/pmbootstrap.py"
     -c "$STATE/rootfs/pmbootstrap.cfg"
     -p "$STATE/pmaports"
     -w "$STATE/pmbootstrap-work")

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "== $*"; }

[[ $EUID -ne 0 ]] || die "run as your user; pmbootstrap calls sudo itself"
[[ -f "$RECIPE/APKBUILD" ]] || die "missing local recipe: $RECIPE"
grep -q '^pkgrel=100$' "$RECIPE/APKBUILD" || die "local recipe is not pkgrel 100"
[[ -f "$STATE/pmbootstrap/pmbootstrap.py" ]] || die "flow pmbootstrap missing: $STATE"
if findmnt -rn -o TARGET | grep -qF "$STATE/pmbootstrap-work/"; then
    die "leftover chroot mounts under $STATE/pmbootstrap-work; clear them first: sudo bash $FLOW/scripts/clear_phosh_mounts.sh"
fi
[[ ! -e "$BACKUP" ]] || die "a previous run left $BACKUP; restore it to $TREE by hand first"
[[ -d "$TREE" && ! -L "$TREE" ]] || die "expected the original phosh recipe directory at $TREE"

restore() {
    local rc=$?
    if [[ -L "$TREE" ]]; then rm -f -- "$TREE"; fi
    if [[ -d "$BACKUP" ]]; then mv -- "$BACKUP" "$TREE"; fi
    note "restored $TREE"
    "${PMB[@]}" shutdown >/dev/null 2>&1 || true
    exit "$rc"
}
trap restore EXIT

mv -- "$TREE" "$BACKUP"
ln -s -- "$RECIPE" "$TREE"
note "building phosh from $RECIPE"
"${PMB[@]}" build --arch aarch64 --ignore-depends --no-depends --force phosh

PKGDIR=$(find "$STATE/pmbootstrap-work/packages" -maxdepth 3 -type d -name aarch64 | head -n1)
note "built packages:"
find "$PKGDIR" -maxdepth 1 -name '*phosh*-99990.58.0-r100.apk' -printf '%p\n' | sort
cat <<EOF

Next (host):  scp the APKs above to the phone's /tmp
Next (phone): sudo apk add /tmp/*phosh*-99990.58.0-r100.apk && sync
              then log out/in or reboot so the new phosh runs.
EOF
