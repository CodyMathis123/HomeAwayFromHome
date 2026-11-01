"""Static guardrails for the offline backup's Compose service selection.

No Docker daemon, real volumes, secrets or service downtime involved.
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class BackupScopeTests(unittest.TestCase):
    def test_all_running_homeaway_containers_included(self):
        backup = (ROOT / "scripts/backup.sh").read_text()
        self.assertIn("docker compose --profile metrics config --format json", backup)
        self.assertIn("label=com.docker.compose.project=", backup)
        self.assertIn("docker ps", backup)
        self.assertIn('docker stop --time 60 "${running[@]}"', backup)
        self.assertIn('docker start "$container"', backup)
        # Old base-only Compose commands silently left optional auth running.
        self.assertNotIn("docker compose --profile metrics stop", backup)
        self.assertNotIn("docker compose --profile metrics ps --services", backup)

    def test_backup_failure_restores_services_and_reports_errors(self):
        backup = (ROOT / "scripts/backup.sh").read_text()
        self.assertIn("trap cleanup EXIT", backup)
        self.assertIn("restore_required=1", backup)
        self.assertIn("docker inspect --format '{{.State.Running}}'", backup)
        self.assertIn("restart_containers || original_status=1", backup)
        self.assertIn('tar -czf "$temp" -- .env secrets data', backup)

    def test_plan_mode_exits_before_stop(self):
        backup = (ROOT / "scripts/backup.sh").read_text()
        self.assertIn('if [[ "$mode" == "--plan" ]]; then', backup)
        self.assertLess(backup.index('if [[ "$mode" == "--plan" ]]; then'),
                        backup.index("docker stop --time 60"))
        self.assertIn("No containers stopped; no archive created.", backup)

if __name__ == "__main__":
    unittest.main()
