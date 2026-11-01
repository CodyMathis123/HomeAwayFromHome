"""Phase 2 computer management tests: no host agent, privileges or network needed."""
from __future__ import annotations
import importlib.util
import io
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
                                    RETIRED=self.state/"retired.json",
                                    AUDIT=self.state/"audit.jsonl")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.manager=agent.InventoryManager(self.live)

    def test_list_exposes_only_active_and_noncontrollable_drafts(self):
        data=self.manager.execute("homeowner","list",{})
        self.assertEqual(data["active"][0]["id"],"gaming")
        self.assertEqual(data["drafts"],[])
        self.assertIs(data["activationAvailable"],True)
        draft=host("living-room","192.168.50.44","02:11:22:33:44:56")
        self.manager.execute("homeowner","draft-create",draft)
        updated=self.manager.execute("homeowner","list",{})
        self.assertEqual([h["id"] for h in updated["active"]],["gaming"])
        self.assertEqual(updated["drafts"][0]["id"],"living-room")
        self.assertNotIn("living-room",self.live.read_text())
        # Only the canonical active inventory is available to the power agent.
        from core import load_hosts
        self.assertNotIn("living-room",load_hosts(str(self.live)))

    def test_verification_checks_draft_but_never_activates(self):
        entry=host("living-room","192.168.50.44","02:11:22:33:44:56")
        created=self.manager.execute("homeowner","draft-create",entry)["draft"]
        original=self.live.read_bytes()
        with patch.object(agent,"_probe",return_value={
            "verified":True,"code":"ssh_key_and_status_verified"}) as verify:
            result=self.manager.execute("homeowner","verify",{
                "id":entry["id"],"revision":created["revision"]})
        self.assertTrue(result["verified"])
        verify.assert_called_once_with(entry)
        self.assertEqual(self.live.read_bytes(),original)

    def test_activation_requires_fresh_key_and_authenticated_ssh(self):
        entry=host("living-room","192.168.50.44","02:11:22:33:44:56")
        draft=self.manager.execute("homeowner","draft-create",entry)["draft"]
        revision=self.manager.execute("homeowner","list",{})["revision"]
        args={"id":entry["id"],"revision":draft["revision"],
              "inventoryRevision":revision,"confirm":entry["id"]}
        with patch.object(agent,"_probe",return_value={
            "verified":False,"code":"host_key_not_pinned"}):
            with self.assertRaises(agent.InventoryFault) as denied:
                self.manager.execute("homeowner","activate",args)
            self.assertEqual(denied.exception.status,409)
        self.assertEqual(len(json.loads(self.live.read_text())["hosts"]),1)
        with patch.object(agent,"_probe",return_value={
            "verified":True,"code":"ssh_key_and_status_verified"}) as check:
            activated=self.manager.execute("homeowner","activate",args)
        self.assertEqual(check.call_count,1)
        self.assertEqual(activated["activated"],entry["id"])
        self.assertFalse(activated["cleanupPending"])
        self.assertEqual(set(x["id"] for x in json.loads(self.live.read_text())["hosts"]),
                         {"gaming","living-room"})
        self.assertEqual(self.manager.execute("homeowner","list",{})["drafts"],[])
        with patch.object(agent,"_probe") as check:
            with self.assertRaises(agent.InventoryFault):
                self.manager.execute("homeowner","activate",args)
            check.assert_not_called()

    def test_activate_denies_bad_confirmation_and_stale_inventory_without_probe(self):
        entry=host("living-room","192.168.50.44","02:11:22:33:44:56")
        draft=self.manager.execute("homeowner","draft-create",entry)["draft"]
        prior=self.manager.execute("homeowner","list",{})["revision"]
        original=self.live.read_bytes()
        base={"id":entry["id"],"revision":draft["revision"],
              "inventoryRevision":prior,"confirm":"wrong"}
        with patch.object(agent,"_probe") as verify:
            with self.assertRaises(agent.InventoryFault) as err:
                self.manager.execute("homeowner","activate",base)
            self.assertEqual(err.exception.status,400)
            agent.update_inventory(self.live,lambda d:{
                "hosts":d["hosts"]+[host("other","192.168.50.45","02:11:22:33:44:57")]})
            with self.assertRaises(agent.InventoryFault) as err:
                self.manager.execute("homeowner","activate",{
                    **base,"confirm":entry["id"]})
            self.assertEqual(err.exception.status,409)
            verify.assert_not_called()
        self.assertNotIn("living-room",self.live.read_text())

    def test_verification_denials_do_not_modify_active_inventory(self):
        entry=host("living-room","192.168.50.44","02:11:22:33:44:56")
        draft=self.manager.execute("homeowner","draft-create",entry)["draft"]
        original=self.live.read_bytes()
        with patch.object(agent,"_probe",return_value={
            "verified":False,"code":"ssh_auth_or_status_failed"}):
            result=self.manager.execute("homeowner","verify",{
                "id":entry["id"],"revision":draft["revision"]})
        self.assertFalse(result["verified"])
        self.assertEqual(self.live.read_bytes(),original)
        with patch.object(agent,"_probe",side_effect=agent.InventoryFault(503,"SSH verifier unavailable")):
            with self.assertRaises(agent.InventoryFault) as err:
                self.manager.execute("homeowner","activate",{
                    "id":entry["id"],"revision":draft["revision"],
                    "inventoryRevision":self.manager.execute("homeowner","list",{})["revision"],
                    "confirm":entry["id"]})
            self.assertEqual(err.exception.status,503)
        self.assertEqual(self.live.read_bytes(),original)

    def test_activated_but_uncleaned_draft_is_visible_and_discardable(self):
        entry=host("living-room","192.168.50.44","02:11:22:33:44:56")
        draft=self.manager.execute("homeowner","draft-create",entry)["draft"]
        args={"id":entry["id"],"revision":draft["revision"],
              "inventoryRevision":self.manager.execute("homeowner","list",{})["revision"],
              "confirm":entry["id"]}
        real_save=agent._save_drafts
        with patch.object(agent,"_probe",return_value={
            "verified":True,"code":"ssh_key_and_status_verified"}),\
             patch.object(agent,"_save_drafts",side_effect=agent.InventoryFault(503,"save failed")):
            activated=self.manager.execute("homeowner","activate",args)
        self.assertTrue(activated["cleanupPending"])
        listed=self.manager.execute("homeowner","list",{})
        self.assertTrue(listed["drafts"][0]["cleanupPending"])
        self.manager.execute("homeowner","draft-discard",{
            "id":entry["id"],"revision":draft["revision"]})
        self.assertEqual(len(self.manager.execute("homeowner","list",{})["active"]),2)

    def disable(self, computer="gaming"):
        current = self.manager.execute("homeowner", "list", {})
        return self.manager.execute("homeowner", "disable", {
            "id": computer, "revision": current["revision"], "confirm": computer})

    def test_disable_restore_legacy_host_with_omitted_ssh_defaults(self):
        # The legacy power-control inventory accepts omitted SSH defaults.
        # The private retirement archive must store a canonical recovery
        # copy, while Disable never rewrites an active host before removal.
        legacy = {k: v for k, v in host().items()
                  if k not in ("ssh_user", "ssh_port")}
        self.live.write_text(json.dumps({"hosts": [legacy]}))
        old = self.live.read_bytes()
        data = self.manager.execute("homeowner", "list", {})
        self.assertNotIn("ssh_user", data["active"][0])
        self.assertEqual(old, self.live.read_bytes())
        self.disable()
        retired = self.manager.execute("homeowner", "list", {})["retired"]
        self.assertEqual(retired[0]["host"]["ssh_user"], "pcctl")
        self.assertEqual(retired[0]["host"]["ssh_port"], 22)
        self.assertFalse(self.manager.execute("homeowner", "list", {})["active"])
        # A restore writes the canonical defaults only after fresh SSH
        # verification; existing PC identifiers, IP and MAC are unchanged.
        current = self.manager.execute("homeowner", "list", {})
        with patch.object(agent, "_probe", return_value={
            "verified": True, "code": "ssh_key_and_status_verified"}) as probe:
            restored = self.manager.execute("homeowner", "restore", {
                "id": "gaming",
                "revision": current["retired"][0]["revision"],
                "inventoryRevision": current["revision"], "confirm": "gaming",
            })
        probe.assert_called_once_with(host())
        self.assertEqual(restored["restored"], "gaming")
        self.assertEqual(self.manager.execute("homeowner", "list", {})["active"][0]["id"], "gaming")

    def test_disable_removes_only_selected_host_from_command_inventory(self):
        # A disabled host is immediately unknown to the pcctl command router.
        from core import load_hosts, perform
        agent.update_inventory(self.live, lambda document: {
            "hosts": document["hosts"] + [
                host("other", "192.168.50.45", "02:11:22:33:44:57")]})
        result = self.disable()
        self.assertEqual(result["disabled"], "gaming")
        self.assertNotIn("gaming", load_hosts(str(self.live)))
        self.assertIn("other", load_hosts(str(self.live)))
        with self.assertRaisesRegex(ValueError, "Unknown computer"):
            perform("gaming", "wake", str(self.live))
        retired = self.manager.execute("homeowner", "list", {})["retired"]
        self.assertEqual(len(retired), 1)
        self.assertEqual(retired[0]["host"], host())
        self.assertFalse(retired[0]["archived"])
        self.assertFalse(retired[0]["cleanupPending"])
        self.assertEqual(agent.RETIRED.stat().st_mode & 0o777, 0o600)

    def test_archive_keeps_config_and_restore_requires_new_verified_ssh(self):
        self.disable()
        listed = self.manager.execute("homeowner", "list", {})
        record = listed["retired"][0]
        with self.assertRaises(agent.InventoryFault) as denied:
            self.manager.execute("homeowner", "archive", {
                "id": "gaming", "revision": record["revision"], "confirm": "wrong"})
        self.assertEqual(denied.exception.status, 400)
        archived = self.manager.execute("homeowner", "archive", {
            "id": "gaming", "revision": record["revision"], "confirm": "gaming"})
        self.assertEqual(archived["archived"], "gaming")
        data = self.manager.execute("homeowner", "list", {})
        self.assertTrue(data["retired"][0]["archived"])
        restore = {"id": "gaming", "revision": data["retired"][0]["revision"],
                   "inventoryRevision": data["revision"], "confirm": "gaming"}
        with patch.object(agent, "_probe", return_value={
            "verified": False, "code": "host_key_not_pinned"}) as checked:
            with self.assertRaises(agent.InventoryFault) as rejected:
                self.manager.execute("homeowner", "restore", restore)
            self.assertEqual(rejected.exception.status, 409)
            checked.assert_called_once_with(host())
        self.assertFalse(self.manager.execute("homeowner", "list", {})["active"])
        with patch.object(agent, "_probe", return_value={
            "verified": True, "code": "ssh_key_and_status_verified"}) as checked:
            restored = self.manager.execute("homeowner", "restore", restore)
            checked.assert_called_once_with(host())
        self.assertEqual(restored["restored"], "gaming")
        self.assertFalse(restored["cleanupPending"])
        self.assertEqual(self.manager.execute("homeowner", "list", {})["retired"], [])
        self.assertEqual(self.manager.execute("homeowner", "list", {})["active"][0]["id"], "gaming")
        self.assertEqual(self.manager.execute("homeowner", "list", {})["active"][0]["address"],
                         "192.168.50.12")

    def test_disable_requires_exact_confirmation_and_current_revision(self):
        before = self.manager.execute("homeowner", "list", {})["revision"]
        for wrong in ("other", "", None):
            with self.assertRaises(agent.InventoryFault) as error:
                self.manager.execute("homeowner", "disable", {
                    "id": "gaming", "revision": before, "confirm": wrong})
            self.assertEqual(error.exception.status, 400)
        agent.update_inventory(self.live, lambda doc: {
            "hosts": doc["hosts"] + [host("other", "192.168.50.45",
                                           "02:11:22:33:44:57")]})
        with self.assertRaises(agent.InventoryFault) as error:
            self.manager.execute("homeowner", "disable", {
                "id": "gaming", "revision": before, "confirm": "gaming"})
        self.assertEqual(error.exception.status, 409)
        self.assertFalse(agent.RETIRED.exists())
        self.assertIn("gaming", self.live.read_text())

    def test_failed_retirement_snapshot_preserves_active_host(self):
        with patch.object(agent, "_save_retired",
                          side_effect=agent.InventoryFault(503, "Disk unavailable")):
            with self.assertRaises(agent.InventoryFault) as error:
                self.disable()
            self.assertEqual(error.exception.status, 503)
        self.assertIn("gaming", self.live.read_text())
        self.assertFalse(agent.RETIRED.exists())

    def test_failure_after_snapshot_can_be_retried_without_data_loss(self):
        # Snapshot commits before removing the command target. A simulated
        # atomic replace failure must retain the PC and its recovery copy.
        original = self.live.read_bytes()
        with patch.object(agent, "update_inventory", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.disable()
        self.assertEqual(original, self.live.read_bytes())
        self.assertTrue(agent.RETIRED.exists())
        pending = self.manager.execute("homeowner", "list", {})
        self.assertTrue(pending["retired"][0]["cleanupPending"])
        self.assertTrue(pending["active"][0]["lifecyclePending"])
        self.disable()
        after = self.manager.execute("homeowner", "list", {})
        self.assertEqual(after["active"], [])
        self.assertEqual(after["retired"][0]["host"], host())

    def test_restore_failure_and_stale_revisions_leave_host_disabled(self):
        self.disable()
        entry = self.manager.execute("homeowner", "list", {})
        record = entry["retired"][0]
        request = {"id": "gaming", "revision": record["revision"],
                   "inventoryRevision": entry["revision"], "confirm": "gaming"}
        with patch.object(agent, "_probe") as checked:
            with self.assertRaises(agent.InventoryFault) as error:
                self.manager.execute("homeowner", "restore", {
                    **request, "revision": "0" * 24})
            self.assertEqual(error.exception.status, 409)
            checked.assert_not_called()
        agent.update_inventory(self.live, lambda doc: {
            "hosts": [host("other", "192.168.50.45",
                            "02:11:22:33:44:57")]})
        with patch.object(agent, "_probe") as checked:
            with self.assertRaises(agent.InventoryFault) as error:
                self.manager.execute("homeowner", "restore", request)
            self.assertEqual(error.exception.status, 409)
            checked.assert_not_called()
        self.assertNotIn("gaming", self.live.read_text())

    def test_corrupted_retirement_metadata_fails_closed(self):
        self.disable()
        agent.RETIRED.write_text('{"retired":[{"host":{}}]}')
        with self.assertRaises(agent.InventoryFault) as error:
            self.manager.execute("homeowner", "list", {})
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn("gaming", self.live.read_text())

    def test_disabled_host_identity_is_reserved_from_new_drafts(self):
        self.disable()
        for proposed in [
            host("gaming", "192.168.50.44", "02:11:22:33:44:56"),
            host("other", "192.168.50.12", "02:11:22:33:44:56"),
            host("other", "192.168.50.44", "02:11:22:33:44:55"),
        ]:
            with self.subTest(proposed=proposed), self.assertRaises(agent.InventoryFault) as err:
                self.manager.execute("homeowner", "draft-create", proposed)
            self.assertEqual(err.exception.status, 409)

    def test_restore_cleanup_failure_flags_active_overlap_without_erasing_metadata(self):
        self.disable()
        state = self.manager.execute("homeowner", "list", {})
        request = {"id": "gaming", "revision": state["retired"][0]["revision"],
                   "inventoryRevision": state["revision"], "confirm": "gaming"}
        with patch.object(agent, "_probe", return_value={
            "verified": True, "code": "ssh_key_and_status_verified"}):
            original = agent._save_retired
            calls = 0
            def fail_cleanup(s):
                raise agent.InventoryFault(503, "cleanup failed")
            with patch.object(agent, "_save_retired", side_effect=fail_cleanup):
                answer = self.manager.execute("homeowner", "restore", request)
        self.assertTrue(answer["cleanupPending"])
        listed = self.manager.execute("homeowner", "list", {})
        self.assertTrue(listed["active"][0]["lifecyclePending"])
        self.assertTrue(listed["retired"][0]["cleanupPending"])
        with self.assertRaises(agent.InventoryFault) as error:
            self.manager.execute("homeowner", "archive", {
                "id": "gaming", "revision": listed["retired"][0]["revision"],
                "confirm": "gaming"})
        self.assertEqual(error.exception.status, 409)

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

    def test_missing_active_inventory_fails_closed(self):
        self.live.unlink()
        with self.assertRaises(agent.InventoryFault) as err:
            self.manager.execute("homeowner","list",{})
        self.assertEqual(err.exception.status,503)
        with self.assertRaises(agent.InventoryFault):
            self.manager.execute("homeowner","draft-create",
                                 host("other","192.168.50.44","02:11:22:33:44:56"))
        self.assertFalse(self.live.exists())

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

    def test_audit_symlink_is_rejected_without_touching_target(self):
        target=self.home/"outside.txt"
        target.write_text("original")
        agent.AUDIT.symlink_to(target)
        with self.assertRaises(agent.InventoryFault) as error:
            self.manager.execute("homeowner","rename",{
                "id":"gaming","name":"New name",
                "revision":self.manager.execute("homeowner","list",{})["revision"]})
        self.assertEqual(error.exception.status,503)
        self.assertEqual(target.read_text(),"original")
        self.assertEqual(json.loads(self.live.read_text())["hosts"][0]["name"],"Gaming")

    def test_audit_rotates_at_bounded_size(self):
        agent.AUDIT.write_bytes(b"a"*1_000_001)
        self.manager.execute("homeowner","rename",{
            "id":"gaming","name":"New name",
            "revision":self.manager.execute("homeowner","list",{})["revision"]})
        self.assertTrue((self.state/"audit.previous.jsonl").is_file())
        self.assertLess(agent.AUDIT.stat().st_size,4096)

    def test_admin_inventory_audit_returns_only_last_fifty_safe_events(self):
        for i in range(61):
            agent._audit("homeowner", "gaming", "rename",
                         "requested" if i % 2 else "committed")
        answer = self.manager.execute("homeowner", "audit-list", {})
        self.assertEqual(answer["limit"], 50)
        self.assertEqual(len(answer["events"]), 50)
        self.assertEqual(answer["events"][0]["actor"], "homeowner")
        self.assertEqual(answer["events"][0]["target"], "gaming")
        self.assertEqual(answer["events"][0]["operation"], "rename")
        self.assertEqual(answer["events"][0]["outcome"], "committed")
        for event in answer["events"]:
            self.assertEqual(set(event), {
                "time", "actor", "target", "operation", "outcome"})
            self.assertNotIn("192.168.", str(event))
            self.assertNotIn("02:11:22:33:44:55", str(event))
        with self.assertRaises(agent.InventoryFault) as denied:
            self.manager.execute("homeowner", "audit-list", {"path": "/etc/passwd"})
        self.assertEqual(denied.exception.status, 400)

    def test_audit_reader_refuses_symlinks_and_bad_history_records(self):
        target = self.home / "outside.txt"
        target.write_text("original")
        agent.AUDIT.symlink_to(target)
        with self.assertRaises(agent.InventoryFault) as denied:
            self.manager.execute("homeowner", "audit-list", {})
        self.assertEqual(denied.exception.status, 503)
        self.assertEqual(target.read_text(), "original")
        agent.AUDIT.unlink()
        agent.AUDIT.write_text('{"actor":"homeowner","operation":"rename","secret":"bad"}\\n')
        with self.assertRaises(agent.InventoryFault) as denied:
            self.manager.execute("homeowner", "audit-list", {})
        self.assertEqual(denied.exception.status, 503)

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

    def test_restore_ssh_verification_gets_same_bounded_rpc_timeout_as_activate(self):
        class FakeSocket:
            def __init__(self): self.timeout = None
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def settimeout(self, timeout): self.timeout = timeout
            def connect(self, target):
                self.target = target
            def sendall(self, data):
                self.data = data
            def makefile(self, mode):
                return io.BytesIO(b'{"ok":true,"result":{}}\n')
        outcomes = []
        def factory(*args, **kwargs):
            result=FakeSocket()
            outcomes.append(result)
            return result
        with self.config(), patch.object(api.socket,"socket",side_effect=factory):
            for operation in ("verify", "activate", "restore", "list", "audit-list"):
                self.assertEqual(api.computer_request("homeowner", operation, {}), {})
        self.assertEqual([socket.timeout for socket in outcomes], [23, 23, 23, 5, 5])
        self.assertTrue(all(x.target == "/run/homeaway-pcinventory/agent.sock"
                            for x in outcomes))

    def test_inventory_audit_endpoint_requires_current_admin_role(self):
        path="/control/api/admin/computers/audit"
        with self.config(),patch.object(api,"broker_request",
              side_effect=self.identity("operators")),patch.object(api,"computer_request") as writer:
            code,_=self.request("GET",path)
            self.assertEqual(code,403)
            writer.assert_not_called()
        with self.config(False),patch.object(api,"broker_request",
              side_effect=self.identity("admins")),patch.object(api,"computer_request") as writer:
            code,_=self.request("GET",path)
            self.assertEqual(code,404)
            writer.assert_not_called()
        expected={"events":[{"time":"2026-11-01T09:00:00+00:00",
                            "actor":"homeowner","target":"gaming",
                            "operation":"disable","outcome":"committed"}],
                  "limit":50}
        with self.config(),patch.object(api,"broker_request",
              side_effect=self.identity("admins")),patch.object(api,"computer_request",
              return_value=expected) as writer:
            code,body=self.request("GET",path)
            self.assertEqual(code,200)
            self.assertEqual(body,expected)
            writer.assert_called_once_with("homeowner","audit-list",{})

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

    def test_verify_and_activate_are_admin_only_and_csrf_guarded(self):
        activation={"id":"living-room","revision":"a"*24,
                    "inventoryRevision":"b"*24,"confirm":"living-room"}
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity("operators")),patch.object(api,"computer_request") as writer:
            for verb,payload in (
                ("verify",{"id":"living-room","revision":"a"*24}),
                ("activate",activation),
            ):
                code,_=self.request("POST","/control/api/admin/computers/"+verb,payload)
                self.assertEqual(code,403)
            writer.assert_not_called()
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity("admins")),patch.object(api,"computer_request") as writer:
            code,_=self.request("POST","/control/api/admin/computers/activate",activation,origin=False)
            self.assertEqual(code,403)
            writer.assert_not_called()
        with self.config(False),patch.object(api,"broker_request",
             side_effect=self.identity("admins")),patch.object(api,"computer_request") as writer:
            code,_=self.request("POST","/control/api/admin/computers/activate",activation)
            self.assertEqual(code,404)
            writer.assert_not_called()

    def test_admin_activate_passes_exact_confirmation_to_writer(self):
        activation={"id":"living-room","revision":"a"*24,
                    "inventoryRevision":"b"*24,"confirm":"living-room"}
        calls=[]
        def fake(actor,op,payload):
            calls.append((actor,op,payload))
            if op=="verify":return {"id":"living-room","verified":True,
                                    "code":"ssh_key_and_status_verified"}
            return {"activated":"living-room","cleanupPending":False}
        with self.config(),patch.object(api,"broker_request",
             side_effect=self.identity("admins")),patch.object(api,"computer_request",side_effect=fake):
            code,result=self.request("POST","/control/api/admin/computers/verify",
                                      {"id":"living-room","revision":"a"*24})
            self.assertEqual(code,200)
            self.assertTrue(result["verified"])
            code,result=self.request("POST","/control/api/admin/computers/activate",activation)
            self.assertEqual(code,200)
            self.assertEqual(result["activated"],"living-room")
            self.assertEqual(calls[-1],("homeowner","activate",activation))

    def test_retirement_routes_enforce_admin_and_csrf(self):
        ids = ("disable", "archive", "restore")
        payload = {"id": "gaming", "revision": "a" * 24, "confirm": "gaming"}
        with self.config(), patch.object(api, "broker_request",
               side_effect=self.identity("operators")), patch.object(api, "computer_request") as writer:
            for route in ids:
                code, _ = self.request("POST",
                    "/control/api/admin/computers/" + route, payload)
                self.assertEqual(code, 403)
            writer.assert_not_called()
        with self.config(), patch.object(api, "broker_request",
               side_effect=self.identity("admins")), patch.object(api, "computer_request") as writer:
            for route in ids:
                code, _ = self.request("POST",
                    "/control/api/admin/computers/" + route, payload, origin=False)
                self.assertEqual(code, 403)
            writer.assert_not_called()
        with self.config(False), patch.object(api, "broker_request",
               side_effect=self.identity("admins")), patch.object(api, "computer_request") as writer:
            code, _ = self.request("POST", "/control/api/admin/computers/disable", payload)
            self.assertEqual(code, 404)
            writer.assert_not_called()

    def test_admin_retire_restore_routes_send_fixed_payload(self):
        payload = {"id": "gaming", "revision": "a" * 24, "confirm": "gaming"}
        with self.config(), patch.object(api, "broker_request",
               side_effect=self.identity("admins")), patch.object(api, "computer_request",
               side_effect=lambda actor, op, data: {"actor": actor, "operation": op, "payload": data}):
            for op in ("disable", "archive", "restore"):
                body = {**payload, **({"inventoryRevision": "b" * 24}
                         if op == "restore" else {})}
                code, result = self.request("POST",
                    "/control/api/admin/computers/" + op, body)
                self.assertEqual(code, 200)
                self.assertEqual(result, {"actor": "homeowner", "operation": op,
                                          "payload": body})

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
