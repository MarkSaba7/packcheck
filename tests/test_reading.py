"""Milestone 2: reading the variable zones, and the never-guess rule.

The demo packs print "2030-03-15" and "L26091 B" in the date box (see packcheck/demo.py),
so we know what a correct reading is. Dates are checked against a FIXED "today", so these
tests give the same answer next year.
"""

from datetime import date

import numpy as np
import pytest

from packcheck import demo, reading
from packcheck.align import crop_zone_from_original
from packcheck.pipeline import inspect_photo
from packcheck.product import load_product

TODAY = date(2026, 9, 17)
DEMO_RULES = [
    {"name": "best_before", "kind": "date", "formats": ["%Y-%m-%d"], "min_days_ahead": 180},
    {"name": "lot", "kind": "code", "pattern": "L[0-9]{5}[A-Z]"},
]


def readings_by_name(result):
    zone = result["variable_zones"][0]
    return {item["name"]: item for item in zone["readings"]}


def test_demo_date_and_lot_are_read_and_valid(product, tmp_path):
    result = inspect_photo(product.reference_path.parent / "photo_good.jpg", product, tmp_path, today=TODAY)
    found = readings_by_name(result)

    assert result["readings_status"] == "ok"
    assert found["best_before"]["text"] == demo.DEMO_DATE_TEXT
    assert found["best_before"]["valid"] is True
    assert found["lot"]["text"] == demo.DEMO_LOT_TEXT.replace(" ", "")
    assert found["lot"]["valid"] is True


def test_date_too_soon_is_invalid(product, tmp_path):
    # 2030-03-15 is only 73 days after 2030-01-01; the rule wants at least 180.
    result = inspect_photo(product.reference_path.parent / "photo_good.jpg", product, tmp_path, today=date(2030, 1, 1))
    found = readings_by_name(result)

    assert result["readings_status"] == "invalid"
    assert found["best_before"]["valid"] is False
    assert "in 73 days" in found["best_before"]["reason"]
    assert found["lot"]["valid"] is True


def test_past_date_is_invalid(product, tmp_path):
    result = inspect_photo(product.reference_path.parent / "photo_good.jpg", product, tmp_path, today=date(2031, 1, 1))
    assert "292 days ago" in readings_by_name(result)["best_before"]["reason"]
    assert result["readings_status"] == "invalid"


def test_packed_on_date_must_not_be_in_the_future():
    rule = {"name": "packed_on", "kind": "date", "formats": ["%Y-%m-%d"], "max_days_ahead": 0}
    (past,) = reading.check_expectations([reading.Line("2026-09-01", 0.99)], [rule], TODAY)
    (future,) = reading.check_expectations([reading.Line("2026-09-20", 0.99)], [rule], TODAY)
    assert past["valid"] is True
    assert future["valid"] is False and "in 3 days" in future["reason"]


def test_blank_zone_is_could_not_read():
    white = np.full((170, 360, 3), 255, np.uint8)
    entry = reading.read_zone(white, DEMO_RULES, today=TODAY)
    assert entry["text"] is None
    assert entry["valid"] is None
    assert entry["reason"] == reading.COULD_NOT_READ


def test_low_confidence_lines_are_never_used(monkeypatch):
    # The OCR "sees" the right date but is unsure. Never guess: that is a check-by-hand, not a pass.
    monkeypatch.setattr(reading, "read_lines", lambda crop: [reading.Line("2030-03-15", 0.55), reading.Line("L26091B", 0.99)])
    entry = reading.read_zone(np.zeros((10, 10, 3), np.uint8), DEMO_RULES, today=TODAY)

    assert entry["text"] == "L26091B"                 # only the confident line is used
    assert entry["valid"] is None                      # the date is unresolved, so a person must look
    assert "best_before" in entry["reason"]
    assert [item["valid"] for item in entry["readings"]] == [None, True]


def test_code_that_matches_nothing_is_check_by_hand_not_invalid():
    lines = [reading.Line("L2609", 0.99)]
    (lot,) = reading.check_expectations(lines, [DEMO_RULES[1]], TODAY)
    assert lot["valid"] is None
    assert lot["reason"].endswith("check by hand")


def test_parse_date_is_strict_and_tries_words():
    assert reading.parse_date("2O30-03-15", ["%Y-%m-%d"]) is None       # letter O: refuse, do not fix it
    assert reading.parse_date("BB 15.03.2030", ["%Y-%m-%d", "%d.%m.%Y"]) == date(2030, 3, 15)
    assert reading.parse_date("15/03/30", ["%d/%m/%y"]) == date(2030, 3, 15)


def test_zone_crop_comes_from_the_original_photo():
    # Original photo = 2x the working photo (photo_scale 0.5), identity homography. The crop of
    # reference rect (10, 20, 30, 40) must then be the original's pixels (20..80, 40..120), untouched.
    original = np.random.default_rng(1).integers(0, 255, (400, 300, 3), dtype=np.uint8)
    crop = crop_zone_from_original(original, np.eye(3), photo_scale=0.5, rect=(10, 20, 30, 40))
    assert crop.shape == (80, 60, 3)
    assert np.array_equal(crop, original[40:120, 20:80])


def test_bad_expectation_in_product_json_is_rejected(products_dir, tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / "reference.png").write_bytes((products_dir / "demo-dog-food-front" / "reference.png").read_bytes())
    (folder / "product.json").write_text(
        '{"product_id": "broken", "reference_image": "reference.png", "zones": [{"name": "z", "kind": "variable",'
        ' "rect": [0.1, 0.1, 0.2, 0.2], "expect": [{"name": "d", "kind": "date"}]}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="formats"):
        load_product(folder / "product.json")
