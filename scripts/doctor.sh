#!/usr/bin/env bash
# Read-only checks; no package installation or network changes.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== HomeAwayFromHome preflight =="
if [[ "$(uname -m)" != "aarch64" ]]; then
  echo "WARN: Expected Pi OS 64-bit aarch64; found $(uname -m)."
fi
for command in python3 docker ss ip; do
  if command -v "$command" >/dev/null; then
    echo "OK   $command available"
  else
    echo "WARN $command missing"
  fi
done
if [[ ! -f .env ]]; then
  echo "ERROR: .env not found; run ./scripts/init.sh" >&2
  exit 1
fi
python3 scripts/validate_config.py .env
IP="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
if command -v ip >/dev/null && ! ip -4 addr show | grep -Fq "$IP/"; then
  echo "WARN: Pi IP $IP does not appear on a local network interface."
fi
if [[ ! -s secrets/pihole_password ]]; then
  echo "ERROR: Missing Pi-hole password secret. Run init.sh" >&2
  exit 1
fi
if command -v ss >/dev/null; then
  echo "--- Existing DNS listener(s) (stop old Pi-hole/dnsmasq BEFORE deployment) ---"
  ss -H -lunt | grep -E '(:53[[:space:]]|:51820[[:space:]]|:51821[[:space:]]|:8080[[:space:]]|:8088[[:space:]])' || true
fi
if command -v docker >/dev/null; then
  if docker compose version >/dev/null 2>&1; then
    echo "--- Docker Compose config ---"
    docker compose config -q
    docker compose --profile monitoring config -q
    echo "OK   Compose configuration valid"
  else
    echo "WARN: Docker Compose plugin not working or missing"
  fi
fi
echo "Review router WAN IP / CGNAT status, firewall, and DNS rollback plan before rollout."
