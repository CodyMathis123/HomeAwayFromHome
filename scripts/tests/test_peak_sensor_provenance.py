"""Ensure maximum-value Grafana cards identify the originating sensor."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def dashboard(name):
    return json.loads((ROOT / "grafana" / "dashboards" / name).read_text())


class PeakSensorProvenanceTests(unittest.TestCase):
    def test_current_storage_peak_shows_drive_and_sensor(self):
        panels = {p["id"]: p for p in dashboard("storage.json")["panels"]}
        peak = panels[2]
        expr = peak["targets"][0]["expr"]
        self.assertTrue(expr.startswith("topk(1, "))
        self.assertIn('device_type="Storage"', expr)
        self.assertIn('name!~"(?i).*(warning|critical).*"', expr)
        self.assertIn("group_left(display,device,name,device_type)", expr)
        self.assertEqual(peak["options"]["textMode"], "value_and_name")
        self.assertEqual(peak["targets"][0]["legendFormat"], "{{display}}")
        self.assertIn("label_replace(", expr)
        self.assertIn("label_join(", expr)
        self.assertIn('alias!="unassigned"', expr)
        self.assertIn('alias="unassigned"', expr)
        self.assertFalse(peak["options"]["wideLayout"])
        self.assertEqual(peak["options"]["justifyMode"], "center")
        self.assertEqual(peak["fieldConfig"]["defaults"]["custom"]["textSize"]["valueSize"], 46)
        self.assertIn("group_left(name,device,device_type)", expr)

    def test_reporting_peaks_keep_identity_but_averages_remain_aggregates(self):
        panels = {p["id"]: p for p in dashboard("reporting-summary.json")["panels"]}
        for panel_id in (1, 2, 5, 6, 9, 10):
            panel = panels[panel_id]
            self.assertTrue(panel["targets"][0]["expr"].startswith("topk(1, "), panel_id)
            self.assertEqual(panel["options"]["textMode"], "value_and_name", panel_id)
            self.assertIn("{{", panel["targets"][0]["legendFormat"], panel_id)
        for panel_id in (3, 4, 7, 8, 11, 12):
            self.assertNotIn("topk(1, ", panels[panel_id]["targets"][0]["expr"], panel_id)


if __name__ == "__main__":
    unittest.main()
