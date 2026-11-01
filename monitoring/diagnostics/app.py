"""Narrow read-only VictoriaMetrics observer for authenticated Phase 3 diagnostics.

No credentials, host state, Docker socket or arbitrary-query API.  Each query
is a static expression against the existing internal VictoriaMetrics service.
The adapter never performs configuration changes or scrapes network peers.
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from urllib.request import urlopen

VM_QUERY = "http://victoria-metrics:8428/api/v1/query"
SCHEMA_VERSION = 1
TIMEOUT_SECONDS = 2
MAX_REPLY_BYTES = 65536
CACHE_SECONDS = 20
MAX_HOSTS = 16
HOST_LABEL = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")

# Fixed allowlist. No client may send a MetricsQL expression, host name,
# label selector, endpoint path, query-time window, or VictoriaMetrics URL.
PROBES = (
    ("pi.scrape", "Pi exporter", 'up{job="pi-node"}', "boolean"),
    ("service.pihole", "Pi-hole metrics exporter", 'up{job="pihole"}', "boolean"),
    ("service.wireguard", "WireGuard metrics exporter", 'up{job="wg-easy"}', "boolean"),
    ("service.victoria", "VictoriaMetrics metrics", 'up{job="victoria-metrics"}', "boolean"),
    ("pi.cpu", "Pi CPU utilization (5-minute rate)",
     '100 * (1 - avg(rate(node_cpu_seconds_total{job="pi-node",mode="idle"}[5m])))',
     "percent"),
    ("pi.memory", "Pi memory in use",
     '100 * (1 - node_memory_MemAvailable_bytes{job="pi-node"} / node_memory_MemTotal_bytes{job="pi-node"})',
     "percent"),
    ("pi.load", "Pi one-minute load average", 'node_load1{job="pi-node"}', "load"),
    ("pi.thermal", "Highest reported Pi thermal zone",
     'max(node_thermal_zone_temp{job="pi-node"})', "celsius"),
    ("vm.free", "VictoriaMetrics storage filesystem free",
     "vm_free_disk_space_bytes", "bytes"),
    ("telemetry.last", "Last SensorDash samples by host",
     'max by (host) (timestamp(sensordash_reading_value[24h]))', "unix"),
)
CACHE_LOCK = threading.Lock()
CACHE_UNTIL = 0.0
CACHE_RESULT: dict | None = None
CERT_CACHE_UNTIL = 0.0
CERT_CACHE_RESULT: dict | None = None


def now_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(
        epoch if epoch is not None else time.time(), timezone.utc
    ).isoformat()


def read_vector(expression: str) -> list[dict]:
    """Return bounded raw instant-vector rows; never return the query response."""
    url = VM_QUERY + "?" + urlencode({"query": expression})
    with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_REPLY_BYTES + 1)
    if len(raw) > MAX_REPLY_BYTES:
        raise ValueError("Oversized VictoriaMetrics response")
    doc = json.loads(raw)
    data = doc.get("data") if isinstance(doc, dict) else None
    if (not isinstance(data, dict) or doc.get("status") != "success" or
            data.get("resultType") != "vector"):
        raise ValueError("Invalid VictoriaMetrics response")
    rows = data.get("result")
    if not isinstance(rows, list) or len(rows) > 1024:
        raise ValueError("Unbounded metric response")
    return rows


def numeric(row: dict) -> float:
    value = float(row["value"][1])
    if not math.isfinite(value):
        raise ValueError("Invalid numeric sample")
    return value


def summarized_probe(probe_id: str, label: str, units: str,
                     rows: list[dict], checked: str) -> dict:
    base = {"id": probe_id, "label": label, "unit": units, "value": None,
            "state": "unknown", "detail": "No current metric sample",
            "checkedAt": checked, "evidence": "VictoriaMetrics instant query"}
    if not rows:
        return base
    try:
        values = [numeric(row) for row in rows]
        if len(values) != 1:
            raise ValueError("Unexpected number of metric samples")
        value = values[0]
        if units == "percent" and not 0 <= value <= 100:
            raise ValueError("Percentage outside expected bounds")
        if units == "bytes" and not 0 <= value <= 1e18:
            raise ValueError("Storage sample outside expected bounds")
        if units == "celsius" and not -40 <= value <= 200:
            raise ValueError("Thermal sample outside expected bounds")
        if units == "load" and not 0 <= value <= 100000:
            raise ValueError("Load sample outside expected bounds")
        if units == "boolean" and value not in (0, 1):
            raise ValueError("Unrecognized scrape state")
        base["value"] = round(value, 2)
        base["state"] = "attention" if units == "boolean" and value == 0 else "observed"
        base["detail"] = ("Pi exporter scrape failed" if value == 0 and units == "boolean"
                          else "Scrape endpoint responded" if units == "boolean"
                          else "Observed metric value, not a system-wide health verdict")
    except (ValueError, TypeError, KeyError, IndexError):
        base["detail"] = "Metric sample invalid or ambiguous"
    return base


def telemetry_hosts(rows: list[dict], checked_epoch: float) -> list[dict]:
    """Return at most 16 validated host labels; no sensor values or metadata."""
    seen: dict[str, float] = {}
    for row in rows:
        try:
            host = row["metric"]["host"]
            if not isinstance(host, str) or not HOST_LABEL.fullmatch(host):
                continue
            epoch = numeric(row)
            if epoch > checked_epoch + 60 or epoch < checked_epoch - 86460:
                continue
            seen[host] = max(seen.get(host, 0), epoch)
        except (ValueError, TypeError, KeyError, IndexError):
            continue
    result = []
    for host in sorted(seen)[:MAX_HOSTS]:
        age = max(0, round(checked_epoch - seen[host]))
        result.append({
            "host": host,
            "lastObservedAt": now_iso(seen[host]),
            "ageSeconds": age,
            "state": "observed" if age <= 300 else "stale",
            "detail": ("Recent sample received" if age <= 300 else
                       "No sample within five minutes; sleep/offline/exporter cause unknown"),
        })
    return result


def snapshot() -> dict:
    checked_epoch = time.time()
    checked = now_iso(checked_epoch)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [
            pool.submit(read_vector, expression)
            for _, _, expression, _ in PROBES
        ]
        results = []
        for item, future in zip(PROBES, futures):
            try:
                rows = future.result()
                results.append((item, rows, None))
            except (OSError, TimeoutError, ValueError, TypeError, KeyError, IndexError,
                    AttributeError, json.JSONDecodeError):
                results.append((item, [], "Metric query unavailable"))
    checks = []
    service_checks = []
    hosts: list[dict] = []
    telemetry_status = "unknown"
    for (probe_id, label, _, units), rows, error in results:
        if probe_id == "telemetry.last":
            if error is None:
                hosts = telemetry_hosts(rows, checked_epoch)
                telemetry_status = "observed" if hosts else "unknown"
            else:
                telemetry_status = "unknown"
            continue
        check = summarized_probe(probe_id, label, units, rows, checked)
        if error:
            check.update(state="unknown", detail=error, value=None)
        if probe_id.startswith("service."):
            # A scrape proves only that the metrics endpoint answered;
            # it cannot establish VPN handshake or Pi-hole client DNS.
            check["detail"] = (
                "Metrics endpoint was scraped" if check["state"] == "observed"
                else "Metrics scrape failed" if check["state"] == "attention"
                else "Metrics scrape evidence unavailable"
            )
            service_checks.append(check)
        else:
            checks.append(check)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "checkedAt": checked,
        "source": "VictoriaMetrics (existing internal series)",
        "checks": checks,
        "serviceChecks": service_checks,
        "telemetry": {
            "state": telemetry_status,
            "windowHours": 24,
            "detail": ("No SensorDash raw sensor samples observed in the past 24 hours"
                       if telemetry_status == "unknown"
                       else "Sample age is evidence of export only, not computer power state"),
            "hosts": hosts,
        },
    }


def cached_snapshot() -> dict:
    global CACHE_UNTIL, CACHE_RESULT
    with CACHE_LOCK:
        if CACHE_RESULT is None or time.monotonic() >= CACHE_UNTIL:
            CACHE_RESULT = snapshot()
            CACHE_UNTIL = time.monotonic() + CACHE_SECONDS
        return CACHE_RESULT


def cached_certificates() -> dict:
    # Certificate handshake evidence refreshes at most every five minutes.
    from certificates import snapshot as observe_certificates
    global CERT_CACHE_UNTIL, CERT_CACHE_RESULT
    with CACHE_LOCK:
        if CERT_CACHE_RESULT is None or time.monotonic() >= CERT_CACHE_UNTIL:
            CERT_CACHE_RESULT = observe_certificates()
            CERT_CACHE_UNTIL = time.monotonic() + 300
        return CERT_CACHE_RESULT


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAwayMetricsObserver/1"

    def do_GET(self):
        if self.path == "/v1/summary":
            source = cached_snapshot()
        elif self.path == "/v1/certificates":
            source = cached_certificates()
        else:
            self.send_error(404, "Not found")
            return
        body = json.dumps(source, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.send_error(405, "Method not allowed")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, _fmt, *_args):
        # Do not log any raw or client-provided query strings.
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8095), Handler).serve_forever()
