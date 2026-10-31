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
            for name in ("sensordash.json", "cooling-systems.json", "electrical.json", "dns-vpn.json")
        }
        self.assertEqual(len({d["uid"] for d in dashboards.values()}), 4)
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

    def test_cooling_metadata_and_legends_are_readable(self):
        d = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        inventory = d["panels"][0]
        self.assertEqual(inventory["title"], "Cooling sensor assignments")
        self.assertEqual(len(inventory["targets"]), 1)
        query = inventory["targets"][0]["expr"]
        self.assertIn("sensordash_cooling_binding_value", query)
        self.assertIn("sensordash_sensor_info_value", query)
        self.assertIn("group_left(name,alias,device)", query)
        self.assertIn("== 1", query)
        organize = inventory["transformations"][0]
        self.assertEqual(organize["id"], "organize")
        columns = organize["options"]["renameByName"]
        self.assertEqual(columns["name"], "Sensor")
        self.assertEqual(columns["component_name"], "Component")
        for key in ("sensor", "component", "system", "__name__", "Time", "Value"):
            self.assertTrue(organize["options"]["excludeByName"].get(key), key)
        for panel in d["panels"][1:]:
            target = panel["targets"][0]
            self.assertIn("sensordash_reading_value", target["expr"])
            self.assertIn("sensordash_cooling_binding_value", target["expr"])
            self.assertIn("group_right()", target["expr"])
            self.assertIn("sensordash_sensor_info_value", target["expr"])
            self.assertIn("group_left(name,alias,device)", target["expr"])
            self.assertEqual(target["legendFormat"], "{{component_name}} · {{name}}")

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

    def test_dns_vpn_dashboard_uses_aggregate_observations(self):
        d = json.loads((PANELS / "dns-vpn.json").read_text(encoding="utf-8"))
        self.assertFalse(d["editable"])
        self.assertEqual(d["uid"], "homeaway-dns-vpn")
        expressions = "\n".join(t["expr"] for p in d["panels"] for t in p["targets"])
        for metric in ("homeaway_pihole_queries_window", "homeaway_pihole_blocked_percent",
                       "homeaway_pihole_blocking_enabled", "homeaway_pihole_clients_active",
                       "wireguard_connected_peers", "wireguard_enabled_peers",
                       "wireguard_configured_peers", "wireguard_latest_handshake_seconds",
                       "wireguard_received_bytes", "wireguard_sent_bytes",
                       'up{job="pihole"}', 'up{job="wg-easy"}'):
            self.assertIn(metric, expressions)
        self.assertNotIn("wireguard_peer_endpoint_info", expressions)
        self.assertNotIn("top_domains", expressions)
        self.assertNotIn("top_clients", expressions)
        self.assertNotIn("rate(homeaway_pihole_queries_window", expressions)
        self.assertEqual(len({p["id"] for p in d["panels"]}), len(d["panels"]))
        # The quiet-network rate should not be duplicated as an oversized
        # status tile. Summary counts remain gauges, never counter rates.
        self.assertEqual(d["time"]["from"], "now-1h")
        panels = {p["id"]: p for p in d["panels"]}
        self.assertEqual(
            {t["legendFormat"] for t in panels[8]["targets"]},
            {"All queries", "Cached", "Forwarded", "Blocked"},
        )
        self.assertEqual(len(panels[8]["targets"]), 4)
        for item in panels[8]["targets"]:
            self.assertTrue(item["range"])
            self.assertIn("_window", item["expr"])
        self.assertEqual(panels[15]["title"], "VPN transferred data")
        self.assertEqual(panels[15]["fieldConfig"]["defaults"]["unit"], "bytes")
        self.assertIn("wireguard_received_bytes", panels[15]["targets"][0]["expr"])
        self.assertIn("wireguard_sent_bytes", panels[15]["targets"][0]["expr"])
        self.assertEqual(
            sum(t["expr"] == "homeaway_pihole_query_frequency"
                for p in d["panels"] for t in p["targets"]), 1,
        )
        for id in (10, 11):
            mappings = panels[id]["fieldConfig"]["defaults"]["mappings"][0]["options"]
            self.assertEqual(mappings["1"]["text"], "UP")
            self.assertEqual(mappings["0"]["text"], "DOWN")

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
