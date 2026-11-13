#!/bin/sh
# Gate 0: does the mainline Qualcomm TEE driver find QTEE on this phone?
#
# Rebased from Patches/fingerprint/gate0/gate0-check.sh for rc4.
#
# SAFETY: the default mode is passive and read-only.  It does not load a
# module and does not issue a secure-world call.  The two actions that can
# touch QTEE are explicit opt-ins:
#
#   --load         modprobe qcomtee (loads the driver and probes QTEE)
#   --diagnostics  run smcinvoke_client -d 1 (QCOMTEE diagnostics query)
#
# The passive check also works on the current rc3 boot: qcom_scm.c calls
# qcom_scm_qtee_init() at every boot and registers the qcomtee platform
# device whenever the smcinvoke probe does not return -EIO.  QCOMTEE only
# adds the driver that binds to that device.  So "no qcomtee device" is
# already a gate-0 failure signal without loading anything.
#
# QCOMTEE=m is not passive by itself: qcomtee has a platform modalias
# (platform:qcomtee), so udev/systemd may autoload it when qcom_scm registers
# the platform device.  This script does not trigger that; check the module
# state and use module_blacklist=qcomtee if the whole boot must stay passive.
#
# Run on the phone as root, booted on a kernel with config/gate0.fragment.
# Review the kernel cmdline and gate0.fragment before using --load.
set -u

load=0
diagnostics=0

usage() {
	cat <<'EOF'
usage: gate0-check.sh [--load] [--diagnostics]

  (no option)     passive checks only: dmesg, /dev/tee*, qcomtee device
  --load          also run: modprobe qcomtee
  --diagnostics   also run: smcinvoke_client -d 1 (secure-world query)

The passive mode is safe to run at any time.  --load and --diagnostics are
the actual gate-0 probe; run them deliberately, not from boot automation.
EOF
}

while [ $# -gt 0 ]; do
	case "$1" in
	--load)
		load=1
		;;
	--diagnostics)
		diagnostics=1
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		echo "unknown option: $1" >&2
		usage >&2
		exit 2
		;;
	esac
	shift
done

if [ "$load" -eq 1 ]; then
	echo "== modprobe qcomtee"
	modprobe qcomtee || echo "modprobe qcomtee failed: $?"
	sleep 1
fi

echo "== dmesg"
dmesg | grep -i -E 'qcomtee|qtee|tzmem|shm.?bridge|smcinvoke' ||
	echo "(nothing matching qcomtee/qtee/tzmem/shm-bridge/smcinvoke)"

echo "== devices"
ls -l /dev/tee* /dev/teepriv* 2>/dev/null || echo "no /dev/tee*"
ls -d /sys/bus/platform/devices/qcomtee* 2>/dev/null ||
	echo "no qcomtee platform device"

echo "== note"
echo "qcom_scm probes QTEE at every boot; QCOMTEE only binds the driver."

echo "== module state"
if [ -d /sys/module/qcomtee ]; then
	echo "qcomtee loaded"
else
	echo "qcomtee not loaded (passive mode does not load it)"
fi

if [ "$diagnostics" -eq 1 ]; then
	if command -v smcinvoke_client >/dev/null 2>&1; then
		echo "== smcinvoke_client -d 1 (explicit secure-world query)"
		smcinvoke_client -d 1
		echo "exit $?"
	else
		echo "smcinvoke_client not installed"
		echo "build qualcomm/minkipc (libminkadaptor) before the next check"
	fi
else
	echo "== diagnostics skipped (passive mode)"
	echo "run with --diagnostics only after reviewing the gate-0 plan"
fi

cat <<'TXT'

PASS  : dmesg has "QTEE version x.y.z", /dev/tee0 exists, and (with
        --diagnostics) smcinvoke_client -d succeeds.
FAIL A: no qcomtee platform device. qcom_scm's smcinvoke probe got -EIO:
        this TrustZone does not accept the call from mainline.  Route B
        (port downstream qseecom) or stop.
FAIL B: device exists but the version query or diagnostics fail.  Read the
        errors; likely SHM-bridge or memory-object handling on this firmware.
NOTE  : this script only reports gate 0.  It does not power the fingerprint
        sensor, touch persist/RPMB, or start any HAL.
TXT
