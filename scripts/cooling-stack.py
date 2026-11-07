#!/usr/bin/env python3
"""Component-selected HomeAway Linux metrics backend operator CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from components import (
    SelectionError, describe, load_catalog, parse_selection,
    plan_json, plan_text, resolve,
)
import compose_generator
import backup_service
from component_changes import transition
import reconfigure_service

ROOT = Path(__file__).resolve().parents[1]
PACKAGED = ROOT == Path("/opt/homeaway-cooling")
CONFIG = Path("/etc/homeaway-cooling/cooling.env") if PACKAGED else ROOT / "deploy/cooling/.env"
STATE = Path("/var/lib/homeaway-cooling") if PACKAGED else ROOT / "data/cooling"
DEFAULT_SELECTION = ("cooling-monitoring",)


def run(argv: list[str], *, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, check=True, text=True, capture_output=capture)


def fail(message: str, code: int = 2) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return code


def docker(*args: str) -> list[str]:
    return [
        "docker", "compose", "--env-file", str(CONFIG),
        "--project-directory", str(ROOT),
        "-f", str(STATE / "generated/compose.generated.json"), *args,
    ]


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
        raise SelectionError("Duplicate COOLING_COMPONENTS setting.")
    # Older cooling-only installs are interpreted as their original selection.
    return parse_selection(choices[0]) if choices else DEFAULT_SELECTION


def validate_saved(catalog: dict[str, dict], requested: tuple[str, ...] | None) -> tuple[dict, dict]:
    installed = configured_selection()
    if installed is None:
        raise ValueError(f"No installation configuration: {CONFIG}. Run init first.")
    if requested is not None and installed != requested:
        raise SelectionError(
            "Requested components differ from installed selection. "
            "Adding/removing components on an existing installation requires a "
            "future migration workflow; no changes made."
        )
    plan = resolve(catalog, installed)
    if not (STATE / "secrets").is_dir() or (STATE / "secrets").is_symlink():
        raise ValueError("Cooling state is missing or symlinked.")
    rendered = compose_generator.render(plan, CONFIG, STATE, ROOT)
    return plan, rendered


def verify_compose(plan: dict) -> None:
    run(docker("config", "-q"))
    actual = set(run(docker("config", "--services"), capture=True).stdout.splitlines())
    if actual != set(plan["services"]):
        raise SelectionError(
            f"Generated Compose services are not the selected bundle: {sorted(actual)}"
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="homeaway-cooling",
        description="Selectable Linux monitoring backend; DNS and VPN are never installed by this CLI.",
    )
    p.add_argument("command", choices=(
        "version", "components", "plan", "init", "start", "status", "stop",
        "change", "backup",
    ))
    p.add_argument("--components", metavar="IDS", help="Comma-separated user features.")
    p.add_argument("--design", action="store_true",
                   help="Read-only planning for future modules; never permits apply.")
    p.add_argument("--json", action="store_true", help="Structured components or plan output.")
    p.add_argument("--apply", action="store_true", help="Explicitly apply backup/change actions.")
    p.add_argument("--recipient", help="Age encryption recipient for private full-state backup.")
    p.add_argument("--backup-file", type=Path,
                   help="Recent full-state .age archive required to apply component changes.")
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
                    print(
                        f'{item["id"]:<24} {item["status"]:<10} '
                        f'{item["label"]}{suffix}'
                    )
            return 0

        requested = parse_selection(args.components) if args.components else None
        installed = configured_selection()
        if args.command == "plan":
            selection = requested or installed or DEFAULT_SELECTION
            plan = resolve(catalog, selection, design=args.design)
            changed = []
            if installed is not None and selection == installed and not args.design:
                current, rendered = validate_saved(catalog, installed)
                changed = compose_generator.differences(rendered, STATE)
            result = {
                **plan,
                "initialized": installed is not None,
                "selection_matches_installed": installed is None or selection == installed,
                "generated_files_to_update": changed,
            }
            if args.json:
                print(plan_json(result))
            else:
                print(plan_text(result))
                if installed is None:
                    print("Not initialized. 'init --bind-ip <LAN_IP>' creates private credentials.")
                elif selection != installed:
                    print("Installed selection differs. This preview does not migrate components.")
                else:
                    print("Generated files requiring creation/update: " + (
                        ", ".join(changed) or "(none)"
                    ))
                print("Read-only: no files, containers or network settings changed.")
            return 0
        if args.design:
            raise SelectionError("--design is read-only and only valid with 'plan'.")
        if args.apply and args.command not in {"backup", "change"}:
            raise SelectionError("--apply only supports 'backup' and 'change'.")
        if args.command == "change":
            if installed is None or requested is None:
                raise SelectionError("Change requires an installed selection and --components.")
            old_plan, old_render = validate_saved(catalog, installed)
            change_plan = transition(catalog, installed, requested)
            stale = compose_generator.differences(old_render, STATE)
            if stale:
                raise SelectionError("Managed configuration differs; use normal plan/start first.")
            if not args.apply:
                if args.json:
                    print(json.dumps(change_plan, indent=2, sort_keys=True))
                else:
                    print("READ-ONLY component change preview:")
                    print(json.dumps(change_plan, indent=2, sort_keys=True))
                    print("Apply requires a recent, verified age-encrypted backup.")
                    print("Nothing has changed.")
                return 0
            if args.backup_file is None:
                raise SelectionError(
                    "Applying a component change requires --backup-file from "
                    "a successful 'backup --apply' operation."
                )
            confirmation("APPLY COMPONENT CHANGE",
                         "Updates only scoped monitoring containers. "
                         "Retains all named volumes and secrets; brief downtime possible.")
            with reconfigure_service.operation_lock(STATE):
                result = reconfigure_service.change(
                    CONFIG, STATE, ROOT, catalog, installed, requested,
                    args.backup_file, docker, run,
                )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "backup":
            old_plan, old_render = validate_saved(catalog, None)
            stale = compose_generator.differences(old_render, STATE)
            if stale:
                raise SelectionError("Managed configuration differs; backup preflight refused.")
            if not args.apply:
                print("READ-ONLY backup plan: stop only this Compose project; ")
                print("stream config, secrets, generated Compose, and four named volumes")
                print("through age encryption. No plaintext backup file is created.")
                print("All scoped containers are restarted after the capture.")
                print("Requires: age recipient, docker, age CLI, and explicit --apply.")
                return 0
            if not args.recipient:
                raise SelectionError("Backup requires --recipient age1... and --apply.")
            if reconfigure_service.running_services(docker, run) != set(old_plan["services"]):
                raise SelectionError("All selected monitoring services must be running.")
            confirmation("BACKUP MONITORING",
                         "Temporarily stops the selected monitoring containers to "
                         "make a consistent encrypted backup of config and all volumes.")
            with reconfigure_service.operation_lock(STATE):
                archive = backup_service.capture(
                    CONFIG, STATE, installed, args.recipient, docker, run,
                )
            print(f"Age-encrypted full-state backup saved: {archive}")
            print("Keep the corresponding .json checksum manifest and decryption key safe.")
            return 0
        if args.command == "init":
            selection = requested or DEFAULT_SELECTION
            plan = resolve(catalog, selection)
            if installed is not None:
                raise ValueError("Already initialized; refusing to overwrite configuration.")
            if not args.bind_ip:
                raise ValueError("init requires --bind-ip.")
            command = [
                sys.executable, "-B", str(ROOT / "scripts/init-cooling-standalone.py"),
                "--init", "--config-file", str(CONFIG), "--state-dir", str(STATE),
                "--bind-ip", args.bind_ip, "--https-port", str(args.https_port),
                "--components", ",".join(plan["selected_features"]),
            ]
            if args.tls_name:
                command += ["--tls-name", args.tls_name]
            run(command)
            saved, generated = validate_saved(catalog, selection)
            compose_generator.write(generated, STATE)
            print("Generated selected-service Compose, scrape configuration, and HTTPS routes.")
            return 0

        if installed is None and args.command == "status":
            print("Not initialized. No backend containers are managed yet.")
            return 0
        plan, rendered = validate_saved(catalog, requested)
        changed = compose_generator.differences(rendered, STATE)
        if args.command == "status":
            if changed:
                print("Generated deployment files are missing or outdated. Run plan then start.")
            else:
                verify_compose(plan)
                run(docker("ps", "--all"))
            return 0

        if args.command == "start":
            confirmation(
                "START MONITORING",
                "Reconciles only the selected monitoring services. "
                "HTTPS binds to the configured local IP. "
                f"Managed configuration changes: {', '.join(changed) or '(none)'}.",
            )
            compose_generator.write(rendered, STATE)
            verify_compose(plan)
            run(docker("up", "-d", "--build"))
            run(docker("ps"))
        elif args.command == "stop":
            confirmation(
                "STOP MONITORING",
                "Stops the installed monitoring bundle; metrics, keys and Docker volumes remain.",
            )
            if changed:
                raise SelectionError(
                    "Generated deployment files differ from installed definition. "
                    "Refusing to stop with an unreviewed service graph."
                )
            verify_compose(plan)
            run(docker("down"))
        return 0
    except (SelectionError, ValueError, RuntimeError, OSError,
            subprocess.CalledProcessError) as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main())
