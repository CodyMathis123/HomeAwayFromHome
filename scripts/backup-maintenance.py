#!/usr/bin/env python3
"""Run safe, read-only HomeAway backup maintenance checks and publish a sanitized result.

This program NEVER runs backup-full.sh --apply or removes archives. A scheduled
run may write only a small public-to-admin status document, not private data.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
STATUS_DIR = ROOT / "data/control/backup-status"
STATUS_FILE = STATUS_DIR / "maintenance.json"
CHECKS = (
    ("backupPreflight", ["bash", str(ROOT / "scripts/backup-full.sh"), "--plan"]),
    ("retentionReview", [sys.executable, str(ROOT / "scripts/plan-backup-retention.py"),
                          "--plan", "--keep-local", "3"]),
)
TIMEOUT_SECONDS = 90


def run_checks(runner=subprocess.run) -> dict:
    results = []
    for name, command in CHECKS:
        try:
            result = runner(command, cwd=ROOT, capture_output=True,
                            text=True, timeout=TIMEOUT_SECONDS, check=False)
            # Do not publish process output: it may contain private paths.
            state = "ok" if result.returncode == 0 else "attention"
        except (OSError, subprocess.TimeoutExpired):
            state = "attention"
        results.append({"id": name, "state": state})
    try:
        enabled = runner(["systemctl", "is-enabled",
                          "homeaway-backup-preflight.timer"],
                         capture_output=True, text=True, timeout=5,
                         check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        enabled = False
    return {"schemaVersion": 1,
            "checkedAt": datetime.now(timezone.utc).isoformat(),
            "mode": "preflight-only", "scheduleEnabled": enabled,
            "checks": results}


def publish(report: dict) -> None:
    # Root-owned host files, no symlinks or world-writable destination.
    if STATUS_DIR.is_symlink() or not STATUS_DIR.is_dir():
        raise RuntimeError("Missing or unsafe status directory")
    info = STATUS_DIR.stat()
    if info.st_uid != 0 or (info.st_mode & stat.S_IWOTH):
        raise RuntimeError("Unsafe status directory ownership or mode")
    if STATUS_FILE.is_symlink():
        raise RuntimeError("Unsafe status file")
    fd, filename = tempfile.mkstemp(prefix=".maintenance-", dir=STATUS_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o644)
            json.dump(report, handle, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, STATUS_FILE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run as root to inspect private backup state.")
    result = run_checks()
    for check in result["checks"]:
        print(f'{check["id"]}: {check["state"]}')
    if args.publish:
        publish(result)
        print("Maintenance preflight status published. No backup executed.")
    else:
        print("PLAN ONLY: no service stopped, archive modified or status written.")
    return 0 if all(x["state"] == "ok" for x in result["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
