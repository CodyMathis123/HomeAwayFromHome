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
            if operation in ("create", "create-invite"):
                msg = "Username or email is already in use"
            elif operation == "delete":
                msg = "Account changed or still has a HomeAway role; refresh and revoke before deleting"
            elif operation == "invite":
                msg = "Invitations require a pending account without a HomeAway role"
            else:
                msg = "Account changed; refresh users and try again"
            raise BrokerError(409, msg) from exc
        if exc.code == 400:
            if operation in ("create","create-invite"):
                msg = "Invalid username, display name or email"
            elif operation == "delete":
                msg = "Type the exact lowercase username to confirm deletion"
            else:
                msg = "Invalid account operation details; refresh and try again"
            raise BrokerError(400, msg) from exc
        if exc.code == 403:
            raise BrokerError(403, "Protected account or administrator permission required") from exc
        if exc.code == 404:
            raise BrokerError(404, "Account no longer exists; refresh users") from exc
        if exc.code == 413:
            raise BrokerError(413, "Account request is too large") from exc
        raise BrokerError(503, "Account broker unavailable") from exc
    except (OSError, URLError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        raise BrokerError(503, "Account broker unavailable") from exc


def computer_admin_enabled() -> bool:
    # Separate opt-in from accounts, and require live directory-role checks.
    return (account_admin_enabled() and
            os.getenv("HOMEAWAY_COMPUTER_ADMIN_ENABLED", "false").lower() == "true")


def computer_request(actor: str, operation: str, payload: dict) -> dict:
    """Send a fixed command over the dedicated, group-protected Unix socket."""
    if not computer_admin_enabled():
        raise BrokerError(404, "Computer management disabled")
    if operation not in ("list", "audit-list", "draft-create", "draft-discard", "rename", "verify", "activate",
                          "disable", "archive", "restore"):
        raise BrokerError(404, "Unsupported computer operation")
    if os.getenv("HOMEAWAY_INVENTORY_SOCKET") != "/run/homeaway-pcinventory/agent.sock":
        raise BrokerError(503, "Inventory socket misconfigured")
    message = (json.dumps({"actor": actor, "operation": operation, "payload": payload},
                          separators=(",", ":")) + "\n").encode()
    if len(message) > 4096:
        raise BrokerError(413, "Computer request too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(23 if operation in ("verify", "activate", "restore") else 5)
            conn.connect("/run/homeaway-pcinventory/agent.sock")
            conn.sendall(message)
            response = conn.makefile("rb").readline(65537)
        if not response.endswith(b"\n") or len(response) > 65536:
            raise ValueError("Invalid inventory response size")
        answer = json.loads(response)
        if not isinstance(answer, dict) or type(answer.get("ok")) is not bool:
            raise ValueError("Invalid inventory response")
        if answer["ok"]:
            if not isinstance(answer.get("result"), dict):
                raise ValueError("Invalid inventory result")
            return answer["result"]
        code = answer.get("status")
        if code not in (400, 403, 404, 409, 413):
            code = 503
        # Errors are selected/validated by the fixed host-side inventory service.
        error = answer.get("error", "")
        if not isinstance(error, str) or not 1 <= len(error) <= 120:
            error = "Inventory operation unavailable"
        raise BrokerError(code, error)
    except (OSError, ValueError, json.JSONDecodeError, TimeoutError) as exc:
        raise BrokerError(503, "Inventory service unavailable") from exc


# Only the authenticated control API may expose fixed, redacted diagnostics.
# The observer lives on a separate bridge and does not accept arbitrary queries.
DIAG_URL = "http://metrics-diagnostics:8095/v1/summary"
DIAG_CHECKS = frozenset(("pi.scrape", "pi.cpu", "pi.memory", "pi.load",
                         "pi.thermal", "vm.free"))
SERVICE_DIAG_CHECKS = frozenset(("service.pihole", "service.wireguard", "service.victoria"))
DIAG_STATES = frozenset(("observed", "attention", "unknown", "stale"))
DIAG_UNITS = frozenset(("boolean", "percent", "load", "celsius", "bytes"))
TELEM_LABEL = re.compile(r"^[A-Za-z0-9_.:/-]{1,80}$")


def diagnostics_snapshot() -> dict:
    """Bounded GET-only adapter. Never forward raw MetricsQL or credentials."""
    if os.getenv("HOMEAWAY_DIAGNOSTICS_URL") != DIAG_URL:
        raise BrokerError(503, "Diagnostics observer is not configured")
    try:
        request = Request(DIAG_URL, headers={"Accept": "application/json"}, method="GET")
        with urlopen(request, timeout=7) as response:
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError("Oversized diagnostics response")
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
            raise ValueError("Unsupported diagnostics schema")
        checked = data.get("checkedAt")
        if not isinstance(checked, str) or not 10 <= len(checked) <= 48:
            raise ValueError("Invalid check time")
        supplied = data.get("checks")
        if not isinstance(supplied, list) or len(supplied) != len(DIAG_CHECKS):
            raise ValueError("Invalid probe count")
        ids = set()
        checks = []
        for item in supplied:
            if not isinstance(item, dict):
                raise ValueError("Invalid metric entry")
            name, state, value = item.get("id"), item.get("state"), item.get("value")
            unit, label, detail = item.get("unit"), item.get("label"), item.get("detail")
            if (name not in DIAG_CHECKS or name in ids or state not in DIAG_STATES or
                    unit not in DIAG_UNITS or not isinstance(label, str) or not 1 <= len(label) <= 90 or
                    not isinstance(detail, str) or not 1 <= len(detail) <= 190 or
                    (value is not None and
                     (type(value) not in (int, float) or not -1e20 < value < 1e20))):
                raise ValueError("Invalid metric entry")
            ids.add(name)
            checks.append({"id": name, "state": state, "value": value,
                           "unit": unit, "label": label, "detail": detail,
                           "checkedAt": checked})
        raw_service_checks = data.get("serviceChecks")
        if not isinstance(raw_service_checks, list) or len(raw_service_checks) != len(SERVICE_DIAG_CHECKS):
            raise ValueError("Invalid service scrape check count")
        service_checks = []
        service_ids = set()
        for item in raw_service_checks:
            if not isinstance(item, dict):
                raise ValueError("Invalid service scrape entry")
            name, state, value = item.get("id"), item.get("state"), item.get("value")
            label, detail = item.get("label"), item.get("detail")
            if (name not in SERVICE_DIAG_CHECKS or name in service_ids or
                    state not in ("observed", "attention", "unknown") or
                    item.get("unit") != "boolean" or
                    (value is not None and (type(value) not in (int, float)
                                             or value not in (0, 1))) or
                    not isinstance(label, str) or not 1 <= len(label) <= 90 or
                    not isinstance(detail, str) or not 1 <= len(detail) <= 190):
                raise ValueError("Invalid service scrape check")
            service_ids.add(name)
            service_checks.append({"id": name, "state": state, "value": value,
                                   "unit": "boolean", "label": label,
                                   "detail": detail, "checkedAt": checked})
        section = data.get("telemetry")
        if not isinstance(section, dict) or section.get("state") not in ("observed", "unknown"):
            raise ValueError("Invalid telemetry evidence")
        hosts = section.get("hosts")
        if not isinstance(hosts, list) or len(hosts) > 16:
            raise ValueError("Invalid telemetry hosts")
        filtered_hosts = []
        seen = set()
        for item in hosts:
            if not isinstance(item, dict):
                raise ValueError("Invalid telemetry host")
            label = item.get("host")
            age = item.get("ageSeconds")
            last = item.get("lastObservedAt")
            state = item.get("state")
            if (not isinstance(label, str) or not TELEM_LABEL.fullmatch(label) or label in seen or
                    type(age) is not int or not 0 <= age <= 86460 or
                    not isinstance(last, str) or not 10 <= len(last) <= 48 or
                    state not in ("observed", "stale")):
                raise ValueError("Invalid telemetry host")
            seen.add(label)
            filtered_hosts.append({"host": label, "ageSeconds": age,
                                   "lastObservedAt": last, "state": state})
        return {"schemaVersion": 1, "checkedAt": checked,
                "source": "Internal VictoriaMetrics instant samples",
                "checks": checks,
                "serviceChecks": service_checks,
                "telemetry": {
                    "state": section["state"], "windowHours": 24,
                    "hosts": filtered_hosts,
                }}
    except (OSError, TimeoutError, ValueError, KeyError, TypeError,
            json.JSONDecodeError) as exc:
        raise BrokerError(503, "Metrics diagnostics unavailable") from exc


CERT_DIAG_URL = "http://metrics-diagnostics:8095/v1/certificates"
CERT_IDS = frozenset(("browser-home", "browser-auth", "browser-metrics", "telemetry-receiver"))
CERT_STATES = frozenset(("observed", "attention", "unknown"))
CERT_TRUST = frozenset(("verified", "not_trusted", "not_checked", "unavailable"))


def certificates_snapshot() -> dict:
    """Redact and validate fixed certificate observations; no raw PEM or keys."""
    if os.getenv("HOMEAWAY_DIAGNOSTICS_URL") != DIAG_URL:
        raise BrokerError(503, "Certificate diagnostics observer not configured")
    try:
        req = Request(CERT_DIAG_URL, headers={"Accept": "application/json"}, method="GET")
        with urlopen(req, timeout=14) as response:
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError("Oversized certificate response")
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get("schemaVersion")) is not int or data["schemaVersion"] != 1:
            raise ValueError("Invalid certificate schema")
        stamp = data.get("checkedAt")
        if not isinstance(stamp, str) or not 12 <= len(stamp) <= 48:
            raise ValueError("Missing certificate check time")
        raw_items = data.get("certificates")
        if not isinstance(raw_items, list) or len(raw_items) != len(CERT_IDS):
            raise ValueError("Invalid certificate list")
        seen = set()
        items = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise ValueError("Invalid certificate entry")
            identity = item.get("id")
            state = item.get("state")
            trust = item.get("publicTrust")
            days = item.get("daysRemaining")
            issuer = item.get("issuer")
            expires = item.get("notAfter")
            matches = item.get("hostnameMatch")
            sans = item.get("subjectAltNames")
            detail = item.get("detail")
            if (identity not in CERT_IDS or identity in seen or
                    state not in CERT_STATES or trust not in CERT_TRUST or
                    (type(days) not in (float, int) and days is not None) or
                    (days is not None and not -3650 <= days <= 3650) or
                    (issuer is not None and
                     (not isinstance(issuer, str) or len(issuer) > 160)) or
                    (expires is not None and
                     (not isinstance(expires, str) or not 12 <= len(expires) <= 48)) or
                    (type(matches) is not bool and matches is not None) or
                    not isinstance(sans, list) or len(sans) > 24 or
                    any(not isinstance(s, str) or len(s) > 180 for s in sans) or
                    not isinstance(detail, str) or not 1 <= len(detail) <= 210):
                raise ValueError("Invalid certificate entry")
            seen.add(identity)
            items.append({"id": identity, "state": state, "daysRemaining": days,
                          "notAfter": expires, "issuer": issuer,
                          "subjectAltNames": sans, "hostnameMatch": matches,
                          "publicTrust": trust, "detail": detail})
        return {"schemaVersion": 1, "checkedAt": stamp,
                "source": "Observed leaf certificate via fixed private TLS connections",
                "certificates": items}
    except (OSError, TimeoutError, ValueError, TypeError, KeyError,
            json.JSONDecodeError) as exc:
        raise BrokerError(503, "Certificate diagnostics unavailable") from exc



def maintenance_status_snapshot() -> dict:
    """Read a bounded sanitized host preflight report, never run commands."""
    fallback = {"schemaVersion": 1, "checkedAt": None,
                "mode": "preflight-only", "scheduleEnabled": False,
                "checks": [{"id": name, "state": "unknown"}
                           for name in ("backupPreflight", "retentionReview")]}
    source = Path("/backup-status/maintenance.json")
    try:
        info = source.lstat()
        if source.is_symlink() or not source.is_file() or info.st_size > 2048:
            return fallback
        data = json.loads(source.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != set(fallback)
                or data["schemaVersion"] != 1
                or data["mode"] != "preflight-only"
                or type(data["scheduleEnabled"]) is not bool
                or not isinstance(data["checkedAt"], str)
                or len(data["checkedAt"]) > 48
                or not isinstance(data["checks"], list)
                or len(data["checks"]) != 2
                or {x.get("id") for x in data["checks"] if isinstance(x, dict)}
                   != {"backupPreflight", "retentionReview"}
                or any(not isinstance(x, dict) or set(x) != {"id", "state"}
                       or x["state"] not in ("ok", "attention")
                       for x in data["checks"])):
            return fallback
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return fallback

def backup_status_snapshot() -> dict:
    """Read a fixed sanitized status file, never archive data or keys."""
    source = Path("/backup-status/status.json")
    try:
        info = source.lstat()
        if not source.is_file() or source.is_symlink() or info.st_size > 2048:
            raise ValueError("Unsafe or oversized summary")
        data = json.loads(source.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or set(data) != {
            "schemaVersion", "checkedAt", "state", "lastEncryptedBackupAt",
            "checksumVerified", "ageHours", "detail"
        } or data["schemaVersion"] != 1
            or data["state"] not in ("observed", "stale", "unknown")
            or type(data["checksumVerified"]) is not bool
            or (data["ageHours"] is not None and
                (type(data["ageHours"]) not in (int, float)
                 or not 0 <= data["ageHours"] <= 1_000_000))
            or (data["lastEncryptedBackupAt"] is not None
                and (not isinstance(data["lastEncryptedBackupAt"], str)
                     or len(data["lastEncryptedBackupAt"]) > 48))
            or not isinstance(data["checkedAt"], str)
            or len(data["checkedAt"]) > 48
            or not isinstance(data["detail"], str)
            or len(data["detail"]) > 200):
            raise ValueError("Invalid backup summary")
        return data
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {"schemaVersion": 1, "checkedAt": None, "state": "unknown",
                "lastEncryptedBackupAt": None, "checksumVerified": False,
                "ageHours": None,
                "detail": "Backup status unavailable; inspect protected archive on Pi"}


def capabilities(role: str) -> dict:
    return {
        "role": role,
        "actions": list(ROLE_ACTIONS[role]),
        "history": role == "admins",
        "manageUsers": role == "admins" and account_admin_enabled(),
        "manageComputers": role == "admins" and computer_admin_enabled(),
    }
LOCKS: dict[str, threading.Lock] = {}
RECENT: dict[str, float] = {}
GUARD = threading.RLock()
AUDIT_LOCK = threading.Lock()
SAFE_FILES = {"/control/": ("index.html", "text/html; charset=utf-8"),
              "/control/app.js": ("app.js", "text/javascript; charset=utf-8"),
              "/control/accounts.js": ("accounts.js", "text/javascript; charset=utf-8"),
              "/control/computers.js": ("computers.js", "text/javascript; charset=utf-8"),
              "/control/readiness.js": ("readiness.js", "text/javascript; charset=utf-8"),
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
        if path == "/control/api/admin/diagnostics":
            if role != "admins":
                return self._json(403, {"error": "Insufficient permissions"})
            try:
                return self._json(200, diagnostics_snapshot())
            except BrokerError as exc:
                return self._json(exc.status, {"error": str(exc)})
        if path == "/control/api/admin/maintenance":
            if role != "admins":
                return self._json(403, {"error": "Insufficient permissions"})
            return self._json(200, maintenance_status_snapshot())
        if path == "/control/api/admin/backup-status":
            if role != "admins":
                return self._json(403, {"error": "Insufficient permissions"})
            return self._json(200, backup_status_snapshot())
        if path == "/control/api/admin/certificates":
            if role != "admins":
                return self._json(403, {"error": "Insufficient permissions"})
            try:
                return self._json(200, certificates_snapshot())
            except BrokerError as exc:
                return self._json(exc.status, {"error": str(exc)})
        if path == "/control/api/admin/users":
            if not account_admin_enabled(): return self._json(404, {"error":"Not found"})
            if role != "admins":
                record_denial(actor, "-", "users")
                return self._json(403, {"error":"Insufficient permissions"})
            try:
                return self._json(200, broker_request(actor, "users", {}))
            except BrokerError as exc:
                return self._json(exc.status, {"error":str(exc)})
        if path in ("/control/api/admin/computers", "/control/api/admin/computers/audit"):
            if not computer_admin_enabled():
                return self._json(404, {"error": "Not found"})
            if role != "admins":
                return self._json(403, {"error": "Insufficient permissions"})
            operation = "audit-list" if path.endswith("/audit") else "list"
            try:
                return self._json(200, computer_request(actor, operation, {}))
            except BrokerError as exc:
                return self._json(exc.status, {"error": str(exc)})
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
            "/control/api/admin/users/create-invite",
            "/control/api/admin/users/invite",
            "/control/api/admin/users/delete",
            "/control/api/admin/users/set-role",
            "/control/api/admin/users/revoke")
        is_computer = request_path in (
            "/control/api/admin/computers/draft-create",
            "/control/api/admin/computers/draft-discard",
            "/control/api/admin/computers/verify",
            "/control/api/admin/computers/activate",
            "/control/api/admin/computers/disable",
            "/control/api/admin/computers/archive",
            "/control/api/admin/computers/restore",
            "/control/api/admin/computers/rename")
        if request_path != "/control/api/actions" and not is_account and not is_computer:
            return self._json(404, {"error": "Not found"})
        if is_computer and not computer_admin_enabled():
            return self._json(404, {"error": "Not found"})
        if is_computer and role != "admins":
            return self._json(403, {"error": "Insufficient permissions"})
        if is_account and not account_admin_enabled():
            return self._json(404, {"error": "Not found"})
        if is_account and role != "admins":
            record_denial(actor, "-", "users")
            return self._json(403, {"error": "Insufficient permissions"})
        expected_origin = os.getenv("HOMEAWAY_CONTROL_ORIGIN", "")
        alternate_origin = os.getenv("HOMEAWAY_CONTROL_ALT_ORIGIN", "")
        permitted_origins = {
            "https://homeaway.home.arpa:8443",
            "https://home.homeawayfromhome.house:8443",
        }
        trusted_origins = {expected_origin} if expected_origin in permitted_origins else set()
        if trusted_origins and alternate_origin in permitted_origins:
            trusted_origins.add(alternate_origin)
        if (self.headers.get("Origin", "") not in trusted_origins or
                self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or
                self.headers.get("X-HomeAway-Request") != "1" or
                self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"):
            return self._json(403, {"error": "Origin or request verification failed"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= (2048 if (is_account or is_computer) else 1024):
                return self._json(413, {"error": "Invalid request length"})
            data = json.loads(self.rfile.read(length))
            if is_computer:
                if not isinstance(data, dict):
                    raise ValueError("Invalid computer payload")
                operation = request_path.rsplit("/", 1)[-1]
                try:
                    return self._json(200, computer_request(actor, operation, data))
                except BrokerError as exc:
                    return self._json(exc.status, {"error": str(exc)})
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
