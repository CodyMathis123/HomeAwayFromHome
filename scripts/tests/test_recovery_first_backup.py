"""Regression coverage: recovery backups preserve state without exporting metrics history."""
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/backup-full.sh"


class RecoveryFirstBackup(unittest.TestCase):
    def test_shell_excludes_only_victoriametrics_history(self):
        source = SCRIPT.read_text()
        self.assertIn("--exclude='data/victoria-metrics'", source)
        self.assertIn('metrics_history_included=no', source)
        self.assertIn('-C "$root" .env secrets data', source)
        self.assertIn('-C / "${host_paths[@]}"', source)
        self.assertIn("homeaway_restore_active", source)
        self.assertIn("age -r", source)
        self.assertNotIn("rm -rf -- \"$root/data/victoria-metrics\"", source)

    def test_tar_filter_keeps_recovery_state_and_omits_samples(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for name, contents in {
                ".env": "test=only",
                "secrets/wireguard_private_key": "private test string",
                "data/victoria-metrics/data/samples.bin": "disposable series",
                "data/grafana/grafana.db": "saved local configuration",
                "data/control/authelia/db.sqlite3": "auth recovery",
                "data/control/lldap/users.db": "directory recovery",
                "data/pihole/pihole.toml": "dns recovery",
                "data/wg-easy/wg0.conf": "vpn recovery",
            }.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(contents)
            tarpath = root / "sample.tar"
            subprocess.run(["tar", "--exclude=data/victoria-metrics", "-C",
                            str(root), "-cf", str(tarpath), ".env", "secrets",
                            "data"], check=True)
            with tarfile.open(tarpath) as archive:
                names = archive.getnames()
            self.assertFalse(any(x.startswith("data/victoria-metrics")
                                 for x in names), names)
            for required in ("data/control/authelia/db.sqlite3",
                             "data/control/lldap/users.db",
                             "secrets/wireguard_private_key",
                             "data/grafana/grafana.db"):
                self.assertIn(required, names)


if __name__ == "__main__":
    unittest.main()
