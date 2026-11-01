"""Transactional writer for HomeAway's one canonical active PC inventory.

This is a *host-side* helper for the trusted CLI and future separately
privileged inventory broker. Do not import into the HTTP controller as a
writable /etc mount. The pcctl agent still reads only hosts.json, and existing
iOS Shortcut host IDs are never silently renamed.

All cooperative writers must acquire this lock. An administrator who edits
hosts.json with a text editor directly bypasses it; that workflow should be
disabled when the management UI becomes live.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Callable

from core import validate_hosts

MAX_INVENTORY_BYTES = 65536


class InventoryConflict(ValueError):
    """The inventory changed since the administrator read it."""


class InventoryStoreError(ValueError):
    """A damaged or unsafe inventory path was encountered."""


def revision_of(document: dict) -> str:
    """Stable content revision independent of JSON indentation/key order."""
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _regular_fd(fd: int, label: str) -> os.stat_result:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InventoryStoreError(label + " must be a regular, unlinked file")
    return info


def _read_existing(path: Path) -> tuple[dict, os.stat_result | None, bytes | None]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        return {"hosts": []}, None, None
    except OSError as exc:
        raise InventoryStoreError("Cannot open inventory safely") from exc
    try:
        info = _regular_fd(fd, "Inventory")
        with os.fdopen(fd, "rb", closefd=False) as source:
            raw = source.read(MAX_INVENTORY_BYTES + 1)
        if len(raw) > MAX_INVENTORY_BYTES:
            raise InventoryStoreError("Inventory exceeds size limit")
        try:
            data = json.loads(raw)
            validate_hosts(data)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise InventoryStoreError("Existing inventory is invalid") from exc
        return data, info, raw
    finally:
        os.close(fd)


def _stage(directory: Path, target: Path, payload: bytes,
           original: os.stat_result | None) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix="." + target.name + ".", dir=directory)
    staged = Path(temp_name)
    try:
        mode = stat.S_IMODE(original.st_mode) if original else 0o644
        if mode & 0o022:
            raise InventoryStoreError("Inventory must not be group/world writable")
        os.fchmod(fd, mode)
        if original:
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid) != (original.st_uid, original.st_gid):
                os.fchown(fd, original.st_uid, original.st_gid)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return staged
    except BaseException:
        if fd >= 0:
            os.close(fd)
        staged.unlink(missing_ok=True)
        raise


def update_inventory(
    path: Path,
    transform: Callable[[dict], dict],
    *,
    expected_revision: str | None = None,
) -> tuple[dict, str]:
    """Serialize a validated inventory update and atomically replace its file.

    A successful update saves the previous valid file as hosts.json.previous;
    it never edits the active inode in-place. The original owner and mode are
    preserved. A failed validation or failed replace leaves hosts.json intact.
    """
    path = Path(path)
    directory = path.parent
    if directory.is_symlink() or path.is_symlink():
        raise InventoryStoreError("Refusing symlinked inventory path")
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise InventoryStoreError("Refusing symlinked inventory directory")

    lock_file = directory / ("." + path.name + ".lock")
    try:
        lock_fd = os.open(lock_file,
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600)
    except OSError as exc:
        raise InventoryStoreError("Cannot open inventory lock safely") from exc
    staged: Path | None = None
    staged_previous: Path | None = None
    try:
        _regular_fd(lock_fd, "Inventory lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Recheck *under* the lock: all cooperating writers see the newest file.
        if path.is_symlink():
            raise InventoryStoreError("Refusing symlinked inventory path")
        current, original, raw = _read_existing(path)
        current_revision = revision_of(current)
        if expected_revision is not None and expected_revision != current_revision:
            raise InventoryConflict("Inventory changed; refresh before retrying")
        candidate = transform(copy.deepcopy(current))
        try:
            validate_hosts(candidate)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise InventoryStoreError("Candidate inventory is invalid") from exc
        if candidate == current:
            return current, current_revision

        payload = (json.dumps(candidate, indent=2, ensure_ascii=False) + "\n").encode()
        if len(payload) > MAX_INVENTORY_BYTES:
            raise InventoryStoreError("Candidate inventory exceeds size limit")

        staged = _stage(directory, path, payload, original)
        if raw is not None:
            # Keep only one previous complete, validated snapshot. Never copy
            # the in-progress file or include keys/passwords here.
            previous = path.with_name(path.name + ".previous")
            if previous.is_symlink():
                raise InventoryStoreError("Refusing symlinked rollback snapshot")
            staged_previous = _stage(directory, previous, raw, original)
            os.replace(staged_previous, previous)
            staged_previous = None
        os.replace(staged, path)
        staged = None
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return candidate, revision_of(candidate)
    finally:
        if staged:
            staged.unlink(missing_ok=True)
        if staged_previous:
            staged_previous.unlink(missing_ok=True)
        os.close(lock_fd)
