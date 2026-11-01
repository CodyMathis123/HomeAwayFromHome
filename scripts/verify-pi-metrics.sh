#!/usr/bin/env bash
# Read-only end-to-end check of the actual Pi host telemetry used in Grafana.
# Works through the existing internal receiver container; VM has no LAN port.
set -euo pipefail
cd "$(dirname "$0")/.."
echo "== Pi host metrics / HomeAway System Health verification =="
docker compose --profile metrics exec -T telemetry-ingest python - <<'PY'
import json
import urllib.parse
import urllib.request

# Reach the exporter from the same private Docker bridge as VictoriaMetrics.
# When this works but the pi-node job is missing, VM likely has stale config.
exporter_ok = False
try:
    with urllib.request.urlopen("http://pi-node-exporter:9100/metrics", timeout=5) as resp:
        metrics = resp.read(2_000_001)
    if len(metrics) > 2_000_000:
        raise ValueError("exporter diagnostic response exceeded limit")
    exporter_ok = b"node_cpu_seconds_total{" in metrics
    if exporter_ok:
        print("PASS: node exporter directly reachable on Docker bridge")
    else:
        print("FAIL: node exporter reachable but CPU metrics absent")
except (OSError, ValueError) as exc:
    print("FAIL: node exporter unreachable on Docker bridge -", str(exc))

base = "http://victoria-metrics:8428/api/v1/query?"
def query(expr):
    with urllib.request.urlopen(base + urllib.parse.urlencode({"query": expr}),
                                timeout=8) as resp:
        obj = json.load(resp)
    if obj.get("status") != "success":
        raise RuntimeError("VictoriaMetrics query unsuccessful")
    return obj["data"]["result"]

checks = (
    ("node exporter scrape", 'up{job="pi-node"}', "up"),
    ("Pi CPU idle counters", 'node_cpu_seconds_total{job="pi-node",mode="idle"}', "series"),
    ("Pi RAM total", 'node_memory_MemTotal_bytes{job="pi-node"}', "series"),
    ("Pi available RAM", 'node_memory_MemAvailable_bytes{job="pi-node"}', "series"),
    ("Pi kernel boot time", 'node_boot_time_seconds{job="pi-node"}', "series"),
)
failed = []
for label, expr, kind in checks:
    try:
        results = query(expr)
        if not results:
            raise ValueError("no active series")
        if kind == "up" and not all(float(r["value"][1]) == 1 for r in results):
            raise ValueError("scrape target is down (up=0)")
        print("PASS:", label, "(" + str(len(results)) + " series)")
    except (OSError, KeyError, ValueError, RuntimeError, IndexError) as exc:
        failed.append(label)
        print("FAIL:", label, "-", str(exc))
for label, expr in (
    ("Pi thermal zones", 'node_thermal_zone_temp{job="pi-node"}'),
    ("Pi disk I/O", 'node_disk_read_bytes_total{job="pi-node"}'),
    ("VM internal metrics", 'vm_free_disk_space_bytes'),
):
    try:
        data = query(expr)
        if data:
            print("PASS:", label, "(" + str(len(data)) + " series)")
        else:
            print("INFO:", label, "- not exposed by this kernel or collector")
    except (OSError, KeyError, ValueError, RuntimeError) as exc:
        print("WARN:", label, "-", str(exc))
if failed or not exporter_ok:
    print("BLOCKED: Pi monitoring is not fully populated.")
    print("Check: sudo docker compose --profile metrics ps pi-node-exporter victoria-metrics")
    if not exporter_ok:
        print("Exporter check failed: sudo docker compose --profile metrics logs --tail=60 pi-node-exporter")
        print("Start exporter: sudo docker compose --profile metrics up -d --no-deps pi-node-exporter")
    if "node exporter scrape" in failed and exporter_ok:
        print("Exporter is healthy, but VictoriaMetrics did not successfully scrape it.")
        print("If pi-node scrape job is absent, VictoriaMetrics may have an old scrape config.")
    if failed:
        print("Reload only VM (retains history):")
        print("sudo docker compose --profile metrics up -d --no-deps --force-recreate victoria-metrics")
        print("Then rerun this verification after two scrape intervals (~60 seconds).")
    raise SystemExit(2)
print("PASS: Pi CPU, memory and uptime metrics are arriving in VictoriaMetrics.")
print("Thermal and disk series are kernel-dependent; some may legitimately be absent.")
PY
