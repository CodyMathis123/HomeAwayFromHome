#!/usr/bin/env python3
"""Safely add canonical DNS admin authorization to an existing Authelia config.

Runs as root on the Pi. Does not print Authelia config or credentials.
Only the exact canonical /dns/* resource is added (admins + two_factor).
"""
import argparse
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "data/control/authelia"
ACTIVE = DIRECTORY / "configuration.yml"
CANDIDATE = DIRECTORY / "configuration.dns-admin-candidate.yml"
BACKUP = DIRECTORY / "configuration.before-dns-admin.yml"
CONTAINER = "homeawayfromhome-authelia-1"
RULE = """    - domain: home.homeawayfromhome.house
      resources:
        - '^/dns(?:/.*)?$'
      subject:
        - 'group:admins'
      policy: two_factor
"""


def updated(text: str) -> tuple[bool, str]:
    sections = list(re.finditer(r"(?m)^([a-z][a-z0-9_]*):[ \t]*$", text))
    access = [i for i, m in enumerate(sections) if m.group(1) == "access_control"]
    if len(access) != 1:
        raise ValueError("Unexpected access-control layout")
    index = access[0]
    begin = sections[index].end()
    end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
    block = text[begin:end]
    if len(re.findall(r"(?m)^  rules:[ \t]*$", block)) != 1:
        raise ValueError("Access-control rules are missing or ambiguous")
    if len(re.findall(r"(?m)^  default_policy:[ \t]*deny[ \t]*$", block)) != 1:
        raise ValueError("Unexpected default access policy")
    if re.search(r"(?m)^\s*-\s*['\"]?\^?/dns", block):
        raise ValueError("DNS rule already exists with unsupported structure; review manually")
    if RULE.strip() in block:
        return False, text
    # Never add another rule if this resource is already mentioned in a different form.
    if "/dns" in block:
        raise ValueError("Existing DNS policy requires manual review")
    return True, text[:end] + RULE + text[end:]


def check_file():
    if DIRECTORY.is_symlink() or ACTIVE.is_symlink() or not ACTIVE.is_file():
        raise ValueError("Active config missing or symlinked")
    return ACTIVE.read_text(encoding="utf-8"), ACTIVE.stat()


def write_private(path, content, meta):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(fd, meta.st_uid, meta.st_gid)
        os.fchmod(fd, stat.S_IMODE(meta.st_mode))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def cmd(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False, timeout=90).returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo")
    try:
        current, meta = check_file()
        needed, candidate = updated(current)
        if not needed:
            print("PASS: DNS authorization rule already present")
            return
        if BACKUP.exists() or CANDIDATE.exists():
            raise ValueError("Protected backup or candidate already exists")
        if args.plan:
            print("PLAN: add admins + two_factor rule for /dns/*; validate, back up and restart Authelia")
            return
        write_private(CANDIDATE, candidate, meta)
        try:
            if not cmd("docker", "exec", "-u", "65534:65534", CONTAINER,
                       "authelia", "config", "validate", "--config",
                       "/config/" + CANDIDATE.name):
                raise ValueError("Candidate validation failed")
            # A separate protected backup is kept for manual recovery.
            write_private(BACKUP, current, meta)
            os.replace(CANDIDATE, ACTIVE)
            if not cmd("docker", "restart", CONTAINER):
                shutil.copy2(BACKUP, ACTIVE)
                cmd("docker", "restart", CONTAINER)
                raise ValueError("Authelia restart failed; restored original configuration")
            if not cmd("docker", "exec", CONTAINER, "authelia",
                       "config", "validate", "--config", "/config/configuration.yml"):
                shutil.copy2(BACKUP, ACTIVE)
                cmd("docker", "restart", CONTAINER)
                raise ValueError("Active configuration failed validation; restored original")
        finally:
            CANDIDATE.unlink(missing_ok=True)
        print("PASS: DNS admin policy installed and Authelia restarted; protected backup retained")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        parser.exit(1, "ERROR: DNS authorization migration blocked or failed; inspect Authelia safely. No secrets printed.\n")


if __name__ == "__main__":
    main()
