"""Authentication-boundary and Authelia bootstrap static contracts."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
script = ROOT / "scripts/init-control-auth.py"
spec = importlib.util.spec_from_file_location("control_bootstrap_test", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AuthContracts(unittest.TestCase):
    def test_safe_domains(self):
        self.assertEqual(module.safe_domain("homeaway.home.arpa"), "homeaway.home.arpa")
        for invalid in ("", "https://evil.com", "foo/../bar", "localhost", "a;b.home.arpa", "example.com:8443"):
            with self.assertRaises(ValueError):
                module.safe_domain(invalid)

    def test_auth_requires_2fa_and_denies_by_default(self):
        cfg = module.build_configuration("homeaway.home.arpa", "auth.homeaway.home.arpa", "homeaway.home.arpa",
                                         "s" * 80, "j" * 80, "e" * 80)
        self.assertIn("default_policy: deny", cfg)
        self.assertIn("policy: two_factor", cfg)
        self.assertIn("group:admins", cfg)
        self.assertNotIn("policy: bypass", cfg)
        self.assertIn("resources:", cfg)
        self.assertIn("/control(?:/.*)?", cfg)
        self.assertIn("https://auth.homeaway.home.arpa:8443", cfg)

    def test_proxy_network_and_no_control_api_host_port(self):
        compose = (ROOT / "compose.control.yaml").read_text()
        self.assertIn("internal: true", compose)
        self.assertIn("group_add:", compose)
        self.assertIn("/run/homeaway-pcctl:/run/homeaway-pcctl:ro", compose)
        self.assertIn("/etc/homeaway-pcctl:/etc/homeaway-pcctl:ro", compose)
        self.assertIn("8443:8443/tcp", compose)
        self.assertIn("no-new-privileges:true", compose)
        caddy = (ROOT / "control/gateway/Caddyfile").read_text()
        self.assertIn("forward_auth authelia:9091", caddy)
        self.assertIn("/api/authz/forward-auth", caddy)
        self.assertIn("request_header -X-HomeAway-User", caddy)
        self.assertIn("reverse_proxy pc-control:8081", caddy)


if __name__ == "__main__":
    unittest.main()
