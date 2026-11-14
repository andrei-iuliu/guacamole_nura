#!/bin/sh
# Verify the two sensor recipes (flows/rc4/device/testing/{iio-sensor-proxy,
# libssc}) without building a package:
#
#   1. download the upstream tarballs the recipes name,
#   2. check every sha512sum of the recipe (tarball + local patch) against
#      the file it names, in source= order,
#   3. apply each patch to the unpacked tarball with zero fuzz (-F0),
#   4. compile every touched source file against the host's glib/gio/gudev/
#      polkit/libqmi headers plus the (unpatched) libssc headers.
#
# Needs: curl, tar, patch, cc, pkg-config and the -dev packages for
# glib/gio/gudev/polkit/libqmi/protobuf-c.  Network access for step 1.
# Does not need a phone, root, pmbootstrap or a chroot.
#
# Usage: flows/rc4/topics/sensors/checks/verify.sh [workdir]
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
work=${1:-$(mktemp -d)}
mkdir -p "$work"
echo "work: $work"

sums_of() {
	# the sha512sums="..." block of an APKBUILD, without the delimiters
	sed -n '/^sha512sums="/,/^"$/p' "$1" | sed '1d;$d'
}

# --- iio-sensor-proxy -----------------------------------------------------
recipe="$here/../../device/testing/iio-sensor-proxy"
dir="$work/iio-sensor-proxy"
src="$dir/iio-sensor-proxy-3.9"
rm -rf "$dir"
mkdir -p "$dir"
cp "$recipe"/APKBUILD "$recipe"/*.patch "$dir/"

echo "== iio-sensor-proxy: source and sums"
curl -fsSL -o "$dir/iio-sensor-proxy-3.9.tar.gz" \
	"https://gitlab.freedesktop.org/hadess/iio-sensor-proxy/-/archive/3.9/iio-sensor-proxy-3.9.tar.gz"
sums_of "$dir/APKBUILD" > "$dir/sums"
(cd "$dir" && sha512sum -c sums)
tar -xzf "$dir/iio-sensor-proxy-3.9.tar.gz" -C "$dir"

echo "== iio-sensor-proxy: apply with -F0 (zero fuzz)"
(cd "$src" && patch -p1 -F0 < "$dir/0001-drv-ssc-enable-and-disable-sensors-asynchronously.patch")

# --- libssc ---------------------------------------------------------------
recipe="$here/../../device/testing/libssc"
dir="$work/libssc"
ssc_src="$dir/libssc"
rm -rf "$dir"
mkdir -p "$dir"
cp "$recipe"/APKBUILD "$recipe"/*.patch "$dir/"

echo "== libssc: source and sums"
curl -fsSL -o "$dir/libssc-0.4.4-1.tar.gz" \
	"https://codeberg.org/DylanVanAssche/libssc/archive/v0.4.4.tar.gz"
sums_of "$dir/APKBUILD" > "$dir/sums"
(cd "$dir" && sha512sum -c sums)
tar -xzf "$dir/libssc-0.4.4-1.tar.gz" -C "$dir"

echo "== libssc: apply with -F0 (zero fuzz)"
(cd "$ssc_src" && patch -p1 -F0 < "$dir/0001-libssc-common-block-in-the-sync-wait.patch")

# --- compile checks -------------------------------------------------------
# The libssc headers only need glib/gio, so the unpatched libssc tree is
# enough for the driver files; libssc-common.c needs libqmi-glib.h too.
echo "== compile: patched iio-sensor-proxy SSC drivers"
cflags=$(pkg-config --cflags gio-2.0 gobject-2.0 glib-2.0 gudev-1.0 polkit-gobject-1)
for f in drv-ssc-light drv-ssc-proximity drv-ssc-accel drv-ssc-compass; do
	printf '   %-22s ' "$f"
	cc -fsyntax-only -Wall -Wextra -Wno-unused-parameter \
		-I"$work/iio-sensor-proxy/iio-sensor-proxy-3.9/src" \
		-I"$ssc_src/src" $cflags \
		"$work/iio-sensor-proxy/iio-sensor-proxy-3.9/src/$f.c"
	echo OK
done

echo "== compile: patched libssc-common.c"
printf '   %-22s ' libssc-common
cc -fsyntax-only -Wall -Wextra -Wno-unused-parameter \
	-I"$ssc_src/src" -I"$ssc_src" \
	$(pkg-config --cflags glib-2.0 gio-2.0 qmi-glib) \
	"$ssc_src/src/libssc-common.c"
echo OK

echo "all checks passed"
