"""Archive-first legacy branch cleanup safety and local bare-Git integration tests."""
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "retire_branches", ROOT/"scripts"/"retire-legacy-branches.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def run(*args):
    return subprocess.check_output(["git", *(str(a) for a in args)],
                                   stderr=subprocess.STDOUT, text=True).strip()


class ArchiveRetirement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.work = base/"work"
        self.remote = base/"remote.git"
        self.clone = base/"clone.git"
        run("init","--bare","--initial-branch=Production",self.remote)
        run("init","--initial-branch=Production",self.work)
        run("-C",self.work,"config","user.name","Test")
        run("-C",self.work,"config","user.email","test@example.invalid")
        (self.work/"example").write_text("production\n")
        run("-C",self.work,"add",".")
        run("-C",self.work,"commit","-m","production")
        run("-C",self.work,"remote","add","origin",self.remote)
        run("-C",self.work,"push","origin","Production")
        for branch,body in (("validation/legacy","historical"),("feature/open-pr","unfinished")):
            run("-C",self.work,"switch","-c",branch)
            (self.work/"example").write_text(body)
            run("-C",self.work,"commit","-am",branch)
            run("-C",self.work,"push","origin",branch)
            run("-C",self.work,"switch","Production")
        self.sha = mod.remote_refs(str(self.remote),"refs/heads/*")["validation/legacy"]
        self.prod = mod.remote_refs(str(self.remote),"refs/heads/*")["Production"]
        self.manifest = {
            "repositories":{"SensorDash":[{"branch":"validation/legacy","sha":self.sha}]}}
        self.url = str(self.remote)

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_sha_only_and_open_pr_preserved(self):
        ready,excluded=mod.plan("SensorDash",self.manifest,self.url)
        self.assertEqual(ready,[("validation/legacy",self.sha,
                                 mod.TAG_PREFIX+"validation/legacy")])
        self.assertIn(("feature/open-pr","not in reviewed manifest: PRESERVE"),excluded)

    def test_archive_remote_tag_verified_before_delete(self):
        run("clone","--bare","--no-tags",self.remote,self.clone)
        tag=mod.TAG_PREFIX+"validation/legacy"
        mod.archive_and_delete(self.url,str(self.clone),"validation/legacy",self.sha,tag)
        live=mod.remote_refs(self.url,"refs/heads/*")
        tags=mod.remote_refs(self.url,"refs/tags/*")
        self.assertNotIn("validation/legacy",live)
        self.assertIn("feature/open-pr",live)
        self.assertEqual(live["Production"],self.prod)
        self.assertEqual(tags[tag],self.sha)
        self.assertEqual(mod.plan("SensorDash",self.manifest,self.url)[0],[])

    def test_moved_branch_refused(self):
        run("-C",self.work,"switch","validation/legacy")
        (self.work/"example").write_text("changed later")
        run("-C",self.work,"commit","-am","new")
        run("-C",self.work,"push","origin","validation/legacy")
        ready,excluded=mod.plan("SensorDash",self.manifest,self.url)
        self.assertEqual(ready,[])
        self.assertIn(("validation/legacy","missing or moved since audit"),excluded)

    def test_colliding_tag_refused(self):
        tag=mod.TAG_PREFIX+"validation/legacy"
        run("-C",self.work,"tag",tag,self.prod)
        run("-C",self.work,"push","origin","refs/tags/"+tag)
        ready,excluded=mod.plan("SensorDash",self.manifest,self.url)
        self.assertEqual(ready,[])
        self.assertIn(("validation/legacy","archive tag already points elsewhere"),excluded)

    def test_manifest_preserves_mobile_pr(self):
        data=mod.load_manifest()
        self.assertEqual(len(data["repositories"]["HomeAwayFromHome"]),5)
        self.assertEqual(len(data["repositories"]["SensorDash"]),19)
        branches=[x["branch"] for x in data["repositories"]["SensorDash"]]
        self.assertNotIn("feature/lan-mobile-dashboard",branches)
        self.assertTrue(all(len(item["sha"])==40 for items in
                            data["repositories"].values() for item in items))


if __name__ == "__main__":
    unittest.main()
