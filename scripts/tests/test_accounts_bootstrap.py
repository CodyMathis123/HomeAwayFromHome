"""First-run HomeAway directory role bootstrap should be repeatable."""
from __future__ import annotations
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS = ROOT / "control/accounts"
sys.path.insert(0, str(ACCOUNTS))
spec = importlib.util.spec_from_file_location("accounts_bootstrap_test", ACCOUNTS / "bootstrap.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class FakeDirectory:
    def __init__(self):
        self.groups = {}
        self.admin = {"id": "admin", "groups": []}
        self.created = 0
        self.memberships = 0

    def all(self):
        return [self.admin], self.groups.copy()

    def _gql(self, operation, args):
        assert "createGroup" in operation
        group = args["name"]
        assert group not in self.groups
        self.created += 1
        self.groups[group] = 10 + self.created
        return {"createGroup": {"id": self.groups[group], "displayName": group}}

    def membership(self, username, groupid, enabled):
        assert username == "admin" and enabled
        self.memberships += 1
        name = next(k for k, v in self.groups.items() if v == groupid)
        self.admin["groups"].append({"id": groupid, "displayName": name})


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_creates_roles_and_grants_protected_admin_once(self):
        directory = FakeDirectory()
        bootstrap.ensure_roles(directory)
        self.assertEqual(set(directory.groups), {"admins", "operators", "viewers"})
        self.assertEqual(directory.created, 3)
        self.assertEqual(directory.memberships, 1)
        bootstrap.ensure_roles(directory)
        self.assertEqual(directory.created, 3)
        self.assertEqual(directory.memberships, 1)

    def test_no_admin_account_refuses_to_create_groups(self):
        directory = FakeDirectory()
        directory.all = lambda: ([], {})
        with self.assertRaises(RuntimeError):
            bootstrap.ensure_roles(directory)


if __name__ == "__main__":
    unittest.main()
