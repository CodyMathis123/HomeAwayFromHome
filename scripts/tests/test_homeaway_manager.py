"""Safety contracts for the operator-side HomeAway command."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("homeaway_manager", ROOT / "scripts/homeaway.py")
manager = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(manager)


class OperatorCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / "scripts").mkdir()
        for name in ("compose.yaml", "scripts/init.sh", "scripts/deploy-ui.sh"):
            path = self.repo / name
            path.touch()
        root = patch.object(manager, "ROOT", self.repo)
        login = patch.object(manager.os, "geteuid", return_value=1000)
        root.start()
        login.start()
        self.addCleanup(root.stop)
        self.addCleanup(login.stop)

    def test_launcher_installer_rejects_missing_or_invalid_arguments(self):
        installer = ROOT / "scripts/install-homeaway-command.sh"
        for arguments in ([], ["--unknown"], ["--plan", "--apply"]):
            result = subprocess.run(
                ["bash", str(installer), *arguments],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2, arguments)
            self.assertIn("Usage:", result.stderr)

    def test_plans_default_to_read_only(self):
        with patch.object(manager, "execute") as run:
            self.assertEqual(manager.main(["install"]), 0)
            run.assert_not_called()
            self.assertEqual(manager.main(["update", "--target", "dashboards"]), 0)
            run.assert_called_once_with(
                "bash", "scripts/deploy-ui.sh", "--plan", "--services", "grafana")
            run.reset_mock()
            self.assertEqual(manager.main(["backup"]), 0)
            run.assert_called_once_with(
                "sudo", "bash", "scripts/backup-full.sh", "--plan")

    def test_mutation_requires_tty(self):
        with patch.object(manager, "execute") as run, patch.object(
            manager.sys, "stdin", io.StringIO("")
        ):
            self.assertNotEqual(manager.main(["update", "--apply"]), 0)
            self.assertNotEqual(
                manager.main(["backup", "--apply", "--recipient", "age1" + "x" * 58]), 0)
            self.assertNotEqual(manager.main(["install", "--apply"]), 0)
            run.assert_not_called()

    def test_update_applies_only_after_typed_confirmation(self):
        class Tty:
            def isatty(self):
                return True

        with patch.object(manager.sys, "stdin", Tty()), patch(
            "builtins.input", return_value="UPDATE DASHBOARDS"
        ), patch.object(manager, "execute") as run:
            self.assertEqual(manager.main(["update", "--target", "dashboards", "--apply"]), 0)
            run.assert_called_once_with(
                "bash", "scripts/deploy-ui.sh", "--apply", "--services", "grafana")

    def test_wrong_confirmation_does_not_apply(self):
        class Tty:
            def isatty(self):
                return True

        with patch.object(manager.sys, "stdin", Tty()), patch(
            "builtins.input", return_value="no"
        ), patch.object(manager, "execute") as run:
            self.assertNotEqual(manager.main(["update", "--apply"]), 0)
            run.assert_not_called()

    def test_existing_install_is_not_reinitialized(self):
        (self.repo / ".env").write_text("test")  # test-only sentinel
        with patch.object(manager, "execute") as run:
            self.assertEqual(manager.main(["install"]), 0)
            self.assertNotEqual(manager.main(["install", "--apply"]), 0)
            run.assert_not_called()

    def test_partial_existing_state_is_protected(self):
        (self.repo / "secrets").mkdir()
        with patch.object(manager, "execute") as run:
            self.assertNotEqual(manager.main(["install", "--apply"]), 0)
            run.assert_not_called()

    def test_core_updates_are_never_auto_deployed(self):
        with patch.object(manager, "execute") as run:
            self.assertEqual(manager.main(["update", "--target", "core"]), 0)
            self.assertNotEqual(manager.main(["update", "--target", "core", "--apply"]), 0)
            run.assert_not_called()

    def test_backup_recipient_required_for_apply(self):
        with patch.object(manager, "execute") as run:
            self.assertNotEqual(manager.main(["backup", "--apply"]), 0)
            self.assertNotEqual(manager.main(["backup", "--recipient", "age1" + "x" * 58]), 0)
            run.assert_not_called()

    def test_management_does_not_require_root_account(self):
        with patch.object(manager.os, "geteuid", return_value=0):
            with patch.object(manager, "execute") as run:
                self.assertNotEqual(manager.main(["status"]), 0)
                run.assert_not_called()

    def test_dashboard_deploy_is_explicit_single_service(self):
        script = (ROOT / "scripts/deploy-ui.sh").read_text(encoding="utf-8")
        self.assertIn('portal|pc-control|grafana) services=("$selection")', script)
        self.assertIn('up -d --no-deps --build "${services[@]}"', script)
        self.assertIn('check_gateway', script)
        self.assertIn('config -q', script)


if __name__ == "__main__":
    unittest.main()
