"""A deliberately narrow TLS + bearer ingest gateway for SensorDash measurements.

The VictoriaMetrics HTTP API is not exposed to LAN clients. Only this endpoint
accepts writes, and only the one whitelisted metric schema can be forwarded.
"""
from __future__ import annotations

import hmac
import http.client
import os
import re
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

MAX_BODY = 256 * 1024
MAX_LINES = 4096
LINE = re.compile(
    rb"^sensordash_reading,host=[A-Za-z0-9_.:/-]{1,80},"
    rb"sensor=[A-Za-z0-9_.:/-]{1,256},"
    rb"kind=[A-Za-z0-9_]{1,40}"
    rb" value=-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)? [0-9]{13,20}$"
)
# Catalog and binding gauges describe Windows-owned configuration.
# They are never accepted as control commands. All labels are restricted to
# the same escape-free ASCII alphabet as raw sensor IDs.
H = rb"[A-Za-z0-9_.:/-]{1,80}"
S = rb"[A-Za-z0-9_.:/-]{1,256}"
K = rb"[A-Za-z0-9_]{1,40}"
R = rb"[A-Za-z0-9_-]{1,40}"
N = rb"[A-Za-z0-9_.:/-]{1,96}"
T = rb"[0-9]{13,20}"
INFO_LINE = re.compile(
    rb"^sensordash_sensor_info,host=" + H
    + rb",sensor=" + S + rb",kind=" + K
    + rb",name=" + N + rb",alias=" + N + rb",device=" + N
    + rb" value=[01] " + T + rb"$"
)
BINDING_LINE = re.compile(
    rb"^sensordash_cooling_binding,host=" + H
    + rb",sensor=" + S + rb",system=" + H
    + rb",system_name=" + N + rb",component=" + H
    + rb",component_name=" + N + rb",role=" + R
    + rb",kind=" + K + rb" value=[01] " + T + rb"$"
)

# Derived fan and pump power is archived by the Windows collector under the
# configuration revision active at the sample timestamp. No remote control.
POWER_LINE = re.compile(
    rb"^sensordash_accessory_power,host=" + H
    + rb",system=" + H + rb",component=" + H
    + rb",source=(?:measured|calibrated|curve|rated)"
    + rb",revision=[0-9a-f]{16}"
    + rb" value=-?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)? "
    + T + rb"$"
)

TOKEN_FILE = Path(os.environ.get("INGEST_TOKEN_FILE", "/run/tls/token"))
VM_HOST = os.environ.get("VICTORIAMETRICS_HOST", "victoria-metrics")
VM_PORT = 8428


def valid_payload(body: bytes) -> bool:
    if not body or len(body) > MAX_BODY or b"\r" in body:
        return False
    rows = body.strip(b"\n").split(b"\n")
    return 0 < len(rows) <= MAX_LINES and all(
        LINE.fullmatch(line) or INFO_LINE.fullmatch(line)
        or BINDING_LINE.fullmatch(line) or POWER_LINE.fullmatch(line) for line in rows
    )


def token_ok(header: str | None, expected: str) -> bool:
    if not header or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:], expected)


def forward(body: bytes) -> int:
    connection = http.client.HTTPConnection(VM_HOST, VM_PORT, timeout=8)
    try:
        connection.request(
            "POST", "/api/v2/write?precision=ms", body=body,
            headers={"Content-Type": "text/plain; charset=utf-8", "Content-Length": str(len(body))}
        )
        response = connection.getresponse()
        response.read(1024)
        return response.status
    finally:
        connection.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAwayIngest/1"
    expected_token = ""

    def reply(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/ingest" or "?" in self.path:
            return self.reply(404)
        if not token_ok(self.headers.get("Authorization"), self.expected_token):
            return self.reply(401)
        if self.headers.get("Transfer-Encoding"):
            return self.reply(400)
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not 0 < int(length) <= MAX_BODY:
            return self.reply(413)
        body = self.rfile.read(int(length))
        if not valid_payload(body):
            return self.reply(422)
        try:
            result = forward(body)
        except (OSError, TimeoutError, http.client.HTTPException):
            return self.reply(503)
        if 200 <= result < 300:
            return self.reply(204)
        return self.reply(503)

    def do_GET(self) -> None:  # noqa: N802
        self.reply(405)

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET


if __name__ == "__main__":
    secret = TOKEN_FILE.read_text(encoding="ascii").strip()
    if len(secret) < 40:
        raise RuntimeError("Ingest bearer token is missing or too short")
    Handler.expected_token = secret
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain("/run/tls/server.crt", "/run/tls/server.key")
    server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
