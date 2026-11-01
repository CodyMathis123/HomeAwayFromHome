"""The shipped pc-control image must include every referenced dashboard script.

The UI previously referenced accounts.js but the Dockerfile omitted it; browser
404 then prevented the feature-gated Users section from being displayed.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "control/api"


class AccountAssets(unittest.TestCase):
    def test_dashboard_scripts_are_packaged_in_control_image(self):
        html = (API / "index.html").read_text()
        docker = (API / "Dockerfile").read_text()
        app = (API / "app.py").read_text()
        copy_line = next(
            (line for line in docker.splitlines()
             if line.startswith("COPY ") and line.endswith(" ./")),
            None)
        self.assertIsNotNone(copy_line)
        packaged = set(copy_line.split()[1:-1])
        self.assertIn("index.html", packaged)
        for script in re.findall(r'<script[^>]+src="/control/([^"]+)"', html):
            with self.subTest(script=script):
                self.assertTrue((API / script).is_file())
                self.assertIn(script, packaged, "Script omitted from Docker image")
                self.assertIn('"/control/' + script + '"', app,
                              "Control HTTP handler must serve packaged script")

    def test_feature_gated_users_markup_and_script_present(self):
        html = (API / "index.html").read_text()
        js = (API / "app.js").read_text()
        account_js = (API / "accounts.js").read_text()
        self.assertIn('id="accounts"', html)
        self.assertIn('src="/control/accounts.js"', html)
        self.assertIn("HomeAwayAccounts.start(identity)", js)
        self.assertIn("manageUsers", account_js)


    def test_pending_user_create_has_explicit_feedback(self):
        js = (API / "accounts.js").read_text()
        self.assertIn('Created pending user "', js)
        self.assertIn("if (created) form.reset()", js,
                      "Do not clear entered account fields after a failed request")
        self.assertIn("const refreshed = await refresh()", js)
        self.assertIn("Change succeeded, but the user list could not refresh", js)
        self.assertIn("Change failed:", js)
        self.assertIn("No HomeAway access · pending or revoked", js)


if __name__ == "__main__":
    unittest.main()
