"""Regressions for opt-in shared Grafana embedding."""
import http.client
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from portal.app import Handler, ThreadingHTTPServer, shared_embed_url

ROOT = Path(__file__).resolve().parents[2]


class EmbeddedMetricsTests(unittest.TestCase):
    def test_disabled_by_default_and_rejects_untrusted_inputs(self):
        with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "", "PI_LAN_IP": "192.168.1.50"}):
            self.assertIsNone(shared_embed_url())
        with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "bad/../admin", "PI_LAN_IP": "192.168.1.50"}):
            self.assertIsNone(shared_embed_url())
        with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "a" * 32, "PI_LAN_IP": "8.8.8.8"}):
            self.assertIsNone(shared_embed_url())
        with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "a" * 32, "PI_LAN_IP": "evil.example"}):
            self.assertIsNone(shared_embed_url())

    def test_private_shared_url_only(self):
        with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "a" * 32, "PI_LAN_IP": "192.168.1.50"}):
            self.assertEqual(shared_embed_url(), "http://192.168.1.50:3000/public-dashboards/" + "a" * 32 + "?theme=dark")

    def test_endpoint_and_csp_are_opt_in(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def get():
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.request("GET", "/api/embed-config")
            response = conn.getresponse()
            headers = dict(response.getheaders())
            data = json.load(response)
            conn.close()
            return headers, data
        try:
            with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "", "PI_LAN_IP": "192.168.1.50"}):
                headers, data = get()
                self.assertFalse(data["enabled"])
                self.assertIsNone(data["url"])
                self.assertIn("frame-src 'none'", headers["Content-Security-Policy"])
            with patch.dict("os.environ", {"GRAFANA_PUBLIC_EMBED_TOKEN": "c" * 32, "PI_LAN_IP": "192.168.1.50"}):
                headers, data = get()
                self.assertTrue(data["enabled"])
                self.assertIn("/public-dashboards/", data["url"])
                self.assertIn("frame-src http://192.168.1.50:3000", headers["Content-Security-Policy"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_shared_dashboard_has_no_template_variables(self):
        dashboard = json.loads((ROOT / "grafana/dashboards/portal-metrics.json").read_text())
        self.assertEqual(dashboard["uid"], "homeaway-portal")
        self.assertFalse(dashboard["editable"])
        self.assertEqual(dashboard["refresh"], "30s")
        self.assertEqual(dashboard["templating"]["list"], [])
        self.assertEqual(len(dashboard["panels"]), 12)\n        self.assertEqual({p["id"] for p in dashboard["panels"]}, set(range(1, 13)))
        self.assertTrue(all(p["datasource"]["uid"] == "homeaway-vm" for p in dashboard["panels"]))
        for p in dashboard["panels"]:
            for target in p.get("targets", []):
                self.assertNotIn("$host", target["expr"])
                self.assertNotIn("$system", target["expr"])
                self.assertNotIn("var-", target["expr"])


if __name__ == "__main__":
    unittest.main()
