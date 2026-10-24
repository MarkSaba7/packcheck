"""Milestone 7: the nightly backup, and a way to try a restore.

    python scripts/backup.py                                  # one zip in backups/, keep the newest 30
    python scripts/backup.py --dest E:/packcheck-backups --keep 60
    python scripts/backup.py --with-runs                      # also the inspection images (large)
    python scripts/backup.py --restore backups/packcheck-2026-10-24_0200.zip --to C:/restore-test

One backup is one zip, packcheck-YYYY-MM-DD_HHMM.zip, holding everything that cannot be
made again: the database (every inspection, decision, reading, user), products/ (the
approved artwork, zones, rules, golden photos) and private/proofs/ (the uploaded proofs the
products were cut from). runs/ (photos and close-ups) is left out unless --with-runs is
given: it is by far the largest part, and the retention rule deletes it anyway.

The zips hold company data: keep them on a drive the company backs up, not in a folder
that syncs to a personal cloud. backups/ is ignored by git for the same reason.

--restore never writes into the live project: it only unpacks into an EMPTY folder, so a
restore can be tried (open the copy, look at it) without any risk to the real data.
"""

import argparse
import re
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The only files --keep may ever delete. Anything else in the backup folder (a file the IT
# person put there, another program's backups) does not match and is left alone.
BACKUP_NAME = re.compile(r"^packcheck-\d{4}-\d{2}-\d{2}_\d{4}\.zip$")


def make_backup(project_dir, dest_dir, with_runs=False, now=None):
    """Write one backup zip of `project_dir` into `dest_dir`. Returns the zip's path."""
    project_dir = Path(project_dir)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / f"packcheck-{(now or datetime.now()):%Y-%m-%d_%H%M}.zip"
    # Written under another name first and renamed at the end, so a backup interrupted
    # half-way (power cut, full disk) never looks like a complete one.
    partial_path = zip_path.with_name(zip_path.name + ".partial")

    folders = [project_dir / "products", project_dir / "private" / "proofs"]
    if with_runs:
        folders.append(project_dir / "runs")

    with tempfile.TemporaryDirectory() as temp_dir, zipfile.ZipFile(partial_path, "w", zipfile.ZIP_DEFLATED) as archive:
        database = project_dir / "packcheck.sqlite"
        if database.exists():
            database_copy = Path(temp_dir) / "packcheck.sqlite"
            copy_database(database, database_copy)
            archive.write(database_copy, "packcheck.sqlite")
        else:
            # A fresh install has no database until the server answers its first request.
            print("note: no packcheck.sqlite yet, backing up the folders only")
        for folder in folders:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    # Stored with its path inside the project (products/<id>/reference.png),
                    # so unpacking the zip gives back the same layout.
                    archive.write(path, path.relative_to(project_dir).as_posix())

    partial_path.replace(zip_path)
    return zip_path


def copy_database(database, copy_path):
    """Copy the SQLite file with sqlite's own backup API, not a plain file copy.

    The server may be writing to the database at 02:00 (a late scan, a decision). A plain
    file copy taken in the middle of a write can catch half of it and give a broken copy.
    Connection.backup() reads the database through sqlite itself, page by page, and starts
    over if a write happens meanwhile, so the copy is always one consistent state of it.
    The live file is opened read-only: the backup can never change it.
    """
    source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    target = sqlite3.connect(str(copy_path))
    try:
        source.backup(target)
    finally:
        # Closed explicitly (a `with` block on a connection does not close it): Windows
        # refuses to delete the temporary folder while the copy is still open.
        target.close()
        source.close()


def prune_backups(dest_dir, keep):
    """Delete the oldest backup zips beyond the newest `keep`. Returns the names removed."""
    # The date and time in the name sort in the same order as the calendar.
    backups = sorted(path for path in Path(dest_dir).iterdir() if BACKUP_NAME.match(path.name))
    to_remove = backups[:-keep] if len(backups) > keep else []
    for path in to_remove:
        path.unlink()
    return [path.name for path in to_remove]


def restore_backup(zip_path, to_dir):
    """Unpack a backup zip into `to_dir`, which must be empty or not exist yet."""
    to_dir = Path(to_dir)
    if to_dir.exists() and any(to_dir.iterdir()):
        raise ValueError(f"{to_dir} is not empty: restore only into an empty folder, so nothing gets overwritten")
    to_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        # extractall refuses to write outside to_dir even if a name in the zip says "../".
        archive.extractall(to_dir)
    return to_dir


def main():
    parser = argparse.ArgumentParser(description="Back up the database, products/ and private/proofs/ into one zip.")
    parser.add_argument("--dest", default=str(ROOT / "backups"), help="folder for the zips (default: backups/ in the project)")
    parser.add_argument("--keep", type=int, default=30, help="how many backup zips to keep in --dest (default 30)")
    parser.add_argument("--with-runs", action="store_true", help="also back up runs/ (the inspection images; large)")
    parser.add_argument("--restore", metavar="ZIP", help="unpack this backup instead of making one")
    parser.add_argument("--to", metavar="FOLDER", help="with --restore: an empty folder to unpack into")
    args = parser.parse_args()

    if args.restore:
        if not args.to:
            parser.error("--restore needs --to FOLDER (an empty folder)")
        try:
            restore_backup(args.restore, args.to)
        except ValueError as error:
            sys.exit(f"refused: {error}")
        print(f"{args.restore} unpacked into {args.to}")
        return

    if args.keep < 1:
        parser.error("--keep must be at least 1 (0 would delete the backup just made)")
    zip_path = make_backup(ROOT, args.dest, with_runs=args.with_runs)
    print(f"backup written: {zip_path} ({zip_path.stat().st_size // 1024} KB)")
    for name in prune_backups(args.dest, args.keep):
        print(f"deleted old backup {name}")


if __name__ == "__main__":
    main()
