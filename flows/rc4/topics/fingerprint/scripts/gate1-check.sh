#!/bin/sh
# Gate 1 passive check: is the goodix_fp groundwork present and inert?
#
# This script NEVER powers or resets the sensor.  It only reads the device
# model, driver binding, /proc nodes, sysfs attributes and interrupt count.
# Running it does not satisfy gate 1: the reset-time interrupt test needs an
# explicit opt-in load with goodix_fp.allow_power=1 and a caller that issues
# GF_IOC_ENABLE_POWER then GF_IOC_RESET.  Do that only after gate 0 passes.
set -u

fail=0

echo "== device tree node"
if [ -r /proc/device-tree/fingerprint/status ]; then
	# status is a NUL-terminated string property
	status=$(tr -d '\000' < /proc/device-tree/fingerprint/status)
	echo "fingerprint status=$status"
	[ "$status" = "okay" ] || echo "node is disabled (expected for rc4 gate 0)"
else
	echo "no /proc/device-tree/fingerprint/status"
	fail=1
fi

echo "== driver binding"
if [ -d /sys/bus/platform/drivers/goodix_fp ]; then
	echo "goodix_fp driver registered"
	ls -l /sys/bus/platform/drivers/goodix_fp/ 2>/dev/null | sed -n '1,12p'
else
	echo "goodix_fp driver not registered"
	fail=1
fi

if [ -c /dev/goodix_fp ]; then
	echo "/dev/goodix_fp present"
	ls -l /dev/goodix_fp
else
	echo "/dev/goodix_fp missing (expected while the DT node is disabled)"
fi

echo "== /proc nodes"
for node in fp_id lcd_type fp_kernel_event tee_bind_core; do
	if [ -e "/proc/$node" ]; then
		ls -l "/proc/$node"
	else
		echo "/proc/$node missing"
	fi
done

if [ -r /proc/fp_id ]; then
	printf 'fp_id: '
	cat /proc/fp_id
	printf '\n'
fi
if [ -r /proc/lcd_type ]; then
	printf 'lcd_type: '
	cat /proc/lcd_type
	printf '\n'
fi

echo "== module parameter"
if [ -r /sys/module/goodix_fp/parameters/allow_power ]; then
	cat /sys/module/goodix_fp/parameters/allow_power
else
	echo "allow_power parameter not readable (module not loaded?)"
fi

echo "== sysfs event attributes"
found=0
for dir in /sys/devices/platform/fingerprint /sys/bus/platform/devices/fingerprint; do
	if [ -d "$dir" ]; then
		found=1
		for attr in touch ui_ready screen; do
			if [ -e "$dir/$attr" ]; then
				echo "$dir/$attr"
			else
				echo "$dir/$attr missing"
				fail=1
			fi
		done
	fi
done
[ "$found" -eq 1 ] || echo "no platform device named fingerprint (DT disabled?)"

echo "== interrupts"
if grep -q goodix_fp /proc/interrupts 2>/dev/null; then
	grep goodix_fp /proc/interrupts
else
	echo "no goodix_fp line in /proc/interrupts"
fi

echo
if [ "$fail" -eq 0 ]; then
	echo "OK: gate-1 groundwork is present.  This is NOT authentication and"
	echo "does not prove the sensor answers; the reset-time IRQ test is separate."
else
	echo "INCOMPLETE: see missing items above.  A missing /dev/goodix_fp while"
	echo "the DT node is disabled is expected, not a regression."
fi
