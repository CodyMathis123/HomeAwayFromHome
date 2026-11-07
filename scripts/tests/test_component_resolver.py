"""Component selection, dependency resolution and fail-closed deployment tests."""
from __future__ import annotations

import copy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import components  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "cooling_operator", ROOT / "scripts/cooling-stack.py"
)
operator = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(operator)


class ComponentResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = components.load_catalog()

    def test_only_real_features_are_selectable(self):
        items = components.describe(self.catalog)
        self.assertEqual(len(items), len(self.catalog))
        self.assertFalse(next(x for x in items if x["id"] == "metrics-storage")["selectable"])
        with self.assertRaisesRegex(components.SelectionError, "internal capability"):
            components.resolve(self.catalog, ("metrics-storage",))

    def test_cooling_only_resolves_shared_metrics_once(self):
        result = components.resolve(
            self.catalog, components.parse_selection("cooling-monitoring,cooling-monitoring")
        )
        self.assertTrue(result["deployable"])
        self.assertEqual(result["selected_features"], ["cooling-monitoring"])
        self.assertEqual(result["component_order"], ["metrics-storage", "cooling-monitoring"])
        self.assertEqual(result["automatically_added"], ["metrics-storage"])
        self.assertEqual(result["services"], [
            "gateway", "grafana", "telemetry-ingest", "victoria-metrics"
        ])
        self.assertEqual(result["deployment"], "compose.cooling-only.yaml")
        self.assertIn("linux-host-monitoring", result["suggestions_not_selected"])
        self.assertNotIn("pihole", result["services"])
        self.assertNotIn("wg-easy", result["services"])

    def test_future_modules_produce_explanatory_read_only_plan(self):
        selected = components.parse_selection("dns-metrics,vpn-metrics,cooling-monitoring")
        with self.assertRaisesRegex(components.SelectionError, "Not installable yet"):
            components.resolve(self.catalog, selected)
        plan = components.resolve(self.catalog, selected, design=True)
        self.assertFalse(plan["deployable"])
        self.assertIsNone(plan["deployment"])
        self.assertEqual(plan["automatically_added"].count("metrics-storage"), 1)
        self.assertIn("dns-filtering", plan["automatically_added"])
        self.assertIn("vpn", plan["automatically_added"])
        self.assertEqual(plan["services"].count("victoria-metrics"), 1)
        self.assertIn("dns-metrics", plan["planned_components"])
        self.assertIn("vpn-metrics", plan["planned_components"])

    def test_plan_never_installs_suggested_modules(self):
        plan = components.resolve(self.catalog, ("dns-filtering",), design=True)
        self.assertIn("dns-metrics", plan["suggestions_not_selected"])
        self.assertNotIn("dns-metrics", plan["component_order"])
        self.assertNotIn("pihole-exporter", plan["services"])

    def test_unwired_prototype_bundle_rejected_even_in_healthy_catalog(self):
        # Removing a feature's wires must not silently reinterpret the
        # existing four-container deployment as a different install choice.
        copy_catalog = copy.deepcopy(self.catalog)
        copy_catalog["cooling-monitoring"]["services"].remove("gateway")
        with self.assertRaisesRegex(components.SelectionError, "not wired"):
            components.resolve(copy_catalog, ("cooling-monitoring",))
        self.assertFalse(
            components.resolve(copy_catalog, ("cooling-monitoring",), design=True)["deployable"]
        )

    def test_unknown_invalid_and_duplicate_choices(self):
        for value in ("", ",cooling-monitoring", "cooling-monitoring,",
                      "cooling monitoring", "cooling-monitoring,,vpn"):
            with self.subTest(value=value), self.assertRaises(components.SelectionError):
                components.parse_selection(value)
        with self.assertRaisesRegex(components.SelectionError, "Unknown feature"):
            components.resolve(self.catalog, ("fake-feature",))
        self.assertEqual(
            components.parse_selection(" vpn , cooling-monitoring , vpn "),
            ("cooling-monitoring", "vpn"),
        )

    def test_conflicts_and_catalog_cycles_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalog.json"
            original = json.loads(components.CATALOG.read_text(encoding="utf-8"))
            modified = copy.deepcopy(original)
            modified["components"][0]["requires"].append("dns-filtering")
            modified["components"][2]["requires"].append("cooling-monitoring")
            path.write_text(json.dumps(modified), encoding="utf-8")
            with self.assertRaisesRegex(components.SelectionError, "Cyclic"):
                components.load_catalog(path)

            modified = copy.deepcopy(original)
            modified["components"][0]["conflicts"] = ["dns-filtering"]
            path.write_text(json.dumps(modified), encoding="utf-8")
            catalog = components.load_catalog(path)
            with self.assertRaisesRegex(components.SelectionError, "Incompatible"):
                components.resolve(catalog, ("cooling-monitoring", "dns-filtering"), design=True)

            modified = copy.deepcopy(original)
            modified["components"][0]["requires"] = ["missing-component"]
            path.write_text(json.dumps(modified), encoding="utf-8")
            with self.assertRaisesRegex(components.SelectionError, "unknown requires"):
                components.load_catalog(path)

    def test_duplicate_component_definition_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            data = json.loads(components.CATALOG.read_text(encoding="utf-8"))
            data["components"].append(dict(data["components"][0]))
            path = Path(folder) / "catalog.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(components.SelectionError, "Duplicate"):
                components.load_catalog(path)

    def test_plan_json_is_read_only_and_explains_future_modules(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "cooling.env"
            with patch.object(operator, "CONFIG", config), patch.object(
                operator, "STATE", Path(folder) / "state"
            ), patch.object(operator, "run") as run:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(operator.main([
                        "plan", "--components", "dns-metrics,cooling-monitoring",
                        "--design", "--json",
                    ]), 0)
                plan = json.loads(output.getvalue())
                self.assertFalse(plan["deployable"])
                self.assertIn("metrics-storage", plan["automatically_added"])
                self.assertEqual(operator.main([
                    "init", "--components", "dns-metrics", "--bind-ip", "127.0.0.1"
                ]), 2)
                run.assert_not_called()
                self.assertFalse(config.exists())

    def test_existing_selection_mismatch_blocks_docker(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "cooling.env"
            config.write_text("COOLING_COMPONENTS=cooling-monitoring\n", encoding="ascii")
            state = Path(folder) / "state"
            (state / "secrets").mkdir(parents=True)
            with patch.object(operator, "CONFIG", config), patch.object(
                operator, "STATE", state
            ), patch.object(operator, "run") as run:
                self.assertEqual(operator.main([
                    "start", "--components", "dns-filtering"
                ]), 2)
                run.assert_not_called()

    def test_saved_invalid_selection_cannot_start(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / "cooling.env"
            config.write_text("COOLING_COMPONENTS=home-portal\n", encoding="ascii")
            state = Path(folder) / "state"
            (state / "secrets").mkdir(parents=True)
            with patch.object(operator, "CONFIG", config), patch.object(
                operator, "STATE", state
            ), patch.object(operator, "run") as run:
                self.assertEqual(operator.main(["start"]), 2)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
