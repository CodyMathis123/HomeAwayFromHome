#!/usr/bin/env python3
"""Build the SAME reviewed cooling backend payload into .tar.gz and .deb.

No downloaded build dependencies, no Docker daemon access, no host state
initialization, no lifecycle scripts, and no service startup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from components import load_catalog, resolve

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "homeaway-cooling"
FILES = (
    "compose.cooling-only.yaml",
    "deploy/cooling/Caddyfile",
    "deploy/components/catalog.json",
    "deploy/cooling/scrape.yml",
    "scripts/init-cooling-standalone.py",
    "scripts/cooling-stack.py",
    "scripts/components.py",
    "ingest/Dockerfile",
    "ingest/app.py",
    "grafana/provisioning/dashboards/dashboards.yaml",
    "grafana/provisioning/datasources/victoriametrics.yaml",
    "grafana/dashboards/sensordash.json",
    "grafana/dashboards/cooling-systems.json",
    "grafana/dashboards/reporting-summary.json",
    "grafana/dashboards/electrical.json",
    "grafana/dashboards/events.json",
    "docs/COOLING-STANDALONE.md",
    "docs/MODULAR-ARCHITECTURE.md",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_payload(destination: Path, version: str) -> dict[str, str]:
    hashes = {}
    for rel in FILES:
        src = ROOT / rel
        if not src.is_file() or src.is_symlink():
            raise ValueError(f"Missing or symlinked release asset: {rel}")
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        target.chmod(0o644)
        hashes[rel] = digest(target)
    (destination / "VERSION").write_text(version + "\n", encoding="ascii")
    hashes["VERSION"] = digest(destination / "VERSION")
    resolved = resolve(
        load_catalog(ROOT / "deploy/components/catalog.json"), ("cooling-monitoring",)
    )
    manifest = {
        "name": PACKAGE,
        "version": version,
        "selected_features": resolved["selected_features"],
        "resolved_components": resolved["component_order"],
        "services": resolved["services"],
        "files_sha256": hashes,
        "config_policy": "runtime config and credentials never shipped",
        "runtime": "Docker Compose v2",
    }
    (destination / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return hashes


def portable_wrapper() -> str:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\n'
        'exec python3 -B "$HERE/scripts/cooling-stack.py" "$@"\n'
    )


def deb_wrapper() -> str:
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        'exec /usr/bin/python3 -B /opt/homeaway-cooling/scripts/cooling-stack.py "$@"\n'
    )


def build(version: str, out: Path) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:~[a-z0-9.]+)?", version):
        raise ValueError("Expected Debian-compatible release version, e.g. 0.1.0.")
    if shutil.which("dpkg-deb") is None:
        raise ValueError("dpkg-deb required to build Linux .deb releases.")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="homeaway-cooling-build-") as tmp:
        base = Path(tmp)
        portable = base / f"{PACKAGE}-{version}"
        portable.mkdir()
        hashes = copy_payload(portable, version)
        (portable / PACKAGE).write_text(portable_wrapper(), encoding="utf-8")
        (portable / PACKAGE).chmod(0o755)
        archive = out / f"{PACKAGE}_{version}_linux-portable.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(portable, arcname=portable.name, recursive=True)
        debroot = base / "debroot"
        appdir = debroot / "opt/homeaway-cooling"
        shutil.copytree(portable, appdir)
        # A portable launcher is useful only at the root of an unpacked tarball.
        (appdir / PACKAGE).unlink()
        executable = debroot / "usr/bin/homeaway-cooling"
        executable.parent.mkdir(parents=True)
        executable.write_text(deb_wrapper(), encoding="utf-8")
        executable.chmod(0o755)
        control = debroot / "DEBIAN/control"
        control.parent.mkdir()
        control.write_text(
            f"Package: {PACKAGE}\n"
            f"Version: {version}\n"
            "Section: admin\n"
            "Priority: optional\n"
            "Architecture: all\n"
            "Maintainer: HomeAwayFromHome maintainers\n"
            "Depends: python3\n"
            "Description: Modular SensorDash cooling history backend\n"
            " Docker Compose definitions, telemetry receiver, Grafana assets\n"
            " and operator CLI. Requires Docker Engine and Compose v2 to run.\n"
            " Installing or upgrading never starts any services.\n",
            encoding="utf-8",
        )
        debfile = out / f"{PACKAGE}_{version}_all.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(debroot), str(debfile)],
            check=True, capture_output=True, text=True,
        )
        checksums = out / "SHA256SUMS"
        checksums.write_text(
            "".join(f"{digest(path)}  {path.name}\n" for path in (archive, debfile)),
            encoding="ascii",
        )
        print("Built portable and Debian release archives using identical application payload.")
        print(f"Source files: {len(hashes)}; version: {version}")
        print(f"Release outputs: {archive.name}, {debfile.name}, SHA256SUMS")
        print("No credentials, persistent data, systemd agents or container images included.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", required=True)
    p.add_argument("--output", type=Path, default=Path("dist"))
    args = p.parse_args()
    build(args.version, args.output.resolve())


if __name__ == "__main__":
    main()
