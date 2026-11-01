#!/usr/bin/env python3
"""Synthetic Authelia 4.39 + LLDAP session revocation integration test.

No production accounts, ports, CA, secrets, TOTP credentials, or volumes.
Run ONLY as HOMEAWAY_LLDAP_LAB=1 in an ephemeral CI worker.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LLDAP_IMAGE="ghcr.io/lldap/lldap:2026-09-22-debian"
AUTHELIA_IMAGE="authelia/authelia:4.39.28"
DIR_URL="http://127.0.0.1:17179"
AUTH_HOST="auth.lab.home.arpa"
APP_HOST="lab.home.arpa"
AUTH_PORT=19091

def docker(*args, check=True):
    r=subprocess.run(["docker",*args],capture_output=True,text=True,
                     timeout=150,check=False)
    if check and r.returncode:
        # Only CI-synthetic secrets exist here; redact opaque tokens regardless
        # before revealing an actionable Authelia validation error.
        detail=re.sub(r"[A-Za-z0-9_+/-]{28,}", "<redacted>",
                      (r.stdout+"\n"+r.stderr)[-6000:])
        raise RuntimeError("Synthetic Docker step failed: " + detail)
    return r.stdout.strip()

def post(url, data, token=None):
    h={"Content-Type":"application/json"}
    if token: h["Authorization"]="Bearer "+token
    with urlopen(Request(url,data=json.dumps(data).encode(),
                         headers=h,method="POST"),timeout=4) as result:
        return json.load(result)

def graphql(token, q, variables=None):
    r=post(DIR_URL+"/api/graphql",{"query":q,"variables":variables or {}},token)
    if r.get("errors") or not isinstance(r.get("data"),dict):
        raise AssertionError("Synthetic LLDAP GraphQL response was rejected")
    return r["data"]

def totp(secret):
    key=base64.b32decode(secret+"="*((-len(secret))%8))
    counter=int(time.time()//30).to_bytes(8,"big")
    digest=hmac.new(key,counter,hashlib.sha1).digest()
    offset=digest[-1]&15
    n=int.from_bytes(digest[offset:offset+4],"big")&0x7fffffff
    return str(n%1000000).zfill(6)

def call_auth(method,path,body=None,cookie=None,headers=None):
    conn=http.client.HTTPConnection("127.0.0.1",AUTH_PORT,timeout=4)
    h={"Host":AUTH_HOST,"X-Forwarded-Proto":"https",
       "Content-Type":"application/json"}
    if cookie: h["Cookie"]=cookie
    if headers: h.update(headers)
    payload=json.dumps(body).encode() if body is not None else None
    conn.request(method,path,body=payload,headers=h)
    resp=conn.getresponse()
    status=resp.status
    out=resp.read(16384)
    received=dict(resp.getheaders())
    conn.close()
    return status,received,out

def forward_auth(cookie):
    return call_auth("GET","/api/authz/forward-auth",cookie=cookie,
       headers={"X-Forwarded-Proto":"https","X-Forwarded-Host":APP_HOST,
                "X-Forwarded-Uri":"/control/api/me",
                "X-Forwarded-Method":"GET"})

def session(password,secret):
    status,headers,_=call_auth("POST","/api/firstfactor",
                               {"username":"admin","password":password})
    assert status==200, "Authelia first-factor synthetic login failed: "+str(status)
    set_cookie=headers.get("Set-Cookie") or headers.get("set-cookie")
    assert set_cookie, "Authelia session cookie was not set"
    cookie=set_cookie.split(";",1)[0]
    # Synthetic environment only: disabling reused-code rejection allows two
    # independent browser cookies without sleeping for 30 seconds.
    status,_,_=call_auth("POST","/api/secondfactor/totp",
                        {"token":totp(secret)},cookie)
    assert status==200, "Synthetic TOTP second factor failed: "+str(status)
    status,auth_headers,_=forward_auth(cookie)
    assert 200<=status<300, "Two-factor session was not authorized: "+str(status)
    assert auth_headers.get("Remote-User")=="admin", \
        "Forward-auth didn't provide expected verified username"
    assert "admins" in auth_headers.get("Remote-Groups","").split(","), \
        "Forward-auth didn't provide verified admin membership"
    return cookie

def main():
    if os.getenv("CI","").lower()!="true" or os.getenv("HOMEAWAY_LLDAP_LAB")!="1":
        raise SystemExit("Only disposable CI identity tests are allowed")
    label="hwl-2fa-"+secrets.token_hex(5)
    network=label+"-net"
    volume=label+"-data"
    password=secrets.token_urlsafe(35)
    totp_secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    temp=tempfile.TemporaryDirectory(prefix="homeaway-synthetic-authelia-")
    try:
        docker("network","create",network)
        docker("volume","create",volume)
        lldap=label+"-ldap"
        authelia=label+"-auth"
        docker("run","-d","--rm","--name",lldap,"--network",network,
               "-p","127.0.0.1:17179:17170","-v",volume+":/data",
               "-e","LLDAP_LDAP_BASE_DN=dc=lab,dc=home,dc=arpa",
               "-e","LLDAP_LDAP_USER_EMAIL=admin@lab.home.arpa",
               "-e","LLDAP_LDAP_USER_PASS="+password,
               "-e","LLDAP_JWT_SECRET="+secrets.token_urlsafe(48),
               "-e","LLDAP_KEY_SEED="+secrets.token_urlsafe(48),LLDAP_IMAGE)
        token=None
        for _ in range(90):
            try:
                token=post(DIR_URL+"/auth/simple/login",
                          {"username":"admin","password":password}).get("token")
                if token:break
            except (OSError,URLError,HTTPError,ValueError):pass
            time.sleep(1)
        assert token, "Synthetic LLDAP did not become healthy"
        group=graphql(token,"mutation($name:String!){createGroup(name:$name){id}}",
                      {"name":"admins"})["createGroup"]["id"]
        mutation="mutation($user:String!,$group:Int!){addUserToGroup(userId:$user,groupId:$group){ok}}"
        rm="mutation($user:String!,$group:Int!){removeUserFromGroup(userId:$user,groupId:$group){ok}}"
        args={"user":"admin","group":group}
        assert graphql(token,mutation,args)["addUserToGroup"]["ok"]
        print("PASS: LLDAP synthetic admin role granted",flush=True)

        # Read-only bind-mounted configuration; all Authelia storage is
        # created in its own disposable container tmpfs, not on the host.
        config=Path(temp.name)
        config.chmod(0o755)
        path=config/"configuration.yml"
        def q(s):return json.dumps(s)
        cfg=f"""server:
  address: tcp://:9091
log:
  level: warn
totp:
  issuer: HomeAwayLab
  disable_reuse_security_policy: true
authentication_backend:
  refresh_interval: always
  password_reset:
    disable: true
  ldap:
    implementation: lldap
    address: ldap://{lldap}:3890
    base_dn: DC=lab,DC=home,DC=arpa
    user: CN=admin,OU=people,DC=lab,DC=home,DC=arpa
    password: {q(password)}
access_control:
  default_policy: deny
  rules:
    - domain: {q(APP_HOST)}
      resources:
        - '^/control(?:/.*)?$'
      subject:
        - 'group:admins'
      policy: two_factor
session:
  secret: {q(secrets.token_urlsafe(64))}
  inactivity: 30m
  expiration: 8h
  cookies:
    - domain: {q(APP_HOST)}
      authelia_url: {q("https://"+AUTH_HOST)}
      default_redirection_url: {q("https://"+APP_HOST)}
storage:
  encryption_key: {q(secrets.token_urlsafe(64))}
  local:
    path: /data/db.sqlite3
notifier:
  filesystem:
    filename: /data/notification.txt
"""
        path.write_text(cfg,encoding="utf-8")
        path.chmod(0o644)
        docker("run","--rm","--network",network,
               "-v",f"{config}:/config:ro",
               AUTHELIA_IMAGE,"authelia","config","validate",
               "--config","/config/configuration.yml")
        docker("run","-d","--rm","--name",authelia,
               "--network",network,"-p",f"127.0.0.1:{AUTH_PORT}:9091",
               "--tmpfs","/data:rw,mode=1777",
               "-v",f"{config}:/config:ro",
               AUTHELIA_IMAGE)
        ready=False
        for _ in range(90):
            try:
                status,_,_=call_auth("GET","/api/health")
                if 200<=status<300:ready=True;break
            except (OSError,TimeoutError):pass
            time.sleep(1)
        assert ready,"Authelia did not become healthy"

        # Authelia's CLI creates a real encrypted TOTP record in its isolated
        # SQLite store, bypassing e-mail enrollment but not TOTP authentication.
        docker("exec",authelia,"authelia","storage","user","totp","generate",
               "admin","--config","/config/configuration.yml",
               "--secret",totp_secret)
        first=session(password,totp_secret)
        second=session(password,totp_secret)
        print("PASS: two independent sessions authenticated with password + TOTP",flush=True)

        assert graphql(token,rm,args)["removeUserFromGroup"]["ok"]
        for cookie in (first,second):
            status,_,_=forward_auth(cookie)
            assert not 200<=status<300, \
                "SECURITY FAIL: old authenticated cookie retained control access"
        print("PASS: removing LLDAP role revokes forward-auth on both old 2FA sessions",flush=True)

        assert graphql(token,mutation,args)["addUserToGroup"]["ok"]
        # User profiles may require a fresh request; the existing session
        # might be invalidated, so reauthentication on grant is allowed.
        print("PASS: role can be restored; do not assert old cookie is revived",flush=True)
    finally:
        docker("rm","-f",label+"-auth",check=False)
        docker("rm","-f",label+"-ldap",check=False)
        docker("volume","rm","-f",volume,check=False)
        docker("network","rm",network,check=False)
        temp.cleanup()

if __name__=="__main__":
    main()
