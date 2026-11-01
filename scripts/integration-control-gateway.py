#!/usr/bin/env python3
"""CI-only validation of real Caddy/Authelia config parsers; never starts Pi services."""
import importlib.util
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/init-control-auth.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


def main():
    env = {
        "HOMEAWAY_DOMAIN": "homeaway.home.arpa",
        "HOMEAWAY_AUTH_DOMAIN": "auth.homeaway.home.arpa",
        "HOMEAWAY_METRICS_DOMAIN": "metrics.homeaway.home.arpa",
    }
    print("Checking Caddyfile syntax (pinned image)...", flush=True)
    adapted = subprocess.run(["docker", "run", "--rm",
                    "-v", str(ROOT / "control/gateway/Caddyfile") + ":/etc/caddy/Caddyfile:ro",
                    *sum((["-e", k + "=" + v] for k, v in env.items()), []),
                    "caddy:2.11.7-alpine", "caddy", "adapt",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                   capture_output=True, text=True, check=True, timeout=180)

    # Do not settle for a syntactically valid Caddyfile. Caddy's normal sorting
    # evaluates forward_auth before request_header; the old config erased the
    # authenticated user and made the control API answer "Not authenticated".
    caddy_json = json.loads(adapted.stdout)

    def find_control(value):
        if isinstance(value, dict):
            matchers = value.get("match", [])
            if isinstance(matchers, list) and any(
                isinstance(m, dict) and "/control" in m.get("path", [])
                for m in matchers
            ):
                return value
            for child in value.values():
                found = find_control(child)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = find_control(child)
                if found is not None:
                    return found
        return None

    control = find_control(caddy_json)
    assert control is not None, "No /control route in adapted Caddy JSON"
    order = []

    def walk(value):
        if isinstance(value, dict):
            kind = value.get("handler")
            if kind == "headers":
                removed = value.get("request", {}).get("delete", [])
                if "Remote-User" in removed and "X-HomeAway-User" in removed:
                    order.append("remove_untrusted_identity")
            elif kind == "reverse_proxy":
                dial = " ".join(x.get("dial", "") for x in value.get("upstreams", []))
                if "authelia:9091" in dial:
                    order.append("forward_auth")
                elif "pc-control:8081" in dial:
                    order.append("pc_control")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(control)
    if order[:3] != ["remove_untrusted_identity", "forward_auth", "pc_control"]:
        print("Adapted control route (CI synthetic, no secrets):", json.dumps(control, indent=2)[:14000], flush=True)
    assert order[:3] == ["remove_untrusted_identity", "forward_auth", "pc_control"], (
        "Unsafe adapted control identity order: " + repr(order)
    )
    caddy_source = (ROOT / "control/gateway/Caddyfile").read_text()
    assert "copy_headers Remote-User>X-HomeAway-User" in caddy_source
    assert "header_up X-HomeAway-User {http.request.header.Remote-User}" not in caddy_source
    print("PASS: Caddy clears spoofed identity before auth and forwards verified username", flush=True)

    with tempfile.TemporaryDirectory() as root:
        folder = Path(root)
        (folder / "configuration.yml").write_text(
            bootstrap.build_configuration("homeaway.home.arpa", "auth.homeaway.home.arpa", "homeaway.home.arpa",
                                          "s" * 80, "j" * 80, "e" * 80))
        (folder / "users_database.yml").write_text(json.dumps({
            "users": {"ci-test": {"disabled": False, "displayname": "CI test",
                "password": "$argon2id$v=19$m=65536,t=3,p=4$Hjc8e7WYcBFcJmEDUOsS9A$ozM7RyZR1EyDR8cuyVpDDfmLrGPGFgo5E2NNqRumui4",
                "email": "ci@example.com", "groups": ["admins"]}}
        }))
        print("Validating Authelia 2FA configuration (pinned image)...", flush=True)
        subprocess.run(["docker", "run", "--rm", "-v", str(folder) + ":/config:ro",
                        "authelia/authelia:4.39.28", "authelia", "config",
                        "validate", "--config", "/config/configuration.yml"],
                       check=True, timeout=180)
    print("PASS: Caddy routing and Authelia config syntax")


if __name__ == "__main__":
    main()
