#!/usr/bin/env bash
# Read-only verification; queries only VictoriaMetrics' unpublished internal API.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== HomeAway DNS/VPN metrics verification =="
docker compose --profile metrics exec -T telemetry-ingest python - <<'PY'
import json
import urllib.parse
import urllib.request

base = "http://victoria-metrics:8428/api/v1/query?"
checks = [
    ("Pi-hole scrape reachable", 'up{job="pihole"}', True),
    ("wg-easy scrape reachable", 'up{job="wg-easy"}', True),
    ("Pi-hole DNS query window", "homeaway_pihole_queries_window", False),
    ("Pi-hole DNS filtering", "homeaway_pihole_blocking_enabled", False),
    ("WireGuard configured peers", "wireguard_configured_peers", False),
    ("WireGuard connected peers", "wireguard_connected_peers", False),
]
failures = []
for name, expression, is_up in checks:
    try:
        url = base + urllib.parse.urlencode({"query": expression})
        with urllib.request.urlopen(url, timeout=7) as response:
            data = json.load(response)["data"]["result"]
        if not data:
            raise ValueError("no current series")
        values = [float(row["value"][1]) for row in data]
        if is_up and any(v != 1 for v in values):
            raise ValueError("target is down")
        print("PASS:", name, ", ".join(f"{v:g}" for v in values))
    except (OSError, ValueError, KeyError) as exc:
        print("WAIT:", name, "-", str(exc))
        failures.append(name)
if failures:
    print("Monitoring setup incomplete:", ", ".join(failures))
    print("Confirm wg-easy Admin > General Prometheus is enabled with matching bearer.")
    print("Confirm the Pi-hole v6 application password and service logs on the Pi.")
    raise SystemExit(2)
print("PASS: DNS/VPN Prometheus series exist. Actual VPN access and DNS filtering")
print("still require separate end-to-end client tests.")
PY
