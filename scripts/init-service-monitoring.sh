#!/usr/bin/env bash
# Initialize *new* monitoring credentials; never rotate live secrets silently.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
if [[ "$EUID" -ne 0 ]]; then
  echo "Run: sudo bash scripts/init-service-monitoring.sh" >&2
  exit 1
fi
command -v openssl >/dev/null || { echo "openssl required" >&2; exit 1; }
# Preserve the existing secrets/ owner. The original init.sh created it
# under the deployment user; changing ownership breaks unprivileged preflight.
mkdir -p secrets
chmod 700 secrets
if [[ ! -s secrets/wg_metrics_bearer ]]; then
  openssl rand -hex 32 > secrets/wg_metrics_bearer
  echo "Created wg-easy bearer credential; paste it into Admin Panel > General > Prometheus."
else
  echo "Existing wg-easy monitoring bearer retained."
fi
if [[ ! -s secrets/pihole_monitor_password ]]; then
  if [[ ! -t 0 ]]; then
    echo "Run interactively on the Pi to enter your Pi-hole v6 app password." >&2
    exit 2
  fi
  echo "Generate an application password in Pi-hole Settings > Web Interface/API."
  read -r -s -p "Paste Pi-hole APP password (input hidden): " pihole_app_password
  echo
  if [[ -z "$pihole_app_password" ]]; then
    echo "Empty password: no Pi-hole secret written." >&2
    exit 2
  fi
  printf '%s\n' "$pihole_app_password" > secrets/pihole_monitor_password
  unset pihole_app_password
  echo "Created isolated Pi-hole application credential."
else
  echo "Existing Pi-hole monitoring password retained."
fi
# Compose mounts local secret files read-only without always changing owner/mode.
# These files are readable by non-root processes INSIDE their isolated containers.
# The host secrets/ directory remains owner-only (0700); unrelated host
# accounts cannot traverse it. Existing Pi-hole/Grafana credentials are untouched.
chown root:root secrets/wg_metrics_bearer secrets/pihole_monitor_password
chmod 644 secrets/wg_metrics_bearer secrets/pihole_monitor_password
chmod 700 secrets
echo "Both credentials remain under the owner-only secrets/ directory; never post or commit them."
echo "To privately display the wg-easy bearer: sudo cat secrets/wg_metrics_bearer"
