#!/usr/bin/env python3
"""Read-only inventory and retention suggestions for encrypted HomeAway backups.

Never deletes, decrypts, transfers, or opens archive contents. Root is required
because the archive directory is intentionally private (0700).
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
from datetime import datetime, timezone

BACKUP_DIR = Path("/var/backups/homeawayfromhome")

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", required=True)
    parser.add_argument("--keep-local", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.keep_local <= 100:
        parser.error("--keep-local must be between 1 and 100")
    if os.geteuid() != 0:
        parser.error("run with sudo; backup directory remains root-only")
    if not BACKUP_DIR.is_dir() or BACKUP_DIR.is_symlink():
        print("ERROR: protected backup directory missing or unsafe")
        return 1
    files = sorted(BACKUP_DIR.glob("full-*.tar.age"), key=lambda p: p.name, reverse=True)
    print("HomeAway encrypted backup retention PLAN ONLY")
    print(f"Encrypted full-state archives: {len(files)}; keep-local target: {args.keep_local}")
    issue = False
    for index, path in enumerate(files):
        if path.is_symlink() or not path.is_file():
            print(f"UNSAFE: {path.name}; inspect manually")
            issue = True
            continue
        checksum = Path(str(path) + ".sha256")
        check = "checksum present" if checksum.is_file() and not checksum.is_symlink() else "CHECKSUM MISSING"
        if not checksum.is_file() or checksum.is_symlink():
            issue = True
        category = "KEEP" if index < args.keep_local else "REVIEW AFTER VERIFIED OFF-DEVICE COPY"
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"{category}: {path.name} ({size_mb:.1f} MiB; {check})")
    print("No files deleted or modified. Never delete the only verified decryptable/off-device copy.")
    print("Retention is advisory; manual deletions require separate off-device verification.")
    return 1 if issue else 0

if __name__ == "__main__":
    raise SystemExit(main())
