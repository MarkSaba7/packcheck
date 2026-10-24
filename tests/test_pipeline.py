"""End-to-end tests on the synthetic demo product.

Because we planted the defects ourselves (see packcheck/demo.py) we know the right answer:
a good pack must give NO differences, the bad pack must give exactly the 3 planted ones.

Run with:  python -m pytest
"""

import cv2
import numpy as np

from packcheck import demo
from packcheck.pipeline import inspect_photo

from conftest import boxes_overlap


def test_good_pack_has_no_differences(product, tmp_path):
    photo = product.reference_path.parent / "photo_good.jpg"
    result = inspect_photo(photo, product, tmp_path)
    assert result["status"] == "ok"
    assert result["differences"] == []


def test_bad_pack_finds_exactly_the_planted_defects(product, tmp_path):
    photo = product.reference_path.parent / "photo_bad.jpg"
    result = inspect_photo(photo, product, tmp_path)
    found = [tuple(difference["rect_px"]) for difference in result["differences"]]

    assert result["status"] == "differences_found"
    for name, planted in demo.PLANTED_DEFECTS.items():
        assert any(boxes_overlap(planted, box) for box in found), f"missed the planted defect: {name}"
    for box in found:
        assert any(boxes_overlap(planted, box) for planted in demo.PLANTED_DEFECTS.values()), f"false alarm at {box}"


def test_colour_and_content_are_told_apart(product, tmp_path):
    photo = product.reference_path.parent / "photo_bad.jpg"
    result = inspect_photo(photo, product, tmp_path)
    kinds = {}
    for difference in result["differences"]:
        for name, planted in demo.PLANTED_DEFECTS.items():
            if boxes_overlap(planted, tuple(difference["rect_px"])):
                kinds[name] = difference["kind"]
    assert kinds == {"badge_colour": "colour", "net_weight_text": "content", "missing_icon": "content"}


def test_date_zone_is_cut_out_and_not_compared(product, tmp_path):
    # The good pack has a date printed where the artwork has an empty box. That must not be
    # reported as a difference; instead the zone is saved as an image for reading.
    photo = product.reference_path.parent / "photo_good.jpg"
    result = inspect_photo(photo, product, tmp_path)
    assert [zone["name"] for zone in result["variable_zones"]] == ["best_before_lot"]
    assert (tmp_path / "zone_best_before_lot.png").exists()


def test_back_face_is_a_product_of_its_own(products_dir, tmp_path):
    from datetime import date
    from packcheck.product import load_product
    back = load_product(products_dir / "demo-dog-food-back" / "product.json")
    result = inspect_photo(back.reference_path.parent / "photo_good.jpg", back, tmp_path / "back", today=date(2026, 9, 17))
    assert result["status"] == "ok", result
    assert result["readings_status"] == "ok"
    assert result["variable_zones"][0]["readings"][0]["text"] == demo.DEMO_PACKED_ON_TEXT

    # A photo of the FRONT must not be accepted as the back (and vice versa).
    front_photo = products_dir / "demo-dog-food-front" / "photo_good.jpg"
    assert inspect_photo(front_photo, back, tmp_path / "wrong")["status"] == "alignment_failed"


def test_photo_of_something_else_is_refused(product, tmp_path):
    # A photo with no relation to the artwork must fail at alignment, not produce nonsense boxes.
    noise = np.random.default_rng(0).integers(0, 255, (1200, 900, 3), dtype=np.uint8)
    other = tmp_path / "other.jpg"
    cv2.imwrite(str(other), cv2.GaussianBlur(noise, (0, 0), 3))
    result = inspect_photo(other, product, tmp_path / "run")
    assert result["status"] == "alignment_failed"
    assert result["differences"] == []
