"""Generated Compose graph tests: services, exposure, mounts, and no mutation."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from components import SelectionError, load_catalog, resolve
import compose_generator


class ComposeGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / "state"
        self.config = Path(self.tmp.name) / "cooling.env"
        self.config.write_text(
            "COOLING_BIND_IP=127.0.0.1\nCOOLING_TLS_NAME=127.0.0.1\n"
            "COOLING_HTTPS_PORT=19443\n"
            f"COOLING_STATE_DIR={self.state}\n",
            encoding="ascii",
        )

    def make(self, *selection):
        plan = resolve(self.catalog, tuple(sorted(selection)))
        rendered = compose_generator.render(plan, self.config, self.state, ROOT)
        compose = json.loads(rendered["compose.generated.json"])
        return plan, rendered, compose

    def test_cooling_only_has_one_listener_and_no_linux_scrapes(self):
        plan, rendered, data = self.make("cooling-monitoring")
        self.assertEqual(set(data["services"]), set(plan["services"]))
        self.assertEqual(set(data["services"]), {
            "grafana", "gateway", "victoria-metrics", "telemetry-ingest"
        })
        self.assertNotIn("node-exporter", data["services"])
        self.assertNotIn("job_name: linux-host", rendered["scrape.generated.yml"])
        self.assertIn("reverse_proxy https://telemetry-ingest:8443",
                      rendered["Caddyfile.generated"])
        self.assertIn("tls_trust_pool file /run/telemetry-ca.crt",
                      rendered["Caddyfile.generated"])
        grafana = json.dumps(data["services"]["grafana"])
        self.assertIn("sensordash.json", grafana)
        self.assertNotIn("linux-host.json", grafana)
        self.assertNotIn("docker.sock", json.dumps(data))
        self.assertEqual(sum(bool(v.get("ports")) for v in data["services"].values()), 1)
        self.assertEqual(data["services"]["gateway"]["ports"][0]["host_ip"], "127.0.0.1")
        self.assertTrue(data["networks"]["cooling-private"]["internal"])
        self.assertFalse((self.state / "generated").exists())

    def test_linux_host_only_does_not_install_telemetry_or_its_credentials(self):
        plan, rendered, data = self.make("linux-host-monitoring")
        self.assertEqual(set(data["services"]), set(plan["services"]))
        self.assertEqual(set(data["services"]), {
            "grafana", "gateway", "victoria-metrics", "node-exporter"
        })
        self.assertNotIn("telemetry-ingest", json.dumps(data))
        self.assertNotIn("/run/telemetry-ca.crt", json.dumps(data))
        self.assertNotIn("reverse_proxy https://telemetry-ingest",
                         rendered["Caddyfile.generated"])
        self.assertIn('respond "Telemetry ingestion not installed" 404',
                      rendered["Caddyfile.generated"])
        self.assertIn('job_name: linux-host', rendered["scrape.generated.yml"])
        self.assertIn('targets: ["node-exporter:9100"]', rendered["scrape.generated.yml"])
        self.assertIn("linux-host.json", json.dumps(data["services"]["grafana"]))
        self.assertNotIn("sensordash.json", json.dumps(data["services"]["grafana"]))
        exporter = data["services"]["node-exporter"]
        self.assertTrue(exporter["read_only"])
        self.assertEqual(exporter["cap_drop"], ["ALL"])
        self.assertFalse(exporter.get("privileged", False))
        self.assertFalse(exporter.get("ports", []))
        self.assertEqual(exporter["networks"], ["cooling-private"])
        self.assertEqual(
            {v["source"] for v in exporter["volumes"]},
            {"/proc", "/sys"},
        )

    def test_both_deduplicates_shared_backend_and_dashboards(self):
        plan, rendered, data = self.make("cooling-monitoring", "linux-host-monitoring")
        self.assertEqual(set(plan["services"]), set(data["services"]))
        self.assertEqual(len(data["services"]), 5)
        self.assertEqual(list(data["services"]).count("victoria-metrics"), 1)
        self.assertIn("telemetry-ingest", data["services"])
        self.assertIn("node-exporter", data["services"])
        self.assertEqual(rendered["scrape.generated.yml"].count("job_name: linux-host"), 1)
        self.assertIn("sensordash.json", json.dumps(data))
        self.assertIn("linux-host.json", json.dumps(data))

    def test_generation_is_idempotent_and_rejects_symlinks(self):
        _, rendered, _ = self.make("linux-host-monitoring")
        self.assertEqual(sorted(compose_generator.differences(rendered, self.state)),
                         sorted(compose_generator.GENERATED))
        compose_generator.write(rendered, self.state)
        self.assertEqual(compose_generator.differences(rendered, self.state), [])
        compose_generator.write(rendered, self.state)
        out = self.state / "generated/compose.generated.json"
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        out.unlink()
        out.symlink_to(self.config)
        with self.assertRaises(SelectionError):
            compose_generator.write(rendered, self.state)
        with self.assertRaises(SelectionError):
            compose_generator.differences(rendered, self.state)

    def test_invalid_settings_and_planned_components_fail(self):
        bad = self.config.read_text().replace("19443", "22")
        self.config.write_text(bad)
        plan = resolve(self.catalog, ("cooling-monitoring",))
        with self.assertRaises(SelectionError):
            compose_generator.render(plan, self.config, self.state, ROOT)
        unavailable = resolve(self.catalog, ("dns-metrics",), design=True)
        with self.assertRaises(SelectionError):
            compose_generator.render(unavailable, self.config, self.state, ROOT)


if __name__ == "__main__":
    unittest.main()
