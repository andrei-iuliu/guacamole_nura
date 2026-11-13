#!/usr/bin/env bash
# Rootless regression test for fragments/device/guacamole-mss-release.
# Uses a fake remoteproc class, a fake proxy runtime_status and a fake module
# parameter file, so it needs no kernel, no root and no phone.
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/.." && pwd)
script=$root/fragments/device/guacamole-mss-release

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

class=$work/class
param=$work/level
proxy=$work/runtime_status
mkdir -p "$class"

fake_modem() {
	local name=$1 state=$2
	mkdir -p "$class/$name"
	printf 'modem\n' >"$class/$name/name"
	printf '%s\n' "$state" >"$class/$name/state"
}

# Extra "VAR=value" arguments override the defaults, so a test can select a
# failing modprobe, a different level, a missing proxy status or a forced
# handover override.
run() {
	env GUACAMOLE_REMOTEPROC_CLASS="$class" \
		GUACAMOLE_MSS_PARAM="$param" \
		GUACAMOLE_MSS_PROXY_STATUS="$proxy" \
		GUACAMOLE_MSS_MODPROBE=/bin/true \
		MSS_POLL_INTERVAL=0 \
		"$@" "$script"
}

pass=0
fail=0
check() {
	if "$@"; then
		pass=$((pass + 1))
	else
		fail=$((fail + 1))
		echo "FAILED: $*" >&2
	fi
}
expect_ok() { run "$@" >/dev/null 2>&1; }
expect_fail() { ! run "$@" >/dev/null 2>&1; }

reset() {
	printf '%s\n' -1 >"$param"
	fake_modem remoteproc0 running
}

# 1. modem running, proxy handover observed: writes level 0 and exits 0.
reset
printf 'suspended\n' >"$proxy"
check expect_ok
check grep -qx 0 "$param"

# 2. no modem at all: fails and leaves the parameter alone.
rm -rf "${class:?}"/*
printf '%s\n' -1 >"$param"
check expect_fail
check grep -qx -- -1 "$param"

# 3. modem present but never running, zero timeout: fails.
fake_modem remoteproc0 offline
check expect_fail MSS_WAIT_TIMEOUT=0

# 4. two modems: refuses to guess.
fake_modem remoteproc1 offline
check expect_fail MSS_WAIT_TIMEOUT=0

# 5. module load failure: fails before writing.
rm -rf "${class:?}"/*
reset
printf 'suspended\n' >"$proxy"
check expect_fail GUACAMOLE_MSS_MODPROBE=/bin/false
check grep -qx -- -1 "$param"

# 6. a non-default but in-range corner is passed through and read back.
reset
printf 'suspended\n' >"$proxy"
check expect_ok MSS_LEVEL=3
check grep -qx 3 "$param"

# 7. proxy still runtime-active (handover not seen): refuses to write.
reset
printf 'active\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=0
check grep -qx -- -1 "$param"

# 8. proxy status missing/deleted: refuses to write.
reset
check expect_fail GUACAMOLE_MSS_PROXY_STATUS="$work/does-not-exist"
check grep -qx -- -1 "$param"

# 9. same, with the explicit override: writes.
reset
check expect_ok GUACAMOLE_MSS_PROXY_STATUS="$work/does-not-exist" \
	MSS_ALLOW_UNVERIFIED_HANDOVER=1
check grep -qx 0 "$param"

# 10. active proxy with the override: writes anyway (documented escape hatch).
reset
printf 'active\n' >"$proxy"
check expect_ok MSS_HANDOVER_TIMEOUT=0 MSS_ALLOW_UNVERIFIED_HANDOVER=1
check grep -qx 0 "$param"

# 11. empty status file: refuses (it is not "suspended").
reset
printf '' >"$work/empty"
check expect_fail GUACAMOLE_MSS_PROXY_STATUS="$work/empty" MSS_HANDOVER_TIMEOUT=0
check grep -qx -- -1 "$param"

# 12. unreadable status (a directory cannot be read as a value): refuses.
reset
mkdir -p "$work/proxy-dir"
check expect_fail GUACAMOLE_MSS_PROXY_STATUS="$work/proxy-dir" MSS_HANDOVER_TIMEOUT=0
check grep -qx -- -1 "$param"

# 13. unreadable status (mode 000), when the test user cannot read it.
reset
printf 'suspended\n' >"$work/unreadable"
chmod 000 "$work/unreadable"
if [ -r "$work/unreadable" ]; then
	echo "SKIP: mode-000 status is readable (running as root?)" >&2
else
	check expect_fail GUACAMOLE_MSS_PROXY_STATUS="$work/unreadable" MSS_HANDOVER_TIMEOUT=0
	check grep -qx -- -1 "$param"
fi
chmod 600 "$work/unreadable"

# 14. "suspending" is transitional, not a handover: refuses.
reset
printf 'suspending\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=0
check grep -qx -- -1 "$param"

# 15. "suspending" that never settles still refuses after the wait.
reset
printf 'suspending\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=1 MSS_POLL_INTERVAL=0.1
check grep -qx -- -1 "$param"

# 16. "resuming" means something took the proxy device back: refuses.
reset
printf 'resuming\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=0
check grep -qx -- -1 "$param"

# 17. unsupported/unexpected values: refuse.
reset
printf 'unsupported\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=0
printf 'frobnicated\n' >"$proxy"
check expect_fail MSS_HANDOVER_TIMEOUT=0

# 18. the modem stops while the script waits for the handover: refuses
#     before the write.
reset
printf 'active\n' >"$proxy"
( sleep 0.4; printf 'offline\n' >"$class/remoteproc0/state" ) &
check expect_fail MSS_HANDOVER_TIMEOUT=20 MSS_POLL_INTERVAL=0.2
wait 2>/dev/null || true
check grep -qx -- -1 "$param"

# 19. the modem stops during modprobe (after the handover, immediately before
#     the write): refuses and leaves the parameter alone.
printf '#!/bin/sh\nprintf "offline\\n" > "%s/remoteproc0/state"\n' "$class" >"$work/flip-modem"
chmod +x "$work/flip-modem"
reset
printf 'suspended\n' >"$proxy"
check expect_fail GUACAMOLE_MSS_MODPROBE="$work/flip-modem"
check grep -qx -- -1 "$param"

# 20. the unverified-handover override does not bypass the modem check.
reset
printf 'suspended\n' >"$proxy"
check expect_fail GUACAMOLE_MSS_MODPROBE="$work/flip-modem" MSS_ALLOW_UNVERIFIED_HANDOVER=1
check grep -qx -- -1 "$param"

echo "guacamole-mss-release tests: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
