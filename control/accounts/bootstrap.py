#!/usr/bin/env python3
"""Idempotent, private-network first-run role bootstrap for HomeAway.

Run inside the account-broker container after LLDAP is healthy:
  python3 -B /app/bootstrap.py

Only creates HomeAway role groups and grants the LLDAP built-in admin the
HomeAway admins role. Does not set passwords, affect Authelia or modify
other directory groups/users.
"""
from __future__ import annotations
import os
from pathlib import Path
from broker import LldapDirectory, ROLES, role_of


def ensure_roles(directory: LldapDirectory) -> None:
    users, groups = directory.all()
    if not any(user["id"] == "admin" for user in users):
        raise RuntimeError("LLDAP built-in admin account is missing")
    for role in ROLES:
        if role not in groups:
            result = directory._gql(
                "mutation($name:String!){createGroup(name:$name){id displayName}}",
                {"name": role},
            )
            created = result.get("createGroup") or {}
            if created.get("displayName") != role:
                raise RuntimeError("Directory did not create role: " + role)
            print("Created HomeAway role group: " + role)
    users, groups = directory.all()
    admin = next(u for u in users if u["id"] == "admin")
    if role_of(admin) != "admins":
        directory.membership("admin", int(groups["admins"]), True)
    users, groups = directory.all()
    admin = next(u for u in users if u["id"] == "admin")
    if role_of(admin) != "admins":
        raise RuntimeError("Directory did not grant the administrator role")
    print("PASS: built-in admin has HomeAway admins role")
    print("PASS: HomeAway admin/operator/viewer groups exist")


def main() -> None:
    password = Path(os.environ["HOMEAWAY_LLDAP_ADMIN_PASSWORD_FILE"]).read_text().strip()
    directory = LldapDirectory(
        os.environ.get("HOMEAWAY_LLDAP_URL", "http://lldap:17170"),
        os.environ.get("HOMEAWAY_LLDAP_USER", "admin"),
        password,
    )
    ensure_roles(directory)


if __name__ == "__main__":
    main()
