"""Regression tests for the Grafana peak-card design contract."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "grafana_stat_audit", ROOT / "scripts/audit_grafana_stat_labels.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class GrafanaStatAuditTests(unittest.TestCase):
    def test_reporting_peak_cards_are_centered_and_attributed(self):
        findings = audit.inspect()
        rows = {(Path(path).name, panel_id): category
                for path, panel_id, _, category in findings}
        for panel_id in (1, 2, 5, 6, 9, 10):
            self.assertEqual(rows[("reporting-summary.json", panel_id)],
                             "attributed / standardized", panel_id)
        self.assertEqual(rows[("storage.json", 2)], "attributed / standardized")
        for panel_id in (5, 6):
            self.assertEqual(rows[("overview.json", panel_id)],
                             "attributed / standardized", panel_id)

    def test_audits_all_dashboard_json_files(self):
        findings = audit.inspect()
        files = {path for path, _, _, _ in findings}
        # File discovery must not be a manually maintained allowlist.
        actual = {str(p.relative_to(ROOT))
                  for p in (ROOT / "grafana/dashboards").rglob("*.json")
                  if any(panel.get("type") == "stat"
                         for panel in __import__("json").loads(p.read_text()).get("panels", []))}
        self.assertEqual(files, actual)


if __name__ == "__main__":
    unittest.main()
