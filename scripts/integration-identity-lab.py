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
from pathlib import Path
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

    with tempfile.TemporaryDirectory(prefix="homeaway-synthetic-identity-") as path:
        directory = Path(path)
        # The image runs with an unprivileged UID. This is a disposable fake
        # identity store that is destroyed at test end, never a host credential.
        directory.chmod(0o777)
        try:
            docker("run", "--detach", "--rm",
                   "--name", label,
                   "--publish", "127.0.0.1:17178:17170",
                   "--volume", f"{directory}:/data",
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

            # NOTE: A successful group removal does NOT prove access revocation
            # for an Authelia browser session with an existing 2FA cookie.
            print("LAB-ONLY: session revocation and TOTP migration NOT VERIFIED",
                  flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", label],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=False)


if __name__ == "__main__":
    main()
