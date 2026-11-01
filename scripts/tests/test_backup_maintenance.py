"""Offline Phase 4 maintenance checker tests: no Docker, systemd or backup access."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("maintenance_for_tests",
    ROOT / "scripts/backup-maintenance.py")
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


class FakeResult:
    def __init__(self, code):
        self.returncode = code


class MaintenanceTests(unittest.TestCase):
    def test_read_only_commands_and_schedule_state(self):
        calls = []
        def runner(args, **kwargs):
            calls.append(list(args))
            return FakeResult(0)
        status = maintenance.run_checks(runner)
        self.assertEqual(status["mode"], "preflight-only")
        self.assertEqual([c["state"] for c in status["checks"]], ["ok", "ok"])
        self.assertTrue(status["scheduleEnabled"])
        self.assertEqual(len(calls), 3)
        self.assertIn("--plan", calls[0])
        self.assertIn("--plan", calls[1])
        self.assertNotIn("--apply", " ".join(map(str, calls)))
        self.assertNotIn("enable", " ".join(map(str, calls)))
        self.assertNotIn("delete", " ".join(map(str, calls)))

    def test_fail_closed_without_exporting_output(self):
        def runner(args, **kwargs):
            return FakeResult(0 if "backup-full.sh" in " ".join(args) else 1)
        status = maintenance.run_checks(runner)
        self.assertEqual([c["state"] for c in status["checks"]], ["ok", "attention"])
        self.assertFalse(status["scheduleEnabled"])
        self.assertNotIn("secret", json.dumps(status))

    def test_publish_atomic_and_no_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(maintenance, "STATUS_DIR", root), \
                 patch.object(maintenance, "STATUS_FILE", root / "maintenance.json"), \
                 patch.object(maintenance.os, "geteuid", return_value=0):
                result = maintenance.run_checks(lambda *a, **kw: FakeResult(1))
                # Simulate unsafe ownership regardless of CI runner UID.
                from unittest.mock import Mock
                stat_result = Mock(st_uid=1000, st_mode=0o755)
                with patch.object(maintenance.Path, "stat", return_value=stat_result):
                    with self.assertRaises(RuntimeError):
                        maintenance.publish(result)

    def test_unit_definitions_never_run_backup(self):
        service = (ROOT / "systemd/homeaway-backup-preflight.service").read_text()
        timer = (ROOT / "systemd/homeaway-backup-preflight.timer").read_text()
        installer = (ROOT / "scripts/install-backup-preflight.sh").read_text()
        self.assertIn("backup-maintenance.py --publish", service)
        self.assertNotIn("backup-full.sh", service)
        self.assertIn("Sun *-*-* 03:00:00", timer)
        self.assertNotIn("\\nsystemctl enable", installer)


if __name__ == "__main__":
    unittest.main()
