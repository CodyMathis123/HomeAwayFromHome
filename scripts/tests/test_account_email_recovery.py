"""Account email recovery must stay opt-in and preserve existing access boundaries."""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]

class AccountEmailRecoveryContracts(unittest.TestCase):
    def test_recovery_overlay_is_optional_and_does_not_grant_access(self):
        recovery=(ROOT/"compose.account-recovery.yaml").read_text()
        base=(ROOT/"compose.accounts.yaml").read_text()
        self.assertIn("LLDAP_SMTP_OPTIONS__ENABLE_PASSWORD_RESET",recovery)
        self.assertIn("LLDAP_HTTP_URL:",recovery)
        self.assertIn("format: raw",recovery)
        self.assertIn("lldap-smtp.env",recovery)
        self.assertIn("account-directory",recovery)
        self.assertIn("Caddyfile.account-recovery",recovery)
        self.assertNotIn("ports:",recovery)
        self.assertNotIn("privileged:",recovery)
        self.assertNotIn("AUTHELIA_",recovery)
        self.assertNotIn("HOMEAWAY_ACCOUNT_ADMIN_ENABLED",recovery)
        self.assertNotIn("LLDAP_SMTP_OPTIONS__PASSWORD_FILE",recovery)
        self.assertIn("127.0.0.1:17170:17170/tcp",base)

    def test_caddy_only_recovers_under_existing_lan_listener(self):
        default=(ROOT/"control/gateway/Caddyfile.production").read_text()
        recovery=(ROOT/"control/gateway/Caddyfile.account-recovery").read_text()
        self.assertNotIn("reverse_proxy lldap:17170",default)
        self.assertIn("home.homeawayfromhome.house:8443",recovery)
        self.assertIn("handle /directory/*",recovery)
        self.assertIn("reverse_proxy lldap:17170",recovery)
        self.assertIn("forward_auth authelia:9091",recovery)
        self.assertIn("reverse_proxy pc-control:8081",recovery)
        self.assertIn("reverse_proxy portal:8080",recovery)
        self.assertIn("reverse_proxy grafana:3000",recovery)
        self.assertEqual(default.count("tls {"),recovery.count("tls {"))

    def test_operator_docs_require_manual_activation_and_rollback(self):
        guide=(ROOT/"docs/ACCOUNT-EMAIL-RECOVERY.md").read_text()
        self.assertIn("not enabled on the pi",guide.lower())
        self.assertIn("Forgot password",guide)
        self.assertIn("Roll back",guide)
        self.assertIn("factor",guide.lower())
        self.assertNotIn("age-secret-key-",guide.lower())

if __name__=="__main__":
    unittest.main()
