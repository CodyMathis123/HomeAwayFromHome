"""Prevent core modularity boundaries from silently eroding.

These are architectural contracts, not stylistic metrics: catalog resolution
and transition decisions cannot invoke Docker, networking or external state.
Only designated lifecycle modules may change the installed application.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

PURE = ("scripts/components.py", "scripts/component_changes.py")
DATA_RENDERER = "scripts/compose_generator.py"
RUNTIME = (
    "scripts/cooling-stack.py",
    "scripts/init-cooling-standalone.py",
    "scripts/components.py",
    "scripts/component_changes.py",
    "scripts/compose_generator.py",
    "scripts/backup_service.py",
    "scripts/reconfigure_service.py",
)
DANGEROUS = (
    "docker volume prune",
    "docker system prune",
    "docker compose down -v",
    "docker compose down --volumes",
)


class DeveloperBoundaries(unittest.TestCase):
    def test_policy_modules_import_no_process_network_or_filesystem_mutators(self):
        forbidden = {
            "os", "shutil", "subprocess", "socket", "urllib", "requests",
            "docker", "tempfile", "fcntl",
        }
        for name in PURE:
            source = (ROOT / name).read_text(encoding="utf-8")
            syntax = ast.parse(source, filename=name)
            imported = set()
            for node in ast.walk(syntax):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                if isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            with self.subTest(name=name):
                self.assertFalse(imported & forbidden, imported & forbidden)
                self.assertNotIn("subprocess.", source)
                self.assertNotIn("docker compose", source.lower())

    def test_rendering_only_requires_explicit_file_writer(self):
        source = (ROOT / DATA_RENDERER).read_text(encoding="utf-8")
        syntax = ast.parse(source)
        named = {node.name: node for node in syntax.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("render", named)
        self.assertIn("write", named)
        render_source = ast.get_source_segment(source, named["render"])
        self.assertNotIn("subprocess", render_source)
        self.assertNotIn("os.replace", render_source)
        self.assertNotIn("Path.write", render_source)

    def test_no_destructive_docker_host_cleanup_in_runtime_scripts(self):
        for name in RUNTIME:
            source = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                for command in DANGEROUS:
                    self.assertNotIn(command, source)
        # Legacy full-stack scripts and CI ephemeral teardown are separate
        # operator environments; they are intentionally not within RUNTIME.

    def test_release_contains_all_runtime_dependencies(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        builder = (ROOT / "scripts/build-cooling-release.py").read_text()
        for path in RUNTIME:
            with self.subTest(path=path):
                self.assertIn(f'"{path}"', builder)


if __name__ == "__main__":
    unittest.main()
