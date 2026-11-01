#!/usr/bin/env python3
"""Check original private HTTPS endpoints after a gateway recreate.

This verifies only transport/SNI and an HTTP response, not browser trust
or authenticated access. Caddy's existing .home.arpa CA is internal, so TLS
verification is intentionally disabled for this *local recovery probe* only.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import sys
from pathlib import Path


def targets(config):
    gateway = config["services"]["gateway"]
    address = gateway["ports"][0]["host_ip"]
    if not ipaddress.ip_address(address).is_private:
        raise ValueError("Gateway address is not a private LAN IP")
    env = gateway["environment"]
    hosts = [env[x] for x in (
        "HOMEAWAY_DOMAIN", "HOMEAWAY_AUTH_DOMAIN", "HOMEAWAY_METRICS_DOMAIN"
    )]
    if len(set(hosts)) != 3 or not all(x.endswith(".home.arpa") for x in hosts):
        raise ValueError("Unexpected legacy SNI targets")
    return address, hosts


def probe(address, host):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((address, 8443), timeout=2.0) as tcp:
        with context.wrap_socket(tcp, server_hostname=host) as tls:
            tls.settimeout(2.0)
            request = f"HEAD / HTTP/1.1\r\nHost: {host}:8443\r\nConnection: close\r\n\r\n"
            tls.sendall(request.encode("ascii"))
            first_line = tls.recv(1024).split(b"\r\n", 1)[0]
            if not first_line.startswith(b"HTTP/1."):
                raise ValueError("No HTTP response from TLS endpoint")


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: check_dns01_gateway_https.py RESOLVED_BASE.json")
    address, hosts = targets(json.loads(Path(sys.argv[1]).read_text()))
    problems = []
    for host in hosts:
        try:
            probe(address, host)
        except (OSError, ssl.SSLError, ValueError) as exc:
            problems.append(host)
    if problems:
        print("NOT READY: private HTTPS probe failed for: " + ", ".join(problems))
        raise SystemExit(1)
    print("PASS: all three legacy .home.arpa HTTPS endpoints answer on the private Pi IP")


if __name__ == "__main__":
    main()
