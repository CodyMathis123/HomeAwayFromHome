"""Regression contracts for the additive private HTTPS 443 migration."""
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]

class Private443Preview(unittest.TestCase):
    def test_only_private_gateway_gains_second_published_port(self):
        c = (ROOT / "compose.control.yaml").read_text()
        self.assertIn('PI_LAN_IP in .env}:443:8443/tcp', c)
        self.assertIn('PI_LAN_IP in .env}:8443:8443/tcp', c)
        gateway = c.split("\n  gateway:", 1)[1].split("\n  authelia:", 1)[0]
        self.assertEqual(gateway.count('443:8443/tcp'), 2)
        self.assertNotIn("0.0.0.0", gateway)
        self.assertNotIn("127.0.0.1:443", gateway)
        control = c.split("\n  pc-control:",1)[1].split("\n  metrics-diagnostics:",1)[0]
        self.assertNotIn("\n    ports:",control)

    def test_origin_and_links_remain_legacy_until_follow_up(self):
        prod=(ROOT / "compose.production.yaml").read_text()
        self.assertIn('HOMEAWAY_CONTROL_ORIGIN: "https://home.homeawayfromhome.house:8443"',prod)
        self.assertIn('HOMEAWAY_CONTROL_ALT_ORIGIN: "https://home.homeawayfromhome.house"',prod)
        self.assertIn('HOMEAWAY_METRICS_ORIGIN: "https://metrics.homeawayfromhome.house:8443"',prod)
        self.assertIn('GF_SERVER_ROOT_URL: "https://metrics.homeawayfromhome.house:8443"',prod)
        app=(ROOT / "control/api/app.py").read_text()
        self.assertIn('"https://home.homeawayfromhome.house"',app)
        self.assertIn('if trusted_origins and alternate_origin in permitted_origins',app)

    def test_authelia_session_and_tls_material_are_not_replaced(self):
        for name in ("control/gateway/Caddyfile.production",
                     "control/gateway/Caddyfile.account-recovery"):
            text=(ROOT / name).read_text()
            self.assertIn("home.homeawayfromhome.house:8443",text)
            self.assertIn("auth.homeawayfromhome.house:8443",text)
        script=(ROOT / "scripts/deploy-https443-preview.sh").read_text()
        self.assertNotIn("docker compose down",script)
        self.assertNotIn("data/control/authelia/configuration.yml",script)
        self.assertNotIn("--force-recreate",script)
        self.assertIn("--no-deps --no-build gateway",script)
        self.assertIn("--no-deps --build pc-control",script)

if __name__ == "__main__":
    unittest.main()
