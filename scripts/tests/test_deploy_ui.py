"""Safe deploy-ui.sh behavior with fake git/docker; never touches real services."""
from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]

GIT_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf 'git %s\n' "$*" >> "$DEPLOY_TEST_LOG"
case "$1" in
  symbolic-ref) printf '%s\n' "${FAKE_BRANCH:-Production}" ;;
  status) if [[ "${FAKE_DIRTY:-0}" == 1 ]]; then echo "?? extra.yaml"; fi ;;
  rev-parse)
    if [[ "$*" == *"origin/Production"* ]]; then echo bbbbbbb;
    else echo aaaaaaa; fi ;;
  fetch|merge-base|diff|merge) ;;
  *) echo "Unexpected git invocation: $*" >&2; exit 89 ;;
esac
"""

SUDO_STUB = r"""#!/usr/bin/env bash
set -euo pipefail
printf 'sudo %s\n' "$*" >> "$DEPLOY_TEST_LOG"
if [[ "${1:-}" == docker && "${2:-}" == ps ]]; then
  echo gateway-test-cid
  exit 0
fi
if [[ "${1:-}" == docker && "${2:-}" == inspect ]]; then
  printf '%s\\n' "$PWD/control/gateway/Caddyfile.production"
  exit 0
fi
[[ "${1:-}" == docker && "${2:-}" == compose ]] || exit 88
if [[ "$*" == *"config --format json"* ]]; then
  printf '{"name":"%s"}\n' "${FAKE_PROJECT:-homeawayfromhome}"
elif [[ "$*" == *"config -q"* ]]; then
  :
elif [[ "$*" == *"ps --status running --quiet portal"* ]]; then
  [[ "${FAKE_STOP_PORTAL:-0}" == 1 ]] || echo portal-container
elif [[ "$*" == *"ps --status running --quiet pc-control"* ]]; then
  [[ "${FAKE_STOP_CONTROL:-0}" == 1 ]] || echo control-container
elif [[ "$*" == *"up -d --no-deps --build"* ]]; then
  :
elif [[ "$*" == *" ps portal pc-control"* || "$*" == *" ps portal"* || "$*" == *" ps pc-control"* ]]; then
  echo "Fake selected services running"
else
  echo "Unexpected Compose invocation: $*" >&2; exit 87
fi
"""

class TargetedUiDeployTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "scripts").mkdir()
        (root / "scripts/deploy-ui.sh").write_text(
            (ROOT / "scripts/deploy-ui.sh").read_text(), encoding="utf-8")
        (root / ".env").write_text("# synthetic\n", encoding="utf-8")
        for name in ("compose.yaml", "compose.control.yaml",
                     "compose.accounts.yaml", "compose.computers.yaml", "compose.production.yaml"):
            (root / name).write_text("services: {}\n", encoding="utf-8")
        (root / "control/gateway").mkdir(parents=True)
        (root / "control/gateway/Caddyfile.production").write_text(
            "# synthetic gateway configuration\\n", encoding="utf-8")
        (root / "scripts/prepare-gateway-permissions.py").write_text(
            "# synthetic no-op for deploy test\\n", encoding="utf-8")
        fake = root / "fakebin"
        fake.mkdir()
        for name, body in (("git", GIT_STUB), ("sudo", SUDO_STUB)):
            target = fake / name
            target.write_text(textwrap.dedent(body), encoding="utf-8")
            target.chmod(0o755)
        self.root = root
        self.log = root / "calls.log"
        self.env = {**os.environ,
                    "PATH": str(fake) + os.pathsep + os.environ["PATH"],
                    "DEPLOY_TEST_LOG": str(self.log)}

    def run_script(self, *args, **settings):
        env = {**self.env, **settings}
        result = subprocess.run(
            ["bash", str(self.root / "scripts/deploy-ui.sh"), *args],
            cwd=self.root, env=env, capture_output=True, text=True,
            check=False, timeout=10)
        calls = self.log.read_text() if self.log.exists() else ""
        return result, calls

    def test_plan_is_read_only_and_targets_both_ui_containers(self):
        result, calls = self.run_script("--plan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PLAN ONLY", result.stdout)
        self.assertIn("Services: portal pc-control", result.stdout)
        self.assertIn("config -q", calls)
        self.assertIn("ps --status running --quiet portal", calls)
        self.assertIn("ps --status running --quiet pc-control", calls)
        self.assertNotIn("git fetch", calls)
        self.assertNotIn(" up -d ", calls)

    def test_apply_targets_selected_service_only(self):
        result, calls = self.run_script("--apply", "--services", "portal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git fetch origin", calls)
        self.assertIn("git merge --ff-only origin/Production", calls)
        self.assertIn("up -d --no-deps --build portal", calls)
        self.assertNotIn("up -d --no-deps --build portal pc-control", calls)
        self.assertNotIn("ps --status running --quiet pc-control", calls)
        self.assertNotIn("pihole wg-easy", calls)

    def test_refuses_dirty_or_nonproduction_checkout(self):
        for settings in ({"FAKE_DIRTY": "1"}, {"FAKE_BRANCH": "feature/test"}):
            with self.subTest(settings=settings):
                self.log.unlink(missing_ok=True)
                result, calls = self.run_script("--apply", **settings)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("git fetch", calls)
                self.assertNotIn(" up -d ", calls)

    def test_refuses_to_start_disabled_optional_service(self):
        result, calls = self.run_script("--apply", "--services", "pc-control",
                                        FAKE_STOP_CONTROL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not running", result.stderr)
        self.assertNotIn("git fetch", calls)
        self.assertNotIn(" up -d ", calls)

    def test_refuses_an_unexpected_compose_project(self):
        result, calls = self.run_script("--apply", FAKE_PROJECT="other")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected Compose project", result.stderr)
        self.assertNotIn(" up -d ", calls)

    def test_rejects_ambiguous_service_or_missing_mode(self):
        for args in ((), ("--apply", "--services", "pihole"),
                     ("--plan", "--apply"), ("--apply", "--services")):
            with self.subTest(args=args):
                self.log.unlink(missing_ok=True)
                result, calls = self.run_script(*args)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn(" up -d ", calls)

if __name__ == "__main__":
    unittest.main()
