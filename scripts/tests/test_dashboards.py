"""Static Grafana dashboard and data-source contracts for the raw + cooling MVP."""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANELS = ROOT / "grafana" / "dashboards"

def all_dash_panels(dashboard):
    """Include panels nested under Grafana's native collapsed rows."""
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])




class DashboardContracts(unittest.TestCase):
    def test_both_dashboards_are_provisioned(self):
        dashboards = {
            name: json.loads((PANELS / name).read_text(encoding="utf-8"))
            for name in ("overview.json", "sensordash.json", "cooling-systems.json", "electrical.json", "dns-vpn.json", "storage.json", "events.json", "system-health.json", "portal-metrics.json", "reporting-summary.json")
        }
        self.assertEqual(len({d["uid"] for d in dashboards.values()}), 10)
        for name, d in dashboards.items():
            self.assertFalse(d["editable"], name)
            self.assertIn("read-only", d["tags"], name)
            self.assertEqual(len({p["id"] for p in all_dash_panels(d)}),
                                 len(list(all_dash_panels(d))), name)
            self.assertTrue(all(p["datasource"]["uid"] == "homeaway-vm"
                                for p in all_dash_panels(d)), name)

    def test_stat_tooltips_use_readable_series_names_not_promql(self):
        # Grafana's Stat hover uses the Prometheus legend as the field name.
        # Without legendFormat, it displays the entire raw PromQL expression.
        for path in PANELS.glob("*.json"):
            dashboard = json.loads(path.read_text(encoding="utf-8"))
            for panel in all_dash_panels(dashboard):
                if panel["type"] != "stat":
                    continue
                self.assertEqual(len(panel["targets"]), 1, (path.name, panel["id"]))
                target = panel["targets"][0]
                # The two peak cards deliberately expose the winning
                # physical sensor name instead of repeating the panel title.
                expected = ("{{name}}" if
                            (path.name == "overview.json" and panel["id"] in (5, 6))
                            or (path.name == "sensordash.json" and panel["id"] in (111, 112))
                            or (path.name == "reporting-summary.json" and panel["id"] in (1, 2))
                            else "{{display}}" if
                            (path.name == "storage.json" and panel["id"] == 2)
                            or (path.name == "reporting-summary.json" and panel["id"] in (5, 6, 9, 10))
                            else panel["title"])
                # Some existing Stat cards intentionally use a concise,
                # human-readable legend rather than repeating their title.
                self.assertTrue(target["legendFormat"].strip(),
                                (path.name, panel["id"]))
                if (path.name == "overview.json" and panel["id"] in (5, 6)) or (
                    path.name == "reporting-summary.json" and panel["id"] in (1, 2)
                ):
                    self.assertEqual(target["legendFormat"], expected,
                                     (path.name, panel["id"]))
                self.assertNotIn("sensordash_", target["legendFormat"])
                self.assertLessEqual(len(target["legendFormat"]), 80)

    def test_next_sensor_panels_use_physical_metadata_and_real_units(self):
        cooling = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        reports = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        cp = {p["id"]: p for p in all_dash_panels(cooling)}
        rp = {p["id"]: p for p in all_dash_panels(reports)}
        self.assertEqual(cp[8]["fieldConfig"]["defaults"]["unit"], "litreh")
        expected = {
            115: ("Temperature", "Water_Temperature", "celsius"),
            116: ("Flow", "Flow", "litreh"),
            117: ("Conductivity", "Conductivity", "suffix: µS/cm"),
            118: ("Level", "Water_Quality", "percent"),
            119: ("Temperature", "External_Temperature", "celsius"),
            120: ("Voltage", "VCC|VCC_USB", "volt"),
            121: ("Factor", "_Alarm", "none"),
            122: ("Power", "Dissipated_Power", "watt"),
        }
        for number, (kind, name, unit) in expected.items():
            panel = cp[number]
            query = panel["targets"][0]["expr"]
            self.assertIn('kind="'+kind+'"', query)
            self.assertIn(name, query)
            self.assertIn('device="high_flow_NEXT"', query)
            self.assertIn("on(host,sensor) group_left(name,device)", query)
            self.assertEqual(panel["fieldConfig"]["defaults"]["unit"], unit)
            self.assertNotIn('sensor=~"/', query)
        for number, kind in ((201, "Temperature"), (202, "Flow"),
                             (203, "Conductivity"), (204, "Level")):
            self.assertIn('device="high_flow_NEXT"', rp[number]["targets"][0]["expr"])
            self.assertIn('kind="'+kind+'"', rp[number]["targets"][0]["expr"])
        self.assertEqual(rp[202]["fieldConfig"]["defaults"]["unit"], "litreh")

    def test_portal_metrics_includes_current_next_coolant_and_flow(self):
        portal = json.loads((PANELS / "portal-metrics.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in all_dash_panels(portal)}
        self.assertEqual(portal["refresh"], "30s")
        self.assertEqual(portal["templating"]["list"], [])
        self.assertEqual(panels[11]["title"], "Coolant temperature")
        self.assertEqual(panels[12]["title"], "Coolant flow")
        self.assertEqual(panels[11]["fieldConfig"]["defaults"]["unit"], "celsius")
        self.assertEqual(panels[12]["fieldConfig"]["defaults"]["unit"], "litreh")
        expected = ((11, "Temperature", "Water_Temperature"),
                    (12, "Flow", "Flow"))
        for number, kind, sensor_name in expected:
            p = panels[number]
            self.assertEqual(p["type"], "stat")
            target = p["targets"][0]
            self.assertTrue(target["instant"])
            self.assertFalse(target["range"])
            expr = target["expr"]
            self.assertIn('kind="'+kind+'"', expr)
            self.assertIn('device="high_flow_NEXT"', expr)
            self.assertIn('name="'+sensor_name+'"', expr)
            self.assertIn("on(host,sensor) group_left()", expr)
            self.assertIn("sensordash_sensor_info_value", expr)
            self.assertNotIn("vector(0)", expr)
            self.assertNotIn("/usbhid/", expr)
            self.assertEqual(target["legendFormat"], p["title"])
            self.assertEqual(p["fieldConfig"]["defaults"]["noValue"], "—")
        self.assertEqual((panels[11]["gridPos"]["y"],
                          panels[12]["gridPos"]["y"]), (5, 5))
        self.assertEqual(panels[9]["gridPos"]["y"], 15)

    def test_cooling_panels_use_binding_metadata_not_copied_raw_series(self):
        d = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in all_dash_panels(d)}
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
        inventory = next(p for p in all_dash_panels(d) if p["id"] == 1)
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
        panels = {p["id"]: p for p in all_dash_panels(d)}
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
        panels = {p["id"]: p for p in all_dash_panels(d)}
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
        expressions = "\n".join(t["expr"] for p in all_dash_panels(d) for t in p["targets"])
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
        self.assertEqual(len({p["id"] for p in all_dash_panels(d)}),
                             len(list(all_dash_panels(d))))
        # The quiet-network rate should not be duplicated as an oversized
        # status tile. Summary counts remain gauges, never counter rates.
        self.assertEqual(d["time"]["from"], "now-1h")
        panels = {p["id"]: p for p in all_dash_panels(d)}
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
                for p in all_dash_panels(d) for t in p["targets"]), 1,
        )
        for id in (10, 11):
            mappings = panels[id]["fieldConfig"]["defaults"]["mappings"][0]["options"]
            self.assertEqual(mappings["1"]["text"], "UP")
            self.assertEqual(mappings["0"]["text"], "DOWN")

    def test_catalog_is_distinct_from_raw_identity(self):
        d = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in all_dash_panels(d)}
        self.assertIn("sensordash_sensor_info_value", panels[10]["targets"][0]["expr"])
        self.assertIn("label_replace(", panels[10]["targets"][0]["expr"])
        for id in (1, 2, 3, 4):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn("sensordash_reading_value", query)
            self.assertIn("label_replace(", query)
            self.assertIn('"name", "$1 $2", "name"', query)
            self.assertIn('sensor=~"$sensor"', query)
            # Alias selectors apply to the presentation metadata, never to
            # the raw reading series: identity remains the stable sensor ID.
            raw_selector = query.split("sensordash_reading_value{", 1)[1].split("}", 1)[0]
            self.assertNotIn("alias=", raw_selector)
            self.assertIn("sensordash_sensor_info_value", query)
            self.assertIn("on(host,sensor) group_left(name,alias,device)", query)
            self.assertEqual(panels[id]["targets"][0]["legendFormat"], "{{name}}")

    def test_hardware_temperature_stat_attribution_is_atomic(self):
        dashboard = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in dashboard["panels"]}
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        self.assertEqual(set(variables), {"host", "sensor"})
        self.assertIn("Highest reported temperature", panels[100]["options"]["content"])
        self.assertIn("Historical peak temperature", panels[100]["options"]["content"])
        self.assertIn("update together", panels[100]["options"]["content"])
        for panel_id, title in (
            (111, "Highest reported temperature"),
            (112, "Historical peak temperature · selected range"),
        ):
            panel = panels[panel_id]
            self.assertEqual(panel["title"], title)
            self.assertNotIn("subtitle", panel)
            self.assertEqual(panel["targets"][0]["legendFormat"], "{{name}}")
            self.assertEqual(panel["options"]["textMode"], "value_and_name")
            self.assertEqual(panel["options"]["orientation"], "vertical")
            self.assertEqual(panel["options"]["wideLayout"], False)
            self.assertEqual(panel["options"]["justifyMode"], "center")
            self.assertEqual(panel["options"]["graphMode"], "none")
            self.assertTrue(panel["targets"][0]["instant"])
            self.assertEqual(panel["fieldConfig"]["defaults"]["custom"]["textSize"]["titleSize"], 12)
            self.assertGreater(panel["fieldConfig"]["defaults"]["custom"]["textSize"]["valueSize"], 36)
            q = panel["targets"][0]["expr"]
            self.assertIn('host=~"$host"', q)
            self.assertIn('sensor=~"$sensor"', q)
            self.assertIn('kind="Temperature"', q)
            self.assertIn('name!~"(?i).*(warning|critical).*"', q)
            self.assertIn("topk(1,", q)
            self.assertIn("group_left(name)", q)
            self.assertIn('alias!="unassigned"', q)
            self.assertIn('alias="unassigned"', q)
            self.assertIn('"name", "$1", "alias", "(.+)"', q)
            self.assertIn('"name", "_+", " "', q)
            self.assertIn('"name", "^ +| +$", ""', q)
            self.assertNotIn("query_result(", q)
            self.assertNotIn("vector(0)", q)
            if panel_id == 112:
                self.assertIn("[$__range]", q)
                self.assertIn("max_over_time(", q)
            else:
                self.assertNotIn("[$__range]", q)
        self.assertEqual(dashboard["templating"]["list"][0]["name"], "host")
        self.assertEqual(dashboard["templating"]["list"][1]["name"], "sensor")
        self.assertIn("can be stale", panels[100]["options"]["content"])
        self.assertIn("selected Grafana time range", panels[100]["options"]["content"])

    def test_user_alias_is_display_name_with_raw_fallback(self):
        # The exporter always emits alias=unassigned when no user alias exists.
        # PromQL's label_replace runs on a separate metadata branch for real
        # aliases; a disjoint fallback retains the original name and sensor ID.
        # This is presentation only: never relabel stored raw measurements.
        targets = {
            "sensordash.json": (1, 2, 3, 4),
            "cooling-systems.json": tuple(range(2, 10)),
            "overview.json": (200, 201, 301),
            "storage.json": (5, 6, 7, 8, 17),
            "portal-metrics.json": (9,),
        }
        for filename, ids in targets.items():
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            panels = {panel["id"]: panel for panel in all_dash_panels(dashboard)}
            for pid in ids:
                with self.subTest(dashboard=filename, panel=pid):
                    query = panels[pid]["targets"][0]["expr"]
                    self.assertIn('alias!="unassigned"', query)
                    self.assertIn('alias="unassigned"', query)
                    self.assertIn('label_replace(sensordash_sensor_info_value', query)
                    self.assertIn('"name", "$1", "alias", "(.+)"', query)
                    self.assertIn(" on(host,sensor) ", query)
                    self.assertIn(" or (sensordash_sensor_info_value", query)
                    self.assertIn("== 1", query)
                    self.assertNotIn("sensor=CPU_Watts", query)
        # Metadata reference tables intentionally retain the separately
        # exported original name and configured alias.
        for filename, pid in (
            ("sensordash.json", 10),
            ("cooling-systems.json", 1),
            ("storage.json", 10),
        ):
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            expr = next(p for p in all_dash_panels(dashboard) if p["id"] == pid)["targets"][0]["expr"]
            self.assertNotIn('alias!="unassigned"', expr)
        # Fixed SSD warning/critical limits are still filtered BEFORE
        # changing display labels for physical-drive temperature charts.
        storage = json.loads((PANELS / "storage.json").read_text(encoding="utf-8"))
        drive = next(p for p in all_dash_panels(storage) if p["id"] == 5)["targets"][0]["expr"]
        self.assertIn('name!~"(?i).*(warning|critical).*"', drive)

    def test_all_physical_temperature_views_hide_fixed_nvme_thresholds(self):
        # LHM reports these as Temperature readings even though they are
        # fixed drive specifications (e.g. FireCuda 89C warning, 94C critical).
        # Match original metadata BEFORE replacing the visible name with an alias.
        reference = 'name!~"(?i).*(warning|critical).*"'
        views = {
            "storage.json": (2, 5),
            "sensordash.json": (1, 111, 112),
            "overview.json": (3, 200),
            "portal-metrics.json": (9,),
            "cooling-systems.json": (2, 7, 9),
        }
        for filename, panel_ids in views.items():
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            panels = {panel["id"]: panel for panel in all_dash_panels(dashboard)}
            for panel_id in panel_ids:
                with self.subTest(dashboard=filename, panel=panel_id):
                    expr = panels[panel_id]["targets"][0]["expr"]
                    self.assertIn('kind="Temperature"', expr)
                    self.assertIn(reference, expr)
                    self.assertIn("sensordash_sensor_info_value", expr)
                    # Both alias branches and (for winner-selecting Stat
                    # panels) the initial selection metadata must exclude
                    # fixed NVMe warning/critical specifications.
                    if 'alias!="unassigned"' in expr:
                        expected = (3 if filename == "sensordash.json"
                                    and panel_id in (111, 112) else 2)
                        self.assertEqual(expr.count(reference), expected)
                        self.assertLess(
                            expr.index(reference),
                            expr.index('"display", "$1", "alias", "(.+)"'
                                       if filename == "storage.json" and panel_id == 2
                                       else '"name", "$1", "alias", "(.+)"'))
        # Mixed sensor tables / sample rankings must filter only storage
        # fixed temperatures, preserving other dynamic warning signals.
        for filename, panel_id in (("overview.json", 301), ("storage.json", 17)):
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            expr = next(p for p in all_dash_panels(dashboard)
                        if p["id"] == panel_id)["targets"][0]["expr"]
            with self.subTest(dashboard=filename, panel=panel_id):
                self.assertIn("unless on(host,sensor)", expr)
                self.assertIn('device_type="Storage"', expr)
                self.assertIn('kind="Temperature"', expr)
                self.assertIn('name=~"(?i).*(warning|critical).*"', expr)
        # These remain specifications in reference inventories; the
        # measurement exporter and metadata are never changed/deleted.
        for filename, panel_id in (
            ("storage.json", 10), ("sensordash.json", 10),
            ("cooling-systems.json", 1)
        ):
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            expr = next(p for p in all_dash_panels(dashboard)
                        if p["id"] == panel_id)["targets"][0]["expr"]
            self.assertIn("sensordash_sensor_info_value", expr)
            self.assertNotIn('name!~"(?i).*(warning|critical).*"', expr)

    def test_static_drive_temperature_limits_never_count_as_live_heat(self):
        # LHM's NVMe "Warning Temperature" and "Critical Temperature"
        # are device specifications, not physical/current observations.
        # The metadata name label uses underscores after line-protocol
        # sanitization; PromQL regex matching must be case-insensitive.
        import re
        pattern = r"(?i).*(warning|critical).*"
        for name in ("Warning_Temperature", "Critical_Temperature",
                     "critical temperature", "Temperature Warning"):
            self.assertRegex(name, pattern)
        for name in ("Composite_Temperature", "Temperature_1", "Drive_Temperature"):
            self.assertNotRegex(name, pattern)

        scenarios = (
            ("storage.json", (2, 5)),
            ("overview.json", (3, 200)),
            ("sensordash.json", (1, 111, 112)),
        )
        selector = 'name!~"(?i).*(warning|critical).*"'
        for file, panel_ids in scenarios:
            d = json.loads((PANELS / file).read_text(encoding="utf-8"))
            panels = {p["id"]: p for p in all_dash_panels(d)}
            for panel_id in panel_ids:
                query = panels[panel_id]["targets"][0]["expr"]
                with self.subTest(dashboard=file, panel=panel_id):
                    self.assertIn(selector, query)
                    self.assertIn("sensordash_sensor_info_value", query)
                    self.assertIn("on(host,sensor)", query)
                    self.assertIn('kind="Temperature"', query)
            if file == "storage.json":
                self.assertIn('device_type="Storage"',
                              panels[2]["targets"][0]["expr"])
                self.assertIn('device_type="Storage"',
                              panels[5]["targets"][0]["expr"])
            elif file == "overview.json":
                self.assertIn('device_type="Storage"',
                              panels[3]["targets"][0]["expr"])
        # Static/diagnostic limits are not deleted from exporter metadata.
        storage = json.loads((PANELS / "storage.json").read_text(encoding="utf-8"))
        self.assertNotIn(selector, next(p for p in all_dash_panels(storage)
                                        if p["id"] == 10)["targets"][0]["expr"])
        raw = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        self.assertNotIn(selector, next(p for p in raw["panels"]
                                        if p["id"] == 10)["targets"][0]["expr"])

    def test_storage_and_events_are_observational(self):
        storage = json.loads((PANELS / "storage.json").read_text(encoding="utf-8"))
        events = json.loads((PANELS / "events.json").read_text(encoding="utf-8"))
        self.assertEqual(storage["uid"], "homeaway-storage")
        self.assertEqual(events["uid"], "homeaway-events")
        s = "\\n".join(t["expr"] for p in all_dash_panels(storage) for t in p["targets"])
        e = "\\n".join(t["expr"] for p in all_dash_panels(events) for t in p["targets"])
        self.assertIn('device_type="Storage"', s)
        self.assertIn("sensordash_spool_value", s)
        self.assertIn("sensordash_sensor_info_value", s)
        self.assertIn("sensordash_reading_value", s)
        for metric in ("vm_data_size_bytes", "vm_free_disk_space_bytes",
                       "vm_rows_added_to_storage_total"):
            self.assertIn(metric, s)
        self.assertIn("topk(25, (count_over_time(", s)
        self.assertIn("unless on(host,sensor)", s)

        self.assertIn("sensordash_event_value", e)
        self.assertIn("count_over_time(", e)
        self.assertNotIn("message=", e)
        self.assertNotIn("fan_control", e)
        for d in (storage, events):
            self.assertFalse(d["editable"])
            self.assertEqual(d["time"]["from"], "now-24h")
            self.assertEqual(len({p["id"] for p in all_dash_panels(d)}),
                             len(list(all_dash_panels(d))))

    def test_cpu_gpu_cards_use_exported_device_family_instead_of_id_guessing(self):
        import re
        d = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in all_dash_panels(d)}
        for id, family in ((1, "Cpu"), (2, "Gpu"), (5, "Cpu"),
                           (6, "Gpu"), (7, "Cpu"), (8, "Gpu")):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn(('device_type=~"Gpu(Amd|Nvidia|Intel)?"' if family == "Gpu" else 'device_type="Cpu"'), query, id)
            self.assertIn("sensordash_sensor_info_value", query, id)
            self.assertIn("on(host,sensor)", query, id)
            self.assertNotIn("sensor=~", query, id)
        # Historical overview plots may still use broad vendor-compatible
        # selectors until their display-name joins are refactored.
        for id in (200, 201):
            query = panels[id]["targets"][0]["expr"]
            example = "/amdcpu/0/power/0" if id == 201 else "/amdcpu/0/temperature/0"
            selectors = re.findall(r'sensor=~"([^"]+)"', query)
            self.assertTrue(any(re.fullmatch(s, example) for s in selectors), id)

    def test_overview_latest_and_peak_temperatures_have_clear_provenance(self):
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in overview["panels"]}
        self.assertIn("latest-available", panels[100]["options"]["content"])
        self.assertIn("update together", panels[101]["options"]["content"])
        for latest_id, peak_id, family in ((1, 5, "CPU"), (2, 6, "GPU")):
            latest = panels[latest_id]
            peak = panels[peak_id]
            self.assertEqual(latest["title"], family + " · highest reported temperature")
            self.assertEqual(peak["title"], family + " · historical peak (selected range)")
            self.assertTrue(latest["targets"][0]["range"])
            self.assertNotIn("instant", latest["targets"][0])
            self.assertEqual(latest["options"]["graphMode"], "area")
            self.assertEqual(latest["options"]["reduceOptions"]["calcs"], ["lastNotNull"])
            self.assertTrue(latest["targets"][0]["expr"].startswith("max("))
            self.assertIn("not a fixed", latest["description"])
            self.assertIn("currently online", latest["description"])
            self.assertTrue(peak["targets"][0]["range"])
            self.assertNotIn("instant", peak["targets"][0])
            self.assertEqual(peak["options"]["graphMode"], "area")
            self.assertEqual(peak["options"]["reduceOptions"]["calcs"], ["max"])
            self.assertEqual(peak["options"]["textMode"], "value_and_name")
            self.assertEqual(peak["options"]["orientation"], "vertical")
            self.assertNotIn("subtitle", peak)
            self.assertIs(peak["options"]["wideLayout"], False)
            self.assertEqual(peak["options"]["justifyMode"], "center")
            self.assertEqual(peak["fieldConfig"]["defaults"]["custom"]["textSize"]["titleSize"], 12)
            self.assertGreater(peak["fieldConfig"]["defaults"]["custom"]["textSize"]["valueSize"], 36)
            self.assertEqual(peak["targets"][0]["legendFormat"], "{{name}}")
            query = peak["targets"][0]["expr"]
            self.assertIn("topk(1, max_over_time(", query)
            self.assertIn("[$__range] @ end()", query)
            self.assertIn("[$__interval]", query)
            self.assertEqual(peak["targets"][0]["interval"], "2m",
                             "Sparkline lookbehind must not be shorter than the expected telemetry cadence")
            self.assertIn("and on(host,sensor)", query)
            self.assertIn("group_left(name)", query)
            self.assertIn("last_over_time(sensordash_sensor_info_value", query)
            self.assertIn('alias!="unassigned"', query)
            self.assertIn('alias="unassigned"', query)
            self.assertIn('"name", "$1", "alias", "(.+)"', query)
            self.assertIn('label_transform(label_transform(', query)
            self.assertIn('"name", "_+", " "', query)
            self.assertIn('"name", "^ +| +$", ""', query)
            self.assertIn("sensor", peak["description"])
            self.assertNotIn("vector(0)", query)

    def test_peak_stat_uses_one_result_for_name_and_value(self):
        dashboard = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        variables = {v["name"]: v for v in dashboard["templating"]["list"]}
        self.assertEqual(set(variables), {"host"})
        self.assertEqual(dashboard["templating"]["list"][0]["name"], "host")
        panels = {panel["id"]: panel for panel in dashboard["panels"]}
        for id, label in ((5, "CPU · historical peak (selected range)"),
                          (6, "GPU · historical peak (selected range)")):
            panel = panels[id]
            self.assertEqual(panel["title"], label)
            self.assertNotIn("subtitle", panel)
            self.assertEqual(panel["options"]["textMode"], "value_and_name")
            self.assertEqual(panel["options"]["orientation"], "vertical")
            self.assertIs(panel["options"]["wideLayout"], False)
            self.assertEqual(panel["options"]["graphMode"], "area")
            self.assertEqual(panel["options"]["justifyMode"], "center")
            self.assertEqual(panel["targets"][0]["legendFormat"], "{{name}}")
            self.assertTrue(panel["targets"][0]["range"])
            self.assertEqual(panel["options"]["reduceOptions"]["calcs"], ["max"])
            query = panel["targets"][0]["expr"]
            self.assertIn("topk(1, max_over_time(", query)
            self.assertIn("[$__range] @ end()", query)
            self.assertIn("group_left(name)", query)
            self.assertIn("label_transform(label_transform(", query)
            self.assertNotIn("query_result(", query)
            self.assertNotIn("vector(0)", query)
        self.assertIn("update together", panels[101]["options"]["content"])

    def test_peak_series_has_single_selected_sensor_and_never_mutates_catalog(self):
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in overview["panels"]}
        for id in (5, 6):
            query = panels[id]["targets"][0]["expr"]
            self.assertEqual(query.count("topk(1,"), 1)
            self.assertEqual(query.count("last_over_time(sensordash_sensor_info_value"), 3)
            self.assertEqual(query.count("@ end()"), 4)
            self.assertNotIn("offset -", query)
            self.assertFalse(panels[id]["editable"] if "editable" in panels[id] else False)
        self.assertIn("sensor", panels[101]["options"]["content"])

    def test_sensordash_style_overview_is_read_only_and_gap_safe(self):
        d = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        self.assertEqual(d["uid"], "homeaway-overview")
        self.assertIn("read-only", d["tags"])
        self.assertFalse(d["editable"])
        panels = {p["id"]: p for p in all_dash_panels(d)}
        self.assertGreaterEqual(sum(p["type"] == "stat" for p in all_dash_panels(d)), 8)
        for id in (4, 10):
            self.assertEqual(panels[id]["fieldConfig"]["defaults"]["unit"], "rotrpm")
        self.assertEqual(panels[9]["fieldConfig"]["defaults"]["unit"], "s")
        self.assertIn('timestamp(sensordash_reading_value', panels[9]["targets"][0]["expr"])
        self.assertIn("[24h]", panels[9]["targets"][0]["expr"])
        self.assertEqual(panels[9]["title"], "Windows telemetry · sample age")
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
        for panel in all_dash_panels(d):
            if panel["type"] == "timeseries":
                self.assertFalse(panel["fieldConfig"]["defaults"]["custom"]["spanNulls"])
        self.assertIn('kind="Temperature"', panels[3]["targets"][0]["expr"])
        self.assertNotIn("vector(0)", panels[3]["targets"][0]["expr"])

    def test_rotational_speed_uses_revolutions_per_minute(self):
        # Grafana "rpm" is Reads per minute (rd/m), not tachometer RPM.
        # Fan and pump charts must use the rotational-speed unit "rotrpm".
        for filename in ("overview.json", "sensordash.json", "cooling-systems.json",
                         "electrical.json", "dns-vpn.json", "storage.json", "events.json", "system-health.json"):
            d = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            for panel in all_dash_panels(d):
                unit = panel.get("fieldConfig", {}).get("defaults", {}).get("unit")
                self.assertNotEqual(unit, "rpm", (filename, panel["title"]))
        raw = json.loads((PANELS / "sensordash.json").read_text(encoding="utf-8"))
        cooling = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        for d, ids in ((raw, (2,)), (cooling, (4, 5, 110, 111)),
                       (overview, (4, 10))):
            panels = {p["id"]: p for p in all_dash_panels(d)}
            for pid in ids:
                self.assertEqual(panels[pid]["fieldConfig"]["defaults"]["unit"], "rotrpm")

    def test_pi_system_health_uses_host_metrics_and_does_not_claim_windows_faults(self):
        d = json.loads((PANELS / "system-health.json").read_text(encoding="utf-8"))
        self.assertEqual(d["uid"], "homeaway-system")
        self.assertFalse(d["editable"])
        self.assertIn("read-only", d["tags"])
        self.assertEqual(d["time"]["from"], "now-24h")
        panels = {p["id"]: p for p in all_dash_panels(d)}
        q = {id: p["targets"][0]["expr"] for id, p in panels.items() if p["targets"]}
        self.assertIn('node_cpu_seconds_total{job="pi-node",mode="idle"}', q[1])
        self.assertIn('node_thermal_zone_temp{job="pi-node"', q[2])
        self.assertIn("node_memory_MemAvailable_bytes", q[3])
        self.assertIn("vm_free_disk_space_bytes", q[4])
        self.assertIn("node_boot_time_seconds", q[5])
        self.assertIn("up{job=~", q[6])
        self.assertIn("timestamp(sensordash_reading_value", q[7])
        self.assertIn("[24h]", q[7])
        self.assertIn("vm_data_size_bytes", q[8])
        self.assertIn("sum by(job)(rate(node_disk_", q[15])
        self.assertIn('direction", "read"', q[15])
        self.assertIn('direction", "write"', q[15])
        self.assertIn("count_over_time(", q[17])
        self.assertNotIn("rate(sensordash_reading_value", q[17])
        self.assertIn("may simply be asleep", d["panels"][5]["options"]["content"])
        for p in all_dash_panels(d):
            if p["type"] == "timeseries":
                self.assertFalse(p["fieldConfig"]["defaults"]["custom"]["spanNulls"])
            for target in p["targets"]:
                self.assertNotIn("fan_control", target["expr"])
                self.assertNotIn("remote_action", target["expr"])

    def test_gpu_families_and_cooling_role_summary(self):
        for filename in ("overview.json", "reporting-summary.json"):
            d = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            for panel in all_dash_panels(d):
                for target in panel.get("targets", []):
                    expr = target["expr"]
                    self.assertNotIn('device_type="Gpu"', expr, (filename, panel["id"]))
            gpu = [p for p in all_dash_panels(d) if p["title"].startswith("GPU")]
            self.assertTrue(gpu)
            self.assertTrue(any('device_type=~"Gpu(Amd|Nvidia|Intel)?"' in t["expr"]
                                for p in gpu for t in p["targets"]))
        d = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in all_dash_panels(d)}
        # Historical RPM traces now live only on Cooling Systems; the
        # summary retains each distinct role's period peak.
        for id, role in ((9, "fan-group"), (10, "pump")):
            query = panels[id]["targets"][0]["expr"]
            self.assertIn('role="' + role + '"', query)
            self.assertIn("sensordash_cooling_binding_value", query)
            self.assertIn("on(host,sensor)", query)
            self.assertEqual(panels[id]["fieldConfig"]["defaults"]["unit"], "rotrpm")
        self.assertNotIn("avg(avg_over_time(", panels[10]["targets"][0]["expr"])
        for redundant in (20, 21, 22, 23, 24, 26, 27):
            self.assertNotIn(redundant, panels, "Detailed history belongs to dedicated dashboards")
        self.assertFalse(any(p["fieldConfig"]["defaults"]["custom"]["spanNulls"]
                             for p in all_dash_panels(d) if p["type"] == "timeseries"))

    def test_summary_period_comparisons_and_data_presence_are_gap_safe(self):
        dashboard = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        panels = {panel["id"]: panel for panel in all_dash_panels(dashboard)}
        for panel_id in (30, 31, 32, 33):
            panel = panels[panel_id]
            self.assertEqual(panel["type"], "stat")
            expression = panel["targets"][0]["expr"]
            self.assertIn("[$__range] offset $__range", expression)
            self.assertIn("[$__range]", expression)
            self.assertIn("on(host,sensor)", expression)
            self.assertNotIn("or vector(0)", expression)
            self.assertEqual(panel["targets"][0]["legendFormat"], panel["title"])
        self.assertIn('device_type=~"Gpu(Amd|Nvidia|Intel)?"',
                      panels[31]["targets"][0]["expr"])
        for panel_id in (34, 35):
            self.assertIn("[24h]", panels[panel_id]["targets"][0]["expr"])
            self.assertFalse(panels[panel_id]["fieldConfig"]["defaults"]["custom"]["spanNulls"])
        data_presence = panels[36]
        self.assertEqual(len(data_presence["targets"]), 2)
        self.assertTrue(all("count_over_time(" in target["expr"] and "[1h]" in target["expr"]
                            for target in data_presence["targets"]))
        self.assertNotIn("avg(", "\\n".join(t["expr"] for t in data_presence["targets"]))
        self.assertIn("sensordash_spool_value", panels[37]["targets"][0]["expr"])
        self.assertNotIn("vm_data_size_bytes", panels[37]["targets"][0]["expr"])
        self.assertIn("equal duration", dashboard["description"].lower())

    def test_reporting_summary_peak_temperature_picks_one_fixed_sensor(self):
        report = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        panels = {p["id"]: p for p in report["panels"]}
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        overview_panels = {p["id"]: p for p in overview["panels"]}
        for report_id, overview_id in ((1, 5), (2, 6)):
            panel = panels[report_id]
            query = panel["targets"][0]["expr"]
            # Historical winner is pinned to the dashboard end time, not
            # reevaluated for every step (which produces several Stat values).
            self.assertEqual(query, overview_panels[overview_id]["targets"][0]["expr"])
            self.assertEqual(query.count("topk(1,"), 1)
            self.assertIn("[$__range] @ end()", query)
            self.assertIn("and on(host,sensor)", query)
            self.assertEqual(panel["targets"][0]["legendFormat"], "{{name}}")
            self.assertEqual(panel["options"]["reduceOptions"]["calcs"], ["max"])
            self.assertEqual(panel["options"]["textMode"], "value_and_name")
            self.assertEqual(panel["options"]["graphMode"], "area")

    def test_reporting_summary_uses_metadata_and_preserves_gaps(self):
        d = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        self.assertEqual(d["uid"], "homeaway-sensordash-summary")
        self.assertFalse(d["editable"])
        self.assertEqual(d["time"]["from"], "now-7d")
        queries = "\\n".join(t["expr"] for p in all_dash_panels(d) for t in p["targets"])
        self.assertIn('device_type="Cpu"', queries)
        self.assertIn('device_type=~"Gpu(Amd|Nvidia|Intel)?"', queries)
        self.assertIn("count_over_time(", queries)
        self.assertIn("sensordash_spool_value", queries)
        self.assertNotIn("or vector(0)", queries)
        # Peak temperature cards use short-window series for a real sparkline.\n        other_queries = "\\n".join(t["expr"] for p in all_dash_panels(d)\n                                   if p["id"] not in (1, 2) for t in p["targets"])\n        self.assertNotIn("[$__interval]", other_queries)
        for p in all_dash_panels(d):
            if p["type"] == "timeseries":
                self.assertFalse(p["fieldConfig"]["defaults"]["custom"]["spanNulls"])

    def test_dashboard_consolidation_preserves_real_metrics(self):
        overview = json.loads((PANELS / "overview.json").read_text(encoding="utf-8"))
        op = {p["id"]: p for p in all_dash_panels(overview)}
        self.assertEqual(op[4]["title"], "Radiator fan speed")
        self.assertEqual(op[9]["title"], "Windows telemetry · sample age")
        self.assertNotEqual(op[4]["targets"][0]["expr"], op[9]["targets"][0]["expr"])
        self.assertTrue(op[9]["targets"][0]["instant"])
        self.assertEqual(op[9]["fieldConfig"]["defaults"]["noValue"], "No samples in 24h")
        self.assertNotIn("vector(0)", op[9]["targets"][0]["expr"])

        cooling = json.loads((PANELS / "cooling-systems.json").read_text(encoding="utf-8"))
        cp = {p["id"]: p for p in all_dash_panels(cooling)}
        row = cp[104]
        self.assertEqual(row["type"], "row")
        self.assertTrue(row["collapsed"])
        self.assertEqual({p["id"] for p in row["panels"]}, {7, 8, 9})
        self.assertEqual(len([p for p in cooling["panels"] if p["id"] in (7, 8, 9)]), 0)
        for probe in row["panels"]:
            self.assertIn("sensordash_reading_value", probe["targets"][0]["expr"])
            self.assertFalse(probe["fieldConfig"]["defaults"]["custom"]["spanNulls"])

        storage = json.loads((PANELS / "storage.json").read_text(encoding="utf-8"))
        sp = {p["id"]: p for p in all_dash_panels(storage)}
        archive = sp[104]
        self.assertTrue(archive["collapsed"])
        self.assertEqual({p["id"] for p in archive["panels"]}, set(range(11, 18)))
        self.assertIn("vm_data_size_bytes", sp[11]["targets"][0]["expr"])
        self.assertIn("vm_free_disk_space_bytes", sp[12]["targets"][0]["expr"])
        self.assertIn("sensordash_sensor_info_value", sp[10]["targets"][0]["expr"])

        dns = json.loads((PANELS / "dns-vpn.json").read_text(encoding="utf-8"))
        dp = {p["id"]: p for p in all_dash_panels(dns)}
        self.assertNotIn(2, dp)
        self.assertNotIn(5, dp)
        self.assertIn("homeaway_pihole_queries_window", dp[8]["targets"][0]["expr"])
        self.assertIn("homeaway_pihole_cached_window", "\\n".join(
            t["expr"] for t in dp[8]["targets"]))

        report = json.loads((PANELS / "reporting-summary.json").read_text(encoding="utf-8"))
        self.assertLessEqual(max(p["gridPos"]["y"]+p["gridPos"]["h"]
                                  for p in report["panels"]), 64)
        for panel in all_dash_panels(report):
            for target in panel.get("targets", []):
                self.assertNotIn("vector(0)", target["expr"])
        expected_class = {
            "overview.json": "everyday",
            "system-health.json": "everyday",
            "dns-vpn.json": "everyday",
            "sensordash.json": "hardware",
            "cooling-systems.json": "hardware",
            "storage.json": "hardware",
            "electrical.json": "analysis",
            "reporting-summary.json": "analysis",
            "events.json": "diagnostics",
            "portal-metrics.json": "embedded",
        }
        for filename, category in expected_class.items():
            dashboard = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            self.assertIn("homeaway", dashboard["tags"])
            self.assertIn(category, dashboard["tags"])
        self.assertEqual(report["refresh"], "5m")
        self.assertEqual(dns["refresh"], "30s")

    def test_dashboard_visual_contract(self):
        # Native panels only; the CSS/theme belongs to Grafana, not a patched
        # app image. Display cleanup must not touch stored sensor labels.
        for filename in ("overview.json", "sensordash.json", "cooling-systems.json",
                         "electrical.json", "dns-vpn.json", "storage.json", "events.json", "system-health.json"):
            d = json.loads((PANELS / filename).read_text(encoding="utf-8"))
            self.assertEqual(d["time"]["from"], "now-24h" if filename in ("storage.json", "events.json", "system-health.json") else "now-1h")
            self.assertEqual(len(d["links"]), 1)
            nav = d["links"][0]
            self.assertEqual(nav["type"], "dashboards")
            self.assertEqual(nav["tags"], ["homeaway"])
            self.assertTrue(nav["asDropdown"])
            self.assertTrue(nav["keepTime"])
            headers = [p for p in all_dash_panels(d) if p["type"] == "text"]
            self.assertGreaterEqual(len(headers), 2, filename)
            self.assertTrue(all(p["options"]["mode"] == "markdown"
                                and p.get("transparent") is True for p in headers))
            for p in all_dash_panels(d):
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
                                    for p in all_dash_panels(d) for t in p["targets"]))



if __name__ == "__main__":
    unittest.main()
