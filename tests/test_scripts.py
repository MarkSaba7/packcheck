"""scripts/export_decisions.py, scripts/prune_runs.py and scripts/backup.py."""

import csv
import sqlite3
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest

from packcheck import storage
from packcheck.pipeline import inspect_photo

import backup
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


def make_fake_project(folder):
    """A tiny project folder: a database with one user, one product, one proof, one run."""
    with storage.connect(folder / "packcheck.sqlite") as connection:
        storage.add_user(connection, "Sam", "1234")
    connection.close()
    (folder / "products" / "demo-front").mkdir(parents=True)
    (folder / "products" / "demo-front" / "product.json").write_text("{}")
    (folder / "private" / "proofs" / "proof-1").mkdir(parents=True)
    (folder / "private" / "proofs" / "proof-1" / "proof.pdf").write_bytes(b"%PDF")
    (folder / "private" / "notes.txt").write_text("not part of the backup")
    (folder / "runs" / "20261024-090000-000000_demo-front").mkdir(parents=True)
    (folder / "runs" / "20261024-090000-000000_demo-front" / "result.json").write_text("{}")


def test_backup_contains_database_products_and_proofs(tmp_path):
    project = tmp_path / "project"
    make_fake_project(project)

    zip_path = backup.make_backup(project, tmp_path / "backups", now=datetime(2026, 10, 24, 2, 0))
    assert zip_path.name == "packcheck-2026-10-24_0200.zip"
    with zipfile.ZipFile(zip_path) as archive:
        names = sorted(archive.namelist())
    assert names == ["packcheck.sqlite", "private/proofs/proof-1/proof.pdf", "products/demo-front/product.json"]

    with_runs = backup.make_backup(project, tmp_path / "backups", with_runs=True, now=datetime(2026, 10, 24, 3, 0))
    with zipfile.ZipFile(with_runs) as archive:
        assert "runs/20261024-090000-000000_demo-front/result.json" in archive.namelist()

    # Restoring gives a database that opens and still has its rows.
    restored = backup.restore_backup(zip_path, tmp_path / "restore")
    copy = sqlite3.connect(restored / "packcheck.sqlite")
    assert copy.execute("SELECT name FROM users").fetchall() == [("Sam",)]
    copy.close()
    assert (restored / "products" / "demo-front" / "product.json").exists()


def test_restore_refuses_a_folder_that_is_not_empty(tmp_path):
    project = tmp_path / "project"
    make_fake_project(project)
    zip_path = backup.make_backup(project, tmp_path / "backups")
    with pytest.raises(ValueError):
        backup.restore_backup(zip_path, project)            # the live project: never overwritten
    assert (project / "private" / "notes.txt").read_text() == "not part of the backup"


def test_keep_deletes_only_the_oldest_backup_zips(tmp_path):
    for name in ("packcheck-2026-10-20_0200.zip", "packcheck-2026-10-21_0200.zip", "packcheck-2026-10-22_0200.zip",
                 "packcheck-old-manual.zip", "other-program-2026-10-01_0200.zip", "notes.txt"):
        (tmp_path / name).write_bytes(b"")

    removed = backup.prune_backups(tmp_path, keep=2)
    assert removed == ["packcheck-2026-10-20_0200.zip"]
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "notes.txt", "other-program-2026-10-01_0200.zip", "packcheck-2026-10-21_0200.zip",
        "packcheck-2026-10-22_0200.zip", "packcheck-old-manual.zip"]


def test_server_logging_config_writes_the_access_log_to_logs(tmp_path, monkeypatch):
    # windows_setup.ps1 starts uvicorn with --log-config scripts/server_logging.json from the
    # project folder; this checks that file is valid and puts each request in logs/access.log.
    import json
    import logging
    import logging.config
    config = json.loads((Path(__file__).resolve().parent.parent / "scripts" / "server_logging.json").read_text())
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    logging.config.dictConfig(config)
    try:
        # The same call uvicorn makes for every request.
        logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "192.168.1.20:5000", "GET", "/desk", "1.1", 200)
        logging.getLogger("uvicorn.error").info("Application startup complete.")
    finally:
        # Give the loggers back as they were, so the other tests do not write into tmp_path.
        for name in ("uvicorn", "uvicorn.access"):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
            logger.propagate = True
    assert '192.168.1.20:5000 - "GET /desk HTTP/1.1" 200' in (tmp_path / "logs" / "access.log").read_text(encoding="utf-8")
    assert "Application startup complete." in (tmp_path / "logs" / "server.log").read_text(encoding="utf-8")
