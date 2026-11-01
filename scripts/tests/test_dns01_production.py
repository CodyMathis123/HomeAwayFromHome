"""Production DNS-01 is an explicit cert-only, reversible gateway overlay."""
from pathlib import Path
import importlib.util
import io
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ProductionDNS01Contracts(unittest.TestCase):
    def test_gateway_only_and_token_isolation(self):
        compose = (ROOT / "compose.dns01-production.yaml").read_text()
        base = (ROOT / "compose.control.yaml").read_text()
        self.assertIn("  gateway:", compose)
        self.assertNotIn("  pc-control:", compose)
        self.assertNotIn("  authelia:", compose)
        self.assertNotIn("  grafana:", compose)
        self.assertNotIn("ports:", compose)
        self.assertNotIn("networks:", compose)
        self.assertNotIn("cap_add:", compose)
        self.assertNotIn("privileged:", compose)
        self.assertIn("cloudflare_dns01_token", compose)
        self.assertNotIn("cloudflare_dns01_token", base)
        self.assertIn("./control/gateway/Caddyfile.dns01-production", compose)
        self.assertIn("./control/gateway/Caddyfile:/etc/caddy/Caddyfile.legacy:ro", compose)

    def test_new_sites_are_cert_only_no_auth_or_control(self):
        caddy = (ROOT / "control/gateway/Caddyfile.dns01-production").read_text()
        self.assertIn("import /etc/caddy/Caddyfile.legacy", caddy)
        self.assertEqual(caddy.count("ca https://acme-v02.api.letsencrypt.org/directory"), 3)
        self.assertEqual(caddy.count("dns cloudflare {env.CF_API_TOKEN}"), 3)
        self.assertEqual(caddy.count('respond "HomeAway public HTTPS certificate ready; application routing not yet enabled" 503'), 3)
        for host in ("home", "auth", "metrics"):
            self.assertIn(host + ".homeawayfromhome.house:8443", caddy)
        self.assertNotIn("forward_auth", caddy)
        self.assertNotIn("reverse_proxy", caddy)
        self.assertNotIn("tls internal", caddy)

    def test_operator_only_script_snapshot_rollback_and_no_domain_edits(self):
        script = (ROOT / "scripts/deploy-dns01-production.sh").read_text()
        self.assertIn("--plan|--apply|--status|--rollback", script)
        self.assertIn("python3 scripts/check_dns01_production_resolved.py", script)
        self.assertIn('sudo tar -czf "$archive"', script)
        self.assertIn("backups/phase3-https", script)
        self.assertIn('[[ "$mode" == --rollback ]]', script)
        self.assertIn('up -d --no-deps --no-build --force-recreate gateway', script)
        self.assertNotIn("sudo bash ", script)
        self.assertNotIn("docker compose down", script)
        self.assertNotIn("git pull", script)
        ui = (ROOT / "scripts/deploy-ui.sh").read_text()
        self.assertNotIn("compose.dns01-production.yaml", ui)
        self.assertNotIn("cloudflare_dns01_token", ui)

    def test_gateway_health_guard_and_automatic_rollback(self):
        script = (ROOT / "scripts/deploy-dns01-production.sh").read_text()
        self.assertIn("--rollback|--probe", script)
        self.assertIn("scripts/check_dns01_gateway_https.py", script)
        self.assertIn("scripts/extract_dns01_gateway_env.py", script)
        self.assertIn("--network none --cap-drop ALL", script)
        self.assertIn("CF_API_TOKEN=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", script)
        self.assertIn('restart', script.lower())
        self.assertIn("rollback_failed_apply", script)
        self.assertIn("gateway-startup-failed-$stamp.log", script)
        self.assertIn('sudo install -m 600 /dev/null "$failure_log"', script)
        self.assertIn("ps --all --quiet gateway", script)
        self.assertIn("{{.RestartCount}}", script)

    def test_health_probe_reads_only_original_hostnames(self):
        spec = importlib.util.spec_from_file_location(
            "healthprobe", ROOT / "scripts/check_dns01_gateway_https.py")
        health = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health)
        config = {"services": {"gateway": {
            "ports": [{"host_ip": "192.168.1.43", "published": "8443"}],
            "environment": {
                "HOMEAWAY_DOMAIN": "homeaway.home.arpa",
                "HOMEAWAY_AUTH_DOMAIN": "auth.homeaway.home.arpa",
                "HOMEAWAY_METRICS_DOMAIN": "metrics.homeaway.home.arpa",
            },
        }}}
        address, names = health.targets(config)
        self.assertEqual(address, "192.168.1.43")
        self.assertEqual(len(names), 3)
        config["services"]["gateway"]["ports"][0]["host_ip"] = "8.8.8.8"
        with self.assertRaises(ValueError):
            health.targets(config)

    def test_offline_probe_env_file_contains_no_other_config(self):
        spec = importlib.util.spec_from_file_location(
            "envprobe", ROOT / "scripts/extract_dns01_gateway_env.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        config = {"services": {"gateway": {"environment": {
            "HOMEAWAY_DOMAIN": "homeaway.home.arpa",
            "HOMEAWAY_AUTH_DOMAIN": "auth.homeaway.home.arpa",
            "HOMEAWAY_METRICS_DOMAIN": "metrics.homeaway.home.arpa",
            "SOME_SECRET": "do-not-include",
        }}}}
        names = helper.get_values(config)
        self.assertEqual(set(names), set(helper.KEYS))
        self.assertNotIn("SOME_SECRET", names)

    def test_status_is_strictly_bounded_and_does_not_print_raw_logs(self):
        path = ROOT / "scripts/summarize_dns01_certificate_logs.py"
        spec = importlib.util.spec_from_file_location("summary", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        stream = [
            'gateway-1 | {"msg":"certificate obtained successfully","identifier":"home.homeawayfromhome.house","issuer":"acme-v02.api.letsencrypt.org-directory","extra":"SECRET"}',
            'gateway-1 | {"msg":"certificate obtained successfully","identifier":"metrics.homeawayfromhome.house","issuer":"acme-staging-v02.api.letsencrypt.org-directory"}',
            'gateway-1 | {"msg":"authorization finalized","identifier":"auth.homeawayfromhome.house","authz_status":"valid"}',
        ]
        result = module.summarize(stream)
        self.assertEqual(result["home.homeawayfromhome.house"], "Production certificate issued")
        self.assertEqual(result["auth.homeawayfromhome.house"],
                         "DNS challenge passed; issuance not yet confirmed")
        self.assertNotEqual(result["metrics.homeawayfromhome.house"],
                            "Production certificate issued")
        self.assertNotIn("SECRET", str(result))


if __name__ == "__main__":
    unittest.main()
