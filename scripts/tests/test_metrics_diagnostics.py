"""Contract tests for the fixed, internal VictoriaMetrics observer.

No live metrics service or real SensorDash host is required for unit tests.
"""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "metrics_observer_for_tests", ROOT / "monitoring/diagnostics/app.py")
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)


def row(value, host=None):
    return {"metric": {"host": host} if host else {},
            "value": [1750000000, str(value)]}


class ObserverContracts(unittest.TestCase):
    def test_query_has_fixed_internal_address_and_bounded_output(self):
        expression = observer.PROBES[0][2]
        doc = {"status": "success", "data": {
            "resultType": "vector", "result": [row(1)]}}
        def fake_open(request_url, timeout):
            self.assertEqual(timeout, observer.TIMEOUT_SECONDS)
            self.assertEqual(urlsplit(request_url).netloc, "victoria-metrics:8428")
            self.assertEqual(parse_qs(urlsplit(request_url).query)["query"], [expression])
            return io.BytesIO(json.dumps(doc).encode())
        with patch.object(observer, "urlopen", side_effect=fake_open):
            self.assertEqual(observer.read_vector(expression), [row(1)])
        with patch.object(observer, "urlopen",
                          return_value=io.BytesIO(b"x" * 65537)):
            with self.assertRaises(ValueError):
                observer.read_vector(expression)
        self.assertEqual(len({item[0] for item in observer.PROBES}), len(observer.PROBES))
        self.assertTrue(all("[" not in item[0] and "__" not in item[0]
                            for item in observer.PROBES))

    def test_no_results_are_unknown_and_invalid_numeric_values_are_not_good(self):
        base = observer.summarized_probe(
            "pi.cpu", "Pi CPU", "percent", [], "2026-11-01T00:00:00+00:00")
        self.assertEqual(base["state"], "unknown")
        bad = observer.summarized_probe(
            "pi.cpu", "Pi CPU", "percent", [row("NaN")], base["checkedAt"])
        self.assertIsNone(bad["value"])
        self.assertEqual(bad["state"], "unknown")
        down = observer.summarized_probe(
            "pi.scrape", "Pi exporter", "boolean", [row(0)], base["checkedAt"])
        self.assertEqual(down["state"], "attention")
        self.assertEqual(down["value"], 0)

    def test_telemetry_age_is_evidence_not_a_power_assertion(self):
        clock = 1762010000.0
        hosts = observer.telemetry_hosts([
            row(clock - 30, "main-desktop"),
            row(clock - 720, "sleeping-pc"),
            row(clock - 30, "bad label\npassword"),
            row(clock + 500, "future"),
            row(clock - 90000, "too-old"),
            row(float("nan"), "not-numeric"),
            {}, None,
        ], clock)
        self.assertEqual([item["host"] for item in hosts],
                         ["main-desktop", "sleeping-pc"])
        self.assertEqual([item["state"] for item in hosts],
                         ["observed", "stale"])
        self.assertIn("cause unknown", hosts[1]["detail"])
        self.assertFalse(any("password" in str(item) for item in hosts))

    def test_snapshot_keeps_independent_unknown_checks_and_host_freshness(self):
        clock = 1762010000.0
        def fake_query(expression):
            if "timestamp(sensordash_reading_value[24h])" in expression:
                return [row(clock - 310, "desktop")]
            if 'node_cpu_seconds_total' in expression:
                raise TimeoutError("Synthetic no CPU rate")
            if expression == 'up{job="pi-node"}':
                return [row(1)]
            if expression == "vm_free_disk_space_bytes":
                return [row(10 * (1024 ** 3))]
            return []
        with patch.object(observer, "read_vector", side_effect=fake_query), \
                patch.object(observer.time, "time", return_value=clock):
            snap = observer.snapshot()
        self.assertEqual(snap["schemaVersion"], 1)
        self.assertEqual(snap["source"], "VictoriaMetrics (existing internal series)")
        checks = {item["id"]: item for item in snap["checks"]}
        self.assertEqual(set(checks), set(observer.DIAG_CHECKS)
                         if hasattr(observer, "DIAG_CHECKS")
                         else {item[0] for item in observer.PROBES
                               if item[0] != "telemetry.last" and not item[0].startswith("service.")})
        self.assertEqual({item["id"] for item in snap["serviceChecks"]},
                         {"service.pihole", "service.wireguard", "service.victoria"})
        self.assertEqual(checks["pi.scrape"]["state"], "observed")
        self.assertEqual(checks["pi.cpu"]["state"], "unknown")
        self.assertEqual(checks["vm.free"]["value"], 10 * (1024 ** 3))
        self.assertEqual(snap["telemetry"]["hosts"][0]["state"], "stale")
        self.assertNotIn("sensor", json.dumps(snap["telemetry"]["hosts"]))

    def test_only_fixed_get_summary_route(self):
        self.assertEqual(observer.Handler.do_PUT, observer.Handler.do_POST)
        self.assertEqual(observer.Handler.do_DELETE, observer.Handler.do_POST)


if __name__ == "__main__":
    unittest.main()
