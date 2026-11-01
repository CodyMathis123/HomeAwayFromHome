"""Read-only certificate diagnostics contract against synthetic X.509 leaves."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "certificates_for_tests", ROOT / "monitoring/diagnostics/certificates.py")
certs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(certs)


def leaf(sans, expires_days=60):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HomeAway Test CA")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=expires_days))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName(s) for s in sans]), critical=False)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.DER)


class CertificateContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.der = leaf(["homeaway.home.arpa", "*.homeawayfromhome.house"])
        cls.expiring = leaf(["homeaway.home.arpa"], expires_days=9)

    def test_fixed_endpoints_and_restricted_sni(self):
        with patch.dict(os.environ, {
            "HOMEAWAY_DOMAIN": "homeaway.home.arpa",
            "HOMEAWAY_AUTH_DOMAIN": "auth.homeaway.home.arpa",
            "HOMEAWAY_METRICS_DOMAIN": "metrics.homeaway.home.arpa"}):
            rows = certs.targets()
        self.assertEqual([item[0] for item in rows], list(certs.CERT_IDS))
        self.assertEqual({target for _, target, _ in rows}, {
            ("gateway", 8443), ("telemetry-ingest", 8443)})
        self.assertEqual(rows[0][2], "homeaway.home.arpa")
        self.assertIsNone(rows[-1][2])
        self.assertIsNone(certs.safe_hostname("192.168.1.15"))
        self.assertIsNone(certs.safe_hostname("../etc/passwd"))
        self.assertIsNone(certs.safe_hostname("wrong host\npassword"))

    def test_internal_ca_not_publicly_trusted_is_observed_not_outage(self):
        with patch.object(certs, "get_leaf", return_value=self.der), \
                patch.object(certs, "system_trust", return_value="not_trusted"):
            item = certs.observe("browser-home", certs.GATEWAY_TARGET,
                                 "homeaway.home.arpa", datetime.now(timezone.utc).timestamp())
        self.assertEqual(item["state"], "observed")
        self.assertEqual(item["hostnameMatch"], True)
        self.assertEqual(item["publicTrust"], "not_trusted")
        self.assertGreater(item["daysRemaining"], 40)
        self.assertIn("HomeAway Test CA", item["issuer"])
        self.assertIn("homeaway.home.arpa", item["subjectAltNames"])

    def test_expiry_and_hostname_mismatch_are_attention(self):
        now = datetime.now(timezone.utc).timestamp()
        with patch.object(certs, "get_leaf", return_value=self.expiring), \
                patch.object(certs, "system_trust", return_value="not_trusted"):
            soon = certs.observe("browser-home", certs.GATEWAY_TARGET,
                                 "homeaway.home.arpa", now)
            mismatch = certs.observe("browser-auth", certs.GATEWAY_TARGET,
                                     "auth.homeaway.home.arpa", now)
        self.assertEqual(soon["state"], "attention")
        self.assertLess(soon["daysRemaining"], 14)
        self.assertEqual(mismatch["hostnameMatch"], False)
        self.assertEqual(mismatch["state"], "attention")

    def test_telemetry_certificate_keeps_ca_trust_unverified(self):
        with patch.object(certs, "get_leaf", return_value=self.der), \
                patch.object(certs, "system_trust") as trust:
            item = certs.observe("telemetry-receiver", certs.TELEMETRY_TARGET,
                                 None, datetime.now(timezone.utc).timestamp())
        self.assertEqual(item["state"], "observed")
        self.assertIsNone(item["hostnameMatch"])
        self.assertEqual(item["publicTrust"], "not_checked")
        trust.assert_not_called()

    def test_missing_certificate_does_not_claim_expiry(self):
        with patch.object(certs, "get_leaf", side_effect=TimeoutError()):
            item = certs.observe("browser-home", certs.GATEWAY_TARGET,
                                 "homeaway.home.arpa", datetime.now(timezone.utc).timestamp())
        self.assertEqual(item["state"], "unknown")
        self.assertIsNone(item["daysRemaining"])
        self.assertIsNone(item["issuer"])


if __name__ == "__main__":
    unittest.main()
