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
        "HOMEAWAY_AUTH_DOMAIN": "auth.home.arpa",
        "HOMEAWAY_METRICS_DOMAIN": "metrics.home.arpa",
    }
    print("Checking Caddyfile syntax (pinned image)...", flush=True)
    subprocess.run(["docker", "run", "--rm",
                    "-v", str(ROOT / "control/gateway/Caddyfile") + ":/etc/caddy/Caddyfile:ro",
                    *sum((["-e", k + "=" + v] for k, v in env.items()), []),
                    "caddy:2.10.2", "caddy", "adapt",
                    "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                   stdout=subprocess.DEVNULL, check=True, timeout=180)
    with tempfile.TemporaryDirectory() as root:
        folder = Path(root)
        (folder / "configuration.yml").write_text(
            bootstrap.build_configuration("homeaway.home.arpa", "auth.home.arpa", "home.arpa",
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
