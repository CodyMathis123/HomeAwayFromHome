"""Authenticated, component-aware read-only portal for standalone monitoring.

No management operations, DNS/VPN/PC-control queries, exposed Docker socket,
secrets in HTML/API, or arbitrary backend URLs. Caddy terminates HTTPS.
"""
from __future__ import annotations

import base64
import binascii
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
PASSWORD_FILE = Path(os.getenv("HOMEAWAY_PORTAL_PASSWORD_FILE", "/run/secrets/portal_password"))
DIAGNOSTICS_URL = "http://metrics-diagnostics:8095/v1/summary"
FEATURES = frozenset(
    item for item in os.getenv("HOMEAWAY_SELECTED_FEATURES", "").split(",")
    if item in ("cooling-monitoring", "linux-host-monitoring", "monitoring-diagnostics", "home-portal")
)
ASSETS = {
    "/": ("modular.html", "text/html; charset=utf-8"),
    "/index.html": ("modular.html", "text/html; charset=utf-8"),
    "/modular.js": ("modular.js", "text/javascript; charset=utf-8"),
    "/modular.css": ("modular.css", "text/css; charset=utf-8"),
}


def authorized(header: str | None) -> bool:
    if not header or not header.startswith("Basic ") or len(header) > 512:
        return False
    try:
        pair = base64.b64decode(header[6:], validate=True)
        if len(pair) > 256 or b":" not in pair:
            return False
        user, password = pair.split(b":", 1)
        expected = PASSWORD_FILE.read_bytes().strip()
        return (0 < len(expected) <= 256 and
                hmac.compare_digest(user, b"admin") and
                hmac.compare_digest(password, expected))
    except (OSError, ValueError, binascii.Error):
        return False


def feature_data() -> dict:
    labels = {
        "cooling-monitoring": "Hardware & cooling",
        "linux-host-monitoring": "Linux host",
        "monitoring-diagnostics": "Monitoring diagnostics",
        "home-portal": "Home portal",
    }
    return {
        "schemaVersion": 1,
        "selectedFeatures": [
            {"id": key, "label": labels[key]}
            for key in sorted(FEATURES)
        ],
        "grafanaUrl": "/",
        "readOnly": True,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAwayModularPortal/1"

    def reply(self, code: int, body: bytes, content_type: str,
              *, authenticate=False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                         "form-action 'none'")
        if authenticate:
            self.send_header("WWW-Authenticate", 'Basic realm="HomeAway monitoring portal"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if path == "/healthz":
            self.reply(200, b"ok\n", "text/plain; charset=utf-8")
            return
        if not authorized(self.headers.get("Authorization")):
            self.reply(401, b"Authentication required\n",
                       "text/plain; charset=utf-8", authenticate=True)
            return
        if path == "/api/features":
            body = json.dumps(feature_data(), separators=(",", ":")).encode()
            self.reply(200, body, "application/json; charset=utf-8")
            return
        if path == "/api/diagnostics":
            try:
                with urlopen(DIAGNOSTICS_URL, timeout=5) as r:
                    raw = r.read(65537)
                if len(raw) > 65536:
                    raise ValueError("Oversized diagnostics response")
                value = json.loads(raw)
                if not isinstance(value, dict) or value.get("schemaVersion") != 1:
                    raise ValueError("Invalid diagnostics response")
                self.reply(200, raw, "application/json; charset=utf-8")
            except (OSError, TimeoutError, ValueError, HTTPError, URLError):
                self.reply(
                    503, b'{"error":"Diagnostics currently unavailable"}',
                    "application/json; charset=utf-8"
                )
            return
        asset = ASSETS.get(path)
        if asset is None:
            self.reply(404, b"Not found\n", "text/plain; charset=utf-8")
            return
        file, mime = asset
        self.reply(200, (ROOT / file).read_bytes(), mime)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        self.reply(405, b"Method not allowed\n", "text/plain; charset=utf-8")

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST

    def log_message(self, *_args):
        # Never log inbound headers or paths (including potential URL credentials).
        pass


if __name__ == "__main__":
    if not PASSWORD_FILE.is_file() or not PASSWORD_FILE.read_bytes().strip():
        raise SystemExit("Portal authentication credential missing")
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
