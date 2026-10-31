#!/usr/bin/env python3
"""Data-driven WOL and Windows power control. No shell, no arbitrary commands."""
from __future__ import annotations
import ipaddress
import json
import os
import re
import socket
import subprocess
from pathlib import Path

ACTIONS = frozenset(("status", "wake", "sleep", "restart", "shutdown"))
MUTATIONS = ACTIONS - {"status"}
HOST_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MAC_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
USER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")
DEFAULT_CONFIG = "/etc/homeaway-pcctl/hosts.json"


def load_hosts(path: str | None = None) -> dict[str, dict]:
    source = Path(path or os.getenv("HOMEAWAY_HOSTS_FILE", DEFAULT_CONFIG))
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("hosts"), list):
        raise ValueError("Inventory must contain a hosts array")
    result = {}
    for h in data["hosts"]:
        if not isinstance(h, dict):
            raise ValueError("Each host must be an object")
        name = h.get("id")
        if not isinstance(name, str) or not HOST_PATTERN.fullmatch(name) or name in result:
            raise ValueError("Host IDs must be unique, lowercase, and safe")
        label = h.get("name")
        if not isinstance(label, str) or not 1 <= len(label) <= 80 or any(ord(c) < 32 for c in label):
            raise ValueError(f"{name}: invalid display name")
        addr = ipaddress.IPv4Address(h.get("address", ""))
        broadcast = ipaddress.IPv4Address(h.get("broadcast", ""))
        if not addr.is_private or not broadcast.is_private:
            raise ValueError(f"{name}: address and broadcast must be private IPv4")
        mac = h.get("mac")
        if not isinstance(mac, str) or not MAC_PATTERN.fullmatch(mac):
            raise ValueError(f"{name}: supply a real six-byte MAC address")
        user = h.get("ssh_user", "pcctl")
        if not isinstance(user, str) or not USER_PATTERN.fullmatch(user):
            raise ValueError(f"{name}: invalid SSH username")
        port = h.get("ssh_port", 22)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError(f"{name}: invalid SSH port")
        result[name] = {"id": name, "name": label, "address": str(addr),
                        "broadcast": str(broadcast), "mac": mac.lower(),
                        "ssh_user": user, "ssh_port": port}
    if len(result) > 64:
        raise ValueError("Maximum 64 computers")
    return result


def execute(host: dict, action: str) -> dict:
    if action not in ACTIONS:
        raise ValueError("Unsupported action")
    if action == "status":
        try:
            with socket.create_connection((host["address"], host["ssh_port"]), timeout=1.5):
                return {"ok": True, "state": "online", "detail": "SSH port reachable"}
        except OSError:
            return {"ok": True, "state": "offline", "detail": "SSH port not reachable; power state unknown"}
    if action == "wake":
        magic = b"\\xff" * 6 + bytes.fromhex(host["mac"].replace(":", "")) * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(3)
            sock.sendto(magic, (host["broadcast"], 9))
        return {"ok": True, "state": "requested", "detail": "Wake-on-LAN packet sent; boot not confirmed"}
    # Windows SSH uses a non-admin account and Windows-side ForceCommand.
    key = os.getenv("PCCTL_SSH_KEY", "/home/pcctl/.ssh/id_ed25519")
    known_hosts = os.getenv("PCCTL_KNOWN_HOSTS", "/home/pcctl/.ssh/known_hosts")
    command = ["ssh", "-F", "/dev/null", "-i", key,
               "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
               "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
               "-o", "ConnectTimeout=5", "-o", "ConnectionAttempts=1",
               "-o", "ClearAllForwardings=yes", "-T",
               "-p", str(host["ssh_port"]),
               f"{host['ssh_user']}@{host['address']}", action]
    try:
        finished = subprocess.run(command, text=True, capture_output=True, timeout=12, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "state": "unknown", "detail": "SSH timed out; the action may have started"}
    if finished.returncode != 0:
        return {"ok": False, "state": "error", "detail": "Windows SSH command failed (check restricted account, host key and network)"}
    return {"ok": True, "state": "requested", "detail": f"{action.capitalize()} requested; completion not confirmed"}


def perform(host_id: str, action: str, inventory: str | None = None) -> dict:
    if action not in ACTIONS:
        raise ValueError("Unsupported action")
    hosts = load_hosts(inventory)
    if host_id not in hosts:
        raise ValueError("Unknown computer")
    result = execute(hosts[host_id], action)
    return {"host": host_id, "action": action, **result}
