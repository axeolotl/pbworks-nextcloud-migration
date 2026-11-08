#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["beautifulsoup4>=4.12", "lxml>=5", "markdown-it-py>=3"]
# ///
"""Create a fresh, offline migration bundle. Never changes the source exports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from normalize_filenames import normalized_name
from convert_wiki import convert_html_to_markdown


def nfc(value):
    return unicodedata.normalize("NFC", value)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def relative_url(target, source):
    return quote(os.path.relpath(target, source.parent).replace(os.sep, "/"), safe="/")


def copy_normalized(source, dest, encoding=None):
    """Preflight all names, including directories; reject portable-name collisions."""
    entries, seen = [], {}
    for p in sorted(source.rglob("*")):
        if p.is_symlink() or not (p.is_file() or p.is_dir()):
            raise ValueError(f"Symlink/special file not allowed: {p}")
        parts = [os.fsdecode(normalized_name(os.fsencode(s), encoding)[0])
                 for s in p.relative_to(source).parts]
        rel = Path(*parts)
        key = rel.as_posix().casefold()
        if key in seen:
            raise ValueError(f"Name collision after normalization: {seen[key]} / {p}")
        seen[key] = p
        entries.append((p, rel))
    dest.mkdir()
    changes = []
    for p, rel in entries:
        target = dest / rel
        if p.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        if p.relative_to(source).as_posix() != rel.as_posix():
            changes.append({"before": os.fsdecode(os.fsencode(p.relative_to(source))),
                            "after": rel.as_posix()})
    return changes


class FileResolver:
    def __init__(self, root, overrides):
        self.paths = {nfc(p.relative_to(root).as_posix()): p
                      for p in root.rglob("*") if p.is_file()}
        self.names = defaultdict(list)
        self.overrides = overrides
        for rel in self.paths:
            self.names[PurePosixPath(rel).name].append(rel)

    def resolve(self, source_rel, href):
        override = self.overrides.get(f"{source_rel}::{href}", self.overrides.get(href))
        if override is not None:
            if override not in self.paths:
                raise ValueError(f"Override points to missing file: {override}")
            return override
        parsed = urlsplit(href)
        path = nfc(unquote(parsed.path))
        path = re.sub(r"^/w/file/(?:fetch/)?\d+/", "", path)
        path = re.sub(r"^/f/", "", path)
        candidates = {path.lstrip("/")}
        if not parsed.netloc and not path.startswith("/"):
            candidates.add(os.path.normpath(str(PurePosixPath(source_rel).parent / path)))
        matches = candidates & self.paths.keys()
        if len(matches) == 1:
            return next(iter(matches))
        if len(matches) > 1:
            raise ValueError(f"Ambiguous paths: {sorted(matches)}")
        matches = self.names.get(PurePosixPath(path).name, [])
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ValueError(f"Ambiguous basename: {matches}; add --overrides")
        raise ValueError("Not found in exported files")


def prepare(pages, files, out, encoding=None, overrides=None):
    pages, files, out = pages.resolve(), files.resolve(), out.absolute()
    if not pages.is_dir() or not files.is_dir():
        raise ValueError("Both source trees must exist")
    if pages == files or pages in files.parents or files in pages.parents:
        raise ValueError("Use two separate, non-overlapping source trees")
    if out.exists() or pages in out.parents or files in out.parents:
        raise ValueError("Output must be a NEW directory outside both source trees")
    out.mkdir(parents=True)
    report = {"errors": [], "warnings": [], "normalization": {}, "references": []}
    report["normalization"]["pages"] = copy_normalized(pages, out / "html", encoding)
    report["normalization"]["files"] = copy_normalized(files, out / "files", encoding)
    htmls = sorted(p for p in (out / "html").rglob("*")
                   if p.is_file() and p.suffix.lower() in {".html", ".htm"})
    if not htmls:
        raise ValueError("No HTML pages found")
    resolver = FileResolver(out / "files", overrides or {})
    titles, md_paths = {}, {}
    for p in htmls:
        rel = p.relative_to(out / "html")
        md = rel.with_suffix(".md")
        if md.as_posix().casefold() in {x.casefold() for x in md_paths.values()}:
            raise ValueError(f"Duplicate Markdown output: {md}")
        raw = p.read_text(encoding="utf-8")  # Never silently replace broken text.
        match = re.search(r"<!--\s*page=(.+?)\s*-->", raw)
        title = nfc(match.group(1).strip() if match else p.stem)
        if title in titles:
            raise ValueError(f"Duplicate page title: {title}")
        titles[title] = md.as_posix()
        md_paths[rel.as_posix()] = md.as_posix()

    embedded, linked, tokens, conversions = set(), set(), {}, []
    (out / "collective").mkdir()
    for p in htmls:
        rel = p.relative_to(out / "html").as_posix()
        dest = out / "collective" / md_paths[rel]
        st = p.stat()
        soup = BeautifulSoup(p.read_text(encoding="utf-8"), "lxml")
        for tag in soup.find_all(["a", "img"]):
            attr = "src" if tag.name == "img" else "href"
            href = tag.get(attr, "")
            if not href or href.startswith("#"):
                continue
            u = urlsplit(href)
            internal = re.match(r"^/w/(?:page|file)/|^/f/", u.path)
            if (u.scheme or u.netloc) and not (
                u.hostname and (u.hostname.endswith(".pbworks.com")
                                or u.hostname.endswith(".pbwiki.com")) and internal
            ):
                continue  # Remote images are NOT fetched.
            try:
                page_match = re.match(r"^/w/page/(?:\d+/)?(.+)$", u.path)
                if page_match:
                    target = titles.get(nfc(unquote(page_match[1])))
                    if not target:
                        raise ValueError("Page missing from export")
                    tag[attr] = relative_url(out / "collective" / target, dest)
                    if u.fragment:
                        tag[attr] += "#" + u.fragment
                    continue
                raw_path = nfc(unquote(u.path))
                html_rel = os.path.normpath(str(PurePosixPath(rel).parent / raw_path))
                if tag.name == "a" and html_rel in md_paths:
                    tag[attr] = relative_url(out / "collective" / md_paths[html_rel], dest)
                    if u.fragment:
                        tag[attr] += "#" + u.fragment
                    continue
                file_rel = resolver.resolve(rel, href)
                record = {"page": md_paths[rel], "kind": tag.name, "href": href,
                          "file": file_rel}
                report["references"].append(record)
                if tag.name == "img":
                    embedded.add(file_rel)
                    tag[attr] = relative_url(out / "media" / file_rel, dest)
                else:
                    linked.add(file_rel)
                    token = "https://migration.invalid/file/" + hashlib.sha256(
                        (file_rel + "#" + u.fragment).encode()).hexdigest()
                    tokens[token] = {"path": file_rel, "fragment": u.fragment}
                    tag[attr] = token
            except ValueError as e:
                report["errors"].append({"page": rel, "href": href, "reason": str(e)})
        # Rewrite only staging copies. Source files and timestamps remain untouched.
        p.write_text(str(soup), encoding="utf-8")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
        conversion = convert_html_to_markdown(p, dest, {}, out / "collective", False)
        conversions.append(conversion)
        report["warnings"].extend({"page": rel, "warning": w}
                                  for w in conversion["warnings"])

    # Image-only files become attachments; linked+embedded files ALSO stay in Files.
    team = sorted(set(resolver.paths) - (embedded - linked))
    for folder, paths in [("media", sorted(embedded)), ("team-files", team)]:
        (out / folder).mkdir()
        for rel in paths:
            target = out / folder / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out / "files" / rel, target)

    # Do not trust images hidden in raw HTML tables: importer only sees Markdown nodes.
    parser = MarkdownIt("commonmark")
    for md in sorted((out / "collective").rglob("*.md")):
        content = md.read_text(encoding="utf-8")
        if re.search(r"<(?:img|a)\b", content, re.I):
            report["errors"].append({"page": str(md.relative_to(out)),
                                     "reason": "Raw HTML links/images need manual conversion"})
        for block in parser.parse(content):
            for child in block.children or []:
                if child.type == "image":
                    target = child.attrGet("src")
                    if not urlsplit(target).scheme:
                        local = (md.parent / unquote(target.split("#")[0])).resolve()
                        if not local.is_file():
                            report["errors"].append({"page": str(md.relative_to(out)),
                                                     "reason": f"Missing staged image: {target}"})
    manifest = {
        "schema": 1, "ready": not report["errors"], "tokens": tokens,
        "pages": {p.relative_to(out / "collective").as_posix(): sha(p)
                  for p in sorted((out / "collective").rglob("*.md"))},
        "team_files": {rel: {"sha256": sha(out / "team-files" / rel),
                             "mtime": int((out / "team-files" / rel).stat().st_mtime)}
                       for rel in team},
        "attachments": {rel: sha(out / "media" / rel) for rel in sorted(embedded)},
        "counts": {"pages": len(htmls), "attachments": len(embedded),
                   "team_files": len(team), "both": len(embedded & linked)},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n")
    (out / "conversion_report.json").write_text(json.dumps(conversions, indent=2, ensure_ascii=False) + "\n")
    return manifest, report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=Path, required=True)
    ap.add_argument("--files", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--from-encoding", help="Only for non-UTF-8 filename bytes, e.g. cp1252")
    ap.add_argument("--overrides", type=Path, help='JSON: {"source.html::old URL": "files/path"}')
    args = ap.parse_args()
    try:
        m, r = prepare(args.pages, args.files, args.out, args.from_encoding,
                       json.loads(args.overrides.read_text()) if args.overrides else {})
        print(json.dumps(m["counts"], indent=2))
        print(f"Errors: {len(r['errors'])}; warnings: {len(r['warnings'])}; see report.json")
        return 0 if m["ready"] else 1
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
