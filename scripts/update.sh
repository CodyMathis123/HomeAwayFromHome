#!/usr/bin/env bash
# Review release notes and take an offline backup before running.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "--apply" ]]; then
  echo "Usage: ./scripts/update.sh --apply" >&2
  echo "First: sudo ./scripts/backup.sh --offline"
  echo "Updates only core Pi-hole and WireGuard; UI and HTTPS gateway are unchanged."
  exit 2
fi
python3 scripts/validate_config.py .env
docker compose config -q
docker compose --profile metrics config -q
# Core service maintenance must never recreate the portal with legacy URLs.
# Keep UI/gateway releases in deploy-ui.sh and the production cutover runbook.
docker compose pull pihole wg-easy
docker compose up -d --no-deps pihole wg-easy
# Optional metrics remain opt-in and are not started by core updates.
echo "Core update applied; HTTPS gateway and UI containers untouched. Run ./scripts/doctor.sh and ./scripts/smoke.sh."
