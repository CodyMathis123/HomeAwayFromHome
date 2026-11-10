#!/usr/bin/env python3
"""CI-only clean-runner recovery of a modular monitoring installation.

Consumes a wholly synthetic age-encrypted backup on a *different* disposable
Linux runner. Nothing is written into /etc/homeaway-cooling, the source
project, or its volumes. Not shipped as a production restore command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import backup_service
import compose_generator
from component_changes import PRESERVED_VOLUMES
from components import load_catalog, parse_selection, resolve
import importlib.util

_INTEGRATION_PATH = Path(__file__).with_name("integration-cooling-linux.py")
_spec = importlib.util.spec_from_file_location("clean_recovery_integration", _INTEGRATION_PATH)
assert _spec is not None and _spec.loader is not None
_integration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_integration)
METRIC = _integration.METRIC
gateway_root = _integration.gateway_root
query_metrics = _integration.query_metrics
request = _integration.request
wait_for_service = _integration.wait_for_service
from rehearse_modular_restore import (
    IMAGE, ARCHIVE_CHECKER, mounts, volumes, assert_new, decrypt_to,
    inspect_restored,
)

REHEARSAL_PREFIX = "homeaway-clean-ci-"
PORT = 19444
EXPECTED_SERVICES = frozenset({
    "victoria-metrics", "grafana", "telemetry-ingest", "gateway"
})


def run(command: list[str], *, check=True, timeout=180):
    return subprocess.run(
        command, check=check, timeout=timeout, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    )


def validate_synthetic_backup(archive: Path, identity: Path) -> dict:
    if os.environ.get("HOMEAWAY_CLEAN_RESTORE_CI") != "1":
        raise RuntimeError("Clean-host recovery is disabled outside isolated CI.")
    if not os.environ.get("GITHUB_ACTIONS") == "true":
        raise RuntimeError("Clean-host recovery requires a disposable GitHub Actions runner.")
    if not archive.is_file() or archive.is_symlink() or not identity.is_file() or identity.is_symlink():
        raise ValueError("Missing or untrusted backup archive/CI identity.")
    manifest_file = archive.with_suffix(archive.suffix + ".json")
    if not manifest_file.is_file() or manifest_file.is_symlink():
        raise ValueError("Missing backup checksum manifest.")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if (manifest.get("schema_version") != 1 or
        manifest.get("project") != "homeaway-cooling" or
        manifest.get("archive_name") != archive.name or
        manifest.get("encryption") != "age" or
        manifest.get("quiesced") is not True or
        manifest.get("volumes") != list(PRESERVED_VOLUMES) or
        manifest.get("selection") != ["cooling-monitoring"]):
        raise ValueError("Backup metadata does not match the synthetic cooling-only fixture.")
    if not isinstance(manifest.get("created_at"), int) or not 0 <= time.time() - manifest["created_at"] <= 3600:
        raise ValueError("Backup is not from the current CI run.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("config_sha256", ""))):
        raise ValueError("Invalid config fingerprint.")
    if backup_service.digest_file(archive) != manifest.get("archive_sha256"):
        raise ValueError("Encrypted backup checksum mismatch.")
    with archive.open("rb") as source:
        if not source.read(22).startswith(b"age-encryption.org/v1"):
            raise ValueError("Not an age-encrypted archive.")
    return manifest


def copy_secret_state(mapping: dict[str, str], destination: Path) -> Path:
    """Move only recovered config/secrets from scratch volumes to a new temp root."""
    if not destination.is_dir() or destination.is_symlink():
        raise RuntimeError("Recovered state destination must be an isolated temporary directory.")
    destination.chmod(0o700)
    (destination / "state").mkdir(mode=0o700)
    # Docker is the only bridge between recovered volumes and the fresh host.
    # cp -a preserves the source UIDs required by Grafana and telemetry ingest.
    script = (
        "set -eu;"
        "cp -a /source/config/cooling.env /destination/source.env;"
        "cp -a /source/state/secrets /destination/state/secrets;"
    )
    run([
        "docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop=ALL", "--cap-add=DAC_OVERRIDE", "--cap-add=CHOWN",
        "--security-opt=no-new-privileges",
        "--mount", f"type=volume,source={mapping['config']},target=/source/config,readonly",
        "--mount", f"type=volume,source={mapping['state']},target=/source/state,readonly",
        "--mount", f"type=bind,source={destination},target=/destination",
        IMAGE, "sh", "-ec", script,
    ])
    if not (destination / "state/secrets/telemetry/token").is_file():
        raise RuntimeError("Restored telemetry credentials absent.")
    return destination / "source.env"


def make_recovered_config(source: Path, destination: Path, state: Path) -> None:
    options = dict(
        line.split("=", 1) for line in source.read_text(encoding="ascii").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    if (options.get("COOLING_COMPONENTS") != "cooling-monitoring" or
        options.get("COOLING_BIND_IP") != "127.0.0.1" or
        options.get("COOLING_TLS_NAME") != "127.0.0.1"):
        raise RuntimeError("This CI test accepts only its own localhost cooling fixture.")
    options["COOLING_STATE_DIR"] = str(state)
    options["COOLING_HTTPS_PORT"] = str(PORT)
    destination.write_text(
        "".join(f"{key}={val}\n" for key, val in sorted(options.items())),
        encoding="ascii",
    )
    destination.chmod(0o600)


def make_recovered_compose(
    root: Path, config: Path, state: Path,
    project: str, mapping: dict[str, str],
) -> tuple[Path, dict]:
    catalog = load_catalog(root / "deploy/components/catalog.json")
    plan = resolve(catalog, parse_selection("cooling-monitoring"))
    generated = compose_generator.render(plan, config, state, root)
    compose_generator.write(generated, state)
    definition = json.loads(generated["compose.generated.json"])
    if set(definition["services"]) != EXPECTED_SERVICES:
        raise RuntimeError("Unexpected services in recovered Compose graph.")
    definition["name"] = project
    for name in definition["volumes"]:
        location = "volumes/" + name
        if location not in mapping:
            raise RuntimeError("Unknown recovered persistent volume.")
        definition["volumes"][name] = {
            "external": True,
            "name": mapping[location],
        }
    path = state / "generated/compose.generated.json"
    path.write_text(json.dumps(definition, sort_keys=True, indent=2) + "\n")
    path.chmod(0o600)
    return path, plan


def compose(root: Path, config: Path, definition: Path, *args: str) -> list[str]:
    return [
        "docker", "compose", "--project-directory", str(root),
        "--env-file", str(config), "-f", str(definition), *args,
    ]


def verify_application(
    root: Path, config: Path, state: Path,
    definition: Path, recovered_ca_hash: str,
) -> dict:
    with tempfile.TemporaryDirectory(prefix="homeaway-restored-tls-") as tmp:
        from_file = Path(tmp) / "root.crt"
        gateway_root(root, config, from_file)
        if hashlib.sha256(from_file.read_bytes()).hexdigest() != recovered_ca_hash:
            raise RuntimeError("Recovered gateway rotated rather than preserved the Caddy CA.")
        url = f"https://127.0.0.1:{PORT}"
        password = (state / "secrets/grafana_admin_password").read_text().strip()
        token = (state / "secrets/telemetry/token").read_text().strip()
        wait_for_service(url, from_file, password)
        status, _ = request(url + "/api/org", from_file)
        if status not in (401, 403):
            raise RuntimeError("Recovered Grafana does not require authentication.")
        anonymous, _ = request(url + "/ingest", from_file, method="POST", body=b"invalid\n")
        malformed, _ = request(url + "/ingest", from_file, method="POST",
                               body=b"invalid\n", token=token)
        if anonymous != 401 or malformed != 422:
            raise RuntimeError("Restored ingress authentication/schema rules failed.")
        original = query_metrics(root, config)["data"]["result"]
        if not original:
            raise RuntimeError("Original telemetry was not recovered.")
        grafana_query = quote(METRIC, safe="")
        query_url = url + "/api/datasources/proxy/uid/homeaway-vm/api/v1/query?query=" + grafana_query
        status, payload = request(query_url, from_file, basic=password)
        if status != 200 or not json.loads(payload)["data"]["result"]:
            raise RuntimeError("Recovered Grafana datasource cannot access historical data.")
        # Exercise *new* telemetry to prove restored receiver identity and
        # backend writes, not merely an old snapshot read.
        stamp = int(time.time() * 1000)
        data = (
            "sensordash_reading,host=ci-after-clean-restore,"
            "sensor=/amdcpu/0/temperature/0,kind=temperature "
            f"value=47.75 {stamp}\n"
        ).encode()
        status, _ = request(url + "/ingest", from_file, method="POST", body=data, token=token)
        if status != 204:
            raise RuntimeError(f"Restored ingestion rejected new synthetic telemetry: HTTP {status}.")
        metric = 'sensordash_reading_value{host="ci-after-clean-restore"}'
        probe = (
            "import json,urllib.request,urllib.parse,time;"
            "name=" + repr(metric) + ";"
            "url='http://victoria-metrics:8428/api/v1/query?query='+urllib.parse.quote(name,safe='');"
            "result=json.load(urllib.request.urlopen(url,timeout=7));"
            "assert result.get('status')=='success' and result['data']['result'];"
            "print('RESTORED_RECEIVER_WRITES')"
        )
        deadline = time.monotonic() + 45
        while True:
            response = run(compose(root, config, definition, "exec", "-T",
                                   "telemetry-ingest", "python", "-c", probe), check=False)
            if response.returncode == 0 and "RESTORED_RECEIVER_WRITES" in response.stdout:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Restored telemetry receiver is not writing fresh readings.")
            time.sleep(2)
        status, payload = request(url + "/api/search?type=dash-db", from_file, basic=password)
        titles = [item.get("title", "") for item in json.loads(payload)] if status == 200 else []
        if not any("SensorDash" in title for title in titles):
            raise RuntimeError("Recovered Grafana dashboard provisioning failed.")
        return {
            "trusted_original_ca": True,
            "grafana_login_required": True,
            "historical_metric_queryable": True,
            "fresh_telemetry_ingested": True,
            "grafana_dashboards_available": True,
        }


def recover(archive: Path, identity: Path, root: Path) -> dict:
    manifest = validate_synthetic_backup(archive, identity)
    if os.geteuid() != 0 or not root.is_dir():
        raise RuntimeError("Clean runner must run as root with an installed test package.")
    if (Path("/etc/homeaway-cooling").exists() or
        Path("/var/lib/homeaway-cooling").exists() or
        run(["docker", "volume", "inspect", "homeaway-cooling_cooling-metrics"],
            check=False).returncode == 0):
        raise RuntimeError("Recovery runner is not a clean installation.")
    preflight = decrypt_to(
        archive, identity, [sys.executable, "-B", str(ARCHIVE_CHECKER)],
    )
    if preflight["volumes"] != sorted(PRESERVED_VOLUMES):
        raise RuntimeError("Backup is missing required Docker volumes.")
    token = secrets.token_hex(6)
    prefix = REHEARSAL_PREFIX + token
    project = prefix + "-app"
    mapping = volumes(prefix)
    # Scratch names are unique; no installed production volume is writable.
    assert_new(list(mapping.values()), prefix + "-net", prefix + "-app-gateway-1")
    allocated = []
    definition = config = state = None
    started = False
    with tempfile.TemporaryDirectory(prefix=prefix + "-") as folder:
        target = Path(folder)
        try:
            for name in mapping.values():
                run(["docker", "volume", "create", "--label",
                     "homeaway.clean-restore-ci=true", name])
                allocated.append(name)
            extractor = [
                "docker", "run", "--rm", "-i", "--network", "none",
                "--read-only", "--pids-limit", "128", "--cap-drop=ALL",
                "--cap-add=CHOWN", "--cap-add=DAC_OVERRIDE", "--cap-add=FOWNER",
                "--security-opt=no-new-privileges",
                *mounts(mapping),
                "--mount",
                f"type=bind,source={ARCHIVE_CHECKER},target=/app/rehearsal_archive.py,readonly",
                IMAGE, "python", "-B", "/app/rehearsal_archive.py",
                "--extract-to", "/rehearsal",
            ]
            extracted = decrypt_to(archive, identity, extractor)
            if extracted["files"] != preflight["files"]:
                raise RuntimeError("Backup changed between validation and extraction.")
            inspect_restored(mapping, manifest["config_sha256"])
            source = copy_secret_state(mapping, target)
            state = target / "state"
            config = target / "recovery.env"
            make_recovered_config(source, config, state)
            definition, plan = make_recovered_compose(root, config, state, project, mapping)
            check = run(compose(root, config, definition, "config", "--services"))
            if set(check.stdout.splitlines()) != EXPECTED_SERVICES:
                raise RuntimeError("Recovered Compose selection is not exactly cooling-only.")
            ca_check = (
                "import hashlib;"
                "from pathlib import Path;"
                "p=Path('/rehearsal/volumes/cooling-caddy-data/caddy/pki/authorities/local/root.crt');"
                "print(hashlib.sha256(p.read_bytes()).hexdigest())"
            )
            fingerprint = run([
                "docker", "run", "--rm", "--network", "none", "--read-only",
                *mounts(mapping, readonly=True),
                IMAGE, "python", "-B", "-c", ca_check,
            ]).stdout.strip()
            if not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
                raise RuntimeError("Recovered CA identity is not valid.")
            run(compose(root, config, definition, "up", "-d", "--build"), timeout=420)
            started = True
            results = verify_application(root, config, state, definition, fingerprint)
            active = run([
                "docker", "ps", "--filter", f"label=com.docker.compose.project={project}",
                "--format", "{{.Names}}",
            ])
            if len(active.stdout.splitlines()) != 4:
                raise RuntimeError("Recovered Compose project did not start exactly four containers.")
            return {
                "clean_host_recovery_passed": True,
                "restored_application_services": sorted(plan["services"]),
                "scratch_volumes": len(allocated),
                "archive_validated": True,
                **results,
            }
        finally:
            if definition is not None and config is not None:
                run(compose(root, config, definition, "down", "--remove-orphans"), check=False)
            # All volumes are "external" to Compose; clean only the randomly
            # allocated scratch names after the recovered containers are down.
            for name in reversed(allocated):
                run(["docker", "volume", "rm", name], check=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/opt/homeaway-cooling"))
    args = parser.parse_args(argv)
    try:
        output = recover(args.archive, args.identity, args.root)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"Clean-host recovery failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
