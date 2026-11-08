#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Normalize Linux/macOS file and directory names to UTF-8 / NFC.

Preview:
  uv run normalize_filenames.py /path/to/file
  uv run normalize_filenames.py /path/to/Wiki --recursive
Apply after reviewing:
  uv run normalize_filenames.py /path/to/Wiki --recursive --apply
Include the selected root directory itself:
  uv run normalize_filenames.py /path/to/Wiki -r --rename-root --apply
Force two-step renaming through an ASCII temporary basename:
  uv run normalize_filenames.py /path/to/Wiki -r --via-temp --apply
Legacy byte names, if their source encoding is known:
  uv run normalize_filenames.py /path/to/Wiki -r --from-encoding cp1252

Valid UTF-8 names are always decoded as UTF-8. --from-encoding only affects
names that fail strict UTF-8 decoding. No encoding is guessed.

Directory arguments process their contents. --rename-root also includes the
selected root directory itself, which is renamed last. Files and directories
are processed by descending path depth, so children keep their original paths
until their own renames are complete. Symlinks are skipped.
Directory arguments without --recursive process only immediate children.
File contents and internal Markdown/HTML links are not changed.
Linux renameat2(RENAME_NOREPLACE) / macOS renamex_np(RENAME_EXCL) prevent
overwriting even if a destination appears after the preview.
On macOS, ASCII temporary basenames are used automatically. On Linux use
--via-temp to request them. Normalization-insensitive lookup aliases are not
treated as collisions, but distinct existing directory entries are.
HFS+ may normalize names back to a decomposed form; in that case normalize
on the Linux Nextcloud server after copying. APFS preserves normalization.
Pause concurrent writes; use a backup, especially with existing Nextcloud data.
"""

import argparse
import codecs
import ctypes
import errno
import json
import os
import stat
import sys
import unicodedata
import uuid
from dataclasses import dataclass


@dataclass
class Rename:
    source: bytes
    target: bytes
    device: int
    inode: int
    reason: str
    kind: str = "file"
    lookup_alias: bool = False


def display(path: bytes) -> str:
    # repr exposes combining characters, control characters and invalid bytes;
    # displaying raw untrusted names could otherwise emit terminal escapes.
    return ascii(os.fsdecode(path))


def normalized_name(raw: bytes, source_encoding: str | None = None) -> tuple[bytes, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
        reason = "NFC normalization"
    except UnicodeDecodeError:
        if not source_encoding:
            raise ValueError(
                "not valid UTF-8; specify --from-encoding only if the original "
                "encoding is known (e.g. cp1252 or latin-1)"
            )
        try:
            text = raw.decode(source_encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"cannot decode as {source_encoding}: {exc}") from exc
        reason = f"{source_encoding} -> UTF-8, NFC"
    result = unicodedata.normalize("NFC", text).encode("utf-8")
    if not result or result in (b".", b"..") or b"/" in result or b"\0" in result:
        raise ValueError("conversion would produce an invalid filename")
    return result, reason


def deepest_first(paths):
    return sorted(paths, key=lambda p: (-p.count(b"/"), p))


def ordered_plan(plan: list[Rename]) -> list[Rename]:
    """Child entries always precede their parents, regardless of input order."""
    return sorted(plan, key=lambda p: (-p.source.count(b"/"), p.source))


def collect_paths(root: bytes, recursive: bool, rename_root: bool = False) -> list[bytes]:
    mode = os.lstat(root).st_mode
    if stat.S_ISLNK(mode):
        raise ValueError("selected path is a symlink; select the actual file/directory")
    if not stat.S_ISDIR(mode):
        return [root]
    if not recursive:
        with os.scandir(root) as entries:
            paths = [entry.path for entry in entries]
        if rename_root:
            paths.append(root)
        return deepest_first(paths)

    paths = []
    def on_error(error):
        raise error
    for directory, folders, files in os.walk(
        root, topdown=False, followlinks=False, onerror=on_error
    ):
        paths.extend(os.path.join(directory, name) for name in files + folders)
    # Children first: their original paths remain valid until parents are renamed.
    if rename_root:
        paths.append(root)
    return deepest_first(paths)


def build_plan(paths: list[bytes], source_encoding: str | None):
    plan, issues = [], []
    for source in paths:
        info = os.lstat(source)
        if stat.S_ISLNK(info.st_mode):
            print(f"SKIP symlink: {display(source)}")
            continue
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            print(f"SKIP special file: {display(source)}")
            continue
        try:
            name, reason = normalized_name(os.path.basename(source), source_encoding)
        except ValueError as exc:
            issues.append(f"{display(source)}: {exc}")
            continue
        target = os.path.join(os.path.dirname(source), name)
        if target != source:
            plan.append(Rename(
                source, target, info.st_dev, info.st_ino, reason,
                "directory" if stat.S_ISDIR(info.st_mode) else "file",
            ))

    targets = {}
    parent_names = {}
    for item in plan:
        if os.path.lexists(item.target):
            parent = os.path.dirname(item.source)
            if parent not in parent_names:
                with os.scandir(parent) as entries:
                    parent_names[parent] = {entry.name for entry in entries}
            destination = os.lstat(item.target)
            same_inode = (destination.st_dev, destination.st_ino) == (
                item.device, item.inode
            )
            # A filesystem lookup alias (e.g. NFD/NFC on APFS) does not have
            # its own byte-exact directory entry. Two distinct hardlinks with
            # the same inode DO have distinct entries and remain a collision.
            if same_inode and os.path.basename(item.target) not in parent_names[parent]:
                item.lookup_alias = True
            else:
                issues.append(f"destination already exists: {display(item.target)}")
        if item.target in targets:
            issues.append(
                f"name collision: {display(item.source)} and "
                f"{display(targets[item.target])} -> {display(item.target)}"
            )
        targets[item.target] = item.source
    return ordered_plan(plan), issues


def load_safe_rename():
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        try:
            renamex = libc.renamex_np
        except AttributeError as exc:
            raise RuntimeError("macOS renamex_np unavailable; refusing unsafe fallback") from exc
        renamex.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex.restype = ctypes.c_int

        def rename(source, target):
            # Darwin RENAME_EXCL = 0x4.
            if renamex(source, target, 0x4) != 0:
                code = ctypes.get_errno()
                raise OSError(code, os.strerror(code), os.fsdecode(source))
        return rename
    if not sys.platform.startswith("linux"):
        raise RuntimeError("--apply requires Linux or macOS for safe renames")
    try:
        renameat2 = libc.renameat2
    except AttributeError as exc:
        raise RuntimeError(
            "libc renameat2 is unavailable; refusing an unsafe overwrite fallback"
        ) from exc
    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint
    ]
    renameat2.restype = ctypes.c_int

    def rename(source, target):
        # AT_FDCWD = -100; RENAME_NOREPLACE = 1.
        if renameat2(-100, source, -100, target, 1) != 0:
            code = ctypes.get_errno()
            if code in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP):
                raise RuntimeError(
                    "filesystem/kernel does not support atomic no-overwrite "
                    "renames; stopping without an unsafe fallback"
                )
            raise OSError(code, os.strerror(code), os.fsdecode(source))
    return rename


def rename_via_temp(item: Rename, rename) -> None:
    """Use an ASCII basename in the same directory, with safe rollback."""
    temporary = os.path.join(
        os.path.dirname(item.source),
        b".nfc-rename-" + uuid.uuid4().hex.encode("ascii"),
    )
    rename(item.source, temporary)
    print(f"TEMP {display(item.source)} -> {display(temporary)}", flush=True)
    try:
        rename(temporary, item.target)
    except (OSError, RuntimeError) as original_error:
        try:
            rename(temporary, item.source)
        except (OSError, RuntimeError) as rollback_error:
            raise RuntimeError(
                f"final rename failed ({original_error}); rollback also failed "
                f"({rollback_error}). Entry remains at {display(temporary)}; "
                "recover it manually."
            ) from original_error
        raise RuntimeError(
            f"final rename failed; original name restored: {original_error}"
        ) from original_error


def apply_plan(plan: list[Rename], via_temp: bool = False) -> int:
    rename = load_safe_rename()
    completed = 0
    for item in ordered_plan(plan):
        info = os.lstat(item.source)
        if (info.st_dev, info.st_ino) != (item.device, item.inode):
            raise RuntimeError(f"source changed since planning: {display(item.source)}")
        if via_temp or item.lookup_alias or sys.platform == "darwin":
            rename_via_temp(item, rename)
        else:
            rename(item.source, item.target)
        completed += 1
        print(f"RENAMED {item.kind}: {display(item.source)} -> "
              f"{display(item.target)}", flush=True)
        with os.scandir(os.path.dirname(item.target)) as entries:
            exact_name = any(
                entry.name == os.path.basename(item.target) for entry in entries
            )
        if not exact_name:
            print(
                f"WARNING: filesystem did not retain the requested NFC name "
                f"{display(item.target)}. Normalize again on the Linux server.",
                file=sys.stderr,
            )
    return completed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", help="File or directory on Linux/macOS")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="Include all subdirectories")
    parser.add_argument("--rename-root", action="store_true",
                        help="Also rename the selected directory itself, last")
    parser.add_argument("--apply", action="store_true",
                        help="Actually rename; default is preview only")
    parser.add_argument("--via-temp", action="store_true",
                        help="Use ASCII temporary names (automatic on macOS)")
    parser.add_argument("--from-encoding",
                        help="Known source encoding for non-UTF-8 names only")
    parser.add_argument("--report", help="Save preview plan as JSON before any renames")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("requires a POSIX filesystem on Linux/macOS")
    if args.from_encoding:
        try:
            codecs.lookup(args.from_encoding)
        except LookupError:
            parser.error(f"unknown encoding: {args.from_encoding}")
    root = os.path.abspath(os.fsencode(args.path))
    try:
        plan, issues = build_plan(
            collect_paths(root, args.recursive, args.rename_root), args.from_encoding
        )
        for item in plan:
            staged = args.via_temp or item.lookup_alias or sys.platform == "darwin"
            print(f"{item.kind}: {display(item.source)} -> "
                  f"{display(item.target)} [{item.reason}"
                  f"{'; via ASCII temporary name' if staged else ''}]")
        directories = sum(p.kind == "directory" for p in plan)
        print(f"\nPlanned renames: {len(plan)} "
              f"({len(plan) - directories} files, {directories} directories); "
              f"blocking issues: {len(issues)}")
        if args.report:
            report = {
                "mode": "apply-requested" if args.apply else "preview",
                "note": "Ordered plan only, not a completion log. Target paths refer "
                        "to each rename step, before ancestor renames. "
                        "Hex paths preserve exact bytes.",
                "renames": [
                    {"source": os.fsdecode(p.source), "target": os.fsdecode(p.target),
                     "source_hex": p.source.hex(), "target_hex": p.target.hex(),
                     "reason": p.reason, "kind": p.kind,
                     "via_temp": args.via_temp or p.lookup_alias
                                 or sys.platform == "darwin"} for p in plan
                ],
                "issues": issues,
            }
            # Keep source Unicode escapes, including surrogateescape bytes, valid JSON.
            with open(args.report, "w", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
        if issues:
            for issue in issues:
                print(f"ERROR: {issue}", file=sys.stderr)
            print("No names changed; resolve the issues and run again.", file=sys.stderr)
            return 2
        if not args.apply:
            print("Preview only. Review, then repeat with --apply.")
            return 0
        if not plan:
            print("Nothing to rename.")
            return 0
        count = apply_plan(plan, via_temp=args.via_temp)
        print(f"Completed {count} renames. Rescan the affected folder in Nextcloud.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {ascii(str(exc))}", file=sys.stderr)
        print("Stopped. An apply run may have completed earlier renames; "
              "check the RENAMED lines, then preview again.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
