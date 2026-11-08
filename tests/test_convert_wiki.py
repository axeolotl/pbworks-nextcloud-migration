"""Run with: uv run --with beautifulsoup4 --with lxml python -m unittest -v test_convert_wiki"""
import tempfile
import unittest
import subprocess
import sys
import json
import os
from pathlib import Path

from bs4 import BeautifulSoup
from convert_wiki import (
    build_page_map, convert_html_to_markdown, extract_markdown_tables,
    run_pandoc, simplify_layout_tables,
)


class NextcloudMarkdownTests(unittest.TestCase):
    def convert_table(self, source):
        soup = BeautifulSoup(source, "lxml")
        warnings = []
        simplify_layout_tables(soup)
        replacements = extract_markdown_tables(soup, warnings)
        output = run_pandoc(str(soup))
        for token, content in replacements.items():
            output = output.replace(token, content)
        return output, warnings

    def test_user_table(self):
        output, warnings = self.convert_table(
            "<table><tr><th>Tabellenkopf1</th><th>Tabellenkopf2</th>"
            "<th>Tabellenkopf3</th></tr>"
            "<tr><td>Z1S1</td><td>Z1S2</td><td>Z1S3</td></tr>"
            "<tr><td>Z2S1</td><td>Z2S2</td><td>Z2S3</td></tr></table>"
        )
        self.assertEqual(output,
            "| Tabellenkopf1 | Tabellenkopf2 | Tabellenkopf3 |\n"
            "|---------------|---------------|---------------|\n"
            "| Z1S1          | Z1S2          | Z1S3          |\n"
            "| Z2S1          | Z2S2          | Z2S3          |")
        self.assertEqual(warnings, [])

    def test_two_column_headerless_data_survives(self):
        output, _ = self.convert_table(
            "<table><tr><td>A</td><td>1</td></tr>"
            "<tr><td>B</td><td>2</td></tr></table>"
        )
        self.assertEqual(len(output.splitlines()), 4)
        self.assertIn("| A   | 1   |", output)
        self.assertIn("| B   | 2   |", output)

    def test_links_bold_pipes_unicode(self):
        output, _ = self.convert_table(
            '<table><tr><th>Name</th><th>Info</th></tr>'
            '<tr><td><strong>Äpfel | Öl</strong></td>'
            '<td><a href="../seite.md">Seite</a> <code>a|b</code></td></tr></table>'
        )
        self.assertIn(r"**Äpfel \| Öl**", output)
        self.assertIn("[Seite](../seite.md)", output)
        self.assertIn(r"`a\|b`", output)

    def test_multiline_warns_without_losing_text(self):
        output, warnings = self.convert_table(
            "<table><tr><td><p>First</p><p>Second<br>Third</p></td></tr></table>"
        )
        for text in ["First", "Second", "Third"]:
            self.assertIn(text, output)
        self.assertTrue(warnings)

    def test_complex_table_preserved_and_reported(self):
        source = '<table><tr><td colspan="2">Merged</td></tr></table>'
        output, warnings = self.convert_table(source)
        self.assertIn('colspan="2"', output)
        self.assertIn("Merged", output)
        self.assertTrue(warnings)

    def test_nested_table_preserved(self):
        output, warnings = self.convert_table(
            "<table><tr><td>Outer<table><tr><td>Inner</td></tr></table>"
            "</td></tr></table>"
        )
        self.assertEqual(output.count("<table>"), 2)
        self.assertTrue(warnings)

    def test_recursive_conversion_frontmatter_and_linebreaks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "input", root / "output"
            (source / "deep").mkdir(parents=True)
            (source / "deep" / "child.html").write_text(
                '<!-- page=Child -->'
                '<p>First<br>Second</p><a href="/w/page/Home">Home</a>'
                '<pre><code>** **\n\n\nend  </code></pre>', encoding="utf-8"
            )
            (source / "home.html").write_text(
                "<!-- page=Home --><h1>Home</h1>", encoding="utf-8"
            )
            page_map = build_page_map(source)
            report = convert_html_to_markdown(
                source / "deep" / "child.html", target / "deep" / "child.md",
                page_map, target,
            )
            output = (target / "deep" / "child.md").read_text()
            self.assertFalse(output.startswith("---"))
            self.assertIn("[Home](../home.md)", output)
            self.assertIn("First  \nSecond", output)
            # Pandoc uses an indented code block here and normalizes trailing
            # spaces itself; our postprocessing must preserve literal markers
            # and the multiple blank lines in the resulting code block.
            self.assertIn("    ** **\n\n\n    end", output)
            self.assertEqual(report["metadata"]["page"], "Child")
            convert_html_to_markdown(
                source / "home.html", target / "home.md", page_map,
                target, front_matter=True,
            )
            self.assertTrue((target / "home.md").read_text().startswith("---"))

    def test_cli_preserves_original_filenames_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, target = root / "input", root / "output"
            folder = source / "Pläne 2026"
            folder.mkdir(parents=True)
            (folder / "Änderungen (final).html").write_text(
                "<!-- page=Completely different page title --><h1>Änderungen</h1>",
                encoding="utf-8",
            )
            (source / "Start Seite.html").write_text(
                '<a href="/w/page/123/Completely%20different%20page%20title#section">'
                'Änderungen</a>', encoding="utf-8",
            )
            dated_source = folder / "Änderungen (final).html"
            os.utime(dated_source, ns=(1000000000000000000, 1234567890123456789))
            source_mtime_ns = dated_source.stat().st_mtime_ns
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/convert_wiki.py"), str(source),
                 "--output-dir", str(target)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / "Pläne 2026" / "Änderungen (final).md").exists())
            self.assertEqual(
                (target / "Pläne 2026" / "Änderungen (final).md").stat().st_mtime_ns,
                source_mtime_ns,
            )
            self.assertTrue((target / "Start Seite.md").exists())
            self.assertIn(
                "(Pl%C3%A4ne%202026/%C3%84nderungen%20%28final%29.md#section)",
                (target / "Start Seite.md").read_text(),
            )
            mapping = json.loads((target / "page_map.json").read_text())
            self.assertEqual(
                mapping["Completely different page title"],
                "Pläne 2026/Änderungen (final).md",
            )
            self.assertFalse((target / "completely-different-page-title.md").exists())


if __name__ == "__main__":
    unittest.main()
