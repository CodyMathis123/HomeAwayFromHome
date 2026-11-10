"""Guardrails for two-runner clean-host recovery testing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recover_clean_host_ci as recovery
from component_changes import PRESERVED_VOLUMES


class RecoveryContracts(unittest.TestCase):
    def test_disabled_outside_disposable_ci(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "synthetic.tar.age"
            identity = Path(tmp) / "key.txt"
            with patch.dict(os.environ, {
                "HOMEAWAY_CLEAN_RESTORE_CI": "1", "GITHUB_ACTIONS": "false"
            }):
                with self.assertRaisesRegex(RuntimeError, "GitHub Actions"):
                    recovery.validate_synthetic_backup(archive, identity)

    def test_manifest_only_synthetic_and_checksum_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "synthetic.tar.age"
            key = root / "identity.txt"
            archive.write_bytes(b"age-encryption.org/v1\n" + b"fake encrypted payload")
            key.write_text("fake test key")
            metadata = {
                "schema_version": 1, "project": "homeaway-cooling",
                "archive_name": archive.name, "encryption": "age",
                "quiesced": True, "volumes": list(PRESERVED_VOLUMES),
                "selection": ["cooling-monitoring"],
                "created_at": int(time.time()),
                "config_sha256": hashlib.sha256(b"test").hexdigest(),
                "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
            manifest = root / "synthetic.tar.age.json"
            manifest.write_text(json.dumps(metadata))
            with patch.dict(os.environ, {
                "HOMEAWAY_CLEAN_RESTORE_CI": "1", "GITHUB_ACTIONS": "true",
            }):
                self.assertEqual(
                    recovery.validate_synthetic_backup(archive, key)["project"],
                    "homeaway-cooling",
                )
                metadata["selection"] = ["vpn"]
                manifest.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    recovery.validate_synthetic_backup(archive, key)
                metadata["selection"] = ["cooling-monitoring"]
                manifest.write_text(json.dumps(metadata))
                archive.write_bytes(b"corrupted")
                with self.assertRaises(ValueError):
                    recovery.validate_synthetic_backup(archive, key)

    def test_recovered_config_is_scoped_to_new_state_and_loopback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "state"
            state.mkdir()
            source = root / "source.env"
            source.write_text(
                "COOLING_BIND_IP=127.0.0.1\nCOOLING_TLS_NAME=127.0.0.1\n"
                "COOLING_HTTPS_PORT=19443\n"
                "COOLING_STATE_DIR=/var/lib/homeaway-cooling\n"
                "COOLING_COMPONENTS=cooling-monitoring\n",
                encoding="ascii",
            )
            new = root / "recovery.env"
            recovery.make_recovered_config(source, new, state)
            text = new.read_text()
            self.assertIn(f"COOLING_STATE_DIR={state}", text)
            self.assertIn("COOLING_HTTPS_PORT=19444", text)
            self.assertNotIn("COOLING_HTTPS_PORT=19443", text)
            self.assertEqual(new.stat().st_mode & 0o777, 0o600)
            source.write_text(source.read_text().replace(
                "COOLING_BIND_IP=127.0.0.1", "COOLING_BIND_IP=0.0.0.0"))
            with self.assertRaisesRegex(RuntimeError, "localhost"):
                recovery.make_recovered_config(source, new, state)

    def test_generated_recovery_compose_has_only_external_scratch_volumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            state = target / "state"
            state.mkdir()
            source = target / "source.env"
            source.write_text(
                "COOLING_BIND_IP=127.0.0.1\nCOOLING_TLS_NAME=127.0.0.1\n"
                "COOLING_HTTPS_PORT=19443\n"
                "COOLING_STATE_DIR=/var/lib/homeaway-cooling\n"
                "COOLING_COMPONENTS=cooling-monitoring\n",
                encoding="ascii",
            )
            config = target / "recovery.env"
            recovery.make_recovered_config(source, config, state)
            mapping = recovery.volumes("homeaway-clean-ci-test-example")
            code_root = Path(__file__).resolve().parents[2]
            definition, plan = recovery.make_recovered_compose(
                code_root, config, state,
                "homeaway-clean-ci-test-app", mapping,
            )
            project = json.loads(definition.read_text())
            self.assertEqual(project["name"], "homeaway-clean-ci-test-app")
            self.assertEqual(set(project["services"]), recovery.EXPECTED_SERVICES)
            for item, meta in project["volumes"].items():
                self.assertTrue(meta["external"])
                self.assertEqual(meta["name"], mapping["volumes/" + item])
                self.assertFalse(meta["name"].startswith("homeaway-cooling_"))
            listeners = [
                service for service, value in project["services"].items()
                if value.get("ports")
            ]
            self.assertEqual(listeners, ["gateway"])
            self.assertEqual(project["services"]["gateway"]["ports"][0]["host_ip"],
                             "127.0.0.1")
            self.assertEqual(project["services"]["gateway"]["ports"][0]["published"],
                             recovery.PORT)
            self.assertTrue(project["networks"]["cooling-private"]["internal"])

    def test_restore_orchestrator_is_excluded_from_shipped_packages(self):
        builder = (Path(__file__).resolve().parents[2] /
                   "scripts/build-cooling-release.py").read_text()
        self.assertNotIn('"scripts/recover_clean_host_ci.py"', builder)
        self.assertNotIn('"scripts/rehearse_modular_restore.py"', builder)


if __name__ == "__main__":
    unittest.main()
