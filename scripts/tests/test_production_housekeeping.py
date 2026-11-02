"""Production deployment regressions found during live Pi acceptance."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from unittest import TestCase, mock

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/audit-production.py"
spec = importlib.util.spec_from_file_location("production_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ProductionHousekeeping(TestCase):
    def test_runtime_python_sources_are_readable_to_unprivileged_user(self):
        for name in (
            "control/accounts/Dockerfile",
            "control/api/Dockerfile",
            "monitoring/diagnostics/Dockerfile",
            "monitoring/pihole_exporter/Dockerfile",
            "ingest/Dockerfile",
            "portal/Dockerfile",
        ):
            with self.subTest(dockerfile=name):
                content = (ROOT / name).read_text()
                self.assertIn("USER 65534:65534", content)
                self.assertIn("COPY --chmod=0644 ", content)

    def test_live_gateway_audit_accepts_recovery_mount(self):
        mounted = str(audit.GATEWAY_RECOVERY.resolve())
        with mock.patch.object(audit.subprocess,"run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=mounted+"\n")
            ok,active = audit.check_gateway_mount()
        self.assertTrue(ok)
        self.assertEqual(active,audit.GATEWAY_RECOVERY)

    def test_live_gateway_audit_accepts_canonical_mount(self):
        mounted = str(audit.GATEWAY.resolve())
        with mock.patch.object(audit.subprocess,"run") as run:
            run.return_value = mock.Mock(returncode=0, stdout=mounted+"\n")
            ok,active = audit.check_gateway_mount()
        self.assertTrue(ok)
        self.assertEqual(active,audit.GATEWAY)

    def test_live_gateway_audit_rejects_unknown_mount(self):
        with mock.patch.object(audit.subprocess,"run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="/tmp/rogue-Caddyfile\n")
            ok,active = audit.check_gateway_mount()
        self.assertFalse(ok)
        self.assertIsNone(active)

    def test_recovery_configuration_does_not_widen_gateway_exposure(self):
        c=(ROOT/"compose.account-recovery.yaml").read_text()
        self.assertIn("account-directory", c)
        self.assertIn("infra:", c)
        self.assertIn("gw_priority: 100",c)
        self.assertNotIn("ports:",c)
        self.assertNotIn("privileged:",c)

    def test_authelia_uses_its_supported_default_config(self):
        c=(ROOT/"compose.control.yaml").read_text()
        self.assertNotIn("AUTHELIA_CONFIG:",c)
        self.assertIn("image: authelia/authelia:4.39.28",c)

    def test_gateway_mode_helper_supports_recovery_overlay(self):
        c=(ROOT/"scripts/prepare-gateway-permissions.py").read_text()
        self.assertIn("Caddyfile.account-recovery",c)
        self.assertIn("--config",c)
        self.assertIn("follow_symlinks=False",c)
