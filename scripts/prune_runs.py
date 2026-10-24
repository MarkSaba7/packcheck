"""Milestone 7: the retention rule for photos.

    python scripts/prune_runs.py --keep-days 90            # delete run folders older than 90 days
    python scripts/prune_runs.py --keep-days 90 --dry-run  # only list what would go

Every inspection keeps its photo and close-ups in runs/<time>_<product>/. Photos of real
packs are confidential and pile up (a few MB each), so the company needs a rule for how
long they stay. This script applies it. Run it by hand or from a scheduled task.

What stays: the database rows (which pack, when, what was found, what was decided) - the
report still lists old inspections, only their images are gone. What goes: the folder.
The folder name starts with the inspection time (YYYYMMDD-HHMMSS-...), so no database
lookup is needed to know its age.
"""

import argparse
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path


def prune_runs(runs_dir, keep_days, today=None, dry_run=False):
    """Delete run folders older than `keep_days`. Returns the names of the folders removed
    (or that would be removed with dry_run)."""
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    removed = []
    for folder in sorted(Path(runs_dir).iterdir()):
        if not folder.is_dir():
            continue
        try:
            folder_day = datetime.strptime(folder.name[:8], "%Y%m%d").date()
        except ValueError:
            continue           # not a run folder (e.g. runs/evaluate): leave it alone
        if folder_day < cutoff:
            removed.append(folder.name)
            if not dry_run:
                shutil.rmtree(folder)
    return removed


def main():
    parser = argparse.ArgumentParser(description="Delete inspection folders older than N days (the database rows stay).")
    parser.add_argument("--runs", default="runs")
    parser.add_argument("--keep-days", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    removed = prune_runs(args.runs, args.keep_days, dry_run=args.dry_run)
    verb = "would delete" if args.dry_run else "deleted"
    for name in removed:
        print(f"{verb} {name}")
    print(f"{len(removed)} folder(s) {verb}")


if __name__ == "__main__":
    main()
