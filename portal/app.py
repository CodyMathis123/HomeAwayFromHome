"""Dependency-free, read-only LAN status portal.

Health probes are intentionally narrow: DNS response and TCP admin listener
do NOT prove WireGuard client handshakes or end-to-end VPN reachability.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import struct
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
TIMEOUT = 1.5


def dns_probe(host: str, timeout: float = TIMEOUT) -> tuple[bool, str]:
    """Ask the configured DNS service to resolve example.com using UDP."""
    request_id = secrets.randbits(16)
    question = b"\x07example\x03com\x00" + struct.pack("!HH", 1, 1)
    request = struct.pack("!HHHHHH", request_id, 0x0100, 1, 0, 0, 0) + question
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(request, (host, 53))
            packet, _ = sock.recvfrom(4096)
        if len(packet) < 12:
            return False, "Invalid DNS response"
        response_id, flags = struct.unpack("!HH", packet[:4])
        if response_id != request_id or not flags & 0x8000:
            return False, "Unexpected DNS response"
        if flags & 0xF:
            return False, "DNS server returned an error"
        return True, "DNS lookup succeeded"
    except (OSError, TimeoutError):
        return False, "DNS query timed out or failed"


def tcp_probe(host: str, port: int, timeout: float = TIMEOUT) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "Service endpoint reachable"
    except OSError:
        return False, "Service endpoint unavailable"


def snapshot() -> dict:
    dns_ok, dns_message = dns_probe(os.getenv("PIHOLE_DNS_HOST", "pihole"))
    wg_ok, wg_message = tcp_probe(os.getenv("WG_ADMIN_HOST", "wg-easy"), 51821)
    kuma_ok, kuma_message = tcp_probe(os.getenv("GRAFANA_HOST", "grafana"), 3000)
    return {
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {"id": "dns", "ok": dns_ok, "label": "Pi-hole DNS", "detail": dns_message,
             "scope": "DNS service responsiveness"},
            {"id": "wireguard", "ok": wg_ok, "label": "WireGuard manager",
             "detail": wg_message if not wg_ok else "Admin listener reachable; tunnel not verified",
             "scope": "Admin listener only"},
            {"id": "monitor", "ok": kuma_ok, "label": "Grafana",
             "detail": kuma_message if kuma_ok else "Optional Grafana metrics profile may be disabled",
             "scope": "Optional HTTP service"},
        ],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAwayPortal/1"

    def _headers(self, status: int, mime: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/status":
            payload = json.dumps(snapshot(), separators=(",", ":")).encode("utf-8")
            self._headers(200, "application/json; charset=utf-8", len(payload))
            self.wfile.write(payload)
            return
        if path == "/healthz":
            payload = b"ok\n"
            self._headers(200, "text/plain; charset=utf-8", len(payload))
            self.wfile.write(payload)
            return
        if path not in FILES:
            self._headers(404, "text/plain; charset=utf-8", 0)
            return
        filename, mime = FILES[path]
        content = (ROOT / filename).read_bytes()
        self._headers(200, mime, len(content))
        self.wfile.write(content)

    def do_POST(self) -> None:  # noqa: N802
        self._headers(405, "text/plain; charset=utf-8", 0)

    do_PUT = do_POST
    do_DELETE = do_POST
    do_PATCH = do_POST


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
