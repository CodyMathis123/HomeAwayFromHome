"""Stage-1 HTTPS gateway readiness before any direct web ports are retired."""
import os
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
                dns=home.split("handle_path /dns/* {",1)[1].split(
                    "\n  handle {\n    reverse_proxy portal:8080",1)[0]
                self.assertIn("@dnsRoot path /dns",home)
                self.assertIn("redir @dnsRoot /dns/admin/ 308",home)
                self.assertIn("route {",dns)
                self.assertIn("forward_auth authelia:9091",dns)
                self.assertIn("uri /api/authz/forward-auth",dns)
                self.assertIn("reverse_proxy pihole:80",dns)
                self.assertLess(dns.index("forward_auth"),dns.index("reverse_proxy pihole"))
                self.assertIn("request_header -Remote-User",dns)
                self.assertIn("request_header -X-HomeAway-Groups",dns)
                self.assertNotIn("tls_insecure_skip_verify",dns)
                self.assertLess(home.index("handle_path /dns/*"),
                                home.index("reverse_proxy portal:8080"))
                self.assertIn("forward_auth authelia:9091",home.split("handle @control",1)[1])
                self.assertIn("handle /ingest",s)
                self.assertNotIn("handle_path /api/",home)

    def test_pihole_prefix_is_supported_without_changing_dns_ports(self):
        s=(ROOT/"compose.yaml").read_text()
        piece=s.split("  pihole:\n",1)[1].split("\n  wg-easy:",1)[0]
        self.assertIn('FTLCONF_webserver_paths_prefix: "/dns"',piece)
        for port in (":53:53/tcp",":53:53/udp"):
            self.assertIn(port,piece)
        # Phase A retains its fallback; Phase B moves it to a separate
        # explicitly opt-in Compose rollback overlay.
        if ":8080:80/tcp" not in piece:
            self.assertIn(":8080:80/tcp",
                (ROOT/"compose.web-lan-rollback.yaml").read_text())
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
        self.assertIn("parsed.port !== ''",app)
        self.assertNotIn("parsed.port !== '' && parsed.port !== '8443'",app)

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

if __name__=="__main__":
    unittest.main()
