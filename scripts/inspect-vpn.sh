#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
echo "WireGuard peers and last handshakes (may expose peer public IP addresses):"
docker compose exec -T wg-easy wg show
