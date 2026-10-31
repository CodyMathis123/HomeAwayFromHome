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
            for name in ("sensordash.json", "cooling-systems.json", "electrical.json")
        }
        self.assertEqual(len({d["uid"] for d in dashboards.values()}), 3)
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
            self.assertIn("group_right()", query)
            self.assertIn("sensordash_cooling_binding_value", query)
            self.assertIn("== 1", query)
        self.assertIn('role=~"fan-group"', "\n".join(queries))
        self.assertIn('role=~"pump"', "\n".join(queries))

    def test_electrical_dashboard_never_conflates_ratings_with_measurements(self):
        d = json.loads((PANELS / "electrical.json").read_text(encoding="utf-8"))
        self.assertIn("Ratings are estimates", d["description"])
        queries = [t["expr"] for p in d["panels"] for t in p["targets"]]
        self.assertIn("sensordash_component_info_value", queries[0])
        for q in queries[1:]:
            self.assertIn("sensordash_accessory_power_value", q)
            self.assertIn("on(host,system,component) group_left(", q)
            self.assertIn("sensordash_component_info_value", q)
            self.assertIn("== 1", q)
        self.assertIn('source="measured"', queries[3])
        self.assertIn('role="pump"', queries[4])
        self.assertIn('role="fan-group"', queries[2])
        self.assertTrue(all(p["targets"][0].get("range", True) for p in d["panels"][1:]))

    def test_electrical_dashboard_labels_and_inventory_are_readable(self):
        d = json.loads((PANELS / "electrical.json").read_text(encoding="utf-8"))
        inventory = d["panels"][0]
        self.assertEqual(inventory["title"], "Cooling component inventory")
        self.assertEqual(len(inventory["targets"]), 1)
        organize = inventory["transformations"][0]
        self.assertEqual(organize["id"], "organize")
        visible = organize["options"]["renameByName"]
        for field in ("component_name", "system_name", "role", "host"):
            self.assertIn(field, visible)
        hidden = organize["options"]["excludeByName"]
        for field in ("component", "system", "__name__", "Time", "Value"):
            self.assertTrue(hidden.get(field), field)
        for panel in d["panels"][1:]:
            target = panel["targets"][0]
            self.assertIn("label_replace(", target["expr"])
            self.assertIn("revision_short", target["expr"])
            self.assertIn("revision", target["expr"])
            self.assertIn("{{source}}", target["legendFormat"])
            self.assertIn("{{revision_short}}", target["legendFormat"])
            self.assertNotIn("{{revision}}", target["legendFormat"])

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
