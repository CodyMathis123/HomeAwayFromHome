import unittest
from ingest.app import valid_payload, token_ok


class IngestTests(unittest.TestCase):
    def test_authorized_header(self):
        token = "a" * 64
        self.assertTrue(token_ok("Bearer " + token, token))
        self.assertFalse(token_ok("Bearer " + "b" * 64, token))
        self.assertFalse(token_ok("Basic " + token, token))
        self.assertFalse(token_ok(None, token))

    def test_raw_sensor_line(self):
        self.assertTrue(valid_payload(
            b"sensordash_reading,host=DESKTOP,sensor=/amdcpu/0/temperature/0,kind=Temperature value=61.75 1793450400000\n"
        ))
        self.assertTrue(valid_payload(
            b"sensordash_reading,host=PC,sensor=/gpu/0/control/0,kind=Control value=0 1793450400000\n"
        ))

    def test_reject_injection_and_oversize(self):
        self.assertFalse(valid_payload(b"other,host=PC value=1 1793450400000"))
        self.assertFalse(valid_payload(
            b"sensordash_reading,host=PC,sensor=something,kind=Temperature value=NaN 1793450400000"
        ))
        self.assertFalse(valid_payload(
            b"sensordash_reading,host=PC,sensor=something,kind=Temperature value=2 1793450400000\nattack!"
        ))
        self.assertFalse(valid_payload(b"a" * (256 * 1024 + 1)))
        self.assertFalse(valid_payload(b""))


if __name__ == "__main__":
    unittest.main()
