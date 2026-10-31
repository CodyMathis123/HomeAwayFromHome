#!/usr/bin/env bash
# Basic tests after first install. Includes tests from your current network.
set -euo pipefail
cd "$(dirname "$0")/.."
IP="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
echo "Testing services on $IP (no settings changed)..."
if command -v dig >/dev/null; then
  dig +tries=1 +time=2 @"$IP" example.com A | grep -q 'status: NOERROR' &&
    echo "PASS Pi-hole DNS query" || echo "FAIL Pi-hole DNS query"
else
  echo "SKIP DNS query: install dnsutils (dig)"
fi
if command -v curl >/dev/null; then
  curl -fsS --max-time 3 "http://$IP:8088/healthz" >/dev/null &&
    echo "PASS Portal HTTP" || echo "FAIL Portal HTTP"
  curl -fsS --max-time 3 "http://$IP:8088/api/status" | python3 -m json.tool >/dev/null &&
    echo "PASS Portal status API" || echo "FAIL Portal status API"
fi
echo "VPN handshake is NOT proven by these checks."
echo "Verify on cellular using WireGuard app, then RDP, Pi-hole queries and DNS leak testing."
