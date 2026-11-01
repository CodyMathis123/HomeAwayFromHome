#!/usr/bin/env python3
"""Read-only checks for permissions and canonical production deployment.

No secrets, credential values, network calls, chmod, Docker restarts, or writes.
Uses docker only to inspect the existing gateway, if accessible.
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATEWAY = ROOT / "control/gateway/Caddyfile.production"
SECRET = ROOT / "secrets/control/cloudflare_dns01_token"
HOST_ITEMS = (
    Path("/etc/homeaway-pcctl/hosts.json"),
    Path("/var/lib/homeaway-pcinventory"),
    Path("/home/pcctl/.ssh"),
    Path("/etc/systemd/system/homeaway-inventory.socket"),
    Path("/etc/systemd/system/homeaway-pcverify.socket"),
)


def check_file(path: Path, label: str, *, readable_by_all: bool = False,
               secret: bool = False, executable: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        print(f"FAIL {label}: missing ({path})")
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        print(f"FAIL {label}: must be a regular, non-symlink file")
        return False
    mode = stat.S_IMODE(info.st_mode)
    if readable_by_all and mode & 0o444 != 0o444:
        print(f"FAIL {label}: mode {mode:04o}; non-root Caddy/Python requires readable source")
        return False
    if executable and mode & 0o111 == 0:
        print(f"FAIL {label}: mode {mode:04o}; no execute bit")
        return False
    if secret and (info.st_uid != 0 or mode not in (0o400, 0o600)):
        print(f"FAIL {label}: must be root owned, mode 0400 or 0600")
        return False
    print(f"PASS {label}: mode {mode:04o}")
    return True


def check_gateway_mount() -> bool:
    command = ["docker", "inspect", "homeawayfromhome-gateway-1",
               "--format", '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}']
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                check=False, timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        print("WARN gateway mount: Docker inspect unavailable (run with sudo if needed)")
        return True
    if result.returncode:
        print("WARN gateway mount: cannot inspect running gateway")
        return True
    mounted = result.stdout.strip()
    expected = str(GATEWAY.resolve())
    if mounted != expected:
        print("FAIL gateway mount: live Caddyfile differs from canonical production path")
        return False
    print("PASS gateway mount: canonical production Caddyfile")
    return True


def audit_host_state() -> None:
    print("\nHost-state backup coverage (inventory only; no credentials printed):")
    for path in HOST_ITEMS:
        if path.exists():
            print(f"PRESENT {path}")
        else:
            print(f"MISSING {path} (may be optional; confirm before recovery planning)")
    print("NOTE: existing scripts/backup.sh does NOT archive these host paths.")
    print("NOTE: a successful Docker-data backup is not full Pi recovery.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-docker", action="store_true",
                        help="Skip live gateway mount inspection")
    args = parser.parse_args()
    print("HomeAway production permission audit (read-only)")
    results = [
        check_file(GATEWAY, "canonical Caddyfile", readable_by_all=True),
        check_file(ROOT / "control/api/app.py", "control API source"),
        check_file(ROOT / "control/api/Dockerfile", "control API Dockerfile"),
        check_file(ROOT / "control/gateway/start-dns01.sh", "DNS-01 entrypoint"),
        check_file(SECRET, "Cloudflare token", secret=True),
    ]
    if not args.skip_docker:
        results.append(check_gateway_mount())
    audit_host_state()
    if all(results):
        print("\nPASS: production permission preflight")
        return 0
    print("\nFAIL: fix flagged permissions/mount before any deployment")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
