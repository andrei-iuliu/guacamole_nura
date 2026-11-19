#!/usr/bin/env bash
# User-run: configure USB networking and serve the existing rootfs on port 10000.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shortcut: defaults to the current USB port's name; override after changing ports.
INTERFACE="${NURA_USB_IFACE:-enp19s0u4u4}"
echo "Waiting for USB network interface $INTERFACE. Run boot_7_0.sh in another terminal."
until ip link show dev "$INTERFACE" >/dev/null 2>&1; do sleep 1; done
sudo ip link set dev "$INTERFACE" up
if ! ip -o -4 address show dev "$INTERFACE" | grep -q ' inet 172.16.42.2/24 '; then
    sudo ip address add 172.16.42.2/24 dev "$INTERFACE"
fi
exec python3 "$ROOT/.work/7.0/pmbootstrap/pmbootstrap.py" \
    -c "$ROOT/.work/7.0/rootfs/pmbootstrap.cfg" \
    -p "$ROOT/.work/7.0/pmaports" \
    -w "$ROOT/.work/7.0/pmbootstrap-work" \
    netboot serve --port 10000
