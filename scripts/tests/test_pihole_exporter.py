"""Pi-hole v6 authentication, retry and Prometheus aggregate telemetry tests."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from monitoring.pihole_exporter.app import PiHoleV6Client, render_metrics


SUMMARY = {
    "queries": {"total": 100, "blocked": 23, "percent_blocked": 23.0,
                "cached": 30, "forwarded": 47, "frequency": 1.5,
                "unique_domains": 67, "types": {"A": 70, "AAAA": 30}},
    "clients": {"active": 3, "total": 9},
    "gravity": {"domains_being_blocked": 87500},
}


class FakeResponse:
    def __init__(self, data):
        self.payload = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size=-1):
        return self.payload


class ExporterTests(unittest.TestCase):
    def test_metrics_are_aggregated_without_query_logs(self):
        result = render_metrics(SUMMARY, {"blocking": "enabled", "timer": None})
        self.assertIn("homeaway_pihole_queries_window 100", result)
        self.assertIn("homeaway_pihole_blocked_percent 23.0", result)
        self.assertIn('homeaway_pihole_query_type_window{type="AAAA"} 30', result)
        self.assertIn("homeaway_pihole_blocking_enabled 1", result)
        self.assertNotIn("domain_name", result)
        self.assertNotIn("client_ip", result)
        self.assertNotIn("password", result)

    def test_invalid_values_never_render(self):
        for value in (-5, float("nan"), float("inf"), True):
            sample = dict(SUMMARY)
            sample["queries"] = dict(SUMMARY["queries"], total=value)
            with self.assertRaises(ValueError):
                render_metrics(sample, {"blocking": True})
        for bad in (True, False, "failed", "unknown", "ENABLED", None):
            with self.subTest(blocking=bad), self.assertRaises(ValueError):
                render_metrics(SUMMARY, {"blocking": bad})

    def test_v6_blocking_state_strings(self):
        # The real GET /api/dns/blocking response is a string; it must never
        # be confused with the boolean accepted when POSTing a state change.
        for state, expected in (("enabled", 1), ("disabled", 0)):
            with self.subTest(state=state):
                output = render_metrics(SUMMARY, {"blocking": state, "timer": None, "took": 0.00004})
                self.assertIn(f"homeaway_pihole_blocking_enabled {expected}\\n", output)
                self.assertIn("homeaway_pihole_queries_window 100", output)

    def test_app_session_reauth_and_no_secret_in_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            pw = Path(tmp) / "monitor-password"
            pw.write_text("a-private-test-password\n")
            calls = []
            state = {"login": 0, "fail_once": True}
            def opener(req, timeout=None):
                path = req.full_url.split("http://pihole")[1]
                calls.append((req.get_method(), path))
                if req.get_method() == "POST":
                    self.assertEqual(path, "/api/auth")
                    self.assertIn(b"a-private-test-password", req.data)
                    state["login"] += 1
                    return FakeResponse({"session": {"valid": True, "sid": "session-" + str(state["login"])}})
                self.assertEqual(req.get_header("X-ftl-sid"), "session-" + str(state["login"]))
                if path == "/api/stats/summary":
                    if state["fail_once"]:
                        state["fail_once"] = False
                        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", None, None)
                    return FakeResponse(SUMMARY)
                if path == "/api/dns/blocking":
                    return FakeResponse({"blocking": "enabled", "timer": None})
                self.fail("Unexpected API endpoint: " + path)
            client = PiHoleV6Client("http://pihole", pw, opener=opener)
            text = client.snapshot()
            self.assertEqual(state["login"], 2)
            self.assertEqual(calls.count(("GET", "/api/stats/summary")), 2)
            self.assertIn("homeaway_pihole_blocked_window 23", text)
            self.assertNotIn("a-private-test-password", text)
            self.assertNotIn("session-", text)
            client.snapshot()
            self.assertEqual(state["login"], 2, "Session should be reused until expired")


if __name__ == "__main__":
    unittest.main()
