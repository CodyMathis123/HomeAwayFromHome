#!/usr/bin/env bash
# Review release notes and take an offline backup before running.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "--apply" ]]; then
  echo "Usage: ./scripts/update.sh --apply" >&2
  echo "First: sudo ./scripts/backup.sh --offline"
  echo "Updates only from version tags explicitly recorded in compose.yaml."
  exit 2
fi
python3 scripts/validate_config.py .env
docker compose config -q
docker compose --profile monitoring config -q
docker compose pull pihole wg-easy
docker compose build portal
docker compose up -d pihole wg-easy portal
# Optional monitoring is never enabled implicitly. Update it explicitly if installed.
echo "Update applied. Run ./scripts/doctor.sh and ./scripts/smoke.sh."
