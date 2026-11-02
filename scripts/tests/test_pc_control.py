"""No-hardware tests for extensible host inventory, WOL, RPC, auth and CSRF."""
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = load_module("pcctl_core_test", "control/pcctl/core.py")
api = load_module("homeaway_control_api_test", "control/api/app.py")
add = load_module("homeaway_add_pc_test", "scripts/add-pc.py")


def example(mac="aa:bb:cc:dd:ee:ff", id="gaming"):
    return {"id": id, "name": id.title() + " PC", "address": "192.168.1.88",
            "mac": mac, "broadcast": "192.168.1.255", "ssh_user": "pcctl", "ssh_port": 22}


class InventoryTests(unittest.TestCase):
    def test_data_driven_add_hosts_without_source_edits(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "hosts.json"
            one = example()
            two = {**example("11:22:33:44:55:66", "work"), "address": "192.168.1.173"}
            self.assertEqual(add.add_host(path, one), 1)
            self.assertEqual(add.add_host(path, two), 2)
            self.assertEqual(set(core.load_hosts(str(path))), {"gaming", "work"})
            with self.assertRaises(ValueError):
                add.add_host(path, two)

    def test_inventory_rejects_injections_and_public_ips(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "hosts.json"
            for changed in ({"id": "gaming;echo fail"}, {"address": "8.8.8.8"},
                            {"mac": "REPLACE_WITH_MAC"}, {"ssh_user": "root;whoami"}):
                bad = {**example(), **changed}
                path.write_text(json.dumps({"hosts": [bad]}))
                with self.assertRaises((ValueError, KeyError)):
                    core.load_hosts(str(path))

    def test_wol_magic_bytes_and_fixed_destination(self):
        host = example()
        sent = []
        class Fake:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def setsockopt(self, *_): pass
            def settimeout(self, *_): pass
            def sendto(self, packet, target): sent.append((packet, target))
        with patch.object(core.socket, "socket", return_value=Fake()):
            result = core.execute(host, "wake")
        self.assertTrue(result["ok"])
        self.assertEqual(sent[0][0], b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16)
        self.assertEqual(sent[0][1], ("192.168.1.255", 9))

    def test_no_shell_and_strict_host_key_verification(self):
        host = example()
        with patch.object(core.subprocess, "run") as run:
            run.return_value.returncode = 0
            result = core.execute(host, "shutdown")
        self.assertTrue(result["ok"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "shutdown")
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertEqual(run.call_args.kwargs["check"], False)
        self.assertNotIn("shell", run.call_args.kwargs)


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = api.ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(2)

    def request(self, method, path, content=None, extra=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = extra or {}
        body = json.dumps(content) if content is not None else None
        if body is not None:
            headers = {"Content-Type": "application/json", **headers}
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        status, body = response.status, response.read()
        conn.close()
        return status, json.loads(body) if body else {}

    def test_no_identity_denied(self):
        with patch.object(api, "apply_action") as mutate:
            code, _ = self.request("POST", "/control/api/actions", {"host":"gaming","action":"wake","confirm":""})
            self.assertEqual(code, 401)
            mutate.assert_not_called()

    def test_csrf_origin_and_confirmation(self):
        base = {"X-HomeAway-User":"homeowner", "X-HomeAway-Groups":"admins", "X-HomeAway-Request":"1"}
        with patch.dict(os.environ, {"HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443"}), patch.object(api, "apply_action") as mutate:
            payload = {"host":"gaming", "action":"shutdown", "confirm":"gaming"}
            code, _ = self.request("POST", "/control/api/actions", payload, base)
            self.assertEqual(code, 403)
            code, _ = self.request("POST", "/control/api/actions", {**payload,"confirm":""},
                                   {**base,"Origin":"https://homeaway.home.arpa:8443"})
            self.assertEqual(code, 422)
            code, _ = self.request("POST", "/control/api/actions", payload,
                                   {**base,"Origin":"https://other.example","Sec-Fetch-Site":"cross-site"})
            self.assertEqual(code, 403)
            mutate.assert_not_called()

    def test_valid_request_reaches_only_allowlisted_action(self):
        base = {"X-HomeAway-User":"homeowner", "X-HomeAway-Groups":"admins", "X-HomeAway-Request":"1",
                "Origin":"https://homeaway.home.arpa:8443", "Sec-Fetch-Site":"same-origin"}
        with patch.dict(os.environ, {"HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443"}), patch.object(api, "apply_action", return_value={"ok":True,"state":"requested","detail":"Wake sent"}) as mutate:
            code, result = self.request("POST", "/control/api/actions",
                                        {"host":"gaming", "action":"wake", "confirm":""}, base)
            self.assertEqual(code, 200)
            self.assertEqual(result["state"], "requested")
            mutate.assert_called_once_with("gaming", "wake", "homeowner")

    def test_roles_and_malformed_claims(self):
        self.assertEqual(api.verified_role("admins,operators"), "admins")
        self.assertEqual(api.verified_role(" operators, viewers "), "operators")
        for invalid in ("", "unknown", "admins;viewers", "admins\nX-Forged:evil", "admins," + "a"*520):
            self.assertIsNone(api.verified_role(invalid))
        self.assertFalse(api.can_command("viewers", "wake"))
        self.assertFalse(api.can_command("operators", "restart"))
        self.assertTrue(api.can_command("operators", "sleep"))

    def test_missing_group_claim_fails_closed(self):
        headers = {"X-HomeAway-User":"homeowner"}
        with patch.object(api, "apply_action") as mutate:
            code, _ = self.request("GET", "/control/api/me", extra=headers)
            self.assertEqual(code, 401)
            mutate.assert_not_called()

    def test_viewer_and_operator_actions_denied_without_rpc(self):
        base = {"X-HomeAway-User":"homeowner", "X-HomeAway-Request":"1",
                "Origin":"https://homeaway.home.arpa:8443", "Sec-Fetch-Site":"same-origin"}
        with patch.dict(os.environ, {"HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443"}), \
             patch.object(api, "apply_action") as mutate, patch.object(api, "record_denial"):
            for group, action, confirmation in (
                    ("viewers", "wake", ""), ("operators", "restart", "gaming"),
                    ("operators", "shutdown", "gaming")):
                code, result = self.request("POST", "/control/api/actions",
                    {"host":"gaming","action":action,"confirm":confirmation},
                    {**base,"X-HomeAway-Groups":group})
                self.assertEqual(code, 403, (group, action, result))
            mutate.assert_not_called()

    def test_configured_ip_is_admin_only_and_never_exposes_connection_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            inventory_path = Path(folder) / "hosts.json"
            host = example()
            inventory_path.write_text(json.dumps({"hosts": [host]}))
            with patch.object(api, "INVENTORY", inventory_path), \
                 patch.object(api, "rpc", return_value={"ok": True, "state": "online",
                                                        "detail": "reachable"}):
                admin = {"X-HomeAway-User":"homeowner", "X-HomeAway-Groups":"admins"}
                member = {"X-HomeAway-User":"member", "X-HomeAway-Groups":"operators"}
                code, result = self.request("GET", "/control/api/hosts", extra=admin)
                self.assertEqual(code, 200)
                self.assertEqual(result["hosts"][0]["address"], "192.168.1.88")
                self.assertEqual(result["hosts"][0]["state"], "online")
                for private_field in ("mac", "broadcast", "ssh_user", "ssh_port"):
                    self.assertNotIn(private_field, result["hosts"][0])
                code, result = self.request("GET", "/control/api/hosts", extra=member)
                self.assertEqual(code, 200)
                self.assertNotIn("address", result["hosts"][0])
                # Do not echo malformed or public addresses even to admins.
                host["address"] = "8.8.8.8"
                inventory_path.write_text(json.dumps({"hosts": [host]}))
                code, result = self.request("GET", "/control/api/hosts", extra=admin)
                self.assertEqual(code, 200)
                self.assertNotIn("address", result["hosts"][0])

    def test_operator_status_allowed_but_history_hidden(self):
        with patch.object(api, "inventory", return_value=[]), patch.object(api, "record_denial"):
            head = {"X-HomeAway-User":"member","X-HomeAway-Groups":"operators"}
            code, data = self.request("GET", "/control/api/me", extra=head)
            self.assertEqual(code, 200)
            self.assertEqual(data["capabilities"]["actions"], ["wake","sleep"])
            self.assertFalse(data["capabilities"]["history"])
            code, data = self.request("GET", "/control/api/hosts", extra=head)
            self.assertEqual(code, 200)
            self.assertEqual(data["hosts"], [])
            code, _ = self.request("GET", "/control/api/history", extra=head)
            self.assertEqual(code, 403)

    def test_request_rejects_arbitrary_commands(self):
        data = {"host":"gaming", "action":"rm -rf /", "confirm":""}
        base = {"X-HomeAway-User":"homeowner", "X-HomeAway-Groups":"admins",
                "X-HomeAway-Request":"1", "Origin":"https://homeaway.home.arpa:8443"}
        with patch.dict(os.environ, {"HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443"}), patch.object(api, "rpc") as sender:
            code, _ = self.request("POST", "/control/api/actions", data, base)
            self.assertEqual(code, 400)
            sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
