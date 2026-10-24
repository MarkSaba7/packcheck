"""Milestone 6, step 0: export the workers' Confirm / Dismiss decisions as a CSV.

    python scripts/export_decisions.py --out private/decisions.csv

Every decision is one row: what the pipeline measured about the box (kind, size, position,
colour distance) and what the worker said about it. That table IS the training set for a
false-alarm filter: a model that learns "boxes like this one usually get dismissed".

Why export first and train later: there is no data yet. A filter trained on ten decisions
is noise. Collect a few hundred over real work days, then compare a trained filter against
the plain rules on held-out rows, and keep it only if it is measurably better (the
milestone 6 definition of done). The CSV goes to private/ because it names products.
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packcheck import storage   # noqa: E402

COLUMNS = ["decision_id", "decided_at", "decided_by", "decision", "note", "product_id", "run",
           "kind", "x", "y", "w", "h", "mean_delta_e", "max_delta_e", "pixels"]


def export_decisions(database_path, out_path):
    """Write the CSV; return how many rows it has."""
    import json
    with storage.connect(database_path) as connection:
        rows = storage.list_decisions(connection)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for row in rows:
            x, y, w, h = json.loads(row["rect_fraction"])
            writer.writerow([row["id"], row["decided_at"], row["decided_by"] or "", row["decision"], row["note"] or "",
                             row["product_id"], row["run"], row["kind"], x, y, w, h,
                             row["mean_delta_e"], row["max_delta_e"], row["pixels"]])
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Export Confirm / Dismiss decisions to a CSV (future training data).")
    parser.add_argument("--database", default="packcheck.sqlite")
    parser.add_argument("--out", default="private/decisions.csv")
    args = parser.parse_args()
    count = export_decisions(args.database, args.out)
    print(f"{count} decisions written to {args.out}")


if __name__ == "__main__":
    main()
