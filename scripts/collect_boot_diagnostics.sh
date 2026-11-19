#!/bin/sh
# collect_boot_diagnostics.sh - tiny bounded startup snapshot for journald.
#
# Runs from nura-boot-diagnostics.service at multi-user and again from
# nura-boot-diagnostics.timer at boot+3min.  It writes NOTHING to disk: stdout
# goes to the persistent journal, which already records _BOOT_ID and
# __MONOTONIC_TIMESTAMP for every line.  The kernel journal is persisted by
# journald; this script does not copy, filter or retain it, and creates no
# boot-ID directories, boot sequence, retention or StateDirectory.
#
# The snapshot is limited to:
#   - `systemctl --failed` output, verbatim (never through a kernel filter);
#   - a fixed non-personal /proc and /sys allowlist.
# Missing systemctl is reported as [MISSING] and does not fail the unit.
#
# It does not survive NBD cable loss: if the rootfs is unavailable, journald
# and this output cannot be written.  pstore/ramoops is separate.

set -u

# Fixed bounds; there are deliberately no runtime knobs.
MAX_BYTES=16384
MAX_LINES=120
MAX_FAILED_LINES=80
MAX_FAILED_BYTES=8192
MAX_SYSFS_VALUE=512
MAX_SYSFS_ENTRIES=64
CMD_TIMEOUT=5
ROOT=${NURA_BOOT_DIAGNOSTICS_ROOT:-}   # test-only prefix; unset on the phone

have() { command -v "$1" >/dev/null 2>&1; }
missing() { printf '[MISSING] %s\n' "$*"; }

for _tool in head tr; do
    have "$_tool" || { missing "$_tool"; exit 0; }
done

if [ -n "$ROOT" ]; then
    case "$ROOT" in
        /*) ;;
        *) missing "invalid test root"; exit 0 ;;
    esac
    [ -d "$ROOT" ] || { missing "test root"; exit 0; }
fi

bounded_systemctl() {
    if have timeout; then
        timeout "$CMD_TIMEOUT" systemctl --failed --no-legend --no-pager 2>/dev/null
    else
        systemctl --failed --no-legend --no-pager 2>/dev/null
    fi
}

snapshot() {
    printf 'nura-boot-diagnostics: begin\n'

    printf 'nura-boot-diagnostics: failed-units\n'
    if have systemctl; then
        _failed=$(bounded_systemctl | head -n "$MAX_FAILED_LINES" | head -c "$MAX_FAILED_BYTES")
        if [ -n "$_failed" ]; then
            printf '%s\n' "$_failed"
        else
            printf '[NONE-OR-UNAVAILABLE]\n'
        fi
    else
        missing systemctl
    fi

    printf 'nura-boot-diagnostics: sysfs\n'
    _n=0
    for _p in "$ROOT/proc/asound/cards" \
              "$ROOT"/sys/class/drm/card[0-9]/status \
              "$ROOT"/sys/class/drm/card[0-9][0-9]/status \
              "$ROOT"/sys/class/drm/card*-*/status \
              "$ROOT"/sys/class/udc/*/state \
              "$ROOT"/sys/class/udc/*/current_speed \
              "$ROOT"/sys/class/remoteproc/remoteproc*/state \
              "$ROOT"/sys/class/remoteproc/remoteproc*/name \
              "$ROOT"/sys/class/remoteproc/remoteproc*/firmware \
              "$ROOT"/sys/class/typec/port*/data_role \
              "$ROOT"/sys/class/typec/port*/power_role; do
        _n=$((_n + 1))
        [ "$_n" -le "$MAX_SYSFS_ENTRIES" ] || break
        [ -r "$_p" ] || continue
        [ -d "$_p" ] && continue
        printf '%s=' "$_p"
        head -c "$MAX_SYSFS_VALUE" "$_p" 2>/dev/null | tr '\n' ' '
        printf '\n'
    done

    printf 'nura-boot-diagnostics: end\n'
}

snapshot | head -c "$MAX_BYTES" | head -n "$MAX_LINES"
exit 0
