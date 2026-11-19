#!/usr/bin/env bash
# Unmount the leftover pmbootstrap chroot mounts of the local-userdata-phosh
# flow, deepest first. Touches only mounts under this flow's pmbootstrap-work.
# Use when pmbootstrap shutdown leaves mounts behind (e.g. a shared proc
# mount that stays busy). Run with:
#   sudo bash flows/rc4/local-storage/scripts/clear_phosh_mounts.sh
set -u
FLOW=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
W="$FLOW/.work/local-userdata-phosh/rootfs-state/pmbootstrap-work"

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

list() { findmnt -rn -o TARGET | grep -F "$W/" | sort -r; }

echo "before: $(list | wc -l) mounts"
for pass in 1 2 3; do
    mapfile -t mounts < <(list)
    (( ${#mounts[@]} )) || break
    echo "== pass $pass: ${#mounts[@]} mounts"
    for m in "${mounts[@]}"; do
        # findmnt -r escapes spaces etc. as \x20; decode before use.
        m=$(printf '%b' "$m")
        if umount "$m" 2>/tmp/umount.err; then
            echo "ok    $m"
        else
            echo "FAIL  $m: $(cat /tmp/umount.err)"
        fi
    done
done

# Pseudo-filesystems (proc/sys/dev) can stay busy through copies in other
# mount namespaces; they hold no data, so detach them lazily.
while read -r m; do
    m=$(printf '%b' "$m")
    case "$(findmnt -rn -o FSTYPE --target "$m" 2>/dev/null)" in
        proc|sysfs|devtmpfs|devpts|tmpfs|mqueue|binfmt_misc)
            umount -l "$m" && echo "lazy  $m" ;;
    esac
done < <(list)

left=$(list | wc -l)
echo "after: $left mounts"
if (( left )); then
    echo "still mounted:"; list
    echo "processes using them:"
    list | while read -r m; do fuser -vm "$(printf '%b' "$m")" 2>&1; done | sort -u | head -40
    exit 1
fi
echo "all clear"
