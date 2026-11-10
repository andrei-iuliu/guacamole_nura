#!/bin/sh
# Run ON THE PHONE. Read-only; no streams, actuator writes, probing or module loads.
printf '\nKernel\n'; uname -a
printf '\nPackages\n'; apk info -v 2>&1 | grep -Ei 'libcamera|pipewire-spa-libcamera'
printf '\nAudio\n'; cat /proc/asound/cards
printf '\nModules\n'; lsmod | grep -Ei 'camcc|camss|cci|imx586|imx481|s5k3m5|ak737|lc898|rpmh'
printf '\nKernel messages (may require user-run privileged collection)\n'
dmesg 2>&1 | grep -Ei 'camcc|camss|cci|imx586|imx481|s5k3m5|ak737|lc898|rpmhpd|camera|under.?voltage|remoteproc' | tail -200
printf '\nPower domains (may be unreadable)\n'
cat /sys/kernel/debug/pm_genpd/pm_genpd_summary 2>&1
printf '\nMedia topology\n'
if command -v media-ctl >/dev/null 2>&1; then
    for media in /dev/media*; do [ -e "$media" ] && media-ctl -d "$media" -p; done
fi
