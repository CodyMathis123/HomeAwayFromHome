import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from ingest.app import Handler, valid_payload, token_ok


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




class IngestHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.expected_token = "a" * 64
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, token="", body=b""):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": "Bearer " + token} if token else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status
        response.read()
        connection.close()
        return result

    def test_rejects_unauthorized_and_wrong_paths(self):
        body = b"sensordash_reading,host=PC,sensor=cpu,kind=Temperature value=41 1793450400000"
        with patch("ingest.app.forward") as mock:
            self.assertEqual(self.request("POST", "/ingest", body=body), 401)
            self.assertEqual(self.request("POST", "/write", token="a" * 64, body=body), 404)
            self.assertEqual(self.request("GET", "/ingest", token="a" * 64), 405)
            mock.assert_not_called()

    def test_authenticated_valid_and_invalid_body(self):
        valid = b"sensordash_reading,host=PC,sensor=cpu,kind=Temperature value=41 1793450400000"
        with patch("ingest.app.forward", return_value=204) as mock:
            self.assertEqual(self.request("POST", "/ingest", token="a" * 64, body=valid), 204)
            mock.assert_called_once_with(valid)
        with patch("ingest.app.forward") as mock:
            self.assertEqual(self.request("POST", "/ingest", token="a" * 64, body=b"not metrics"), 422)
            mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
