"""Cleanup script safety contracts: merged PR-only, strict tips, open PR exclusion."""
import importlib.util
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cleanup_remote", ROOT / "scripts/cleanup-merged-pr-branches.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def pr(name, sha, number, merged=True, state="closed", repo="HomeAwayFromHome"):
    return {"number": number, "state": state, "merged_at": "2026-11-02" if merged else None,
            "head": {"ref": name, "sha": sha,
                     "repo": {"full_name": f"CodyMathis123/{repo}"}}}

class BranchCleanupContracts(unittest.TestCase):
    def test_only_merged_unchanged_branch_selected(self):
        branches = [{"name": "Production", "commit": {"sha": "P"}},
                    {"name": "merged", "commit": {"sha": "abc"}},
                    {"name": "changed", "commit": {"sha": "new"}},
                    {"name": "open", "commit": {"sha": "abc"}},
                    {"name": "other", "commit": {"sha": "abc"}},
                    {"name": "unmerged", "commit": {"sha": "abc"}},
                    {"name": "protected", "commit": {"sha": "abc"}, "protected": True}]
        prs = [pr("merged","abc",1), pr("changed","old",2),
               pr("open","abc",3), pr("open","abc",4,False,"open"),
               pr("other","abc",5,repo="OtherRepo"),
               pr("unmerged","abc",6,False),
               pr("protected","abc",7)]
        ready, exclusions = mod.candidates("HomeAwayFromHome", branches, prs, "Production")
        self.assertEqual(ready, [("merged", "abc", 1)])
        for name in ("changed","open","other","unmerged","protected"):
            self.assertIn(name,exclusions)
        self.assertNotIn("Production", exclusions)

    def test_ref_syntax_is_bounded(self):
        self.assertIsNone(mod.REF_RE.fullmatch("bad name"))
        self.assertIsNone(mod.REF_RE.fullmatch("-bad"))
        self.assertIsNotNone(mod.REF_RE.fullmatch("feature/legit-branch"))

    def test_apply_requires_interactive_confirmation_and_explicit_lease(self):
        code = (ROOT / "scripts/cleanup-merged-pr-branches.py").read_text()
        self.assertIn("sys.stdin.isatty()",code)
        self.assertIn("--force-with-lease=refs/heads/",code)
        self.assertIn("remote_now.get(name) != sha", code)
        self.assertIn('parser.add_argument("--apply"',code)
        self.assertNotIn("force=True", code)

    def test_snapshot_uses_remote_sha_and_keeps_new_or_changed_branches(self):
        sha = "a" * 40
        manifest = {
            "schema_version": 1,
            "repository_owner": "CodyMathis123",
            "selection": "exact_branch_sha_of_merged_same_repository_pr",
            "repositories": {
                "SensorDash": {
                    "default_branch": "Production",
                    "observed_open_pr_branches": ["feature/lan-mobile-dashboard"],
                    "candidates": [
                        {"name": "feature/merged", "sha": sha, "merged_pr": 11,
                         "merged_at": "2026-11-02"},
                        {"name": "feature/moved", "sha": "b"*40, "merged_pr": 12,
                         "merged_at": "2026-11-02"},
                    ]
                }
            }
        }
        remote = {
            "Production": "f"*40,
            "feature/merged": sha,
            "feature/moved": "c"*40,
            "feature/lan-mobile-dashboard": "e"*40,
            "feature/unknown": "d"*40
        }
        ready, excluded = mod.snapshot_candidates("SensorDash", manifest, remote)
        self.assertEqual(ready, [("feature/merged", sha, 11)])
        self.assertIn("feature/moved", excluded)
        self.assertIn("feature/unknown", excluded)
        self.assertIn("feature/lan-mobile-dashboard", excluded)
        manifest["repositories"]["SensorDash"]["candidates"].append(
            {"name": "feature/lan-mobile-dashboard", "sha": "e"*40,
             "merged_pr": 99, "merged_at": "2026-11-02"}
        )
        with self.assertRaises(RuntimeError):
            mod.snapshot_candidates("SensorDash", manifest, remote)

    def test_snapshot_is_present_and_all_candidates_have_sha_evidence(self):
        import json
        manifest = json.loads(mod.SNAPSHOT.read_text())
        self.assertEqual(manifest["repository_owner"], "CodyMathis123")
        self.assertEqual(manifest["schema_version"], 1)
        self.assertGreaterEqual(sum(len(r["candidates"]) for r in
                                    manifest["repositories"].values()), 80)
        for repo in mod.REPOS:
            record = manifest["repositories"][repo]
            self.assertEqual(record["default_branch"], "Production")
            names = [c["name"] for c in record["candidates"]]
            self.assertEqual(len(names), len(set(names)))
            for candidate in record["candidates"]:
                self.assertRegex(candidate["sha"], r"^[0-9a-f]{40}$")
                self.assertIsInstance(candidate["merged_pr"], int)


if __name__ == "__main__":
    unittest.main()
