#!/usr/bin/env bash
# Run every rootless check for the suspend-fixes topic.
set -uo pipefail

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
status=0
"$here/patch_apply_check.sh" || status=1
"$here/check_fragments.sh" || status=1
"$here/test_guacamole_mss_release.sh" || status=1
exit "$status"
