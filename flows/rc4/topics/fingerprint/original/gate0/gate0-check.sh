#!/bin/sh
# Gate 0: does the mainline Qualcomm TEE driver find QTEE on this phone?
# Run as root on the phone, booted on the 7.0 kernel with config.fragment.
# Read-only: it loads one module and asks QTEE for its diagnostics.
set -u

modprobe qcomtee 2>/dev/null || true
sleep 1

echo "== dmesg"
dmesg | grep -i -E 'qcomtee|qtee|tzmem|shm.?bridge|smcinvoke' || echo "(nothing)"

echo "== devices"
ls -l /dev/tee* /dev/teepriv* 2>/dev/null || echo "no /dev/tee*"
ls -d /sys/bus/platform/devices/qcomtee* 2>/dev/null || echo "no qcomtee platform device"

# smcinvoke_client comes from qualcomm/minkipc (libminkadaptor), see README.
if command -v smcinvoke_client >/dev/null; then
	echo "== smcinvoke_client -d 1"
	smcinvoke_client -d 1
	echo "exit $?"
else
	echo "smcinvoke_client not installed: build qualcomm/minkipc for the next check"
fi

cat <<'TXT'

PASS  : dmesg has "QTEE version x.y.z", /dev/tee0 exists, smcinvoke_client -d succeeds.
FAIL A: no qcomtee platform device. qcom_scm's smcinvoke probe got -EIO: this
        TrustZone does not accept the call from mainline. Route B (port
        downstream qseecom) or stop.
FAIL B: device exists but the version query or diagnostics fail. Read the
        errors; likely SHM bridge or memory-object handling on this firmware.
TXT
