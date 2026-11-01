#!/usr/bin/env bash
# Disable only Phase 2 host management; keep current PC power commands.
set -euo pipefail
mode="${1:---plan}"
if [[ "$mode" != "--plan" && "$mode" != "--apply" || $# -gt 1 ]]; then
  echo "Usage: bash scripts/disable-pcinventory-host.sh --plan | sudo bash scripts/disable-pcinventory-host.sh --apply" >&2
  exit 2
fi
echo "HomeAway Phase 2 management rollback:"
echo " - FIRST disable HOMEAWAY_COMPUTER_ADMIN_ENABLED and omit compose.computers.yaml"
echo " - Recreate ONLY pc-control using the original working Compose overlays"
echo " - Stop/disable only homeaway-inventory and homeaway-pcverify sockets/services"
echo " - Preserve /etc/homeaway-pcctl/hosts.json (including changes since installation)"
echo " - Preserve retired/draft/audit records and all Pi/Windows SSH keys"
echo " - NEVER stop or restart pcctl-agent, Pi-hole, WireGuard or SensorDash"
if [[ "$mode" == "--plan" ]]; then
  echo "DRY RUN: no services changed"
  exit 0
fi
[[ "$(id -u)" == 0 ]] || { echo "Use sudo for --apply" >&2; exit 1; }
# Only disable new host sockets after explicitly turning off the new
# management API. Do not touch the existing power-control agent.
if [[ "${HOMEAWAY_COMPUTER_ADMIN_ENABLED:-}" != "false" ]]; then
  echo "STOP: after disabling the feature in .env and removing the Compose overlay, run: sudo env HOMEAWAY_COMPUTER_ADMIN_ENABLED=false bash scripts/disable-pcinventory-host.sh --apply" >&2
  exit 1
fi
for unit in homeaway-inventory.socket homeaway-pcverify.socket; do
  systemctl disable --now "$unit"
done
for unit in homeaway-inventory.service homeaway-pcverify.service; do
  systemctl stop "$unit" || true
done
echo "PASS: optional inventory/verifier host sockets are disabled."
echo "Current pcctl power commands and active PC IDs are unchanged."
echo "Inventory ownership, recovery records and SSH keys were not changed."
