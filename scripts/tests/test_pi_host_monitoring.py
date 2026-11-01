"""Regression guards for Pi host metrics and separately scoped verification."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PiMetricsContracts(unittest.TestCase):
    def test_pi_exporter_is_configured_for_vm_scraping(self):
        compose = (ROOT / "compose.yaml").read_text()
        scrape = (ROOT / "monitoring/scrape.yml").read_text()
        self.assertIn("  pi-node-exporter:", compose)
        self.assertIn("  - job_name: pi-node", scrape)
        self.assertIn("pi-node-exporter:9100", scrape)
        self.assertIn("--collector.cpu", compose)
        self.assertIn("--collector.meminfo", compose)
        self.assertIn("--collector.thermal_zone", compose)

    def test_pi_check_covers_real_required_system_health_series(self):
        verify = (ROOT / "scripts/verify-pi-metrics.sh").read_text()
        for name in ("up", "node_cpu_seconds_total", "node_memory_MemTotal_bytes",
                     "node_memory_MemAvailable_bytes", "node_boot_time_seconds"):
            self.assertIn(name, verify)
        self.assertIn("raise SystemExit(2)", verify)
        self.assertIn("pi-node-exporter", verify)
        telemetry = (ROOT / "scripts/verify-telemetry.sh").read_text()
        self.assertIn("NOT CHECKED: Pi host", telemetry)
        docs = (ROOT / "docs/TELEMETRY.md").read_text()
        self.assertIn("pi-node-exporter", docs)
        self.assertIn("verify-pi-metrics.sh", docs)


if __name__ == "__main__":
    unittest.main()
