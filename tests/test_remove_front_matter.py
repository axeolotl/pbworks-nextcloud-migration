import os
import tempfile
import unittest
from pathlib import Path

from remove_front_matter import BOM, process_file, strip_front_matter


class FrontMatterTests(unittest.TestCase):
    def test_standard(self):
        data = b'---\npage: "Test"\nwiki: example\n---\n\n# Body\n'
        result, _ = strip_front_matter(data)
        self.assertEqual(result, b"\n# Body\n")

    def test_bom_crlf_and_alternative_end(self):
        data = BOM + b"---\r\npage: Test\r\n...\r\n\r\nBody\r\n"
        self.assertEqual(strip_front_matter(data)[0], BOM + b"\r\nBody\r\n")

    def test_no_header_unchanged(self):
        self.assertIsNone(strip_front_matter(b"# Body\n---\n")[0])

    def test_invalid_or_unterminated_skipped(self):
        for data in [b"---\npage: Test\nBody\n", b"---\npage: [\n---\nBody\n"]:
            self.assertIsNone(strip_front_matter(data)[0])

    def test_horizontal_rules_not_removed(self):
        self.assertIsNone(strip_front_matter(b"---\nOrdinary paragraph\n---\nBody\n")[0])

    def test_empty_header_and_end_of_file(self):
        self.assertEqual(strip_front_matter(b"---\n---")[0], b"")

    def test_preserves_time_mode_content_and_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Änderungen.md"
            data = b"---\npage: Test\n---\n\nBody  \n\n\nend\n"
            path.write_bytes(data)
            path.chmod(0o640)
            os.utime(path, ns=(1000000000000000000, 1234567890123456789))
            before = path.stat()
            self.assertEqual(process_file(path, False), "would remove")
            self.assertEqual(path.read_bytes(), data)
            # Reset access time after preview/read before testing application.
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            self.assertEqual(process_file(path, True), "removed")
            after = path.stat()
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(after.st_atime_ns, before.st_atime_ns)
            self.assertEqual(after.st_mode, before.st_mode)
            self.assertEqual(after.st_uid, before.st_uid)
            self.assertEqual(after.st_gid, before.st_gid)
            self.assertEqual(path.read_bytes(), b"\nBody  \n\n\nend\n")
            self.assertFalse(list(Path(tmp).glob(".remove-front-matter-*")))

    def test_symlink_and_hardlink_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "actual.md"
            path.write_bytes(b"---\npage: Test\n---\nBody\n")
            link = root / "link.md"
            link.symlink_to(path)
            self.assertEqual(process_file(link, True), "skip symlink")
            os.link(path, root / "hard.md")
            self.assertEqual(process_file(path, True), "skip hardlinked file")


if __name__ == "__main__":
    unittest.main()
