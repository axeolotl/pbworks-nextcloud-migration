#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6.0"]
# ///
"""Remove leading YAML front matter from .md files recursively.

Preview:
  uv run remove_front_matter.py /path/to/markdown
Apply:
  uv run remove_front_matter.py /path/to/markdown --apply

Only an opening --- line at the start (after an optional UTF-8 BOM), followed
by valid YAML mapping metadata and a closing --- or ... line, is removed.
An empty header is allowed. Other content remains byte-for-byte unchanged,
including blank lines, BOM, and CRLF/LF line endings.

Updated files retain modification/access times at filesystem-supported precision.
Preview reads may update access time; modification time is unaffected.
Changes use a same-directory temporary file and atomic replacement. Inodes
change; hardlinked files and symlinks are skipped. Owner/group and permission
bits are preserved. ACL/xattr preservation depends on platform copy support.
Use a backup and avoid concurrent writes during an apply run.
"""

import argparse
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import yaml


BOM = b"\xef\xbb\xbf"


def strip_front_matter(data: bytes) -> tuple[bytes | None, str]:
    prefix = BOM if data.startswith(BOM) else b""
    lines = data[len(prefix):].splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        return None, "no front matter"
    offset = len(prefix) + len(lines[0])
    for index, line in enumerate(lines[1:], 1):
        offset += len(line)
        if line.rstrip(b"\r\n") in (b"---", b"..."):
            header = b"".join(lines[1:index])
            try:
                metadata = yaml.safe_load(header.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, yaml.YAMLError):
                return None, "invalid UTF-8/YAML header; skipped"
            if metadata is not None and not isinstance(metadata, dict):
                return None, "header is not a YAML mapping; skipped"
            # Retain the original bytes after the closing delimiter unchanged.
            return prefix + data[offset:], "front matter removed"
    return None, "opening --- without closing delimiter; skipped"


def same_file_version(left, right) -> bool:
    return (
        left.st_dev, left.st_ino, left.st_size, left.st_mtime_ns, left.st_ctime_ns
    ) == (
        right.st_dev, right.st_ino, right.st_size, right.st_mtime_ns, right.st_ctime_ns
    )


def write_preserving_metadata(path: Path, content: bytes, original) -> None:
    """Prepare replacement fully before replacing the original file."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".remove-front-matter-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        # Includes permission bits and available extended attributes.
        shutil.copystat(path, temporary, follow_symlinks=False)
        temporary_stat = temporary.stat()
        if hasattr(os, "chown") and (
            temporary_stat.st_uid, temporary_stat.st_gid
        ) != (original.st_uid, original.st_gid):
            os.chown(temporary, original.st_uid, original.st_gid)
        # chown can clear permission bits; restore afterwards.
        os.chmod(temporary, stat.S_IMODE(original.st_mode))
        os.utime(temporary, ns=(original.st_atime_ns, original.st_mtime_ns))
        if not same_file_version(path.lstat(), original):
            raise RuntimeError("file changed during processing; refusing to replace it")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def process_file(path: Path, apply: bool) -> str:
    original = path.lstat()
    if stat.S_ISLNK(original.st_mode):
        return "skip symlink"
    if not stat.S_ISREG(original.st_mode):
        return "skip non-regular file"
    if original.st_nlink > 1:
        return "skip hardlinked file"
    data = path.read_bytes()
    content, reason = strip_front_matter(data)
    if content is None:
        return reason
    if apply:
        write_preserving_metadata(path, content, original)
        return "removed"
    return "would remove"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directory", type=Path, help="Root directory to scan recursively")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes; default is a preview")
    args = parser.parse_args()
    if not args.directory.is_dir() or args.directory.is_symlink():
        parser.error("select a real directory, not a symlink")

    changed = skipped = errors = 0
    def walk_error(error):
        nonlocal errors
        errors += 1
        print(f"ERROR scanning directory: {ascii(str(error))}", file=sys.stderr)
    # os.walk does not descend into symlink directories by default.
    for directory, folders, files in os.walk(
        args.directory, followlinks=False, onerror=walk_error
    ):
        folders.sort()
        for name in sorted(files):
            if Path(name).suffix.lower() != ".md":
                continue
            path = Path(directory) / name
            try:
                result = process_file(path, args.apply)
                if result in ("removed", "would remove"):
                    changed += 1
                else:
                    skipped += 1
                print(f"{result.upper()}: {ascii(str(path))}")
            except (OSError, RuntimeError) as exc:
                errors += 1
                print(f"ERROR: {ascii(str(path))}: {ascii(str(exc))}", file=sys.stderr)
    action = "Updated" if args.apply else "Would update"
    print(f"\n{action}: {changed}; skipped: {skipped}; errors: {errors}")
    if not args.apply:
        print("Preview only. Repeat with --apply after reviewing.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
