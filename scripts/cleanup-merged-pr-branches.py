#!/usr/bin/env python3
"""Preview or explicitly delete stale GitHub branches from merged pull requests.

Uses a reviewed GitHub PR/branch snapshot to avoid unauthenticated REST API
rate limits on the Pi. Only exact SHA-matching previously merged PR branches
are eligible. Git SSH rechecks live tips before every deletion. Never touches
default, protected, open-PR, changed-after-merge, or unreviewed branches.

Usage:
  python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome
  python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome --apply

The apply action requires interactive confirmation. No background scheduling.
Requires Git SSH permissions for git@github.com:CodyMathis123/<repo>.git.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

OWNER = "CodyMathis123"
REPOS = ("HomeAwayFromHome", "SensorDash", "walnut-loop-lab")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/]*$")
API = "https://api.github.com"


def read_api(repo: str, endpoint: str) -> list[dict] | dict:
    """Request GitHub metadata. Never prints authorization headers."""
    url = API + f"/repos/{OWNER}/{repo}/" + endpoint
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "HomeAway-Branch-Pruner"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    results: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
            if isinstance(payload, list):
                results.extend(payload)
            else:
                return payload
            link = response.headers.get("Link", "")
        more = None
        for item in link.split(","):
            match = re.search(r'<(https://api\.github\.com/[^>]+)>;\s*rel="next"', item)
            if match:
                more = match.group(1)
                break
        url = more
    return results


def candidates(repo: str, branches: list[dict], prs: list[dict],
               default_branch: str) -> tuple[list[tuple[str, str, int]], dict[str, str]]:
    """Return (branch, exact SHA, merged PR number) plus excluded reasons."""
    live = {row["name"]: row for row in branches}
    open_branches = {
        p["head"]["ref"] for p in prs
        if p.get("state") == "open"
        and p.get("head", {}).get("repo")
        and p["head"]["repo"].get("full_name") == f"{OWNER}/{repo}"
    }
    eligible: dict[str, tuple[str, int]] = {}
    excluded: dict[str, str] = {}
    for p in prs:
        head = p.get("head") or {}
        parent_repo = head.get("repo") or {}
        name, sha = head.get("ref", ""), head.get("sha", "")
        if not p.get("merged_at") or parent_repo.get("full_name") != f"{OWNER}/{repo}":
            continue
        if name not in live or name in open_branches or name == default_branch:
            continue
        if not REF_RE.fullmatch(name) or name.startswith("/") or ".." in name:
            excluded[name] = "invalid ref syntax"
            continue
        entry = live[name]
        if entry.get("protected", False):
            excluded[name] = "protected branch"
            continue
        if entry.get("commit", {}).get("sha") != sha:
            excluded[name] = "branch tip differs from merged PR"
            continue
        eligible[name] = sha, p["number"]
    for name in live:
        if name == default_branch:
            continue
        if name in open_branches:
            excluded[name] = "open PR"
        elif name not in eligible:
            excluded.setdefault(name, "not a matching merged PR branch")
    ordered = sorted((name, sha, number) for name, (sha, number) in eligible.items())
    return ordered, excluded


def current_remote_refs(repo: str) -> dict[str, str]:
    url = f"git@github.com:{OWNER}/{repo}.git"
    result = subprocess.run(
        ["git", "ls-remote", "--heads", url],
        capture_output=True, text=True, check=True, timeout=60,
    )
    refs = {}
    for line in result.stdout.splitlines():
        sha, ref = line.split("\t", 1)
        if ref.startswith("refs/heads/"):
            refs[ref.removeprefix("refs/heads/")] = sha
    return refs


SNAPSHOT = Path(__file__).with_name("branch-cleanup-merged-pr-snapshot-20261102.json")
HEX40 = re.compile(r"^[0-9a-f]{40}$")


def snapshot_candidates(repo: str, snapshot: dict, remote: dict[str, str]
                        ) -> tuple[list[tuple[str, str, int]], dict[str, str]]:
    """Use pinned merged-PR evidence plus real-time Git SSH branch tips.

    Snapshot metadata is static. Any deleted, moved, new or unexpected branch
    is not selected. The manifest explicitly excludes the open SensorDash PR.
    """
    if (snapshot.get("schema_version") != 1 or
            snapshot.get("repository_owner") != OWNER or
            snapshot.get("selection") != "exact_branch_sha_of_merged_same_repository_pr"):
        raise RuntimeError("Invalid/unknown snapshot metadata")
    record = snapshot.get("repositories", {}).get(repo)
    if not isinstance(record, dict) or record.get("default_branch") != "Production":
        raise RuntimeError("Missing expected repository snapshot")
    default = record["default_branch"]
    open_branches = set(record.get("observed_open_pr_branches", []))
    entries = record.get("candidates")
    if not isinstance(entries, list):
        raise RuntimeError("Missing snapshot candidates")
    ready = []
    excluded = {}
    seen = set()
    for item in entries:
        name, sha, number = item["name"], item["sha"], item["merged_pr"]
        if (not isinstance(name, str) or not REF_RE.fullmatch(name) or
                ".." in name or name.startswith("/") or name.endswith("/") or
                name.endswith(".lock") or not isinstance(sha, str) or
                not HEX40.fullmatch(sha) or type(number) is not int or number <= 0 or
                name in seen or name == default or name in open_branches or
                not item.get("merged_at")):
            raise RuntimeError("Unsafe or inconsistent snapshot candidate")
        seen.add(name)
        if remote.get(name) == sha:
            ready.append((name, sha, number))
        else:
            excluded[name] = "missing or changed since reviewed snapshot"
    # New branches are deliberately excluded, even if a later PR was merged.
    for name in remote:
        if name != default and name not in seen:
            excluded[name] = "not in reviewed snapshot"
    if default not in remote:
        raise RuntimeError("Remote default branch cannot be verified")
    return sorted(ready), excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", choices=REPOS, required=True)
    parser.add_argument("--apply", action="store_true",
                        help="Explicit destructive action, with typed confirmation")
    parser.add_argument("--live-api", action="store_true",
                        help="Optional fresh GitHub REST check; may require GITHUB_TOKEN")
    args = parser.parse_args()
    repo = args.repo
    try:
        remote = current_remote_refs(repo)
        if args.live_api:
            info = read_api(repo, "")
            branches = read_api(repo, "branches?per_page=100")
            prs = read_api(repo, "pulls?state=all&per_page=100")
            if not isinstance(info, dict) or not isinstance(branches, list) or not isinstance(prs, list):
                raise RuntimeError("Unexpected GitHub response; no deletions")
            wanted, _ = candidates(repo, branches, prs, info["default_branch"])
            ready = [x for x in wanted if remote.get(x[0]) == x[1]]
            excluded = {
                name: "not eligible on live PR audit or tip changed"
                for name in remote
                if name != info["default_branch"] and name not in {x[0] for x in ready}
            }
            source = "live GitHub REST metadata"
        else:
            with SNAPSHOT.open(encoding="utf-8") as file:
                snapshot = json.load(file)
            ready, excluded = snapshot_candidates(repo, snapshot, remote)
            source = "reviewed 2026-11-02 GitHub snapshot (live Git SSH SHA checks)"
        print(f"{repo}: {len(ready)} merged-PR branches eligible; "
              f"{len(excluded)} excluded; source={source}", flush=True)
        for name, sha, number in ready:
            print(f"  candidate  {name:<60} PR #{number:<4} {sha[:12]}")
        for name, why in sorted(excluded.items()):
            print(f"  keep       {name:<60} {why}")
        print("\nAll other branches are PRESERVED, including open PRs, "
              "unmerged work and divergent historical branches.")
        if not args.apply:
            print("\nPLAN ONLY: nothing deleted. Re-run with --apply to review "
                  "and confirm the same tip checks.")
            return 0
        if not ready:
            print("Nothing to delete.")
            return 0
        if not sys.stdin.isatty():
            raise RuntimeError("Apply requires an interactive terminal")
        expected = f"DELETE {len(ready)} {repo}"
        print(f"\nTo delete ONLY these candidates, type exactly: {expected}")
        if input("> ").strip() != expected:
            print("Cancelled; nothing deleted.")
            return 1
        # Recheck all selected heads immediately before acting. The per-ref
        # --force-with-lease also guards against a concurrent branch update.
        remote_now = current_remote_refs(repo)
        if any(remote_now.get(name) != sha for name, sha, _ in ready):
            raise RuntimeError("Remote branch tips changed since preview; aborting")
        url = f"git@github.com:{OWNER}/{repo}.git"
        failed = []
        for name, sha, _ in ready:
            command = [
                "git", "push", "--porcelain",
                f"--force-with-lease=refs/heads/{name}:{sha}",
                url, f":refs/heads/{name}",
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=90)
            if result.returncode:
                print(f"FAILED (kept): {name}; inspect Git/SSH permissions", file=sys.stderr)
                failed.append(name)
            else:
                print(f"DELETED: {name}")
        print(f"Completed: {len(ready)-len(failed)} deleted, {len(failed)} failed.")
        return 1 if failed else 0
    except (OSError, urllib.error.URLError, ValueError, KeyError, RuntimeError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Do not echo tokens, credentials, exception response bodies or secrets.
        print(f"ERROR: {type(exc).__name__}; no further branches changed. "
              "Check Git SSH and the reviewed snapshot file.", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
