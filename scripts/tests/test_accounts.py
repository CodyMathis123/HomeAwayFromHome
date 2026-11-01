"""No-network identity-broker security and lifecycle tests."""
from __future__ import annotations
import importlib.util
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("account_broker_test",ROOT/"control/accounts/broker.py")
broker=importlib.util.module_from_spec(spec)
spec.loader.exec_module(broker)

class Directory:
    def __init__(self):
        self.groups={"admins":7,"operators":8,"viewers":9}
        self.people={
            "homeowner":self.person("homeowner","admins"),
            "manager":self.person("manager","admins"),
            "operator":self.person("operator","operators"),
            "viewer":self.person("viewer","viewers"),
            "pending":self.person("pending",None),
        }
        self.writes=[]
        self.fail=False

    def person(self,id,role):
        return {"id":id,"displayName":id.title(),
                "email":id+"@home.invalid",
                "groups":[{"id":self.groups[role],"displayName":role}] if role else []}

    def all(self):
        if self.fail:raise broker.BrokerFailure(503,"Offline")
        return list(self.people.values()),self.groups.copy()

    def user(self,id):
        if self.fail:raise broker.BrokerFailure(503,"Offline")
        return self.people.get(id)

    def create(self,id,display,email):
        if self.fail:raise broker.BrokerFailure(503,"Offline")
        if id in self.people: raise AssertionError("duplicate")
        self.people[id]={"id":id,"displayName":display,"email":email,"groups":[]}
        self.writes.append(("create",id))

    def membership(self,id,group_id,enabled):
        if self.fail:raise broker.BrokerFailure(503,"Offline")
        role=next(k for k,v in self.groups.items() if v==group_id)
        if enabled:
            self.people[id]["groups"].append({"id":group_id,"displayName":role})
        else:
            self.people[id]["groups"]=[g for g in self.people[id]["groups"] if g["id"]!=group_id]
        self.writes.append((("grant" if enabled else "revoke"),id,role))

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.directory=Directory()
        self.temp=tempfile.TemporaryDirectory()
        self.api=broker.AccountBroker(self.directory,Path(self.temp.name)/"audit.jsonl")

    def tearDown(self):self.temp.cleanup()

    def invoke(self,actor,op,**params):
        return self.api.execute(actor,op,params)

    def test_current_role_never_cached(self):
        self.assertEqual(self.api.resolve("operator"),{"role":"operators"})
        self.directory.people["operator"]["groups"]=[]
        self.assertEqual(self.api.resolve("operator"),{"role":None})
        self.directory.fail=True
        with self.assertRaises(broker.BrokerFailure) as cm:
            self.api.resolve("operator")
        self.assertEqual(cm.exception.status,503)

    def test_read_requires_live_admin(self):
        self.assertEqual(len(self.invoke("homeowner","users")["users"]),5)
        for actor in ("operator","viewer","pending","missing"):
            with self.assertRaises(broker.BrokerFailure) as cm:
                self.invoke(actor,"users")
            self.assertEqual(cm.exception.status,403)

    def test_create_pending_no_role_or_password(self):
        r=self.invoke("homeowner","create",id="newmember",name="New Member",
                      email="newmember@home.invalid")
        self.assertEqual(r["user"]["role"],None)
        self.assertFalse(r["passwordReady"])
        self.assertEqual(self.api.resolve("newmember"),{"role":None})
        self.assertEqual(self.directory.writes,[("create","newmember")])
        for id,email in (("newmember","other@home.invalid"),("different","newmember@home.invalid"),
                         ("NEWMEMBER","another@home.invalid")):
            with self.assertRaises(broker.BrokerFailure) as cm:
                self.invoke("homeowner","create",id=id,name="Other",email=email)
            self.assertIn(cm.exception.status,(400,409))

    def test_role_demote_revoke_and_revision(self):
        target=self.directory.user("operator")
        rev=broker.fingerprint(target)
        changed=self.invoke("homeowner","set-role",id="operator",role="viewers",revision=rev)
        self.assertEqual(changed["user"]["role"],"viewers")
        self.assertEqual(self.directory.writes[0],("revoke","operator","operators"))
        self.assertEqual(self.directory.writes[1],("grant","operator","viewers"))
        with self.assertRaises(broker.BrokerFailure) as cm:
            self.invoke("homeowner","set-role",id="operator",role="admins",revision=rev)
        self.assertEqual(cm.exception.status,409)
        rev=changed["user"]["revision"]
        denied=self.invoke("homeowner","revoke",id="operator",revision=rev)
        self.assertEqual(denied["user"]["role"],None)
        self.assertEqual(self.api.resolve("operator"),{"role":None})

    def test_last_admin_and_self_protection(self):
        # Bootstrap and current actor may never be edited.
        for id in ("homeowner","admin"):
            with self.assertRaises(broker.BrokerFailure) as cm:
                self.invoke("homeowner","revoke",id=id,revision="0"*24)
            self.assertEqual(cm.exception.status,403)
        # With two admins manager can be demoted.
        rev=broker.fingerprint(self.directory.user("manager"))
        self.invoke("homeowner","revoke",id="manager",revision=rev)
        # The remaining admin may NOT edit self, and no outsider can edit.
        with self.assertRaises(broker.BrokerFailure):
            self.invoke("manager","users")
        self.assertEqual(self.api.resolve("homeowner"),{"role":"admins"})

    def test_last_admin_guard_when_not_self(self):
        # Synthetic bootstrap is 'homeowner'; deliberately make it a viewer
        # to exercise the final-admin safeguard against a different actor.
        self.directory.people["homeowner"]["groups"]=[]
        self.directory.people["operator"]["groups"]=[{"id":7,"displayName":"admins"}]
        self.directory.people["manager"]["groups"]=[]
        # The actor is now the last admin and self-modification is forbidden.
        with self.assertRaises(broker.BrokerFailure):
            self.invoke("operator","revoke",id="operator",
                        revision=broker.fingerprint(self.directory.user("operator")))

    def test_rejected_fields_and_invalid_roles_have_no_writes(self):
        rev=broker.fingerprint(self.directory.user("operator"))
        for args in ({"id":"operator","revision":rev,"role":"root"},
                     {"id":"operator","revision":rev,"role":"admins","extra":"x"},
                     {"id":"operator","revision":"bad","role":"viewers"}):
            with self.assertRaises(broker.BrokerFailure):
                self.invoke("homeowner","set-role",**args)
        self.assertFalse(self.directory.writes)

    def test_down_directory_and_audit_failure_fail_closed(self):
        self.directory.fail=True
        with self.assertRaises(broker.BrokerFailure):
            self.invoke("homeowner","users")
        self.directory.fail=False
        self.api.audit_file=Path("/nonexistent/no-permission?")/"audit.jsonl"
        # Don't depend on runner permissions: mock the bound audit method.
        with patch.object(self.api,"audit",side_effect=broker.BrokerFailure(503,"Audit unavailable")):
            with self.assertRaises(broker.BrokerFailure):
                self.invoke("homeowner","create",id="newmember",name="N",email="n@home.invalid")
        self.assertFalse(self.directory.writes)

    def test_audit_has_only_minimal_fields(self):
        self.invoke("homeowner","create",id="newmember",name="New",email="new@home.invalid")
        rows=[json.loads(x) for x in self.api.audit_file.read_text().splitlines()]
        self.assertEqual(len(rows),2)
        self.assertEqual(set(rows[0]),{"time","actor","target","action","outcome"})
        self.assertNotIn("new@home.invalid",self.api.audit_file.read_text())

if __name__=="__main__":unittest.main()
