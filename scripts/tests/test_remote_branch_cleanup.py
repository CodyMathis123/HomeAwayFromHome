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
        self.assertIn("remote.get(name) != sha", code)
        self.assertIn('parser.add_argument("--apply"',code)
        self.assertNotIn("force=True", code)

if __name__ == "__main__":
    unittest.main()
