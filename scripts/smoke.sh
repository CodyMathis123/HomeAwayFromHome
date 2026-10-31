#!/usr/bin/env bash
# LAN/localhost smoke checks. Returns nonzero if a required probe fails.
set -euo pipefail
cd "$(dirname "$0")/.."
IP="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
failed=0
echo "Testing services on $IP (no settings changed)..."
if ! command -v dig >/dev/null; then
  echo "FAIL: dig is missing; install dnsutils before claiming DNS works."
  failed=1
elif dig +tries=1 +time=2 @"$IP" example.com A | grep -q 'status: NOERROR'; then
  echo "PASS: Pi-hole answered DNS query"
else
  echo "FAIL: Pi-hole did not answer DNS query"
  failed=1
fi
if ! command -v curl >/dev/null; then
  echo "FAIL: curl is missing"
  failed=1
elif curl -fsS --max-time 3 "http://$IP:8088/healthz" >/dev/null; then
  echo "PASS: Portal HTTP"
else
  echo "FAIL: Portal HTTP"
  failed=1
fi
if command -v curl >/dev/null && curl -fsS --max-time 3 "http://$IP:8088/api/status" |
  python3 -c 'import json,sys; data=json.load(sys.stdin); checks={x["id"]:x["ok"] for x in data["checks"]}; assert checks["dns"] and checks["wireguard"], checks'
then
  echo "PASS: Portal reports DNS and WireGuard admin listener reachable"
else
  echo "FAIL: Portal core service probes"
  failed=1
fi
echo "A successful smoke test does NOT verify real WireGuard peer handshakes, routing or Pi-hole filtering for VPN clients."
exit "$failed"
