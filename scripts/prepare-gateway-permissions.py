#!/usr/bin/env python3
"""Safely prepare the one public Caddy configuration bind mount.

Explicit --apply only: never touches secrets, credentials, Docker or services.
"""
from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "control/gateway/Caddyfile.production"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = p.parse_args()
    try:
        info = PATH.lstat()
    except FileNotFoundError:
        print("FAIL: canonical Caddyfile missing; no changes made")
        return 1
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
        print("FAIL: Caddyfile must be a non-symlink, single-link regular file")
        return 1
    if info.st_uid != os.geteuid():
        print("FAIL: run as the checkout owner, not via sudo")
        return 1
    original = stat.S_IMODE(info.st_mode)
    if args.plan:
        print(f"Caddyfile mode: {original:04o}; required: 0644")
        print("PLAN ONLY: no changes made")
        return 0
    if original != 0o644:
        os.chmod(PATH, 0o644, follow_symlinks=False)
    final = PATH.lstat()
    if not stat.S_ISREG(final.st_mode) or stat.S_IMODE(final.st_mode) != 0o644:
        print("FAIL: mode check did not succeed")
        return 1
    print("PASS: canonical Caddyfile mode 0644; no other file changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
