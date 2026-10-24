"""scripts/export_decisions.py and scripts/prune_runs.py."""

import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from packcheck import storage
from packcheck.pipeline import inspect_photo

import export_decisions
import prune_runs


def test_decisions_export_to_csv(product, tmp_path):
    result = inspect_photo(product.reference_path.parent / "photo_bad.jpg", product, tmp_path / "run", read_zones=False)
    database = tmp_path / "db.sqlite"
    with storage.connect(database) as connection:
        storage.save_inspection(connection, result, run="run-1", inspector="Sam")
        storage.save_decision(connection, result["differences"][0]["id"], "confirm", note="badge", decided_by="Sam")
        storage.save_decision(connection, result["differences"][1]["id"], "dismiss", decided_by="Sam")

    out = tmp_path / "private" / "decisions.csv"
    assert export_decisions.export_decisions(database, out) == 2
    with out.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["decision"] for row in rows] == ["confirm", "dismiss"]
    assert rows[0]["kind"] == result["differences"][0]["kind"]
    assert float(rows[0]["mean_delta_e"]) == result["differences"][0]["mean_delta_e"]
    assert rows[0]["product_id"] == product.product_id


def test_prune_runs_removes_only_old_run_folders(tmp_path):
    runs = tmp_path / "runs"
    for name in ("20260101-090000-000000_demo", "20260910-090000-000000_demo", "20260917-090000-000000_demo", "evaluate"):
        (runs / name).mkdir(parents=True)
        (runs / name / "result.json").write_text("{}")

    would = prune_runs.prune_runs(runs, keep_days=30, today=date(2026, 9, 17), dry_run=True)
    assert would == ["20260101-090000-000000_demo"]
    assert (runs / "20260101-090000-000000_demo").exists()          # dry run deletes nothing

    removed = prune_runs.prune_runs(runs, keep_days=30, today=date(2026, 9, 17))
    assert removed == ["20260101-090000-000000_demo"]
    assert sorted(p.name for p in runs.iterdir()) == ["20260910-090000-000000_demo", "20260917-090000-000000_demo", "evaluate"]
