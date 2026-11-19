#!/usr/bin/env bash
# USB network helper for the rear-camera netboot session.
#
# Finds the phone's USB network gadget interface on this host, waits for it to
# enumerate, configures 172.16.42.2/24 idempotently and removes only the
# address this tool itself added. It never starts a server, never runs a
# phone-side command, and never touches the baseline 172.16.42.2 setup.
#
#   scripts/camera_usb_net.sh detect [--iface NAME]
#   scripts/camera_usb_net.sh wait   [--iface NAME] [--timeout SECONDS]
#   scripts/camera_usb_net.sh up     [--iface NAME]
#   scripts/camera_usb_net.sh status [--iface NAME]
#   scripts/camera_usb_net.sh down
#
# `detect` writes only `IFACE=<name>` to stdout (empty value, exit 1, when no
# USB network interface is present) so callers can parse it. Everything else
# goes to stderr.
#
# Environment (all optional):
#   NURA_SYSFS_ROOT  sysfs tree to inspect; fixture directories for tests (default /sys)
#   NURA_STATE_DIR   ownership record directory (default <repo>/.work/delivery)
#   NURA_USB_IFACE   preferred interface name, same variable serve_rear_camera.sh used
set -Eeuo pipefail

PROG=${0##*/}
ADDR=172.16.42.2/24
PEER=172.16.42.1
SYSFS=${NURA_SYSFS_ROOT:-/sys}
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
PROFILE=${NURA_PROFILE:-rear-camera}
STATE_DIR=
STATE_FILE=

COMMAND=
IFACE_ARG=${NURA_USB_IFACE:-}
TIMEOUT=180
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: camera_usb_net.sh <command> [options]

Commands:
  detect   print IFACE=<name> for the selected USB network interface
  wait     wait until a USB network interface exists, then print IFACE=<name>
  up       bring the interface up and configure 172.16.42.2/24 (idempotent)
  status   print interface, carrier, addresses, route and address ownership
  down     remove 172.16.42.2/24 again, but only if this tool added it

Options:
  --iface NAME     use NAME instead of auto-detection (default: $NURA_USB_IFACE)
  --timeout SECS   wait timeout, never infinite (default: 180)
  --profile NAME   state profile: rear-camera (default) or plasma-mobile
  --dry-run        print the privileged commands without running them
  -h, --help       show this help

Auto-detection prefers an interface whose device driver is a USB network driver
(cdc_ncm, cdc_ether, rndis_host, usbnet, ...), then carrier-up, then the
highest ifindex (newest). All candidates are printed for inspection.
EOF
}

die() { printf '%s: %s\n' "$PROG" "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }
warn() { printf 'WARN  %s\n' "$*" >&2; }
pass() { printf 'PASS  %s\n' "$*" >&2; }

# Keep the default ownership record exactly where it always was.  Plasma must
# not remove or claim an address recorded by the working Phosh session.
configure_profile() {
    local delivery
    case $PROFILE in
        rear-camera) delivery=delivery ;;
        plasma-mobile) delivery=delivery-plasma-mobile ;;
        *) die "unknown profile '$PROFILE' (use rear-camera or plasma-mobile)" ;;
    esac
    STATE_DIR=${NURA_STATE_DIR:-$ROOT/.work/$delivery}
    STATE_FILE=$STATE_DIR/usb-net.state
    if [[ $PROFILE == plasma-mobile ]]; then
        case "$(readlink -m -- "$STATE_DIR")/" in
            "$ROOT/.work/delivery/"*)
                die "plasma-mobile must not share Phosh USB state: $STATE_DIR" ;;
        esac
    fi
}

is_usb_driver() {
    case "$1" in
        cdc_ncm|cdc_ether|rndis_host|usbnet|ax88179_178a|asix|smsc95xx|r8152) return 0 ;;
        *) return 1 ;;
    esac
}

iface_exists() { [[ -e $SYSFS/class/net/$1 ]]; }

iface_driver() {
    local link=$SYSFS/class/net/$1/device/driver target
    [[ -e $link ]] || return 0
    target=$(readlink -f -- "$link" 2>/dev/null) || return 0
    printf '%s' "${target##*/}"
}

iface_uevent() {
    local file=$SYSFS/class/net/$1/device/uevent
    [[ -r $file ]] && cat -- "$file"
    return 0
}

iface_carrier() {
    local value
    if value=$(cat -- "$SYSFS/class/net/$1/carrier" 2>/dev/null); then
        printf '%s' "$value"
        return 0
    fi
    value=$(cat -- "$SYSFS/class/net/$1/operstate" 2>/dev/null) || value=unknown
    case $value in
        up) printf '1' ;;
        *) printf '0' ;;
    esac
}

iface_ifindex() {
    local value
    value=$(cat -- "$SYSFS/class/net/$1/ifindex" 2>/dev/null) || value=0
    printf '%s' "$value"
}

iface_is_usb() {
    local driver device
    driver=$(iface_driver "$1")
    is_usb_driver "$driver" && return 0
    iface_uevent "$1" | grep -qx 'DEVTYPE=usb_interface' && return 0
    device=$(readlink -f -- "$SYSFS/class/net/$1/device" 2>/dev/null) || device=
    [[ -n $device && $device == */usb* ]] && return 0
    return 1
}

# Machine table: iface|driver|carrier|ifindex (pipe-separated so empty fields
# stay in place; ths is also what tests feed through fixture sysfs trees).
usb_candidates() {
    local dir iface
    [[ -d $SYSFS/class/net ]] || return 0
    for dir in "$SYSFS"/class/net/*; do
        iface=${dir##*/}
        [[ $iface == lo ]] && continue
        iface_is_usb "$iface" || continue
        printf '%s|%s|%s|%s\n' "$iface" "$(iface_driver "$iface")" \
            "$(iface_carrier "$iface")" "$(iface_ifindex "$iface")"
    done
}

print_candidates() {
    local rows iface driver carrier ifindex
    rows=$(usb_candidates)
    if [[ -z $rows ]]; then
        note "no USB network interface in $SYSFS/class/net (driver/uevent check found none)"
        return 0
    fi
    note "USB network interface candidates (iface driver carrier ifindex):"
    while IFS='|' read -r iface driver carrier ifindex; do
        note "  ${iface:-?} ${driver:-unknown} carrier=${carrier:-0} ifindex=${ifindex:-0}"
    done <<<"$rows"
}

# Prefer an explicit name, else carrier-up, then the highest ifindex (newest).
pick_iface() {
    local explicit=${1:-} rows best
    if [[ -n $explicit ]]; then
        iface_exists "$explicit" || return 1
        printf '%s' "$explicit"
        return 0
    fi
    rows=$(usb_candidates)
    [[ -n $rows ]] || return 1
    best=$(printf '%s\n' "$rows" | sort -t'|' -k3,3nr -k4,4nr -k1,1)
    printf '%s' "${best%%|*}"
}

cmd_string() {
    local out='' word
    for word in "$@"; do out+=${out:+ }$(printf '%q' "$word"); done
    printf '%s' "$out"
}

# Run a privileged command, or print it under --dry-run.
run() {
    local -a privileged=(sudo)
    (( EUID == 0 )) && privileged=()
    if (( DRY_RUN )); then
        printf 'DRY-RUN: %s\n' "$(cmd_string "${privileged[@]}" "$@")"
        return 0
    fi
    note "+ $(cmd_string "${privileged[@]}" "$@")"
    "${privileged[@]}" "$@"
}

cmd_output() {
    if (( DRY_RUN )); then
        printf 'DRY-RUN: %s\n' "$(cmd_string "$@")"
        return 0
    fi
    "$@"
}

state_get() {
    local key=$1
    [[ -r $STATE_FILE ]] || return 0
    sed -n "s/^$key=//p" "$STATE_FILE" | head -n1
}

write_state() {
    local iface=$1 owned=$2
    if (( DRY_RUN )); then
        printf 'DRY-RUN: write %s (IFACE=%s OWNED=%s)\n' "$STATE_FILE" "$iface" "$owned"
        return 0
    fi
    mkdir -p -- "$STATE_DIR"
    {
        printf 'IFACE=%s\n' "$iface"
        printf 'ADDR=%s\n' "$ADDR"
        printf 'OWNED=%s\n' "$owned"
        printf 'UPDATED=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >"$STATE_FILE"
    note "address ownership recorded in $STATE_FILE"
}

addr_on_iface() {
    ip -o -4 address show dev "$1" 2>/dev/null | grep -Fq " inet $ADDR "
}

other_ifaces_with_addr() {
    local self=$1 iface
    while read -r iface; do
        [[ $iface == "$self" ]] && continue
        printf '%s ' "$iface"
    done < <(ip -o -4 address show 2>/dev/null | awk -v a="$ADDR" '$0 ~ (" inet " a " ") {print $2}')
}

show_nm_state() {
    local iface=$1 state
    command -v nmcli >/dev/null 2>&1 || return 0
    state=$(nmcli -t -f DEVICE,STATE device status 2>/dev/null \
        | awk -F: -v d="$iface" '$1==d {print $2}' || true)
    case $state in
        connected|connecting)
            warn "NetworkManager still manages $iface ($state); it can flush $ADDR."
            warn "If the address disappears: sudo nmcli device set $iface managed no"
            ;;
    esac
}

check_route() {
    local iface=$1 route
    route=$(ip route get "$PEER" 2>/dev/null) || route=
    route=${route//$'\n'/ }
    if [[ -z $route ]]; then
        warn "no route to $PEER yet (expected until the address is configured)"
        return 0
    fi
    if [[ $route == *" dev $iface "* ]]; then
        pass "route to $PEER uses $iface ($route)"
    else
        warn "route to $PEER does not use $iface: $route"
        warn "remove the conflicting route or interface before serving NBD"
    fi
}

cmd_detect() {
    local selected
    print_candidates
    if ! selected=$(pick_iface "$IFACE_ARG"); then
        warn "no USB network interface found; connect the phone and boot the candidate"
        printf 'IFACE=\n'
        return 1
    fi
    pass "selected interface $selected"
    printf 'IFACE=%s\n' "$selected"
}

cmd_wait() {
    local deadline=$((SECONDS + TIMEOUT)) selected
    while :; do
        if selected=$(pick_iface "$IFACE_ARG"); then
            pass "USB network interface $selected is present"
            printf 'IFACE=%s\n' "$selected"
            return 0
        fi
        (( SECONDS < deadline )) || die "timed out after ${TIMEOUT}s waiting for ${IFACE_ARG:-a USB network interface}; check the cable, the phone state and NURA_USB_IFACE"
        if (( (deadline - SECONDS) % 10 == 0 )); then
            note "waiting for ${IFACE_ARG:-a USB network interface}, $((deadline - SECONDS))s left"
        fi
        sleep 1
    done
}

cmd_up() {
    local iface=$1 conflicts owned
    iface_exists "$iface" || die "no such interface: $iface"
    conflicts=$(other_ifaces_with_addr "$iface")
    if [[ -n $conflicts ]]; then
        die "$ADDR is already configured on: ${conflicts% }; remove it there first (sudo ip addr del $ADDR dev <iface>)"
    fi
    run ip link set dev "$iface" up
    if addr_on_iface "$iface"; then
        owned=$(state_get OWNED)
        [[ $(state_get IFACE) == "$iface" && -n $owned ]] || owned=no
        pass "$ADDR already on $iface (owned=$owned, left as-is)"
        write_state "$iface" "$owned"
    else
        run ip addr replace "$ADDR" dev "$iface"
        pass "configured $ADDR on $iface"
        write_state "$iface" yes
    fi
    show_nm_state "$iface"
    check_route "$iface"
}

cmd_status() {
    local iface selected owned carrier driver
    print_candidates
    if ! selected=$(pick_iface "$IFACE_ARG"); then
        warn "no USB network interface present"
        return 1
    fi
    iface=${IFACE_ARG:-$selected}
    carrier=$(iface_carrier "$iface")
    driver=$(iface_driver "$iface")
    printf 'IFACE=%s\n' "$iface"
    printf 'DRIVER=%s\n' "$driver"
    printf 'CARRIER=%s\n' "$carrier"
    printf 'STATE_IFACE=%s\n' "$(state_get IFACE)"
    owned=$(state_get OWNED)
    printf 'OWNED=%s\n' "${owned:-}"
    printf 'ADDRESSES=%s\n' "$(ip -o -4 address show dev "$iface" 2>/dev/null | awk '{print $4}' | paste -sd, -)"
    printf 'ROUTE=%s\n' "$(ip route get "$PEER" 2>/dev/null | tr '\n' ' ' || true)"
    if [[ $carrier == 1 ]]; then pass "carrier up"; else warn "carrier down on $iface"; fi
    check_route "$iface"
}

cmd_down() {
    local iface owned
    iface=$(state_get IFACE)
    owned=$(state_get OWNED)
    if [[ -z $iface ]]; then
        note "no recorded address ownership in $STATE_FILE; nothing to remove"
        return 0
    fi
    if [[ $owned == yes ]] && addr_on_iface "$iface"; then
        run ip addr del "$ADDR" dev "$iface"
        pass "removed $ADDR from $iface"
    else
        note "$ADDR on $iface was not added by this tool (owned=${owned:-unset}); leaving it"
    fi
    if (( DRY_RUN )); then
        printf 'DRY-RUN: rm -f %s\n' "$STATE_FILE"
        return 0
    fi
    rm -f -- "$STATE_FILE"
    pass "removed $STATE_FILE"
}

parse_args() {
    [[ $# -gt 0 ]] || die "no command given (try --help)"
    case $1 in
        -h|--help) usage; exit 0 ;;
        detect|wait|up|status|down) COMMAND=$1; shift ;;
        *) die "unknown command: $1 (try --help)" ;;
    esac
    while (( $# )); do
        case $1 in
            --iface) [[ $# -ge 2 ]] || die "--iface needs a value"; IFACE_ARG=$2; shift ;;
            --timeout) [[ $# -ge 2 ]] || die "--timeout needs a value"; TIMEOUT=$2; shift ;;
            --profile) [[ $# -ge 2 ]] || die "--profile needs a value"; PROFILE=$2; shift ;;
            --dry-run) DRY_RUN=1 ;;
            -h|--help) usage; exit 0 ;;
            *) die "unknown argument: $1 (try --help)" ;;
        esac
        shift
    done
    [[ $TIMEOUT =~ ^[0-9]+$ ]] && (( TIMEOUT > 0 )) || die "--timeout must be a positive integer"
}

main() {
    parse_args "$@"
    configure_profile
    if [[ -n $IFACE_ARG && $COMMAND != wait ]] && ! iface_exists "$IFACE_ARG"; then
        die "no such interface: $IFACE_ARG (from --iface or NURA_USB_IFACE)"
    fi
    case $COMMAND in
        detect) cmd_detect ;;
        wait) cmd_wait ;;
        up)
            local iface
            iface=$(pick_iface "$IFACE_ARG") || die "no USB network interface found"
            cmd_up "$iface"
            ;;
        status) cmd_status ;;
        down) cmd_down ;;
    esac
}

main "$@"
