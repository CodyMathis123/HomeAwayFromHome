"""Offline, age-encrypted full-state snapshots for modular monitoring.

No plaintext backup files. Explicit container quiescence, fixed Docker
volume allowlist, private temporary output, no delete/prune/restore action.
Snapshots are also a prerequisite for approved in-place component changes.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from components import SelectionError
from component_changes import PRESERVED_VOLUMES

IMAGE = "alpine:3.21"
PROJECT = "homeaway-cooling"
RECIPIENT = re.compile(r"^age1[023456789acdefghjklmnpqrstuvwxyz]{55,85}$")


def capture(config: Path, state: Path, selection: tuple[str, ...],
            recipient: str, docker_command, run) -> Path:
    """Stop the scoped project, stream snapshot to age, start again in finally."""
    if not RECIPIENT.fullmatch(recipient):
        raise SelectionError("A valid age recipient (age1...) is required.")
    if not config.is_file() or config.is_symlink():
        raise SelectionError("Backup configuration is missing or symlinked.")
    for path in (state, state / "secrets", state / "generated"):
        if not path.is_dir() or path.is_symlink():
            raise SelectionError(f"Unsafe backup state directory: {path}")
    for name in PRESERVED_VOLUMES:
        volume = f"{PROJECT}_{name}"
        run(["docker", "volume", "inspect", volume], capture=True)
    run(["docker", "image", "inspect", IMAGE], capture=True)
    backups = state / "backups"
    if backups.is_symlink():
        raise SelectionError("Refusing symlinked backup destination.")
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    backups.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backups / f"modular-monitoring-{stamp}-{os.urandom(4).hex()}.tar.age"
    temporary = destination.with_suffix(".partial")
    mounts = [
        "--mount", f"type=bind,source={config},target=/snapshot/config/cooling.env,readonly",
        "--mount", f"type=bind,source={state / 'secrets'},target=/snapshot/state/secrets,readonly",
        "--mount", f"type=bind,source={state / 'generated'},target=/snapshot/state/generated,readonly",
    ]
    for name in PRESERVED_VOLUMES:
        mounts += [
            "--mount", f"type=volume,source={PROJECT}_{name},target=/snapshot/volumes/{name},readonly",
        ]
    archive = [
        "docker", "run", "--rm", "--pull=never", "--network", "none",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        *mounts, IMAGE, "tar", "-cf", "-", "-C", "/snapshot", ".",
    ]
    # This stops all writers for a consistent point-in-time volume capture.
    # Do not use docker compose down, especially not with -v.
    run(docker_command("stop"))
    snapshot_error = None
    try:
        with temporary.open("xb") as output:
            temporary.chmod(0o600)
            tar = subprocess.Popen(archive, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
            assert tar.stdout is not None
            try:
                cipher = subprocess.Popen(["age", "-r", recipient],
                                          stdin=tar.stdout, stdout=output,
                                          stderr=subprocess.PIPE)
                tar.stdout.close()
                _, err = cipher.communicate(timeout=600)
                rc = tar.wait(timeout=60)
                if cipher.returncode != 0 or rc != 0:
                    raise RuntimeError(
                        "Encrypted snapshot failed; container or age command returned error."
                    )
            finally:
                if tar.poll() is None:
                    tar.kill()
                    tar.wait()
        if temporary.stat().st_size < 100:
            raise RuntimeError("Encrypted snapshot unexpectedly empty.")
        os.replace(temporary, destination)
        manifest = {
            "schema_version": 1,
            "created_at": int(time.time()),
            "project": PROJECT,
            "selection": list(selection),
            "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "archive_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "archive_name": destination.name,
            "volumes": list(PRESERVED_VOLUMES),
            "encryption": "age",
            "quiesced": True,
        }
        metadata = destination.with_suffix(destination.suffix + ".json")
        with metadata.open("x", encoding="ascii") as file:
            os.chmod(metadata, 0o600)
            json.dump(manifest, file, indent=2, sort_keys=True)
            file.write("\n")
    except Exception as error:
        snapshot_error = error
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
    try:
        run(docker_command("start"))
    except Exception as error:
        if snapshot_error is not None:
            raise RuntimeError(
                "Snapshot failed and scoped containers could not restart; manual recovery needed."
            ) from error
        raise RuntimeError(
            "Snapshot was encrypted but containers did not restart; inspect project immediately."
        ) from error
    if snapshot_error is not None:
        raise snapshot_error
    return destination


def validate_reference(path: Path, config: Path,
                       selection: tuple[str, ...], *, max_age_seconds=3600) -> None:
    if path.is_symlink() or not path.is_file():
        raise SelectionError("Encrypted backup file does not exist or is symlinked.")
    info = path.with_suffix(path.suffix + ".json")
    if info.is_symlink() or not info.is_file():
        raise SelectionError("Signed-off backup metadata is unavailable.")
    metadata = json.loads(info.read_text(encoding="utf-8"))
    if metadata.get("project") != PROJECT or metadata.get("schema_version") != 1:
        raise SelectionError("Backup belongs to an incompatible project.")
    if metadata.get("selection") != list(selection):
        raise SelectionError("Backup selection does not match installed components.")
    if metadata.get("archive_name") != path.name:
        raise SelectionError("Backup filename does not match manifest.")
    if metadata.get("volumes") != list(PRESERVED_VOLUMES):
        raise SelectionError("Backup volume inventory differs.")
    if metadata.get("encryption") != "age" or not metadata.get("quiesced"):
        raise SelectionError("Backup must be a quiesced age-encrypted archive.")
    if not 0 <= time.time() - metadata["created_at"] <= max_age_seconds:
        raise SelectionError("Backup is older than the one-hour migration window.")
    if hashlib.sha256(config.read_bytes()).hexdigest() != metadata["config_sha256"]:
        raise SelectionError("Installed config changed since backup.")
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["archive_sha256"]:
        raise SelectionError("Backup checksum mismatch.")
