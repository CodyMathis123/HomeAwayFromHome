#!/usr/bin/env bash
# Offline, consistent HomeAway Compose backup.
# Explicit outage: Pi-hole/DNS, WireGuard, Caddy/Authelia and metrics may stop.
# Usage: sudo ./scripts/backup.sh --plan | --offline
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."

mode="${1:-}"
if [[ "$mode" != "--offline" && "$mode" != "--plan" ]]; then
  echo "Usage: sudo ./scripts/backup.sh --plan | --offline" >&2
  echo "Plan prints scope without stopping services; --offline stops the entire" >&2
  echo "HomeAway Compose project for a consistent secret-containing backup." >&2
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo to read root-owned HomeAway data and inspect Docker." >&2
  exit 1
fi
if [[ ! -f .env || ! -s secrets/pihole_password ]]; then
  echo "Missing deployment configuration or secret." >&2
  exit 1
fi

# The optional control/accounts services are in overlays, so base Compose
# 'ps --services' CANNOT identify them all. Resolve this checkout's actual
# Compose project name, then select ONLY its labeled running containers.
project="$(docker compose --profile metrics config --format json |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')"
if [[ -z "$project" || ! "$project" =~ ^[a-zA-Z0-9][a-zA-Z0-9_-]*$ ]]; then
  echo "Could not verify the HomeAway Compose project name; refusing backup." >&2
  exit 1
fi
running_list="$(docker ps \
  --filter "label=com.docker.compose.project=${project}" \
  --format '{{.ID}}')"
running=()
while IFS= read -r container; do
  [[ -n "$container" ]] && running+=("$container")
done <<< "$running_list"

if [[ "$mode" == "--plan" ]]; then
  echo "HomeAway Compose project: $project"
  echo "Currently running project containers to stop: ${#running[@]}"
  echo "Backup scope: .env, secrets/, data/ (including Authelia, LLDAP if present, and Caddy)"
  echo "No containers stopped; no archive created."
  exit 0
fi

mkdir -p /var/backups/homeawayfromhome
chmod 700 /var/backups/homeawayfromhome
echo "Stopping ${#running[@]} HomeAway Compose project containers (DNS/VPN and authentication may temporarily stop)."

restore_required=0
restart_containers() {
  if [[ "$restore_required" == 1 && "${#running[@]}" -gt 0 ]]; then
    echo "Restarting previously running HomeAway containers..."
    failed=0
    for container in "${running[@]}"; do
      # A partial stop failure must still restore any containers that stopped.
      if [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)" == "false" ]]; then
        docker start "$container" || failed=1
      fi
    done
    if [[ "$failed" == 1 ]]; then
      echo "ERROR: Some containers did not restart; recover immediately with Docker Compose." >&2
    fi
  fi
}
archive=""
temp=""
cleanup() {
  if [[ -n "$temp" ]]; then rm -f -- "$temp"; fi
  restart_containers
}
trap cleanup EXIT
if [[ "${#running[@]}" -gt 0 ]]; then
  restore_required=1
  docker stop --time 60 "${running[@]}"
fi

archive="/var/backups/homeawayfromhome/backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
temp="${archive}.partial"
tar -czf "$temp" -- .env secrets data
tar -tzf "$temp" >/dev/null
mv "$temp" "$archive"
temp=""
chmod 600 "$archive"
sha256sum "$archive" > "${archive}.sha256"
chmod 600 "${archive}.sha256"
echo "Backup complete: $archive"
echo "WARNING: Contains authentication secrets, private keys and history; encrypt off-device."
