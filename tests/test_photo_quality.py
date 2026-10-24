"""The photo check the phone runs right after each photo: blurry, too dark, too bright.

The limits were set on the demo's simulated photos, so every demo photo must pass, and a
clearly spoiled copy of one must fail with the right reason.
"""

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from packcheck import demo, server
from packcheck.photo_quality import check_photo

DEMO_PHOTOS = [("demo-dog-food-front", "photo_good.jpg"), ("demo-dog-food-front", "photo_bad.jpg"),
               ("demo-dog-food-back", "photo_good.jpg")]


@pytest.fixture
def client(products_dir, tmp_path):
    server.app.state.products_dir = products_dir
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    with TestClient(server.app) as client:
        yield client


def good_photo(products_dir):
    return cv2.imread(str(products_dir / "demo-dog-food-front" / "photo_good.jpg"))


def codes(image):
    return [problem["code"] for problem in check_photo(image)]


def test_every_demo_photo_passes(products_dir):
    for product_id, name in DEMO_PHOTOS:
        assert check_photo(cv2.imread(str(products_dir / product_id / name))) == [], name
    # Fresh simulated photos too, with other random seeds (other angles and noise).
    for seed in (5, 6, 7):
        assert check_photo(demo.simulate_photo(demo.draw_face(), seed)) == []
        assert check_photo(demo.simulate_photo(demo.draw_back_face(), seed)) == []


def test_a_spoiled_photo_fails_for_the_right_reason(products_dir):
    photo = good_photo(products_dir)
    assert codes(cv2.GaussianBlur(photo, (0, 0), 6)) == ["blurry"]
    # A dark photo also has weak edges, so it may ALSO be blurry - but darkness comes first.
    assert codes((photo * 0.2).astype(np.uint8))[0] == "too_dark"
    assert codes(np.clip(photo * 2.0, 0, 255).astype(np.uint8)) == ["too_bright"]


def test_the_numbers_do_not_depend_on_the_photo_size(products_dir):
    photo = good_photo(products_dir)
    bigger = cv2.resize(photo, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    assert check_photo(bigger) == []
    blurred = cv2.GaussianBlur(photo, (0, 0), 6)
    assert codes(cv2.resize(blurred, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)) == ["blurry"]


def test_the_endpoint(client, products_dir):
    good = (products_dir / "demo-dog-food-front" / "photo_good.jpg").read_bytes()
    assert client.post("/api/photos/check", files={"photo": ("p.jpg", good, "image/jpeg")}).json() == {"ok": True, "problems": []}

    blurred = cv2.imencode(".jpg", cv2.GaussianBlur(good_photo(products_dir), (0, 0), 6))[1].tobytes()
    body = client.post("/api/photos/check", files={"photo": ("p.jpg", blurred, "image/jpeg")}).json()
    assert body["ok"] is False and [problem["code"] for problem in body["problems"]] == ["blurry"]
    assert body["problems"][0]["value"] < body["problems"][0]["limit"]

    assert client.post("/api/photos/check", files={"photo": ("p.jpg", b"not a photo", "image/jpeg")}).status_code == 400
