#!/usr/bin/env python3
"""Narrow Pi-host inventory RPC. Never accepts shell, paths, SSH keys or power verbs.

The web controller cannot write /etc/homeaway-pcctl; only this socket-activated
service can update the single live hosts.json. A draft is NOT a power target.
"""
from __future__ import annotations

import copy
import json
import os
import re
import socket
import socketserver
import stat
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from core import validate_hosts
from inventory_store import InventoryConflict, InventoryStoreError, read_inventory, revision_of, update_inventory

HOSTS = Path(os.getenv("HOMEAWAY_HOSTS_FILE", "/etc/homeaway-pcctl/hosts.json"))
STATE_DIR = Path(os.getenv("HOMEAWAY_INVENTORY_STATE", "/var/lib/homeaway-pcinventory"))
DRAFTS = STATE_DIR / "drafts.json"
AUDIT = STATE_DIR / "audit.jsonl"
RETIRED = STATE_DIR / "retired.json"
MAX_RETIRED = 128
SOCKET = "/run/homeaway-pcinventory/agent.sock"
VERIFY_SOCKET = "/run/homeaway-pcverify/agent.sock"
ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ACTOR = re.compile(r"^[a-zA-Z0-9_.@-]{1,80}$")
MAX_MESSAGE = 4096
MAX_DRAFTS = 64


class InventoryFault(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _read_drafts() -> dict:
    try:
        fd = os.open(DRAFTS, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return {"drafts": []}
    except OSError as exc:
        raise InventoryFault(503, "Draft registry cannot be read safely") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InventoryFault(503, "Invalid draft registry")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise InventoryFault(503, "Draft registry too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"drafts"} or not isinstance(data["drafts"], list):
            raise ValueError("bad draft schema")
        if len(data["drafts"]) > MAX_DRAFTS:
            raise ValueError("too many drafts")
        ids = set()
        for entry in data["drafts"]:
            if not isinstance(entry, dict) or set(entry) != {
                "id", "name", "address", "mac", "broadcast", "ssh_user", "ssh_port",
            }:
                raise ValueError("bad draft entry")
            validate_hosts({"hosts": [entry]})
            if entry["id"] in ids:
                raise ValueError("duplicate draft")
            ids.add(entry["id"])
        return data
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise InventoryFault(503, "Invalid draft registry; manual recovery required") from exc
    finally:
        os.close(fd)


def _save_drafts(data: dict) -> None:
    # Always called under InventoryManager's process lock. One dedicated
    # systemd instance only; no other editor may modify draft state.
    if DRAFTS.is_symlink() or STATE_DIR.is_symlink():
        raise InventoryFault(503, "Unsafe draft state path")
    payload = (json.dumps(data, indent=2) + "\n").encode()
    if len(payload) > 65536:
        raise InventoryFault(413, "Draft registry too large")
    fd, name = tempfile.mkstemp(prefix=".drafts-", dir=STATE_DIR)
    staged = Path(name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, DRAFTS)
        directory_fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        staged.unlink(missing_ok=True)


def _read_retired() -> dict:
    """Strict, separate retirement registry; never directly drives power actions."""
    try:
        fd = os.open(RETIRED, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return {"retired": []}
    except OSError as exc:
        raise InventoryFault(503, "Retirement records cannot be read safely") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise InventoryFault(503, "Invalid retirement registry")
        with os.fdopen(fd, "rb", closefd=False) as reader:
            raw = reader.read(65537)
        if len(raw) > 65536:
            raise InventoryFault(503, "Retirement registry too large")
        state = json.loads(raw)
        if (not isinstance(state, dict) or set(state) != {"retired"} or
                not isinstance(state["retired"], list) or len(state["retired"]) > MAX_RETIRED):
            raise ValueError("Invalid retirement schema")
        ids = set()
        for record in state["retired"]:
            if (not isinstance(record, dict) or set(record) !=
                    {"host", "archived", "retiredAt", "retiredBy"} or
                    type(record["archived"]) is not bool or
                    not isinstance(record["retiredAt"], str) or
                    len(record["retiredAt"]) > 48 or
                    not isinstance(record["retiredBy"], str) or
                    not ACTOR.fullmatch(record["retiredBy"]) or
                    not isinstance(record["host"], dict) or
                    set(record["host"]) !=
                    {"id", "name", "address", "mac", "broadcast", "ssh_user", "ssh_port"}):
                raise ValueError("Invalid retirement record")
            validate_hosts({"hosts": [record["host"]]})
            if record["host"]["id"] in ids:
                raise ValueError("Duplicate retired ID")
            ids.add(record["host"]["id"])
        return state
    except (ValueError, TypeError, KeyError, UnicodeError, AttributeError) as exc:
        raise InventoryFault(503, "Retirement registry damaged; manual recovery required") from exc
    finally:
        os.close(fd)


def _save_retired(state: dict) -> None:
    """Atomic private archive snapshot, separate from the command registry."""
    if RETIRED.is_symlink() or STATE_DIR.is_symlink():
        raise InventoryFault(503, "Unsafe retirement registry path")
    payload = (json.dumps(state, indent=2, ensure_ascii=False) + "\n").encode()
    if len(payload) > 65536:
        raise InventoryFault(413, "Retirement registry too large")
    fd, temp_name = tempfile.mkstemp(prefix=".retired-", dir=STATE_DIR)
    staged = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as writer:
            fd = -1
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(staged, RETIRED)
        directory_fd = os.open(STATE_DIR, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        staged.unlink(missing_ok=True)


def _retired_record(payload: dict, state: dict) -> dict:
    """Return record only if the user has read its current exact revision."""
    if (set(payload) != {"id", "revision", "confirm"} or
            not isinstance(payload.get("id"), str) or
            not ID.fullmatch(payload["id"]) or
            not isinstance(payload.get("revision"), str) or
            not re.fullmatch(r"[0-9a-f]{24}", payload["revision"]) or
            payload["confirm"] != payload["id"]):
        raise InventoryFault(400, "Confirm the exact computer ID and refresh")
    record = next((r for r in state["retired"] if r["host"]["id"] == payload["id"]), None)
    if record is None:
        raise InventoryFault(404, "Retired computer not found")
    if revision_of(record) != payload["revision"]:
        raise InventoryFault(409, "Retirement record changed; refresh")
    return record


def _audit(actor: str, target: str, operation: str, outcome: str) -> None:
    # Audit names and outcome only: never network details or credentials.
    line = json.dumps({
        "time": datetime.now(timezone.utc).isoformat(),
        "actor": actor, "target": target,
        "operation": operation, "outcome": outcome,
    }, separators=(",", ":")) + "\n"
    try:
        fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                     os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe inventory audit")
            os.fchmod(fd, 0o600)
            # Bound the audit log to a current and previous ~1MB generation.
            # Rotation happens before the next write under the manager lock.
            if info.st_size > 1_000_000:
                os.close(fd)
                fd = -1
                os.replace(AUDIT, STATE_DIR / "audit.previous.jsonl")
                fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                             os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                os.fchmod(fd, 0o600)
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError as exc:
        raise InventoryFault(503, "Inventory audit unavailable") from exc


def _probe(host: dict) -> dict:
    """Require pin-verified, authenticated Windows forced-command status.

    The inventory user does not read pcctl's key or known_hosts. The separate
    pcctl-user verifier is socket ACL gated and always runs a fixed "status".
    """
    payload = (json.dumps({"operation": "verify", "candidate": host},
                          separators=(",", ":")) + "\n").encode()
    if len(payload) > 1024:
        raise InventoryFault(400, "Invalid verifier payload")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(16)
            conn.connect(VERIFY_SOCKET)
            conn.sendall(payload)
            result = conn.makefile("rb").readline(1025)
        if not result.endswith(b"\n") or len(result) > 1024:
            raise ValueError("Invalid verifier reply")
        outcome = json.loads(result)
        if not isinstance(outcome, dict) or set(outcome) != {"verified", "code"} or type(outcome["verified"]) is not bool:
            raise ValueError("Invalid verifier schema")
        if outcome["verified"] and outcome["code"] == "ssh_key_and_status_verified":
            return {"verified": True, "code": outcome["code"]}
        code = outcome["code"] if outcome["code"] in (
            "host_key_not_pinned", "ssh_auth_or_status_failed",
            "ssh_unavailable", "invalid_candidate"
        ) else "verification_failed"
        return {"verified": False, "code": code}
    except (OSError, TimeoutError, ValueError, UnicodeError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise InventoryFault(503, "SSH verifier unavailable") from exc


def _collision(entry: dict, existing: list[dict]) -> bool:
    return any(
        item["id"] == entry["id"] or item["address"] == entry["address"] or
        item["mac"].casefold() == entry["mac"].casefold()
        for item in existing
    )


class InventoryManager:
    def __init__(self, inventory_path: Path = HOSTS):
        self.path = Path(inventory_path)
        self.lock = threading.RLock()

    def _active(self) -> tuple[dict, str]:
        # Unlike the standalone add-pc CLI, the live management service must
        # never silently treat a missing/unsafe active registry as empty.
        if self.path.is_symlink() or not self.path.is_file():
            raise InventoryFault(503, "Active computer registry unavailable")
        return read_inventory(self.path)

    @staticmethod
    def _draft_with_revision(payload: dict, state: dict) -> dict:
        if (set(payload) not in ({"id", "revision"}, {"id", "revision", "inventoryRevision", "confirm"}) or
                not isinstance(payload.get("id"), str) or
                not ID.fullmatch(payload["id"]) or
                not isinstance(payload.get("revision"), str) or
                not re.fullmatch(r"[0-9a-f]{24}", payload["revision"])):
            raise InventoryFault(400, "Invalid draft revision or identifier")
        draft = next((d for d in state["drafts"] if d["id"] == payload["id"]), None)
        if draft is None:
            raise InventoryFault(404, "Draft not found")
        if revision_of(draft) != payload["revision"]:
            raise InventoryFault(409, "Draft changed; refresh before retry")
        return draft

    def execute(self, actor: str, op: str, payload: dict) -> dict:
        if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
            raise InventoryFault(403, "Invalid authenticated actor")
        if not isinstance(payload, dict):
            raise InventoryFault(400, "Invalid request")
        with self.lock:
            if op == "list":
                if payload:
                    raise InventoryFault(400, "Invalid list request")
                active, revision = self._active()
                drafts = _read_drafts()["drafts"]
                retired = _read_retired()["retired"]
                active_ids = {h["id"] for h in active["hosts"]}
                return {"active": [
                            {**host, "lifecyclePending": host["id"] in
                             {r["host"]["id"] for r in retired}}
                            for host in active["hosts"]],
                        "revision": revision,
                        "drafts": [{**d, "revision": revision_of(d),
                                    "cleanupPending": d["id"] in active_ids}
                                   for d in drafts],
                        "retired": [{**r, "revision": revision_of(r),
                                    "cleanupPending": r["host"]["id"] in active_ids}
                                   for r in retired],
                        "activationAvailable": True}

            if op == "draft-create":
                expected = {"id", "name", "address", "mac", "broadcast", "ssh_user", "ssh_port"}
                if set(payload) != expected:
                    raise InventoryFault(400, "Invalid computer draft fields")
                try:
                    validate_hosts({"hosts": [payload]})
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise InventoryFault(400, "Invalid private network or host settings")
                active, _ = self._active()
                state = _read_drafts()
                if len(state["drafts"]) >= MAX_DRAFTS:
                    raise InventoryFault(409, "Draft limit reached")
                retired = [r["host"] for r in _read_retired()["retired"]]
                if _collision(payload, active["hosts"] + state["drafts"] + retired):
                    raise InventoryFault(409, "ID, IP address or MAC already used")
                _audit(actor, payload["id"], op, "requested")
                state["drafts"].append(copy.deepcopy(payload))
                _save_drafts(state)
                _audit(actor, payload["id"], op, "committed")
                return {"draft": {**payload, "revision": revision_of(payload)},
                        "activationAvailable": False}

            if op == "draft-discard":
                if set(payload) != {"id", "revision"} or not isinstance(payload.get("id"), str) or not ID.fullmatch(payload["id"]):
                    raise InventoryFault(400, "Invalid draft identifier")
                state = _read_drafts()
                draft = next((d for d in state["drafts"] if d["id"] == payload["id"]), None)
                if draft is None:
                    raise InventoryFault(404, "Draft not found")
                if payload["revision"] != revision_of(draft):
                    raise InventoryFault(409, "Draft changed; refresh before retry")
                _audit(actor, draft["id"], op, "requested")
                state["drafts"] = [d for d in state["drafts"] if d["id"] != draft["id"]]
                _save_drafts(state)
                _audit(actor, draft["id"], op, "committed")
                return {"discarded": draft["id"]}

            if op in ("verify", "activate"):
                if op == "verify" and set(payload) != {"id", "revision"}:
                    raise InventoryFault(400, "Invalid verify fields")
                if op == "activate":
                    if set(payload) != {"id", "revision", "inventoryRevision", "confirm"}:
                        raise InventoryFault(400, "Invalid activation fields")
                    if (not isinstance(payload.get("inventoryRevision"), str) or
                            not re.fullmatch(r"[0-9a-f]{24}", payload["inventoryRevision"]) or
                            payload.get("confirm") != payload.get("id")):
                        raise InventoryFault(400, "Type the exact computer ID to activate")
                state = _read_drafts()
                draft = self._draft_with_revision(payload, state)
                active, revision = self._active()
                if _collision(draft, active["hosts"] +
                              [r["host"] for r in _read_retired()["retired"]]):
                    raise InventoryFault(409, "Computer ID, IP or MAC is already active or reserved")
                if len(active["hosts"]) >= 64:
                    raise InventoryFault(409, "Active inventory is full")
                if op == "activate" and payload["inventoryRevision"] != revision:
                    raise InventoryFault(409, "Active inventory changed; refresh before activating")
                # Fresh trust check for BOTH operations; a previous successful
                # verify button or UI data is never an activation token.
                checked = _probe(draft)
                if op == "verify":
                    return {"id": draft["id"], **checked}
                if not checked["verified"]:
                    raise InventoryFault(409, "SSH verification failed: " + checked["code"])

                _audit(actor, draft["id"], op, "requested")

                def promote(document: dict) -> dict:
                    if _collision(draft, document["hosts"]):
                        raise InventoryFault(409, "Computer ID, IP or MAC already active")
                    document["hosts"].append(copy.deepcopy(draft))
                    return document

                try:
                    _, final_revision = update_inventory(
                        self.path, promote, expected_revision=payload["inventoryRevision"])
                except InventoryConflict as exc:
                    raise InventoryFault(409, "Inventory changed; refresh before activating") from exc

                # The active file is the only command authority. If the
                # separate draft-state cleanup fails, report the committed
                # activation and allow explicit, safe draft discard later.
                cleanup_pending = False
                state["drafts"] = [d for d in state["drafts"] if d["id"] != draft["id"]]
                try:
                    _save_drafts(state)
                except (OSError, InventoryFault):
                    cleanup_pending = True
                try:
                    _audit(actor, draft["id"], op, "committed")
                except InventoryFault:
                    cleanup_pending = True
                return {"activated": draft["id"], "revision": final_revision,
                        "cleanupPending": cleanup_pending,
                        "verification": checked["code"]}

            if op == "disable":
                if (set(payload) != {"id", "revision", "confirm"} or
                        not isinstance(payload.get("id"), str) or
                        not ID.fullmatch(payload["id"]) or
                        payload["confirm"] != payload["id"] or
                        not isinstance(payload.get("revision"), str) or
                        not re.fullmatch(r"[0-9a-f]{24}", payload["revision"])):
                    raise InventoryFault(400, "Confirm exact computer ID and inventory revision")
                active, revision = self._active()
                if revision != payload["revision"]:
                    raise InventoryFault(409, "Active inventory changed; refresh")
                current = next((h for h in active["hosts"] if h["id"] == payload["id"]), None)
                if current is None:
                    raise InventoryFault(404, "Computer is not active")
                state = _read_retired()
                record = next((r for r in state["retired"] if r["host"]["id"] == payload["id"]), None)
                # Legacy pcctl inventories may omit ssh_user and ssh_port.
                # Canonicalize only the private recovery snapshot; never
                # rewrite a live row until the user explicitly restores it.
                canonical = validate_hosts({"hosts": [current]})[current["id"]]
                if record is not None and record["host"] != canonical:
                    raise InventoryFault(409, "Retirement snapshot conflicts; manual review required")
                if record is None:
                    if len(state["retired"]) >= MAX_RETIRED:
                        raise InventoryFault(409, "Retirement registry is full")
                    _audit(actor, current["id"], op, "requested")
                    record = {"host": canonical, "archived": False,
                              "retiredAt": datetime.now(timezone.utc).isoformat(),
                              "retiredBy": actor}
                    # Commit recovery snapshot BEFORE touching the active file.
                    # If the actual removal fails, this entry remains visibly
                    # pending and retrying Disable will safely finish removal.
                    state["retired"].append(record)
                    _save_retired(state)
                else:
                    _audit(actor, current["id"], op, "retry")

                def remove(document: dict) -> dict:
                    if not any(h["id"] == current["id"] and h == current
                               for h in document["hosts"]):
                        raise InventoryFault(409, "Computer changed; refresh")
                    document["hosts"] = [h for h in document["hosts"]
                                         if h["id"] != current["id"]]
                    return document

                try:
                    _, final_revision = update_inventory(
                        self.path, remove, expected_revision=payload["revision"])
                except InventoryConflict as exc:
                    raise InventoryFault(409, "Inventory changed; refresh") from exc
                # This is the commit point: the pcctl agent and Shortcut can no
                # longer route newly requested actions to the retired host.
                try:
                    _audit(actor, current["id"], op, "committed")
                    audited = True
                except InventoryFault:
                    audited = False
                return {"disabled": current["id"], "revision": final_revision,
                        "archived": False, "auditPending": not audited}

            if op in ("archive", "restore"):
                allowed = {"id", "revision", "confirm"}
                if op == "restore":
                    allowed.add("inventoryRevision")
                if set(payload) != allowed:
                    raise InventoryFault(400, "Invalid lifecycle request fields")
                state = _read_retired()
                record = _retired_record({k: payload[k] for k in
                                          ("id", "revision", "confirm")}, state)
                current, revision = self._active()
                uid = record["host"]["id"]
                in_active = next((h for h in current["hosts"] if h["id"] == uid), None)

                if in_active is not None:
                    raise InventoryFault(409, "Computer is still active")
                if op == "archive":
                    if record["archived"]:
                        return {"archived": uid, "revision": revision_of(record)}
                    _audit(actor, uid, op, "requested")
                    record["archived"] = True
                    _save_retired(state)
                    _audit(actor, uid, op, "committed")
                    return {"archived": uid, "revision": revision_of(record)}

                # Restoring never trusts an old connection/fingerprint.
                if (not isinstance(payload.get("inventoryRevision"), str) or
                        not re.fullmatch(r"[0-9a-f]{24}", payload["inventoryRevision"]) or
                        payload["inventoryRevision"] != revision):
                    raise InventoryFault(409, "Active inventory changed; refresh")
                if _collision(record["host"], current["hosts"] +
                              _read_drafts()["drafts"]):
                    raise InventoryFault(409, "ID, address or MAC already in use")
                if len(current["hosts"]) >= 64:
                    raise InventoryFault(409, "Active computer limit reached")
                checked = _probe(record["host"])
                if not checked["verified"]:
                    raise InventoryFault(409, "Restore requires verified SSH: " + checked["code"])
                _audit(actor, uid, op, "requested")

                def restore(document: dict) -> dict:
                    if _collision(record["host"], document["hosts"]):
                        raise InventoryFault(409, "Computer identity is in use")
                    document["hosts"].append(copy.deepcopy(record["host"]))
                    return document

                try:
                    _, final_revision = update_inventory(
                        self.path, restore, expected_revision=payload["inventoryRevision"])
                except InventoryConflict as exc:
                    raise InventoryFault(409, "Inventory changed; refresh") from exc
                # The restored PC is now active. Failure to clear the private
                # record must not cause a retry to report the action as failed.
                cleanup_pending = False
                state["retired"] = [r for r in state["retired"]
                                    if r["host"]["id"] != uid]
                try:
                    _save_retired(state)
                except (OSError, InventoryFault):
                    cleanup_pending = True
                try:
                    _audit(actor, uid, op, "committed")
                except InventoryFault:
                    cleanup_pending = True
                return {"restored": uid, "revision": final_revision,
                        "cleanupPending": cleanup_pending,
                        "verification": checked["code"]}

            if op == "rename":
                if set(payload) != {"id", "name", "revision"}:
                    raise InventoryFault(400, "Invalid rename fields")
                name = payload["name"]
                uid = payload["id"]
                if not isinstance(uid, str) or not ID.fullmatch(uid) or not isinstance(name, str):
                    raise InventoryFault(400, "Invalid computer identity")
                if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
                    raise InventoryFault(400, "Invalid computer name")
                if not isinstance(payload["revision"], str) or not re.fullmatch(r"[0-9a-f]{24}", payload["revision"]):
                    raise InventoryFault(400, "Invalid inventory revision")

                def rename(document: dict) -> dict:
                    target = next((h for h in document["hosts"] if h["id"] == uid), None)
                    if target is None:
                        raise InventoryFault(404, "Computer not found")
                    target["name"] = name
                    return document

                # Read first to avoid auditing an unchanged name. The same
                # optimistic revision must then match inside the writer lock.
                current, revision = self._active()
                if revision != payload["revision"]:
                    raise InventoryFault(409, "Inventory changed; refresh")
                target = next((h for h in current["hosts"] if h["id"] == uid), None)
                if target is None:
                    raise InventoryFault(404, "Computer not found")
                if target["name"] == name:
                    return {"revision": revision, "renamed": uid}
                _audit(actor, uid, op, "requested")
                try:
                    _, final_revision = update_inventory(
                        self.path, rename, expected_revision=payload["revision"])
                except InventoryConflict as exc:
                    raise InventoryFault(409, "Inventory changed; refresh") from exc
                _audit(actor, uid, op, "committed")
                return {"revision": final_revision, "renamed": uid}

            raise InventoryFault(404, "Unsupported inventory operation")


class Handler(socketserver.StreamRequestHandler):
    manager: InventoryManager

    def handle(self):
        self.request.settimeout(20)
        try:
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE:
                raise InventoryFault(413, "Invalid request size")
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"actor", "operation", "payload"}:
                raise InventoryFault(400, "Invalid request")
            result = self.manager.execute(data["actor"], data["operation"], data["payload"])
            response = {"ok": True, "result": result}
        except InventoryFault as exc:
            response = {"ok": False, "status": exc.status, "error": str(exc)}
        except (InventoryConflict, InventoryStoreError, OSError):
            response = {"ok": False, "status": 503, "error": "Inventory service unavailable"}
        except (ValueError, TypeError, KeyError, UnicodeError):
            response = {"ok": False, "status": 400, "error": "Invalid inventory request"}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode())


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    if int(os.getenv("LISTEN_FDS", "0")) != 1:
        raise SystemExit("Inventory agent must run under systemd socket activation")
    if not STATE_DIR.is_dir() or STATE_DIR.is_symlink():
        raise SystemExit("Inventory state directory missing or unsafe")
    Handler.manager = InventoryManager()
    inherited = socket.socket(fileno=3)
    server = Server.__new__(Server)
    socketserver.BaseServer.__init__(server, SOCKET, Handler)
    server.socket = inherited
    server.server_address = SOCKET
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
