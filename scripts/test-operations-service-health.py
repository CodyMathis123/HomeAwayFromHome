"""Tests for fixed service-scrape checks. No network or live Pi required."""
import importlib.util
import unittest
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "monitoring/diagnostics/app.py"
spec = importlib.util.spec_from_file_location("homeaway_metrics_observer", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class ServiceScrapeTests(unittest.TestCase):
    def test_fixed_allowlisted_queries(self):
        probes = {entry[0]: entry for entry in mod.PROBES}
        self.assertEqual(probes["service.pihole"][2], 'up{job="pihole"}')
        self.assertEqual(probes["service.wireguard"][2], 'up{job="wg-easy"}')
        self.assertEqual(probes["service.victoria"][2], 'up{job="victoria-metrics"}')
        for key in ("service.pihole", "service.wireguard", "service.victoria"):
            self.assertEqual(probes[key][3], "boolean")

    def test_boolean_scrape_states(self):
        for raw, expected in (("1", "observed"), ("0", "attention")):
            check = mod.summarized_probe("service.pihole", "Pi-hole", "boolean",
                [{"value": [0, raw]}], "test")
            self.assertEqual(check["state"], expected)
        for rows in ([], [{"value": [0, "1"]}, {"value": [0, "1"]}]):
            check = mod.summarized_probe("service.pihole", "Pi-hole", "boolean", rows, "test")
            self.assertEqual(check["state"], "unknown")

if __name__ == "__main__":
    unittest.main()
