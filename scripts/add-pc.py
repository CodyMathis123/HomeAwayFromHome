#!/usr/bin/env python3
"""Add one computer to the single Pi inventory; no service restart needed.

Run with --file to prepare a staging inventory before editing the installed one.
Requires a real MAC, private IPv4, and broadcast; does not create credentials.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control/pcctl"))
from inventory_store import update_inventory  # noqa: E402


def add_host(path: Path, item: dict) -> int:
    # Same transaction helper will be used by the narrow computer-management
    # broker. CLI and browser edits must never overwrite one another.
    def append(document: dict) -> dict:
        hosts = document["hosts"]
        if any(host["id"] == item["id"] for host in hosts):
            raise ValueError(f"Computer {item['id']} already exists")
        if any(host["address"] == item["address"] for host in hosts):
            raise ValueError("A computer already uses that IP address")
        if any(host["mac"].lower() == item["mac"].lower() for host in hosts):
            raise ValueError("A computer already uses that MAC address")
        return {**document, "hosts": hosts + [item]}

    updated, _revision = update_inventory(path, append)
    return len(updated["hosts"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="/etc/homeaway-pcctl/hosts.json")
    parser.add_argument("--id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--broadcast", required=True)
    parser.add_argument("--ssh-user", default="pcctl")
    parser.add_argument("--ssh-port", type=int, default=22)
    args = parser.parse_args()
    count = add_host(Path(args.file), {"id": args.id, "name": args.name, "address": args.ip,
                          "mac": args.mac, "broadcast": args.broadcast,
                          "ssh_user": args.ssh_user, "ssh_port": args.ssh_port})
    print(f"Registered {args.id}; inventory now has {count} computers.")
    print("Next: install the restricted Windows SSH handler and Pi authorized key on the computer.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        raise SystemExit(f"Cannot register computer: {error}")
