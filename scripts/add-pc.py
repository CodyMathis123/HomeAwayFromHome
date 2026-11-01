#!/usr/bin/env python3
"""Add one computer to the single Pi inventory; no service restart needed.

Run with --file to prepare a staging inventory before editing the installed one.
Requires a real MAC, private IPv4, and broadcast; does not create credentials.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "control/pcctl"))
from core import load_hosts  # noqa: E402


def add_host(path: Path, item: dict) -> int:
    data = {"hosts": []}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    current = data.get("hosts", [])
    if any(h.get("id") == item["id"] for h in current):
        raise ValueError(f"Computer {item['id']} already exists")
    candidate = {"hosts": current + [item]}
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate all hosts with the same parser used by the actual Pi service.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
        tmp = Path(temp.name)
        json.dump(candidate, temp, indent=2)
        temp.write("\n")
    try:
        load_hosts(str(tmp))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(candidate["hosts"])


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
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot register computer: {error}")
