#!/usr/bin/env python3
"""Publish a sanitized, checksum-verified encrypted backup observation.

Operator-run on the Pi as root. Never decrypts backup data or reveals paths,
archive names, key identities, checksums, or private metadata to the web UI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKUPS = Path("/var/backups/homeawayfromhome")
ROOT = Path(__file__).resolve().parent.parent
DEST_DIR = ROOT / "data/control/backup-status"
ARCHIVE_RE = re.compile(r"^full-(\d{8}T\d{6}Z)-\d+\.tar\.age$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVES = 1000

def safe_regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("not a safe regular archive file")
    return info

def verified_archive(path: Path) -> bool:
    checksum_path = Path(str(path) + ".sha256")
    try:
        safe_regular(path)
        cinfo = safe_regular(checksum_path)
        if cinfo.st_size > 512:
            return False
        line = checksum_path.read_text(encoding="ascii").strip()
        expected, separator, name = line.partition("  ")
        if separator != "  " or not DIGEST_RE.fullmatch(expected):
            return False
        # sha256sum with an absolute path; don't follow arbitrary names.
        if name != str(path):
            return False
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected
    except (OSError, ValueError, UnicodeError):
        return False

def inspect() -> dict:
    now = datetime.now(timezone.utc)
    result = {
        "schemaVersion": 1,
        "checkedAt": now.isoformat(),
        "state": "unknown",
        "lastEncryptedBackupAt": None,
        "checksumVerified": False,
        "ageHours": None,
        "detail": "No verified encrypted full-state archive found",
    }
    if not BACKUPS.is_dir() or BACKUPS.is_symlink():
        result["detail"] = "Protected backup directory unavailable"
        return result
    candidates = []
    for path in BACKUPS.iterdir():
        match = ARCHIVE_RE.fullmatch(path.name)
        if match:
            try:
                when = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                if when <= now:
                    candidates.append((when, path))
            except ValueError:
                continue
    if len(candidates) > MAX_ARCHIVES:
        result["detail"] = "Too many archives for bounded inspection"
        return result
    for when, path in sorted(candidates, reverse=True):
        if verified_archive(path):
            age = max(0, round((now - when).total_seconds() / 3600, 1))
            result.update(
                state="observed" if age <= 24 * 7 else "stale",
                lastEncryptedBackupAt=when.isoformat(),
                checksumVerified=True,
                ageHours=age,
                detail="Encrypted archive SHA-256 matched at observation time; decryption and clean-host restore not verified",
            )
            return result
    return result

def publish(result: dict) -> None:
    if DEST_DIR.exists() and (DEST_DIR.is_symlink() or not DEST_DIR.is_dir()):
        raise RuntimeError("Unsafe status directory")
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    os.chown(DEST_DIR, 0, 0)
    os.chmod(DEST_DIR, 0o755)
    target = DEST_DIR / "status.json"
    if target.is_symlink():
        raise RuntimeError("Unsafe status file")
    fd, tmp = tempfile.mkstemp(prefix=".backup-status-", dir=DEST_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(json.dumps(result, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--publish", action="store_true")
    args = p.parse_args()
    if os.geteuid() != 0:
        p.error("root required to inspect protected backup directory")
    result = inspect()
    print(f"Backup observation: {result['state']}; checksum verified: {result['checksumVerified']}")
    print(result["detail"])
    if args.publish:
        publish(result)
        print("Sanitized read-only status published; archive/private key unchanged.")
    else:
        print("PLAN ONLY: no status file written.")
    return 0 if result["checksumVerified"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
