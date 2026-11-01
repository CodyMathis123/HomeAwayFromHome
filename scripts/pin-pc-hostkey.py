#!/usr/bin/env python3
"""Safely pin a Windows OpenSSH ED25519 host key after OUT-OF-BAND comparison.

An administrator first reads SHA256 host-key fingerprint from the Windows
console, NOT from the Pi/network scan. --plan scans + compares but does not
write. --apply requires root, refuses an existing pin, and only appends the
matching public host key to pcctl's known_hosts. No SSH private key is read.
"""
from __future__ import annotations

import argparse
import fcntl
import ipaddress
import os
import pwd
import re
import stat
import subprocess
import tempfile
from pathlib import Path

RFC1918 = tuple(ipaddress.IPv4Network(v) for v in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
EXPECTED_RE = re.compile(r"SHA256:[A-Za-z0-9+/]{40,44}$")
KNOWN_HOSTS = Path("/home/pcctl/.ssh/known_hosts")


def target_name(ip: str, port: int) -> str:
    address = ipaddress.IPv4Address(ip)
    if address not in RFC1918:
        raise ValueError("Only RFC1918 LAN/VPN IPv4 addresses are supported")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("SSH port is invalid")
    return str(address) if port == 22 else f"[{address}]:{port}"


def fingerprint_for_key(line: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", encoding="ascii") as keyfile:
        keyfile.write(line + "\n")
        keyfile.flush()
        result = subprocess.run(["ssh-keygen", "-lf", keyfile.name],
                                capture_output=True, text=True, timeout=4, check=False)
    parts = result.stdout.strip().split()
    if result.returncode != 0 or len(parts) < 3 or not parts[1].startswith("SHA256:"):
        raise ValueError("Unable to calculate scanned host-key fingerprint")
    return parts[1]


def discover(ip: str, port: int) -> tuple[str, str]:
    hostname = target_name(ip, port)
    result = subprocess.run(
        ["ssh-keyscan", "-T", "5", "-p", str(port), "-t", "ed25519", ip],
        capture_output=True, text=True, timeout=9, check=False)
    public = set()
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == hostname and parts[1] == "ssh-ed25519":
            if not re.fullmatch(r"[A-Za-z0-9+/=]{20,4096}", parts[2]):
                raise ValueError("Invalid host public-key encoding")
            public.add(line.strip())
    if len(public) != 1:
        raise ValueError("Exactly one unambiguous ED25519 host key is required")
    line = next(iter(public))
    return line, fingerprint_for_key(line)


def _safe_known_hosts_fd(path: Path) -> int:
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                 os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    info = os.fstat(fd)
    uid = pwd.getpwnam("pcctl").pw_uid
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid not in (0, uid):
        os.close(fd)
        raise ValueError("Refusing unsafe known_hosts file")
    if info.st_uid == 0:
        os.fchown(fd, uid, pwd.getpwnam("pcctl").pw_gid)
    os.fchmod(fd, 0o600)
    return fd


def pin(ip: str, port: int, expected: str, *, apply: bool,
        known_hosts: Path = KNOWN_HOSTS) -> dict:
    if not EXPECTED_RE.fullmatch(expected):
        raise ValueError("Provide the complete SHA256 fingerprint from the Windows console")
    hostname = target_name(ip, port)
    line, actual = discover(ip, port)
    if actual != expected:
        raise ValueError("Host-key fingerprint MISMATCH; no trust change made")
    if not apply:
        return {"verified": True, "pinned": False, "fingerprint": actual}
    if os.geteuid() != 0:
        raise PermissionError("--apply must run with sudo")
    parent = known_hosts.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("Unsafe or missing pcctl SSH directory")
    lock_path = parent / ".homeaway-known-hosts.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT |
                      os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ValueError("Unsafe known_hosts lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if known_hosts.is_symlink():
            raise ValueError("Refusing symlinked known_hosts")
        if known_hosts.exists():
            existing = subprocess.run(
                ["ssh-keygen", "-F", hostname, "-f", str(known_hosts)],
                capture_output=True, text=True, timeout=4, check=False)
            if existing.returncode == 0:
                raise ValueError("Host already pinned; review manually, never overwrite automatically")
        fd = _safe_known_hosts_fd(known_hosts)
        try:
            os.write(fd, (line + "\n").encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"verified": True, "pinned": True, "fingerprint": actual}
    finally:
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--expected", required=True, help="From the Windows PC's LOCAL console")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = pin(args.ip, args.port, args.expected, apply=args.apply)
    print("PASS: scanned key matches independently supplied SHA256 fingerprint")
    if result["pinned"]:
        print("Pinned public ED25519 host key in the existing pcctl known_hosts")
    else:
        print("PLAN ONLY: no trust changed. Inspect Windows setup before --apply.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, PermissionError, OSError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"Host-key enrollment refused: {exc}")
