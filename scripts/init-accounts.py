#!/usr/bin/env python3
"""Idempotent local LLDAP setup. Does NOT start, stop, or reconfigure services.

Run: sudo python3 scripts/init-accounts.py
Secrets are never displayed, overwritten, or committed.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "secrets/control"
ACCOUNT_FILES = {
    "lldap_admin_password": 1000,
    "lldap_jwt_secret": 1000,
    "lldap_key_seed": 1000,
    "account_broker_token": 65534,
}


def secret_file(path: Path, gid: int) -> str:
    if path.is_symlink():
        raise RuntimeError("Refusing symlink: " + str(path))
    if not path.exists():
        value = (secrets.token_urlsafe(48) + "\n").encode("ascii")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(value)
        status = "created"
    else:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size < 40:
            raise RuntimeError("Existing account secret invalid: " + str(path))
        status = "retained"
    # Compose file-backed secrets retain host file owner/mode, even when mounted
    # inside containers. UID 1000 is the pinned LLDAP image's runtime identity;
    # the broker joins GID 1000 but runs as UID 65534. No world-readability.
    os.chown(path, 0, gid)
    os.chmod(path, 0o640)
    return status


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Use sudo: account secrets require restricted ownership")
    if not (ROOT / "data/control/authelia/configuration.yml").is_file():
        raise SystemExit("Existing Authelia installation not found; refusing account setup")
    CONTROL.mkdir(parents=True, exist_ok=True, mode=0o700)
    if CONTROL.is_symlink():
        raise SystemExit("Refusing symlinked secrets directory")
    os.chown(CONTROL, 0, 0)
    os.chmod(CONTROL, 0o700)

    for name, gid in ACCOUNT_FILES.items():
        print(name + ": " + secret_file(CONTROL / name, gid))

    for path, owner in ((ROOT / "data/control/lldap", 1000),
                        (ROOT / "data/control/account-audit", 65534)):
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise SystemExit("Refusing symlinked account data directory")
        os.chown(path, owner, owner)
        os.chmod(path, 0o750)

    print("Account secrets and data directories initialized; running services unchanged.")
    print("Directory admin password is stored in secrets/control/lldap_admin_password.")
    print("Never paste that file or its contents into chat, logs, or Git.")


if __name__ == "__main__":
    main()
