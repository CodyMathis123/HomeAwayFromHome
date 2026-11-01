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
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
INVENTORY = Path(os.getenv("HOMEAWAY_HOSTS_FILE", "/etc/homeaway-pcctl/hosts.json"))
SOCKET = os.getenv("PCCTL_AGENT_SOCKET", "/run/homeaway-pcctl/agent.sock")
AUDIT = Path(os.getenv("HOMEAWAY_AUDIT_FILE", "/data/events.jsonl"))
ACTIONS = frozenset(("wake", "sleep", "restart", "shutdown"))
USER_RE = re.compile(r"^[a-zA-Z0-9_.@-]{1,80}$")
HOST_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ROLE_PRIORITY = ("admins", "operators", "viewers")
ROLE_ACTIONS = {
    "admins": ("wake", "sleep", "restart", "shutdown"),
    "operators": ("wake", "sleep"),
    "viewers": (),
}


def verified_role(group_header: str) -> str | None:
    """Interpret Caddy's sanitized Authelia groups in legacy file-user mode.

    With account management enabled, LLDAP provides the current role instead.
    In both modes, an authenticated username alone never grants permission.
    """
    if not isinstance(group_header, str) or not 1 <= len(group_header) <= 512:
        return None
    groups = [part.strip() for part in group_header.split(",")]
    if len(groups) > 16 or any(not GROUP_RE.fullmatch(group) for group in groups):
        return None
    return next((role for role in ROLE_PRIORITY if role in groups), None)


def can_command(role: str, action: str) -> bool:
    return action in ROLE_ACTIONS.get(role, ())


def account_admin_enabled() -> bool:
    # Explicit deployment opt-in; standalone Production stays on the safe
    # single-user behavior. No browser input may enable this.
    return os.getenv("HOMEAWAY_ACCOUNT_ADMIN_ENABLED", "false").lower() == "true"


class BrokerError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def broker_request(actor: str, operation: str, payload: dict) -> dict:
    if not account_admin_enabled():
        raise BrokerError(404, "Account management disabled")
    address = os.getenv("HOMEAWAY_ACCOUNT_BROKER_URL", "")
    if address != "http://account-broker:8092":
        raise BrokerError(503, "Account broker configuration invalid")
    try:
        key = Path("/run/secrets/homeaway_account_broker_token").read_text(encoding="utf-8").strip()
        if len(key) < 40:
            raise ValueError("Broker credential too short")
        req = Request(address, data=json.dumps({"actor":actor,"operation":operation,
                                                 "payload":payload}).encode(),
                      headers={"Content-Type":"application/json",
                               "X-HomeAway-Broker-Key":key}, method="POST")
        with urlopen(req, timeout=3) as response:
            data = response.read(131073)
            if len(data) > 131072: raise ValueError("Oversize response")
            result = json.loads(data)
            if not isinstance(result, dict): raise ValueError("Invalid response")
            return result
    except HTTPError as exc:
        # Distinguish expected admin-side validation conflicts without leaking
        # directory internals or echoing user-controlled fields.
        if exc.code == 409:
            msg = ("Username or email is already in use" if operation == "create"
                   else "Account record changed; refresh users and try again")
            raise BrokerError(409, msg) from exc
        if exc.code == 400 and operation == "create":
            raise BrokerError(400, "Invalid username, display name, or email") from exc
        if exc.code in (400, 403, 404, 413):
            raise BrokerError(exc.code, "Account operation rejected") from exc
        raise BrokerError(503, "Account broker unavailable") from exc
    except (OSError, URLError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        raise BrokerError(503, "Account broker unavailable") from exc


def capabilities(role: str) -> dict:
    return {
        "role": role,
        "actions": list(ROLE_ACTIONS[role]),
        "history": role == "admins",
        "manageUsers": role == "admins" and account_admin_enabled(),
    }
LOCKS: dict[str, threading.Lock] = {}
RECENT: dict[str, float] = {}
GUARD = threading.RLock()
AUDIT_LOCK = threading.Lock()
SAFE_FILES = {"/control/": ("index.html", "text/html; charset=utf-8"),
              "/control/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/control/accounts.js": ("accounts.js", "text/javascript; charset=utf-8"),
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


def record_denial(actor: str, host: str, action: str) -> None:
    # Best-effort bounded audit; never reflect unknown user-controlled payloads.
    safe_host = host if isinstance(host, str) and HOST_RE.fullmatch(host) else "-"
    safe_action = action if action in ACTIONS else "invalid-action"
    try:
        record(actor, safe_host, safe_action, {"ok": False, "state": "denied"})
    except OSError:
        pass


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

    @staticmethod
    def _deny_auth(reason: str) -> None:
        # A fixed reason code only; never log request headers, username, IP,
        # group membership, cookies, tokens, or directory response bodies.
        # Useful during greenfield rollout to distinguish a Caddy handoff
        # from stale permissions or an unavailable directory.
        print("HOMEAWAY_AUTH_REJECT " + reason, flush=True)
        return None

    def _principal(self) -> tuple[str, str] | None:
        # All identity headers must be removed by Caddy before its forward_auth;
        # only verified Authelia response values enter this isolated service.
        # Do not accept a valid username alone as an authorization grant.
        actor = self.headers.get("X-HomeAway-User", "")
        if not USER_RE.fullmatch(actor):
            return self._deny_auth("missing_or_invalid_user_header")
        if account_admin_enabled():
            # Caddy's forward_auth must first authenticate the session with
            # Authelia (including two-factor policy) and copy a verified user.
            # With account management enabled, LLDAP is the *sole* authority
            # for the CURRENT role. Do not rely on an optional/stale groups
            # header from Authelia to grant permissions.
            try:
                identity = broker_request(actor, "resolve", {"user": actor})
            except BrokerError:
                return self._deny_auth("account_broker_unavailable")
            role = identity.get("role")
            if role not in ROLE_ACTIONS:
                return self._deny_auth("directory_access_denied")
            return actor, role
        # Compatibility with the original file-backed deployment: without
        # the account feature, the two-factor Authelia-provided groups header
        # continues to be required for every protected action.
        role = verified_role(self.headers.get("X-HomeAway-Groups", ""))
        if not role:
            return self._deny_auth("missing_or_invalid_role_header")
        return actor, role

    def do_GET(self):
        principal = self._principal()
        if principal is None:
            return self._json(401, {"error": "Not authenticated or not authorized"})
        actor, role = principal
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
        if path == "/control/api/me":
            return self._json(200, {"user": actor, "capabilities": capabilities(role)})
        if path == "/control/api/admin/users":
            if not account_admin_enabled(): return self._json(404, {"error":"Not found"})
            if role != "admins":
                record_denial(actor, "-", "users")
                return self._json(403, {"error":"Insufficient permissions"})
            try:
                return self._json(200, broker_request(actor, "users", {}))
            except BrokerError as exc:
                return self._json(exc.status, {"error":str(exc)})
        if path == "/control/api/hosts":
            try:
                hosts = inventory()
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(lambda x: check_status(x, actor), hosts))
                return self._json(200, {"hosts": results})
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                return self._json(503, {"error": "Computer inventory unavailable"})
        if path == "/control/api/history":
            if role != "admins":
                record_denial(actor, "-", "history")
                return self._json(403, {"error": "Insufficient permissions"})
            try:
                return self._json(200, {"events": history()})
            except (OSError, ValueError, json.JSONDecodeError):
                return self._json(503, {"error": "Audit history unavailable"})
        return self._json(404, {"error": "Not found"})

    def do_POST(self):
        principal = self._principal()
        if principal is None:
            return self._json(401, {"error": "Not authenticated or not authorized"})
        actor, role = principal
        request_path = urlsplit(self.path).path
        is_account = request_path in (
            "/control/api/admin/users/create",
            "/control/api/admin/users/set-role",
            "/control/api/admin/users/revoke")
        if request_path != "/control/api/actions" and not is_account:
            return self._json(404, {"error": "Not found"})
        if is_account and not account_admin_enabled():
            return self._json(404, {"error": "Not found"})
        if is_account and role != "admins":
            record_denial(actor, "-", "users")
            return self._json(403, {"error": "Insufficient permissions"})
        expected_origin = os.getenv("HOMEAWAY_CONTROL_ORIGIN", "")
        if (not expected_origin or self.headers.get("Origin", "") != expected_origin or
                self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or
                self.headers.get("X-HomeAway-Request") != "1" or
                self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"):
            return self._json(403, {"error": "Origin or request verification failed"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= (2048 if is_account else 1024):
                return self._json(413, {"error": "Invalid request length"})
            data = json.loads(self.rfile.read(length))
            if is_account:
                if not isinstance(data, dict):
                    raise ValueError("Invalid account payload")
                operation = request_path.rsplit("/", 1)[-1]
                try:
                    return self._json(200, broker_request(actor, operation, data))
                except BrokerError as exc:
                    return self._json(exc.status, {"error": str(exc)})
            if not isinstance(data, dict) or set(data) != {"host", "action", "confirm"}:
                raise ValueError("Invalid payload")
            host, action, confirm = data["host"], data["action"], data["confirm"]
            if not all(isinstance(v, str) for v in (host, action, confirm)):
                raise ValueError("Invalid types")
            if action in ("restart", "shutdown") and confirm != host:
                return self._json(422, {"error": "Confirm the computer ID for this action"})
            if action not in ("restart", "shutdown") and confirm:
                raise ValueError("Confirmation must be empty")
            if action not in ACTIONS:
                raise ValueError("Unknown action")
            # The local agent has fixed verbs, but must never determine user
            # permissions. Enforce the independently resolved role on every request.
            if not can_command(role, action):
                record_denial(actor, host, action)
                return self._json(403, {"error": "Insufficient permissions"})
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
