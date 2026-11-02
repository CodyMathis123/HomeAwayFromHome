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


# The original HomeAway deployment retains a second .home.arpa cookie
# configuration. Never change that cookie or its redirects during the
# homeawayfromhome.house canonical HTTPS migration.
PRODUCTION_COOKIE_DOMAIN = "homeawayfromhome.house"
LEGACY_COOKIE_DOMAIN = "homeaway.home.arpa"
COOKIE_DOMAIN_LINE = re.compile(
    r'(?m)^(?P<indent>[ \t]+)-[ \t]+domain:[ \t]*'
    r'(?P<quote>["\']?)(?P<domain>[a-zA-Z0-9.-]+)(?P=quote)[ \t]*$'
)


def session_cookie_blocks(section: str) -> list[tuple[str, int, int]]:
    """Identify only exact, well-formed cookie entries inside session.cookies.

    Reject ambiguous YAML (repeated domains, unknown domains, stray redirect
    declarations) rather than risk changing the wrong authentication site.
    """
    headers = list(re.finditer(r"(?m)^[ \t]+cookies:[ \t]*$", section))
    if len(headers) != 1:
        raise ValueError("Expected a single session.cookies list")
    header = headers[0]
    header_indent = len(header.group(0)) - len(header.group(0).lstrip())
    lines = section[header.end():].splitlines(keepends=True)
    cookie_end = header.end()
    for line in lines:
        whitespace = len(line) - len(line.lstrip(" \t"))
        if line.strip() and not line.lstrip().startswith("#") and whitespace <= header_indent:
            break
        cookie_end += len(line)
    region = section[header.end():cookie_end]
    matches = list(COOKIE_DOMAIN_LINE.finditer(region))
    if not 1 <= len(matches) <= 2:
        raise ValueError("Unsupported number of session cookie domains")
    domains = [match.group("domain") for match in matches]
    if domains.count(PRODUCTION_COOKIE_DOMAIN) != 1 or len(set(domains)) != len(domains):
        raise ValueError("Expected exactly one canonical production cookie domain")
    if any(domain not in (PRODUCTION_COOKIE_DOMAIN, LEGACY_COOKIE_DOMAIN)
           for domain in domains):
        raise ValueError("Unrecognized session cookie domain")
    boundaries = [
        (header.end() + match.start(), header.end() + (
            matches[i + 1].start() if i + 1 < len(matches) else len(region)))
        for i, match in enumerate(matches)
    ]
    # Require every redirect declaration (including unsupported syntax) to
    # belong to one of these cookie blocks; never skip a stray field.
    declared = [re.findall(r"(?m)^[ \t]+" + re.escape(key) + r":[ \t]*", region)
                for key in URLS]
    if any(len(group) != len(matches) for group in declared):
        raise ValueError("Redirect declarations do not match cookie count")
    return [(domain, begin, end) for domain, (begin, end) in zip(domains, boundaries)]


def state_and_render(document: str, target: str) -> tuple[str, str]:
    if target not in ("portless", "legacy"):
        raise ValueError("Unsupported target state")
    start, end = session_slice(document)
    section = document[start:end]
    blocks = session_cookie_blocks(section)
    updates: list[tuple[int, int, str]] = []
    observed = {}
    for domain, begin, finish in blocks:
        block = section[begin:finish]
        lines = list(LINE.finditer(block))
        if len(lines) != 2 or {match.group(2) for match in lines} != set(URLS):
            raise ValueError("Expected one URL pair per cookie domain")
        for match in lines:
            # Detect malformed/duplicate declarations before editing.
            key = match.group(2)
            if len(re.findall(r"(?m)^[ \t]+" + re.escape(key) + r":[ \t]*", block)) != 1:
                raise ValueError("Duplicate or unsupported redirect declaration")
            if domain == PRODUCTION_COOKIE_DOMAIN:
                if match.group("url") not in URLS[key]:
                    raise ValueError("Production cookie redirect does not match known host")
                observed[key] = URLS[key].index(match.group("url"))
                desired = 1 if target == "portless" else 0
                replacement = match.group(0).replace(
                    match.group("url"), URLS[key][desired], 1)
                updates.append((start + begin + match.start(),
                                start + begin + match.end(), replacement))
        # Deliberately preserve every byte of the .home.arpa cookie, even if it
        # has different operational URLs. Never rewrite its redirects.
    if len(observed) != 2 or len(set(observed.values())) != 1:
        raise ValueError("Production cookie has mixed redirect port states")
    old_state = "portless" if next(iter(observed.values())) else "legacy"
    result = document
    for begin, finish, replacement in reversed(updates):
        result = result[:begin] + replacement + result[finish:]
    return old_state, result


def safe_redirect_report(document: str) -> str:
    """Describe only known field names, counts and statuses, never their URLs.

    Existing Authelia may have multiple cookie domains or locally chosen
    redirect URLs. Inspect their shape, but never rewrite an unknown layout.
    """
    try:
        start, end = session_slice(document)
    except ValueError:
        return "session_sections=unexpected"
    section = document[start:end]
    parsed = list(LINE.finditer(section))
    statuses = []
    for key in URLS:
        declared = len(re.findall(
            r"(?m)^[ \t]+" + re.escape(key) + r":[ \t]*", section))
        matches = [match for match in parsed if match.group(2) == key]
        legacy = sum(match.group("url") == URLS[key][0] for match in matches)
        portless = sum(match.group("url") == URLS[key][1] for match in matches)
        unknown = len(matches) - legacy - portless
        unparsed = max(0, declared - len(matches))
        statuses.append(
            f"{key}(fields={declared},legacy={legacy},portless={portless},"
            f"unexpected={unknown},unparsed={unparsed})")
    return "; ".join(statuses)


def safe_preflight_report() -> str:
    """Only a fixed schema and binary presence indicators are printed."""
    if DIRECTORY.is_symlink() or ACTIVE.is_symlink():
        layout = "configuration_symlink_detected"
    elif not ACTIVE.is_file():
        layout = "configuration_missing_or_not_regular"
    else:
        try:
            layout = safe_redirect_report(ACTIVE.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            layout = "configuration_unreadable"
    return (
        "authelia=" + layout
        + "; previous_backup=" + ("present" if BACKUP.exists() else "absent")
        + "; candidate=" + ("present" if CANDIDATE.exists() else "absent")
    )


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
    group.add_argument("--inspect", action="store_true",
                       help="Report safe counts/statuses of redirect fields only")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Requires sudo; do not reveal root-protected configuration")
    if args.inspect:
        print("SAFE INSPECTION: " + safe_preflight_report())
        return
    mode = "plan" if args.plan else "apply" if args.apply else "rollback"
    try:
        print(run(mode))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        # Exception bodies can contain private paths/URLs: never emit them.
        parser.exit(1, "ERROR: Authelia redirect preflight blocked. "
                    "Run sudo python3 scripts/update-authelia-canonical-urls.py --inspect "
                    "for a safe field-status report; no secrets printed.\n")


if __name__ == "__main__":
    main()
