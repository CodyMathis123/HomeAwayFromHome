"""No-hardware, Linux-only tests of the shared PC inventory transaction layer."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "control/pcctl"))

import inventory_store as store  # noqa: E402
from core import load_hosts  # noqa: E402


def host(i: int) -> dict:
    return {
        "id": f"computer-{i}",
        "name": f"Computer {i}",
        "address": f"192.168.50.{10 + i}",
        "mac": f"02:10:20:30:40:{i:02x}",
        "broadcast": "192.168.50.255",
        "ssh_user": "pcctl",
        "ssh_port": 22,
    }


def append(document: dict, entry: dict) -> dict:
    document["hosts"].append(entry)
    return document


class InventoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "hosts.json"

    def write(self, document, mode=0o640):
        self.path.write_text(json.dumps(document) + "\n")
        self.path.chmod(mode)

    def test_serialized_updates_preserve_other_hosts_and_rollback_snapshot(self):
        first, rev1 = store.update_inventory(self.path, lambda d: append(d, host(1)))
        self.assertEqual([x["id"] for x in first["hosts"]], ["computer-1"])
        second, rev2 = store.update_inventory(self.path, lambda d: append(d, host(2)),
                                              expected_revision=rev1)
        self.assertNotEqual(rev1, rev2)
        self.assertEqual(set(load_hosts(str(self.path))), {"computer-1", "computer-2"})
        previous = self.path.with_name("hosts.json.previous")
        self.assertEqual([x["id"] for x in json.loads(previous.read_text())["hosts"]],
                         ["computer-1"])

    def test_no_stale_revision_can_overwrite_newer_inventory(self):
        _, prior = store.update_inventory(self.path, lambda d: append(d, host(1)))
        store.update_inventory(self.path, lambda d: append(d, host(2)))
        original = self.path.read_bytes()
        with self.assertRaises(store.InventoryConflict):
            store.update_inventory(self.path, lambda d: append(d, host(3)),
                                   expected_revision=prior)
        self.assertEqual(self.path.read_bytes(), original)

    def test_validating_candidate_uses_exact_live_pcctl_rules(self):
        self.write({"hosts": [host(1)]})
        original = self.path.read_bytes()
        for payload in (
            {**host(2), "id": "evil;command"},
            {**host(2), "address": "8.8.8.8"},
            {**host(2), "ssh_user": "root && id"},
            {**host(2), "mac": "not a mac"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(store.InventoryStoreError):
                    store.update_inventory(self.path, lambda d: append(d, payload))
                self.assertEqual(self.path.read_bytes(), original)

    def test_existing_corruption_is_never_overwritten(self):
        self.path.write_text('{"hosts": [broken}')
        original = self.path.read_bytes()
        with self.assertRaises(store.InventoryStoreError):
            store.update_inventory(self.path, lambda d: append(d, host(1)))
        self.assertEqual(self.path.read_bytes(), original)

    def test_inventory_file_owner_and_mode_preserved(self):
        self.write({"hosts": [host(1)]}, 0o640)
        before = self.path.stat()
        store.update_inventory(self.path, lambda d: append(d, host(2)))
        after = self.path.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), 0o640)
        self.assertEqual((before.st_uid, before.st_gid), (after.st_uid, after.st_gid))

    def test_symlinked_inventory_and_lock_are_rejected(self):
        real = self.path.with_name("other.json")
        real.write_text(json.dumps({"hosts": [host(1)]}))
        self.path.symlink_to(real)
        with self.assertRaises(store.InventoryStoreError):
            store.update_inventory(self.path, lambda d: append(d, host(2)))
        self.path.unlink()
        lock = self.path.with_name(".hosts.json.lock")
        lock.symlink_to(real)
        with self.assertRaises(store.InventoryStoreError):
            store.update_inventory(self.path, lambda d: append(d, host(2)))
        self.assertFalse(self.path.exists())

    def test_symlinked_parent_directory_is_rejected(self):
        link = Path(self.temp.name) / "link"
        real_dir = Path(self.temp.name) / "real"
        real_dir.mkdir()
        link.symlink_to(real_dir, target_is_directory=True)
        with self.assertRaises(store.InventoryStoreError):
            store.update_inventory(link / "hosts.json",
                                   lambda d: append(d, host(1)))
        self.assertFalse((real_dir / "hosts.json").exists())

    def test_failed_replace_keeps_valid_file(self):
        self.write({"hosts": [host(1)]})
        original = self.path.read_bytes()
        with patch.object(store.os, "replace", side_effect=OSError("simulated disk error")):
            with self.assertRaises(OSError):
                store.update_inventory(self.path, lambda d: append(d, host(2)))
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(sorted(p.name for p in self.path.parent.glob(".hosts.json.*")),
                         [".hosts.json.lock"])

    def test_concurrent_cooperating_writers_do_not_lose_entries(self):
        # One lock for the whole read/validate/replace cycle, not just write.
        def put(i):
            store.update_inventory(self.path, lambda d: append(d, host(i)))
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(put, range(1, 21)))
        self.assertEqual(len(load_hosts(str(self.path))), 20)

    def test_no_change_does_not_write_file(self):
        self.write({"hosts": [host(1)]})
        original = self.path.stat().st_ino
        previous = self.path.with_name("hosts.json.previous")
        result, rev = store.update_inventory(self.path, lambda d: d)
        self.assertEqual(result["hosts"][0]["id"], "computer-1")
        self.assertEqual(rev, store.revision_of(result))
        self.assertEqual(self.path.stat().st_ino, original)
        self.assertFalse(previous.exists())

    def test_oversized_inventory_is_rejected(self):
        self.path.write_bytes(b" " * (store.MAX_INVENTORY_BYTES + 1))
        with self.assertRaises(store.InventoryStoreError):
            store.update_inventory(self.path, lambda d: append(d, host(1)))


if __name__ == "__main__":
    unittest.main()
