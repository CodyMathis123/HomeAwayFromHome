#!/usr/bin/env python3
"""Update only known Authelia session redirect URLs with protected rollback.

This tool must be run by root. It never displays the private Authelia
configuration, tokens, SMTP credentials, LDAP settings or session secrets.
A candidate is validated in the running Authelia container before replacing
the current configuration; :8443 is retained as fallback on the gateway.
"""
from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "data/control/authelia"
ACTIVE = DIRECTORY / "configuration.yml"
BACKUP = DIRECTORY / "configuration.before-default-https.yml"
CANDIDATE = DIRECTORY / "configuration.https443-candidate.yml"
CONTAINER = "homeawayfromhome-authelia-1"

URLS = {
    "authelia_url": (
        "https://auth.homeawayfromhome.house:8443",
        "https://auth.homeawayfromhome.house",
    ),
    "default_redirection_url": (
        "https://home.homeawayfromhome.house:8443",
        "https://home.homeawayfromhome.house",
    ),
}
LINE = re.compile(
    r'(?m)^([ \t]+)(authelia_url|default_redirection_url):[ \t]*'
    r'(?P<quote>["\']?)(?P<url>https://[^"\'\s#]+)(?P=quote)[ \t]*$'
)
SECTION = re.compile(r"(?m)^session:[ \t]*$")
NEXT_SECTION = re.compile(r"(?m)^[a-z][a-z0-9_]*:[ \t]*$")


def session_slice(document: str) -> tuple[int, int]:
    matches = list(SECTION.finditer(document))
    if len(matches) != 1:
        raise ValueError("Expected exactly one Authelia session section")
    start = matches[0].end()
    next_section = NEXT_SECTION.search(document, start)
    return start, next_section.start() if next_section else len(document)


def state_and_render(document: str, target: str) -> tuple[str, str]:
    start, end = session_slice(document)
    section = document[start:end]
    matches = list(LINE.finditer(section))
    if len(matches) != 2 or {m.group(2) for m in matches} != set(URLS):
        raise ValueError("Expected exactly one canonical Authelia and home redirect URL")
    observed = {}
    for match in matches:
        key, url = match.group(2), match.group("url")
        if url not in URLS[key]:
            raise ValueError("Unexpected Authelia redirect host or port")
        observed[key] = URLS[key].index(url)
    if len(set(observed.values())) != 1:
        raise ValueError("Mixed legacy and portless redirect values; refusing change")
    old_state = "portless" if next(iter(observed.values())) else "legacy"
    desired = 1 if target == "portless" else 0
    def rewrite(match: re.Match[str]) -> str:
        key = match.group(2)
        return match.group(0).replace(match.group("url"), URLS[key][desired], 1)
    revised = document[:start] + LINE.sub(rewrite, section) + document[end:]
    return old_state, revised


def private_active() -> tuple[str, os.stat_result]:
    if DIRECTORY.is_symlink() or ACTIVE.is_symlink() or not ACTIVE.is_file():
        raise RuntimeError("Authelia configuration unavailable or symlinked")
    metadata = ACTIVE.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("Authelia configuration must be a regular file")
    return ACTIVE.read_text(encoding="utf-8"), metadata


def exclusive_file(path: Path, text: str, uid: int, gid: int, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, mode)
    try:
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def validate_candidate() -> None:
    result = subprocess.run(
        ["docker", "exec", "-u", "65534:65534", CONTAINER, "authelia",
         "config", "validate", "--config", "/config/" + CANDIDATE.name],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=90,
    )
    if result.returncode != 0:
        raise RuntimeError("Authelia candidate rejected; active configuration unchanged")


def run(mode: str) -> str:
    current, metadata = private_active()
    state, next_config = state_and_render(current, "portless")
    if mode == "plan":
        if state == "legacy":
            if BACKUP.exists() or CANDIDATE.exists():
                raise RuntimeError("Existing backup or candidate requires review")
            return "PLAN: known legacy redirect values; protected backup and validated portless candidate will be prepared"
        return "PLAN: Authelia already uses portless redirect URLs"
    if mode == "apply":
        if state == "portless":
            return "PASS: Authelia canonical redirect URLs already portless"
        if BACKUP.exists() or CANDIDATE.exists():
            raise RuntimeError("Existing backup or candidate requires review; no overwrite")
        target = next_config
    else:
        if not BACKUP.is_file() or BACKUP.is_symlink() or CANDIDATE.exists():
            raise RuntimeError("Protected pre-migration backup missing or candidate exists")
        if state != "portless":
            raise RuntimeError("Active configuration is not portless; rollback refused")
        original = BACKUP.read_text(encoding="utf-8")
        original_state, original_rewritten = state_and_render(original, "legacy")
        if original_state != "legacy" or original_rewritten != original:
            raise RuntimeError("Backup does not contain known legacy redirect URLs")
        target = original
    exclusive_file(CANDIDATE, target, metadata.st_uid, metadata.st_gid,
                   stat.S_IMODE(metadata.st_mode))
    try:
        validate_candidate()
        if mode == "apply":
            exclusive_file(BACKUP, current, 0, 0, 0o600)
        os.replace(CANDIDATE, ACTIVE)
    finally:
        CANDIDATE.unlink(missing_ok=True)
    return ("PASS: portless Authelia URLs installed; protected backup retained"
            if mode == "apply" else
            "PASS: original Authelia URLs restored from protected backup")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Requires sudo; do not reveal root-protected configuration")
    mode = "plan" if args.plan else "apply" if args.apply else "rollback"
    try:
        print(run(mode))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        parser.exit(1, "ERROR: Authelia redirect migration could not be validated; no secrets printed.\n")


if __name__ == "__main__":
    main()
