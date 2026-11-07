"""Strict streaming validation/extraction of a modular monitoring tar backup.

Used only by isolated restore CI. Does not touch an installed stack. Archive
paths are allowlisted and links/devices are forbidden before scratch extraction.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile

VOLUMES = frozenset({
    "cooling-metrics", "cooling-grafana", "cooling-caddy-data",
    "cooling-caddy-config",
})
NEEDED = frozenset({
    "config/cooling.env", "state/generated/compose.generated.json",
    "state/generated/scrape.generated.yml", "state/generated/Caddyfile.generated",
    "state/secrets/grafana_admin_password",
})
MAX_MEMBERS = 500_000
MAX_FILE_BYTES = 32 * 1024**3
MAX_TOTAL_BYTES = 128 * 1024**3


class UnsafeArchive(ValueError):
    """Invalid paths, special entries, excess size, missing state or bad tar."""


def checked_name(name: str) -> str:
    if name in ("./", "."):
        return ""
    if name.startswith("./"):
        name = name[2:]
    if name.startswith("/") or "\\" in name or "\x00" in name:
        raise UnsafeArchive("Absolute or invalid archive member")
    parts = name.rstrip("/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise UnsafeArchive("Unsafe path components")
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise UnsafeArchive("Control character in archive member")
    normalized = str(PurePosixPath(*parts))
    if normalized in ("config", "state", "volumes"):
        return normalized
    if parts[0] == "config":
        if parts != ["config", "cooling.env"]:
            raise UnsafeArchive("Unexpected backup configuration path")
    elif parts[0] == "state":
        if len(parts) < 2 or parts[1] not in ("secrets", "generated"):
            raise UnsafeArchive("Unexpected state path")
    elif parts[0] == "volumes":
        if len(parts) < 2 or parts[1] not in VOLUMES:
            raise UnsafeArchive("Unknown Docker volume in archive")
    else:
        raise UnsafeArchive("Unexpected backup top-level path")
    return normalized


def process(stream, *, extract_to: Path | None = None) -> dict:
    """Read full tar. Writes only to pre-mounted scratch roots when requested."""
    if extract_to is not None:
        if not extract_to.is_dir() or extract_to.is_symlink():
            raise UnsafeArchive("Scratch extraction root is missing or symlinked")
        for label in ("config", "state", "volumes"):
            root = extract_to / label
            if root.is_symlink() or not root.is_dir():
                raise UnsafeArchive("Scratch mount is missing or symlinked")
    seen = set()
    volume_roots = set()
    directories = []
    file_count = byte_count = count = 0
    try:
        with tarfile.open(fileobj=stream, mode="r|*") as archive:
            for member in archive:
                count += 1
                if count > MAX_MEMBERS:
                    raise UnsafeArchive("Too many archive entries")
                path = checked_name(member.name)
                if path in seen:
                    raise UnsafeArchive("Duplicate archive entry")
                seen.add(path)
                if not (member.isdir() or member.isfile()):
                    raise UnsafeArchive("Links, devices and special entries are forbidden")
                if member.isfile():
                    if path in ("", "config", "state", "volumes"):
                        raise UnsafeArchive("Expected directory")
                    if member.size < 0 or member.size > MAX_FILE_BYTES:
                        raise UnsafeArchive("Oversized individual file")
                    byte_count += member.size
                    file_count += 1
                    if byte_count > MAX_TOTAL_BYTES:
                        raise UnsafeArchive("Archive exceeds rehearsal size limit")
                if path.startswith("volumes/"):
                    volume_roots.add(path.split("/")[1])
                if extract_to is not None and path:
                    destination = extract_to.joinpath(*PurePosixPath(path).parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        if path not in ("config", "state", "volumes"):
                            directories.append((destination, member))
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        fd = os.open(
                            destination, os.O_WRONLY | os.O_CREAT |
                            os.O_EXCL | os.O_NOFOLLOW, 0o600,
                        )
                        with os.fdopen(fd, "wb") as target:
                            source = archive.extractfile(member)
                            if source is None:
                                raise UnsafeArchive("Missing regular-file payload")
                            remaining = member.size
                            while remaining:
                                chunk = source.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    raise UnsafeArchive("Truncated archive content")
                                target.write(chunk)
                                remaining -= len(chunk)
                        os.chmod(destination, stat.S_IMODE(member.mode) & 0o777)
                        os.chown(destination, member.uid, member.gid)
            if extract_to is not None:
                for destination, member in reversed(directories):
                    os.chmod(destination, stat.S_IMODE(member.mode) & 0o777)
                    os.chown(destination, member.uid, member.gid)
    except (tarfile.TarError, EOFError, OSError) as error:
        raise UnsafeArchive(
            f"Invalid or incomplete backup stream: {type(error).__name__}"
        ) from error
    if NEEDED - seen:
        raise UnsafeArchive("Backup missing essential configuration entries")
    if volume_roots != VOLUMES:
        raise UnsafeArchive("Backup volume inventory incomplete")
    return {
        "validated": True, "entries": count, "files": file_count,
        "uncompressed_bytes": byte_count,
        "volumes": sorted(volume_roots), "rehearsal_only": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args(argv)
    try:
        result = process(sys.stdin.buffer, extract_to=args.extract_to)
    except UnsafeArchive as error:
        print(f"Unsafe restore rehearsal archive: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
