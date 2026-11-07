"""Read-only planning, encrypted backup references and rollback contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_service
import component_changes
import components
import compose_generator
import reconfigure_service


class ComponentLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = components.load_catalog()

    def test_add_remove_are_service_scoped_and_preserve_volumes(self):
        first = component_changes.transition(
            self.catalog, ("cooling-monitoring",),
            ("cooling-monitoring", "home-portal"),
        )
        self.assertEqual(first["add_features"], ["home-portal"])
        self.assertEqual(first["remove_features"], [])
        self.assertEqual(first["add_services"], ["metrics-diagnostics", "portal"])
        self.assertEqual(first["remove_services"], [])
        self.assertEqual(len(first["retained_volumes"]), 4)
        self.assertTrue(first["requires_confirmation"])
        back = component_changes.transition(
            self.catalog, ("cooling-monitoring", "home-portal"),
            ("cooling-monitoring",),
        )
        self.assertEqual(back["remove_services"], ["metrics-diagnostics", "portal"])
        self.assertFalse(set(back["remove_services"]) & {"gateway", "victoria-metrics"})
        self.assertIn("retained", back["data_policy"])

    def test_cooling_source_change_and_invalid_selection_are_blocked(self):
        for before, after in (
            (("cooling-monitoring",), ("linux-host-monitoring",)),
            (("linux-host-monitoring",), ("cooling-monitoring",)),
            (("cooling-monitoring",), ("cooling-monitoring",)),
            (("cooling-monitoring", "home-portal"), ("home-portal",)),
        ):
            with self.subTest(before=before, after=after):
                with self.assertRaises(components.SelectionError):
                    component_changes.transition(self.catalog, before, after)

    def test_portal_secret_is_not_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "secrets").mkdir()
            with patch.object(reconfigure_service.os, "chown") as chown:
                reconfigure_service.ensure_portal_secret(state)
                path = state / "secrets/portal_password"
                secret = path.read_bytes()
                reconfigure_service.ensure_portal_secret(state)
                self.assertEqual(path.read_bytes(), secret)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                chown.assert_called_once()
            path.unlink()
            path.symlink_to(state / "untrusted")
            with self.assertRaises(components.SelectionError):
                reconfigure_service.ensure_portal_secret(state)

    def test_backups_require_valid_local_recent_reference(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config = base / "cooling.env"
            config.write_text("COOLING_COMPONENTS=cooling-monitoring\n")
            folder = base / "backups"
            folder.mkdir()
            archive = folder / "demo.tar.age"
            archive.write_bytes(b"age-encryption.org/v1\n" + b"encrypted-synthetic-only\n")
            info = {
                "project": "homeaway-cooling", "schema_version": 1,
                "created_at": int(time.time()),
                "selection": ["cooling-monitoring"],
                "archive_name": archive.name,
                "volumes": list(component_changes.PRESERVED_VOLUMES),
                "encryption": "age", "quiesced": True,
                "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                "archive_sha256": backup_service.digest_file(archive),
            }
            (folder / "demo.tar.age.json").write_text(json.dumps(info))
            backup_service.validate_reference(
                archive, config, ("cooling-monitoring",), backup_root=base
            )
            with self.assertRaises(components.SelectionError):
                backup_service.validate_reference(
                    archive, config, ("cooling-monitoring", "home-portal"),
                    backup_root=base,
                )
            info["created_at"] -= 7200
            (folder / "demo.tar.age.json").write_text(json.dumps(info))
            with self.assertRaises(components.SelectionError):
                backup_service.validate_reference(
                    archive, config, ("cooling-monitoring",), backup_root=base
                )
            info["created_at"] = int(time.time())
            (folder / "demo.tar.age.json").write_text(json.dumps(info))
            archive.write_bytes(b"corrupt")
            with self.assertRaises(components.SelectionError):
                backup_service.validate_reference(
                    archive, config, ("cooling-monitoring",), backup_root=base
                )

    def test_reconfiguration_rolls_back_after_failed_compose_up(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            state = base / "state"
            (state / "secrets").mkdir(parents=True)
            config = base / "cooling.env"
            config.write_text(
                "COOLING_BIND_IP=127.0.0.1\n"
                "COOLING_TLS_NAME=127.0.0.1\n"
                "COOLING_HTTPS_PORT=19443\n"
                f"COOLING_STATE_DIR={state}\n"
                "COOLING_COMPONENTS=cooling-monitoring\n",
                encoding="ascii",
            )
            old = ("cooling-monitoring",)
            new = ("cooling-monitoring", "linux-host-monitoring")
            previous = components.resolve(self.catalog, old)
            created = compose_generator.render(previous, config, state, ROOT)
            compose_generator.write(created, state)
            before_env = config.read_bytes()
            before_files = {name: (state / "generated" / name).read_bytes()
                            for name in compose_generator.GENERATED}
            calls = []
            running = set(previous["services"])
            attempt = [0]

            def fake_run(command, capture=False):
                nonlocal running
                calls.append(command)
                cmd = " ".join(command)
                if "ps --services --status running" in cmd:
                    return subprocess.CompletedProcess(command, 0,
                                                      "\n".join(sorted(running)) + "\n")
                if "config --services" in cmd:
                    selected = (components.parse_selection(
                        next(row.split("=", 1)[1] for row in
                             config.read_text().splitlines()
                             if row.startswith("COOLING_COMPONENTS="))
                    ))
                    graph = components.resolve(self.catalog, selected)
                    return subprocess.CompletedProcess(
                        command, 0, "\n".join(graph["services"]) + "\n"
                    )
                if "up -d --build --force-recreate --remove-orphans" in cmd:
                    attempt[0] += 1
                    if attempt[0] == 1:
                        raise subprocess.CalledProcessError(1, command)
                    running = set(previous["services"])
                return subprocess.CompletedProcess(command, 0, "")

            with patch.object(backup_service, "validate_reference"), patch.object(
                reconfigure_service.os, "geteuid", return_value=0
            ):
                with self.assertRaisesRegex(RuntimeError, "restored"):
                    reconfigure_service.change(
                        config, state, ROOT, self.catalog, old, new,
                        base / "backup.age", lambda *args: list(args), fake_run
                    )
            self.assertEqual(config.read_bytes(), before_env)
            self.assertEqual(
                {name: (state / "generated" / name).read_bytes()
                 for name in compose_generator.GENERATED},
                before_files,
            )
            self.assertEqual(attempt[0], 2)
            self.assertEqual(running, set(previous["services"]))
            self.assertEqual(len(list((state / "rollback-checkpoints").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
