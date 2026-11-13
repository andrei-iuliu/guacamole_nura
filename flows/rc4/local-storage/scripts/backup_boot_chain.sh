#!/bin/sh
# USER RUN ON PHONE AS ROOT. Read three fixed partitions into temporary files.
# Does not read userdata, persist, EFS or calibration, and never writes a partition.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run on the phone with sudo sh.' >&2; exit 1; }
case "$(uname -r)" in 7.0.0-sm8150-guacamole-rc4) ;; *) echo "Expected the known rc4 netboot kernel ($(uname -r) does not match $1?)." >&2; exit 1 ;; esac
DEST=$(mktemp -d /tmp/nura-boot-chain.XXXXXX)
chmod 755 "$DEST"
for name in boot_b dtbo_b vbmeta_b; do
    source=/dev/disk/by-partlabel/$name
    [ -b "$source" ] || { echo "Missing partition: $name" >&2; exit 1; }
    size=$(blockdev --getsize64 "$source")
    case "$size" in ''|*[!0-9]*) exit 1 ;; esac
    [ "$size" -gt 0 ] && [ "$size" -le 134217728 ] || { echo "Unexpected size: $name $size" >&2; exit 1; }
    before=$(sha256sum "$source" | awk '{print $1}')
    dd if="$source" of="$DEST/$name.img" bs=1048576
    [ "$(stat -c %s "$DEST/$name.img")" = "$size" ] || exit 1
    after=$(sha256sum "$source" | awk '{print $1}')
    copy=$(sha256sum "$DEST/$name.img" | awk '{print $1}')
    [ "$before" = "$after" ] && [ "$before" = "$copy" ] || { echo "Backup mismatch: $name" >&2; exit 1; }
    chmod 644 "$DEST/$name.img"
    printf '%s  %s.img\n' "$copy" "$name" >> "$DEST/SHA256SUMS"
done
chmod 644 "$DEST/SHA256SUMS"
printf '\nBackups verified on phone: %s\nCopy this directory to the host before rebooting.\n' "$DEST"
