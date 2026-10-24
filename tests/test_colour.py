"""Milestone 5: glare, CIEDE2000 and the golden photo as colour reference."""

import json

import cv2
import numpy as np
import pytest

from packcheck import compare, demo
from packcheck.pipeline import inspect_photo
from packcheck.product import load_product

from conftest import boxes_overlap


def test_delta_e_2000_matches_published_values():
    # Test pairs from Sharma, Wu & Dalal (2005), the paper that defines the reference
    # implementation. Columns: L, a, b of colour 1 and colour 2, expected delta E 2000.
    pairs = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
        ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
        ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),     # hue wraps past 360
        ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
        ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
        ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ]
    first = np.array([[a for a, _, _ in pairs]], dtype=np.float32)
    second = np.array([[b for _, b, _ in pairs]], dtype=np.float32)
    expected = np.array([e for _, _, e in pairs])
    assert np.allclose(compare.delta_e_2000(first, second)[0], expected, atol=1e-3)
    assert np.allclose(compare.delta_e_2000(first, first), 0.0)


def photo_with_glare(product, tmp_path, radius):
    """The demo good photo with a pure-white blob painted over the illustration."""
    photo = cv2.imread(str(product.reference_path.parent / "photo_good.jpg"))
    centre = (photo.shape[1] // 2, photo.shape[0] // 2)          # the middle of the photo = the bowl illustration
    cv2.ellipse(photo, centre, (radius, radius // 2), 20, 0, 360, (255, 255, 255), -1)
    path = tmp_path / "glare.jpg"
    cv2.imwrite(str(path), photo, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return path


def test_small_glare_is_left_out_not_flagged(product, tmp_path):
    result = inspect_photo(photo_with_glare(product, tmp_path, radius=60), product, tmp_path / "run", read_zones=False)
    assert result["glare"]["fraction"] > 0
    assert result["glare"]["rects_px"]
    assert result["glare"]["retake_recommended"] is False
    assert any("Reflections" in warning for warning in result["warnings"])
    assert result["status"] == "ok", result["differences"]          # the white blob is not a "missing element"


def test_large_glare_asks_for_a_retake(product, tmp_path):
    result = inspect_photo(photo_with_glare(product, tmp_path, radius=300), product, tmp_path / "run", read_zones=False)
    assert result["glare"]["fraction"] > compare.GLARE_RETAKE_FRACTION
    assert result["glare"]["retake_recommended"] is True
    assert any("Retake" in warning for warning in result["warnings"])


def write_product(folder, golden=None, compare_settings=None):
    """A demo-like product in a temporary folder, optionally with a golden photo / settings."""
    folder.mkdir()
    cv2.imwrite(str(folder / "reference.png"), demo.draw_face())
    data = {"product_id": folder.name, "name": folder.name, "reference_image": "reference.png", "zones": [
        {"name": "best_before_lot", "kind": "variable", "rect": demo._fraction(demo.DATE_BOX)}]}
    if golden is not None:
        cv2.imwrite(str(folder / "golden.jpg"), golden, [cv2.IMWRITE_JPEG_QUALITY, 90])
        data["golden_photo"] = "golden.jpg"
    if compare_settings:
        data["compare_settings"] = compare_settings
    (folder / "product.json").write_text(json.dumps(data), encoding="utf-8")
    return load_product(folder / "product.json")


def simulated(face, seed, path):
    cv2.imwrite(str(path), demo.simulate_photo(face, seed=seed), [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path


def test_golden_photo_becomes_the_colour_reference(tmp_path):
    # The press prints the badge in ORANGE_OFF and everyone agrees that is correct; only the
    # artwork's on-screen orange is different. Without a golden photo that print is flagged
    # as a colour difference on every pack. With one, it is not - and real defects still are.
    printed = dict(badge_colour=demo.ORANGE_OFF, date_text=demo.DEMO_DATE_TEXT, lot_text=demo.DEMO_LOT_TEXT)
    without = write_product(tmp_path / "without", golden=None)
    with_golden = write_product(tmp_path / "with", golden=demo.simulate_photo(demo.draw_face(**printed), seed=5))

    good_print = simulated(demo.draw_face(**printed), 6, tmp_path / "good_print.jpg")
    flagged = inspect_photo(good_print, without, tmp_path / "run1", read_zones=False)
    assert any(d["kind"] == "colour" and boxes_overlap(demo.BADGE_BOX, tuple(d["rect_px"])) for d in flagged["differences"])

    accepted = inspect_photo(good_print, with_golden, tmp_path / "run2", read_zones=False)
    assert accepted["status"] == "ok", accepted["differences"]

    # A missing icon is a CONTENT defect: judged against the artwork, so still caught.
    missing_icon = simulated(demo.draw_face(icons=2, **printed), 7, tmp_path / "missing_icon.jpg")
    result = inspect_photo(missing_icon, with_golden, tmp_path / "run3", read_zones=False)
    assert [d["kind"] for d in result["differences"]] == ["content"]
    assert result["differences"][0]["compared_with"] == "artwork"
    assert boxes_overlap(demo.ICON_BOXES[2], tuple(result["differences"][0]["rect_px"]))

    # And a badge that is off compared with the GOLDEN print is a colour defect again.
    wrong_badge = simulated(demo.draw_face(badge_colour=(40, 80, 230), date_text=demo.DEMO_DATE_TEXT, lot_text=demo.DEMO_LOT_TEXT), 8, tmp_path / "wrong_badge.jpg")
    result = inspect_photo(wrong_badge, with_golden, tmp_path / "run4", read_zones=False)
    colour = [d for d in result["differences"] if d["kind"] == "colour"]
    assert colour and colour[0]["compared_with"] == "golden_photo"
    assert boxes_overlap(demo.BADGE_BOX, tuple(colour[0]["rect_px"]))
    assert colour[0]["delta_e_2000"] > 0


def test_per_product_compare_settings(product, tmp_path):
    blind = write_product(tmp_path / "blind", compare_settings={"strong_threshold": 1000.0, "weak_threshold": 1000.0})
    result = inspect_photo(product.reference_path.parent / "photo_bad.jpg", blind, tmp_path / "run", read_zones=False)
    assert result["status"] == "ok"                                  # thresholds from product.json were used

    with pytest.raises(ValueError, match="unknown compare_settings"):
        write_product(tmp_path / "typo", compare_settings={"strong_treshold": 1.0})
