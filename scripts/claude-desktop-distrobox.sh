#!/bin/sh
# Run Anthropic's official Claude Desktop (arm64 .deb, glibc) on this Alpine
# phone inside a Debian distrobox, and export its launcher to the host menu.
#
# Why a container: the .deb bundles Electron and a closed-source Rust N-API
# module (claude-native) built for glibc, which Alpine's musl Electron cannot
# load, and the app refuses some file operations without it. A glibc
# container keeps Anthropic's build untouched; distrobox shares $HOME, the
# Wayland socket and the session bus with the host.
#
# Notes learned on 2026-11-24:
# - distrobox defaults to rootless podman, which fails on Alpine (newuidmap
#   has no setuid capability); Docker is active with the user in its group,
#   so distrobox is told to use Docker.
# - Electron's setuid sandbox does not work in a Docker container:
#   --no-sandbox.
# - The window's minimum width is wider than the 480-logical-pixel screen at
#   the panel's 3x scale, and kwin cannot shrink it below the client's hint.
#   On Wayland, --force-device-scale-factor renders at the forced scale but
#   reports buffer scale 1, so kwin blows the window up (tried 2 with and
#   without WaylandPerSurfaceScale: "giant"). Under Xwayland the forced
#   factor works: kwin is set to let X11 clients scale themselves
#   ([Xwayland] Scale=1 in kwinrc, compositor-wide) and Claude runs with
#   --ozone-platform=x11 --force-device-scale-factor=2.5, which fits the
#   screen and reads well. The session's XAUTHORITY is forwarded by distrobox.
# - Sign-in opens a browser through xdg-open; the container has none, so a
#   wrapper hands the URL to the host's xdg-open.
# Usage: run on the phone as the desktop user, with network access:
#   scripts/claude-desktop-distrobox.sh [version]
set -e

REPO=https://downloads.claude.ai/claude-desktop/apt/stable
NAME=claude
IMAGE=debian:stable
want="$1"

mkdir -p ~/.config/distrobox ~/Downloads
grep -q '^container_manager=' ~/.config/distrobox/distrobox.conf 2>/dev/null ||
	echo 'container_manager="docker"' >> ~/.config/distrobox/distrobox.conf

# Pick the package from Anthropic's index (the index is what the Fedora
# helper verifies against Anthropic's key; this script checks the sha256 the
# index lists for the .deb).
work=$(mktemp -d)
curl -fsSL -o "$work/Packages" "$REPO/dists/stable/main/binary-arm64/Packages"
entry=$(awk '/^Version:/{v=$2} /^Filename:/{f=$2} /^SHA256:/{h=$2} /^$/{if(v)print v, f, h; v=""}' "$work/Packages" | sort -V | tail -1)
[ -n "$want" ] && entry=$(awk '/^Version:/{v=$2} /^Filename:/{f=$2} /^SHA256:/{h=$2} /^$/{if(v)print v, f, h; v=""}' "$work/Packages" | awk -v w="$want" '$1==w')
[ -n "$entry" ] || { echo "no package found"; exit 1; }
version=$(echo "$entry" | cut -d' ' -f1)
file=$(echo "$entry" | cut -d' ' -f2)
sha=$(echo "$entry" | cut -d' ' -f3)
deb=~/Downloads/claude-desktop_${version}_arm64.deb
if [ ! -f "$deb" ] || [ "$(sha256sum "$deb" | cut -d' ' -f1)" != "$sha" ]; then
	echo "downloading Claude Desktop $version"
	curl -fL --progress-bar -o "$deb" "$REPO/$file"
fi
echo "$sha  $deb" | sha256sum -c -
rm -rf "$work"

distrobox list 2>/dev/null | grep -q " $NAME " || distrobox create --yes --name "$NAME" --image "$IMAGE"
distrobox enter "$NAME" -- sh -c "sudo apt-get update -qq && sudo apt-get install -y -qq ca-certificates >/dev/null"
distrobox enter "$NAME" -- sh -c "sudo apt-get install -y -qq '$deb' >/dev/null && dpkg -s claude-desktop | grep ^Version"
distrobox enter "$NAME" -- sh -c 'printf "#!/bin/sh\nexec distrobox-host-exec xdg-open \"\$@\"\n" | sudo tee /usr/bin/xdg-open >/dev/null && sudo chmod 755 /usr/bin/xdg-open'

distrobox enter "$NAME" -- distrobox-export --app claude-desktop --delete >/dev/null 2>&1 || true
distrobox enter "$NAME" -- distrobox-export --app claude-desktop --extra-flags "--no-sandbox --ozone-platform=x11 --force-device-scale-factor=2.5"
# distrobox-export adds the flags to the main Exec only; the desktop actions need them too,
# and the "(on claude)" suffix is noise on a phone menu.
entry=~/.local/share/applications/claude-com.anthropic.Claude.desktop
sed -i -e 's#--   claude-desktop "claude://#--   claude-desktop --no-sandbox --ozone-platform=x11 --force-device-scale-factor=2.5 "claude://#' \
	-e 's/ (on claude)//' "$entry"
# distrobox create also adds a terminal entry for the container itself; one Claude icon is enough.
distrobox generate-entry "$NAME" --delete >/dev/null 2>&1 || true
rm -f ~/.local/share/applications/claude.desktop

# kwin: X11 clients scale themselves (see the header), and a rule keeps Claude maximized.
kwriteconfig6 --file kwinrc --group Xwayland --key Scale 1
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
for kv in "Description=Claude Desktop fits the phone screen" wmclass=com.anthropic.Claude wmclassmatch=1 \
	  wmclasscomplete=false strictgeometry=false strictgeometryrule=2 \
	  maximizehoriz=true maximizehorizrule=2 maximizevert=true maximizevertrule=2; do
	kwriteconfig6 --file kwinrulesrc --group claude-fit --key "${kv%%=*}" "${kv#*=}"
done
rules=$(kreadconfig6 --file kwinrulesrc --group General --key rules)
case ",$rules," in *,claude-fit,*) ;; *) kwriteconfig6 --file kwinrulesrc --group General --key rules "${rules:+$rules,}claude-fit";; esac
kwriteconfig6 --file kwinrulesrc --group General --key count "$(kreadconfig6 --file kwinrulesrc --group General --key rules | tr ',' '\n' | grep -c .)"
busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
echo "Claude Desktop $version exported as 'Claude'. Update: re-run this script."
