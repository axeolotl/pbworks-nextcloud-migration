#!/usr/bin/env python3
"""Create final import/ Markdown using Nextcloud file IDs; preserve staged mtimes."""
import argparse
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit
from check_bundle import validate, digest


def finalize(bundle, index_path, base_url):
    m = validate(bundle)
    u = urlsplit(base_url)
    if u.scheme != "https" or not u.netloc or u.query or u.fragment or u.username:
        raise ValueError("Use an HTTPS Nextcloud base URL, optionally with subdirectory")
    index = json.loads(index_path.read_text())
    if index.get("complete") is not True:
        raise ValueError("File copy/index did not complete")
    for rel, record in m["team_files"].items():
        node = index["files"].get(rel)
        if not node or node["sha256"] != record["sha256"] or int(node["id"]) <= 0:
            raise ValueError(f"Missing or mismatched Nextcloud file: {rel}")
    replacements = {}
    for token, info in m["tokens"].items():
        node = index["files"][info["path"]]
        replacements[token] = base_url.rstrip("/") + "/f/" + str(int(node["id"]))
        if info["fragment"]:
            replacements[token] += "#" + info["fragment"]
    out = bundle / "import"
    if out.exists():
        raise ValueError("import/ already exists; use a fresh bundle to avoid stale import state")
    shutil.copytree(bundle / "collective", out, copy_function=shutil.copy2)
    for p in out.rglob("*.md"):
        st = p.stat()
        text = p.read_text()
        for token, url in replacements.items():
            text = text.replace(token, url)
        if "https://migration.invalid/" in text:
            raise ValueError(f"Unresolved placeholder: {p}")
        p.write_text(text, encoding="utf-8")
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    (bundle / "finalized.json").write_text(json.dumps({
        "base_url": base_url,
        "pages": {p.relative_to(out).as_posix(): digest(p) for p in out.rglob("*.md")}
    }, indent=2))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path)
    ap.add_argument("--index", required=True, type=Path)
    ap.add_argument("--base-url", required=True)
    a = ap.parse_args()
    print(finalize(a.bundle.resolve(), a.index, a.base_url))
