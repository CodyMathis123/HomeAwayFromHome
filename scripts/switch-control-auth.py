#!/usr/bin/env python3
"""Switch HomeAway Authelia's first factor between file users and LLDAP.

Never rotates Authelia secrets, touches its SQLite/TOTP database, or starts
containers. A validated, root-readable copy of the original file-backend
configuration remains available for quick rollback.

Usage: sudo python3 scripts/switch-control-auth.py --apply
       sudo python3 scripts/switch-control-auth.py --rollback
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONF_DIR = ROOT / "data/control/authelia"
CONFIG = CONF_DIR / "configuration.yml"
ORIGINAL = CONF_DIR / "configuration.file-backend.yml"
SECRET = ROOT / "secrets/control/lldap_admin_password"
IMAGE = "authelia/authelia:4.39.28"


def replace_section(document: str, section: str, replacement: str) -> str:
    pattern = re.compile(r"(?ms)^" + re.escape(section) + r":\n.*?(?=^[a-z][a-z0-9_]*:\n|\Z)")
    if len(pattern.findall(document)) != 1:
        raise ValueError("Expected exactly one " + section + " section")
    return pattern.sub(lambda _: replacement, document, count=1)


def to_ldap(document: str, password: str) -> str:
    backend = re.search(r"(?ms)^authentication_backend:\n.*?(?=^[a-z][a-z0-9_]*:\n|\Z)", document)
    if backend is None:
        raise ValueError("Existing Authelia backend not found")
    if backend.group(0).strip() != (
        "authentication_backend:\n  file:\n    path: /config/users_database.yml"
    ):
        raise ValueError("Only the project's known file-backed configuration can be migrated")
    new_backend = (
        "authentication_backend:\n"
        "  refresh_interval: always\n"
        "  password_reset:\n"
        "    disable: true\n"
        "  ldap:\n"
        "    implementation: lldap\n"
        "    address: ldap://lldap:3890\n"
        "    base_dn: DC=homeaway,DC=home,DC=arpa\n"
        "    user: CN=admin,OU=people,DC=homeaway,DC=home,DC=arpa\n"
        "    password: " + json.dumps(password) + "\n"
    )
    document = replace_section(document, "authentication_backend", new_backend)
    rule = "- 'group:admins'\n      policy: two_factor"
    if (document.count("- 'group:admins'") != 1 or
        "- 'group:operators'" in document or
        "- 'group:viewers'" in document):
        raise ValueError("Unexpected existing role policy")
    if document.count(rule) != 1:
        raise ValueError("Expected one HomeAway two-factor admin rule")
    document = document.replace(
        rule,
        "- 'group:admins'\n"
        "        - 'group:operators'\n"
        "        - 'group:viewers'\n"
        "      policy: two_factor",
        1,
    )
    return document


def save_atomic(destination: Path, text: str, source: Path) -> None:
    info = source.stat()
    fd, tmp = tempfile.mkstemp(prefix=".auth-", suffix=".yml", dir=CONF_DIR)
    try:
        os.fchown(fd, info.st_uid, info.st_gid)
        os.fchmod(fd, stat.S_IMODE(info.st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, destination)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_candidate(text: str) -> None:
    path = CONF_DIR / "configuration.accounts-candidate.yml"
    if path.exists() or path.is_symlink():
        raise RuntimeError("An earlier validation file remains; remove it manually first")
    try:
        save_atomic(path, text, CONFIG)
        result = subprocess.run(
            ["docker", "run", "--rm", "--user", "65534:65534",
             "--volume", str(CONF_DIR) + ":/config:ro",
             IMAGE, "authelia", "config", "validate",
             "--config", "/config/" + path.name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=120, check=False,
        )
        if result.returncode:
            # Keep diagnostic limited; never print the generated configuration
            # or any password-bearing YAML lines.
            raise RuntimeError(
                "Authelia rejected candidate configuration. No active config changed."
                " Check Docker/Authelia validation logs locally."
            )
    finally:
        path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo")
    if CONFIG.is_symlink() or not CONFIG.is_file():
        parser.error("Authelia configuration missing or symlinked")
    if args.rollback:
        if ORIGINAL.is_symlink() or not ORIGINAL.is_file():
            parser.error("Original file-backend snapshot missing")
        restored = ORIGINAL.read_text(encoding="utf-8")
        if "    path: /config/users_database.yml" not in restored:
            parser.error("Rollback snapshot is not the original file backend")
        save_atomic(CONFIG, restored, CONFIG)
        print("Original file-backed Authelia configuration restored.")
        print("Recreate Authelia and disable HOMEAWAY_ACCOUNT_ADMIN_ENABLED to finish rollback.")
        return
    if ORIGINAL.exists() or ORIGINAL.is_symlink():
        parser.error("Original snapshot exists; refusing to overwrite it")
    if SECRET.is_symlink() or not SECRET.is_file():
        parser.error("Directory admin secret missing; run init-accounts.py")
    password = SECRET.read_text(encoding="utf-8").strip()
    if len(password) < 20:
        parser.error("Directory admin secret invalid")
    current = CONFIG.read_text(encoding="utf-8")
    candidate = to_ldap(current, password)
    validate_candidate(candidate)
    shutil.copy2(CONFIG, ORIGINAL)
    save_atomic(CONFIG, candidate, CONFIG)
    print("Authelia configuration switched to LLDAP; encrypted SQLite/TOTP untouched.")
    print("Original config saved beside it as configuration.file-backend.yml.")
    print("Recreate Authelia using the accounts Compose overlay to activate the new backend.")


if __name__ == "__main__":
    main()
