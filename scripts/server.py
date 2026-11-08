#!/usr/bin/env python3
"""Server-side migration stages. Run as the Nextcloud web-server user.

Preview is the default. --apply is required for every mutating stage.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from check_bundle import digest, validate, safe_path

REPO = Path(__file__).resolve().parents[1]


def run(cmd, **kwargs):
    print("+", " ".join(map(str, cmd)), file=sys.stderr)
    return subprocess.run(list(map(str, cmd)), check=True, **kwargs)


def patch(app, action, apply, php):
    target = app / "lib/Service/ImportService.php"
    backup = target.with_suffix(".php.pbworks-backup")
    known = json.loads((REPO / "patches/versions.json").read_text())
    current = digest(target)
    if action == "restore":
        if not backup.exists():
            raise ValueError("No patch backup found")
        if digest(backup) not in known:
            raise ValueError("Backup is not a recognized original")
        if current != known[digest(backup)]["patched_sha256"]:
            raise ValueError("Importer changed since patching; will NOT overwrite an app update")
        if apply:
            shutil.copyfile(backup, target)
            backup.unlink()
        print("Restore" + (" complete" if apply else " preview OK"))
        return
    if any(current == r["patched_sha256"] for r in known.values()):
        print("Recognized migration patch already installed")
        return
    if current not in known:
        raise ValueError("Unknown ImportService.php hash; no fuzzy/forced patching. Update and test patch first.")
    if backup.exists():
        raise ValueError("Backup already exists; investigate previous patch attempt")
    diff = REPO / "patches" / known[current]["patch"]
    run(["patch", "--batch", "--forward", "--fuzz=0", "--dry-run", "-p1", "-i", diff], cwd=app)
    if apply:
        shutil.copy2(target, backup)
        try:
            run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", diff], cwd=app)
            run([php, "-l", target])
            if digest(target) != known[current]["patched_sha256"]:
                raise ValueError("Patched hash mismatch")
        except BaseException:
            shutil.copyfile(backup, target)
            backup.unlink()
            raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stage", choices=["patch", "restore", "copy", "import", "scan"])
    ap.add_argument("--nextcloud", type=Path, required=True)
    ap.add_argument("--app-dir", type=Path, help="Collectives application directory")
    ap.add_argument("--bundle", type=Path)
    ap.add_argument("--user")
    ap.add_argument("--groupfolder-id", type=int)
    ap.add_argument("--team-mount", help="Existing path visible to --user, e.g. Dokumente")
    ap.add_argument("--destination", help="Subdirectory inside the Team folder, e.g. PBworks")
    ap.add_argument("--collective-id", type=int)
    ap.add_argument("--confirm-new-collective", action="store_true")
    ap.add_argument("--php", default="php")
    ap.add_argument("--memory-limit", default="2G")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    nc = a.nextcloud.resolve()
    occ = [a.php, "-d", f"memory_limit={a.memory_limit}", nc / "occ"]
    if not (nc / "occ").is_file():
        ap.error("Invalid Nextcloud directory")
    if a.stage in {"patch", "restore"}:
        if not a.app_dir:
            ap.error("--app-dir required")
        patch(a.app_dir.resolve(), a.stage, a.apply, a.php)
        return
    if a.stage == "scan":
        if not a.groupfolder_id:
            ap.error("--groupfolder-id required")
        cmd = occ + ["groupfolders:scan", str(a.groupfolder_id)]
        if a.apply:
            run(cmd)
        else:
            print("Preview:", *cmd)
        return
    if not a.bundle or not a.user:
        ap.error("--bundle and --user required")
    bundle = a.bundle.resolve()
    validate(bundle)
    # Prevent two server stages from modifying a bundle concurrently.
    with (bundle / ".server.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if a.stage == "copy":
            if not a.groupfolder_id or not a.team_mount or not a.destination:
                ap.error("--groupfolder-id, --team-mount and --destination required")
            cmd = [a.php, REPO / "server/copy_files.php", "--nextcloud", nc,
                   "--user", a.user, "--groupfolder-id", str(a.groupfolder_id),
                   "--team-mount", a.team_mount, "--destination", a.destination,
                   "--bundle", bundle]
            if a.apply:
                cmd.append("--apply")
            result = run(cmd, stdout=subprocess.PIPE, text=True)
            index = json.loads(result.stdout)
            if a.apply:
                (bundle / "files-index.json").write_text(json.dumps(index, indent=2))
                run(occ + ["groupfolders:scan", str(a.groupfolder_id)])
            else:
                print("Copy preview OK; no Nextcloud files changed")
            return
        if not a.collective_id or not a.app_dir:
            ap.error("--collective-id and --app-dir required")
        known = json.loads((REPO / "patches/versions.json").read_text())
        if digest(a.app_dir / "lib/Service/ImportService.php") not in {
            x["patched_sha256"] for x in known.values()
        }:
            raise ValueError("Install the tested patch before import")
        final = json.loads((bundle / "finalized.json").read_text())
        actual = {p.relative_to(bundle / "import").as_posix()
                  for p in (bundle / "import").rglob("*") if p.is_file()}
        if actual != set(final["pages"]) or actual != set(json.loads(
            (bundle / "manifest.json").read_text())["pages"]):
            raise ValueError("Final import page set changed")
        for rel, expected in final["pages"].items():
            if digest(safe_path(bundle / "import", rel)) != expected:
                raise ValueError("Final import changed: " + rel)
        marker = bundle / "import-started.json"
        if marker.exists():
            raise ValueError("Import already attempted. Inspect import.log; do not retry into the same Collective.")
        run([a.php, REPO / "server/check_collective.php", "--nextcloud", nc,
             "--user", a.user, "--collective-id", str(a.collective_id)])
        cmd = occ + ["collectives:import:markdown", "-v", "-c", str(a.collective_id),
                     "-u", a.user, bundle / "import"]
        if not a.apply:
            print("Import preview OK:", *cmd)
            return
        if not a.confirm_new_collective:
            ap.error("After reviewing target ID, pass --confirm-new-collective")
        marker.write_text(json.dumps({"collective_id": a.collective_id, "user": a.user}))
        with (bundle / "import.log").open("w") as log:
            proc = subprocess.Popen(list(map(str, cmd)), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True)
            failed = False
            for line in proc.stdout:
                print(line, end="")
                log.write(line)
                log.flush()
                if any(word in line for word in ["✗", "Unresolved ", "Unreplaceable ", "Failed:", "Blocked:"]):
                    failed = True
            code = proc.wait()
        if code or failed:
            raise ValueError("Import had failures; inspect import.log. It may be partially imported.")
        (bundle / "import-completed.json").write_text(marker.read_text())
        print("Import complete. Review pages, attachments and links in the Web UI.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
