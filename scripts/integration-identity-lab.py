#!/usr/bin/env python3
"""Disposable, synthetic LLDAP identity lab. Never use live account data.

This is only a GraphQL mutation smoke test. It does NOT establish Authelia
session invalidation, TOTP migration, backups, or production cutover safety.
Run only on an isolated CI worker with disposable Docker resources.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IMAGE = "ghcr.io/lldap/lldap:2026-09-22-debian"
BASE_URL = "http://127.0.0.1:17178"


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=150, check=True)
    return result.stdout.strip()


def post_json(endpoint: str, data: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(BASE_URL + endpoint, data=json.dumps(data).encode(),
                      headers=headers, method="POST")
    with urlopen(request, timeout=4) as reply:
        value = json.load(reply)
    if not isinstance(value, dict):
        raise RuntimeError("Unexpected directory response")
    return value


def graphql(token: str, query: str, variables: dict | None = None) -> dict:
    result = post_json("/api/graphql", {"query": query,
                                        "variables": variables or {}}, token)
    if result.get("errors"):
        # Deliberately avoid printing full response; GraphQL errors may
        # contain submitted identity values in real production contexts.
        raise RuntimeError("LLDAP GraphQL mutation rejected")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Missing GraphQL data")
    return data


def main() -> None:
    if os.getenv("CI", "").lower() != "true" or \
            os.getenv("HOMEAWAY_LLDAP_LAB") != "1":
        raise SystemExit("Identity lab requires CI=true and HOMEAWAY_LLDAP_LAB=1")

    admin_password = secrets.token_urlsafe(35)
    label = "homeaway-lldap-lab-" + secrets.token_hex(5)

    with tempfile.TemporaryDirectory(prefix="homeaway-synthetic-identity-"):
        # Docker-managed volume avoids container chown on host TemporaryDirectory
        # (which made the successful CI test fail during cleanup). All data is
        # synthetic and deleted when the container stops.
        volume = label + "-data"
        docker("volume", "create", volume)
        try:
            docker("run", "--detach", "--rm",
                   "--name", label,
                   "--publish", "127.0.0.1:17178:17170",
                   "--volume", volume + ":/data",
                   "--env", "LLDAP_LDAP_BASE_DN=dc=lab,dc=invalid",
                   "--env", "LLDAP_LDAP_USER_EMAIL=admin@lab.invalid",
                   "--env", "LLDAP_LDAP_USER_PASS=" + admin_password,
                   "--env", "LLDAP_JWT_SECRET=" + secrets.token_urlsafe(48),
                   "--env", "LLDAP_KEY_SEED=" + secrets.token_urlsafe(48),
                   IMAGE)

            token = None
            for _ in range(100):
                try:
                    reply = post_json("/auth/simple/login",
                                      {"username": "admin",
                                       "password": admin_password})
                    token = reply.get("token")
                    if isinstance(token, str) and token:
                        break
                except (OSError, HTTPError, URLError, ValueError, RuntimeError):
                    pass
                time.sleep(1)
            if not token:
                raise AssertionError("LLDAP did not become ready or admin login failed")
            print("PASS: synthetic directory login", flush=True)

            qcreate_group = ("mutation($name:String!){createGroup(name:$name)"
                             "{id displayName}}")
            groups = {}
            for name in ("operators", "viewers"):
                result = graphql(token, qcreate_group, {"name": name})
                group = result["createGroup"]
                assert group["displayName"] == name
                groups[name] = group["id"]

            qcreate_user = ("mutation($user:CreateUserInput!){"
                            "createUser(user:$user){id}}")
            member = {"id": "laboperator", "displayName": "Synthetic Operator",
                      "email": "operator@lab.invalid"}
            assert graphql(token, qcreate_user, {"user": member})[
                "createUser"]["id"] == member["id"]

            qadd = ("mutation($user:String!,$group:Int!){"
                    "addUserToGroup(userId:$user,groupId:$group){ok}}")
            qremove = ("mutation($user:String!,$group:Int!){"
                       "removeUserFromGroup(userId:$user,groupId:$group){ok}}")
            qread = ("query($id:String!){user(userId:$id)"
                     "{id groups{id displayName}}}")
            args = {"user": member["id"], "group": groups["operators"]}
            assert graphql(token, qadd, args)["addUserToGroup"]["ok"]
            current = graphql(token, qread, {"id": member["id"]})["user"]
            assert "operators" in {g["displayName"] for g in current["groups"]}
            assert graphql(token, qremove, args)["removeUserFromGroup"]["ok"]
            current = graphql(token, qread, {"id": member["id"]})["user"]
            assert not {"operators", "viewers"} & {
                g["displayName"] for g in current["groups"]
            }
            print("PASS: role grant/removal visible in LLDAP GraphQL", flush=True)

            # A duplicate login must be rejected, not silently reinterpreted.
            try:
                graphql(token, qcreate_user, {"user": {
                    "id": member["id"],
                    "displayName": "Duplicate", "email": "other@lab.invalid"
                }})
            except (RuntimeError, HTTPError):
                pass
            else:
                raise AssertionError("Directory accepted a duplicate user ID")
            print("PASS: duplicate account rejected", flush=True)

            # Exercise the real HomeAway broker against this disposable
            # directory, not just its fake-directory unit tests. CI's host
            # loopback port is substituted into a prevalidated test instance;
            # production refuses any non-private LLDAP URL.
            import importlib.util
            from pathlib import Path
            module_path = Path(__file__).resolve().parents[1] / "control/accounts/broker.py"
            spec = importlib.util.spec_from_file_location("homeaway_identity_ci", module_path)
            identity = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(identity)
            directory = object.__new__(identity.LldapDirectory)
            directory.url = BASE_URL
            directory.username = "admin"
            directory.password = admin_password
            directory._token = None

            assert identity.role_of({"groups":[]}) is None
            admin_group = graphql(token,qcreate_group,{"name":"admins"})["createGroup"]
            assert graphql(token,qadd,{"user":"admin","group":admin_group["id"]})["addUserToGroup"]["ok"]
            backend = identity.AccountBroker(directory)
            assert backend.resolve("admin")["role"]=="admins"
            created = backend.execute("admin","create",{
                "id":"labmember","name":"Synthetic Member",
                "email":"member@lab.invalid"})
            assert created["user"]["role"] is None
            assert not created["passwordReady"]
            target = directory.user("labmember")
            expected = identity.fingerprint(target)
            granted = backend.execute("admin","set-role",{
                "id":"labmember","role":"operators","revision":expected})
            assert granted["user"]["role"]=="operators"
            assert backend.resolve("labmember")["role"]=="operators"
            backend.execute("admin","revoke",{
                "id":"labmember","revision":granted["user"]["revision"]})
            assert backend.resolve("labmember")["role"] is None
            print("PASS: actual HomeAway broker create/grant/revoke against LLDAP",flush=True)

            # NOTE: A successful group removal does NOT prove access revocation
            # for an Authelia browser session with an existing 2FA cookie.
            print("LAB-ONLY: Authelia 2FA revocation is tested in separate CI; TOTP migration NOT VERIFIED",
                  flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", label],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)
            subprocess.run(["docker", "volume", "rm", "-f", volume],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)


if __name__ == "__main__":
    main()
