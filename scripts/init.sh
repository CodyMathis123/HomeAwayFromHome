#!/usr/bin/env bash
# Non-destructive local setup; DOES NOT install Docker or start services.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env (edit IPs, LAN CIDR and VPN domain before deployment)."
else
  echo ".env already exists; left untouched."
fi
mkdir -p secrets data/pihole data/wireguard
chmod 700 secrets
if [[ ! -s secrets/pihole_password ]]; then
  if ! command -v openssl >/dev/null; then
    echo "openssl required to create a strong Pi-hole admin password." >&2
    exit 1
  fi
  openssl rand -hex 32 > secrets/pihole_password
  chmod 600 secrets/pihole_password
  echo "Generated Pi-hole admin secret at secrets/pihole_password (never commit this file)."
else
  echo "Pi-hole secret exists; left unchanged."
fi
echo "Next: edit .env, run ./scripts/doctor.sh and review README.md."
