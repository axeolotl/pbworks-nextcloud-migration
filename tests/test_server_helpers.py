import json
import tempfile
import unittest
from pathlib import Path

from server import patch
from finalize import finalize
from check_bundle import safe_path


class ServerSafetyTests(unittest.TestCase):
    def test_unknown_importer_refused_without_changes(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d)
            target = app / "lib/Service/ImportService.php"
            target.parent.mkdir(parents=True)
            target.write_text("<?php // unknown upstream revision\n")
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "Unknown"):
                patch(app, "patch", True, "php")
            self.assertEqual(before, target.read_bytes())
            self.assertFalse(target.with_suffix(".php.pbworks-backup").exists())

    def test_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                safe_path(Path(d), "../outside")

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x").symlink_to("/etc/hosts")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                safe_path(root, "x")

    def test_restore_requires_backup(self):
        with tempfile.TemporaryDirectory() as d:
            app = Path(d)
            target = app / "lib/Service/ImportService.php"
            target.parent.mkdir(parents=True)
            target.write_text("<?php\n")
            with self.assertRaisesRegex(ValueError, "No patch backup"):
                patch(app, "restore", True, "php")
