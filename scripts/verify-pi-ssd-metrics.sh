#!/usr/bin/env bash
# Verify host Pi SMART metrics via the existing internal VictoriaMetrics API.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose --profile metrics exec -T telemetry-ingest python - <<'PY'
import json
import urllib.parse
import urllib.request

base = "http://victoria-metrics:8428/api/v1/query?"
def query(expr):
    with urllib.request.urlopen(base + urllib.parse.urlencode({"query": expr}), timeout=8) as response:
        body = json.load(response)
    if body.get("status") != "success":
        raise ValueError("query failed")
    return body["data"]["result"]

checks = {
    "node-exporter scrape": ('up{job="pi-node"}', 1),
    "SMART collection": ('homeaway_pi_ssd_collection_success{job="pi-node"}', 1),
    "kernel journal collection": ('homeaway_pi_ssd_kernel_collection_success{job="pi-node"}', 1),
    "Pi filesystem free": ('homeaway_pi_root_filesystem_available_bytes{job="pi-node"}', None),
    "SMART health reported": ('homeaway_pi_ssd_smart_passed{job="pi-node"}', None),
}
failed = False
for label, (expr, want) in checks.items():
    try:
        items = query(expr)
        if not items:
            raise ValueError("missing series")
        values = [float(i["value"][1]) for i in items]
        if want is not None and any(v != want for v in values):
            raise ValueError("expected " + str(want) + ", got " + str(values))
        print("PASS:", label)
    except (OSError, ValueError, KeyError, IndexError) as exc:
        print("FAIL:", label, "-", exc)
        failed = True
if failed:
    print("Inspect homeaway-pi-ssd.service and node-exporter textfile mount.")
    raise SystemExit(2)
print("PASS: Pi SMART and filesystem readings reached VictoriaMetrics.")
PY
