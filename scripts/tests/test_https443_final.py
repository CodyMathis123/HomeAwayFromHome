"""Pure URL migration and deployment boundary contracts for private HTTPS 443."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
path = ROOT / "scripts" / "update-authelia-canonical-urls.py"
spec = importlib.util.spec_from_file_location("authelia_portless_urls", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class FinalHTTPS443Tests(unittest.TestCase):
    def sample(self, a="https://auth.homeawayfromhome.house:8443",
               home="https://home.homeawayfromhome.house:8443"):
        return (
            "session:\n  secret: supersecret_never_log\n  cookies:\n"
            '    - domain: "homeawayfromhome.house"\n'
            '      authelia_url: "' + a + '"\n'
            '      default_redirection_url: "' + home + '"\n'
            "regulation:\n  max_retries: 4\n"
            "storage:\n  encryption_key: secret_never_log\n"
        )

    def test_only_two_session_urls_are_changed(self):
        before = self.sample()
        state, after = mod.state_and_render(before, "portless")
        self.assertEqual(state, "legacy")
        self.assertNotIn("auth.homeawayfromhome.house:8443", after)
        self.assertNotIn("home.homeawayfromhome.house:8443", after)
        self.assertIn("supersecret_never_log", after)
        self.assertIn("secret_never_log", after)
        self.assertEqual(mod.state_and_render(after, "portless"), ("portless", after))
        self.assertEqual(mod.state_and_render(after, "legacy"), ("portless", before))

    def test_fail_closed_for_mixed_or_unexpected_redirects(self):
        for text in (self.sample(a="https://auth.homeawayfromhome.house"),
                     self.sample(a="https://evil.example"),
                     self.sample().replace("default_redirection_url:", "wrong_key:"),
                     self.sample().replace("session:", "sessions:"),
                     self.sample() + "session:\n  extra: true\n"):
            with self.subTest(text=text[-80:]):
                with self.assertRaises(ValueError):
                    mod.state_and_render(text, "portless")

    def test_exact_portless_destinations_and_fallback(self):
        prod = (ROOT / "compose.production.yaml").read_text()
        self.assertIn('HOMEAWAY_CONTROL_ORIGIN: "https://home.homeawayfromhome.house"',prod)
        self.assertIn('HOMEAWAY_CONTROL_ALT_ORIGIN: "https://home.homeawayfromhome.house:8443"',prod)
        self.assertIn('HOMEAWAY_CONTROL_URL: "https://home.homeawayfromhome.house/control/"',prod)
        self.assertIn('HOMEAWAY_METRICS_ORIGIN: "https://metrics.homeawayfromhome.house"',prod)
        self.assertIn('GF_SERVER_ROOT_URL: "https://metrics.homeawayfromhome.house"',prod)
        recovery = (ROOT / "compose.account-recovery.yaml").read_text()
        self.assertIn('LLDAP_HTTP_URL: "https://home.homeawayfromhome.house/directory"',recovery)
        back = (ROOT / "compose.https443-rollback.yaml").read_text()
        self.assertNotIn("ports:",back)
        self.assertNotIn("secrets:",back)
        self.assertIn('HOMEAWAY_CONTROL_ORIGIN: "https://home.homeawayfromhome.house:8443"',back)

    def test_targeted_recovery_and_no_secret_logging(self):
        s=(ROOT/"scripts/deploy-https443-final.sh").read_text()
        for name in ("--plan","--apply","--rollback","compose.account-recovery.yaml",
                     "compose.authelia-smtp.yaml","--no-deps","restart authelia"):
            self.assertIn(name,s)
        self.assertNotIn("docker compose down",s)
        self.assertNotIn("up -d gateway",s)
        self.assertNotIn("up -d pihole",s)
        self.assertNotIn("up -d wg-easy",s)
        helper=(ROOT/"scripts/update-authelia-canonical-urls.py").read_text()
        self.assertIn("configuration.before-default-https.yml",helper)
        self.assertIn("os.replace(CANDIDATE, ACTIVE)",helper)
        self.assertIn("validate_candidate()",helper)
        self.assertIn("subprocess.DEVNULL",helper)

if __name__ == "__main__":
    unittest.main()
