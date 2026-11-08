#!/usr/bin/env python3
"""Validate prepared bundle integrity and all local Markdown link targets."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(root, rel):
    p = root / rel
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        raise ValueError(f"Unsafe manifest path: {rel}")
    if any(q.is_symlink() for q in [p, *p.parents]):
        raise ValueError(f"Symlink in bundle: {p}")
    if not p.is_file():
        raise ValueError(f"Missing file: {p}")
    return p


def validate(bundle):
    m = json.loads((bundle / "manifest.json").read_text())
    if m.get("schema") != 1 or not m.get("ready"):
        raise ValueError("Bundle is not ready; fix preparation errors and prepare a fresh bundle")
    for name, items in [("collective", m["pages"]), ("media", m["attachments"]),
                        ("team-files", m["team_files"])]:
        actual = {p.relative_to(bundle / name).as_posix()
                  for p in (bundle / name).rglob("*") if p.is_file()}
        if actual != set(items):
            raise ValueError(f"Unexpected/missing files in {name}: {actual ^ set(items)}")
        for rel, record in items.items():
            expected = record["sha256"] if isinstance(record, dict) else record
            if digest(safe_path(bundle / name, rel)) != expected:
                raise ValueError(f"Changed file: {name}/{rel}; prepare a new bundle")
    md = MarkdownIt("commonmark")
    for rel in m["pages"]:
        p = bundle / "collective" / rel
        for block in md.parse(p.read_text()):
            for node in block.children or []:
                if node.type not in {"image", "link_open"}:
                    continue
                href = node.attrGet("src" if node.type == "image" else "href")
                url = urlsplit(href)
                if url.scheme or url.netloc or not url.path:
                    continue
                target = (p.parent / unquote(url.path)).resolve()
                root = (bundle / ("media" if node.type == "image" else "collective")).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    raise ValueError(f"{rel}: unresolved/escaping link {href}")
    return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    args = ap.parse_args()
    try:
        m = validate(args.bundle.resolve())
        print(json.dumps(m["counts"], indent=2))
        print("Bundle integrity and local link targets OK")
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
