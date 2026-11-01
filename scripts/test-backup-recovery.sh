#!/usr/bin/env bash
# Exercise real backup recovery helper with mock systemctl/docker only.
# Safe: never calls system services and never accesses the real Docker daemon.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/backup-recovery.sh

systemctl() {
  [[ "$1" == start ]] || return 99
  case "$2" in
    socket-a) printf 'started %s\n' "$2" >> "$events" ;;
    service-b) printf 'started %s\n' "$2" >> "$events" ;;
    failed-unit) return 1 ;;
    *) return 99 ;;
  esac
}

docker() {
  case "$1" in
    inspect)
      case "${4:-}" in
        stopped-container) echo false ;;
        running-container) echo true ;;
        missing-container) return 1 ;;
        failed-container) echo false ;;
        *) return 99 ;;
      esac
      ;;
    start)
      [[ "$2" != failed-container ]] || return 1
      printf 'started %s\n' "$2" >> "$events"
      ;;
    *) return 99 ;;
  esac
}

tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
events="$tmp/events"
: > "$events"
active_units=(socket-a service-b)
running=(stopped-container running-container)
homeaway_restore_active
grep -qx 'started socket-a' "$events"
grep -qx 'started service-b' "$events"
grep -qx 'started stopped-container' "$events"
! grep -q 'running-container' "$events"

: > "$events"
active_units=(failed-unit socket-a)
running=(failed-container missing-container stopped-container)
if homeaway_restore_active 2>"$tmp/failure-log"; then
  echo "FAIL: recovery erroneously reported success" >&2
  exit 1
fi
grep -qx 'started socket-a' "$events"
grep -qx 'started stopped-container' "$events"
grep -q 'failed to restore host unit' "$tmp/failure-log"
grep -q 'failed to restart container' "$tmp/failure-log"
grep -q 'previously running container missing' "$tmp/failure-log"

echo "PASS: mocked recovery restores prior active services/containers and continues after failures."
echo "No real Docker calls or systemctl operations were performed."
