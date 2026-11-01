#!/usr/bin/env bash
# EXPLICIT operator-only installation of the separate, non-root inventory writer.
# Not run by Docker Compose or the existing pcctl installer.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mode="--plan"
if [[ $# -gt 0 ]]; then mode="$1"; fi
if [[ "$mode" != "--plan" && "$mode" != "--apply" ]]; then
  echo "Usage: bash scripts/install-pcinventory-host.sh --plan | sudo bash scripts/install-pcinventory-host.sh --apply" >&2
  exit 2
fi

echo "HomeAway computer inventory management service:"
echo " - preserves existing active PC IDs, IP/MAC and iOS Shortcut bindings"
echo " - creates a separate unprivileged system user and Unix socket"
echo " - changes ONLY /etc/homeaway-pcctl directory owner and hosts.json owner/group"
echo " - makes a root-only before-install snapshot; never edits host entries"
echo " - installs a separate read-only SSH trust verifier under the existing pcctl user"
echo " - does NOT change pcctl-agent, Pi-hole, WireGuard, DNS or Windows"
inventory=/etc/homeaway-pcctl/hosts.json
# This is a genuine read-only check, not a confirmation-only "dry run".
# Fail before changing any ownership or service if the live inventory is
# invalid or the existing host cannot satisfy the new service requirements.
python3 -B "$root/scripts/preflight-pcinventory-host.py" --inventory "$inventory"
if [[ "$mode" == "--plan" ]]; then
  echo "DRY RUN: preflight passed; no filesystem ownership or services changed"
  exit 0
fi
[[ "$EUID" == 0 ]] || { echo "Use sudo for --apply" >&2; exit 1; }
command -v systemctl >/dev/null || { echo "systemd is required" >&2; exit 1; }
command -v systemd-tmpfiles >/dev/null || { echo "systemd-tmpfiles is required" >&2; exit 1; }
for source in \
  "$root/control/pcctl/core.py" \
  "$root/control/pcctl/inventory_store.py" \
  "$root/control/pcinventory/agent.py" \
  "$root/control/pcinventory/homeaway-inventory.service" \
  "$root/control/pcinventory/homeaway-inventory.socket" \
  "$root/control/pcinventory/homeaway-inventory.tmpfiles.conf" \
  "$root/control/pcverify/agent.py" \
  "$root/control/pcverify/homeaway-pcverify.service" \
  "$root/control/pcverify/homeaway-pcverify.socket" \
  "$root/control/pcverify/homeaway-pcverify.tmpfiles.conf"; do
  [[ -f "$source" && ! -L "$source" ]] || { echo "Missing or unsafe installed source: $source" >&2; exit 1; }
done
directory=/etc/homeaway-pcctl
[[ -d "$directory" && ! -L "$directory" ]] || { echo "Unsafe or missing inventory directory" >&2; exit 1; }
[[ -f "$inventory" && ! -L "$inventory" ]] || { echo "Unsafe or missing active inventory" >&2; exit 1; }
[[ "$(stat -c %h "$inventory")" == 1 ]] || { echo "Refusing hardlinked inventory" >&2; exit 1; }
id pcctl >/dev/null || { echo "Existing pcctl Linux account required" >&2; exit 1; }
if ! id homeaway-inventory >/dev/null 2>&1; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin homeaway-inventory
fi
# Keep the inventory-writer group distinct from pcctl's power-command group.
# The inventory is connection metadata (no passwords/keys), and uses the
# original 0644 read model; the inventory socket is private mode 0660.
# NEVER add the inventory service user to the pcctl socket's group.
backup=/var/backups/homeaway-pcinventory
install -d -m 0700 -o root -g root "$backup"
snapshot="$backup/hosts-preinstall-$(date -u +%Y%m%dT%H%M%SZ).json"
install -m 0600 -o root -g root "$inventory" "$snapshot"

install -d -m 0755 -o homeaway-inventory -g homeaway-inventory "$directory"
chown homeaway-inventory:homeaway-inventory "$inventory"
chmod 0644 "$inventory"
lock="$directory/.hosts.json.lock"
if [[ -L "$lock" ]]; then echo "Unsafe inventory lock" >&2; exit 1; fi
if [[ -e "$lock" && ! -f "$lock" ]]; then echo "Inventory lock must be regular" >&2; exit 1; fi
touch "$lock"
chown homeaway-inventory:homeaway-inventory "$lock"
chmod 0600 "$lock"

install -d -m 0755 -o root -g root /opt/homeaway-pcinventory
for file in core.py inventory_store.py; do
  install -m 0644 "$root/control/pcctl/$file" "/opt/homeaway-pcinventory/$file"
done
install -m 0644 "$root/control/pcinventory/agent.py" /opt/homeaway-pcinventory/agent.py
install -d -m 0700 -o homeaway-inventory -g homeaway-inventory /var/lib/homeaway-pcinventory
install -m 0644 "$root/control/pcinventory/homeaway-inventory.tmpfiles.conf" /etc/tmpfiles.d/homeaway-inventory.conf
systemd-tmpfiles --create /etc/tmpfiles.d/homeaway-inventory.conf
install -m 0644 "$root/control/pcinventory/homeaway-inventory.socket" /etc/systemd/system/homeaway-inventory.socket
install -m 0644 "$root/control/pcinventory/homeaway-inventory.service" /etc/systemd/system/homeaway-inventory.service
systemctl daemon-reload
systemctl enable --now homeaway-inventory.socket

# Verification is a separate service running under the EXISTING pcctl user.
# It can read the Pi's private SSH key, while the inventory writer cannot.
# Its Unix socket permits the inventory writer group only; never place
# homeaway-inventory in the pcctl power-command group.
install -d -m 0755 -o root -g root /opt/homeaway-pcverify
install -m 0644 "$root/control/pcctl/core.py" /opt/homeaway-pcverify/core.py
install -m 0644 "$root/control/pcverify/agent.py" /opt/homeaway-pcverify/agent.py
install -m 0644 "$root/control/pcverify/homeaway-pcverify.tmpfiles.conf" /etc/tmpfiles.d/homeaway-pcverify.conf
systemd-tmpfiles --create /etc/tmpfiles.d/homeaway-pcverify.conf
install -m 0644 "$root/control/pcverify/homeaway-pcverify.socket" /etc/systemd/system/homeaway-pcverify.socket
install -m 0644 "$root/control/pcverify/homeaway-pcverify.service" /etc/systemd/system/homeaway-pcverify.service
systemctl daemon-reload
systemctl enable --now homeaway-pcverify.socket

echo "Inventory and SSH-verifier sockets ready; no live host entries were changed."
echo "Local rollback inventory snapshot: $snapshot (root-only)"
echo "Set PCINV_GID=$(id -g homeaway-inventory) in .env only when activating compose.computers.yaml"
echo "Management API remains feature-flagged off unless separately enabled."
