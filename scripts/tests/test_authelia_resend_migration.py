"""Authelia SMTP migration must preserve unrelated auth settings and secrets."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/"scripts/prepare-authelia-smtp.py"
spec=importlib.util.spec_from_file_location("authelia_smtp_prep",SCRIPT)
migration=importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

class AutheliaResendMigration(unittest.TestCase):
    def test_removes_only_filesystem_subtree(self):
        original=('server:\n  address: tcp://0.0.0.0:9091\n'
                  'notifier:\n  disable_startup_check: false\n'
                  '  filesystem:\n    filename: /config/notification.txt\n'
                  'session:\n  cookies: []\n')
        result=migration.prepare(original)
        self.assertNotIn('filesystem:',result)
        self.assertNotIn('notification.txt',result)
        self.assertIn('notifier:\n  disable_startup_check: false',result)
        self.assertIn('session:\n  cookies: []',result)
        self.assertIn('server:\n  address:',result)

    def test_fails_closed_on_unknown_notifier_shape(self):
        samples=(
            'notifier:\n  smtp:\n    address: smtp://localhost:25\n',
            'notifier:\n  filesystem:\n    filename: /tmp/f\n'
            '  smtp:\n    address: smtp://localhost\n',
            'notifier:\n  filesystem:\n    strange: true\n',
            'notifier:\n  filesystem:\n    filename: /config/notification.txt\n'
            '  filesystem:\n    filename: /tmp/other\n',
            'notifier:\n  filesystem:\n  filename: /tmp/file\n',
            'something: 123\n',
        )
        for sample in samples:
            with self.subTest(sample=sample),self.assertRaises(ValueError):
                migration.prepare(sample)

    def test_overlay_uses_file_secret_and_isolated_egress(self):
        conf=(ROOT/"compose.authelia-smtp.yaml").read_text()
        self.assertIn("AUTHELIA_NOTIFIER_SMTP_PASSWORD_FILE",conf)
        self.assertIn("submissions://smtp.resend.com:465",conf)
        self.assertIn("gw_priority: 100",conf)
        self.assertIn("authelia-mail-egress",conf)
        self.assertNotIn("ports:",conf)
        self.assertNotIn("privileged:",conf)
        self.assertNotIn("AUTHELIA_NOTIFIER_FILESYSTEM_FILENAME",conf)
        self.assertNotIn("AUTHELIA_NOTIFIER_SMTP_PASSWORD:",conf)

if __name__=="__main__":
    unittest.main()
