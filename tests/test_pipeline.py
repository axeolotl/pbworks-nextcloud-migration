import json
import os
import tempfile
import unittest
from pathlib import Path

from prepare import prepare, FileResolver, copy_normalized
from check_bundle import validate
from finalize import finalize


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pages, self.files = self.root / "pages", self.root / "files"
        self.pages.mkdir()
        self.files.mkdir()
        self.out = self.root / "bundle"

    def write(self, root, rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def fixture(self):
        self.write(self.pages, "Unter/Seite.html",
                   '<!-- page=Seite --><a href="/w/page/123/Start">Start</a>')
        self.write(self.pages, "Start.html",
                   '<!-- page=Start --><h1>Start</h1>'
                   '<a href="/w/page/123/Seite">Unterseite</a>'
                   '<a href="/w/file/fetch/4/Protokoll%20neu.pdf">PDF</a>'
                   '<img src="/f/Ecke%20neu.png" alt="Ecke">'
                   '<img src="/f/Doppelt.png"><a href="/f/Doppelt.png">Bilddatei</a>'
                   '<p>Umlaute äöü bleiben erhalten.</p>')
        for name in ["Protokoll neu.pdf", "Ecke neu.png", "Doppelt.png", "unreferenziert.zip"]:
            self.write(self.files, name, "test data " + name)

    def test_partition_finalize_and_mtimes(self):
        self.fixture()
        stamp = 1600000000123456789
        os.utime(self.pages / "Start.html", ns=(stamp, stamp))
        original = (self.pages / "Start.html").read_bytes()
        m, r = prepare(self.pages, self.files, self.out)
        self.assertEqual(r["errors"], [])
        self.assertEqual(m["counts"], {"pages": 2, "attachments": 2, "team_files": 3, "both": 1})
        self.assertNotIn("Ecke neu.png", m["team_files"])
        self.assertIn("unreferenziert.zip", m["team_files"])
        self.assertEqual((self.pages / "Start.html").read_bytes(), original)
        self.assertEqual((self.out / "collective/Start.md").stat().st_mtime_ns, stamp)
        self.assertFalse((self.out / "collective/Start.md").read_text().startswith("---"))
        self.assertIn("../media/Ecke%20neu.png", (self.out / "collective/Start.md").read_text())
        validate(self.out)
        index = self.out / "files-index.json"
        index.write_text(json.dumps({"complete": True, "files": {
            rel: {"id": i + 100, "sha256": rec["sha256"]}
            for i, (rel, rec) in enumerate(m["team_files"].items())}}))
        dest = finalize(self.out, index, "https://cloud.example.org/nextcloud")
        text = (dest / "Start.md").read_text()
        self.assertNotIn("migration.invalid", text)
        self.assertIn("https://cloud.example.org/nextcloud/f/", text)
        self.assertEqual((dest / "Start.md").stat().st_mtime_ns, stamp)

    def test_normalization_nfd(self):
        self.write(self.pages, "Sta\u0308rt.html", "<h1>Hallo</h1>")
        self.write(self.files, "Do\u0308k/file.bin", "x")
        m, r = prepare(self.pages, self.files, self.out)
        self.assertTrue((self.out / "collective/Stärt.md").is_file())
        self.assertIn("Dök/file.bin", m["team_files"])
        self.assertTrue((self.pages / "Sta\u0308rt.html").exists())

    def test_collision_fails(self):
        self.write(self.files, "File.bin", "a")
        self.write(self.files, "file.bin", "b")
        with self.assertRaisesRegex(ValueError, "collision"):
            copy_normalized(self.files, self.out)

    def test_ambiguous_basename_and_override(self):
        self.write(self.files, "a/x.png", "x")
        self.write(self.files, "b/x.png", "y")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            FileResolver(self.files, {}).resolve("Start.html", "/f/x.png")
        self.assertEqual(FileResolver(self.files, {"/f/x.png": "b/x.png"}).resolve(
            "Start.html", "/f/x.png"), "b/x.png")

    def test_missing_is_not_ready(self):
        self.write(self.pages, "Start.html", '<img src="/f/fehlt.png">')
        m, r = prepare(self.pages, self.files, self.out)
        self.assertFalse(m["ready"])
        self.assertTrue(r["errors"])
        with self.assertRaisesRegex(ValueError, "not ready"):
            validate(self.out)

    def test_integrity_refuses_changed_source(self):
        self.fixture()
        prepare(self.pages, self.files, self.out)
        (self.out / "team-files/unreferenziert.zip").write_text("changed")
        with self.assertRaisesRegex(ValueError, "Changed"):
            validate(self.out)

    def test_symlink_refused(self):
        (self.files / "link").symlink_to("/etc/hosts")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            copy_normalized(self.files, self.out)

    def test_raw_html_table_requires_review(self):
        self.write(self.files, "a.png", "x")
        self.write(self.pages, "Start.html",
                   '<table><tr><td colspan="2"><img src="a.png"></td></tr></table>')
        m, r = prepare(self.pages, self.files, self.out)
        self.assertFalse(m["ready"])

    def test_local_html_link(self):
        self.write(self.pages, "deep/Start.html", '<a href="../Ziel.html">Ziel</a>')
        self.write(self.pages, "Ziel.html", "<h1>Ziel</h1>")
        m, r = prepare(self.pages, self.files, self.out)
        self.assertTrue(m["ready"])
        self.assertIn("../Ziel.md", (self.out / "collective/deep/Start.md").read_text())
        validate(self.out)

    def test_existing_output_refused(self):
        self.out.mkdir()
        with self.assertRaisesRegex(ValueError, "NEW"):
            prepare(self.pages, self.files, self.out)


if __name__ == "__main__":
    unittest.main()
