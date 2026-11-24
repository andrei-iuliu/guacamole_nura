#!/bin/sh
# Rasterize app icons that QtSvg cannot render for Plasma Mobile.
#
# Current GNOME app icons ship as SVG only and use <filter> and <mask>, which
# Qt's SVG renderer does not implement, so Plasma draws them with black
# backgrounds while Phosh (librsvg) renders them fine. KDE's icon loader
# prefers a fixed-size PNG over the scalable SVG, so render PNGs with librsvg
# into the user's icon directory. Run on the phone as the desktop user, and
# again after installing new apps; restart plasmashell afterwards:
#   systemctl --user restart plasma-plasmashell.service
# Needs the rsvg-convert package.
set -e
out="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
n=0
for svg in /usr/share/icons/hicolor/scalable/apps/*.svg \
	   /var/lib/flatpak/exports/share/icons/hicolor/scalable/apps/*.svg \
	   "$HOME"/.local/share/flatpak/exports/share/icons/hicolor/scalable/apps/*.svg; do
	[ -f "$svg" ] || continue
	grep -qE "<filter|<mask" "$svg" || continue
	name=$(basename "$svg" .svg)
	for size in 48 64 96 128 192 256; do
		mkdir -p "$out/${size}x${size}/apps"
		rsvg-convert -w "$size" -h "$size" -o "$out/${size}x${size}/apps/$name.png" "$svg"
	done
	n=$((n + 1))
done
echo "rasterized $n icons into $out"
