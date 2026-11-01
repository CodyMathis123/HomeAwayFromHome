#!/usr/bin/env python3
"""Validate only the small, explicit .env schema. Never parse .env as shell code."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=value")
        key, value = line.split("=", 1)
        if not key.isidentifier() or key in config or not value.strip():
            raise ValueError(f"{path}:{number}: invalid/duplicate/empty variable")
        config[key] = value.strip().strip('"').strip("'")
    return config


def validate(values: dict[str, str], allow_example: bool = False) -> None:
    required = ["PI_LAN_IP", "LAN_CIDR", "VPN_ENDPOINT", "VPN_PORT", "VPN_CIDR"]
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError("Missing settings: " + ", ".join(missing))

    address = ipaddress.ip_address(values["PI_LAN_IP"])
    lan = ipaddress.ip_network(values["LAN_CIDR"], strict=False)
    vpn = ipaddress.ip_network(values["VPN_CIDR"], strict=False)
    docker = ipaddress.ip_network("10.42.42.0/24")
    if address.version != 4 or not address.is_private or address not in lan:
        raise ValueError("PI_LAN_IP must be an IPv4 address inside private LAN_CIDR")
    if lan.version != 4 or vpn.version != 4 or not vpn.is_private:
        raise ValueError("LAN_CIDR and VPN_CIDR must be private IPv4 ranges")
    if lan.overlaps(vpn) or lan.overlaps(docker) or vpn.overlaps(docker):
        raise ValueError("Home LAN, VPN and Docker bridge networks must not overlap")
    if not 1 <= int(values["VPN_PORT"]) <= 65535:
        raise ValueError("VPN_PORT must be 1–65535")
    host = values["VPN_ENDPOINT"]
    if not allow_example and (host.endswith(".example.com") or host == "vpn.example.com"):
        raise ValueError("Set VPN_ENDPOINT to your own domain or public IP")
    if "://" in host or "/" in host or " " in host:
        raise ValueError("VPN_ENDPOINT must be a hostname or IP, not a URL")
    if any(c.isspace() for c in values.get("UPSTREAM_DNS", "")):
        raise ValueError("UPSTREAM_DNS should be semicolon-separated without spaces")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", default=".env")
    parser.add_argument("--allow-example", action="store_true")
    parser.add_argument("--get", help="Print one non-secret configuration key")
    args = parser.parse_args()
    values = parse_env(Path(args.file))
    validate(values, allow_example=args.allow_example)
    if args.get:
        if args.get not in ("PI_LAN_IP", "LAN_CIDR", "VPN_ENDPOINT", "VPN_PORT", "VPN_CIDR", "TZ"):
            raise ValueError("Retrieval of this key is not allowed")
        print(values.get(args.get, ""))
    else:
        print("Configuration validated (no network changes made).")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Configuration error: {exc}")
