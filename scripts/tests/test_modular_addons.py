"""Optional monitoring diagnostics and portal: dependency and security boundaries."""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from components import SelectionError, load_catalog, resolve
import compose_generator


class ModularAddonsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_catalog()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "state"
        self.config = Path(tmp.name) / "cooling.env"
        self.config.write_text(
            "COOLING_BIND_IP=127.0.0.1\n"
            "COOLING_TLS_NAME=127.0.0.1\n"
            "COOLING_HTTPS_PORT=19443\n"
            f"COOLING_STATE_DIR={self.state}\n", encoding="ascii",
        )

    def make(self, *features):
        plan = resolve(self.catalog, tuple(sorted(features)))
        generated = compose_generator.render(plan, self.config, self.state, ROOT)
        return plan, generated, json.loads(generated["compose.generated.json"])

    def test_diagnostics_optional_and_private(self):
        plan, generated, compose = self.make("cooling-monitoring", "monitoring-diagnostics")
        self.assertEqual(
            set(compose["services"]),
            {"gateway", "grafana", "victoria-metrics", "telemetry-ingest", "metrics-diagnostics"}
        )
        self.assertEqual(compose["services"]["metrics-diagnostics"]["networks"], ["cooling-private"])
        self.assertFalse(compose["services"]["metrics-diagnostics"].get("ports"))
        self.assertTrue(compose["services"]["metrics-diagnostics"]["read_only"])
        self.assertEqual(compose["services"]["metrics-diagnostics"]["cap_drop"], ["ALL"])
        self.assertIn("HOMEAWAY_DIAGNOSTICS_MODE", compose["services"]["metrics-diagnostics"]["environment"])
        self.assertNotIn("portal", compose["services"])
        self.assertNotIn("reverse_proxy portal", generated["Caddyfile.generated"])
        self.assertIn('respond "Portal not installed" 404', generated["Caddyfile.generated"])
        self.assertNotIn("/run/secrets/portal_password", json.dumps(compose))
        self.assertEqual(plan["deployment"], "compose.generated.json")

    def test_portal_implicitly_enables_diagnostics_but_not_network_services(self):
        plan, generated, compose = self.make("linux-host-monitoring", "home-portal")
        self.assertTrue(plan["deployable"])
        self.assertIn("monitoring-diagnostics", plan["automatically_added"])
        self.assertEqual(
            set(compose["services"]),
            {"gateway", "grafana", "victoria-metrics", "node-exporter", "portal", "metrics-diagnostics"},
        )
        self.assertEqual(
            {service for service, spec in compose["services"].items() if spec.get("ports")},
            {"gateway"},
        )
        self.assertEqual(compose["services"]["portal"]["cap_drop"], ["ALL"])
        self.assertTrue(compose["services"]["portal"]["read_only"])
        self.assertEqual(compose["services"]["portal"]["depends_on"], ["metrics-diagnostics"])
        self.assertIn("/run/secrets/portal_password", json.dumps(compose))
        self.assertNotIn("telemetry-ingest", json.dumps(compose))
        self.assertNotIn("/run/telemetry-ca.crt", json.dumps(compose))
        self.assertIn("handle_path /portal/*", generated["Caddyfile.generated"])
        self.assertIn("reverse_proxy portal:8080", generated["Caddyfile.generated"])
        self.assertIn('respond "Telemetry ingestion not installed" 404',
                      generated["Caddyfile.generated"])
        self.assertNotIn("pihole", json.dumps(compose).lower())
        self.assertNotIn("wg-easy", json.dumps(compose).lower())

    def test_combined_bundle_shares_single_metrics_database_and_gateway(self):
        plan, generated, compose = self.make(
            "cooling-monitoring", "linux-host-monitoring", "home-portal"
        )
        self.assertEqual(len(compose["services"]), 7)
        self.assertEqual(set(plan["services"]), set(compose["services"]))
        self.assertEqual(len(compose["volumes"]), 4)
        self.assertEqual(
            sum(bool(spec.get("ports")) for spec in compose["services"].values()), 1
        )
        self.assertEqual(
            generated["scrape.generated.yml"].count("job_name: linux-host"), 1
        )
        self.assertIn("reverse_proxy portal:8080", generated["Caddyfile.generated"])
        self.assertIn("reverse_proxy https://telemetry-ingest:8443",
                      generated["Caddyfile.generated"])

    def test_cannot_install_portal_or_diagnostics_without_monitoring(self):
        for selection in (
            ("home-portal",), ("monitoring-diagnostics",),
            ("home-portal", "monitoring-diagnostics"),
        ):
            with self.subTest(selection=selection):
                with self.assertRaises(SelectionError):
                    resolve(self.catalog, selection)
                plan = resolve(self.catalog, selection, design=True)
                self.assertFalse(plan["deployable"])
                with self.assertRaises(SelectionError):
                    compose_generator.render(plan, self.config, self.state, ROOT)

    def test_portal_password_authentication_and_feature_filtering(self):
        spec = importlib.util.spec_from_file_location("modular_portal", ROOT / "portal/modular.py")
        portal = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(portal)
        with tempfile.TemporaryDirectory() as tmp:
            password_path = Path(tmp) / "password"
            password_path.write_text("server-generated-secret\n", encoding="ascii")
            with patch.object(portal, "PASSWORD_FILE", password_path), patch.object(
                portal, "FEATURES", frozenset({"linux-host-monitoring", "home-portal"})
            ):
                encoded = base64.b64encode(b"admin:server-generated-secret").decode()
                self.assertTrue(portal.authorized("Basic " + encoded))
                self.assertFalse(portal.authorized(None))
                self.assertFalse(portal.authorized("Bearer token"))
                self.assertFalse(portal.authorized(
                    "Basic " + base64.b64encode(b"admin:wrong").decode()))
                self.assertFalse(portal.authorized(
                    "Basic " + base64.b64encode(b"root:server-generated-secret").decode()))
                data = portal.feature_data()
                self.assertEqual(
                    {item["id"] for item in data["selectedFeatures"]},
                    {"linux-host-monitoring", "home-portal"},
                )
                self.assertNotIn("server-generated-secret", json.dumps(data))
                self.assertNotIn("dns-filtering", json.dumps(data))

    def test_existing_full_household_diagnostics_probes_are_not_in_modular_mode(self):
        spec = importlib.util.spec_from_file_location(
            "modular_diagnostics", ROOT / "monitoring/diagnostics/app.py"
        )
        with patch.dict(os.environ, {
            "HOMEAWAY_DIAGNOSTICS_MODE": "modular",
            "HOMEAWAY_SELECTED_FEATURES": "linux-host-monitoring,monitoring-diagnostics",
        }):
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(mod)
        expressions = "\n".join(p[2] for p in mod.PROBES)
        self.assertTrue(mod.MODULAR)
        self.assertIn('up{job="linux-host"}', expressions)
        self.assertNotIn('job="pi-node"', expressions)
        self.assertNotIn('job="pihole"', expressions)
        self.assertNotIn('job="wg-easy"', expressions)
        self.assertNotIn("sensordash_reading_value", expressions)

    def test_no_portal_or_diagnostics_in_basic_monitors(self):
        for selection in (("cooling-monitoring",), ("linux-host-monitoring",)):
            _, generated, compose = self.make(*selection)
            self.assertNotIn("portal", compose["services"])
            self.assertNotIn("metrics-diagnostics", compose["services"])
            self.assertIn('respond "Portal not installed" 404',
                          generated["Caddyfile.generated"])


if __name__ == "__main__":
    unittest.main()
