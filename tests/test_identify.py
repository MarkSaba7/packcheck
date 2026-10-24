"""Choosing the product FROM the photo instead of from a list.

The demo already gives us two genuinely different faces (front, back) that share some
detail (a small logo, similar fonts) - exactly the case that must NOT be confused. See
packcheck/identify.py for why each check exists.
"""

import cv2
import numpy as np
import pytest

from packcheck import identify
from packcheck.product import list_products


@pytest.fixture
def products(products_dir):
    return list_products(products_dir)


def test_a_front_photo_is_identified_as_the_front_product(products):
    front = next(p for p in products if p.face == "front")
    photo = cv2.imread(str(front.reference_path.parent / "photo_good.jpg"))
    result = identify.identify_product(photo, products)

    assert result.product_id == "demo-dog-food-front"
    assert result.candidates[0].product_id == "demo-dog-food-front"
    # The back must not even squeak onto the list: it fails the spread check (align.py),
    # same as align_photo would refuse it outright.
    assert all(candidate.product_id != "demo-dog-food-back" for candidate in result.candidates)


def test_a_back_photo_is_identified_as_the_back_product(products):
    back = next(p for p in products if p.face == "back")
    photo = cv2.imread(str(back.reference_path.parent / "photo_good.jpg"))
    result = identify.identify_product(photo, products)
    assert result.product_id == "demo-dog-food-back"


def test_photo_of_nothing_recognisable_matches_no_product(products):
    noise = np.random.default_rng(0).integers(0, 255, (1200, 900, 3), dtype=np.uint8)
    blurred = cv2.GaussianBlur(noise, (0, 0), 3)
    result = identify.identify_product(blurred, products)
    assert result.product_id is None
    assert result.candidates == []
    assert result.message


def test_when_two_products_are_equally_plausible_neither_is_auto_picked(products):
    # Two products with IDENTICAL references: any photo of one matches both equally well.
    # The margin rule must refuse to pick, exactly like a human would ask "which one?".
    front = next(p for p in products if p.face == "front")
    twin = type(front)(**{**front.__dict__, "product_id": "demo-dog-food-front-twin"})
    photo = cv2.imread(str(front.reference_path.parent / "photo_good.jpg"))

    result = identify.identify_product(photo, [front, twin])
    assert result.product_id is None
    assert len(result.candidates) == 2
    assert result.message


def test_reference_feature_cache_is_reused_and_invalidated(products, tmp_path):
    front = next(p for p in products if p.face == "front")
    identify._reference_features_cache.clear()
    first = identify._cached_reference_features(front)
    second = identify._cached_reference_features(front)
    assert first is second   # same object: the second call hit the cache, did not recompute

    # Touching the file (a new mtime) must force a recompute, so an edited reference.png
    # (new artwork) is never matched with stale features.
    import time
    time.sleep(0.01)
    front.reference_path.touch()
    third = identify._cached_reference_features(front)
    assert third is not first
