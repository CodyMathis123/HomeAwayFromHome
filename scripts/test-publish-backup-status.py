"""Standalone tests for sanitized backup observations; no Pi paths touched."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

MODULE = Path(__file__).with_name("publish-backup-status.py")
spec = importlib.util.spec_from_file_location("homeaway_backup_status", MODULE)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_backups, self.old_dest = mod.BACKUPS, mod.DEST_DIR
        mod.BACKUPS = self.root / "archives"
        mod.DEST_DIR = self.root / "status"
        mod.BACKUPS.mkdir()
        self.addCleanup(lambda: setattr(mod, "BACKUPS", self.old_backups))
        self.addCleanup(lambda: setattr(mod, "DEST_DIR", self.old_dest))

    def archive(self, when: datetime | None = None, content=b"encrypted test bytes"):
        when = when or datetime.now(timezone.utc)
        stamp = when.strftime("%Y%m%dT%H%M%SZ")
        path = mod.BACKUPS / f"full-{stamp}-432624.tar.age"
        path.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        Path(str(path) + ".sha256").write_text(f"{sha}  {path}\n")
        return path

    def test_verified_archive_has_bounded_summary(self):
        self.archive()
        result = mod.inspect()
        self.assertTrue(result["checksumVerified"])
        self.assertEqual(result["state"], "observed")
        self.assertNotIn("full-", json.dumps(result))
        self.assertNotIn(str(mod.BACKUPS), json.dumps(result))

    def test_bad_checksum_is_not_accepted(self):
        path = self.archive()
        path.write_bytes(b"tampered")
        result = mod.inspect()
        self.assertFalse(result["checksumVerified"])
        self.assertEqual(result["state"], "unknown")

    def test_stale_valid_backup(self):
        self.archive(datetime.now(timezone.utc) - timedelta(days=10))
        result = mod.inspect()
        self.assertEqual(result["state"], "stale")
        self.assertTrue(result["checksumVerified"])

    def test_unsafe_symlink_rejected(self):
        original = self.archive()
        linked = mod.BACKUPS / "full-20261101T222713Z-1.tar.age"
        linked.symlink_to(original)
        self.assertFalse(mod.verified_archive(linked))

    def test_publish_is_sanitized_and_readable(self):
        self.archive()
        mod.publish(mod.inspect())
        path = mod.DEST_DIR / "status.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        data = json.loads(path.read_text())
        self.assertEqual(set(data), {
            "schemaVersion", "checkedAt", "state", "lastEncryptedBackupAt",
            "checksumVerified", "ageHours", "detail"
        })


if __name__ == "__main__":
    unittest.main()
