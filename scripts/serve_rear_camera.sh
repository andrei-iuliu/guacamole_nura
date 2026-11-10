#!/usr/bin/env bash
# USER RUN ONLY: refresh the stopped candidate's cached image, then serve port 10001.
# The working port-10000 server can stay running.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.work/rear-camera"
test -f "$ROOT/build/rear-camera/boot.img"
echo "Run only after stopping the old candidate server and leaving the candidate OS."
echo "Confirm pmbootstrap's rootfs replacement prompt to serve the newly packaged image."
# shortcut: defaults to the current USB port's name; override after changing ports.
INTERFACE="${NURA_USB_IFACE:-enp19s0u4u4}"
echo "Waiting for USB network interface $INTERFACE. Boot the candidate in another terminal."
until ip link show dev "$INTERFACE" >/dev/null 2>&1; do sleep 1; done
sudo ip link set dev "$INTERFACE" up
if ! ip -o -4 address show dev "$INTERFACE" | grep -q ' inet 172.16.42.2/24 '; then
    sudo ip address add 172.16.42.2/24 dev "$INTERFACE"
fi
exec python3 "$STATE/pmbootstrap/pmbootstrap.py" \
    -c "$STATE/rootfs/pmbootstrap.cfg" -p "$STATE/pmaports" -w "$STATE/pmbootstrap-work" \
    netboot serve --port 10001 --replace
