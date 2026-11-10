#!/usr/bin/env python3
"""Verify that the portable tar and .deb install the same trusted payload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(version: str, dist: Path) -> None:
    deb = dist / f"homeaway-cooling_{version}_all.deb"
    archive = dist / f"homeaway-cooling_{version}_linux-portable.tar.gz"
    assert deb.is_file() and archive.is_file()
    manifest_sha = {}
    for line in (dist / "SHA256SUMS").read_text().splitlines():
        digest, filename = line.split(maxsplit=1)
        manifest_sha[filename] = digest
    assert manifest_sha == {p.name: sha(p) for p in (deb, archive)}
    with tempfile.TemporaryDirectory(prefix="cooling-package-verify-") as folder:
        root = Path(folder)
        tar_dest = root / "portable"
        tar_dest.mkdir()
        with tarfile.open(archive, "r:gz") as tar:
            # Reject traversal/symlink entries in release archives before extraction.
            for entry in tar.getmembers():
                assert not entry.issym() and not entry.islnk(), entry.name
                assert ".." not in Path(entry.name).parts and not entry.name.startswith("/"), entry.name
            tar.extractall(tar_dest, filter="data")
        portable = tar_dest / f"homeaway-cooling-{version}"
        deb_dest = root / "deb"
        deb_dest.mkdir()
        subprocess.run(["dpkg-deb", "-x", str(deb), str(deb_dest)], check=True)
        installed = deb_dest / "opt/homeaway-cooling"
        assert (deb_dest / "usr/bin/homeaway-cooling").is_file()
        assert (portable / "homeaway-cooling").is_file()
        assert (installed / "VERSION").read_text().strip() == version
        manifest = json.loads((portable / "release-manifest.json").read_text())
        assert manifest == json.loads((installed / "release-manifest.json").read_text())
        for rel, digest in manifest["files_sha256"].items():
            assert sha(portable / rel) == digest, rel
            assert sha(installed / rel) == digest, rel
        for tree in (portable, installed):
            paths = {str(p.relative_to(tree)) for p in tree.rglob("*") if p.is_file()}
            assert all(not p.startswith(("data/", "secrets/", ".env")) for p in paths)
            assert not any("password" in p.lower() or p.endswith(".key") for p in paths)
        print(f"Verified same {len(manifest['files_sha256'])} source payloads in .deb and tar.gz")
        print("No runtime state, tokens, passwords or private keys are shipped.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--version", required=True)
    p.add_argument("--dist", type=Path, default=Path("dist"))
    args = p.parse_args()
    verify(args.version, args.dist.resolve())
