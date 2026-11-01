"""Phase 2 computer management tests: no host agent, privileges or network needed."""
from __future__ import annotations
import importlib.util
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.client import HTTPConnection

ROOT=Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0,str(ROOT/"control/pcctl"))

spec=importlib.util.spec_from_file_location("test_computer_agent",ROOT/"control/pcinventory/agent.py")
agent=importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
spec=importlib.util.spec_from_file_location("test_computer_api",ROOT/"control/api/app.py")
api=importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def host(uid="gaming",address="192.168.50.12",mac="02:11:22:33:44:55"):
    return {"id":uid,"name":uid.title(),"address":address,
            "mac":mac,"broadcast":"192.168.50.255",
            "ssh_user":"pcctl","ssh_port":22}


class InventoryAgentTests(unittest.TestCase):
    def setUp(self):
        self.root=tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.home=Path(self.root.name)
        self.live=self.home/"hosts.json"
        self.state=self.home/"draft-data"
        self.state.mkdir(mode=0o700)
        self.live.write_text(json.dumps({"hosts":[host()]}))
        self.live.chmod(0o640)
        self.patcher=patch.multiple(agent,STATE_DIR=self.state,
                                    DRAFTS=self.state/"drafts.json",
                                    AUDIT=self.state/"audit.jsonl")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manager=agent.InventoryManager(self.live)

    def test_list_exposes_only_active_and_noncontrollable_drafts(self):
        data=self.manager.execute("homeowner","list",{})
        self.assertEqual(data["active"][0]["id"],"gaming")
        self.assertEqual(data["drafts"],[])
        self.assertIs(data["activationAvailable"],False)
        draft=host("living-room","192.168.50.44","02:11:22:33:44:56")
        self.manager.execute("homeowner","draft-create",draft)
        updated=self.manager.execute("homeowner","list",{})
        self.assertEqual([h["id"] for h in updated["active"]],["gaming"])
        self.assertEqual(updated["drafts"][0]["id"],"living-room")
        self.assertNotIn("living-room",self.live.read_text())
        # Only the canonical active inventory is available to the power agent.
        from core import load_hosts
        self.assertNotIn("living-room",load_hosts(str(self.live)))

    def test_duplicate_id_address_mac_are_denied(self):
        originals=self.live.read_bytes()
        for entry in [
            host("gaming","192.168.50.30","02:11:22:33:44:56"),
            host("other","192.168.50.12","02:11:22:33:44:57"),
            host("other","192.168.50.30","02:11:22:33:44:55"),
        ]:
            with self.subTest(entry=entry),self.assertRaises(agent.InventoryFault) as err:
                self.manager.execute("homeowner","draft-create",entry)
            self.assertEqual(err.exception.status,409)
        self.assertEqual(self.live.read_bytes(),originals)

    def test_invalid_draft_and_arbitrary_operation_denied(self):
        candidate=host("bad","8.8.8.8","02:11:22:33:44:58")
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","draft-create",candidate)
        self.assertEqual(err.exception.status,400)
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","shell",{"command":"shutdown"})
        self.assertEqual(err.exception.status,404)
        with self.assertRaises(agent.InventoryFault):
            self.manager.execute("homeowner","draft-create",{"path":"/etc/passwd"})

    def test_rename_preserves_stable_id_and_connection_details(self):
        state=self.manager.execute("homeowner","list",{})
        result=self.manager.execute("homeowner","rename",{
            "id":"gaming","name":"Gaming Desktop","revision":state["revision"]})
        self.assertNotEqual(result["revision"],state["revision"])
        active=self.manager.execute("homeowner","list",{})["active"][0]
        self.assertEqual(active["name"],"Gaming Desktop")
        self.assertEqual(active["id"],"gaming")
        self.assertEqual(active["address"],"192.168.50.12")
        self.assertEqual(active["mac"],"02:11:22:33:44:55")
        self.assertEqual(self.live.stat().st_mode&0o777,0o640)
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","rename",{
                "id":"gaming","name":"Stale change","revision":state["revision"]})
        self.assertEqual(err.exception.status,409)

    def test_draft_discard_requires_current_revision(self):
        entry=host("draft","192.168.50.18","02:11:22:33:44:59")
        created=self.manager.execute("homeowner","draft-create",entry)["draft"]
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","draft-discard",{"id":"draft","revision":"0"*24})
        self.assertEqual(err.exception.status,409)
        self.manager.execute("homeowner","draft-discard",
                             {"id":"draft","revision":created["revision"]})
        self.assertEqual(self.manager.execute("homeowner","list",{})["drafts"],[])
        self.assertEqual(len(json.loads(self.live.read_text())["hosts"]),1)

    def test_corrupted_draft_state_fails_closed(self):
        agent.DRAFTS.write_text("garbled")
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","list",{})
        self.assertEqual(err.exception.status,503)
        self.assertEqual(len(json.loads(self.live.read_text())["hosts"]),1)

    def test_failing_audit_prevents_mutation(self):
        with patch.object(agent,"_audit",side_effect=agent.InventoryFault(503,"audit down")):
            with self.assertRaises(agent.InventoryFault):
                self.manager.execute("homeowner","rename",{
                    "id":"gaming","name":"New name",
                    "revision":self.manager.execute("homeowner","list",{})["revision"]})
        self.assertEqual(json.loads(self.live.read_text())["hosts"][0]["name"],"Gaming")

    def test_socket_rpc_only_accepts_fixed_request(self):
        sockpath=str(self.home/"test.sock")
        agent.Handler.manager=self.manager
        server=agent.Server(sockpath,agent.Handler)
        self.addCleanup(server.server_close)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        self.addCleanup(server.shutdown)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(sockpath)
            conn.sendall(b'{"actor":"homeowner","operation":"list","payload":{}}\n')
            response=json.loads(conn.makefile("rb").readline(65536))
        self.assertTrue(response["ok"])
        self.assertEqual(response["result"]["active"][0]["id"],"gaming")
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.settimeout(3)
            conn.connect(sockpath)
            conn.sendall(b'{"actor":"homeowner","operation":"run-shell","payload":{}}\n')
            response=json.loads(conn.makefile("rb").readline(65536))
        self.assertFalse(response["ok"])
        self.assertEqual(response["status"],404)


class ComputerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=api.ThreadingHTTPServer(("127.0.0.1",0),api.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def request(self,verb,path,body=None,role="admins",origin=True):
        conn=HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        headers={"X-HomeAway-User":"homeowner","Origin":"https://homeaway.home.arpa:8443",
                 "Sec-Fetch-Site":"same-origin","X-HomeAway-Request":"1",
                 "Content-Type":"application/json"}
        if not origin: headers.pop("Origin")
        conn.request(verb,path,json.dumps(body) if body is not None else None,headers)
        resp=conn.getresponse()
        result=(resp.status,json.loads(resp.read()))
        conn.close()
        return result

    def config(self,enabled=True):
        return patch.dict(os.environ,{
            "HOMEAWAY_ACCOUNT_ADMIN_ENABLED":"true",
            "HOMEAWAY_COMPUTER_ADMIN_ENABLED":"true" if enabled else "false",
            "HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443",
            "HOMEAWAY_INVENTORY_SOCKET":"/run/homeaway-pcinventory/agent.sock",
        })

    def identity(self,role="admins"):
        def mock(actor,op,payload):
            if op=="resolve": return {"role":role}
            raise AssertionError("Account mutation should not be called")
        return mock

    def test_only_admin_sees_computer_manager(self):
        with self.config(),patch.object(api,"broker_request",
              side_effect=self.identity("viewers")),patch.object(api,"computer_request") as broker:
            code,data=self.request("GET","/control/api/me")
            self.assertEqual(code,200)
            self.assertFalse(data["capabilities"]["manageComputers"])
            code,_=self.request("GET","/control/api/admin/computers")
            self.assertEqual(code,403)
            code,_=self.request("POST","/control/api/admin/computers/draft-create",host("other"))
            self.assertEqual(code,403)
            broker.assert_not_called()

    def test_operator_cannot_rename(self):
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity("operators")),patch.object(api,"computer_request") as broker:
            code,_=self.request("POST","/control/api/admin/computers/rename",
                {"id":"gaming","name":"New","revision":"a"*24})
            self.assertEqual(code,403)
            broker.assert_not_called()

    def test_feature_is_off_by_default(self):
        with self.config(False),patch.object(api,"broker_request",
              side_effect=self.identity()),patch.object(api,"computer_request") as broker:
            code,data=self.request("GET","/control/api/me")
            self.assertFalse(data["capabilities"]["manageComputers"])
            code,_=self.request("GET","/control/api/admin/computers")
            self.assertEqual(code,404)
            code,_=self.request("POST","/control/api/admin/computers/rename",{})
            self.assertEqual(code,404)
            broker.assert_not_called()

    def test_missing_csrf_denied_without_writer(self):
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity()),patch.object(api,"computer_request") as broker:
            code,_=self.request("POST","/control/api/admin/computers/draft-create",
                                host("other"),origin=False)
            self.assertEqual(code,403)
            broker.assert_not_called()

    def test_admin_list_create_and_rename_use_fixed_operations(self):
        calls=[]
        def fake(actor,op,payload):
            calls.append((actor,op,payload))
            if op=="list":return {"active":[],"drafts":[],"revision":"abc"}
            return {"ok":True}
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity()),patch.object(api,"computer_request",side_effect=fake):
            code,data=self.request("GET","/control/api/me")
            self.assertTrue(data["capabilities"]["manageComputers"])
            code,data=self.request("GET","/control/api/admin/computers")
            self.assertEqual(code,200)
            self.assertEqual(data["active"],[])
            draft=host("other")
            code,_=self.request("POST","/control/api/admin/computers/draft-create",draft)
            self.assertEqual(code,200)
            self.assertEqual(calls[-1],("homeowner","draft-create",draft))
            code,_=self.request("POST","/control/api/admin/computers/rename",
                {"id":"gaming","name":"New name","revision":"b"*24})
            self.assertEqual(code,200)
            self.assertEqual(calls[-1][1],"rename")


if __name__=="__main__":
    unittest.main()
