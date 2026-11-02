"""Private receiver port retirement is reversible and isolated from the stack."""
import pathlib
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class TelemetryPortRetirementTests(unittest.TestCase):
    def test_receiver_is_bridge_only_with_no_host_ports(self):
        base=(ROOT/"compose.yaml").read_text()
        receiver=base.split("  telemetry-ingest:\n",1)[1].split("\n  pihole-exporter:",1)[0]
        self.assertNotIn("    ports:",receiver)
        self.assertNotIn(":8429:",receiver)
        self.assertIn("      - infra",receiver)
        self.assertIn("./secrets/telemetry:/run/tls:ro",receiver)
        self.assertIn("    read_only: true",receiver)
        self.assertIn("    cap_drop: [ALL]",receiver)
        # No services should acquire a new host listener as part of retirement.
        self.assertNotIn(":8429:",base)
        contracts=(ROOT/"scripts/check_contracts.py").read_text()
        self.assertIn('assert not published("telemetry-ingest")',contracts)
        self.assertIn('assert "infra" in services["telemetry-ingest"]',contracts)

    def test_direct_rollback_is_explicit_and_narrow(self):
        rollback=(ROOT/"compose.telemetry-direct-rollback.yaml").read_text()
        self.assertEqual(rollback.count("  telemetry-ingest:"),1)
        self.assertNotIn("  gateway:",rollback)
        self.assertNotIn("  pihole:",rollback)
        self.assertNotIn("  grafana:",rollback)
        self.assertIn('8429:8443/tcp',rollback)
        self.assertIn("PI_LAN_IP",rollback)

    def test_cutover_guardrails(self):
        script=(ROOT/"scripts/deploy-telemetry-private.sh").read_text()
        for piece in (
            "--plan","--apply","--status","--rollback",
            "compose.telemetry-direct-rollback.yaml",
            "config -q","check_config private","check_config direct",
            'no_published_ports',
            'route_ok',
            '[[ "$code" != 401 ]]',
            'up -d --no-deps --no-build --force-recreate telemetry-ingest',
            "automatically restoring original LAN mapping",
            "Only the receiver container",
        ):
            with self.subTest(piece=piece):
                self.assertIn(piece,script)
        self.assertNotIn("docker compose down",script)
        self.assertNotIn("force-recreate gateway",script)
        self.assertNotIn("up -d --build",script)
        self.assertNotIn("tls_insecure_skip_verify",script)

    def test_verification_uses_canonical_https(self):
        script=(ROOT/"scripts/verify-telemetry.sh").read_text()
        self.assertIn("https://metrics.homeawayfromhome.house/ingest",script)
        self.assertIn('metrics.homeawayfromhome.house:443:$IP',script)
        self.assertNotIn("https://$IP:8429/ingest",script)

if __name__=="__main__":
    unittest.main()
