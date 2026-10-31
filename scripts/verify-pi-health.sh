#!/usr/bin/env bash
# Read-only Pi/telemetry freshness smoke test; no host-admin or Docker socket APIs.
set -euo pipefail
cd "$(dirname "$0")/.."
host="${1:-main-desktop}"
echo "== HomeAway Pi system and telemetry health verification =="
docker compose --profile metrics exec -T telemetry-ingest python - "$host" <<'PY'
import json
import re
import sys
import urllib.parse
import urllib.request

host = sys.argv[1]
if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", host):
    raise SystemExit("Invalid Windows host label")
base = "http://victoria-metrics:8428/api/v1/query?"
def query(expr):
    url = base + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=8) as response:
        result = json.load(response)
    if result.get("status") != "success":
        raise ValueError("VictoriaMetrics query failed")
    return result["data"]["result"]

checks = [
    ("Pi exporter scrape", 'up{job="pi-node"}', True),
    ("Host CPU idle counters", 'node_cpu_seconds_total{job="pi-node",mode="idle"}', False),
    ("Host memory total", 'node_memory_MemTotal_bytes{job="pi-node"}', False),
    ("VictoriaMetrics data filesystem free", "vm_free_disk_space_bytes", False),
]
failed = []
for name, expr, needs_up in checks:
    try:
        results = query(expr)
        if not results:
            raise ValueError("no series")
        values = [float(x["value"][1]) for x in results]
        if needs_up and any(value != 1 for value in values):
            raise ValueError("scrape target down")
        print("PASS:", name, f"({len(results)} series)")
    except (OSError, KeyError, ValueError) as error:
        print("WAIT:", name, "-", error)
        failed.append(name)

try:
    thermal = query('node_thermal_zone_temp{job="pi-node"}')
    if thermal:
        print("INFO: thermal zones found:", ", ".join(
            f"{x['metric'].get('type','unknown')}={float(x['value'][1]):.1f}°C"
            for x in thermal[:8]
        ))
    else:
        print("INFO: no kernel thermal zones exported; Pi temperature panel may be empty")
except (OSError, KeyError, ValueError) as error:
    print("INFO: thermal discovery unavailable:", type(error).__name__)

try:
    # A 24-hour window is intentional: an idling or sleeping desktop is not
    # automatically an exporter failure. It may have no recent series at all.
    age = query(
        'time() - max(timestamp('
        'sensordash_reading_value{host="' + host + '"}[24h]))'
    )
    if age:
        seconds = max(0, float(age[0]["value"][1]))
        print("INFO: last observed Windows sample age:", f"{seconds/60:.1f} minutes")
        if seconds > 300:
            print("INFO: no fresh Windows sample in 5 minutes; sleep/offline/exporter status is unknown")
    else:
        print("INFO: no Windows readings in the last 24 hours; cause not established")
except (OSError, KeyError, ValueError) as error:
    print("INFO: Windows freshness unavailable:", type(error).__name__)

if failed:
    print("Monitoring incomplete:", ", ".join(failed))
    raise SystemExit(2)
print("PASS: Pi host exporter and VictoriaMetrics storage metrics are available.")
print("These checks do not prove real DNS lookup, VPN handshake or physical fan behavior.")
PY
