"""DNS-01 staging is opt-in and cannot modify or restart the live gateway."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class DNS01StagingContracts(unittest.TestCase):
    def test_pinned_builder_and_token_secret(self):
        docker = (ROOT / "control/gateway/Dockerfile.dns01").read_text()
        wrapper = (ROOT / "control/gateway/start-dns01.sh").read_text()
        self.assertIn("FROM caddy:2.11.7-builder-alpine", docker)
        self.assertIn("xcaddy build v2.11.7 --with github.com/caddy-dns/cloudflare@v0.2.4", docker)
        self.assertIn("FROM caddy:2.11.7-alpine", docker)
        self.assertIn("dns.providers.cloudflare", docker)
        # /usr/bin/caddy in the official image has a file capability for
        # privileged ports; this must NOT be the executable used under
        # cap_drop ALL and no-new-privileges.
        self.assertIn("/usr/local/bin/homeaway-caddy", docker)
        self.assertIn("COPY --from=builder --chmod=0755 /usr/bin/caddy", docker)
        self.assertIn("setcap -r /usr/local/bin/homeaway-caddy", docker)
        self.assertIn('test -z "$(getcap /usr/local/bin/homeaway-caddy)"', docker)
        self.assertIn("exec /usr/local/bin/homeaway-caddy run", wrapper)
        self.assertNotIn("exec /usr/bin/caddy", wrapper)
        self.assertIn("/run/secrets/cloudflare_dns01_token", wrapper)
        self.assertIn("read -r CF_API_TOKEN", wrapper)
        self.assertIn("export CF_API_TOKEN", wrapper)
        self.assertNotIn('echo "$CF_API_TOKEN"', wrapper)
        self.assertNotIn("eval ", wrapper)

    def test_fixed_staging_certificates_do_not_include_auth_or_live_routes(self):
        live = (ROOT / "control/gateway/Caddyfile").read_text()
        staging = (ROOT / "control/gateway/Caddyfile.dns01-staging").read_text()
        self.assertNotIn("import /etc/caddy/Caddyfile.legacy", staging)
        self.assertNotIn("tls internal", staging)
        self.assertIn("auto_https disable_redirects", staging)
        self.assertIn("forward_auth authelia:9091", live)
        self.assertIn("tls internal", live)
        self.assertNotIn("forward_auth", staging)
        self.assertEqual(staging.count(
            "ca https://acme-staging-v02.api.letsencrypt.org/directory"), 3)
        self.assertEqual(staging.count("dns cloudflare {env.CF_API_TOKEN}"), 3)
        self.assertIn("resolvers 1.1.1.1", staging)
        for host in ("home", "auth", "metrics"):
            self.assertIn(host + ".homeawayfromhome.house:8443", staging)
        self.assertEqual(staging.count(
            'respond "HomeAway ACME staging validation only"'), 3)

    def test_separate_compose_project_never_modifies_gateway(self):
        overlay = (ROOT / "compose.dns01-staging.yaml").read_text()
        control = (ROOT / "compose.control.yaml").read_text()
        ui = (ROOT / "scripts/deploy-ui.sh").read_text()
        script = (ROOT / "scripts/deploy-dns01-staging.sh").read_text()
        self.assertIn("  dns01-staging:", overlay)
        self.assertNotIn("  gateway:", overlay)
        self.assertNotIn("ports:", overlay)
        self.assertIn("read_only: true", overlay)
        self.assertIn("cap_drop: [ALL]", overlay)
        self.assertIn("networks:\n      - dns01-acme", overlay)
        self.assertIn("./data/control/dns01-staging/data:/data", overlay)
        self.assertIn("./data/control/dns01-staging/config:/config", overlay)
        self.assertNotIn("./data/control/caddy:/data", overlay)
        self.assertIn("file: ./secrets/control/cloudflare_dns01_token", overlay)
        self.assertNotIn("cloudflare_dns01_token", control)
        self.assertNotIn("compose.dns01-staging.yaml", ui)
        self.assertIn('sudo test -s "$secret"', script)
        self.assertIn("sudo stat -c '%a'", script)
        self.assertIn("sudo stat -c '%u'", script)
        self.assertIn('[[ "$owner" == "0" ]]', script)
        self.assertNotIn('[[ -s "$secret" ]]', script)
        self.assertNotIn('perms="$(stat -c', script)
        self.assertIn("homeaway-dns01-staging", script)
        self.assertIn("up -d --no-deps --build dns01-staging", script)
        self.assertNotIn("up -d gateway", script)
        self.assertIn('ps --all dns01-staging', script)
        self.assertIn('ps --status running --quiet dns01-staging', script)


if __name__ == "__main__":
    unittest.main()
