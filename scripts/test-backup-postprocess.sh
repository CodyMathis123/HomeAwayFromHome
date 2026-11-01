#!/usr/bin/env bash
# Mock postbackup publisher; no real backup, Docker, or systemctl access.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/backup-postprocess.sh

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
python3() {
  [[ "$1" == "$tmp/scripts/publish-backup-status.py" &&
     "$2" == "--publish" ]] || return 99
  echo "$2" >> "$tmp/calls"
  return "${FAIL_PUBLISH:-0}"
}
mkdir -p "$tmp/scripts"
homeaway_publish_postbackup "$tmp"
[[ "$(wc -l < "$tmp/calls")" -eq 1 ]]

FAIL_PUBLISH=1
if homeaway_publish_postbackup "$tmp" 2>"$tmp/errors"; then
  echo "FAIL: publish failure was silently treated as success" >&2
  exit 1
fi
grep -q "backup exists" "$tmp/errors"
grep -q "manual" "$tmp/errors"
[[ "$(wc -l < "$tmp/calls")" -eq 2 ]]
echo "PASS: postbackup publication succeeds/fails independently; real services and archives untouched."
