"""Read-only, internal-only Prometheus exporter for Pi-hole v6 summary metrics.

A private Pi-hole *application* password is used only to obtain expiring API
sessions. The exporter never exposes the SID, DNS query details, client IPs,
client names, domain names or password. No Pi-hole control endpoints are used.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PIHOLE_BASE = os.environ.get("PIHOLE_API_BASE", "http://pihole").rstrip("/")
PASSWORD_PATH = Path(os.environ.get("PIHOLE_PASSWORD_FILE", "/run/secrets/pihole_monitor_password"))
PORT = 9187
TYPE_NAME = re.compile(r"[A-Za-z0-9_]{1,32}\Z")
STAT_FIELDS = (
    ("homeaway_pihole_queries_window", ("queries", "total"), "DNS queries in the Pi-hole summary window"),
    ("homeaway_pihole_blocked_window", ("queries", "blocked"), "Blocked DNS queries in the Pi-hole summary window"),
    ("homeaway_pihole_blocked_percent", ("queries", "percent_blocked"), "Percent of DNS queries blocked, 0-100"),
    ("homeaway_pihole_cached_window", ("queries", "cached"), "DNS queries answered from cache in the summary window"),
    ("homeaway_pihole_forwarded_window", ("queries", "forwarded"), "DNS queries forwarded to upstream resolvers in the summary window"),
    ("homeaway_pihole_query_frequency", ("queries", "frequency"), "Current DNS queries per second reported by Pi-hole"),
    ("homeaway_pihole_unique_domains_window", ("queries", "unique_domains"), "Unique domains in the Pi-hole summary window"),
    ("homeaway_pihole_clients_active", ("clients", "active"), "Clients active in the Pi-hole summary window"),
    ("homeaway_pihole_clients_total", ("clients", "total"), "Total clients in the Pi-hole summary window"),
    ("homeaway_pihole_gravity_domains", ("gravity", "domains_being_blocked"), "Entries in Pi-hole gravity blocklist"),
)


def number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Expected finite nonnegative metric")
    return str(value)


def render_metrics(summary: dict, blocking: dict) -> str:
    rows = []
    for name, path, help_text in STAT_FIELDS:
        value = summary
        for key in path:
            value = value[key]
        rows.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge", f"{name} {number(value)}"))
    # GET /api/dns/blocking has a Boolean example in official Pi-hole v6
    # docs. Other deployments/clients may return enabled/disabled strings.
    # Normalize both *without* treating an unexpected response as OFF.
    status = blocking["blocking"]
    if status is True or status == "enabled":
        enabled = 1
    elif status is False or status == "disabled":
        enabled = 0
    else:
        raise ValueError("Unrecognized Pi-hole v6 blocking state")
    rows += (
        "# HELP homeaway_pihole_blocking_enabled DNS filtering enabled (1/0)",
        "# TYPE homeaway_pihole_blocking_enabled gauge",
        f"homeaway_pihole_blocking_enabled {enabled}",
    )
    types = summary.get("queries", {}).get("types", {})
    if isinstance(types, dict):
        rows.extend((
            "# HELP homeaway_pihole_query_type_window DNS queries by type in summary window",
            "# TYPE homeaway_pihole_query_type_window gauge",
        ))
        for key, value in sorted(types.items())[:32]:
            if isinstance(key, str) and TYPE_NAME.fullmatch(key):
                rows.append(f'homeaway_pihole_query_type_window{{type="{key}"}} {number(value)}')
    # No query log, client identity, DNS domain or router address is exported.
    return "\n".join(rows) + "\n"


class PiHoleV6Client:
    def __init__(self, base: str, password_file: Path, opener=None):
        self.base = base
        self.password_file = password_file
        self.opener = opener or urllib.request.urlopen
        self.sid: str | None = None
        self.lock = threading.RLock()

    def request_json(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if self.sid and method == "GET":
            headers["X-FTL-SID"] = self.sid
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        with self.opener(request, timeout=7) as response:
            body = response.read(1024 * 1024 + 1)
        if len(body) > 1024 * 1024:
            raise ValueError("Pi-hole response unexpectedly large")
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("Pi-hole response is not an object")
        return parsed

    def login(self) -> None:
        password = self.password_file.read_text(encoding="utf-8").strip()
        if not password:
            raise ValueError("Missing Pi-hole metrics app password")
        reply = self.request_json("POST", "/api/auth", {"password": password})
        session = reply.get("session")
        sid = session.get("sid") if isinstance(session, dict) else None
        if not isinstance(sid, str) or not sid:
            raise ValueError("Pi-hole API did not return an authenticated session")
        self.sid = sid

    def get(self, path: str) -> dict:
        for attempt in range(2):
            if not self.sid:
                self.login()
            try:
                return self.request_json("GET", path)
            except urllib.error.HTTPError as ex:
                if ex.code != 401 or attempt:
                    raise
                self.sid = None
        raise RuntimeError("Unable to authenticate to Pi-hole")

    def snapshot(self) -> str:
        with self.lock:
            summary = self.get("/api/stats/summary")
            blocking = self.get("/api/dns/blocking")
            return render_metrics(summary, blocking)


class Handler(BaseHTTPRequestHandler):
    client: PiHoleV6Client

    def do_GET(self) -> None:
        if self.path != "/metrics":
            return self.reply(404, b"Not found\n")
        try:
            content = self.client.snapshot().encode("ascii")
        except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError, RuntimeError) as exc:
            # Scrape failure becomes up=0 in VictoriaMetrics. Only emit a
            # safe class/HTTP status, never request headers, session IDs,
            # password files, API response bodies, or exception messages.
            import sys
            detail = f"HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            print(f"Pi-hole exporter scrape failed: {detail}", file=sys.stderr, flush=True)
            return self.reply(503, b"Pi-hole API unavailable\n")
        self.reply(200, content, "text/plain; version=0.0.4; charset=utf-8")

    def reply(self, code: int, content: bytes, mime: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        # Avoid accidentally logging SID-bearing URLs or future API details.
        pass


if __name__ == "__main__":
    Handler.client = PiHoleV6Client(PIHOLE_BASE, PASSWORD_PATH)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
