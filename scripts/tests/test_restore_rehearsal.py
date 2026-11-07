"""Unsafe backup entries and isolation guard contracts for restore CI."""
from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rehearsal_archive
import rehearse_modular_restore


def make_tar(*, other=None, duplicate=False, symlink=False, missing=False):
    buf = io.BytesIO()
    names = [
        "./", "./config/", "./config/cooling.env",
        "./state/", "./state/secrets/", "./state/secrets/grafana_admin_password",
        "./state/generated/", "./state/generated/compose.generated.json",
        "./state/generated/scrape.generated.yml",
        "./state/generated/Caddyfile.generated", "./volumes/",
    ] + [f"./volumes/{v}/" for v in sorted(rehearsal_archive.VOLUMES)]
    if other:
        names.append(other)
    if duplicate:
        names.append("./config/cooling.env")
    with tarfile.open(fileobj=buf, mode="w:") as tar:
        for name in names:
            if missing and name == "./state/secrets/grafana_admin_password":
                name = "./state/secrets/different-secret"
            info = tarfile.TarInfo(name)
            if name.endswith("/"):
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif symlink and name == "./config/cooling.env":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/shadow"
                tar.addfile(info)
            else:
                data = b"fixture"
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class ArchiveSecurityTests(unittest.TestCase):
    def test_expected_archive(self):
        result = rehearsal_archive.process(io.BytesIO(make_tar()))
        self.assertTrue(result["validated"])
        self.assertEqual(result["files"], 5)
        self.assertEqual(result["volumes"], sorted(rehearsal_archive.VOLUMES))

    def test_traversal_and_foreign_path(self):
        for path in (
            "./config/../../etc/shadow", "/etc/passwd",
            "./volumes/cooling-metrics/../other", "./other/file",
            "./state/unknown/cmd",
        ):
            with self.subTest(path=path):
                with self.assertRaises(rehearsal_archive.UnsafeArchive):
                    rehearsal_archive.process(io.BytesIO(make_tar(other=path)))

    def test_duplicate_and_link(self):
        with self.assertRaises(rehearsal_archive.UnsafeArchive):
            rehearsal_archive.process(io.BytesIO(make_tar(duplicate=True)))
        with self.assertRaises(rehearsal_archive.UnsafeArchive):
            rehearsal_archive.process(io.BytesIO(make_tar(symlink=True)))

    def test_missing_required_secret(self):
        with self.assertRaisesRegex(rehearsal_archive.UnsafeArchive, "essential"):
            rehearsal_archive.process(io.BytesIO(make_tar(missing=True)))

    def test_rehearsal_is_disabled_outside_ci_and_volume_names_are_scoped(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"HOMEAWAY_RESTORE_REHEARSAL_CI": "0"}):
                with self.assertRaisesRegex(RuntimeError, "disabled outside"):
                    rehearse_modular_restore.rehearse(
                        Path(folder) / "missing.age", Path(folder) / "missing.key",
                        Path(folder) / "config", Path(folder), "up",
                    )
        names = rehearse_modular_restore.volumes("homeaway-restore-ci-test123")
        self.assertEqual(len(names), 6)
        self.assertTrue(all(v.startswith("homeaway-restore-ci-") for v in names.values()))
        self.assertFalse(any(v.startswith("homeaway-cooling_") for v in names.values()))


if __name__ == "__main__":
    unittest.main()
