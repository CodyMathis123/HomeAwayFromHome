"""Administrator-only metrics diagnostics proxy: strict response and auth."""
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "pc_control_metrics_for_tests", ROOT / "control/api/app.py")
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


def response():
    return {
        "schemaVersion": 1,
        "checkedAt": "2026-11-01T15:00:00+00:00",
        "source": "never expose this untrusted source",
        "checks": [
            {"id": key, "label": key, "state": "observed", "value": 1,
             "unit": "boolean" if key == "pi.scrape" else
             "bytes" if key == "vm.free" else
             "load" if key == "pi.load" else
             "celsius" if key == "pi.thermal" else "percent",
             "detail": "Measured sample", "unexpectedSecret": "never pass this through"}
            for key in sorted(control.DIAG_CHECKS)
        ],
        "serviceChecks": [
            {"id": key, "label": key, "state": "observed", "value": 1,
             "unit": "boolean", "detail": "Synthetic scrape", "unexpectedSecret": "redacted"}
            for key in sorted(control.SERVICE_DIAG_CHECKS)
        ],
        "telemetry": {"state": "observed", "hosts": [
            {"host": "main-desktop", "ageSeconds": 72,
             "lastObservedAt": "2026-11-01T14:58:48+00:00", "state": "observed",
             "privateInfo": "do not copy"}]}
    }


class ProxyContracts(unittest.TestCase):
    def test_fixed_target_and_response_redaction(self):
        def fake_open(request, timeout):
            self.assertEqual(timeout, 7)
            self.assertEqual(request.full_url, control.DIAG_URL)
            self.assertEqual(request.get_method(), "GET")
            return io.BytesIO(json.dumps(response()).encode())
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": control.DIAG_URL}), \
                patch.object(control, "urlopen", side_effect=fake_open):
            result = control.diagnostics_snapshot()
        self.assertEqual(len(result["checks"]), 6)
        self.assertNotIn("unexpectedSecret", json.dumps(result))
        self.assertNotIn("privateInfo", json.dumps(result))
        self.assertEqual(result["telemetry"]["hosts"][0]["host"], "main-desktop")
        self.assertEqual(result["source"], "Internal VictoriaMetrics instant samples")

    def test_rejects_wrong_destination_and_untrusted_host_labels(self):
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": "http://attacker.invalid"}, clear=False):
            with self.assertRaises(control.BrokerError) as caught:
                control.diagnostics_snapshot()
        self.assertEqual(caught.exception.status, 503)
        bad = response()
        bad["telemetry"]["hosts"][0]["host"] = "main-desktop\nsecrets"
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": control.DIAG_URL}), \
                patch.object(control, "urlopen",
                             return_value=io.BytesIO(json.dumps(bad).encode())):
            with self.assertRaises(control.BrokerError):
                control.diagnostics_snapshot()

    def test_rejects_oversized_invalid_and_duplicate_probe_data(self):
        bad = response()
        bad["checks"][0]["id"] = bad["checks"][1]["id"]
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": control.DIAG_URL}):
            for data in (bad, {"schemaVersion": 2}, [1, 2]):
                with self.subTest(payload=data), \
                     patch.object(control, "urlopen", return_value=io.BytesIO(json.dumps(data).encode())):
                    with self.assertRaises(control.BrokerError):
                        control.diagnostics_snapshot()
            with patch.object(control, "urlopen", return_value=io.BytesIO(b"A" * 32769)):
                with self.assertRaises(control.BrokerError):
                    control.diagnostics_snapshot()


class AuthenticatedRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, role=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {}
        if role:
            headers.update({"X-HomeAway-User": "test",
                            "X-HomeAway-Groups": role})
        connection.request("GET", "/control/api/admin/diagnostics", headers=headers)
        res = connection.getresponse()
        code, body = res.status, res.read()
        connection.close()
        return code, json.loads(body)

    def test_admin_only_route_and_fail_closed_observer(self):
        with patch.object(control, "account_admin_enabled", return_value=False), \
                patch.object(control, "diagnostics_snapshot", return_value=response()) as snap:
            code, _ = self.request()
            self.assertEqual(code, 401)
            code, _ = self.request("viewers")
            self.assertEqual(code, 403)
            self.assertEqual(snap.call_count, 0)
            code, value = self.request("admins")
            self.assertEqual(code, 200)
            self.assertEqual(value["schemaVersion"], 1)
            self.assertEqual(snap.call_count, 1)
        with patch.object(control, "account_admin_enabled", return_value=False), \
                patch.object(control, "diagnostics_snapshot",
                             side_effect=control.BrokerError(503, "Metrics diagnostics unavailable")):
            code, body = self.request("admins")
            self.assertEqual(code, 503)
            self.assertEqual(body["error"], "Metrics diagnostics unavailable")


if __name__ == "__main__":
    unittest.main()
