"""HTTP authorization contracts for the feature-flagged account UI.

No Pi, Docker, LDAP, or actual privileged agent is accessed.
"""
import importlib.util
import json
import os
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from http.client import HTTPConnection

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location("homeaway_accounts_http",ROOT/"control/api/app.py")
api=importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

class HttpAccounts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=api.ThreadingHTTPServer(("127.0.0.1",0),api.Handler)
        cls.worker=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join(2)

    def request(self,method,path,body=None,headers=None):
        conn=HTTPConnection("127.0.0.1",self.server.server_port,timeout=4)
        data=json.dumps(body) if body is not None else None
        h={"Content-Type":"application/json",**(headers or {})}
        conn.request(method,path,body=data,headers=h)
        response=conn.getresponse()
        code=response.status
        text=response.read()
        conn.close()
        return code,json.loads(text) if text else {}

    def claims(self,role="admins"):
        return {"X-HomeAway-User":"homeowner","X-HomeAway-Groups":role,
                "Origin":"https://homeaway.home.arpa:8443",
                "Sec-Fetch-Site":"same-origin","X-HomeAway-Request":"1"}

    def config(self,enabled="true"):
        return patch.dict(os.environ,{"HOMEAWAY_ACCOUNT_ADMIN_ENABLED":enabled,
                  "HOMEAWAY_CONTROL_ORIGIN":"https://homeaway.home.arpa:8443"})

    def resolved(self,role="admins"):
        def mock(actor,operation,payload):
            if operation=="resolve":return {"role":role}
            if operation=="users":return {"users":[]}
            return {"user":{"id":"newmember","homeawayAccess":False}}
        return mock

    def test_disabled_mode_has_no_admin_endpoints(self):
        with self.config("false"),patch.object(api,"broker_request") as call:
            code,_=self.request("GET","/control/api/admin/users",headers=self.claims())
            self.assertEqual(code,404)
            code,_=self.request("POST","/control/api/admin/users/create",
                {"id":"newmember","name":"New Member","email":"new@home.invalid"},
                self.claims())
            self.assertEqual(code,404)
            call.assert_not_called()

    def test_stale_authelia_role_rejected_even_for_current_session(self):
        with self.config(),patch.object(api,"broker_request",side_effect=self.resolved("viewers")):
            code,_=self.request("GET","/control/api/me",headers=self.claims("admins"))
            self.assertEqual(code,401)
            code,_=self.request("POST","/control/api/actions",
                {"host":"gaming","action":"wake","confirm":""},self.claims("admins"))
            self.assertEqual(code,401)

    def test_broker_outage_fails_closed_without_rpc(self):
        with self.config(),patch.object(api,"broker_request",side_effect=api.BrokerError(503,"Down")),\
             patch.object(api,"apply_action") as rpc:
            code,_=self.request("POST","/control/api/actions",
                {"host":"gaming","action":"wake","confirm":""},self.claims("admins"))
            self.assertEqual(code,401)
            rpc.assert_not_called()

    def test_admin_screen_not_available_to_operator(self):
        with self.config(),patch.object(api,"broker_request",side_effect=self.resolved("operators")):
            code,result=self.request("GET","/control/api/me",headers=self.claims("operators"))
            self.assertEqual(code,200)
            self.assertFalse(result["capabilities"]["manageUsers"])
            code,_=self.request("GET","/control/api/admin/users",headers=self.claims("operators"))
            self.assertEqual(code,403)
            code,_=self.request("POST","/control/api/admin/users/revoke",
                {"id":"pending","revision":"0"*24},self.claims("operators"))
            self.assertEqual(code,403)

    def test_admin_create_requires_csrf_headers(self):
        with self.config(),patch.object(api,"broker_request",side_effect=self.resolved()):
            req={"id":"newmember","name":"New Member","email":"new@home.invalid"}
            head=self.claims()
            head.pop("Origin")
            code,_=self.request("POST","/control/api/admin/users/create",req,head)
            self.assertEqual(code,403)
            head=self.claims()
            head["Sec-Fetch-Site"]="cross-site"
            code,_=self.request("POST","/control/api/admin/users/create",req,head)
            self.assertEqual(code,403)

    def test_admin_can_list_and_submit_narrow_mutations(self):
        sent=[]
        def mock(actor,operation,payload):
            sent.append((actor,operation,payload))
            if operation=="resolve":return {"role":"admins"}
            if operation=="users":return {"users":[]}
            return {"user":{"id":"newmember","role":None}}
        with self.config(),patch.object(api,"broker_request",side_effect=mock):
            code,data=self.request("GET","/control/api/admin/users",headers=self.claims())
            self.assertEqual(code,200)
            self.assertEqual(data,{"users":[]})
            req={"id":"newmember","name":"New Member","email":"new@home.invalid"}
            code,data=self.request("POST","/control/api/admin/users/create",req,self.claims())
            self.assertEqual(code,200)
            self.assertEqual(data["user"]["id"],"newmember")
            self.assertEqual(sent[-1],("homeowner","create",req))

if __name__=="__main__":unittest.main()
