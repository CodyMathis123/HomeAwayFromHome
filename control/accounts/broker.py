"""Narrow HomeAway account broker, with LLDAP as authoritative role source.

This module never modifies Authelia's file user store or TOTP SQLite.
A revoked user can still have an LDAP password but cannot access HomeAway
control resources; do not label this operation "disable LDAP login".

The Docker deployment is intentionally not enabled until identity migration,
backup/restore and the Authelia session revocation test are accepted.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,128}\.[^@\s]{2,32}$")
ROLES = ("admins", "operators", "viewers")
MAX_BODY = 2048

class BrokerFailure(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status

def role_of(user: dict) -> str | None:
    names = {g["displayName"] for g in user["groups"]}
    return next((r for r in ROLES if r in names), None)

def fingerprint(user: dict) -> str:
    obj = {"id":user["id"], "displayName":user["displayName"],
           "email":user["email"],
           "groups":sorted(g["displayName"] for g in user["groups"])}
    return hashlib.sha256(json.dumps(obj,sort_keys=True,
                                     separators=(",",":")).encode()).hexdigest()[:24]

def view(user: dict) -> dict:
    role = role_of(user)
    return {"id":user["id"], "name":user["displayName"],
            "email":user["email"], "role":role,
            "homeawayAccess":role is not None, "revision":fingerprint(user)}

class LldapDirectory:
    Q_USERS = "query{users{id email displayName groups{id displayName}} groups{id displayName}}"
    Q_USER = "query($id:String!){user(userId:$id){id email displayName groups{id displayName}}}"
    CREATE = "mutation($user:CreateUserInput!){createUser(user:$user){id}}"
    ADD = "mutation($id:String!,$gid:Int!){addUserToGroup(userId:$id,groupId:$gid){ok}}"
    REMOVE = "mutation($id:String!,$gid:Int!){removeUserFromGroup(userId:$id,groupId:$gid){ok}}"

    def __init__(self, url: str, username: str, password: str):
        # The broker is expected on a private Docker-only network, never
        # accept arbitrary client-supplied GraphQL URLs or query strings.
        if url != "http://lldap:17170":
            raise ValueError("Only the private LLDAP service is permitted")
        self.url=url
        self.username=username
        self.password=password
        self._token=None

    def _post(self, path: str, payload: dict, token: str | None=None) -> dict:
        headers={"Content-Type":"application/json"}
        if token: headers["Authorization"]="Bearer "+token
        request=Request(self.url+path,data=json.dumps(payload).encode(),
                        headers=headers,method="POST")
        try:
            with urlopen(request,timeout=4) as reply:
                body=reply.read(262145)
                if len(body)>262144: raise BrokerFailure(503,"Identity response too large")
                data=json.loads(body)
                if not isinstance(data,dict):raise ValueError("Unexpected response")
                return data
        except (HTTPError,URLError,OSError,ValueError) as exc:
            # Never return upstream errors: they might contain identities or
            # secret-bearing submitted GraphQL variables.
            raise BrokerFailure(503,"Identity directory unavailable") from exc

    def _gql(self,query: str,variables: dict | None=None) -> dict:
        if not self._token:
            data=self._post("/auth/simple/login",
                            {"username":self.username,"password":self.password})
            self._token=data.get("token")
            if not isinstance(self._token,str) or not self._token:
                raise BrokerFailure(503,"Directory authentication unavailable")
        try:
            data=self._post("/api/graphql",{"query":query,
                                            "variables":variables or {}},self._token)
        except BrokerFailure as exc:
            self._token=None
            raise exc
        if data.get("errors") or not isinstance(data.get("data"),dict):
            raise BrokerFailure(503,"Directory rejected the operation")
        return data["data"]

    def all(self) -> tuple[list[dict], dict[str,int]]:
        data=self._gql(self.Q_USERS)
        users=data.get("users")
        groups=data.get("groups")
        if not isinstance(users,list) or not isinstance(groups,list):
            raise BrokerFailure(503,"Directory result invalid")
        mapping={g["displayName"]:g["id"] for g in groups if g["displayName"] in ROLES}
        return users,mapping

    def user(self,username: str) -> dict | None:
        try:
            result=self._gql(self.Q_USER,{"id":username})
            return result.get("user")
        except BrokerFailure:
            # LLDAP GraphQL returns errors for absent users, which must not
            # be confused with directory outages. Resolve from all() instead.
            users,_=self.all()
            return next((u for u in users if u["id"]==username),None)

    def create(self,username: str,display: str,email: str) -> None:
        result=self._gql(self.CREATE,{"user":{"id":username,
                                              "displayName":display,"email":email}})
        if result.get("createUser",{}).get("id")!=username:
            raise BrokerFailure(503,"Directory user creation did not commit")

    def membership(self,username: str,group_id: int,enabled: bool) -> None:
        q=self.ADD if enabled else self.REMOVE
        key="addUserToGroup" if enabled else "removeUserFromGroup"
        if self._gql(q,{"id":username,"gid":group_id}).get(key,{}).get("ok") is not True:
            raise BrokerFailure(503,"Directory membership change failed")

class AccountBroker:
    def __init__(self,directory, audit_file: Path | None=None):
        self.directory=directory
        self.audit_file=audit_file
        self.lock=threading.RLock()

    def audit(self, actor: str, target: str, action: str, outcome: str):
        if not self.audit_file: return
        row={"time":datetime.now(timezone.utc).isoformat(),"actor":actor,
             "target":target,"action":action,"outcome":outcome}
        try:
            self.audit_file.parent.mkdir(parents=True,exist_ok=True)
            with self.audit_file.open("a",encoding="utf-8") as handle:
                handle.write(json.dumps(row,separators=(",",":"))+"\n")
        except OSError:
            # For privileged mutations fail closed if audit storage unavailable.
            raise BrokerFailure(503,"Audit unavailable")

    def resolve(self,username: str) -> dict:
        if not USER_RE.fullmatch(username):
            raise BrokerFailure(403,"Invalid identity")
        # A fresh read for every request prevents stale cookies from
        # bypassing role revocation. No success caching is permitted.
        users,_=self.directory.all()
        current=next((u for u in users if u["id"]==username),None)
        return {"role":role_of(current) if current else None}

    def execute(self,actor: str, operation: str, payload: dict) -> dict:
        if not USER_RE.fullmatch(actor):
            raise BrokerFailure(403,"Invalid actor")
        with self.lock:
            users,groups=self.directory.all()
            acting=next((u for u in users if u["id"]==actor),None)
            if not acting or role_of(acting)!="admins":
                raise BrokerFailure(403,"Administrator privileges required")

            if operation=="users":
                return {"users":sorted((view(u) for u in users),
                                       key=lambda u:u["id"])}

            if operation=="create":
                if set(payload)!={"id","name","email"}:
                    raise BrokerFailure(400,"Invalid account fields")
                uid,name,email=payload["id"],payload["name"],payload["email"]
                if not all(isinstance(v,str) for v in (uid,name,email)) \
                   or not USER_RE.fullmatch(uid) or not 1<=len(name)<=80 \
                   or not EMAIL_RE.fullmatch(email):
                    raise BrokerFailure(400,"Invalid account details")
                if any(u["id"].casefold()==uid.casefold()
                       or u["email"].casefold()==email.casefold() for u in users):
                    raise BrokerFailure(409,"Account already exists")
                # Deliberately create PENDING with no HomeAway role; until
                # password enrollment is implemented, never hand out access.
                self.audit(actor,uid,"create-pending","requested")
                self.directory.create(uid,name,email)
                created=self.directory.user(uid)
                if not created or role_of(created):
                    raise BrokerFailure(503,"Unexpected directory account state")
                self.audit(actor,uid,"create-pending","committed")
                return {"user":view(created),"passwordReady":False}

            if operation not in ("set-role","revoke"):
                raise BrokerFailure(404,"Unknown operation")
            expected={"id","revision","role"} if operation=="set-role" else {"id","revision"}
            if set(payload)!=expected:
                raise BrokerFailure(400,"Invalid mutation fields")
            uid,revision=payload["id"],payload["revision"]
            if not isinstance(uid,str) or not USER_RE.fullmatch(uid) or \
               not isinstance(revision,str) or len(revision)!=24:
                raise BrokerFailure(400,"Invalid account revision")
            if uid==actor or uid=="admin":
                raise BrokerFailure(403,"Cannot change the active or bootstrap administrator")
            target=next((u for u in users if u["id"]==uid),None)
            if target is None:
                raise BrokerFailure(404,"Account does not exist")
            if fingerprint(target)!=revision:
                raise BrokerFailure(409,"Account changed; refresh before retry")
            current=role_of(target)
            newrole=payload["role"] if operation=="set-role" else None
            if newrole is not None and (not isinstance(newrole,str) or newrole not in ROLES):
                raise BrokerFailure(400,"Invalid role")
            if current=="admins" and newrole!="admins" and sum(
                    role_of(user)=="admins" for user in users)<=1:
                raise BrokerFailure(409,"Cannot remove the last administrator")
            if newrole==current:
                return {"user":view(target)}
            if newrole is not None and newrole not in groups:
                raise BrokerFailure(503,"Required directory role group missing")
            self.audit(actor,uid,operation,"requested")
            # Remove grants before adding new ones: a failed partial update
            # must never retain excess privilege. There is no GraphQL
            # compare-and-swap, so all external directory writers must be
            # disabled during privileged account mutations.
            for g in target["groups"]:
                if g["displayName"] in ROLES:
                    self.directory.membership(uid,int(g["id"]),False)
            if newrole:
                self.directory.membership(uid,int(groups[newrole]),True)
            final=self.directory.user(uid)
            if not final or role_of(final)!=newrole:
                raise BrokerFailure(503,"Directory confirmation failed")
            self.audit(actor,uid,operation,"committed")
            return {"user":view(final)}

class Handler(BaseHTTPRequestHandler):
    broker: AccountBroker
    secret: str
    server_version="HomeAwayAccounts/1"

    def log_message(self,format,*args):
        # Avoid request/payload/cookie logging.
        pass

    def respond(self,status:int,data:dict):
        payload=json.dumps(data,separators=(",",":")).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(payload)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        supplied=self.headers.get("X-HomeAway-Broker-Key","")
        if not supplied or not hmac.compare_digest(supplied,self.secret):
            return self.respond(403,{"error":"Forbidden"})
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.respond(415,{"error":"JSON required"})
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 1<=length<=MAX_BODY:
                raise BrokerFailure(413,"Invalid request length")
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict) or set(data)!={"actor","operation","payload"} \
               or not isinstance(data["payload"],dict):
                raise BrokerFailure(400,"Invalid request")
            actor,op,payload=data["actor"],data["operation"],data["payload"]
            if not isinstance(actor,str) or not isinstance(op,str):
                raise BrokerFailure(400,"Invalid request")
            if op=="resolve":
                if set(payload)!={"user"}:
                    raise BrokerFailure(400,"Invalid resolve request")
                answer=self.broker.resolve(payload["user"])
            else:
                answer=self.broker.execute(actor,op,payload)
            return self.respond(200,answer)
        except BrokerFailure as exc:
            return self.respond(exc.status,{"error":str(exc)})
        except (ValueError,TypeError,KeyError,UnicodeError):
            return self.respond(400,{"error":"Invalid request"})

    def do_GET(self):self.respond(405,{"error":"Method not allowed"})
    do_PUT=do_GET
    do_DELETE=do_GET
    do_PATCH=do_GET

def main():
    key=Path(os.environ["HOMEAWAY_BROKER_SECRET_FILE"]).read_text().strip()
    password=Path(os.environ["HOMEAWAY_LLDAP_ADMIN_PASSWORD_FILE"]).read_text().strip()
    if len(key)<40 or len(password)<20:
        raise SystemExit("Missing strong account broker or directory credential")
    directory=LldapDirectory(os.environ.get("HOMEAWAY_LLDAP_URL","http://lldap:17170"),
                             os.environ.get("HOMEAWAY_LLDAP_USER","admin"),
                             password)
    Handler.broker=AccountBroker(directory,Path("/data/accounts-audit.jsonl"))
    Handler.secret=key
    ThreadingHTTPServer(("0.0.0.0",8092),Handler).serve_forever()

if __name__=="__main__":
    main()
