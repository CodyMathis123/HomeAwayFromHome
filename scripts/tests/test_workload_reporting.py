"""Contract checks for lightweight utilization-aware cooling reports.

The physical values are collected by LibreHardwareMonitor/SensorDash. Grafana
must not invent activity during missing/paused periods or add a second poller.
"""
import json
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parents[2] / "grafana" / "dashboards"


def open_dashboard(name):
    return json.loads((DASH / name).read_text(encoding="utf-8"))


class WorkloadReportingTests(unittest.TestCase):
    def test_overview_has_two_interpretable_utilization_charts(self):
        dashboard = open_dashboard("overview.json")
        panels = {p["id"]: p for p in dashboard["panels"]}
        self.assertEqual((panels[210]["gridPos"]["y"], panels[211]["gridPos"]["y"]),
                         (26, 26))
        self.assertEqual((panels[210]["gridPos"]["w"], panels[211]["gridPos"]["w"]),
                         (12, 12))
        for id, names in ((210, ("CPU_Total", "CPU_Core_Max")),
                          (211, ("GPU_Core", "GPU_Memory"))):
            panel = panels[id]
            self.assertEqual(panel["type"], "timeseries")
            self.assertEqual(panel["fieldConfig"]["defaults"]["unit"], "percent")
            self.assertEqual(panel["fieldConfig"]["defaults"]["max"], 100)
            self.assertEqual(len(panel["targets"]), 2)
            for target, name in zip(panel["targets"], names):
                expr = target["expr"]
                self.assertIn('kind="Load"', expr)
                self.assertIn('name="'+name+'"', expr)
                self.assertIn("sensordash_sensor_info_value", expr)
                self.assertIn("on(host,sensor)", expr)
                self.assertTrue(target["range"])
                self.assertNotIn("vector(0)", expr)
                self.assertNotIn('sensor=~"/', expr)
        # No temperature / power panel silently shifted into the load row.
        self.assertEqual(panels[200]["gridPos"]["y"], 16)
        self.assertEqual(panels[201]["gridPos"]["y"], 16)
        self.assertGreaterEqual(panels[202]["gridPos"]["y"], 35)

    def test_detailed_telemetry_keeps_activity_separate_from_vram_bytes(self):
        dashboard = open_dashboard("sensordash.json")
        panels = {p["id"]: p for p in dashboard["panels"]}
        load = panels[120]
        used = panels[121]
        self.assertEqual(load["fieldConfig"]["defaults"]["unit"], "percent")
        self.assertIn('kind="Load"', load["targets"][0]["expr"])
        self.assertIn("CPU_Core_Max", load["targets"][0]["expr"])
        self.assertIn("GPU_Memory", load["targets"][0]["expr"])
        self.assertEqual(used["fieldConfig"]["defaults"]["unit"], "bytes")
        self.assertIn('kind="Data"', used["targets"][0]["expr"])
        self.assertIn("GPU_Memory_Used", used["targets"][0]["expr"])
        self.assertIn("D3D_Dedicated_Memory_Used", used["targets"][0]["expr"])
        self.assertIn("on(host,sensor)", used["targets"][0]["expr"])
        self.assertNotIn("vector(0)", used["targets"][0]["expr"])
        self.assertEqual(panels[10]["gridPos"]["y"], 41)

    def test_low_load_baselines_are_cautious_and_lazy(self):
        dashboard = open_dashboard("reporting-summary.json")
        rows = [p for p in dashboard["panels"] if p["id"] == 220]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["type"], "row")
        self.assertTrue(row["collapsed"], "Expensive 30-day rollups should be opt-in")
        self.assertEqual({p["id"] for p in row["panels"]}, set(range(221, 227)))
        stats = {p["id"]: p for p in row["panels"]}
        for id, unit in ((221, "m"), (222, "celsius"), (223, "celsius"),
                         (224, "watt"), (225, "watt"), (226, "celsius")):
            p = stats[id]
            self.assertEqual(p["type"], "stat")
            self.assertEqual(p["fieldConfig"]["defaults"]["unit"], unit)
            self.assertEqual(p["fieldConfig"]["defaults"]["noValue"], "No qualified samples")
            self.assertEqual(p["options"]["graphMode"], "none")
            self.assertEqual(len(p["targets"]), 1)
            t = p["targets"][0]
            self.assertTrue(t["instant"])
            self.assertFalse(t["range"])
            expr = t["expr"]
            self.assertIn("sensordash_reading_value", expr)
            self.assertIn("sensordash_sensor_info_value", expr)
            self.assertIn('name="CPU_Total"', expr)
            self.assertIn('name="GPU_Core"', expr)
            self.assertIn("avg_over_time(", expr)
            self.assertIn("max_over_time(", expr)
            self.assertIn("count_over_time(", expr)
            self.assertIn("[15m]", expr)
            self.assertIn(" >= 30", expr)
            self.assertIn(" < 5", expr)
            self.assertIn(" < 15", expr)
            self.assertIn("time() - timestamp(", expr)
            self.assertIn(" < 90", expr)
            self.assertIn("and on(host)", expr)
            self.assertIn("[$__range:5m]", expr)
            self.assertNotIn("vector(0)", expr)
            self.assertNotIn('sensor=~"/', expr)
        for id in (222, 223, 224, 225, 226):
            expr = stats[id]["targets"][0]["expr"]
            self.assertIn("quantile_over_time(0.5", expr)
            self.assertNotIn("sum(", expr,
                "Don't add overlapping CPU/GPU power sources together")
        for id, ident in ((222, "Core__Tdie_"),
                          (223, 'name="GPU_Core"'),
                          (224, 'name="Package"'),
                          (225, 'name="GPU_Package"'),
                          (226, 'name="Water_Temperature"')):
            self.assertIn(ident, stats[id]["targets"][0]["expr"])
        self.assertIn("sum_over_time(", stats[221]["targets"][0]["expr"])
        self.assertIn("* 5", stats[221]["targets"][0]["expr"])
        # Missing one of the two physical load sensors -> no idle series.
        self.assertIn(" and on(host) ", stats[221]["targets"][0]["expr"])
        # Existing reporting headline cards stay in the uncluttered main row.
        self.assertTrue(all(p["id"] not in (221, 222, 223, 224, 225, 226)
                            for p in dashboard["panels"]))


if __name__ == "__main__":
    unittest.main()
