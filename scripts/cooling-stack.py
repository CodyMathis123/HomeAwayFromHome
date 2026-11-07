#!/usr/bin/env python3
"""Cooling-only backend operator CLI.

Release payload is code-only. Runtime configuration and all private data are
outside /opt/homeaway-cooling for .deb installs. Nothing starts on installation.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = ROOT == Path("/opt/homeaway-cooling")
CONFIG = Path("/etc/homeaway-cooling/cooling.env") if PACKAGED else ROOT / "deploy/cooling/.env"
STATE = Path("/var/lib/homeaway-cooling") if PACKAGED else ROOT / "data/cooling"
COMPOSE = ROOT / "compose.cooling-only.yaml"
SERVICES = {"victoria-metrics", "grafana", "telemetry-ingest", "gateway"}


def run(argv: list[str], *, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=ROOT, check=True, text=True,
        capture_output=capture,
    )


def fail(message: str, code=2) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def docker(*args: str) -> list[str]:
    return ["docker", "compose", "--env-file", str(CONFIG),
            "-f", str(COMPOSE), *args]


def confirmation(phrase: str, warning: str) -> None:
    print(warning, file=sys.stderr)
    if not sys.stdin.isatty():
        raise ValueError("An interactive terminal and typed confirmation are required.")
    if input(f'Type "{phrase}" to continue: ').strip() != phrase:
        raise ValueError("Cancelled.")


def validate_existing() -> None:
    if not CONFIG.is_file() or CONFIG.is_symlink():
        raise ValueError(f"No trusted cooling configuration: {CONFIG}. Run init first.")
    if not (STATE / "secrets").is_dir() or (STATE / "secrets").is_symlink():
        raise ValueError("Cooling state is missing or symlinked; refusing to deploy.")
    run(docker("config", "-q"))
    service_list = set(run(docker("config", "--services"), capture=True).stdout.splitlines())
    if service_list != SERVICES:
        raise ValueError(f"Unexpected Compose services: {sorted(service_list)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="homeaway-cooling",
        description="Isolated SensorDash monitoring backend; no household DNS/VPN.",
    )
    p.add_argument("command", choices=("version", "plan", "init", "start", "status", "stop"))
    p.add_argument("--bind-ip", help="Host LAN IPv4, required on first init.")
    p.add_argument("--tls-name", help="Client-facing IP/DNS name.")
    p.add_argument("--https-port", type=int, default=9443)
    args = p.parse_args(argv)
    try:
        if args.command == "version":
            print((ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file()
                  else "development")
            return 0
        if args.command == "init":
            if not args.bind_ip:
                raise ValueError("init requires --bind-ip.")
            cmd = [sys.executable, str(ROOT / "scripts/init-cooling-standalone.py"),
                   "--init", "--config-file", str(CONFIG),
                   "--state-dir", str(STATE), "--bind-ip", args.bind_ip,
                   "--https-port", str(args.https_port)]
            if args.tls_name:
                cmd += ["--tls-name", args.tls_name]
            run(cmd)
            return 0
        if args.command == "plan":
            if not CONFIG.is_file():
                print("Not initialized; run homeaway-cooling init --bind-ip <LAN_IPV4>.")
                print("Package installation made no changes to running services.")
                return 0
            validate_existing()
            print("Configuration valid. Isolated Compose services: " + ", ".join(sorted(SERVICES)))
            print("Apply with 'sudo homeaway-cooling start'; no network/router changes are automatic.")
            return 0
        if args.command == "status":
            if not CONFIG.is_file():
                print("Not initialized. No backend containers are managed yet.")
                return 0
            validate_existing()
            run(docker("ps", "--all"))
            return 0
        validate_existing()
        if args.command == "start":
            confirmation(
                "START COOLING",
                "Starts/reconciles only 4 cooling services. HTTPS will bind to the configured local IP.",
            )
            run(docker("up", "-d", "--build"))
            run(docker("ps"))
        elif args.command == "stop":
            confirmation(
                "STOP COOLING",
                "Stops and removes cooling containers; named volumes and credentials are preserved.",
            )
            run(docker("down"))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
