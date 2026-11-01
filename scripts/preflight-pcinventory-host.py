#!/usr/bin/env python3
"""Read-only Pi preflight for HomeAway's opt-in computer-management installer.

Never prints host addresses, MACs, SSH keys or known_hosts. Does not run SSH
commands, open network sockets, change ownership or modify systemd units.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pwd
import stat
from pathlib import Path

DEFAULT_INVENTORY = Path("/etc/homeaway-pcctl/hosts.json")
DEFAULT_CORE = Path(__file__).resolve().parents[1] / "control/pcctl/core.py"
DEFAULT_SSH = Path("/home/pcctl/.ssh")


class PreflightError(ValueError):
    pass


def _mode(info: os.stat_result) -> str:
    return f"{stat.S_IMODE(info.st_mode):04o}"


def check_file(path: Path, *, allow_missing: bool = False,
               require_private_writes: bool = True) -> os.stat_result | None:
    """Reject unsafe inodes; enforce non-writable group/other for sensitive files.

    A checkout's *source code* can legitimately be group-writable depending
    on the user's Git checkout permissions. That is separate from the
    protections required for live inventory and SSH trust files.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        if allow_missing:
            return None
        raise PreflightError(f"{path.name}: missing")
    except OSError as exc:
        raise PreflightError(f"{path.name}: unsafe or unreadable") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PreflightError(f"{path.name}: must be an ordinary singly-linked file")
        if require_private_writes and stat.S_IMODE(info.st_mode) & 0o022:
            raise PreflightError(f"{path.name}: group/world writable")
        return info
    finally:
        os.close(fd)


def check_inventory(path: Path, core_path: Path) -> tuple[int, str]:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PreflightError("Active inventory parent directory is missing or unsafe")
    folder = path.parent.stat()
    if stat.S_IMODE(folder.st_mode) & 0o022:
        raise PreflightError("Active inventory parent is group/world writable")
    info = check_file(path)
    assert info is not None
    if info.st_size > 65536:
        raise PreflightError("Active inventory exceeds the accepted size limit")
    # Validate that the checkout source is an ordinary file; do not apply
    # live-inventory permissions to a Git worktree (often mode 0664).
    # The operator explicitly trusts the checked-out script when invoking
    # the preflight; the protected inventory and key checks stay strict.
    core_info = check_file(core_path, require_private_writes=False)
    assert core_info is not None
    spec = importlib.util.spec_from_file_location("_homeaway_pcctl_preflight_core", core_path)
    if spec is None or spec.loader is None:
        raise PreflightError("PC control validation module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            with os.fdopen(fd, "rb", closefd=False) as handle:
                data = json.load(handle)
        finally:
            os.close(fd)
        hosts = module.validate_hosts(data)
    except (ValueError, TypeError, KeyError, UnicodeError, OSError) as exc:
        raise PreflightError("Active inventory failed canonical pcctl validation") from exc
    return len(hosts), _mode(info)


def inspect_ssh(ssh_dir: Path, pcctl_uid: int) -> list[str]:
    """Only assess readiness. A missing key is not a reason to break existing WOL."""
    notes = []
    try:
        info = ssh_dir.lstat()
    except FileNotFoundError:
        return ["PENDING: pcctl SSH directory absent; pairing cannot be verified yet"]
    except PermissionError:
        if os.geteuid() != 0:
            return ["PENDING: SSH permissions hidden from ordinary user; rerun sudo --plan for complete checks"]
        raise PreflightError("pcctl SSH directory unreadable")
    if not stat.S_ISDIR(info.st_mode):
        raise PreflightError("pcctl SSH directory is unsafe")
    if info.st_uid != pcctl_uid or stat.S_IMODE(info.st_mode) & 0o077:
        raise PreflightError("pcctl SSH directory needs pcctl ownership and private permissions")
    for name in ("id_ed25519", "known_hosts"):
        entry = ssh_dir / name
        # A symlink must never be downgraded to a mere "uninspected" warning,
        # even when this plan is run as a non-root user.
        if entry.is_symlink():
            raise PreflightError(f"{name}: symlink is unsafe")
        try:
            result = check_file(entry, allow_missing=True)
        except PreflightError:
            if os.geteuid() != 0:
                notes.append(f"PENDING: {name} unreadable; rerun sudo --plan for full checks")
                continue
            raise
        if result is None:
            notes.append(f"PENDING: {name} absent; new Windows pairing will require it")
            continue
        if result.st_uid not in (pcctl_uid, 0):
            raise PreflightError(f"{name}: ownership unexpected")
        if name == "id_ed25519":
            if result.st_uid != pcctl_uid or stat.S_IMODE(result.st_mode) & 0o077:
                raise PreflightError("pcctl SSH private key must be owned by pcctl and unreadable by other users")
        else:
            # known_hosts contains public server keys, commonly mode 0644.
            # The SSH verifier still needs to be able to read it as pcctl.
            if result.st_uid == 0 and not (stat.S_IMODE(result.st_mode) & 0o004):
                raise PreflightError("pcctl cannot read root-owned known_hosts")

    return notes


def preflight(*, inventory: Path, core: Path, ssh_dir: Path,
              pcctl_uid: int | None = None) -> tuple[int, list[str]]:
    if pcctl_uid is None:
        try:
            pcctl_uid = pwd.getpwnam("pcctl").pw_uid
        except KeyError as exc:
            raise PreflightError("Existing pcctl Linux account is required") from exc
    count, mode = check_inventory(inventory, core)
    messages = [f"PASS: {count} existing active computer(s) validated; hosts.json mode {_mode_to_report(mode)}"]
    messages.extend(inspect_ssh(ssh_dir, pcctl_uid))
    return count, messages


def _mode_to_report(mode: str) -> str:
    # Deliberately emit permissions only, never file content.
    return mode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    args = parser.parse_args()
    try:
        count, messages = preflight(inventory=Path(args.inventory), core=DEFAULT_CORE,
                                    ssh_dir=DEFAULT_SSH)
        for message in messages:
            print(message)
        print("PASS: read-only preflight; no inventory, keys, sockets or services changed")
    except (PreflightError, OSError) as exc:
        raise SystemExit("STOP: computer inventory preflight failed: " + str(exc))


if __name__ == "__main__":
    main()
