#!/usr/bin/env bash
# Install an optional weekly READ-ONLY backup preflight timer, disabled by default.
set -euo pipefail

usage() { echo "Usage: sudo bash scripts/install-backup-preflight.sh --plan|--apply" >&2; }
[[ "$#" == 1 && ( "$1" == --plan || "$1" == --apply ) ]] || { usage; exit 2; }
[[ "$EUID" == 0 ]] || { echo "Run as root." >&2; exit 1; }
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$root/scripts/backup-maintenance.py" && -f "$root/systemd/homeaway-backup-preflight.service" ]] ||
  { echo "Missing required files." >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemctl unavailable." >&2; exit 1; }
echo "Weekly Sunday 03:00 local; PREVIEW/REPORT ONLY; does not execute backups."
echo "Installs service and timer unit definitions for: $root"
echo "Timer remains DISABLED until operator explicitly enables it."
if [[ "$1" == --plan ]]; then
  echo "PLAN ONLY: no units installed or enabled."
  exit 0
fi
# This installer installs only units, not a backup, key, or automatic outage.
# Expand % in the path because systemd treats it as a specifier.
[[ "$root" != *%* && "$root" != *$'\n'* ]] ||
  { echo "Unsafe repository path for systemd unit." >&2; exit 1; }
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
sed "s|/home/codymathis/HomeAwayFromHome|$root|g" \
  "$root/systemd/homeaway-backup-preflight.service" > "$tmp"
install -o root -g root -m 0644 "$tmp" /etc/systemd/system/homeaway-backup-preflight.service
install -o root -g root -m 0644 "$root/systemd/homeaway-backup-preflight.timer" \
  /etc/systemd/system/homeaway-backup-preflight.timer
systemctl daemon-reload
echo "Units installed. Timer NOT enabled or started."
echo "To enable READ-ONLY weekly checks later: sudo systemctl enable --now homeaway-backup-preflight.timer"
echo "To run once without scheduling: sudo python3 scripts/backup-maintenance.py --publish"
