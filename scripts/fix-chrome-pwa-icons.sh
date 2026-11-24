#!/bin/sh
# Repair Chrome (Flatpak) web-app shortcuts whose Icon= points into the
# sandbox. Flathub's Chrome wrapper writes the shortcut and copies the icon
# PNGs into ~/.local/share/icons, but records the icon as an absolute path
# under ~/.var/app/com.google.Chrome/data, which the host cannot resolve, so
# the launcher shows a blank icon. Replace the path with the icon name so the
# theme lookup finds the copied PNGs. Run as the desktop user; the matching
# systemd user path unit (scripts/fix-chrome-pwa-icons.path) runs it whenever
# ~/.local/share/applications changes.
apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
for f in "$apps"/com.google.Chrome.*.desktop "$apps"/chrome-*.desktop; do
	[ -f "$f" ] || continue
	grep -q '^Icon=/.*\.var/app/.*/\(chrome-[A-Za-z0-9_-]*\)\.png$' "$f" || continue
	sed -i 's#^Icon=/.*/\(chrome-[A-Za-z0-9_-]*\)\.png$#Icon=\1#' "$f"
	echo "fixed $(basename "$f")"
done
exit 0
