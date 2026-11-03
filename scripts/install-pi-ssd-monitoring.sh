#!/usr/bin/env bash
# Explicit, opt-in host installation. No Compose, DNS, VPN or Windows changes.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
mode="${1:---plan}"
collector=/usr/local/libexec/homeaway-pi-ssd/collector.py
metrics_dir=/var/lib/homeaway-ssd-metrics
units=(homeaway-pi-ssd.service homeaway-pi-ssd.timer)
case "$mode" in
  --plan|--apply|--status|--rollback) ;;
  *) echo "Usage: bash scripts/install-pi-ssd-monitoring.sh --plan|--apply|--status|--rollback" >&2; exit 2 ;;
esac
if [[ "$mode" == "--status" ]]; then
  systemctl status homeaway-pi-ssd.timer --no-pager || true
  systemctl status homeaway-pi-ssd.service --no-pager || true
  [[ -f "$metrics_dir/pi-ssd.prom" ]] && grep '^homeaway_pi_ssd_.*success' "$metrics_dir/pi-ssd.prom" || true
  exit 0
fi
if [[ "$mode" == "--rollback" ]]; then
  [[ "$EUID" == 0 ]] || { echo "Use sudo for --rollback" >&2; exit 1; }
  systemctl disable --now homeaway-pi-ssd.timer 2>/dev/null || true
  systemctl stop homeaway-pi-ssd.service 2>/dev/null || true
  for unit in "${units[@]}"; do rm -f "/etc/systemd/system/$unit"; done
  rm -f "$collector"
  systemctl daemon-reload
  echo "Host collector removed; retained historical metrics directory: $metrics_dir"
  echo "Revert the node-exporter bind mount separately if desired."
  exit 0
fi
for cmd in smartctl findmnt lsblk journalctl python3 systemctl; do
  command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd" >&2; exit 1; }
done
for file in monitoring/pi_ssd/collector.py systemd/homeaway-pi-ssd.service systemd/homeaway-pi-ssd.timer; do
  [[ -f "$root/$file" && ! -L "$root/$file" ]] || { echo "Missing/unsafe: $file" >&2; exit 1; }
done
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN: root-owned host SMART + journald reader -> $metrics_dir/pi-ssd.prom"
  echo "PLAN: systemd 5-minute collection timer; read-only node-exporter mount"
  echo "PLAN: no automatic container restart, no public network ports"
  echo "PLAN: requires smartmontools and Pi OS root on USB SATA SSD"
  exit 0
fi
[[ "$EUID" == 0 ]] || { echo "Use sudo for --apply" >&2; exit 1; }
install -d -m 0755 -o root -g root /usr/local/libexec/homeaway-pi-ssd
install -d -m 0755 -o root -g root "$metrics_dir"
install -m 0644 "$root/monitoring/pi_ssd/collector.py" "$collector"
for unit in "${units[@]}"; do
  install -m 0644 "$root/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now homeaway-pi-ssd.timer
if systemctl start homeaway-pi-ssd.service; then
  echo "PASS: SMART sample collected"
else
  echo "WARNING: collector wrote an unavailable status; inspect journalctl -u homeaway-pi-ssd.service" >&2
fi
echo "No containers restarted. Recreate ONLY pi-node-exporter after confirming the metrics file."
