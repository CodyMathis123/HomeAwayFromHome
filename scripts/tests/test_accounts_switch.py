"""Offline regression tests for the reversible Authelia configuration change."""
from __future__ import annotations
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("homeaway_auth_switch", ROOT / "scripts/switch-control-auth.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ORIGINAL = """server:
  address: tcp://:9091
authentication_backend:
  file:
    path: /config/users_database.yml
identity_validation:
  reset_password:
    jwt_secret: "SYNTHETIC-JWT"
access_control:
  default_policy: deny
  rules:
    - domain: "homeaway.home.arpa"
      resources:
        - '^/control(?:/.*)?$'
      subject:
        - 'group:admins'
      policy: two_factor
session:
  secret: "SYNTHETIC-SESSION"
storage:
  encryption_key: "SYNTHETIC-ENCRYPTION"
  local:
    path: /config/db.sqlite3
"""


class SwitchTests(unittest.TestCase):
    def test_switch_keeps_sensitive_authelia_state(self):
        updated = module.to_ldap(ORIGINAL, "SYNTHETIC-PASS-NEVER-USE")
        self.assertIn('password: "SYNTHETIC-PASS-NEVER-USE"', updated)
        self.assertIn("refresh_interval: always", updated)
        self.assertIn("implementation: lldap", updated)
        self.assertIn("group:operators", updated)
        self.assertIn("group:viewers", updated)
        self.assertIn('jwt_secret: "SYNTHETIC-JWT"', updated)
        self.assertIn('secret: "SYNTHETIC-SESSION"', updated)
        self.assertIn('encryption_key: "SYNTHETIC-ENCRYPTION"', updated)
        self.assertIn("path: /config/db.sqlite3", updated)
        self.assertIn("default_policy: deny", updated)
        self.assertIn("^/control", updated)

    def test_refuses_unknown_backend_and_duplicate_rules(self):
        with self.assertRaises(ValueError):
            module.to_ldap(ORIGINAL.replace("    path: /config/users_database.yml",
                                            "    path: /wrong/path"), "x" * 40)
        with self.assertRaises(ValueError):
            module.to_ldap(ORIGINAL.replace(
                "        - 'group:admins'\n      policy: two_factor",
                "        - 'group:admins'\n        - 'group:admins'\n      policy: two_factor"
            ), "x" * 40)

    def test_refuses_repeated_backend_section(self):
        with self.assertRaises(ValueError):
            module.to_ldap(ORIGINAL + ORIGINAL.split("identity_validation:")[0], "x" * 40)


if __name__ == "__main__":
    unittest.main()
