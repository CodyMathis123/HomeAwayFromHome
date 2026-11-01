#!/usr/bin/env python3
"""Pi-host Unix socket agent. Socket permissions gate callers, inventory gates actions."""
import json
import logging
import os
import socket
import socketserver
from core import perform

SOCKET = os.getenv("PCCTL_AGENT_SOCKET", "/run/homeaway-pcctl/agent.sock")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(15)
        result = {"ok": False, "state": "error", "detail": "Invalid request"}
        try:
            line = self.rfile.readline(2049)
            if not line or len(line) > 2048 or not line.endswith(b"\n"):
                raise ValueError("Invalid or oversized request")
            message = json.loads(line)
            if not isinstance(message, dict) or set(message) != {"host", "action", "actor"}:
                raise ValueError("Invalid request fields")
            host, action, actor = message["host"], message["action"], message["actor"]
            if not all(isinstance(x, str) for x in (host, action, actor)):
                raise ValueError("Invalid request types")
            if not actor or len(actor) > 64 or not actor.replace("-", "").replace("_", "").isalnum():
                raise ValueError("Invalid actor")
            result = perform(host, action)
            logging.info("actor=%s host=%s action=%s ok=%s", actor, host, action, result["ok"])
        except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            logging.warning("Rejected control request: %s", exc)
        except Exception:
            logging.exception("Unexpected pcctl-agent failure")
        try:
            self.wfile.write((json.dumps(result, separators=(",", ":")) + "\n").encode())
        except OSError:
            pass

class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == "__main__":
    # systemd socket activation passes the bound fd (socket inode owned by pcctl).
    if int(os.getenv("LISTEN_FDS", "0")) != 1:
        raise SystemExit("Start via the pcctl-agent.socket systemd unit only")
    inherited = socket.socket(fileno=3)
    server = Server.__new__(Server)
    socketserver.BaseServer.__init__(server, SOCKET, Handler)
    server.socket = inherited
    server.server_address = SOCKET
    server.serve_forever(poll_interval=0.25)
