"""The JSON API end to end, through FastAPI's TestClient (no real network).

The app is pointed at temporary folders (products, runs, database) so the tests never touch
the developer's real data.
"""

import json

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from packcheck import codes, server, storage


@pytest.fixture
def client(products_dir, tmp_path):
    server.app.state.products_dir = products_dir
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    with TestClient(server.app) as client:
        yield client


def upload(path):
    return {"photo": (path.name, path.read_bytes(), "image/jpeg")}


def demo_photo(products_dir, name="photo_bad.jpg", face="front"):
    return products_dir / f"demo-dog-food-{face}" / name


def test_pages_and_assets_are_served(client):
    for path in ("/", "/desk", "/products/demo-dog-food-front/zones", "/static/strings.json", "/static/common.js", "/static/packcheck.css"):
        response = client.get(path)
        assert response.status_code == 200, path
    assert client.get("/products/nope/zones").status_code == 404
    strings = client.get("/static/strings.json").json()
    assert set(strings["fr"]) == set(strings["en"])    # every sentence exists in both languages


def test_products_list_both_faces_of_the_demo_pack(client):
    products = client.get("/api/products").json()
    assert [p["product_id"] for p in products] == ["demo-dog-food-back", "demo-dog-food-front"]
    assert {p["pack"] for p in products} == {"demo-dog-food"}
    assert {p["face"] for p in products} == {"front", "back"}


def test_inspection_decision_and_report(client, products_dir):
    response = client.post("/api/inspections", data={"product_id": "demo-dog-food-front", "work_order": "WO-7", "inspector": "Sam"},
                           files=upload(demo_photo(products_dir)))
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "differences_found"
    assert result["run"] and result["inspection_id"]
    assert all("id" in difference for difference in result["differences"])

    first = result["differences"][0]
    decision = client.post(f"/api/differences/{first['id']}/decisions", json={"decision": "confirm", "note": "badge is off"}).json()
    assert decision["decision"] == "confirm" and decision["decided_by"] == "Sam" or decision["decided_by"] is None

    day = result["created_at"][:10]
    listed = client.get("/api/inspections", params={"day": day}).json()
    assert len(listed) == 1 and listed[0]["work_order"] == "WO-7"
    assert listed[0]["differences"][0]["decision"]["note"] == "badge is off"

    # A clean pack on the same work order: the report must handle "no differences" rows too.
    clean = client.post("/api/inspections", data={"product_id": "demo-dog-food-front", "work_order": "WO-7"},
                        files=upload(demo_photo(products_dir, "photo_good.jpg")))
    assert clean.status_code == 200 and clean.json()["status"] == "ok"

    report = client.get("/report", params={"work_order": "WO-7"})
    assert report.status_code == 200
    report = report.text
    assert "Demo dog food 2 kg - front" in report and "badge is off" in report and "Confirmed" in report
    assert "2030-03-15" in report                      # the readings are on the report too
    assert report.count("<article") == 2
    french = client.get("/report", params={"work_order": "WO-7", "lang": "fr"}).text
    assert "Rapport de contrôle" in french and "Confirmée" in french


def test_inspection_without_a_product_id_is_identified_from_the_photo(client, products_dir):
    # No product_id at all: the server must work out it is the FRONT face on its own.
    response = client.post("/api/inspections", data={}, files=upload(demo_photo(products_dir, "photo_bad.jpg")))
    result = response.json()
    assert response.status_code == 200, response.text
    assert result["product_id"] == "demo-dog-food-front"
    assert result["status"] == "differences_found"

    # "auto" is the same as omitting it.
    response = client.post("/api/inspections", data={"product_id": "auto"}, files=upload(demo_photo(products_dir, "photo_good.jpg", "back")))
    assert response.json()["product_id"] == "demo-dog-food-back"


def test_unidentifiable_photo_asks_the_worker_to_pick_instead_of_guessing(client, tmp_path):
    noise = np.random.default_rng(0).integers(0, 255, (600, 450, 3), dtype=np.uint8)
    photo_path = tmp_path / "noise.jpg"
    cv2.imwrite(str(photo_path), cv2.GaussianBlur(noise, (0, 0), 3))

    response = client.post("/api/inspections", data={}, files=upload(photo_path))
    body = response.json()
    assert response.status_code == 200          # a legitimate outcome, not a server error
    assert body["status"] == "identify_failed"
    assert body["candidates"] == []
    assert body["message"]
    assert "run" not in body                     # nothing was inspected or stored


def test_bad_requests(client, tmp_path):
    assert client.post("/api/inspections", data={"product_id": "nope"}, files={"photo": ("x.jpg", b"123", "image/jpeg")}).status_code == 404
    assert client.post("/api/inspections", data={"product_id": "demo-dog-food-front"}, files={"photo": ("x.jpg", b"not an image", "image/jpeg")}).status_code == 400
    assert client.get("/api/inspections").status_code == 400
    assert client.post("/api/differences/12345/decisions", json={"decision": "confirm"}).status_code == 404
    assert client.get("/report").status_code == 400


def test_server_url_prefers_a_lan_address_over_localhost(products_dir, tmp_path, monkeypatch):
    # This is the bug a worker actually hit: open the page as http://localhost:8000 (the
    # obvious thing to type on the same computer), and a QR built from that address is
    # useless to a phone, because "localhost" there means the phone itself.
    server.app.state.products_dir = products_dir
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"

    monkeypatch.setattr(server, "local_network_ip", lambda: "192.168.1.42")
    with TestClient(server.app, base_url="http://localhost:8000") as opened_as_localhost:
        assert opened_as_localhost.get("/api/server-url").json() == {"url": "http://192.168.1.42:8000"}

    # No LAN address could be found at all (e.g. an offline dev machine): fall back to
    # whatever host the browser used, rather than failing outright.
    monkeypatch.setattr(server, "local_network_ip", lambda: None)
    with TestClient(server.app, base_url="http://localhost:8000") as opened_as_localhost:
        assert opened_as_localhost.get("/api/server-url").json() == {"url": "http://localhost:8000"}


def test_local_network_ip_never_crashes():
    # Whatever this machine's network looks like, it must return a string or None, never raise.
    result = server.local_network_ip()
    assert result is None or isinstance(result, str)


def test_desk_and_phone_qr_use_the_lan_address_not_localhost(products_dir, tmp_path, monkeypatch):
    server.app.state.products_dir = products_dir
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    monkeypatch.setattr(server, "local_network_ip", lambda: "192.168.1.42")

    with TestClient(server.app, base_url="http://localhost:8000") as opened_as_localhost:
        for page in ("/", "/desk"):
            html = opened_as_localhost.get(page).text
            # The comment explaining WHY is allowed to mention it; the actual QR-building
            # code must not read location.origin (only /api/server-url's result).
            assert "encodeURIComponent(location.origin)" not in html, f"{page} still builds its QR link from location.origin"


def test_desk_session_and_qr(client, products_dir):
    session_id = client.post("/api/sessions").json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").status_code == 200
    assert client.get("/api/sessions/unknown").status_code == 404

    link = f"http://192.168.1.10:8000/?session={session_id}"
    png = client.get("/api/qr.png", params={"text": link})
    assert png.headers["content-type"] == "image/png"
    assert codes.decode_codes(codes.decode_png_bytes(png.content)) == [link]

    # The phone sends its inspections with the session id; the desk asks for them by that id.
    response = client.post("/api/inspections", data={"product_id": "demo-dog-food-front", "session_id": session_id},
                           files=upload(demo_photo(products_dir, "photo_good.jpg")))
    assert response.status_code == 200, response.text
    seen = client.get("/api/inspections", params={"session": session_id}).json()
    assert len(seen) == 1 and seen[0]["status"] == "ok"
    assert client.get("/api/inspections", params={"session": session_id, "after_id": seen[0]["id"]}).json() == []
    assert client.post("/api/inspections", data={"product_id": "demo-dog-food-front", "session_id": "nope"},
                       files=upload(demo_photo(products_dir, "photo_good.jpg"))).status_code == 404


def test_pick_product_by_scanning_its_code(client, tmp_path):
    from packcheck.demo import DEMO_BARCODE
    qr = tmp_path / "code.png"
    qr.write_bytes(codes.make_qr_png(DEMO_BARCODE))
    found = client.post("/api/products/lookup", files={"photo": ("code.png", qr.read_bytes(), "image/png")}).json()
    assert found["codes"] == [DEMO_BARCODE]
    assert sorted(p["product_id"] for p in found["products"]) == ["demo-dog-food-back", "demo-dog-food-front"]

    other = tmp_path / "other.png"
    other.write_bytes(codes.make_qr_png("0000000000000"))
    assert client.post("/api/products/lookup", files={"photo": ("o.png", other.read_bytes(), "image/png")}).json()["products"] == []


def test_login_is_required_once_a_user_exists(client, products_dir, tmp_path):
    assert client.get("/api/me").json() == {"login_required": False, "user": None}
    with storage.connect(tmp_path / "test.sqlite") as connection:
        storage.add_user(connection, "Sam", "1234")
    assert client.get("/api/me").json()["login_required"] is True

    photo = upload(demo_photo(products_dir, "photo_good.jpg"))
    assert client.post("/api/inspections", data={"product_id": "demo-dog-food-front"}, files=photo).status_code == 401
    assert client.post("/api/login", json={"name": "Sam", "pin": "0000"}).status_code == 401
    assert client.post("/api/login", json={"name": "Sam", "pin": "1234"}).status_code == 200
    assert client.get("/api/me").json()["user"] == "Sam"

    result = client.post("/api/inspections", data={"product_id": "demo-dog-food-front", "inspector": "Someone else"}, files=photo).json()
    assert result["inspector"] == "Sam"                # the login wins over the typed name
    client.post("/api/logout")
    assert client.get("/api/me").json()["user"] is None


def test_zone_editor_saves_and_rejects(client, products_dir):
    product = client.get("/api/products/demo-dog-food-back").json()
    assert [zone["name"] for zone in product["zones"]] == ["packed_on"]
    assert client.get("/api/products/demo-dog-food-back/reference.png").headers["content-type"] == "image/png"

    new_zones = product["zones"] + [{"name": "logo_sticker", "kind": "ignore", "rect": [0.5, 0.95, 0.4, 0.04], "expect": []}]
    saved = client.put("/api/products/demo-dog-food-back/zones", json={"zones": new_zones}).json()
    assert [zone["name"] for zone in saved["zones"]] == ["packed_on", "logo_sticker"]
    on_disk = json.loads((products_dir / "demo-dog-food-back" / "product.json").read_text(encoding="utf-8"))
    assert on_disk["zones"][1]["kind"] == "ignore" and on_disk["barcode"]     # other fields kept

    bad = client.put("/api/products/demo-dog-food-back/zones", json={"zones": [{"name": "x", "kind": "weird", "rect": [0, 0, 1, 1]}]})
    assert bad.status_code == 400
    still = json.loads((products_dir / "demo-dog-food-back" / "product.json").read_text(encoding="utf-8"))
    assert len(still["zones"]) == 2                     # the bad save changed nothing

    client.put("/api/products/demo-dog-food-back/zones", json={"zones": product["zones"]})   # restore for other tests
