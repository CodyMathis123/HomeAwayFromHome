"""Non-destructive modularity contracts for cooling-only HomeAway proof of concept."""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.cooling-only.yaml"
CATALOG = ROOT / "deploy/components/catalog.json"
BOOTSTRAP = ROOT / "scripts/init-cooling-standalone.py"
spec = importlib.util.spec_from_file_location("cooling_bootstrap", BOOTSTRAP)
bootstrap = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bootstrap)


class CoolingModularityContracts(unittest.TestCase):
    def test_catalog_is_well_formed_and_acyclic(self):
        content = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(content["schema_version"], 1)
        components = {item["id"]: item for item in content["components"]}
        self.assertEqual(len(components), len(content["components"]))
        self.assertEqual(components["cooling-monitoring"]["status"], "prototype")
        self.assertEqual(components["cooling-monitoring"]["requires"], [])
        self.assertEqual(set(components["cooling-monitoring"]["services"]), {
            "victoria-metrics", "grafana", "telemetry-ingest", "gateway"
        })
        for item in components.values():
            for dep in item["requires"] + item["suggests"]:
                self.assertIn(dep, components, (item["id"], dep))
        visited = set()
        active = set()

        def visit(cid):
            if cid in active:
                self.fail(f"Component dependency cycle at {cid}")
            if cid in visited:
                return
            active.add(cid)
            for dep in components[cid]["requires"]:
                visit(dep)
            active.remove(cid)
            visited.add(cid)

        for cid in components:
            visit(cid)

    def test_caddy_validates_upstream_tls_and_requires_grafana_login(self):
        caddy = (ROOT / "deploy/cooling/Caddyfile").read_text(encoding="utf-8")
        self.assertIn("tls internal", caddy)
        self.assertIn("tls_trust_pool file /run/telemetry-ca.crt", caddy)
        self.assertIn("tls_server_name {$COOLING_BIND_IP}", caddy)
        self.assertIn("handle /ingest", caddy)
        self.assertNotIn("tls_insecure_skip_verify", caddy)
        config = COMPOSE.read_text(encoding="utf-8")
        self.assertIn('GF_AUTH_ANONYMOUS_ENABLED: "false"', config)
        self.assertIn('GF_SECURITY_COOKIE_SECURE: "true"', config)
        self.assertIn("GF_SECURITY_ADMIN_PASSWORD__FILE:", config)
        self.assertIn("read_only: true", config)
        self.assertIn("cooling-private:", config)
        self.assertIn("internal: true", config)
        self.assertNotIn("docker.sock", config)

    def test_scrape_does_not_require_dns_or_vpn(self):
        scrape = (ROOT / "deploy/cooling/scrape.yml").read_text(encoding="utf-8")
        self.assertIn("victoria-metrics:8428", scrape)
        self.assertNotIn("pihole", scrape.lower())
        self.assertNotIn("wg-easy", scrape.lower())
        self.assertNotIn("wireguard", scrape.lower())

    def test_initializer_plan_has_no_side_effects(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env_file = root / "deploy/cooling/.env"
            secret_dir = root / "data/cooling/secrets"
            with patch.object(bootstrap, "ENV_FILE", env_file), patch.object(
                bootstrap, "SECRET_DIR", secret_dir
            ):
                self.assertEqual(bootstrap.main(["--plan"]), 0)
                self.assertFalse(env_file.exists())
                self.assertFalse(secret_dir.exists())
                secret_dir.mkdir(parents=True)
                self.assertNotEqual(bootstrap.main(["--plan"]), 0)

    def test_initializer_requires_explicit_valid_ip_and_tty(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env_file = root / "deploy/cooling/.env"
            secret_dir = root / "data/cooling/secrets"
            with patch.object(bootstrap, "ENV_FILE", env_file), patch.object(
                bootstrap, "SECRET_DIR", secret_dir
            ), patch.object(bootstrap.os, "geteuid", return_value=0), patch.object(
                bootstrap.shutil, "which", return_value="/usr/bin/openssl"
            ), patch.object(bootstrap.sys.stdin, "isatty", return_value=False):
                self.assertNotEqual(bootstrap.main(["--init"]), 0)
                self.assertNotEqual(bootstrap.main(["--init", "--bind-ip", "0.0.0.0"]), 0)
                self.assertNotEqual(bootstrap.main(["--init", "--bind-ip", "239.1.1.1"]), 0)
                self.assertNotEqual(bootstrap.main(["--init", "--bind-ip", "127.0.0.1"]), 0)
                self.assertFalse(env_file.exists())
                self.assertFalse(secret_dir.exists())

    def test_cooling_only_compose_has_only_four_services(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker Compose not installed in this environment")
        version = subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=False
        )
        if version.returncode:
            self.skipTest("Docker Compose v2 unavailable")
        env = dict(os.environ)
        env.update(COOLING_BIND_IP="127.0.0.1", COOLING_TLS_NAME="127.0.0.1",
                   COOLING_HTTPS_PORT="9443")
        run = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE), "config", "--format", "json"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        config = json.loads(run.stdout)
        self.assertEqual(config["name"], "homeaway-cooling")
        self.assertEqual(set(config["services"]), {
            "victoria-metrics", "grafana", "telemetry-ingest", "gateway"
        })
        self.assertEqual(config["networks"]["cooling-private"]["internal"], True)
        self.assertEqual(
            {name for name, service in config["services"].items() if service.get("ports")},
            {"gateway"},
        )
        exposed = config["services"]["gateway"]["ports"][0]
        self.assertEqual(exposed["host_ip"], "127.0.0.1")
        self.assertEqual(str(exposed["published"]), "9443")
        for service in ("telemetry-ingest", "grafana", "victoria-metrics"):
            self.assertEqual(config["services"][service].get("ports", []), [])


if __name__ == "__main__":
    unittest.main()
