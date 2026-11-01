"""Certificates proxy remains fixed-target and admin-only."""
import importlib.util
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "pc_control_certificates_for_tests", ROOT / "control/api/app.py")
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)


def sample():
    items = []
    for id in sorted(control.CERT_IDS):
        items.append({
            "id": id, "state": "observed", "daysRemaining": 62.5,
            "notAfter": "2027-01-01T16:30:00+00:00",
            "issuer": "CN=HomeAway Test CA",
            "subjectAltNames": ["homeaway.home.arpa"],
            "hostnameMatch": None if id == "telemetry-receiver" else True,
            "publicTrust": "not_checked" if id == "telemetry-receiver" else "not_trusted",
            "detail": "Leaf certificate observed",
            "privateKey": "DO-NOT-RELAY",
        })
    return {
        "schemaVersion": 1, "checkedAt": "2026-11-01T16:30:00+00:00",
        "certificates": items, "internalURL": "DO-NOT-RELAY"
    }


class CertificatesProxyContracts(unittest.TestCase):
    def fetch(self, raw):
        def fake_urlopen(request, timeout):
            self.assertEqual(request.full_url, control.CERT_DIAG_URL)
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(timeout, 14)
            return io.BytesIO(raw)
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": control.DIAG_URL}), \
                patch.object(control, "urlopen", side_effect=fake_urlopen):
            return control.certificates_snapshot()

    def test_fixed_target_and_metadata_whitelist(self):
        output = self.fetch(json.dumps(sample()).encode())
        self.assertEqual(len(output["certificates"]), 4)
        self.assertNotIn("DO-NOT-RELAY", json.dumps(output))
        self.assertEqual(output["certificates"][0]["issuer"], "CN=HomeAway Test CA")
        self.assertEqual(output["source"],
                         "Observed leaf certificate via fixed private TLS connections")

    def test_rejects_bad_host_name_and_oversized_data(self):
        scenarios = [
            {"schemaVersion": 2}, {"certificates": []}, [1, 2],
            dict(sample(), certificates=[dict(sample()["certificates"][0],
                 subjectAltNames=["\nNEVER"] * 25)] * 4),
        ]
        for raw in [json.dumps(x).encode() for x in scenarios] + [b"x" * 32769]:
            with self.subTest(length=len(raw)):
                with self.assertRaises(control.BrokerError):
                    self.fetch(raw)
        with patch.dict(os.environ, {"HOMEAWAY_DIAGNOSTICS_URL": "http://wrong.invalid"}):
            with self.assertRaises(control.BrokerError):
                control.certificates_snapshot()

    def test_route_requires_admin(self):
        # Route itself verifies the role before invoking the certificate proxy.
        source = (ROOT / "control/api/app.py").read_text()
        self.assertIn('if path == "/control/api/admin/certificates":', source)
        section = source.split('if path == "/control/api/admin/certificates":')[1].split(
            'if path == "/control/api/admin/users":')[0]
        self.assertIn('if role != "admins":', section)
        self.assertIn("certificates_snapshot()", section)
        self.assertIn('self._json(403', section)


if __name__ == "__main__":
    unittest.main()
