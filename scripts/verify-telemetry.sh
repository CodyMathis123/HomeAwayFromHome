#!/usr/bin/env bash
# Read-only, post-deployment checks. No secrets printed or router changes.
# Optional first argument: stable SensorDash hostLabel. Empty = all machines.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-}"
if [[ -n "$HOST" && ! "$HOST" =~ ^[A-Za-z0-9_.:/-]{1,80}$ ]]; then
  echo "Host label must use only letters, digits, . _ : / -" >&2
  exit 2
fi
IP="$(python3 scripts/validate_config.py .env --get PI_LAN_IP)"
echo "== HomeAway telemetry verification on $IP =="
command -v curl >/dev/null || { echo "curl is required" >&2; exit 2; }
echo "Checking Grafana health..."
curl --fail --silent --show-error --max-time 8 "http://$IP:3000/api/health"
echo
echo "Checking authenticated TLS receiver never permits unauthenticated writes..."
code="$(curl --silent --show-error --max-time 8 \
  --resolve "metrics.homeawayfromhome.house:443:$IP" \
  --output /dev/null --write-out '%{http_code}' -X POST \
  --data-binary 'invalid metric' "https://metrics.homeawayfromhome.house/ingest")"
if [[ "$code" != "401" ]]; then
  echo "FAIL: Canonical HTTPS ingress returned HTTP $code (expected 401)." >&2
  exit 1
fi
echo "PASS: public TLS certificate validated; unauthenticated intake denied by receiver"
echo "Reading series counts via internal Docker network (VictoriaMetrics has no LAN port)..."
docker compose --profile metrics exec -T telemetry-ingest python - "$HOST" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

host = sys.argv[1]
selector = '{host="' + host + '"}' if host else ""
names = (
    "sensordash_reading_value",
    "sensordash_sensor_info_value",
    "sensordash_component_info_value",
    "sensordash_cooling_binding_value",
    "sensordash_accessory_power_value",
    "sensordash_spool_value",
    "sensordash_event_value",
)
counts = {}
for name in names:
    expression = "count(" + name + selector + ")"
    url = "http://victoria-metrics:8428/api/v1/query?" + urllib.parse.urlencode({"query": expression})
    with urllib.request.urlopen(url, timeout=6) as response:
        result = json.load(response)["data"]["result"]
    counts[name] = int(float(result[0]["value"][1])) if result else 0
    print(f"{name}: {counts[name]} active series")
if counts["sensordash_reading_value"] == 0:
    print("BLOCKED: no recent Windows raw sensor measurements. See the Windows exporter setup.", file=sys.stderr)
    raise SystemExit(2)
if counts["sensordash_sensor_info_value"] == 0:
    print("WARN: no fresh catalog metadata. Review Windows exporter logs.", file=sys.stderr)
if counts["sensordash_accessory_power_value"] == 0:
    print("INFO: no recent fan/pump wattage samples. Configure actual electrical sensors/calibration.", file=sys.stderr)
if counts["sensordash_spool_value"] == 0:
    print("INFO: no Windows retry-spool footprint series yet (new collector build required).")
if counts["sensordash_event_value"] == 0:
    print("INFO: no threshold-event impulses in the current lookback; this may be normal.")
print("PASS: VictoriaMetrics is receiving recent Windows sensor measurements")
PY
echo "Note: Active series counts exclude history older than instant-query lookback."
echo "These checks do not prove WireGuard handshakes or physical fan-control behavior."
echo "NOT CHECKED: Pi host CPU/RAM/thermal/uptime series (separate node exporter)."
echo "For HomeAway · System Health, run: sudo bash scripts/verify-pi-metrics.sh"
