#!/usr/bin/env bash
# USER RUN ONLY: clear leftover Plasma chroots using pmbootstrap, never force-unmount.
set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
STATE=$ROOT/.work/plasma-mobile
WORK=$STATE/pmbootstrap-work
PORT=${1:-10001}
fail() { printf 'FAIL  plasma_shutdown: %s\n' "$*" >&2; exit 1; }
[[ $PORT =~ ^[0-9]+$ ]] && (( PORT >= 1024 && PORT <= 65535 )) || fail 'invalid port'
case $PORT in 9999|10000) fail 'refusing a baseline port' ;; esac
for path in "$STATE" "$WORK"; do
    [[ $(realpath -m -- "$path") == "$path" ]] || fail "path resolves outside Plasma state: $path"
done

phone_outside_linux() {
    local serial android fastboot
    [[ -f $ROOT/build/plasma-mobile/last-boot.json ]] || return 1
    serial=$(python3 - "$ROOT/build/plasma-mobile/last-boot.json" <<'PY'
import json, sys
try:
    serial = json.load(open(sys.argv[1]))['device']['serial']
    if not isinstance(serial, str) or not serial or not serial.isalnum():
        raise ValueError('invalid serial')
    print(serial)
except (OSError, KeyError, ValueError, TypeError):
    sys.exit(1)
PY
    ) || return 1
    android=$ROOT/platform-tools/adb
    fastboot=$ROOT/platform-tools/fastboot
    if [[ -x $android ]]; then
        # Android getprop is positive evidence; an SSH/ping timeout is not.
        if [[ $(timeout 5 "$android" -s "$serial" get-state 2>/dev/null || true) == device ]] &&
            [[ $(timeout 5 "$android" -s "$serial" shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r' || true) =~ ^[0-9]+$ ]]; then
            printf 'Phone %s is in Android.\n' "$serial"
            return 0
        fi
    fi
    if [[ -x $fastboot ]] && timeout 5 "$fastboot" devices 2>/dev/null |
        awk -v serial="$serial" '$1 == serial && $2 == "fastboot" { found=1 } END { exit !found }'; then
        printf 'Phone %s is in fastboot.\n' "$serial"
        return 0
    fi
    return 1
}

ports=(10001)
[[ $PORT == 10001 ]] || ports+=("$PORT")
for port in "${ports[@]}"; do
    listeners=$(ss -H -ltn "( sport = :$port )") || fail 'cannot inspect the candidate port'
    if [[ -n $listeners ]]; then
        phone_outside_linux || fail "port $port is already listening; cleanup never stops another server without confirming this phone is in Android/fastboot; disconnect alone is insufficient"
        sudo python3 "$ROOT/scripts/stop_plasma_server.py" "$WORK" "$port" ||
            fail 'could not stop the verified stale Plasma server'
        for (( attempt=0; attempt<20; attempt++ )); do
            listeners=$(ss -H -ltn "( sport = :$port )") || fail 'cannot inspect the candidate port'
            [[ -z $listeners ]] && break
            sleep 0.25
        done
        [[ -z $listeners ]] || fail "port $port remains in use after stale-server cleanup; refusing to unmount"
    fi
done

mounted() {
    local targets target
    targets=$(findmnt -rn -o TARGET) || fail 'cannot inspect mounts'
    while IFS= read -r target; do
        # findmnt escapes whitespace/backslashes in raw output.
        printf -v target '%b' "$target"
        case $target in "$STATE"|"$STATE"/*) return 0 ;; esac
    done <<< "$targets"
    return 1
}

if ! mounted; then
    exit 0
fi
PMB=$STATE/pmbootstrap/pmbootstrap.py
CFG=$STATE/rootfs/pmbootstrap.cfg
APORTS=$STATE/pmaports
[[ -f $PMB && -f $CFG && -d $APORTS && -d $WORK ]] ||
    fail 'leftover mounts found but the Plasma pmbootstrap installation is incomplete'
printf 'Cleaning up leftover Plasma mounts with pmbootstrap shutdown (may request sudo).\n'
python3 "$PMB" -c "$CFG" -p "$APORTS" -w "$WORK" shutdown ||
    fail 'pmbootstrap shutdown failed; no forced unmount was attempted'
if mounted; then
    fail 'mounts remain after shutdown; stop any other Plasma build/chroot and retry'
fi
printf 'Plasma mounts cleared.\n'
