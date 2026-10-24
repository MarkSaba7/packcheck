"""The scanning flow: desk QR -> phone connects -> front + back sent -> result -> R79 form.

All on the synthetic demo pack (front + back). The demo's photo_bad.jpg front has 3
planted differences; photo_good.jpg of each face has none.
"""

import shutil

import cv2
import pytest
from fastapi.testclient import TestClient

from packcheck import r79, server, storage
from packcheck.identify import identify_pack
from packcheck.product import list_products


@pytest.fixture
def client(products_dir, tmp_path):
    server.app.state.products_dir = products_dir
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    with TestClient(server.app) as client:
        yield client


def photos(products_dir, front="photo_good.jpg", back="photo_good.jpg"):
    front_path = products_dir / "demo-dog-food-front" / front
    back_path = products_dir / "demo-dog-food-back" / back
    return {"front": ("front.jpg", front_path.read_bytes(), "image/jpeg"), "back": ("back.jpg", back_path.read_bytes(), "image/jpeg")}


def test_the_pages_are_served(client):
    for page in ("/", "/desk", "/phone", "/history", "/artworks/new"):
        assert client.get(page).status_code == 200, page
    home = client.get("/").text
    assert 'href="/desk"' in home and 'href="/artworks/new"' in home   # the two buttons
    # Browsers must check for a newer page, stylesheet or script instead of mixing an old one
    # with a new page (the redesign once showed up unstyled because of exactly that).
    for path in ("/", "/static/packcheck.css", "/static/common.js", "/static/strings.json"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_both_photos_identify_the_pack(products_dir):
    products = list_products(products_dir)
    front = cv2.imread(str(products_dir / "demo-dog-food-front" / "photo_good.jpg"))
    back = cv2.imread(str(products_dir / "demo-dog-food-back" / "photo_good.jpg"))
    found = identify_pack(front, back, products)
    assert found.pack.pack == "demo-dog-food" and not found.swapped
    # Photos in the wrong slots: still found, and said so.
    found = identify_pack(back, front, products)
    assert found.pack.pack == "demo-dog-food" and found.swapped


def test_desk_sees_the_phone_connect(client):
    session_id = client.post("/api/sessions").json()["session_id"]
    assert client.get(f"/api/sessions/{session_id}").json() == {"session_id": session_id, "connected": False, "checking": False}
    assert client.post(f"/api/sessions/{session_id}/connect").status_code == 200
    assert client.get(f"/api/sessions/{session_id}").json()["connected"] is True
    assert client.post("/api/sessions/nope/connect").status_code == 404


def test_a_scan_with_differences_goes_from_pending_to_refused(client, products_dir):
    session_id = client.post("/api/sessions").json()["session_id"]
    scan = client.post("/api/packs/inspect", data={"session_id": session_id, "inspector": "Sam Tremblay"},
                       files=photos(products_dir, front="photo_bad.jpg")).json()
    assert scan["status"] == "inspected"
    assert [face["face"] for face in scan["faces"]] == ["front", "back"]
    assert len(scan["faces"][0]["differences"]) == 3 and scan["faces"][1]["differences"] == []

    # The desk finds both faces through the session, front first.
    seen = client.get("/api/inspections", params={"session": session_id}).json()
    assert [inspection["product_id"] for inspection in seen] == ["demo-dog-food-front", "demo-dog-food-back"]
    assert {inspection["pack_id"] for inspection in seen} == {scan["pack_id"]}

    pack = client.get(f"/api/packs/{scan['pack_id']}").json()
    assert pack["verdict"] == "pending" and pack["missing"] == [["r79.undecided", {"count": 3}]]

    differences = seen[0]["differences"]
    # Sent exactly as desk.html sends them: empty note, null point. (Null once gave a 422.)
    for difference in differences[:2]:
        dismissed = client.post(f"/api/differences/{difference['id']}/decisions",
                                json={"decision": "dismiss", "note": "", "checklist_item": None})
        assert dismissed.status_code == 200, dismissed.text
    bad = client.post(f"/api/differences/{differences[2]['id']}/decisions",
                      json={"decision": "confirm", "note": "2.5 kg", "checklist_item": "net_weight"})
    assert bad.json()["checklist_item"] == "net_weight"
    assert client.post(f"/api/differences/{differences[2]['id']}/decisions",
                       json={"decision": "confirm", "checklist_item": "made_up"}).status_code == 400

    assert client.get(f"/api/packs/{scan['pack_id']}").json()["verdict"] == "refused"
    form = client.get(f"/report/pack/{scan['pack_id']}").text
    assert "R79- Validation des matériaux imprimés à la réception" in form
    assert "Mention du poids net" in form and "2.5 kg" in form
    assert "Initiales" in form and ">ST<" in form                          # Sam Tremblay
    assert "window.print()" in client.get(f"/report/pack/{scan['pack_id']}", params={"print": True}).text
    assert "R79 - Validation of printed materials" in client.get(f"/report/pack/{scan['pack_id']}", params={"lang": "en"}).text


def test_a_clean_scan_is_accepted_and_only_the_first_is_a_first_reception(client, products_dir):
    first = client.post("/api/packs/inspect", files=photos(products_dir)).json()
    second = client.post("/api/packs/inspect", files=photos(products_dir)).json()
    assert client.get(f"/api/packs/{first['pack_id']}").json()["verdict"] == "accepted"

    with storage.connect(server.app.state.database_path) as connection:
        assert server.pack_form(connection, first["pack_id"])["first_reception"] is True
        assert server.pack_form(connection, second["pack_id"])["first_reception"] is False

    history = client.get("/api/packs", params={"range": "week"}).json()
    assert [pack["pack_id"] for pack in history] == [second["pack_id"], first["pack_id"]]   # newest first
    assert client.get("/api/packs", params={"range": "year"}).status_code == 400


def test_always_ignore_this_spot_adds_an_ignore_zone(products_dir, tmp_path):
    # A copy of the demo: this test changes the artwork's zones, the shared demo must not change.
    own_products = tmp_path / "products"
    shutil.copytree(products_dir, own_products)
    server.app.state.products_dir = own_products
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    with TestClient(server.app) as client:
        scan = client.post("/api/packs/inspect", files=photos(own_products, front="photo_bad.jpg")).json()
        first = scan["faces"][0]["differences"][0]

        answer = client.post(f"/api/differences/{first['id']}/ignore-spot", json={"note": None}).json()   # as the desk sends it
        assert answer["decision"]["decision"] == "dismiss" and answer["product_id"] == "demo-dog-food-front"
        client.post(f"/api/differences/{first['id']}/ignore-spot")     # twice: still one zone
        zones = [zone for zone in client.get("/api/products/demo-dog-food-front").json()["zones"] if zone["name"] == answer["zone"]]
        assert len(zones) == 1 and zones[0]["kind"] == "ignore"
        x, y, w, h = first["rect_fraction"]
        zx, zy, zw, zh = zones[0]["rect"]
        assert zx <= x and zy <= y and zx + zw >= x + w and zy + zh >= y + h   # covers the box, with a margin

        # The same pack again: the ignored spot is no longer flagged, the other two still are.
        again = client.post("/api/packs/inspect", files=photos(own_products, front="photo_bad.jpg")).json()
        assert len(again["faces"][0]["differences"]) == 2
        assert client.post("/api/differences/999999/ignore-spot").status_code == 404


def test_photos_of_nothing_known_ask_instead_of_guessing(client, tmp_path):
    blank = cv2.imencode(".jpg", (cv2.UMat(400, 300, cv2.CV_8UC3, (200, 200, 200)).get()))[1].tobytes()
    body = client.post("/api/packs/inspect", files={"front": ("a.jpg", blank, "image/jpeg"), "back": ("b.jpg", blank, "image/jpeg")}).json()
    assert body["status"] == "identify_failed" and body["candidates"] == []


def test_the_upc_point_uses_the_barcode_read_in_the_photos(products_dir):
    """fill_form on its own: a code read that is not the artwork's is a cross; none read is not."""
    products = {product.product_id: product for product in list_products(products_dir)}

    def inspection(product_id, codes):
        return {"id": 1, "product_id": product_id, "product_name": products[product_id].name, "created_at": "2026-10-23T10:00:00",
                "status": "ok", "pack_id": "p", "inspector": "", "differences": [], "readings": [], "codes_read": codes, "run": "r"}

    def upc(form):
        return next(item for item in form["items"] if item["key"] == "upc")

    expected = products["demo-dog-food-front"].barcode
    right = r79.fill_form([inspection("demo-dog-food-front", [expected]), inspection("demo-dog-food-back", [])], products)
    assert upc(right)["state"] is True and right["verdict"] == "accepted"
    wrong = r79.fill_form([inspection("demo-dog-food-front", ["999"]), inspection("demo-dog-food-back", [])], products)
    assert upc(wrong)["state"] is False and wrong["verdict"] == "refused"
    unread = r79.fill_form([inspection("demo-dog-food-front", []), inspection("demo-dog-food-back", [])], products)
    assert upc(unread)["state"] is True and upc(unread)["comments"] == [("r79.upc_not_read", {})]
    one_face = r79.fill_form([inspection("demo-dog-food-front", [expected])], products)
    assert one_face["verdict"] == "pending" and ("r79.missing_face", {"face": "back"}) in one_face["missing"]


def test_an_old_database_gets_the_new_columns(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.sqlite")
    old.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
    old.execute("INSERT INTO sessions VALUES ('abc', '2026-01-01T00:00:00')")
    old.commit()
    old.close()
    with storage.connect(tmp_path / "old.sqlite") as connection:
        assert storage.get_session(connection, "abc")["connected_at"] is None
