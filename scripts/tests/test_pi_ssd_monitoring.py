"""SSD SMART collector tests: real firmware-style data, missing data, isolation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "pi_ssd", ROOT / "monitoring/pi_ssd/collector.py")
pi_ssd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pi_ssd)

def fixture(passed=True, status=0):
    def row(i, value, normalized=100, raw_text=None):
        return {"id": i, "value": normalized,
                "raw": {"value": value, "string": raw_text or str(value)}}
    return {
        "smartctl": {"exit_status": status},
        "smart_status": {"passed": passed},
        "serial_number": "DO-NOT-PUBLISH",
        "ata_smart_attributes": {"table": [
            row(5, 3), row(9, 0, raw_text="8628h+52m+49.240s"),
            row(171, 1), row(172, 0), row(174, 1566),
            row(187, 0), row(194, 26, raw_text="26 (Min/Max 6/48)"),
            row(196, 3), row(231, 0), row(241, 7410), row(242, 10579),
        ]}
    }

class PiSsdCollectorTests(unittest.TestCase):
    def test_sandforce_raw_vs_normalized_and_private_data(self):
        m = pi_ssd.parse_smart(fixture())
        self.assertEqual(m["homeaway_pi_ssd_temperature_celsius"], 26)
        self.assertEqual(m["homeaway_pi_ssd_retired_blocks"], 3)
        self.assertEqual(m["homeaway_pi_ssd_life_normalized"], 100)
        self.assertEqual(m["homeaway_pi_ssd_power_on_hours"], 8628)
        self.assertEqual(m["homeaway_pi_ssd_lifetime_writes_gib"], 7410)
        self.assertNotIn("serial", pi_ssd.render(m).lower())
        self.assertNotIn("DO-NOT-PUBLISH", pi_ssd.render(m))
        self.assertNotIn("life_percent", pi_ssd.render(m))

    def test_failed_smart_is_reported_as_unhealthy_not_missing(self):
        # smartctl bit 3 reports a failing drive, but does not mean query failed.
        m = pi_ssd.parse_smart(fixture(passed=False, status=8))
        self.assertEqual(m["homeaway_pi_ssd_smart_passed"], 0)

    def test_unreadable_smart_does_not_imply_health(self):
        with self.assertRaises(ValueError):
            pi_ssd.parse_smart({"smartctl": {"exit_status": 2}})
        with self.assertRaises(ValueError):
            pi_ssd.parse_smart({"smartctl": {"exit_status": 0}})
        with patch.object(pi_ssd, "disk_for_root", side_effect=RuntimeError("missing")):
            with patch.object(pi_ssd, "kernel_metrics", return_value={}):
                with patch.object(pi_ssd, "filesystem_metrics", return_value={}):
                    m = pi_ssd.collect()
        self.assertEqual(m["homeaway_pi_ssd_collection_success"], 0)
        self.assertNotIn("homeaway_pi_ssd_smart_passed", m)

    def test_kernel_counts_are_since_boot_not_rates(self):
        lines = ("uas_eh_abort_handler x\nreset SuperSpeed USB device number 2\n"
                 "I/O error, dev sda, sector 1\nHC died; cleaning up\n"
                 "usb 2-2: USB disconnect, device number 2\n")
        with patch.object(pi_ssd, "command", return_value=types.SimpleNamespace(
                returncode=0, stdout=lines)):
            m = pi_ssd.kernel_metrics()
        for key in pi_ssd.EVENTS:
            self.assertEqual(m['homeaway_pi_ssd_kernel_events_since_boot{kind="' + key + '"}'], 1)

    def test_atomic_textfile_and_no_sensitive_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pi-ssd.prom"
            pi_ssd.publish(path, pi_ssd.render(pi_ssd.parse_smart(fixture())))
            self.assertIn("homeaway_pi_ssd_retired_blocks 3", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(list(Path(temp).glob(".homeaway-ssd-*")), [])

    def test_grafana_never_treats_stale_smart_file_as_live(self):
        dashboard = json.loads((ROOT / "grafana/dashboards/storage.json").read_text())
        by_id = {panel["id"]: panel for panel in dashboard["panels"]}
        for panel_id in (106, 107, 108, 109, 110, 112, 113, 114, 115):
            for target in by_id[panel_id]["targets"]:
                self.assertIn("homeaway_pi_ssd_last_attempt_timestamp_seconds",
                              target["expr"], panel_id)
                self.assertIn("< 900", target["expr"], panel_id)
        self.assertIn("homeaway_pi_ssd_collection_success",
                      by_id[107]["targets"][0]["expr"])

    def test_static_security_and_scrape_contract(self):
        compose = (ROOT / "compose.yaml").read_text()
        self.assertIn("homeaway-ssd-metrics:ro", compose)
        self.assertIn("--collector.textfile", compose)
        self.assertIn("--collector.disable-defaults", compose)
        self.assertNotIn("--collector.filesystem", compose)
        self.assertIn("ReadWritePaths=/var/lib/homeaway-ssd-metrics",
                      (ROOT / "systemd/homeaway-pi-ssd.service").read_text())
        self.assertIn("OnUnitActiveSec=5min",
                      (ROOT / "systemd/homeaway-pi-ssd.timer").read_text())

if __name__ == "__main__":
    unittest.main()
