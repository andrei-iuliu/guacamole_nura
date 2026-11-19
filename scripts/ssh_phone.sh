#!/usr/bin/env bash
# User-run: Plasma USB SSH, with host keys isolated from Android and Phosh.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
HOST=172.16.42.1
KNOWN_HOSTS=$ROOT/.work/delivery-plasma-mobile/known_hosts
HOST_ALIAS=$HOST
# Fresh NBD copies generate fresh host keys; retain a separate pin per boot.
# This is bookkeeping, not identity proof: first contact still asks the user.
if [[ -f $ROOT/build/plasma-mobile/last-boot.json ]]; then
    HOST_ALIAS=$(python3 - "$ROOT" <<'PY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
receipt = json.loads((root / 'build/plasma-mobile/last-boot.json').read_text())
if receipt.get('result') != 'success' or receipt.get('worktree') != str(root):
    sys.exit('SSH: last-boot receipt does not describe this successful Plasma boot')
stamp, serial = receipt.get('finished_at'), receipt.get('device', {}).get('serial')
if not isinstance(stamp, str) or not stamp or not isinstance(serial, str) or not serial:
    sys.exit('SSH: last-boot receipt has no boot time or phone serial')
token = hashlib.sha256((stamp + '\0' + serial).encode()).hexdigest()[:16]
print('nura-plasma-' + token)
PY
    ) || exit 1
fi

case ${1:-} in
    '') ;;
    --forget-host-key)
        [[ $# == 1 ]] || { echo 'Unexpected extra arguments' >&2; exit 2; }
        if [[ -f $KNOWN_HOSTS ]]; then
            ssh-keygen -R "$HOST_ALIAS" -f "$KNOWN_HOSTS"
        fi
        echo 'Cached Plasma host key reset; verify the new fingerprint before accepting.' >&2
        ;;
    -h|--help)
        echo 'Usage: bash scripts/ssh_phone.sh [--forget-host-key]'
        echo 'Reset only after an intentional rebuild; changed keys are never removed automatically.'
        exit 0
        ;;
    *) echo 'Usage: bash scripts/ssh_phone.sh [--forget-host-key]' >&2; exit 2 ;;
esac
mkdir -p -- "$(dirname -- "$KNOWN_HOSTS")"
exec ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=ask \
    -o "UserKnownHostsFile=$KNOWN_HOSTS" -o "HostKeyAlias=$HOST_ALIAS" "user@$HOST"
