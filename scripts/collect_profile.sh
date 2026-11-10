#!/usr/bin/env bash
# collect_profile.sh - assemble a read-only Nura profiling bundle on the HOST.
#
# USER-RUN.  This script does not contact the phone unless --ssh is explicitly
# given.  It never uses sudo, never starts/stops a server or recording, never
# flashes/reboots, and never writes outside --out.
#
#   # Preferred: collect the snapshot on the phone, then copy it here.
#   sh nura-probe.sh > /tmp/nura-snapshot.txt
#   bash collect_profile.sh --snapshot /tmp/nura-snapshot.txt \
#       --recordings ~/nura-clips --manifest /path/to/DELIVERY.json
#
#   # Optional read-only SSH: exactly one BatchMode attempt, no retry.
#   bash collect_profile.sh --ssh user@172.16.42.1 --recordings ~/nura-clips
#
# The SSH form deliberately leaves host-key verification at the user's normal
# SSH configuration.  It does not inspect or change keys.  If the single
# attempt fails (the previous password-only phone did), use --snapshot.
set -Eeuo pipefail

SELF_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROBE="$SELF_DIR/nura-probe.sh"
ANALYZE="$SELF_DIR/analyze_profile.py"
DEFAULT_OUT="$SELF_DIR/../collections/$(date -u +%Y%m%dT%H%M%SZ)"

SNAPSHOT=""
SSH_TARGET=""
OUT="$DEFAULT_OUT"
RECORDINGS=""
MANIFEST=""
DMESG=""
JOURNAL=""
SYMPTOM=""
FFPROBE="ffprobe"

usage() {
    cat <<'EOF'
Usage: bash collect_profile.sh (--snapshot FILE | --ssh USER@HOST) [options]

Build a read-only profiling bundle: snapshot + optional recording metadata +
analysis.  This script is user-run and never touches the phone unless --ssh is
given.  It never uses sudo, never starts/stops a recording or server, and
writes only under --out.

Input (exactly one):
  --snapshot FILE     nura-probe.sh output collected on the phone
  --ssh USER@HOST     one BatchMode SSH attempt to run nura-probe.sh; no retry

Options:
  --out DIR           output directory (default: ../collections/<UTC>)
  --recordings DIR    directory of user-recorded short clips (read in place;
                      videos are not copied into the bundle)
  --manifest FILE     build manifest for this boot (for example DELIVERY.json)
  --dmesg FILE        optional user-run `sudo dmesg` text capture
  --journal FILE      optional user-collected journal text capture
  --symptom NAME      preview|recording|playback|both|mixed|unknown
  --ffprobe PATH      ffprobe binary (default: ffprobe)
  -h, --help          show this help

Outputs: snapshot.txt, analysis.json, report.md, videos.tsv, SHA256SUMS,
plus ssh.rc/ssh.stderr for the optional SSH form.  Missing tools/data are
reported as UNKNOWN/MISSING/PARTIAL; no PASS verdict is produced.
EOF
}

die() {
    printf 'collect_profile: %s\n' "$*" >&2
    exit 2
}

need_value() {
    [[ $# -ge 2 && -n "$2" ]] || die "$1 needs a value"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --snapshot) need_value "$@"; SNAPSHOT=$2; shift 2 ;;
        --ssh) need_value "$@"; SSH_TARGET=$2; shift 2 ;;
        --out) need_value "$@"; OUT=$2; shift 2 ;;
        --recordings) need_value "$@"; RECORDINGS=$2; shift 2 ;;
        --manifest) need_value "$@"; MANIFEST=$2; shift 2 ;;
        --dmesg) need_value "$@"; DMESG=$2; shift 2 ;;
        --journal) need_value "$@"; JOURNAL=$2; shift 2 ;;
        --symptom) need_value "$@"; SYMPTOM=$2; shift 2 ;;
        --ffprobe) need_value "$@"; FFPROBE=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $1 (try --help)" ;;
    esac
done

[[ -f "$PROBE" ]] || die "missing probe: $PROBE"
[[ -f "$ANALYZE" ]] || die "missing analyzer: $ANALYZE"
if [[ -n "$SNAPSHOT" && -n "$SSH_TARGET" ]]; then
    die "--snapshot and --ssh are mutually exclusive"
fi
if [[ -z "$SNAPSHOT" && -z "$SSH_TARGET" ]]; then
    die "provide --snapshot FILE (preferred) or --ssh USER@HOST"
fi
if [[ -n "$SNAPSHOT" && ! -r "$SNAPSHOT" ]]; then
    die "snapshot is not readable: $SNAPSHOT"
fi
if [[ -n "$RECORDINGS" && ! -d "$RECORDINGS" ]]; then
    die "recordings directory does not exist: $RECORDINGS"
fi
if [[ -n "$MANIFEST" && ! -r "$MANIFEST" ]]; then
    die "manifest is not readable: $MANIFEST"
fi
if [[ -n "$DMESG" && ! -r "$DMESG" ]]; then
    die "dmesg capture is not readable: $DMESG"
fi
if [[ -n "$JOURNAL" && ! -r "$JOURNAL" ]]; then
    die "journal capture is not readable: $JOURNAL"
fi
case "$SYMPTOM" in
    ""|unknown|preview|recording|playback|both|mixed) ;;
    *) die "--symptom must be preview, recording, playback, both, mixed or unknown" ;;
esac

mkdir -p -- "$OUT"

if [[ -n "$SNAPSHOT" ]]; then
    cp -- "$SNAPSHOT" "$OUT/snapshot.txt"
    printf 'snapshot_source=%s\n' "$SNAPSHOT" > "$OUT/input.txt"
else
    command -v ssh >/dev/null 2>&1 || die "ssh not found"
    printf 'ssh_target=%s\n' "$SSH_TARGET" > "$OUT/input.txt"
    set +e
    # One attempt only.  BatchMode disables password/passphrase prompts and
    # StrictHostKeyChecking=yes keeps normal host-key verification without
    # writing known_hosts.  No key or credential file is read or changed by
    # this script.
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 \
        -o NumberOfPasswordPrompts=0 \
        "$SSH_TARGET" 'sh -s' < "$PROBE" > "$OUT/snapshot.txt" 2> "$OUT/ssh.stderr"
    rc=$?
    set -e
    printf '%s\n' "$rc" > "$OUT/ssh.rc"
    if [[ $rc -ne 0 ]]; then
        printf 'collect_profile: single BatchMode SSH attempt failed (rc=%s); no retry was made.\n' "$rc" >&2
        printf 'collect_profile: run scripts/nura-probe.sh on the phone and use --snapshot.\n' >&2
        exit 3
    fi
fi

if [[ ! -s "$OUT/snapshot.txt" ]]; then
    printf 'collect_profile: snapshot is empty; no analysis written.\n' >&2
    exit 3
fi

MANIFEST_ARG=()
if [[ -n "$MANIFEST" ]]; then
    cp -- "$MANIFEST" "$OUT/manifest.json"
    MANIFEST_ARG=(--manifest "$OUT/manifest.json")
fi

RECORDINGS_ARG=()
if [[ -n "$RECORDINGS" ]]; then
    RECORDINGS_ARG=(--recordings "$RECORDINGS")
fi

LOG_ARG=()
if [[ -n "$DMESG" ]]; then
    cp -- "$DMESG" "$OUT/dmesg.txt"
    LOG_ARG+=(--dmesg "$OUT/dmesg.txt")
fi
if [[ -n "$JOURNAL" ]]; then
    cp -- "$JOURNAL" "$OUT/journal.txt"
    LOG_ARG+=(--journal "$OUT/journal.txt")
fi

SYMPTOM_ARG=()
if [[ -n "$SYMPTOM" ]]; then
    SYMPTOM_ARG=(--symptom "$SYMPTOM")
fi

if ! python3 "$ANALYZE" --snapshot "$OUT/snapshot.txt" --out "$OUT" \
        "${MANIFEST_ARG[@]}" "${RECORDINGS_ARG[@]}" "${SYMPTOM_ARG[@]}" \
        "${LOG_ARG[@]}" --ffprobe "$FFPROBE"; then
    printf 'collect_profile: analyzer failed; see %s\n' "$OUT" >&2
    exit 4
fi

(
    cd -- "$OUT"
    find . -type f ! -name SHA256SUMS -print0 |
        sort -z |
        xargs -0 -r sha256sum > SHA256SUMS
)

printf 'collect_profile: wrote %s\n' "$OUT"
printf '  report: %s/report.md\n' "$OUT"
printf '  json:   %s/analysis.json\n' "$OUT"
printf '  clips:  %s/videos.tsv\n' "$OUT"
printf 'Next: read report.md, then collect any missing labeled clip/watch data.\n'
