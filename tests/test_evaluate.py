"""scripts/evaluate.py must count false alarms and misses correctly.

We build a small evaluation folder from the demo photos: the good photo becomes good_1.jpg,
the bad photo becomes defect_1.jpg, and labels.json says where its planted defects are.
"""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from packcheck import demo
import evaluate


def make_photo_folder(product, folder):
    source = product.reference_path.parent
    folder.mkdir()
    shutil.copy(source / "photo_good.jpg", folder / "good_1.jpg")
    shutil.copy(source / "photo_bad.jpg", folder / "defect_1.jpg")
    planted = [
        [x / demo.ART_W, y / demo.ART_H, w / demo.ART_W, h / demo.ART_H]
        for (x, y, w, h) in demo.PLANTED_DEFECTS.values()
    ]
    (folder / "labels.json").write_text(json.dumps({"defect_1.jpg": planted}), encoding="utf-8")
    return folder


def test_defaults_find_the_defect_without_false_alarms(product, tmp_path):
    photos = make_photo_folder(product, tmp_path / "photos")
    rows, summary = evaluate.evaluate_folder(product, photos, tmp_path / "out")

    assert [row["verdict"] for row in rows] == ["found", "ok"]   # sorted by file name: defect_1, good_1
    assert summary["false_alarms_on_good"] == 0
    assert summary["defects_found"] == 1
    assert summary["defects_missed"] == 0
    assert (tmp_path / "out" / "defect_1" / "result.json").exists()


def test_threshold_overrides_reach_the_pipeline(product, tmp_path):
    # Absurdly high thresholds must make the pipeline blind: the defect is then a MISS.
    photos = make_photo_folder(product, tmp_path / "photos")
    blind = {"strong_threshold": 1000.0, "weak_threshold": 1000.0}
    rows, summary = evaluate.evaluate_folder(product, photos, tmp_path / "out", blind)

    assert summary["defects_missed"] == 1
    assert summary["false_alarms_on_good"] == 0
