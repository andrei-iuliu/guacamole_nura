#!/usr/bin/env bash
# Static checks for the suspend-fixes fragments: shell syntax, service unit
# keys, Kconfig symbols and their rc3 config dependencies, the opt-in policy
# and JSON validity.
set -euo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH= cd -- "$here/.." && pwd)
repo=$(CDPATH= cd -- "$root/../../../.." && pwd)
KERNEL_ROOT=${KERNEL_ROOT:-$root/../../../local-storage/.work/local-userdata/kernel-state/linux-v7.0.0-sm8150}
CONFIG=${CONFIG:-$repo/device/testing/rc3/linux-postmarketos-qcom-sm8150/config-postmarketos-qcom-sm8150.aarch64}

pass=0
fail=0
ok() { pass=$((pass + 1)); echo "PASS $*"; }
bad() { fail=$((fail + 1)); echo "FAIL $*"; }

# Shell syntax.
for f in "$root"/fragments/device/guacamole-mss-release "$root"/checks/*.sh; do
	if sh -n "$f" 2>/dev/null || bash -n "$f" 2>/dev/null; then
		ok "syntax $f"
	else
		bad "syntax $f"
	fi
done

python3 -c 'import sys; compile(open(sys.argv[1]).read(), sys.argv[1], "exec")' \
	"$root/checks/key_state_model.py" && ok "syntax key_state_model.py" || bad "syntax key_state_model.py"

# The release must stay opt-in: no starter hook, and the unit is not enabled
# by the preset fragment.
[[ ! -e "$root/fragments/device/guacamole-mpss-start.patch" ]] &&
	ok "no automatic mpss-start hook" || bad "mpss-start hook is still present"
rel=$root/fragments/device/guacamole-mss-release
grep -q 'handover_seen' "$rel" &&
	ok "release helper gates on the proxy handover" || bad "release helper has no handover gate"
grep -qE '^[[:space:]]*suspended\)$' "$rel" &&
	ok "handover accepts the exact status suspended" || bad "handover does not require exact suspended"
grep -q "unexpected proxy runtime status" "$rel" &&
	ok "handover rejects unknown/empty status" || bad "handover accepts unknown/empty status"
[ "$(grep -c 'modem_running ||' "$rel")" -ge 2 ] &&
	ok "modem is rechecked while waiting and before the write" || bad "modem is not rechecked"

# Config fragment.  QCOM_GUACAMOLE_MSS_VOTE comes from patch 0044; the other
# symbols must already exist in the 7.0 tree.
[[ -d $KERNEL_ROOT/drivers/soc/qcom ]] || { echo "SKIP: no kernel tree at $KERNEL_ROOT" >&2; exit 3; }
grep -q '^CONFIG_QCOM_GUACAMOLE_MSS_VOTE=m$' "$root/fragments/kernel-config.fragment" &&
	grep -q '^+config QCOM_GUACAMOLE_MSS_VOTE$' "$root/patches/0044-soc-qcom-guacamole-mss-vote.patch" &&
	ok "QCOM_GUACAMOLE_MSS_VOTE defined by 0044 and enabled as =m" || bad "QCOM_GUACAMOLE_MSS_VOTE wiring"

for sym in PM_DEBUG PM_SLEEP_DEBUG PM_ADVANCED_DEBUG PM_TEST_SUSPEND DPM_WATCHDOG; do
	grep -rqE "^config $sym\$" "$KERNEL_ROOT"/kernel/power/Kconfig &&
		ok "Kconfig has $sym" || bad "Kconfig is missing $sym"
done

for dep in CONFIG_ARCH_QCOM=y CONFIG_QCOM_RPMH=y CONFIG_QCOM_COMMAND_DB=y CONFIG_PM_GENERIC_DOMAINS=y CONFIG_PM_SLEEP=y CONFIG_SUSPEND=y; do
	grep -qx "$dep" "$CONFIG" && ok "rc3 config has $dep" || bad "rc3 config lacks $dep"
done

# Service unit.
unit=$root/fragments/device/guacamole-mss-release.service
for key in '^\[Unit\]' '^\[Service\]' '^\[Install\]' '^Type=exec$' '^RemainAfterExit=yes$' \
	'^ExecStart=/usr/libexec/guacamole-mss-release$' '^WantedBy=multi-user.target$'; do
	grep -qE "$key" "$unit" && ok "unit $key" || bad "unit lacks $key"
done

# The preset fragment must not enable anything.
grep -qE '^enable ' "$root/fragments/device/80-guacamole.preset.fragment" &&
	bad "preset fragment enables a unit" || ok "preset fragment enables nothing"

# The udev rule keeps its single match line.
grep -qx 'ACTION=="add", SUBSYSTEM=="remoteproc", ATTR{name}=="modem", ATTR{recovery}="disabled"' \
	"$root/fragments/device/90-guacamole-modem-recovery.rules" &&
	ok "modem-recovery udev rule" || bad "modem-recovery udev rule"

# Inventory must be valid JSON.
python3 -m json.tool "$root/inventory.json" >/dev/null && ok "inventory.json parses" || bad "inventory.json"

echo "fragment checks: $pass passed, $fail failed"
[[ $fail -eq 0 ]]
