"""Narrow, reversible changes for optional monitoring components.

Never removes named volumes, deletes secrets or changes the SensorDash intake
identity. Requires an age-encrypted full-state backup. The local checkpoint
only reverts managed configuration and service topology.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import secrets
import tempfile
import time

from components import SelectionError, resolve
from component_changes import transition
import compose_generator
import backup_service


@contextmanager
def operation_lock(state: Path):
    if not state.is_dir() or state.is_symlink():
        raise SelectionError("Unsafe installation state directory.")
    lockpath = state / ".operator.lock"
    if lockpath.is_symlink():
        raise SelectionError("Unsafe lockfile symlink.")
    fd = os.open(lockpath, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SelectionError("Another backup/component operation is running.") from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def replace_file(path: Path, payload: bytes) -> None:
    if path.is_symlink():
        raise SelectionError(f"Refusing symlinked managed file: {path}")
    fd, tmp = tempfile.mkstemp(prefix=".homeaway-change-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def selected_config(config: bytes, target: tuple[str, ...]) -> bytes:
    text = config.decode("ascii")
    lines = text.splitlines()
    matches = [i for i, x in enumerate(lines) if x.startswith("COOLING_COMPONENTS=")]
    if len(matches) > 1:
        raise SelectionError("Duplicate component selection in installed configuration.")
    value = "COOLING_COMPONENTS=" + ",".join(target)
    if matches:
        lines[matches[0]] = value
    else:
        lines.append(value)
    return ("\n".join(lines) + "\n").encode("ascii")


def checkpoint(config: Path, state: Path, plan: dict) -> Path:
    base = state / "rollback-checkpoints"
    if base.is_symlink():
        raise SelectionError("Rollback checkpoint directory may not be symlinked.")
    base.mkdir(mode=0o700, exist_ok=True)
    base.chmod(0o700)
    folder = Path(tempfile.mkdtemp(prefix="monitoring-change-", dir=base))
    folder.chmod(0o700)
    replace_file(folder / "cooling.env", config.read_bytes())
    for name in compose_generator.GENERATED:
        path = state / "generated" / name
        if not path.is_file() or path.is_symlink():
            raise SelectionError(f"Missing pre-change managed file: {path}")
        replace_file(folder / name, path.read_bytes())
    replace_file(
        folder / "transition.json",
        (json.dumps(plan, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    return folder


def ensure_portal_secret(state: Path) -> None:
    path = state / "secrets/portal_password"
    if path.is_symlink():
        raise SelectionError("Portal password path is a symlink.")
    if path.exists():
        if not path.is_file() or not path.read_bytes().strip():
            raise SelectionError("Existing Portal password is not valid.")
        return
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write((secrets.token_urlsafe(32) + "\n").encode("ascii"))
    os.chown(path, 65534, 65534)
    path.chmod(0o600)


def running_services(docker_command, run) -> set[str]:
    result = run(docker_command("ps", "--services", "--status", "running"), capture=True)
    return set(result.stdout.splitlines())


def change(config: Path, state: Path, root: Path, catalog: dict[str, dict],
           old: tuple[str, ...], new: tuple[str, ...],
           encrypted_backup: Path, docker_command, run) -> dict:
    if os.geteuid() != 0:
        raise SelectionError("Scoped changes require sudo.")
    plan = transition(catalog, old, new)
    backup_service.validate_reference(encrypted_backup, config, old, backup_root=state)
    before = resolve(catalog, old)
    prior = compose_generator.render(before, config, state, root)
    if compose_generator.differences(prior, state):
        raise SelectionError(
            "Installed Compose/config differs from expected package version. "
            "Run normal plan/start first; migration refused."
        )
    if running_services(docker_command, run) != set(before["services"]):
        raise SelectionError("Installed services are not all running; change aborted.")
    target = resolve(catalog, new)
    generated = compose_generator.render(target, config, state, root)
    old_config = config.read_bytes()
    next_config = selected_config(old_config, new)
    checkpoint_path = checkpoint(config, state, plan)
    if "home-portal" in new:
        ensure_portal_secret(state)
    try:
        replace_file(config, next_config)
        compose_generator.write(generated, state)
        run(docker_command("config", "-q"))
        listed = set(run(docker_command("config", "--services"), capture=True).stdout.splitlines())
        if listed != set(target["services"]):
            raise SelectionError("Generated Compose graph differs from resolved target.")
        run(docker_command("up", "-d", "--build", "--remove-orphans"))
        deadline = time.monotonic() + 75
        while running_services(docker_command, run) != set(target["services"]):
            if time.monotonic() >= deadline:
                raise RuntimeError("Selected services did not all reach running state.")
            time.sleep(3)
    except Exception as error:
        try:
            replace_file(config, old_config)
            compose_generator.write(prior, state)
            run(docker_command("up", "-d", "--build", "--remove-orphans"))
            if running_services(docker_command, run) != set(before["services"]):
                raise RuntimeError("Old services did not return to running.")
        except Exception as rollback_error:
            raise RuntimeError(
                "Component change failed AND automatic rollback failed. "
                f"Checkpoint: {checkpoint_path}. Restore manually; do not delete volumes."
            ) from rollback_error
        raise RuntimeError(
            "Component change failed; old configuration and services restored. "
            f"Checkpoint: {checkpoint_path}"
        ) from error
    return {"applied": True, "checkpoint": str(checkpoint_path), **plan}
