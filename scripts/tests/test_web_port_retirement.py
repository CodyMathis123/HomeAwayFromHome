"""Stage-2 private web host-port retirement contract tests."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class WebPortRetirementTests(unittest.TestCase):
    def test_only_essential_ports_remain(self):
        base=(ROOT/"compose.yaml").read_text()
        pihole=base.split("  pihole:\n",1)[1].split("\n  wg-easy:",1)[0]
        portal=base.split("  portal:\n",1)[1].split("\n  victoria-metrics:",1)[0]
        grafana=base.split("  grafana:\n",1)[1].split("\n  telemetry-ingest:",1)[0]
        control=(ROOT/"compose.control.yaml").read_text()
        gateway=control.split("  gateway:\n",1)[1].split("\n  authelia:",1)[0]
        for required in (":53:53/tcp",":53:53/udp"):
            self.assertIn(required,pihole)
        self.assertNotIn(":8080:80/tcp",pihole)
        self.assertNotIn("\n    ports:",portal)
        self.assertNotIn("\n    ports:",grafana)
        self.assertNotIn(":8443:8443/tcp",gateway)
        self.assertIn(":443:8443/tcp",gateway)
        self.assertNotIn(":8088:",base)
        self.assertNotIn(":3000:3000",base)
        self.assertIn(":51820/udp",base)
        self.assertIn("127.0.0.1:51821:",base)
        accounts=(ROOT/"compose.accounts.yaml").read_text()
        self.assertIn("127.0.0.1:17170:",accounts)

    def test_only_original_private_web_ports_are_in_rollback_overlay(self):
        source=(ROOT/"compose.web-lan-rollback.yaml").read_text()
        for port in (":8080:80/tcp",":8088:8080/tcp",":3000:3000/tcp",":8443:8443/tcp"):
            self.assertIn(port,source)
        for name in ("pihole","portal","grafana","gateway"):
            self.assertEqual(source.count("  "+name+":"),1)
        self.assertNotIn("wg-easy:",source)
        self.assertNotIn("53:53",source)
        self.assertNotIn(":443:8443",source)
        self.assertNotIn("secrets:",source)

    def test_narrow_rollback_and_live_checks(self):
        script=(ROOT/"scripts/deploy-private-web-ports.sh").read_text()
        for expected in ("--plan","--apply","--status","--rollback",
            "compose.web-lan-rollback.yaml","check", "validate_ports private",
            "validate_ports old","dns_prefix_live",
            "routes_ok","private_ports_live",
            "--no-deps --no-build --force-recreate",
            "PLAN ONLY: no services changed.",
            "only portal, Grafana, Pi-hole", "Caddy"):
            with self.subTest(expected=expected):
                self.assertIn(expected,script)
        self.assertNotIn("docker compose down",script)
        self.assertNotIn("force-recreate wg-easy",script)
        self.assertNotIn("tls_insecure_skip_verify",script)
        self.assertNotIn("sudo docker system prune",script)

    def test_repo_contracts_enforce_gateway_only_web(self):
        contracts=(ROOT/"scripts/check_contracts.py").read_text()
        controls=(ROOT/"scripts/check_control_contracts.py").read_text()
        for value in ('len(published("pihole")) == 2',
                      'assert not published("portal")',
                      'assert not published("grafana")'):
            self.assertIn(value,contracts)
        self.assertIn('expected = {("443", 8443, "tcp", private_ip)}',controls)

if __name__=="__main__":
    unittest.main()
