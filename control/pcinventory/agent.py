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
SOCKET = "/run/homeaway-pcinventory/agent.sock"
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

    def execute(self, actor: str, op: str, payload: dict) -> dict:
        if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
            raise InventoryFault(403, "Invalid authenticated actor")
        if not isinstance(payload, dict):
            raise InventoryFault(400, "Invalid request")
        with self.lock:
            if op == "list":
                if payload:
                    raise InventoryFault(400, "Invalid list request")
                active, revision = read_inventory(self.path)
                drafts = _read_drafts()["drafts"]
                return {"active": active["hosts"], "revision": revision,
                        "drafts": [{**d, "revision": revision_of(d)} for d in drafts],
                        "activationAvailable": False}

            if op == "draft-create":
                expected = {"id", "name", "address", "mac", "broadcast", "ssh_user", "ssh_port"}
                if set(payload) != expected:
                    raise InventoryFault(400, "Invalid computer draft fields")
                try:
                    validate_hosts({"hosts": [payload]})
                except (ValueError, TypeError, KeyError, AttributeError):
                    raise InventoryFault(400, "Invalid private network or host settings")
                active, _ = read_inventory(self.path)
                state = _read_drafts()
                if len(state["drafts"]) >= MAX_DRAFTS:
                    raise InventoryFault(409, "Draft limit reached")
                if _collision(payload, active["hosts"] + state["drafts"]):
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
                current, revision = read_inventory(self.path)
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
        self.request.settimeout(8)
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
