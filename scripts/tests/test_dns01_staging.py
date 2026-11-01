"""DNS-01 staging is opt-in, token-isolated and never changes the live Caddyfile."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DNS01StagingContracts(unittest.TestCase):
    def test_pinned_builder_and_private_secret_entrypoint(self):
        docker = (ROOT / "control/gateway/Dockerfile.dns01").read_text()
        wrapper = (ROOT / "control/gateway/start-dns01.sh").read_text()
        self.assertIn("FROM caddy:2.11.7-builder-alpine", docker)
        self.assertIn("xcaddy build v2.11.7 --with github.com/caddy-dns/cloudflare@v0.2.4", docker)
        self.assertIn("FROM caddy:2.11.7-alpine", docker)
        self.assertIn("dns.providers.cloudflare", docker)
        self.assertIn("/run/secrets/cloudflare_dns01_token", wrapper)
        self.assertIn("read -r CF_API_TOKEN", wrapper)
        self.assertIn("export CF_API_TOKEN", wrapper)
        self.assertNotIn("echo \"$CF_API_TOKEN\"", wrapper)
        self.assertNotIn("eval ", wrapper)
        self.assertNotIn("curl ", wrapper)

    def test_staging_imports_legacy_and_uses_staging_only(self):
        live = (ROOT / "control/gateway/Caddyfile").read_text()
        staging = (ROOT / "control/gateway/Caddyfile.dns01-staging").read_text()
        self.assertIn("import /etc/caddy/Caddyfile.legacy", staging)
        self.assertIn("ca https://acme-staging-v02.api.letsencrypt.org/directory", staging)
        self.assertEqual(staging.count("ca https://acme-staging-v02.api.letsencrypt.org/directory"), 3)
        self.assertEqual(staging.count("dns cloudflare {env.CF_API_TOKEN}"), 3)
        self.assertIn("resolvers 1.1.1.1", staging)
        self.assertIn("{$HOMEAWAY_PUBLIC_HOME}:8443", staging)
        self.assertIn("{$HOMEAWAY_PUBLIC_AUTH}:8443", staging)
        self.assertIn("{$HOMEAWAY_PUBLIC_METRICS}:8443", staging)
        self.assertNotIn("forward_auth", staging, "New names cannot bypass or clone Authelia")
        self.assertIn("forward_auth authelia:9091", live)
        self.assertIn("tls internal", live)
        self.assertEqual(staging.count("respond \"HomeAway ACME staging validation only\""), 3)

    def test_not_enabled_by_normal_deployments(self):
        overlay = (ROOT / "compose.dns01-staging.yaml").read_text()
        base = (ROOT / "compose.control.yaml").read_text()
        ui = (ROOT / "scripts/deploy-ui.sh").read_text()
        self.assertIn("cloudflare_dns01_token", overlay)
        self.assertIn("file: ./secrets/control/cloudflare_dns01_token", overlay)
        self.assertNotIn("cloudflare_dns01_token", base)
        self.assertNotIn("compose.dns01-staging.yaml", ui)
        self.assertNotIn("cloudflare_dns01_token", ui)
        self.assertNotIn("ports:", overlay)
        self.assertNotIn("privileged:", overlay)
        self.assertNotIn("network_mode:", overlay)


if __name__ == "__main__":
    unittest.main()
