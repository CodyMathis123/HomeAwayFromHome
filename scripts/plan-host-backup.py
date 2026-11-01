#!/usr/bin/env python3
"""Read-only inventory of host-side resources required for a complete Pi backup.

This is NOT a backup. No content, usernames, fingerprints or secret values are
printed. No services are stopped, files modified or archives created.
"""
from __future__ import annotations
import argparse
import os
import stat
import subprocess
from pathlib import Path

SOURCES = (
    ("Active PC registry", Path("/etc/homeaway-pcctl/hosts.json"), True),
    ("PC registry rollback", Path("/etc/homeaway-pcctl/hosts.json.previous"), False),
    ("PC control SSH trust/key directory", Path("/home/pcctl/.ssh"), True),
    ("Computer management state", Path("/var/lib/homeaway-pcinventory"), True),
    ("Control agent code", Path("/opt/homeaway-pcctl"), True),
    ("Inventory agent code", Path("/opt/homeaway-pcinventory"), True),
    ("Trust verifier code", Path("/opt/homeaway-pcverify"), True),
    ("Power agent socket unit", Path("/etc/systemd/system/pcctl-agent.socket"), True),
    ("Power agent service unit", Path("/etc/systemd/system/pcctl-agent.service"), True),
    ("Inventory agent socket unit", Path("/etc/systemd/system/homeaway-inventory.socket"), True),
    ("Inventory agent service unit", Path("/etc/systemd/system/homeaway-inventory.service"), True),
    ("Verifier socket unit", Path("/etc/systemd/system/homeaway-pcverify.socket"), True),
    ("Verifier service unit", Path("/etc/systemd/system/homeaway-pcverify.service"), True),
    ("Inventory tempfiles definition", Path("/etc/tmpfiles.d/homeaway-inventory.conf"), True),
    ("Verifier tempfiles definition", Path("/etc/tmpfiles.d/homeaway-pcverify.conf"), True),
)
SERVICES = (
    "pcctl-agent.socket",
    "pcctl-agent.service",
    "homeaway-inventory.socket",
    "homeaway-inventory.service",
    "homeaway-pcverify.socket",
    "homeaway-pcverify.service",
)

def check_path(label: str, path: Path, required: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        print(f"{'MISSING' if required else 'OPTIONAL ABSENT'}: {label} ({path})")
        return not required
    except OSError:
        print(f"UNREADABLE METADATA: {label} ({path})")
        return False
    if stat.S_ISLNK(info.st_mode):
        print(f"UNSAFE SYMLINK: {label} ({path})")
        return False
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        print(f"UNEXPECTED TYPE: {label} ({path})")
        return False
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        print(f"UNSAFE HARDLINK: {label} ({path})")
        return False
    typ = "directory" if stat.S_ISDIR(info.st_mode) else "file"
    print(f"PRESENT: {label} ({typ}; {path})")
    return True

def service_state(name: str) -> None:
    try:
        result = subprocess.run(["systemctl", "is-active", name],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        print(f"UNKNOWN: {name} (systemctl unavailable)")
        return
    state = result.stdout.strip() if result.stdout.strip() else "unknown"
    if state not in {"active", "inactive", "failed", "activating",
                     "deactivating", "unknown"}:
        state = "unknown"
    print(f"{state.upper()}: {name}")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        print("ERROR: root is required for full host-path metadata inspection")
        return 2
    print("HomeAway host-state backup SCOPE ONLY (read-only)")
    problems = 0
    for label, path, required in SOURCES:
        if not check_path(label, path, required):
            problems += 1
    print("\nHost writer/socket states (no changes):")
    for name in SERVICES:
        service_state(name)
    print("\nConsistency warning: stopping only Docker services does NOT quiesce")
    print("host inventory writes. A consistent snapshot must coordinate the")
    print("inventory agent and its socket, and restore them on error.")
    print("Backup archive does not exist; no encryption or off-device restore test performed.")
    print("DO NOT copy the SSH directory or other secrets into Git or chat.")
    print(f"Host path preflight: {problems} missing/unsafe required path(s)")
    return 0 if not problems else 1

if __name__ == "__main__":
    raise SystemExit(main())
