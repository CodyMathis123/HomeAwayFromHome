"""Private control API; accessible ONLY behind Caddy + Authelia on the control bridge."""
from __future__ import annotations
import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent
INVENTORY = Path(os.getenv("HOMEAWAY_HOSTS_FILE", "/etc/homeaway-pcctl/hosts.json"))
SOCKET = os.getenv("PCCTL_AGENT_SOCKET", "/run/homeaway-pcctl/agent.sock")
AUDIT = Path(os.getenv("HOMEAWAY_AUDIT_FILE", "/data/events.jsonl"))
ACTIONS = frozenset(("wake", "sleep", "restart", "shutdown"))
USER_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,80}$")
HOST_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
LOCKS: dict[str, threading.Lock] = {}
RECENT: dict[str, float] = {}
GUARD = threading.RLock()
AUDIT_LOCK = threading.Lock()
SAFE_FILES = {"/control/": ("index.html", "text/html; charset=utf-8"),
              "/control/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/control/styles.css": ("styles.css", "text/css; charset=utf-8")}


def inventory() -> list[dict]:
    # Strict schema reused by host-side service; avoid exporting IP/MAC/SSH details.
    from_inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))["hosts"]
    if len(from_inventory) > 64:
        raise ValueError("Too many hosts")
    ids = set()
    hosts = []
    for host in from_inventory:
        identifier = host["id"]
        if not isinstance(identifier, str) or not HOST_RE.fullmatch(identifier) or identifier in ids:
            raise ValueError("Invalid inventory identifier")
        ids.add(identifier)
        hosts.append({"id": identifier, "name": str(host["name"])[:80]})
    return hosts


def rpc(host: str, action: str, actor: str) -> dict:
    if not isinstance(host, str) or not HOST_RE.fullmatch(host):
        raise ValueError("Invalid host")
    if action not in ACTIONS | {"status"}:
        raise ValueError("Invalid action")
    if not USER_RE.fullmatch(actor):
        raise ValueError("Invalid actor")
    payload = (json.dumps({"host": host, "action": action, "actor": actor}) + "\n").encode()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(16)
        conn.connect(SOCKET)
        conn.sendall(payload)
        stream = conn.makefile("rb")
        reply = stream.readline(4097)
    if not reply.endswith(b"\n") or len(reply) > 4096:
        raise OSError("Invalid pcctl-agent reply")
    result = json.loads(reply)
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise ValueError("Invalid agent response")
    return result


def record(actor: str, host: str, action: str, result: dict) -> None:
    entry = {"time": datetime.now(timezone.utc).isoformat(), "actor": actor,
             "host": host, "action": action, "ok": result["ok"],
             "state": result.get("state", "unknown")}
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK:
        with AUDIT.open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry, separators=(",", ":")) + "\n")
        # A bounded event log (rotate at 1 MB); logs contain user/host/action, no tokens.
        if AUDIT.stat().st_size > 1_000_000:
            backup = AUDIT.with_suffix(".old")
            AUDIT.replace(backup)


def history(limit: int = 30) -> list[dict]:
    if not AUDIT.exists():
        return []
    with AUDIT_LOCK:
        with AUDIT.open(encoding="utf-8") as data:
            lines = data.readlines()[-min(limit, 50):]
    return [json.loads(line) for line in reversed(lines) if line.strip()]


def apply_action(host: str, action: str, actor: str) -> dict:
    if action not in ACTIONS or host not in {x["id"] for x in inventory()}:
        raise ValueError("Unknown computer or command")
    with GUARD:
        lock = LOCKS.setdefault(host, threading.Lock())
    if not lock.acquire(blocking=False):
        return {"ok": False, "state": "busy", "detail": "Another command is in progress"}
    try:
        with GUARD:
            now = time.monotonic()
            if now - RECENT.get(host, -100000) < 10:
                return {"ok": False, "state": "cooldown", "detail": "Another command was recently submitted"}
            RECENT[host] = now
        try:
            outcome = rpc(host, action, actor)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            outcome = {"ok": False, "state": "unknown", "detail": "Control agent unreachable; outcome unknown"}
        record(actor, host, action, outcome)
        return outcome
    finally:
        lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "HomeAwayControl/1"

    def _headers(self, code: int, mime: str, length: int):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()

    def _json(self, status: int, body: dict | list):
        content = json.dumps(body, separators=(",", ":")).encode()
        self._headers(status, "application/json; charset=utf-8", len(content))
        self.wfile.write(content)

    def _actor(self) -> str | None:
        # Proxy MUST be the only network path. Caddy removes client-supplied
        # Remote-User before forward_auth and injects verified identity.
        actor = self.headers.get("X-HomeAway-User", "")
        return actor if USER_RE.fullmatch(actor) else None

    def do_GET(self):
        actor = self._actor()
        if actor is None:
            return self._json(401, {"error": "Not authenticated"})
        path = urlsplit(self.path).path
        if path == "/control":
            self.send_response(308)
            self.send_header("Location", "/control/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path in SAFE_FILES:
            file, mime = SAFE_FILES[path]
            content = (ROOT / file).read_bytes()
            self._headers(200, mime, len(content))
            self.wfile.write(content)
            return
        if path == "/control/api/hosts":
            try:
                hosts = inventory()
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda x: check_status(x, actor), hosts))
                return self._json(200, {"hosts": results})
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                return self._json(503, {"error": "Computer inventory unavailable"})
        if path == "/control/api/history":
            try:
                return self._json(200, {"events": history()})
            except (OSError, ValueError, json.JSONDecodeError):
                return self._json(503, {"error": "Audit history unavailable"})
        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        actor = self._actor()
        if actor is None:
            return self._json(401, {"error": "Not authenticated"})
        if urlsplit(self.path).path != "/control/api/actions":
            return self._json(404, {"error": "Not found"})
        expected_origin = os.getenv("HOMEAWAY_CONTROL_ORIGIN", "")
        if (not expected_origin or self.headers.get("Origin", "") != expected_origin or
                self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or
                self.headers.get("X-HomeAway-Request") != "1" or
                self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"):
            return self._json(403, {"error": "Origin or request verification failed"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 1024:
                return self._json(413, {"error": "Invalid request length"})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {"host", "action", "confirm"}:
                raise ValueError("Invalid payload")
            host, action, confirm = data["host"], data["action"], data["confirm"]
            if not all(isinstance(v, str) for v in (host, action, confirm)):
                raise ValueError("Invalid types")
            if action in ("restart", "shutdown") and confirm != host:
                return self._json(422, {"error": "Confirm the computer ID for this action"})
            if action not in ("restart", "shutdown") and confirm:
                raise ValueError("Confirmation must be empty")
            result = apply_action(host, action, actor)
            return self._json(200 if result["ok"] else 503, result)
        except (ValueError, KeyError, json.JSONDecodeError):
            return self._json(400, {"error": "Invalid computer or action"})

    def do_PUT(self): self._json(405, {"error": "Method not allowed"})
    do_DELETE = do_PUT
    do_PATCH = do_PUT


def check_status(host: dict, actor: str) -> dict:
    try:
        outcome = rpc(host["id"], "status", actor)
        return {**host, "state": outcome.get("state", "unknown"),
                "detail": outcome.get("detail", "No status")}
    except (OSError, ValueError, TimeoutError):
        return {**host, "state": "unknown", "detail": "Host agent unavailable"}


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
