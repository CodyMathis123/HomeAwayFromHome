#!/usr/bin/env python3
"""Create credentials for a fresh, isolated cooling-only proof of concept.

Never changes any existing HomeAway installation, starts containers, or edits
network interfaces. Must run as root only for --init to set host UID ownership.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "deploy/cooling/.env"
SECRET_DIR = ROOT / "data/cooling/secrets"


def hostname(value: str) -> str:
    if len(value) > 253 or not value or any(
        not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in value.split(".")
    ):
        raise ValueError("TLS name must be an IPv4 address or plain DNS hostname.")
    return value


def bind_ip(value: str) -> str:
    ip = ipaddress.IPv4Address(value)
    if ip.is_unspecified or ip.is_multicast:
        raise ValueError("Use a specific local IPv4 address (not 0.0.0.0).")
    return str(ip)


def check_fresh_state() -> None:
    # A symlinked data parent could redirect credential creation elsewhere.
    if (ROOT / "data").is_symlink() or (ROOT / "data/cooling").is_symlink():
        raise ValueError("Symlinked cooling data parent is not supported.")
    if ENV_FILE.exists() or ENV_FILE.is_symlink():
        raise ValueError("Cooling configuration already exists; refusing to overwrite it.")
    if SECRET_DIR.exists() or SECRET_DIR.is_symlink():
        raise ValueError("Cooling secrets already exist; refusing to overwrite them.")


def openssl(*args: str) -> None:
    subprocess.run(
        ["openssl", *args],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        timeout=90,
    )


def make_tls(staging: Path, address: str) -> None:
    openssl(
        "req", "-x509", "-new", "-nodes", "-newkey", "rsa:3072",
        "-sha256", "-days", "1825",
        "-subj", "/CN=HomeAway cooling upstream local CA",
        "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", str(staging / "ca.key"), "-out", str(staging / "ca.crt"),
    )
    openssl(
        "req", "-new", "-nodes", "-newkey", "rsa:3072",
        "-sha256", "-subj", f"/CN={address}",
        "-keyout", str(staging / "server.key"), "-out", str(staging / "server.csr"),
    )
    (staging / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        f"subjectAltName=IP:{address}\n",
        encoding="ascii",
    )
    openssl(
        "x509", "-req", "-in", str(staging / "server.csr"),
        "-CA", str(staging / "ca.crt"),
        "-CAkey", str(staging / "ca.key"), "-CAcreateserial",
        "-out", str(staging / "server.crt"),
        "-days", "365", "-sha256", "-extfile", str(staging / "server.ext"),
    )
    openssl(
        "verify", "-CAfile", str(staging / "ca.crt"),
        "-verify_ip", address, str(staging / "server.crt"),
    )


def write_secret(src: Path, dest: Path, uid: int, mode: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as source, dest.open("xb") as target:
        shutil.copyfileobj(source, target)
    os.chown(dest, uid, uid if uid else 0)
    dest.chmod(mode)


def initialize(address: str, name: str, port: int) -> None:
    check_fresh_state()
    if shutil.which("openssl") is None:
        raise ValueError("OpenSSL is required to generate a scoped local TLS identity.")
    if os.geteuid() != 0:
        raise ValueError("Initialization requires sudo for scoped file permissions.")
    if not sys.stdin.isatty():
        raise ValueError("Initialization requires an interactive terminal.")
    print(
        f"Create NEW cooling-only credentials at {SECRET_DIR} and {ENV_FILE}.\n"
        f"One HTTPS listener will be configured at {address}:{port} for {name}.\n"
        "No Docker services are started. Existing HomeAway state is not touched."
    )
    if input('Type "INITIALIZE COOLING" to proceed: ').strip() != "INITIALIZE COOLING":
        raise ValueError("Cancelled without changes.")

    with tempfile.TemporaryDirectory(prefix="homeaway-cooling-") as folder:
        staging = Path(folder)
        make_tls(staging, address)
        (staging / "token").write_text(secrets.token_hex(32) + "\n", encoding="ascii")
        (staging / "grafana_admin_password").write_text(
            secrets.token_urlsafe(32) + "\n", encoding="ascii"
        )
        # Recheck immediately before writing to protect existing installations.
        check_fresh_state()
        SECRET_DIR.mkdir(parents=True, mode=0o700)
        SECRET_DIR.chmod(0o700)
        telemetry = SECRET_DIR / "telemetry"
        telemetry.mkdir(mode=0o700)
        os.chown(telemetry, 65534, 65534)
        write_secret(staging / "ca.key", SECRET_DIR / "telemetry-ca.key", 0, 0o600)
        for filename, mode in (
            ("ca.crt", 0o644),
            ("server.crt", 0o644),
            ("server.key", 0o600),
            ("token", 0o600),
        ):
            write_secret(staging / filename, telemetry / filename, 65534, mode)
        write_secret(
            staging / "grafana_admin_password",
            SECRET_DIR / "grafana_admin_password", 472, 0o600,
        )
        ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(
                "# Cooling-only proof of concept; not the normal HomeAway .env\n"
                f"COOLING_BIND_IP={address}\n"
                f"COOLING_TLS_NAME={name}\n"
                f"COOLING_HTTPS_PORT={port}\n"
            )
    print("Cooling-only credentials generated. No containers were started.")
    print("Next: validate Compose and review docs/COOLING-STANDALONE.md.")
    print("Caddy generates a SEPARATE client-facing local CA on its first start.")
    print("Import only Caddy's PUBLIC CA certificate into SensorDash trust settings.")
    print("Never share the token, private keys or Grafana password in logs/issues.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="Read-only plan (default).")
    mode.add_argument("--init", action="store_true", help="Initialize only new state.")
    p.add_argument("--bind-ip", help="Specific LAN IPv4 address of this Linux host.")
    p.add_argument("--tls-name", help="IP or DNS name clients use; defaults to --bind-ip.")
    p.add_argument("--https-port", type=int, default=9443, help="Host port (default 9443).")
    args = p.parse_args(argv)
    try:
        if args.https_port < 1024 or args.https_port > 65535:
            raise ValueError("Use an unprivileged HTTPS port between 1024 and 65535.")
        address = bind_ip(args.bind_ip) if args.bind_ip else None
        name = hostname(args.tls_name or address) if (args.tls_name or address) else None
        if args.init:
            if address is None:
                raise ValueError("--init requires --bind-ip.")
            initialize(address, name, args.https_port)
        else:
            check_fresh_state()
            print("PLAN ONLY — cooling-only installation; no Pi-hole or WireGuard.")
            print("Prerequisites: Docker Engine, Compose v2, Python 3, OpenSSL, a LAN IP.")
            print("Will create isolated data/cooling/secrets and deploy/cooling/.env.")
            print("Will NOT start containers, publish a WAN port or change existing services.")
            print("Example: sudo python3 scripts/init-cooling-standalone.py "
                  "--init --bind-ip 192.168.1.50")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
