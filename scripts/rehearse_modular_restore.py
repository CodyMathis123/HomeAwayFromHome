#!/usr/bin/env python3
"""CI-only encrypted restore rehearsal into newly created, disposable volumes.

Not packaged for users, not a production restore path. Never mounts a live
Docker volume writable, restarts the installed project or publishes a port.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import quote

import backup_service
from component_changes import PRESERVED_VOLUMES
from components import SelectionError, parse_selection

IMAGE = "python:3.13-alpine"
VM_IMAGE = "victoriametrics/victoria-metrics:v1.155.0"
ARCHIVE_CHECKER = Path(__file__).with_name("rehearsal_archive.py").resolve()


def call(command: list[str], *, check=True):
    return subprocess.run(
        command, capture_output=True, text=True, check=check,
        timeout=120, stdin=subprocess.DEVNULL,
    )


def decrypt_to(archive: Path, identity: Path, command: list[str]) -> dict:
    """Pipe age output to a validator or scratch-container without a tar file."""
    decrypt = subprocess.Popen(
        ["age", "-d", "-i", str(identity), str(archive)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        assert decrypt.stdout is not None
        result = subprocess.run(
            command, stdin=decrypt.stdout, capture_output=True,
            text=True, timeout=1200,
        )
        decrypt.stdout.close()
        decrypt.communicate(timeout=90)
        if decrypt.returncode or result.returncode:
            # Avoid logging archive contents, private keys or paths.
            raise RuntimeError(
                "Age decryption or archive verification failed "
                f"(age={decrypt.returncode}, validator={result.returncode})."
            )
        return json.loads(result.stdout.strip())
    finally:
        if decrypt.poll() is None:
            decrypt.kill()
            decrypt.communicate()


def volumes(prefix: str) -> dict[str, str]:
    return {
        "config": f"{prefix}-config",
        "state": f"{prefix}-state",
        **{f"volumes/{name}": f"{prefix}-{name}" for name in PRESERVED_VOLUMES},
    }


def mounts(mapping: dict[str, str], *, readonly=False) -> list[str]:
    result = []
    for location, volume in mapping.items():
        flags = f"type=volume,source={volume},target=/rehearsal/{location}"
        if readonly:
            flags += ",readonly"
        result += ["--mount", flags]
    return result


def assert_new(names: list[str], network: str, container: str) -> None:
    for name in names:
        if call(["docker", "volume", "inspect", name], check=False).returncode == 0:
            raise RuntimeError("Rehearsal volume already exists.")
    if call(["docker", "network", "inspect", network], check=False).returncode == 0:
        raise RuntimeError("Rehearsal network already exists.")
    if call(["docker", "container", "inspect", container], check=False).returncode == 0:
        raise RuntimeError("Rehearsal container already exists.")


def inspect_restored(mapping: dict[str, str], config_digest: str):
    # This check runs in a disposable container against *only* scratch volumes.
    py = (
        "import hashlib,json,sqlite3;"
        "from pathlib import Path;"
        "b=Path('/rehearsal');"
        f"assert hashlib.sha256((b/'config/cooling.env').read_bytes()).hexdigest()=='{config_digest}';"
        "assert (b/'state/secrets/grafana_admin_password').is_file();"
        "db=b/'volumes/cooling-grafana/grafana.db';"
        "assert db.is_file() and db.stat().st_size>0;"
        "con=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);"
        "assert con.execute('PRAGMA integrity_check').fetchone()[0]=='ok';"
        "con.close();"
        "assert (b/'volumes/cooling-caddy-data/caddy/pki/authorities/local/root.crt').is_file();"
        "print('SCRATCH_CONFIG_GRAFANA_CA_VALIDATED')"
    )
    result = call([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        *mounts(mapping, readonly=True), IMAGE, "python", "-B", "-c", py,
    ])
    if "SCRATCH_CONFIG_GRAFANA_CA_VALIDATED" not in result.stdout:
        raise RuntimeError("Restored config/Grafana/Caddy validation failed.")


def check_metric(network: str, vm: str, volume: str, metric: str) -> None:
    call([
        "docker", "run", "-d", "--name", vm, "--network", network,
        "--mount", f"type=volume,source={volume},target=/victoria-metrics-data",
        VM_IMAGE, "-storageDataPath=/victoria-metrics-data", "-retentionPeriod=90d",
    ])
    query = quote(metric, safe="")
    py = (
        "import json,urllib.request;"
        f"u='http://{vm}:8428/api/v1/query?query={query}';"
        "j=json.load(urllib.request.urlopen(u,timeout=5));"
        "assert j.get('status')=='success' and j['data']['result'];"
        "print('RESTORED_METRIC_VERIFIED')"
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        result = call([
            "docker", "run", "--rm", "--network", network, "--read-only",
            IMAGE, "python", "-B", "-c", py,
        ], check=False)
        if result.returncode == 0 and "RESTORED_METRIC_VERIFIED" in result.stdout:
            return
        time.sleep(3)
    raise RuntimeError("Restored VictoriaMetrics did not return the expected sample.")


def rehearse(archive: Path, identity: Path, config: Path,
             state: Path, metric: str) -> dict:
    if os.environ.get("HOMEAWAY_RESTORE_REHEARSAL_CI") != "1":
        raise RuntimeError("Restore rehearsal disabled outside disposable CI.")
    if not identity.is_file() or identity.is_symlink():
        raise SelectionError("Missing or unsafe decryption identity.")
    if not config.is_file() or config.is_symlink():
        raise SelectionError("Missing or unsafe runtime configuration.")
    selected = next((
        line.split("=", 1)[1] for line in config.read_text().splitlines()
        if line.startswith("COOLING_COMPONENTS=")
    ), "cooling-monitoring")
    selection = parse_selection(selected)
    backup_service.validate_reference(
        archive, config, selection, backup_root=state, max_age_seconds=3600,
    )
    manifest = json.loads(
        archive.with_suffix(archive.suffix + ".json").read_text(encoding="utf-8")
    )
    preflight = decrypt_to(
        archive, identity, [sys.executable, "-B", str(ARCHIVE_CHECKER)]
    )
    if preflight["volumes"] != sorted(PRESERVED_VOLUMES):
        raise RuntimeError("Archive volume inventory differs from expected.")
    prefix = f"homeaway-restore-ci-{secrets.token_hex(6)}"
    network, vm = prefix + "-net", prefix + "-vm"
    mapping = volumes(prefix)
    assert_new(list(mapping.values()), network, vm)
    created, network_created = [], False
    try:
        call(["docker", "pull", "--quiet", IMAGE])
        call(["docker", "pull", "--quiet", VM_IMAGE])
        for name in mapping.values():
            call(["docker", "volume", "create", "--label",
                  "homeaway.rehearsal=true", name])
            created.append(name)
        extract = [
            "docker", "run", "--rm", "-i", "--network", "none", "--read-only",
            "--pids-limit", "128", "--cap-drop=ALL", "--cap-add=CHOWN",
            "--cap-add=DAC_OVERRIDE", "--cap-add=FOWNER",
            "--security-opt=no-new-privileges", *mounts(mapping),
            "--mount",
            f"type=bind,source={ARCHIVE_CHECKER},target=/app/rehearsal_archive.py,readonly",
            IMAGE, "python", "-B", "/app/rehearsal_archive.py",
            "--extract-to", "/rehearsal",
        ]
        extracted = decrypt_to(archive, identity, extract)
        if extracted["files"] != preflight["files"]:
            raise RuntimeError("Scratch restore file count changed.")
        inspect_restored(mapping, manifest["config_sha256"])
        call(["docker", "network", "create", "--internal", "--label",
              "homeaway.rehearsal=true", network])
        network_created = True
        check_metric(network, vm, mapping["volumes/cooling-metrics"], metric)
        return {
            "rehearsal_passed": True, "restored_metric_verified": True,
            "grafana_sqlite_verified": True, "caddy_public_ca_present": True,
            "restored_files": extracted["files"],
            "scope": "disposable-ci-docker-volumes-only",
        }
    finally:
        # Cleanup ONLY the unique resources created by this process.
        call(["docker", "rm", "-f", vm], check=False)
        if network_created:
            call(["docker", "network", "rm", network], check=False)
        for name in reversed(created):
            call(["docker", "volume", "rm", name], check=False)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--config", type=Path,
                        default=Path("/etc/homeaway-cooling/cooling.env"))
    parser.add_argument("--state", type=Path,
                        default=Path("/var/lib/homeaway-cooling"))
    parser.add_argument("--metric", default='sensordash_reading_value{host="ci-synthetic-cooling"}')
    args = parser.parse_args(argv)
    try:
        result = rehearse(args.archive, args.identity, args.config, args.state, args.metric)
    except (OSError, SelectionError, ValueError, RuntimeError,
            subprocess.SubprocessError) as error:
        print(f"Restore rehearsal failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
