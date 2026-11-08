import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from normalize_filenames import (
    apply_plan, build_plan, collect_paths, main, normalized_name,
    load_safe_rename, rename_via_temp,
)


class FilenameTests(unittest.TestCase):
    def test_nfd_to_nfc(self):
        self.assertEqual(normalized_name("A\u0308pfel.txt".encode())[0],
                         "Äpfel.txt".encode())

    def test_utf8_unchanged_even_with_legacy_option(self):
        raw = "Öl.txt".encode()
        self.assertEqual(normalized_name(raw, "cp1252")[0], raw)

    def test_legacy_requires_explicit_encoding(self):
        with self.assertRaises(ValueError):
            normalized_name(b"\xe4pfel.txt")
        self.assertEqual(normalized_name(b"\xe4pfel.txt", "cp1252")[0],
                         "äpfel.txt".encode())

    def test_recursive_apply_preserves_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "Bu\u0308ro"
            folder.mkdir()
            path = folder / "A\u0308pfel.md"
            path.write_bytes(b"unaltered contents")
            os.utime(path, ns=(1000000000000000000, 1234567890123456789))
            original = path.stat()
            plan, issues = build_plan(collect_paths(os.fsencode(root), True), None)
            self.assertEqual(len(plan), 2)
            self.assertEqual(issues, [])
            self.assertTrue(path.exists())  # preview is non-mutating
            self.assertEqual(apply_plan(plan), 2)
            final = root / "Büro" / "Äpfel.md"
            self.assertEqual(final.read_bytes(), b"unaltered contents")
            self.assertEqual(final.stat().st_mtime_ns, original.st_mtime_ns)
            self.assertEqual(final.stat().st_uid, original.st_uid)
            self.assertEqual(final.stat().st_mode, original.st_mode)

    def test_existing_destination_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a\u0308.txt").touch()
            (root / "ä.txt").write_text("existing")
            plan, issues = build_plan(collect_paths(os.fsencode(root), False), None)
            self.assertTrue(issues)
            self.assertEqual((root / "ä.txt").read_text(), "existing")

    def test_new_destination_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.fsencode(tmp)
            # Both NFC-normalize to the same name.
            for name in ["A\u030Angstrom", "\u212Bngstrom"]:
                with open(os.path.join(root, name.encode()), "wb"):
                    pass
            _, issues = build_plan(collect_paths(root, False), None)
            self.assertTrue(issues)

    def test_atomic_no_overwrite_after_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a\u0308").write_text("source")
            plan, issues = build_plan(collect_paths(os.fsencode(root), False), None)
            self.assertFalse(issues)
            (root / "ä").write_text("arrived later")
            with self.assertRaises(FileExistsError):
                apply_plan(plan)
            self.assertEqual((root / "ä").read_text(), "arrived later")
            self.assertEqual((root / "a\u0308").read_text(), "source")

    def test_legacy_byte_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.fsencode(tmp)
            with open(os.path.join(root, b"\xf6l.txt"), "wb") as stream:
                stream.write(b"test")
            plan, issues = build_plan(collect_paths(root, False), "latin-1")
            self.assertFalse(issues)
            apply_plan(plan)
            self.assertTrue(os.path.exists(os.path.join(root, "öl.txt".encode())))

    def test_symlink_not_followed_or_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "actual").mkdir()
            (root / "a\u0308").symlink_to("actual", target_is_directory=True)
            plan, issues = build_plan(collect_paths(os.fsencode(root), True), None)
            self.assertEqual(plan, [])
            self.assertEqual(issues, [])

    def test_nested_directories_and_root_last(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Bu\u0308ro"
            nested = root / "Pla\u0308ne" / "Entwu\u0308rfe"
            nested.mkdir(parents=True)
            (nested / "U\u0308bersicht.md").write_text("content")
            (nested / "unchanged.md").write_text("unchanged")
            (root / "empty-O\u0308").mkdir()
            paths = collect_paths(os.fsencode(root), True, rename_root=True)
            # Test planning and execution order even with reversed inputs.
            plan, issues = build_plan(list(reversed(paths)), None)
            self.assertFalse(issues)
            self.assertEqual(len(plan), 5)
            self.assertEqual(plan[-1].source, os.fsencode(root))
            self.assertEqual(plan[-1].kind, "directory")
            self.assertEqual(apply_plan(list(reversed(plan))), 5)
            final = Path(tmp) / "Büro" / "Pläne" / "Entwürfe"
            self.assertEqual((final / "Übersicht.md").read_text(), "content")
            self.assertEqual((final / "unchanged.md").read_text(), "unchanged")
            self.assertTrue((Path(tmp) / "Büro" / "empty-Ö").is_dir())
            self.assertFalse(root.exists())

    def test_root_unchanged_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Bu\u0308ro"
            root.mkdir()
            (root / "A\u0308.md").touch()
            plan, issues = build_plan(collect_paths(os.fsencode(root), True), None)
            self.assertFalse(issues)
            self.assertEqual(len(plan), 1)
            apply_plan(plan)
            self.assertTrue(root.is_dir())
            self.assertTrue((root / "Ä.md").exists())

    def test_directory_collision_blocks_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Bu\u0308ro").mkdir()
            (root / "Büro").mkdir()
            _, issues = build_plan(collect_paths(os.fsencode(root), True), None)
            self.assertTrue(issues)

    def test_temporary_rename_nested_and_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Bu\u0308ro"
            nested = root / "Pla\u0308ne"
            nested.mkdir(parents=True)
            path = nested / "O\u0308l.md"
            path.write_bytes(b"content")
            original = path.stat()
            plan, issues = build_plan(
                collect_paths(os.fsencode(root), True, True), None
            )
            self.assertFalse(issues)
            self.assertEqual(apply_plan(plan, via_temp=True), 3)
            final = Path(tmp) / "Büro" / "Pläne" / "Öl.md"
            self.assertEqual(final.read_bytes(), b"content")
            self.assertEqual(final.stat().st_mtime_ns, original.st_mtime_ns)
            self.assertFalse(list(Path(tmp).rglob(".nfc-rename-*")))

    def test_staged_collision_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a\u0308"
            source.write_text("source")
            plan, _ = build_plan(collect_paths(os.fsencode(root), False), None)
            (root / "ä").write_text("destination")
            with self.assertRaisesRegex(RuntimeError, "original name restored"):
                apply_plan(plan, via_temp=True)
            self.assertEqual(source.read_text(), "source")
            self.assertEqual((root / "ä").read_text(), "destination")
            self.assertFalse(list(root.glob(".nfc-rename-*")))

    def test_distinct_hardlink_is_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a\u0308"
            source.touch()
            os.link(source, root / "ä")
            _, issues = build_plan(collect_paths(os.fsencode(root), False), None)
            self.assertTrue(issues)

    def test_simulated_normalization_insensitive_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "a\u0308"
            source.touch()
            target_bytes = os.fsencode(root / "ä")
            source_info = source.lstat()
            real_lstat = os.lstat
            real_lexists = os.path.lexists
            def fake_lstat(path, *args, **kwargs):
                if path == target_bytes:
                    return source_info
                return real_lstat(path, *args, **kwargs)
            def fake_lexists(path):
                if path == target_bytes:
                    return True
                return real_lexists(path)
            with patch("normalize_filenames.os.lstat", side_effect=fake_lstat), \
                 patch("normalize_filenames.os.path.lexists", side_effect=fake_lexists):
                plan, issues = build_plan(
                    collect_paths(os.fsencode(root), False), None
                )
            self.assertFalse(issues)
            self.assertTrue(plan[0].lookup_alias)
            # Alias-marked plans automatically use the staged method.
            apply_plan(plan)
            self.assertTrue((root / "ä").exists())


if __name__ == "__main__":
    unittest.main()
