#!/usr/bin/env bash
# Non-disruptive post-backup publication. Called ONLY after service recovery.
# Archive success is not undone if status publication fails.
homeaway_publish_postbackup() {
  local root="$1" status
  if ! python3 "$root/scripts/publish-backup-status.py" --publish; then
    echo "WARNING: encrypted backup exists, but dashboard checksum status was not refreshed." >&2
    echo "Retry later: sudo python3 scripts/publish-backup-status.py --publish" >&2
    return 1
  fi
  return 0
}
