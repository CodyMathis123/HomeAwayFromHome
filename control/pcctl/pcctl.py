#!/usr/bin/env python3
"""Backwards-compatible CLI: pcctl {host} {status|wake|sleep|restart|shutdown}."""
import json
import sys
from core import ACTIONS, perform

def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[1] not in ACTIONS:
        print("Usage: pcctl HOST {status|wake|sleep|restart|shutdown}", file=sys.stderr)
        return 2
    try:
        value = perform(*args)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f"pcctl: {exc}", file=sys.stderr)
        return 1
    print(f"{value['host']}: {value['state']} - {value['detail']}")
    return 0 if value["ok"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
