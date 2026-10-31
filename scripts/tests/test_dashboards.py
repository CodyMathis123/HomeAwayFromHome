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
            for name in ("overview.json", "sensordash.json", "cooling-systems.json", "electrical.json", "dns-vpn.json", "storage.json", "events.json")
        }
        self.assertEqual(len({d["uid"] for d in dashboards.values()}), 7)
        for name, d in dashboards.items():
            self.assertFalse(d["editable"], name)
            self.assertIn("read-only", d["tags"], name)
            self.assertEqual(len({p["id"] for p in d["panels"]}), len(d["panels"]), name)
            self.assertTrue(all(p["datasource"]["uid"] == "homeaway-vm"
                                for p in d["panels"]), name)

    def test_cooling_panels_use_binding_metadata_not_copied_raw_series(self):
        d = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in d["panels"]}
        inventory_query = panels[1]["targets"][0]["expr"]
        self.assertIn("sensordash_cooling_binding_value", inventory_query)
        self.assertIn("== 1", inventory_query)
        for id in range(2, 10):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn("sensordash_reading_value", query)
            self.assertIn("group_right()", query)
            self.assertIn("sensordash_cooling_binding_value", query)
            self.assertIn("== 1", query)
            self.assertIn("sensordash_sensor_info_value", query)
            self.assertEqual(panels[id]["targets"][0]["legendFormat"],
                             "{{component_name}} · {{name}}")
            self.assertIn('"component_name", "$1 $2", "component_name"', query)
            self.assertIn('"name", "$1 $2", "name"', query)
        self.assertIn('role=~"fan-group"', "\n".join(
            panels[id]["targets"][0]["expr"] for id in range(2, 10)))
        self.assertIn('role=~"pump"', "\n".join(
            panels[id]["targets"][0]["expr"] for id in range(2, 10)))

    def test_cooling_metadata_and_legends_are_readable(self):
        d = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        inventory = next(p for p in d["panels"] if p["id"] == 1)
        self.assertEqual(inventory["title"], "Sensor assignments · reference")
        query = inventory["targets"][0]["expr"]
        for required in ("sensordash_cooling_binding_value",
                         "sensordash_sensor_info_value", "group_left(name,alias,device)",
                         "label_replace(", "component_name", "system_name", "== 1"):
            self.assertIn(required, query)
        organize = inventory["transformations"][0]
        self.assertEqual(organize["id"], "organize")
        columns = organize["options"]["renameByName"]
        self.assertEqual(columns["name"], "Sensor")
        self.assertEqual(columns["component_name"], "Component")
        for key in ("sensor", "component", "system", "__name__", "Time", "Value"):
            self.assertTrue(organize["options"]["excludeByName"].get(key), key)

    def test_electrical_dashboard_never_conflates_ratings_with_measurements(self):
        d = json.loads((PANELS / "electrical.json").read_text(encoding="utf-8"))
        self.assertIn("Ratings are estimates", d["description"])
        panels = {p["id"]: p for p in d["panels"]}
        self.assertIn("sensordash_component_info_value", panels[1]["targets"][0]["expr"])
        for id in (2, 3, 4, 5):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn("sensordash_accessory_power_value", query)
            self.assertIn("on(host,system,component) group_left(", query)
            self.assertIn("sensordash_component_info_value", query)
            self.assertIn("== 1", query)
            self.assertTrue(panels[id]["targets"][0].get("range", True))
        self.assertIn('source="measured"', panels[4]["targets"][0]["expr"])
        self.assertIn('role="pump"', panels[5]["targets"][0]["expr"])
        self.assertIn('role="fan-group"', panels[3]["targets"][0]["expr"])

    def test_electrical_dashboard_labels_and_inventory_are_readable(self):
        d = json.loads((PANELS / "electrical.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in d["panels"]}
        inventory = panels[1]
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
        self.assertIn("label_replace(", inventory["targets"][0]["expr"])
        for id in (2, 3, 4, 5):
            target = panels[id]["targets"][0]
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
        panels = {p["id"]: p for p in d["panels"]}
        self.assertIn("sensordash_sensor_info_value", panels[10]["targets"][0]["expr"])
        self.assertIn("label_replace(", panels[10]["targets"][0]["expr"])
        for id in (1, 2, 3, 4):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn("sensordash_reading_value", query)
            self.assertIn("label_replace(", query)
            self.assertIn('"name", "$1 $2", "name"', query)
            self.assertIn('sensor=~"$sensor"', query)
            self.assertNotIn("alias=", query)
            self.assertIn("sensordash_sensor_info_value", query)
            self.assertIn("on(host,sensor) group_left(name,alias,device)", query)
            self.assertEqual(panels[id]["targets"][0]["legendFormat"], "{{name}}")

    def test_storage_and_events_are_observational(self):
        storage = json.loads((PANELS / "storage.json").read_text(encoding="utf-8"))
        events = json.loads((PANELS / "events.json").read_text(encoding="utf-8"))
        self.assertEqual(storage["uid"], "homeaway-storage")
        self.assertEqual(events["uid"], "homeaway-events")
        s = "\\n".join(t["expr"] for p in storage["panels"] for t in p["targets"])
        e = "\\n".join(t["expr"] for p in events["panels"] for t in p["targets"])
        self.assertIn('device_type="Storage"', s)
        self.assertIn("sensordash_spool_value", s)
        self.assertIn("sensordash_sensor_info_value", s)
        self.assertIn("sensordash_reading_value", s)
        for metric in ("vm_data_size_bytes", "vm_free_disk_space_bytes",
                       "vm_rows_added_to_storage_total"):
            self.assertIn(metric, s)
        self.assertIn("topk(25, count_over_time(", s)

        self.assertIn("sensordash_event_value", e)
        self.assertIn("count_over_time(", e)
        self.assertNotIn("message=", e)
        self.assertNotIn("fan_control", e)
        for d in (storage, events):
            self.assertFalse(d["editable"])
            self.assertEqual(d["time"]["from"], "now-24h")
            self.assertEqual(len({p["id"] for p in d["panels"]}), len(d["panels"]))

    def test_cpu_amd_librehardwaremonitor_paths_are_included(self):
        # LibreHardwareMonitor AMD CPUs use /amdcpu/... not /cpu/...;
        # Grafana selectors are full regex matches, so /cpu/ was a bug.
        import re
        d = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in d["panels"]}
        for id in (1, 5, 7, 200, 201):
            query = panels[id]["targets"][0]["expr"]
            self.assertNotIn(".*/cpu.*/", query, id)
            self.assertNotIn(".*/(cpu|gpu", query, id)
            # Ensure a genuine AMD CPU ID is covered by a selector.
            matches = re.findall(r'sensor=~"([^"]+)"', query)
            self.assertTrue(matches, id)
            example = "/amdcpu/0/power/0" if id in (7, 201) else "/amdcpu/0/temperature/0"
            self.assertTrue(any(re.fullmatch(pattern, example) for pattern in matches), id)

    def test_sensordash_style_overview_is_read_only_and_gap_safe(self):
        d = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        self.assertEqual(d["uid"], "homeaway-overview")
        self.assertIn("read-only", d["tags"])
        self.assertFalse(d["editable"])
        panels = {p["id"]: p for p in d["panels"]}
        self.assertGreaterEqual(sum(p["type"] == "stat" for p in d["panels"]), 8)
        for id in (4, 9, 10):
            self.assertEqual(panels[id]["fieldConfig"]["defaults"]["unit"], "rotrpm")
        for id in (5, 6):
            self.assertIn("max_over_time(", panels[id]["targets"][0]["expr"])
            self.assertIn("[$__range]", panels[id]["targets"][0]["expr"])
        for id in (7, 8):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn('kind="Power"', query)
            self.assertTrue(query.startswith("max("), query)
            self.assertNotIn("sum(", query)
        wattage = panels[202]["targets"][0]["expr"]
        self.assertIn("sensordash_accessory_power_value", wattage)
        self.assertIn("sensordash_component_info_value", wattage)
        self.assertIn("{{source}}", panels[202]["targets"][0]["legendFormat"])
        self.assertIn("{{revision_short}}", panels[202]["targets"][0]["legendFormat"])
        self.assertNotIn("sum(", wattage)
        self.assertEqual(panels[11]["title"], "Radiator PWM duty")
        self.assertEqual(panels[11]["fieldConfig"]["defaults"]["unit"], "percent")
        self.assertIn('kind="Control"', panels[11]["targets"][0]["expr"])
        self.assertIn('role="fan-group"', panels[11]["targets"][0]["expr"])
        self.assertEqual(panels[300]["type"], "bargauge")
        self.assertIn("sensordash_sensor_info_value", panels[300]["targets"][0]["expr"])
        self.assertIn("sensordash_reading_value", panels[301]["targets"][0]["expr"])
        self.assertIn("group_left(name,alias,device)", panels[301]["targets"][0]["expr"])
        for panel in d["panels"]:
            if panel["type"] == "timeseries":
                self.assertFalse(panel["fieldConfig"]["defaults"]["custom"]["spanNulls"])
        self.assertIn('kind="Temperature"', panels[3]["targets"][0]["expr"])
        self.assertNotIn("vector(0)", panels[3]["targets"][0]["expr"])

    def test_rotational_speed_uses_revolutions_per_minute(self):
        # Grafana "rpm" is Reads per minute (rd/m), not tachometer RPM.
        # Fan and pump charts must use the rotational-speed unit "rotrpm".
        for filename in ("overview.json", "sensordash.json", "cooling-systems.json",
                         "electrical.json", "dns-vpn.json", "storage.json", "events.json"):
            d = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            for panel in d["panels"]:
                unit = panel.get("fieldConfig", {}).get("defaults", {}).get("unit")
                self.assertNotEqual(unit, "rpm", (filename, panel["title"]))
        raw = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        cooling = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        for d, ids in ((raw, (2,)), (cooling, (4, 5, 110, 111)),
                       (overview, (4, 9, 10))):
            panels = {p["id"]: p for p in d["panels"]}
            for pid in ids:
                self.assertEqual(panels[pid]["fieldConfig"]["defaults"]["unit"], "rotrpm")

    def test_dashboard_visual_contract(self):
        # Native panels only; the CSS/theme belongs to Grafana, not a patched
        # app image. Display cleanup must not touch stored sensor labels.
        for filename in ("overview.json", "sensordash.json", "cooling-systems.json",
                         "electrical.json", "dns-vpn.json", "storage.json", "events.json"):
            d = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            self.assertEqual(d["time"]["from"], "now-24h" if filename in ("storage.json", "events.json") else "now-1h")
            self.assertEqual(len(d["links"]), 1)
            nav = d["links"][0]
            self.assertEqual(nav["type"], "dashboards")
            self.assertEqual(nav["tags"], ["homeaway"])
            self.assertTrue(nav["asDropdown"])
            self.assertTrue(nav["keepTime"])
            headers = [p for p in d["panels"] if p["type"] == "text"]
            self.assertGreaterEqual(len(headers), 2, filename)
            self.assertTrue(all(p["options"]["mode"] == "markdown"
                                and p.get("transparent") is True for p in headers))
            for p in d["panels"]:
                a = p["gridPos"]
                self.assertGreater(a["h"], 0)
                self.assertGreater(a["w"], 0)
                self.assertGreaterEqual(a["x"], 0)
                self.assertLessEqual(a["x"] + a["w"], 24)
                for q in d["panels"]:
                    if p["id"] >= q["id"]:
                        continue
                    b = q["gridPos"]
                    collision = (a["x"] < b["x"] + b["w"]
                                 and a["x"] + a["w"] > b["x"]
                                 and a["y"] < b["y"] + b["h"]
                                 and a["y"] + a["h"] > b["y"])
                    self.assertFalse(collision, (filename, p["id"], q["id"]))
                if p["type"] == "timeseries":
                    self.assertEqual(p["fieldConfig"]["defaults"]["color"]["mode"],
                                     "palette-classic-by-name")
                    self.assertTrue(any(t["id"] == "renameByRegex"
                                        and t["options"]["regex"] == "/_/g"
                                        and t["options"]["renamePattern"] == " "
                                        for t in p.get("transformations", [])))
            # Keep required data sources and full IDs in *raw series*;
            # rename only graph titles or joined display/catalog fields.
            if filename in ("overview.json", "sensordash.json", "cooling-systems.json", "electrical.json"):
                self.assertTrue(any("label_replace(" in t["expr"]
                                    for p in d["panels"] for t in p["targets"]))



if __name__ == "__main__":
    unittest.main()
