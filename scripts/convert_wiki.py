#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "beautifulsoup4",
#     "lxml",
# ]
# ///
"""
PBworks-Wiki HTML → Markdown Konverter

Pipeline:
  1. Link-Map aus allen HTML-Dateien aufbauen (page= Kommentar + interne Links)
  2. Pre-Processing mit BeautifulSoup (pbraw, Metadata, nbsp, Layout-Tabellen)
  3. Interne Links auf lokale .md-Dateien umschreiben
  4. Konvertierung mit Pandoc (Nextcloud-kompatibles GFM mit Pipe-Tabellen)
  5. Post-Processing & Validierung

Usage:
  # Alle Dateien konvertieren:
  uv run convert_wiki.py /pfad/zu/html-dateien/ --output-dir /pfad/zu/markdown/

  # Nur Validierung:
  uv run convert_wiki.py /pfad/zu/html-dateien/ --output-dir /pfad/zu/markdown/ --validate-only

  Hinweis: pandoc muss als Systembinary installiert sein (nicht über pip).
  Standardmäßig keine YAML-Frontmatter; Metadaten stehen im JSON-Report.
  Mit --front-matter lässt sich das frühere Verhalten aktivieren.
  Komplexe Tabellen bleiben als HTML erhalten und erzeugen eine Warnung.
  Bilder/Anhänge werden nicht kopiert; relative Pfade bleiben unverändert.
  Originaldateinamen und Unterordner bleiben erhalten; nur .html wird .md.
  Der Dateisystem-Änderungszeitstempel (mtime) wird von der HTML-Datei übernommen.
  Interne Linkziele werden bei Bedarf URL-kodiert (z.B. Leerzeichen als %20).
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    """Erzeugt einen dateisystem-freundlichen Slug aus einem Seitennamen."""
    # Umlaute umschreiben
    replacements = {
        "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
        "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    # Alles außer a-z, A-Z, 0-9, - durch - ersetzen
    text = re.sub(r"[^a-zA-Z0-9-]+", "-", text)
    # Mehrfache Bindestriche zusammenfassen
    text = re.sub(r"-+", "-", text)
    # Führende/abschließende Bindestriche entfernen
    return text.strip("-").lower()


def extract_metadata(soup: BeautifulSoup, filepath: Path) -> dict:
    """Extrahiert Metadaten aus HTML-Kommentaren als dict."""
    meta = {}
    keep_keys = {"page", "wiki", "name", "time"}
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        text = str(comment).strip()
        # Muster: key=value
        match = re.match(r"^(\w+)=(.+)$", text)
        if match:
            key, value = match.group(1), match.group(2)
            if key in keep_keys:
                if key == "time" and value.isdigit():
                    # Unix-Timestamp → ISO
                    from datetime import datetime, timezone
                    ts = int(value)
                    meta[key] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                else:
                    meta[key] = value
    if not meta.get("page"):
        meta["page"] = filepath.stem
    return meta


def remove_unwanted_comments(soup: BeautifulSoup) -> None:
    """Entfernt pbraw-Kommentare und private Metadaten-Kommentare."""
    drop_keys = {"ip", "session", "uid", "content-type", "email"}
    for comment in list(soup.find_all(string=lambda t: isinstance(t, Comment))):
        text = str(comment).strip()
        match = re.match(r"^(\w+)=", text)
        if match and match.group(1) in drop_keys:
            comment.extract()
        elif text.startswith("pbraw="):
            comment.extract()


def clean_nbsp_spacers(soup: BeautifulSoup) -> None:
    """Ersetzt &nbsp;-Spacer durch Leerzeilen."""
    for tag in soup.find_all(["p", "h1", "h2", "h3"]):
        # Wenn ein Tag nur &nbsp; oder Leerzeichen enthält → leeren
        if tag.get_text(strip=True) == "" and tag.find(["img", "a", "strong", "em"]) is None:
            tag.decompose()


def strip_styles_and_unwrap_spans(soup: BeautifulSoup) -> None:
    """
    Entfernt inline style-Attribute und entwickelt <span>-Tags auf.
    Pandoc kann style-Attribute nicht verarbeiten und lässt sie als Raw-HTML stehen.
    """
    # Alle style-Attribute entfernen
    for tag in soup.find_all(style=True):
        del tag["style"]

    # <span>-Tags entwickeln (Inhalt bleibt, Tag verschwindet)
    for span in soup.find_all("span"):
        span.unwrap()

    # Überflüssige Attribute von Links entfernen (data-*, id, class etc.)
    for a in soup.find_all("a"):
        for attr in list(a.attrs.keys()):
            if attr not in ("href", "title"):
                del a[attr]

    # Überflüssige Attribute von Bildern entfernen (nur src, alt behalten)
    for img in soup.find_all("img"):
        for attr in list(img.attrs.keys()):
            if attr not in ("src", "alt"):
                del img[attr]

    # <strong> und <em> aufräumen: leere Tags entfernen
    for tag in soup.find_all(["strong", "em", "b", "i"]):
        if tag.get_text(strip=True) == "" and tag.find("img") is None:
            tag.decompose()


def move_images_out_of_headings(soup: BeautifulSoup) -> None:
    """
    Verschiebt Bilder aus <h1>-<h6>-Tags heraus, da Pandoc diese nicht
    korrekt als Markdown-Bild in einer Überschrift rendern kann.
    Stattdessen wird das Bild vor die Überschrift gesetzt.
    Leere Überschriften (nach Bild-Extraktion) werden entfernt.
    """
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        for img in heading.find_all("img"):
            heading.insert_before(img.extract())
            # <br> nach dem Bild einfügen
            heading.insert_before(BeautifulSoup("<br/>", "html.parser"))

    # Leere Überschriften entfernen (nur noch whitespace oder &nbsp;)
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if heading.get_text(strip=True) == "" and heading.find(["img", "a"]) is None:
            heading.decompose()


def simplify_layout_tables(soup: BeautifulSoup) -> None:
    """
    Ersetzt Layout-Tabellen (die nur für Positionierung dienen) durch
    ihre Inhalte. Behaltet echte Datentabellen.
    """
    for table in list(soup.find_all("table")):
        if table.parent is None:
            continue
        rows = table.find_all("tr")
        # Nur eindeutig markierte, einzeilige PBworks-Layout-Tabellen.
        # Zwei Spalten oder fehlende <th> allein sind kein Layout-Indiz.
        marked_layout = "pbNoBorder" in table.get("class", [])
        if (
            marked_layout and len(rows) == 1
            and table.find("th") is None
            and table.find("table") is None
            and table.find(["h1", "h2", "h3", "h4", "h5", "h6"]) is not None
        ):
            for cell in rows[0].find_all("td", recursive=False):
                # Inhalt vor die Tabelle verschieben
                for child in list(cell.contents):
                    table.insert_before(child.extract() if child.parent else child)
            table.decompose()


def run_pandoc(source: str) -> str:
    """GFM verwendet ATX-Überschriften, Codeblöcke und Pipe-Tabellen."""
    result = subprocess.run(
        ["pandoc", "--from", "html", "--to", "gfm", "--wrap=none"],
        input=source, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"Pandoc error: {result.stderr.strip()}")
    return result.stdout.strip()


def extract_markdown_tables(soup: BeautifulSoup, warnings: list) -> dict:
    """Einfache Tabellen explizit als Pipe-Tabellen serialisieren.

    Komplexe Tabellen bleiben unverändert als HTML erhalten. Ihre Grenzen
    werden im Report dokumentiert, statt Inhalte stillschweigend zu verlieren.
    """
    replacements = {}
    # Nur äußerste Tabellen; verschachtelte Tabellen sind komplex.
    tables = [t for t in soup.find_all("table") if t.find_parent("table") is None]
    for index, table in enumerate(tables, 1):
        rows = table.find_all("tr")
        cells_by_row = [
            row.find_all(["th", "td"], recursive=False) for row in rows
        ]
        cells = [cell for row in cells_by_row for cell in row]
        complex_table = (
            table.find("table") is not None
            or any(str(c.get("colspan", "1")) != "1"
                   or str(c.get("rowspan", "1")) != "1" for c in cells)
            or any(c.find(["ul", "ol", "pre", "blockquote", "h1", "h2", "h3",
                          "h4", "h5", "h6", "iframe"]) is not None for c in cells)
            or (cells_by_row and len({len(row) for row in cells_by_row}) != 1)
        )
        if not cells or complex_table:
            markdown = str(table)
            warnings.append(
                f"Tabelle {index}: komplexe/leere Tabelle als HTML erhalten; "
                "Darstellung in Nextcloud Text manuell prüfen."
            )
        else:
            data = []
            multiline = False
            for row in cells_by_row:
                values = []
                for cell in row:
                    value = run_pandoc(str(cell))
                    if "\n" in value or cell.find("br") is not None:
                        multiline = True
                    # Pipe-Tabellen erfordern eine Quelltextzeile pro Zeile.
                    value = re.sub(r"\s*\n\s*", " ", value).strip()
                    # Pandoc schützt Literale gewöhnlich bereits mit \|.
                    value = re.sub(r"(?<!\\)\|", r"\\|", value)
                    values.append(value)
                data.append(values)
            first_row_header = all(c.name == "th" for c in cells_by_row[0])
            if first_row_header:
                header, body = data[0], data[1:]
            else:
                # Keine Datenzeile zur Kopfzeile erklären oder entfernen.
                header, body = [""] * len(data[0]), data
            widths = [
                max(3, *(len(row[col]) for row in [header, *body]))
                for col in range(len(header))
            ]
            def render_row(row):
                return "| " + " | ".join(
                    value.ljust(width) for value, width in zip(row, widths)
                ) + " |"
            separator = "|" + "|".join("-" * (w + 2) for w in widths) + "|"
            markdown = "\n".join([
                render_row(header), separator, *(render_row(row) for row in body)
            ])
            caption = table.find("caption", recursive=False)
            if caption is not None:
                markdown = run_pandoc(str(caption)) + "\n\n" + markdown
            if multiline:
                warnings.append(
                    f"Tabelle {index}: Zeilenumbrüche/Absätze in Zellen zu "
                    "Leerzeichen normalisiert; Zelltext erhalten."
                )
        token = f"NEXTCLOUDTABLEPLACEHOLDER{index}END"
        # Collision check includes literal text in the source.
        while token in soup.get_text() or token in replacements:
            token += "X"
        replacements[token] = markdown
        placeholder = soup.new_tag("p")
        placeholder.string = token
        table.replace_with(placeholder)
    return replacements


def rewrite_internal_links(
    soup: BeautifulSoup, page_map: dict, output_path: Path, output_dir: Path
) -> list:
    """
    Schreibt interne PBworks-Links (/w/page/...) auf lokale .md-Dateien um.
    Der Zielpfad wird relativ zur Position der Zieldatei berechnet.
    Gibt Liste der behandelten (Link, Ziel) zurück.
    """
    rewritten = []
    source_output_dir = output_path.parent  # Verzeichnis der erzeugten .md-Datei

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Interner PBworks-Link: /w/page/ID/Slug  oder  /w/page/Slug
        parsed = urlsplit(href)
        match = re.match(r"^/w/page/(?:\d+/)?(.+)$", parsed.path)
        if match:
            slug_raw = unquote(match.group(1))
            # Im page_map nach diesem Slug suchen
            target = page_map.get(slug_raw)
            if target is None:
                normalized_slug = unicodedata.normalize("NFC", slug_raw)
                candidates = [
                    md_file for page_name, md_file in page_map.items()
                    if unicodedata.normalize("NFC", page_name) == normalized_slug
                ]
                if not candidates:
                    candidates = [
                        md_file for page_name, md_file in page_map.items()
                        if slugify(page_name) == slugify(slug_raw)
                    ]
                # Mehrdeutige Seiten nicht willkürlich zuordnen.
                if len(set(candidates)) == 1:
                    target = candidates[0]

            if target:
                # Relativen Pfad von der Quelldatei zur Zieldatei berechnen
                target_abs = output_dir / target
                rel_target = Path(os.path.relpath(
                    target_abs, source_output_dir
                )).as_posix()
                # Nur die URL kodieren, niemals den tatsächlichen Dateinamen.
                link_target = quote(rel_target, safe="/")
                if parsed.query:
                    link_target += "?" + parsed.query
                if parsed.fragment:
                    link_target += "#" + parsed.fragment
                a["href"] = link_target
                rewritten.append((href, link_target))
            else:
                # Ohne passende Exportdatei ist der Originaldateiname unbekannt.
                # Original-Link behalten statt ein lokales Ziel zu erfinden.
                rewritten.append((href, href))

        # Auch pbwiki.com / pbworks.com externe Links bleiben unberührt
    return rewritten


def build_page_map(html_dir: Path) -> dict:
    """
    Baut eine Mapping-Tabelle: Seitenname → relativer Zieldateipfad.md
    aus den <!-- page=... --> Kommentaren aller HTML-Dateien.
    Durchsucht rekursiv alle Unterordner. Die Verzeichnisstruktur
    wird im Output erhalten, um Namenskollisionen zu vermeiden.
    """
    page_map = {}
    for html_file in sorted(html_dir.rglob("*.html")):
        text = html_file.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"<!--\s*page=(.+?)\s*-->", text)
        if match:
            page_name = match.group(1).strip()
        else:
            page_name = html_file.stem
        # Relativen Pfad zum html_dir berechnen und Verzeichnisstruktur erhalten
        rel_path = html_file.relative_to(html_dir)
        md_rel_path = rel_path.with_suffix(".md")
        page_map[page_name] = md_rel_path.as_posix()
    return page_map


# ---------------------------------------------------------------------------
# Hauptkonvertierung
# ---------------------------------------------------------------------------

def convert_html_to_markdown(
    html_path: Path,
    output_path: Path,
    page_map: dict,
    output_dir: Path,
    front_matter: bool = False,
) -> dict:
    """
    Konvertiert eine einzelne HTML-Datei zu Markdown.
    Gibt ein Report-Dict zurück.
    """
    report = {
        "file": str(html_path),
        "rewritten_links": [],
        "missing_images": [],
        "warnings": [],
    }

    # 1. HTML lesen
    source_stat = html_path.stat()
    raw_html = html_path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(raw_html, "lxml")

    # 2. Metadaten extrahieren
    meta = extract_metadata(soup, html_path)
    report["metadata"] = meta

    # 3. Unerwünschte Kommentare entfernen
    remove_unwanted_comments(soup)

    # 4. &nbsp;-Spacer bereinigen
    clean_nbsp_spacers(soup)

    # 5. Styles entfernen, Spans entwickeln, Attribute aufräumen
    strip_styles_and_unwrap_spans(soup)

    # 6. Bilder aus Überschriften herauslösen
    move_images_out_of_headings(soup)

    # 7. Layout-Tabellen vereinfachen
    simplify_layout_tables(soup)

    # 8. Interne Links umschreiben
    report["rewritten_links"] = rewrite_internal_links(soup, page_map, output_path, output_dir)
    for original, target in report["rewritten_links"]:
        if original == target:
            report["warnings"].append(
                f"Internes Linkziel nicht eindeutig im Export gefunden; "
                f"Original-Link erhalten: {original}"
            )

    # 9. Tabellen explizit serialisieren, danach übriges HTML konvertieren.
    replacements = extract_markdown_tables(soup, report["warnings"])
    # Redundante Fettung ausschließlich in Überschriften entfernen.
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        for strong in heading.find_all(["strong", "b"]):
            strong.unwrap()
    markdown = run_pandoc(str(soup))
    for token, table_markdown in replacements.items():
        markdown = markdown.replace(token, table_markdown)

    # 10. YAML-Frontmatter hinzufügen
    if front_matter and meta:
        frontmatter_lines = ["---"]
        for key in ["page", "wiki", "name", "time"]:
            if key in meta:
                frontmatter_lines.append(
                    f"{key}: {json.dumps(meta[key], ensure_ascii=False)}"
                )
        frontmatter_lines.append("---\n")
        markdown = "\n".join(frontmatter_lines) + "\n" + markdown

    # Keine globalen Regex-Rewrites: Codeblöcke und Markdown-Zeilenumbrüche
    # dürfen nicht durch rstrip() oder Leerzeilen-Kompression beschädigt werden.

    # 12. Bilder prüfen
    img_dir = html_path.parent
    for img in soup.find_all("img", src=True):
        src = img["src"]
        # Nur relative Pfade prüfen
        if not src.startswith(("http://", "https://")):
            img_path = img_dir / src
            if not img_path.exists():
                report["missing_images"].append(src)

    # 13. Schreiben
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown + "\n", encoding="utf-8")
    # mtime der Quelle übernehmen, ohne den Zugriffszeitstempel der neu
    # erzeugten Markdown-Datei zu verändern. Nanosekundenpräzision, soweit
    # das Zieldateisystem sie unterstützt.
    os.utime(
        output_path,
        ns=(output_path.stat().st_atime_ns, source_stat.st_mtime_ns),
    )

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="PBworks-Wiki HTML → Markdown Batch-Konverter"
    )
    parser.add_argument("html_dir", type=Path, help="Verzeichnis mit HTML-Dateien")
    parser.add_argument(
        "--output-dir", type=Path, required=True,
        help="Zielverzeichnis für Markdown-Dateien",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Nur Link-Map bauen und Validierung, keine Konvertierung",
    )
    parser.add_argument(
        "--front-matter", action="store_true",
        help="Optional YAML-Frontmatter schreiben; standardmäßig stehen "
             "Metadaten nur in conversion_report.json (Nextcloud-Profil).",
    )
    args = parser.parse_args()

    if not args.html_dir.is_dir():
        print(f"Fehler: {args.html_dir} ist kein Verzeichnis", file=sys.stderr)
        sys.exit(1)

    # 1. Link-Map aufbauen
    print(f"Durchsuche {args.html_dir} (rekursiv) nach HTML-Dateien...")
    html_files = sorted(args.html_dir.rglob("*.html"))
    print(f"Gefunden: {len(html_files)} HTML-Dateien")

    page_map = build_page_map(args.html_dir)
    print(f"Link-Map: {len(page_map)} Seiten")

    # Link-Map speichern
    map_path = args.output_dir / "page_map.json"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        json.dumps(page_map, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Link-Map gespeichert: {map_path}")

    if args.validate_only:
        print("\n--- Link-Map ---")
        for name, md in page_map.items():
            print(f"  {name}  →  {md}")
        return

    # 2. Konvertierung
    all_reports = []
    for i, html_file in enumerate(html_files, 1):
        # Dateiname unabhängig vom page=-Metadatum unverändert übernehmen.
        rel_path = html_file.relative_to(args.html_dir).with_suffix(".md")
        md_filename = rel_path.name
        output_path = args.output_dir / rel_path

        report = convert_html_to_markdown(
            html_file, output_path, page_map, args.output_dir, args.front_matter
        )
        all_reports.append(report)

        if i % 50 == 0 or i == len(html_files):
            print(f"  [{i}/{len(html_files)}] Konvertiert: {html_file.name} → {md_filename}")

    # 3. Report
    print("\n" + "=" * 60)
    print("KONVERTIERUNGS-REPORT")
    print("=" * 60)

    total_links = sum(len(r["rewritten_links"]) for r in all_reports)
    total_missing_imgs = sum(len(r["missing_images"]) for r in all_reports)
    total_warnings = sum(len(r["warnings"]) for r in all_reports)

    print(f"Dateien konvertiert: {len(all_reports)}")
    print(f"Interne Links umgeschrieben: {total_links}")
    print(f"Fehlende Bilder: {total_missing_imgs}")
    print(f"Warnungen: {total_warnings}")

    if total_missing_imgs > 0:
        print("\n--- Fehlende Bilder ---")
        seen = set()
        for r in all_reports:
            for img in r["missing_images"]:
                if img not in seen:
                    print(f"  {img}  (in {Path(r['file']).name})")
                    seen.add(img)

    if total_warnings > 0:
        print("\n--- Warnungen ---")
        for r in all_reports:
            for w in r["warnings"]:
                print(f"  {Path(r['file']).name}: {w}")

    # Detaillierter Report speichern
    report_path = args.output_dir / "conversion_report.json"
    report_path.write_text(
        json.dumps(all_reports, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nDetaillierter Report: {report_path}")


if __name__ == "__main__":
    main()
