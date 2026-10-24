"""Milestone 1: measure how the pipeline does on a folder of REAL photos.

    python scripts/evaluate.py --product products/<id>/product.json --photos private/photos/<id>/
    python scripts/evaluate.py --product ... --photos ... --strong-threshold 16 --weak-threshold 7

The file names say what each photo contains:

    good_*.jpg      a correct pack            -> any difference reported is a FALSE ALARM
    defect_*.jpg    a pack with a planted defect -> no difference reported is a MISS

Optionally, a `labels.json` next to the photos says WHERE each planted defect is, as
fractions of the face, so that a defect photo with one hit and one false alarm is counted
correctly (without it, a defect photo counts as "found" as soon as anything is reported):

    { "defect_sticker_1.jpg": [[0.70, 0.18, 0.20, 0.15]],  "defect_tape_2.jpg": [[...], [...]] }

Why this script exists: every threshold in compare.find_differences was tuned on simulated
photos. Only real photos can tell how far off they are. Change ONE threshold at a time,
re-run, and write the numbers into docs/m1-findings.md.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Make `packcheck` importable when the script is run from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packcheck.pipeline import inspect_photo   # noqa: E402  (import after the path fix)
from packcheck.product import load_product     # noqa: E402

PHOTO_SUFFIXES = (".jpg", ".jpeg", ".png")


def boxes_overlap(a, b):
    """Two (x, y, w, h) boxes in the same units: do they touch?"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def evaluate_folder(product, photos_dir, out_dir, compare_settings=None):
    """Run every good_*/defect_* photo and return one row per photo plus a summary.

    Each row: {"photo", "expected", "status", "differences", "verdict", "seconds"}.
    Verdicts: "ok", "false alarm", "found", "miss", "alignment failed", "found + false alarm".
    """
    photos_dir = Path(photos_dir)
    out_dir = Path(out_dir)
    labels = {}
    labels_path = photos_dir / "labels.json"
    if labels_path.exists():
        labels = json.loads(labels_path.read_text(encoding="utf-8"))

    rows = []
    for photo in sorted(photos_dir.iterdir()):
        if photo.suffix.lower() not in PHOTO_SUFFIXES:
            continue
        if photo.name.startswith("good_"):
            expected = "good"
        elif photo.name.startswith("defect_"):
            expected = "defect"
        else:
            print(f"skipped {photo.name}: name must start with good_ or defect_")
            continue

        started = time.perf_counter()
        result = inspect_photo(photo, product, out_dir / photo.stem, compare_settings)
        seconds = time.perf_counter() - started
        rows.append({
            "photo": photo.name,
            "expected": expected,
            "status": result["status"],
            "differences": len(result["differences"]),
            "verdict": _verdict(expected, result, labels.get(photo.name)),
            "seconds": round(seconds, 2),
        })

    return rows, summarise(rows)


def _verdict(expected, result, planted_boxes):
    if result["status"] == "alignment_failed":
        return "alignment failed"
    found = [tuple(difference["rect_fraction"]) for difference in result["differences"]]

    if expected == "good":
        return "false alarm" if found else "ok"

    if not planted_boxes:
        # No labels: we only know that SOMETHING should be reported.
        return "found" if found else "miss"

    # With labels we can tell hits from false alarms inside the same photo.
    hit = any(boxes_overlap(planted, box) for planted in planted_boxes for box in found)
    extra = any(not any(boxes_overlap(planted, box) for planted in planted_boxes) for box in found)
    if hit and extra:
        return "found + false alarm"
    if hit:
        return "found"
    return "miss" if not found else "false alarm"


def summarise(rows):
    good = [row for row in rows if row["expected"] == "good"]
    defect = [row for row in rows if row["expected"] == "defect"]
    return {
        "good_photos": len(good),
        "false_alarms_on_good": sum(row["verdict"] == "false alarm" for row in good),
        "defect_photos": len(defect),
        "defects_found": sum(row["verdict"].startswith("found") for row in defect),
        "defects_missed": sum(row["verdict"] == "miss" for row in defect),
        "false_alarms_on_defect": sum("false alarm" in row["verdict"] for row in defect),
        "alignment_failures": sum(row["verdict"] == "alignment failed" for row in rows),
        "average_seconds": round(sum(row["seconds"] for row in rows) / len(rows), 2) if rows else 0.0,
    }


def print_report(rows, summary):
    for row in rows:
        print(f"{row['photo']:40s} {row['expected']:7s} {row['status']:18s} {row['differences']:2d} diff  {row['verdict']:20s} {row['seconds']:5.1f} s")
    print()
    print(f"good photos:      {summary['good_photos']:3d}   false alarms: {summary['false_alarms_on_good']}")
    print(f"defect photos:    {summary['defect_photos']:3d}   found: {summary['defects_found']}   missed: {summary['defects_missed']}   with extra false alarms: {summary['false_alarms_on_defect']}")
    print(f"alignment failed: {summary['alignment_failures']:3d}")
    print(f"average time:     {summary['average_seconds']} s per photo")


def main():
    parser = argparse.ArgumentParser(description="Run a folder of good_*/defect_* photos and count false alarms and misses.")
    parser.add_argument("--product", required=True, help="path to a product.json")
    parser.add_argument("--photos", required=True, help="folder with good_*.jpg and defect_*.jpg (and optionally labels.json)")
    parser.add_argument("--out", default="runs/evaluate", help="where result folders go (default: runs/evaluate)")
    # One flag per threshold of compare.find_differences, so that tuning is one command.
    parser.add_argument("--tolerance-px", type=int)
    parser.add_argument("--strong-threshold", type=float)
    parser.add_argument("--strong-min-pixels", type=int)
    parser.add_argument("--weak-threshold", type=float)
    parser.add_argument("--weak-min-pixels", type=int)
    parser.add_argument("--merge-px", type=int)
    args = parser.parse_args()

    compare_settings = {
        name: value
        for name, value in vars(args).items()
        if name not in ("product", "photos", "out") and value is not None
    }
    if compare_settings:
        print(f"thresholds overridden: {compare_settings}")

    product = load_product(args.product)
    rows, summary = evaluate_folder(product, args.photos, args.out, compare_settings)
    print_report(rows, summary)

    # Also as a file, so the numbers can be pasted into docs/m1-findings.md.
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "evaluation.json").write_text(
        json.dumps({"compare_settings": compare_settings, "summary": summary, "rows": rows}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWritten to {out_dir / 'evaluation.json'}")


if __name__ == "__main__":
    main()
