"""Private portless telemetry reverse-proxy and safe rollout contracts."""
import pathlib
import re
import unittest

ROOT=pathlib.Path(__file__).resolve().parents[2]

class CanonicalIngestRouteTests(unittest.TestCase):
    def test_both_live_caddy_flavors_route_exact_ingest(self):
        for name in ('Caddyfile.production','Caddyfile.account-recovery'):
            with self.subTest(name=name):
                source=(ROOT/'control/gateway'/name).read_text()
                metrics=source.split('metrics.homeawayfromhome.house:8443 {',1)[1]
                self.assertEqual(metrics.count('handle /ingest {'),1)
                self.assertIn('reverse_proxy https://telemetry-ingest:8443',metrics)
                self.assertIn('tls_trust_pool file /run/telemetry-ca.crt',metrics)
                self.assertIn('tls_server_name {$PI_LAN_IP}',metrics)
                self.assertIn('max_size 256KB',metrics)
                self.assertIn('header_up -Cookie',metrics)
                self.assertNotIn('tls_insecure_skip_verify',source)
                self.assertIn('handle {\n    reverse_proxy grafana:3000\n  }',metrics)
                self.assertLess(metrics.index('handle /ingest {'),
                                metrics.index('reverse_proxy grafana:3000'))
                self.assertIn('forward_auth authelia:9091',source)
                self.assertIn('reverse_proxy pc-control:8081',source)

    def test_only_public_ca_is_shared_with_gateway(self):
        compose=(ROOT/'compose.production.yaml').read_text()
        gateway=compose.split('  gateway:',1)[1].split('\n  pc-control:',1)[0]
        self.assertIn('PI_LAN_IP: ${PI_LAN_IP:',gateway)
        self.assertIn('./secrets/telemetry/ca.crt:/run/telemetry-ca.crt:ro',gateway)
        self.assertNotIn('server.key',gateway)
        self.assertNotIn('telemetry/token',gateway)
        self.assertNotIn('telemetry-ca.key',gateway)
        self.assertNotIn('ports:',gateway)
        init=(ROOT/'scripts/init-telemetry.sh').read_text()
        self.assertIn('install -m 644 "$temp/ca.crt"',init)
        self.assertIn('install -m 600 "$temp/server.key"',init)
        self.assertIn('chmod 600 secrets/telemetry/token',init)
        self.assertIn('chmod 700 secrets/telemetry',init)

    def test_operator_controls_and_rollout_safety(self):
        script=(ROOT/'scripts/deploy-telemetry-route.sh').read_text()
        for clause in ('--plan','--apply','--status','config -q',
                       'prepare-gateway-permissions.py','openssl verify',
                       'validate --config /etc/caddy/Caddyfile',
                       'up -d --no-deps --no-build --force-recreate gateway',
                       'metrics.homeawayfromhome.house/ingest',
                       '[[ "$code" == 401 ]]','curl --silent'):
            self.assertIn(clause,script)
        self.assertNotIn('up -d --build',script)
        self.assertNotIn('stop telemetry-ingest',script)
        self.assertNotIn('docker compose down',script)
        self.assertNotIn('tls_insecure_skip_verify',script)

    def test_offline_token_is_valid_shape_and_plan_checks_config(self):
        script=(ROOT/'scripts/deploy-telemetry-route.sh').read_text()
        token=re.search(r'^offline_cf_token="([^"]+)"
    unittest.main()
,script,re.MULTILINE)
        self.assertIsNotNone(token,"Offline dummy token must be fixed, never secret-derived")
        self.assertRegex(token.group(1),r'^[A-Za-z0-9_-]{35,50}
    unittest.main()
)
        self.assertNotIn('offline-validation-only',script)
        self.assertIn('CF_API_TOKEN="$offline_cf_token"',script)
        self.assertIn('--network none',script)
        self.assertNotIn('cloudflare_dns01_token',script,
                         "Real DNS-01 token must never be read for offline validation")
        plan_index=script.index('validate_offline\nif [[ "$mode" == "--plan" ]]')
        apply_index=script.index('prepare-gateway-permissions.py --apply')
        self.assertLess(plan_index,apply_index,
                        "Plan must validate before any permission or deployment mutation")
        self.assertIn('PASS: Caddy configuration validated offline',script)
        self.assertIn('request appears to reach Grafana',script)

if __name__=="__main__":
    unittest.main()
