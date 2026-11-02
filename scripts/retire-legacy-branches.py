#!/usr/bin/env python3
"""Archive pinned historical branch SHAs as remote Git tags before deletion.

Read-only by default. No GitHub REST API needed. Apply creates lightweight
archive tags, verifies them on GitHub, and deletes only unchanged exact refs
using per-ref --force-with-lease. The open SensorDash mobile PR is excluded.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

OWNER = "CodyMathis123"
MANIFEST = Path(__file__).with_name("retire-legacy-branches-20261102.json")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
TAG_PREFIX = "archive/pre-prod-only-20261102/"


def git(*args: str, cwd: str | None = None) -> str:
    command = ["git", *(["-C", cwd] if cwd else []), *args]
    proc = subprocess.run(command, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=150)
    return proc.stdout


def remote_for(repo: str) -> str:
    return f"git@github.com:{OWNER}/{repo}.git"


def remote_refs(remote: str, refs: str) -> dict[str, str]:
    output = git("ls-remote", remote, refs)
    prefix = refs.rstrip("*")
    rows = {}
    for line in output.splitlines():
        sha, path = line.split("\t", 1)
        if path.startswith(prefix):
            rows[path[len(prefix):]] = sha
    return rows


def load_manifest() -> dict:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if (data.get("schema_version") != 1 or data.get("owner") != OWNER or
            data.get("tag_prefix") != TAG_PREFIX):
        raise ValueError("Unsupported cleanup manifest")
    for repo, rows in data["repositories"].items():
        if repo not in ("HomeAwayFromHome", "SensorDash") or not isinstance(rows, list):
            raise ValueError("Unsupported repository")
        names = set()
        for row in rows:
            name, sha = row["branch"], row["sha"]
            if (not isinstance(name, str) or not SAFE_REF.fullmatch(name) or
                    name == "Production" or name.startswith("/") or
                    name.endswith("/") or ".." in name or "@{" in name or
                    name.endswith(".lock") or not isinstance(sha, str) or
                    not HEX40.fullmatch(sha) or name in names):
                raise ValueError("Unsafe or duplicate cleanup ref")
            names.add(name)
    return data


def plan(repo: str, manifest: dict, remote: str
         ) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    live = remote_refs(remote, "refs/heads/*")
    if "Production" not in live:
        raise RuntimeError("Missing remote Production; refusing cleanup")
    tags = remote_refs(remote, "refs/tags/*")
    eligible, excluded = [], []
    expected = {row["branch"]: row["sha"] for row in manifest["repositories"][repo]}
    for branch, sha in sorted(expected.items()):
        actual = live.get(branch)
        tag = TAG_PREFIX + branch
        if actual != sha:
            excluded.append((branch, "missing or moved since audit"))
        elif tags.get(tag) not in (None, sha):
            excluded.append((branch, "archive tag already points elsewhere"))
        elif tag + "^{}" in tags:
            excluded.append((branch, "annotated archive tag collision"))
        else:
            eligible.append((branch, sha, tag))
    for branch in sorted(live):
        if branch != "Production" and branch not in expected:
            excluded.append((branch, "not in reviewed manifest: PRESERVE"))
    return eligible, excluded


def archive_and_delete(remote: str, checkout: str,
                       branch: str, sha: str, tag: str) -> None:
    if remote_refs(remote, "refs/heads/*").get(branch) != sha:
        raise RuntimeError(f"{branch}: branch changed; preserved")
    tags = remote_refs(remote, "refs/tags/*")
    if tags.get(tag) not in (None, sha) or tag + "^{}" in tags:
        raise RuntimeError(f"{branch}: archive tag collision")
    if tags.get(tag) is None:
        if git("cat-file", "-t", sha, cwd=checkout).strip() != "commit":
            raise RuntimeError(f"{branch}: missing commit")
        git("tag", tag, sha, cwd=checkout)
        git("push", "--porcelain", "origin",
            f"refs/tags/{tag}:refs/tags/{tag}", cwd=checkout)
    if remote_refs(remote, "refs/tags/*").get(tag) != sha:
        raise RuntimeError(f"{branch}: archive not verified")
    if remote_refs(remote, "refs/heads/*").get(branch) != sha:
        raise RuntimeError(f"{branch}: branch moved; preserved")
    git("push", "--porcelain", f"--force-with-lease=refs/heads/{branch}:{sha}",
        "origin", f":refs/heads/{branch}", cwd=checkout)
    if branch in remote_refs(remote, "refs/heads/*"):
        raise RuntimeError(f"{branch}: branch remains after deletion attempt")
    print(f"ARCHIVED + DELETED: {branch} -> {tag} ({sha[:12]})", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=["HomeAwayFromHome", "SensorDash"],
                        required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Create archive tags then delete exact matching branch refs")
    args = parser.parse_args()
    try:
        data = load_manifest()
        remote = remote_for(args.repo)
        candidates, excluded = plan(args.repo, data, remote)
        print(f"{args.repo}: {len(candidates)} archive/delete candidates; "
              f"{len(excluded)} excluded", flush=True)
        for branch, sha, tag in candidates:
            print(f"  ARCHIVE/DELETE {branch} ({sha[:12]}) -> {tag}")
        for branch, reason in excluded:
            print(f"  KEEP           {branch}: {reason}")
        if not args.apply:
            print("PLAN ONLY: no tags or branches changed")
            return 0
        if not candidates:
            print("Nothing eligible for deletion")
            return 0
        if not sys.stdin.isatty():
            raise RuntimeError("Apply requires interactive confirmation")
        confirmation = f"ARCHIVE DELETE {len(candidates)} {args.repo}"
        print(f"To archive and delete listed branches, type: {confirmation}")
        if input("> ").strip() != confirmation:
            print("Cancelled; no changes")
            return 1
        with tempfile.TemporaryDirectory(prefix="homeaway-branch-archive-") as folder:
            checkout = str(Path(folder) / "repo.git")
            git("clone", "--bare", "--no-tags", remote, checkout)
            if plan(args.repo, data, remote)[0] != candidates:
                raise RuntimeError("Eligibility changed; no deletion")
            for branch, sha, tag in candidates:
                archive_and_delete(remote, checkout, branch, sha, tag)
        print(f"Completed {len(candidates)} archival deletions")
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        # Exception details can contain local paths; do not echo credentials.
        print(f"ERROR: {type(exc).__name__}; stopped. "
              "Previously successful archives and deletions remain complete.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
