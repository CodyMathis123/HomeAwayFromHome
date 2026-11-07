#!/usr/bin/env python3
"""Isolated, component-selected HomeAway cooling backend operator CLI.

Feature choices are resolved against a validated manifest before changing
state. Only the four-service cooling bundle is currently deployable.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from components import (
    SelectionError,
    describe,
    load_catalog,
    parse_selection,
    plan_json,
    plan_text,
    resolve,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = ROOT == Path("/opt/homeaway-cooling")
CONFIG = Path("/etc/homeaway-cooling/cooling.env") if PACKAGED else ROOT / "deploy/cooling/.env"
STATE = Path("/var/lib/homeaway-cooling") if PACKAGED else ROOT / "data/cooling"
COMPOSE = ROOT / "compose.cooling-only.yaml"
SUPPORTED_SERVICES = {"victoria-metrics", "grafana", "telemetry-ingest", "gateway"}
DEFAULT_SELECTION = ("cooling-monitoring",)


def run(argv: list[str], *, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=ROOT, check=True, text=True, capture_output=capture,
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


def configured_selection() -> tuple[str, ...] | None:
    if not CONFIG.exists():
        return None
    if not CONFIG.is_file() or CONFIG.is_symlink():
        raise SelectionError(f"Untrusted cooling configuration path: {CONFIG}.")
    choices = []
    for line in CONFIG.read_text(encoding="ascii").splitlines():
        if line.startswith("COOLING_COMPONENTS="):
            choices.append(line.split("=", 1)[1])
    if len(choices) > 1:
        raise SelectionError("Duplicate COOLING_COMPONENTS setting; refusing to deploy.")
    # Backward compatible with the earlier cooling-only prototype.
    return parse_selection(choices[0]) if choices else DEFAULT_SELECTION


def validate_existing(catalog: dict[str, dict], expected: tuple[str, ...] | None) -> dict:
    selected = configured_selection()
    if selected is None:
        raise ValueError(f"No cooling configuration: {CONFIG}. Run init first.")
    if expected is not None and expected != selected:
        raise SelectionError(
            "Requested selection differs from installed components. "
            "Component reconfiguration is not implemented yet; nothing changed."
        )
    plan = resolve(catalog, selected)
    if not (STATE / "secrets").is_dir() or (STATE / "secrets").is_symlink():
        raise ValueError("Cooling state is missing or symlinked; refusing to deploy.")
    run(docker("config", "-q"))
    actual_services = set(run(docker("config", "--services"), capture=True).stdout.splitlines())
    if actual_services != SUPPORTED_SERVICES or actual_services != set(plan["services"]):
        raise ValueError(f"Unexpected Compose services: {sorted(actual_services)}")
    return plan


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="homeaway-cooling",
        description="Modular SensorDash monitoring backend; DNS and VPN are optional and not installed.",
    )
    p.add_argument("command", choices=(
        "version", "components", "plan", "init", "start", "status", "stop"
    ))
    p.add_argument(
        "--components", metavar="IDS",
        help="Comma-separated user features, e.g. cooling-monitoring,dns-metrics.",
    )
    p.add_argument(
        "--design", action="store_true",
        help="Plan hypothetical feature dependencies only; never permits deployment.",
    )
    p.add_argument("--json", action="store_true", help="Structured output for components/plan.")
    p.add_argument("--bind-ip", help="Host LAN IPv4, required on first init.")
    p.add_argument("--tls-name", help="Client-facing IP/DNS name.")
    p.add_argument("--https-port", type=int, default=9443)
    args = p.parse_args(argv)
    try:
        if args.command == "version":
            print((ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").is_file()
                  else "development")
            return 0
        catalog = load_catalog()
        if args.command == "components":
            if args.components or args.design:
                raise SelectionError("Use 'plan --components ... --design' to inspect choices.")
            listing = describe(catalog)
            if args.json:
                print(json.dumps(listing, indent=2))
            else:
                for item in listing:
                    suffix = " [shared dependency]" if not item["selectable"] else ""
                    print(f'{item["id"]:<24} {item["status"]:<10} {item["label"]}{suffix}')
                print("Only cooling-monitoring is currently deployable; others are design previews.")
            return 0

        requested = parse_selection(args.components) if args.components else None
        installed = configured_selection()

        if args.command == "plan":
            selection = requested or installed or DEFAULT_SELECTION
            plan = resolve(catalog, selection, design=args.design)
            if installed is not None and selection == installed and not args.design:
                validate_existing(catalog, selection)
            if args.json:
                print(plan_json({
                    **plan,
                    "initialized": installed is not None,
                    "selection_matches_installed": installed is None or selection == installed,
                }))
            else:
                print(plan_text(plan))
                if installed is None:
                    print("Not initialized: 'init --bind-ip <LAN_IP>' prepares credentials only.")
                elif selection != installed:
                    print("Installed selection differs: this is a read-only preview, NOT an update.")
                else:
                    validate_existing(catalog, selection)
                    print("Installed configuration and Compose service graph verified.")
                print("No services or configuration changed.")
            return 0

        if args.design:
            raise SelectionError("--design is read-only and only valid with 'plan'.")

        if args.command == "init":
            selection = requested or DEFAULT_SELECTION
            plan = resolve(catalog, selection)
            if installed is not None:
                raise ValueError("Already initialized; refusing to overwrite configuration.")
            if not args.bind_ip:
                raise ValueError("init requires --bind-ip.")
            command = [
                sys.executable, str(ROOT / "scripts/init-cooling-standalone.py"),
                "--init", "--config-file", str(CONFIG),
                "--state-dir", str(STATE), "--bind-ip", args.bind_ip,
                "--https-port", str(args.https_port),
                "--components", ",".join(plan["selected_features"]),
            ]
            if args.tls_name:
                command += ["--tls-name", args.tls_name]
            run(command)
            return 0

        if installed is None and args.command == "status":
            print("Not initialized. No backend containers are managed yet.")
            return 0
        plan = validate_existing(catalog, requested)
        if args.command == "status":
            run(docker("ps", "--all"))
        elif args.command == "start":
            confirmation(
                "START COOLING",
                "Starts only the resolved cooling services; local HTTPS listener will become active.",
            )
            run(docker("up", "-d", "--build"))
            run(docker("ps"))
        elif args.command == "stop":
            confirmation(
                "STOP COOLING",
                "Stops cooling containers; metrics, configurations and credentials are retained.",
            )
            run(docker("down"))
        return 0
    except (SelectionError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
