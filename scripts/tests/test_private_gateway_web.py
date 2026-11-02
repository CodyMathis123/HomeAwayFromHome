"""Stage-1 HTTPS gateway readiness before any direct web ports are retired."""
import os
import runpy
from pathlib import Path
import unittest
from unittest.mock import patch

from portal.app import control_url, metrics_origin, shared_embed_url

ROOT=Path(__file__).resolve().parents[2]

class PrivateWebGatewayTests(unittest.TestCase):
    def test_both_caddy_variants_protect_pihole_admin_and_api(self):
        for flavor in ("production","account-recovery"):
            with self.subTest(flavor=flavor):
                s=(ROOT/"control/gateway"/f"Caddyfile.{flavor}").read_text()
                home=s.split("home.homeawayfromhome.house:8443 {",1)[1].split(
                    "metrics.homeawayfromhome.house:8443 {",1)[0]
                dns=home.split("handle /dns/* {",1)[1].split(
                    "\n  handle {\n    reverse_proxy portal:8080",1)[0]
                self.assertIn("@dnsRoot path /dns",home)
                self.assertIn("redir @dnsRoot /dns/admin/ 308",home)
                self.assertIn("route {",dns)
                self.assertIn("forward_auth authelia:9091",dns)
                self.assertIn("uri /api/authz/forward-auth",dns)
                self.assertIn("reverse_proxy pihole:80",dns)
                self.assertIn("uri strip_prefix /dns",dns)
                self.assertLess(dns.index("forward_auth"),dns.index("uri strip_prefix /dns"))
                self.assertLess(dns.index("uri strip_prefix /dns"),dns.index("reverse_proxy pihole"))
                self.assertNotIn("handle_path /dns/*",home)
                self.assertLess(dns.index("forward_auth"),dns.index("reverse_proxy pihole"))
                self.assertIn("request_header -Remote-User",dns)
                self.assertIn("request_header -X-HomeAway-Groups",dns)
                self.assertNotIn("tls_insecure_skip_verify",dns)
                self.assertLess(home.index("handle /dns/*"),
                                home.index("reverse_proxy portal:8080"))
                self.assertIn("forward_auth authelia:9091",home.split("handle @control",1)[1])
                self.assertIn("handle /ingest",s)
                self.assertNotIn("handle_path /api/",home)

    def test_pihole_prefix_is_supported_without_changing_dns_ports(self):
        s=(ROOT/"compose.yaml").read_text()
        piece=s.split("  pihole:\n",1)[1].split("\n  wg-easy:",1)[0]
        self.assertIn('FTLCONF_webserver_paths_prefix: "/dns"',piece)
        for port in (":53:53/tcp",":53:53/udp",":8080:80/tcp"):
            self.assertIn(port,piece)
        self.assertIn("WEBPASSWORD_FILE: pihole_password",piece)
        self.assertIn("pihole_password",piece)
        rollback=(ROOT/"compose.dns-prefix-rollback.yaml").read_text()
        self.assertIn('FTLCONF_webserver_paths_prefix: ""',rollback)
        self.assertNotIn("ports:",rollback)

    def test_portal_canonical_urls_and_strict_host_checks(self):
        env={
            "HOMEAWAY_CONTROL_URL":"https://home.homeawayfromhome.house/control/",
            "HOMEAWAY_METRICS_ORIGIN":"https://metrics.homeawayfromhome.house",
            "PI_LAN_IP":"192.168.1.43",
            "GRAFANA_PUBLIC_EMBED_TOKEN":"a"*32
        }
        with patch.dict(os.environ,env,clear=True):
            self.assertEqual(control_url(),env["HOMEAWAY_CONTROL_URL"])
            self.assertEqual(metrics_origin(),env["HOMEAWAY_METRICS_ORIGIN"])
            self.assertEqual(shared_embed_url(),
                env["HOMEAWAY_METRICS_ORIGIN"]+"/public-dashboards/"+"a"*32+"?theme=dark")
            for malicious in ("https://evil.example/control/",
                              "http://home.homeawayfromhome.house/control/",
                              "https://home.homeawayfromhome.house:444/control/"):
                with patch.dict(os.environ,{"HOMEAWAY_CONTROL_URL":malicious}):
                    self.assertIsNone(control_url())
            with patch.dict(os.environ,{"HOMEAWAY_METRICS_ORIGIN":"https://evil.example"}):
                self.assertIsNone(metrics_origin())

    def test_frontend_no_old_direct_admin_links(self):
        app=(ROOT/"portal/app.js").read_text()
        nav=(ROOT/"portal/navigation.js").read_text()
        for s in (app,nav):
            self.assertIn("/dns/admin/",s)
            self.assertIn("https://metrics.homeawayfromhome.house/",s)
            self.assertNotIn("http://' + location.hostname + ':8080",s)
            self.assertNotIn("http://' + location.hostname + ':3000",s)
            self.assertNotIn(":8080/admin/",s)
        self.assertIn("(parsed.port !== '' && parsed.port !== '8443')",app)

    def test_staged_deployment_does_not_retire_fallback_ports(self):
        script=(ROOT/"scripts/deploy-gateway-web.sh").read_text()
        for token in ("--plan","--apply","--status","--rollback-dns",
            "validate_offline","--network none","prepare-gateway-permissions.py",
            "--no-deps --no-build --force-recreate gateway",
            "--no-deps --build portal",
            "--no-deps --no-build --force-recreate pihole",
            "FTLCONF_webserver_paths_prefix=/dns",
            "verify_route","/dns/admin/",
            "PLAN ONLY: nothing changed."):
            with self.subTest(token=token): self.assertIn(token,script)
        self.assertNotIn("up -d --build pihole wg-easy",script)
        self.assertNotIn("docker compose down",script)
        self.assertNotIn("force-recreate wg-easy",script)
        self.assertNotIn("sudo docker system prune",script)
        self.assertNotIn("tls_insecure_skip_verify",script)
        self.assertIn('[[ "$dns" == 302 ]]',script)
        self.assertIn("https://auth.homeawayfromhome.house/",script)
        self.assertNotIn('"$dns" == 403',script)

    def test_dns_auth_migration_render_is_idempotent_and_limited(self):
        migration=runpy.run_path(str(ROOT/"scripts/fix-dns-admin-auth.py"))
        render=migration["updated"]
        initial=("access_control:\\n"
                 "  default_policy: deny\\n"
                 "  rules:\\n"
                 "    - domain: homeaway.home.arpa\\n"
                 "      resources:\\n"
                 "        - '^/control(?:/.*)?
        script=(ROOT/"scripts/fix-dns-admin-auth.py").read_text()
        for token in ("group:admins", "two_factor", "/dns(?:/.*)?$",
                      "configuration.before-dns-admin.yml",
                      "config", "validate", "--plan", "--apply"):
            self.assertIn(token,script)
        self.assertNotIn("default_policy: bypass",script)

if __name__=="__main__":
    unittest.main()
\\n"
                 "      policy: two_factor\\n"
                 "session:\\n"
                 "  secret: dummy\\n")
        changed,updated=render(initial)
        self.assertTrue(changed)
        self.assertIn("group:admins",updated)
        self.assertIn("'^/dns(?:/.*)?
        script=(ROOT/"scripts/fix-dns-admin-auth.py").read_text()
        for token in ("group:admins", "two_factor", "/dns(?:/.*)?$",
                      "configuration.before-dns-admin.yml",
                      "config", "validate", "--plan", "--apply"):
            self.assertIn(token,script)
        self.assertNotIn("default_policy: bypass",script)

if __name__=="__main__":
    unittest.main()
",updated)
        self.assertIn("policy: two_factor",updated)
        self.assertEqual(updated.replace(migration["RULE"],""),initial)
        changed_again,same=render(updated)
        self.assertFalse(changed_again)
        self.assertEqual(same,updated)
        with self.assertRaises(ValueError):
            render(initial.replace("default_policy: deny","default_policy: bypass"))
        with self.assertRaises(ValueError):
            render(initial.replace("session:", "    - domain: home.homeawayfromhome.house\\n"
                                   "      resources:\\n"
                                   "        - '^/dns/.*
        script=(ROOT/"scripts/fix-dns-admin-auth.py").read_text()
        for token in ("group:admins", "two_factor", "/dns(?:/.*)?$",
                      "configuration.before-dns-admin.yml",
                      "config", "validate", "--plan", "--apply"):
            self.assertIn(token,script)
        self.assertNotIn("default_policy: bypass",script)

if __name__=="__main__":
    unittest.main()
\\n"
                                   "session:"))

    def test_dns_admin_auth_migration_scoped_and_protected(self):
        script=(ROOT/"scripts/fix-dns-admin-auth.py").read_text()
        for token in ("group:admins", "two_factor", "/dns(?:/.*)?$",
                      "configuration.before-dns-admin.yml",
                      "config", "validate", "--plan", "--apply"):
            self.assertIn(token,script)
        self.assertNotIn("default_policy: bypass",script)

if __name__=="__main__":
    unittest.main()
