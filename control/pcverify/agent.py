#!/usr/bin/env python3
"""Read-only, non-destructive Windows SSH trust probe under the existing pcctl user.

The inventory writer never receives the SSH private key or access to pcctl's
power socket. A verified response means pinned host key AND authenticated
Windows forced-command "status" both succeeded. No Wake/Sleep/Restart/Shutdown
or arbitrary commands are accepted by this service.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import socketserver
import subprocess

from core import validate_hosts

SOCKET = "/run/homeaway-pcverify/agent.sock"
SSH_KEY = "/home/pcctl/.ssh/id_ed25519"
KNOWN_HOSTS = "/home/pcctl/.ssh/known_hosts"
RFC1918 = tuple(ipaddress.IPv4Network(x) for x in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
MAX_REQUEST = 1024


def probe(candidate: dict) -> dict:
    """Return a safe, bounded result; no command stderr or credential content."""
    if not isinstance(candidate, dict) or set(candidate) != {
        "id", "name", "address", "mac", "broadcast", "ssh_user", "ssh_port"
    }:
        return {"verified": False, "code": "invalid_candidate"}
    try:
        validate_hosts({"hosts": [candidate]})
        address = ipaddress.IPv4Address(candidate["address"])
        if address not in RFC1918 or candidate["ssh_user"] != "pcctl":
            return {"verified": False, "code": "invalid_candidate"}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"verified": False, "code": "invalid_candidate"}

    port = candidate["ssh_port"]
    target = candidate["address"] if port == 22 else f"[{candidate['address']}]:{port}"
    # Never implicitly trust on first use. ssh-keygen -F supports hashed
    # known_hosts and nonstandard port syntax; stdout is not forwarded.
    try:
        pin = subprocess.run(
            ["ssh-keygen", "-F", target, "-f", KNOWN_HOSTS],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if pin.returncode != 0:
            return {"verified": False, "code": "host_key_not_pinned"}
        verified = subprocess.run([
            "ssh", "-F", "/dev/null", "-i", SSH_KEY,
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", f"UserKnownHostsFile={KNOWN_HOSTS}",
            "-o", "UpdateHostKeys=no", "-o", "VerifyHostKeyDNS=no",
            "-o", "PasswordAuthentication=no",
            "-o", "KbdInteractiveAuthentication=no",
            "-o", "ConnectTimeout=5",
            "-o", "ConnectionAttempts=1",
            "-o", "ClearAllForwardings=yes",
            "-T", "-p", str(port),
            f"pcctl@{candidate['address']}", "status",
        ], capture_output=True, text=True, timeout=11, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"verified": False, "code": "ssh_unavailable"}
    if verified.returncode != 0 or verified.stdout.strip() != "ready":
        return {"verified": False, "code": "ssh_auth_or_status_failed"}
    return {"verified": True, "code": "ssh_key_and_status_verified"}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        try:
            raw = self.rfile.readline(MAX_REQUEST + 1)
            if not raw.endswith(b"\n") or len(raw) > MAX_REQUEST:
                result = {"verified": False, "code": "invalid_request"}
            else:
                request = json.loads(raw)
                if not isinstance(request, dict) or set(request) != {"operation", "candidate"} or request["operation"] != "verify":
                    result = {"verified": False, "code": "invalid_request"}
                else:
                    result = probe(request["candidate"])
        except (OSError, TypeError, ValueError, UnicodeError, KeyError):
            result = {"verified": False, "code": "invalid_request"}
        self.wfile.write((json.dumps(result, separators=(",", ":")) + "\n").encode())


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    if int(os.getenv("LISTEN_FDS", "0")) != 1:
        raise SystemExit("Start homeaway-pcverify under systemd socket activation only")
    inherited = socket.socket(fileno=3)
    server = Server.__new__(Server)
    socketserver.BaseServer.__init__(server, SOCKET, Handler)
    server.socket = inherited
    server.server_address = SOCKET
    server.serve_forever(poll_interval=0.25)


if __name__ == "__main__":
    main()
