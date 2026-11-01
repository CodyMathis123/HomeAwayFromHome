import http.client
import json
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from portal.app import Handler, ThreadingHTTPServer, dns_probe, tcp_probe


class DnsTests(unittest.TestCase):
    def test_successful_dns_packet(self):
        response = struct.pack("!HHHHHH", 123, 0x8180, 1, 1, 0, 0) + b"\x00" * 18

        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def settimeout(self, _): pass
            def sendto(self, data, address):
                self.sent = data
                self.address = address
            def recvfrom(self, _):
                return response, ("192.168.50.10", 53)

        with patch("portal.app.secrets.randbits", return_value=123), patch(
            "portal.app.socket.socket", return_value=FakeSocket()
        ):
            ok, message = dns_probe("pihole")
        self.assertTrue(ok)
        self.assertIn("succeeded", message)

    def test_mismatched_dns_id_rejected(self):
        response = struct.pack("!HHHHHH", 124, 0x8180, 1, 1, 0, 0)

        class FakeSocket:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def settimeout(self, _): pass
            def sendto(self, *_): pass
            def recvfrom(self, _): return response, ("127.0.0.1", 53)

        with patch("portal.app.secrets.randbits", return_value=123), patch(
            "portal.app.socket.socket", return_value=FakeSocket()
        ):
            self.assertFalse(dns_probe("pihole")[0])

    def test_tcp_failure_is_not_claimed_healthy(self):
        with patch("portal.app.socket.create_connection", side_effect=OSError):
            self.assertFalse(tcp_probe("wg-easy", 51821)[0])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path):
        con = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        con.request(method, path)
        response = con.getresponse()
        code, headers, data = response.status, response.headers, response.read()
        con.close()
        return code, headers, data

    def test_read_only_and_headers(self):
        status, headers, data = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"HomeAway", data)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        status, _, _ = self.request("POST", "/api/status")
        self.assertEqual(status, 405)
        status, _, _ = self.request("PUT", "/api/status")
        self.assertEqual(status, 405)
        status, _, _ = self.request("GET", "/.env")
        self.assertEqual(status, 404)

    def test_status_is_accurately_scoped(self):
        sample = {"checkedAt": "2026-10-31T12:00:00+00:00", "checks": [
            {"id": "dns", "ok": True, "detail": "DNS lookup succeeded"},
            {"id": "wireguard", "ok": False, "detail": "Admin listener unavailable"},
        ]}
        with patch("portal.app.snapshot", return_value=sample):
            status, headers, payload = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), sample)
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
