"""Static Grafana dashboard and data-source contracts for the raw + cooling MVP."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANELS = ROOT / "grafana" / "dashboards"


class DashboardContracts(unittest.TestCase):
    def test_both_dashboards_are_provisioned(self):
        dashboards = {
            name: json.loads((PANELS / name).read_text(encoding="utf-8"))
            for name in ("sensordash.json", "cooling-systems.json")
        }
        self.assertEqual(len({d["uid"] for d in dashboards.values()}), 2)
        for name, d in dashboards.items():
            self.assertFalse(d["editable"], name)
            self.assertIn("read-only", d["tags"], name)
            self.assertEqual(len({p["id"] for p in d["panels"]}), len(d["panels"]), name)
            self.assertTrue(all(p["datasource"]["uid"] == "homeaway-vm"
                                for p in d["panels"]), name)

    def test_cooling_panels_use_binding_metadata_not_copied_raw_series(self):
        d = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        queries = [t["expr"] for p in d["panels"] for t in p["targets"]]
        self.assertIn("sensordash_cooling_binding_value", queries[0])
        self.assertIn("== 1", queries[0])
        for query in queries[1:]:
            self.assertIn("sensordash_reading_value", query)
            self.assertTrue("group_right()" in query or "group_left()" in query, query)
            self.assertIn("sensordash_cooling_binding_value", query)
            self.assertIn("== 1", query)
        self.assertIn('role=~"fan-group"', "\n".join(queries))
        self.assertIn('role=~"pump"', "\n".join(queries))

    def test_catalog_is_distinct_from_raw_identity(self):
        d = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        queries = [t["expr"] for p in d["panels"] for t in p["targets"]]
        self.assertTrue(queries[0].startswith("sensordash_sensor_info_value"))
        for query in queries[1:]:
            self.assertTrue(query.startswith("sensordash_reading_value"))
            self.assertIn('sensor=~"$sensor"', query)
            self.assertNotIn("alias=", query)


if __name__ == "__main__":
    unittest.main()
