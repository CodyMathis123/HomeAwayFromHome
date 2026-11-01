"""Offline, unprivileged tests for the actual Pi installation preflight."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "control/pcctl/core.py"
SCRIPT = ROOT / "scripts/preflight-pcinventory-host.py"

spec = importlib.util.spec_from_file_location("test_pc_inventory_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def entry():
    return {
        "id": "gaming", "name": "Gaming PC",
        "address": "192.168.50.12", "broadcast": "192.168.50.255",
        "mac": "02:11:22:33:44:55",
        # Old pcctl configurations may omit ssh_user/ssh_port.
    }


class PreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.ssh = self.root / "ssh"
        self.ssh.mkdir(mode=0o700)
        self.key = self.ssh / "id_ed25519"
        self.key.write_text("fake - do not use to authenticate")
        self.key.chmod(0o600)
        self.known = self.ssh / "known_hosts"
        self.known.write_text("placeholder public host keys")
        self.known.chmod(0o644)
        self.inventory = self.root / "hosts.json"
        self.inventory.write_text(json.dumps({"hosts": [entry()]}))
        self.inventory.chmod(0o644)

    def run_preflight(self):
        return preflight.preflight(inventory=self.inventory, core=CORE,
                                   ssh_dir=self.ssh, pcctl_uid=os.getuid())

    def test_plan_validates_host_without_writing_any_file(self):
        before = (self.inventory.read_bytes(), self.key.read_bytes(),
                  self.known.read_bytes())
        count, messages = self.run_preflight()
        self.assertEqual(count, 1)
        self.assertTrue(messages[0].startswith("PASS: 1 existing active computer(s)"))
        self.assertNotIn("192.168.50.12", "\n".join(messages))
        self.assertEqual((self.inventory.read_bytes(), self.key.read_bytes(),
                          self.known.read_bytes()), before)

    def test_missing_known_hosts_is_pending_not_an_excuse_to_trust_on_first_use(self):
        self.known.unlink()
        _, messages = self.run_preflight()
        self.assertTrue(any("PENDING: known_hosts absent" in m for m in messages))
        self.assertFalse(self.known.exists())

    def test_missing_ssh_key_keeps_wol_preview_available(self):
        self.key.unlink()
        _, messages = self.run_preflight()
        self.assertTrue(any("PENDING: id_ed25519 absent" in m for m in messages))
        self.assertFalse(self.key.exists())

    def test_symlinked_inventory_rejected_without_following_target(self):
        original = self.inventory.read_bytes()
        self.inventory.unlink()
        source = self.root / "source"
        source.write_bytes(original)
        self.inventory.symlink_to(source)
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()
        self.assertEqual(source.read_bytes(), original)

    def test_corrupt_inventory_and_duplicate_ids_rejected(self):
        for document in (
            "this is not JSON",
            '{"hosts":[{"id":"bad"}]}',
            json.dumps({"hosts": [entry(), entry()]}),
            json.dumps({"hosts": [{**entry(), "address": "8.8.8.8"}]}),
        ):
            with self.subTest(document=document):
                self.inventory.write_text(document)
                with self.assertRaises(preflight.PreflightError):
                    self.run_preflight()

    def test_unsafe_inventory_permissions_and_hardlink_rejected(self):
        self.inventory.chmod(0o666)
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()
        self.inventory.chmod(0o644)
        clone = self.root / "same-inode"
        os.link(self.inventory, clone)
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()

    def test_bad_ssh_key_ownership_or_permissions_block_preview(self):
        self.key.chmod(0o644)
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()
        self.key.chmod(0o600)
        with patch.object(preflight.os, "getuid", return_value=os.getuid()):
            _, messages = self.run_preflight()
        self.assertNotIn("PENDING", "\n".join(messages))

    def test_symlink_known_hosts_is_not_treated_as_readable_pin(self):
        self.known.unlink()
        target = self.root / "outside"
        target.write_text("not a real pin")
        self.known.symlink_to(target)
        with self.assertRaises(preflight.PreflightError):
            self.run_preflight()
        self.assertEqual(target.read_text(), "not a real pin")

    def test_installer_smoke_and_rollback_shell_syntax(self):
        scripts = (
            "scripts/install-pcinventory-host.sh",
            "scripts/verify-pcinventory-host.sh",
            "scripts/disable-pcinventory-host.sh",
        )
        for path in scripts:
            with self.subTest(script=path):
                result = subprocess.run(
                    ["bash", "-n", str(ROOT / path)],
                    text=True, capture_output=True, timeout=5, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_disable_plan_is_non_mutating_and_never_stops_power_socket(self):
        path = ROOT / "scripts/disable-pcinventory-host.sh"
        result = subprocess.run(["bash", str(path), "--plan"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN: no services changed", result.stdout)
        script = path.read_text()
        self.assertNotIn('systemctl stop pcctl-agent', script)
        self.assertNotIn('systemctl disable --now pcctl-agent', script)
        self.assertIn('HOMEAWAY_COMPUTER_ADMIN_ENABLED', script)
        self.assertIn('homeaway-inventory.socket homeaway-pcverify.socket', script)
        self.assertNotIn("rm -rf /var/lib/homeaway-pcinventory", script)

    def test_live_smoke_uses_unprivileged_read_only_and_invalid_verifier(self):
        script = (ROOT / "scripts/verify-pcinventory-host.sh").read_text()
        self.assertIn('runuser -u homeaway-inventory', script)
        self.assertIn('"operation": "list"', script)
        self.assertIn('"operation": "invalid-smoke-operation"', script)
        self.assertNotIn('"operation": "activate"', script)
        self.assertNotIn('"operation": "disable"', script)
        self.assertNotIn('"operation": "wake"', script)
        self.assertNotIn('"operation": "shutdown"', script)
        self.assertNotIn("systemctl restart", script)

    def test_preflight_is_called_before_mutating_installer_steps(self):
        text = (ROOT / "scripts/install-pcinventory-host.sh").read_text()
        check = text.index('python3 -B "$root/scripts/preflight-pcinventory-host.py"')
        mutate = text.index('useradd --system --user-group')
        self.assertLess(check, mutate)
        self.assertIn('if [[ "$mode" == "--plan" ]]', text)
        self.assertIn("DRY RUN: preflight passed", text)


if __name__ == "__main__":
    unittest.main()
