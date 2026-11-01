#!/usr/bin/env bash
# Operator-only encrypted full Pi application+host-state snapshot.
# --plan is strictly read-only; --apply intentionally interrupts HomeAway
# Docker containers AND the three host-side PC-management socket services.
set -euo pipefail
umask 077

usage() {
  echo "Usage: sudo bash scripts/backup-full.sh --plan | --apply --recipient age1PUBLIC_RECIPIENT" >&2
}
die() { echo "ERROR: $*" >&2; exit 1; }

mode="${1:-}"
recipient=""
case "$mode" in
  --plan) [[ "$#" == 1 ]] || { usage; exit 2; } ;;
  --apply)
    [[ "$#" == 3 && "${2:-}" == "--recipient" ]] || { usage; exit 2; }
    recipient="$3"
    [[ "$recipient" =~ ^age1[a-z0-9]{50,100}$ ]] ||
      die "Expected one public age X25519 recipient (age1...); never supply a private key."
    ;;
  *) usage; exit 2 ;;
esac

[[ "$EUID" == 0 ]] || die "Run with sudo."
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
# Recovery helper is shell-only; never execute extra commands on import.
source "$root/scripts/backup-recovery.sh"
source "$root/scripts/backup-postprocess.sh"

for tool in docker python3 systemctl tar sha256sum flock; do
  command -v "$tool" >/dev/null || die "Missing prerequisite: $tool"
done
if [[ "$mode" == --apply ]]; then
  command -v age >/dev/null || die "age encryption executable not found; install and configure age before applying."
fi
[[ -f .env && -d secrets && -d data ]] || die "Missing existing HomeAway application state."

# The host-state set is intentionally fixed. No user-controlled archive paths.
host_paths=(
  etc/homeaway-pcctl
  home/pcctl/.ssh
  var/lib/homeaway-pcinventory
  opt/homeaway-pcctl
  opt/homeaway-pcinventory
  opt/homeaway-pcverify
  etc/systemd/system/pcctl-agent.socket
  etc/systemd/system/pcctl-agent.service
  etc/systemd/system/homeaway-inventory.socket
  etc/systemd/system/homeaway-inventory.service
  etc/systemd/system/homeaway-pcverify.socket
  etc/systemd/system/homeaway-pcverify.service
  etc/tmpfiles.d/homeaway-inventory.conf
  etc/tmpfiles.d/homeaway-pcverify.conf
)
for rel in "${host_paths[@]}"; do
  [[ -e "/$rel" && ! -L "/$rel" ]] || die "Missing or symlinked required host path /$rel"
done

compose=(docker compose -f compose.yaml -f compose.control.yaml
  -f compose.accounts.yaml -f compose.computers.yaml
  -f compose.production.yaml --profile control --profile accounts --profile metrics)
"${compose[@]}" config -q >/dev/null ||
  die "Canonical production Compose configuration invalid."
project="$("${compose[@]}" config --format json |
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))')" ||
  die "Cannot determine Compose project."
[[ "$project" == homeawayfromhome ]] || die "Unexpected Compose project."

# Required host writes are socket-activated. Capture the original active
# service/socket set; stop sockets before writers after Docker is stopped.
units=(
  pcctl-agent.socket
  homeaway-inventory.socket
  homeaway-pcverify.socket
  pcctl-agent.service
  homeaway-inventory.service
  homeaway-pcverify.service
)
active_units=()
for unit in "${units[@]}"; do
  systemctl is-active --quiet "$unit" && active_units+=("$unit") || true
done
running=()
while IFS= read -r cid; do
  [[ -n "$cid" ]] && running+=("$cid")
done < <(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.ID}}')

echo "HomeAway encrypted full-state backup: $mode"
echo "Compose containers currently running: ${#running[@]}"
echo "Host socket/services currently active: ${#active_units[@]}"
echo "Application paths: .env, secrets/, data/"
echo "Host-side required paths: ${#host_paths[@]} (registry, SSH trust, agent code, units and inventory)"
echo "Scope: all running Compose-project containers + six fixed host units (only those currently active)"
echo "Outage: DNS, WireGuard, HTTPS login/portal and PC operations may be unavailable."
echo "Encryption: age recipient required; no plaintext tar archive will be written."
echo "Not included: operating system image, packages, boot files, router configuration, off-device copy or restore test."
if [[ "$mode" == --plan ]]; then
  echo "PLAN ONLY: no services stopped; no files changed; no archives created."
  exit 0
fi

# A second in-progress backup must not race the first.
exec 9>/run/lock/homeaway-full-backup.lock
flock -n 9 || die "Another full backup is running."

# Recheck inventory service state under lock to make preflight output useful.
# Retain original states above for exact restoration after an interrupted stop.
archive_dir=/var/backups/homeawayfromhome
install -d -o root -g root -m 0700 "$archive_dir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$archive_dir/full-$stamp-$$.tar.age"
partial="$archive.partial"
metadata="$(mktemp -d)"
chmod 700 "$metadata"
printf 'format=homeaway-full-v1\ncreated_utc=%s\nproject=%s\ncommit=%s\n' \
  "$stamp" "$project" "$(git rev-parse HEAD 2>/dev/null || echo unknown)" \
  > "$metadata/backup-metadata.txt"
chmod 600 "$metadata/backup-metadata.txt"

recovery_needed=0
backup_committed=0
cleanup() {
  original_status=$?
  trap - EXIT HUP INT TERM
  if (( recovery_needed )); then
    if ! homeaway_restore_active; then
      echo "ERROR: one or more previously active units/containers could not be restarted." >&2
      original_status=1
    fi
  fi
  # Only publish new backup evidence after the encrypted artifact, checksum,
  # and restoration of all previously active services have succeeded.
  # A publication failure is reported but does not delete a usable backup.
  if (( original_status == 0 && backup_committed == 1 )); then
    homeaway_publish_postbackup "$root" ||
      echo "WARNING: archive retained; status publishing needs manual retry." >&2
  fi
  [[ ! -e "$partial" ]] || rm -f -- "$partial"
  [[ ! -d "$metadata" ]] || rm -rf -- "$metadata"
  if (( original_status != 0 )); then
    echo "ERROR: full backup failed. Inspect systemctl/Docker status before another attempt." >&2
  fi
  exit "$original_status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

recovery_needed=1
echo "Stopping active project containers (up to 60 seconds each)..."
if (( ${#running[@]} > 0 )); then
  docker stop --time 60 "${running[@]}" >/dev/null
fi
echo "Pausing host-side socket listeners and their writer services..."
for unit in "${active_units[@]}"; do
  systemctl stop "$unit"
done
# Guard against service/socket activation after stop: if any target still
# active, abort before creating the archive.
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit"; then
    die "Host unit still active after quiesce: $unit"
  fi
done

echo "Streaming tar directly into age; only encrypted output goes to disk."
tar --acls --xattrs --numeric-owner -cf - \
  -C "$root" .env secrets data \
  -C / "${host_paths[@]}" \
  -C "$metadata" backup-metadata.txt |
  age -r "$recipient" -o "$partial"
[[ -s "$partial" ]] || die "Encrypted archive is missing/empty."
chmod 600 "$partial"
mv -- "$partial" "$archive"
sha256sum "$archive" > "$archive.sha256"
chmod 600 "$archive.sha256"
backup_committed=1
echo "Encrypted archive created: $archive"
echo "Encrypted archive SHA-256: $archive.sha256"
echo "IMPORTANT: copy the encrypted archive and checksum off the Pi and perform an isolated restore test."
