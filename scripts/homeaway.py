#!/usr/bin/env python3
"""Operator-only HomeAway management facade.

The existing audited scripts remain the implementation of privileged actions.
No Docker socket, HTTP management endpoint, or unattended updater is added.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOCKETS = (
    "pcctl-agent.socket",
    "homeaway-inventory.socket",
    "homeaway-pcverify.socket",
)
TARGETS = {
    "ui": "both",
    "portal": "portal",
    "computers": "pc-control",
    "dashboards": "grafana",
}


def fail(message: str, code: int = 2) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def execute(*argv: str) -> None:
    subprocess.run(list(argv), cwd=ROOT, check=True)


def verify_checkout() -> None:
    for name in ("compose.yaml", "scripts/init.sh", "scripts/deploy-ui.sh"):
        if not (ROOT / name).is_file():
            raise ValueError(f"Missing {name}; command must point to the HomeAway checkout.")


def require_login_user() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise ValueError("Run homeaway as the Pi login user, not with sudo. Privileged helpers request sudo themselves.")


def confirm(phrase: str, warning: str) -> None:
    print(warning, file=sys.stderr)
    if not sys.stdin.isatty():
        raise ValueError("Applying requires an interactive terminal and typed confirmation.")
    if input(f'Type "{phrase}" to continue: ').strip() != phrase:
        raise ValueError("Cancelled; no command executed.")


def install(args: argparse.Namespace) -> int:
    """Prepare local configuration on a NEW checkout; never start containers."""
    existing = (ROOT / ".env").exists()
    print("HomeAway new-install preparation")
    print("This does not install an OS, Docker, start services, alter router/DNS, or enroll VPN peers.")
    if existing:
        print("Existing .env detected; its contents and existing state will not be modified.")
        if args.apply:
            return fail("Existing installation/configuration detected. Use 'homeaway status' and the operations guide.")
        return 0
    if (ROOT / "data").exists() or (ROOT / "secrets").exists():
        return fail("Existing state directories found with no .env; refusing to initialize over possible live data.")
    required = ("openssl", "python3")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        return fail("Prerequisites missing: " + ", ".join(missing))
    print("Next: initialize .env and generated local secrets, edit .env, install Docker/Compose,")
    print("then use 'homeaway doctor' and the INSTALL guide for staged DNS/VPN cutover.")
    if args.apply:
        confirm("PREPARE", "Only creates local configuration and secrets. No containers will be started.")
        execute("bash", "scripts/init.sh")
    else:
        print("PLAN ONLY. Run 'homeaway install --apply' to prepare a new checkout.")
    return 0


def status(_args: argparse.Namespace) -> int:
    """Read-only inventory without echoing secrets or Compose environment."""
    print("HomeAway status (read only)")
    print(f"Checkout: {ROOT}")
    if shutil.which("git"):
        execute("git", "rev-parse", "--short", "HEAD")
    if shutil.which("docker"):
        print("Compose project containers:")
        execute(
            "sudo", "docker", "ps", "-a",
            "--filter", "label=com.docker.compose.project=homeawayfromhome",
            "--format", "table {{.Names}}\t{{.Status}}",
        )
    else:
        print("Docker unavailable; install Docker Engine + Compose to run services.")
    if shutil.which("systemctl"):
        print("PC host sockets:")
        for unit in SOCKETS:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            print(f"  {unit}: {result.stdout.strip() or 'unknown'}")
    print("Container running state does not prove DNS, VPN handshake, or application health.")
    return 0


def doctor(_args: argparse.Namespace) -> int:
    execute("bash", "scripts/doctor.sh")
    return 0


def update(args: argparse.Namespace) -> int:
    if args.target == "core":
        print("Core Pi-hole/WireGuard maintenance changes household DNS/VPN.")
        print("This unified command intentionally does not run the legacy core updater.")
        print("Review a full encrypted backup and the core maintenance runbook first.")
        if args.apply:
            return fail("Core rollout is not yet automated; no services were changed.")
        return 0
    service = TARGETS[args.target]
    argv = ("bash", "scripts/deploy-ui.sh", "--apply" if args.apply else "--plan",
            "--services", service)
    if args.apply:
        confirm(f"UPDATE {args.target.upper()}",
                "This will fetch and fast-forward Production, then recreate ONLY the selected running service(s).")
    execute(*argv)
    return 0


def backup(args: argparse.Namespace) -> int:
    print("Encrypted full-state backup: includes credentials and PC trust; excludes VictoriaMetrics history.")
    print("Applying stops the HomeAway project and host PC-management sockets temporarily.")
    print("An isolated restoration and off-device copy remain operator responsibilities.")
    if args.apply:
        if not args.recipient:
            return fail("Use --recipient age1... with --apply; never supply a private age key.")
        if not args.recipient.startswith("age1") or len(args.recipient) < 54:
            return fail("Invalid age recipient; expected an age1... PUBLIC key.")
        confirm("BACKUP", "DNS, VPN, login and PC controls may be unavailable during this backup.")
        execute("sudo", "bash", "scripts/backup-full.sh", "--apply", "--recipient", args.recipient)
    else:
        if args.recipient:
            return fail("--recipient is only used with --apply.")
        execute("sudo", "bash", "scripts/backup-full.sh", "--plan")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="homeaway",
        description="One command for HomeAway setup, status and reviewed maintenance.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    for name, description in (
        ("install", "Prepare NEW local configuration (never start services)."),
        ("update", "Plan or apply a TARGETED existing deployment update."),
        ("backup", "Plan or create a disruptive encrypted full-state backup."),
    ):
        cmd = sub.add_parser(name, help=description)
        modes = cmd.add_mutually_exclusive_group()
        modes.add_argument("--plan", action="store_true", help="Read-only preview (default).")
        modes.add_argument("--apply", action="store_true", help="Confirm and perform the action.")
        if name == "update":
            cmd.add_argument("--target", choices=(*TARGETS, "core"), default="ui")
        if name == "backup":
            cmd.add_argument("--recipient", help="Public age recipient for encrypted backup.")
    sub.add_parser("status", help="Show running containers and host sockets (read-only).")
    sub.add_parser("doctor", help="Run the existing read-only diagnostics.")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        require_login_user()
        verify_checkout()
        args = parser().parse_args(argv)
        return {
            "install": install,
            "status": status,
            "doctor": doctor,
            "update": update,
            "backup": backup,
        }[args.command](args)
    except (ValueError, OSError) as exc:
        return fail(str(exc))
    except subprocess.CalledProcessError as exc:
        return fail(f"Command failed (exit {exc.returncode}); inspect output above.", exc.returncode or 1)
    except (KeyboardInterrupt, EOFError):
        return fail("Cancelled.", 130)


if __name__ == "__main__":
    sys.exit(main())
