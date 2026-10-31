#!/usr/bin/env bash
# Offline, consistent backup (intentional DNS and VPN outage while services stop).
# Run: sudo ./scripts/backup.sh --offline
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ "${1:-}" != "--offline" ]]; then
  echo "Usage: sudo ./scripts/backup.sh --offline" >&2
  echo "Temporarily stops running services for a consistent, secret-containing backup." >&2
  exit 2
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo to read root-owned Pi-hole and WireGuard data." >&2
  exit 1
fi
if [[ ! -f .env || ! -s secrets/pihole_password ]]; then
  echo "Missing deployment configuration or secret." >&2
  exit 1
fi
mkdir -p /var/backups/homeawayfromhome
chmod 700 /var/backups/homeawayfromhome
running_list="$(docker compose --profile metrics ps --services --status running)"
running=()
while IFS= read -r service; do
  [[ -n "$service" ]] && running+=("$service")
done <<< "$running_list"
echo "Stopping ${running[*]:-none} for consistent backup (DNS/VPN may briefly stop)."
stopped=0
restart_services() {
  if [[ "$stopped" == 1 && "${#running[@]}" -gt 0 ]]; then
    echo "Restarting previously running services..."
    docker compose --profile metrics start "${running[@]}" || echo "ERROR: Manual recovery required" >&2
  fi
}
trap restart_services EXIT
if [[ "${#running[@]}" -gt 0 ]]; then
  docker compose --profile metrics stop "${running[@]}"
  stopped=1
fi
archive="/var/backups/homeawayfromhome/backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
temp="${archive}.partial"
trap 'rm -f "$temp"; restart_services' EXIT
tar -czf "$temp" -- .env secrets data
tar -tzf "$temp" >/dev/null
mv "$temp" "$archive"
chmod 600 "$archive"
sha256sum "$archive" > "${archive}.sha256"
chmod 600 "${archive}.sha256"
echo "Backup complete: $archive"
echo "WARNING: Contains Pi-hole secrets, VPN private keys, and usage history. Store encrypted off-device."
