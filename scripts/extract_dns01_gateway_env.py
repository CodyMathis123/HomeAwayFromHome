#!/usr/bin/env python3
"""Emit ONLY the three public hostname settings for offline Caddy validation.

All other Compose environment variables, secrets, and service settings remain
private. File is written under a temporary mode-0700 directory by caller.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

KEYS = ("HOMEAWAY_DOMAIN", "HOMEAWAY_AUTH_DOMAIN", "HOMEAWAY_METRICS_DOMAIN")
DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+\.home\.arpa$")


def get_values(config):
    env = config["services"]["gateway"]["environment"]
    result = {}
    for key in KEYS:
        value = env.get(key)
        if not isinstance(value, str) or not DOMAIN.fullmatch(value):
            raise ValueError("Unexpected or missing legacy gateway DNS name: " + key)
        result[key] = value
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: extract_dns01_gateway_env.py RESOLVED_BASE.json")
    values = get_values(json.loads(Path(sys.argv[1]).read_text()))
    for key in KEYS:
        print(key + "=" + values[key])


if __name__ == "__main__":
    main()
