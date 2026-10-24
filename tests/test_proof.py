"""Products made from a printer's proof sheet (packcheck/proof.py), on a SYNTHETIC proof.

The proof is drawn by demo.draw_proof_sheet(): the demo artwork with bleed, trim lines,
dashed seal lines, a copy-safety box and "8mm" labels - the layout of a real proof, none of
its content. The photos are of the faces as really printed: without any of that markup.
"""

import io

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from packcheck import demo, proof, server
from packcheck.pipeline import inspect_photo
from packcheck.product import load_product


@pytest.fixture(scope="module")
def proof_sheet():
    return demo.draw_proof_sheet()


@pytest.fixture(scope="module")
def proof_products(proof_sheet, tmp_path_factory):
    """Both faces of the synthetic proof turned into products, in their own folder."""
    sheet, rects, _ = proof_sheet
    folder = tmp_path_factory.mktemp("proof_products")
    faces = [{"face": name, "rect": rect} for name, rect in rects.items()]
    proof.create_products(sheet, folder, "DEMO-PROOF", "Demo dog food", faces, source="demo proof")
    return folder


def test_each_face_becomes_a_product_with_its_markup_mask(proof_products):
    for face in ("front", "back"):
        product = load_product(proof_products / f"demo-proof-{face}" / "product.json")
        assert product.face == face and product.pack == "DEMO-PROOF"
        assert product.markup_path is not None
        reference = cv2.imread(str(product.reference_path))
        assert reference.shape[:2] == (1720, 1320)   # exactly the face inside the trim line, no bleed


def test_markup_is_found_but_green_artwork_is_kept(proof_products):
    markup = cv2.imread(str(proof_products / "demo-proof-front" / "markup.png"), cv2.IMREAD_GRAYSCALE)
    height, width = markup.shape
    inset_x, inset_y = round(0.065 * width), round(0.065 * height)
    # Proof markup: the copy-safety box, a dashed seal line, an "8mm" label.
    assert markup[height // 2, inset_x] > 0
    assert markup[inset_y, width // 2] > 0
    assert markup[300:900, 45].max() > 0
    assert markup[40:75, width - 260:width - 190].max() > 0
    # Real print in the SAME green, inside the copy-safety box: never masked.
    brand_band = markup[demo.PROOF_MARGIN:demo.PROOF_MARGIN + 190, demo.PROOF_MARGIN:demo.PROOF_MARGIN + demo.ART_W]
    assert brand_band.max() == 0
    back = cv2.imread(str(proof_products / "demo-proof-back" / "markup.png"), cv2.IMREAD_GRAYSCALE)
    footer = back[demo.PROOF_MARGIN + 1355:demo.PROOF_MARGIN + 1395, demo.PROOF_MARGIN + 560:demo.PROOF_MARGIN + 800]
    assert footer.max() == 0   # the small green "DEMO PET FOODS" logo text


def test_a_photo_of_the_printed_pack_matches_its_proof(proof_sheet, proof_products, tmp_path):
    """The real point: a good pack has none of the proof markup, and must still pass."""
    _, _, printed = proof_sheet
    photo_path = tmp_path / "photo.jpg"
    cv2.imwrite(str(photo_path), demo.simulate_photo(printed["front"], seed=11), [cv2.IMWRITE_JPEG_QUALITY, 85])
    product = load_product(proof_products / "demo-proof-front" / "product.json")

    result = inspect_photo(photo_path, product, tmp_path / "with_mask", read_zones=False)
    assert result["status"] == "ok", result["differences"]

    # Without the mask the same photo is full of "missing" lines and labels: the mask is what fixes it.
    product.markup_path = None
    result = inspect_photo(photo_path, product, tmp_path / "without_mask", read_zones=False)
    assert result["status"] == "differences_found"


def test_a_real_defect_is_still_found_on_a_proof_product(proof_products, tmp_path):
    # A missing icon. (A single changed digit is NOT reliably found here: the margin makes the
    # artwork smaller in the 1400 px working image than in the plain demo - see Known limits.)
    bad = demo.proof_face(demo.draw_face(icons=2))
    photo_path = tmp_path / "photo_bad.jpg"
    cv2.imwrite(str(photo_path), demo.simulate_photo(bad, seed=12), [cv2.IMWRITE_JPEG_QUALITY, 85])
    product = load_product(proof_products / "demo-proof-front" / "product.json")
    result = inspect_photo(photo_path, product, tmp_path, read_zones=False)
    x, y, w, h = demo.ICON_BOXES[2]
    planted = (x + demo.PROOF_MARGIN, y + demo.PROOF_MARGIN, w, h)
    found = [difference["rect_px"] for difference in result["differences"]]
    # Reference pixels: the reference is 1720 px high, the pipeline works at 1400.
    scale = 1400 / 1720
    planted_working = [round(value * scale) for value in planted]
    assert any(_overlap(planted_working, box) for box in found), found


def test_an_artwork_already_in_the_database_is_refused(proof_sheet, proof_products):
    sheet, rects, _ = proof_sheet
    both = [{"face": "front", "rect": rects["front"]}, {"face": "back", "rect": rects["back"]}]

    # Same product code: refused, and no person can overrule it.
    with pytest.raises(proof.DuplicateArtwork) as same_code:
        proof.create_products(sheet, proof_products, "demo-proof", "again", both)
    assert not same_code.value.can_override and "already in the database" in str(same_code.value)

    # Another code, but the same artwork: refused because it LOOKS the same; a person may overrule.
    with pytest.raises(proof.DuplicateArtwork) as looks_same:
        proof.create_products(sheet, proof_products, "OTHER", "copy", both)
    assert looks_same.value.can_override and "looks the same" in str(looks_same.value)

    # Mistakes in the request itself.
    with pytest.raises(ValueError):
        proof.create_products(sheet, proof_products, "OTHER", "tiny", [{"face": "front", "rect": [0.1, 0.1, 0.001, 0.001]},
                                                                       {"face": "back", "rect": rects["back"]}])
    with pytest.raises(ValueError):
        proof.create_products(sheet, proof_products, "OTHER", "twice", [{"face": "front", "rect": rects["front"]},
                                                                        {"face": "Front", "rect": rects["back"]}])
    with pytest.raises(ValueError):
        proof.create_products(sheet, proof_products, "OTHER", "front only", [{"face": "front", "rect": rects["front"]}])
    assert not (proof_products / "other-front").exists()   # a refused request writes nothing


def test_a_lookalike_can_be_added_when_a_person_says_so(proof_sheet, tmp_path):
    sheet, rects, _ = proof_sheet
    both = [{"face": "front", "rect": rects["front"]}, {"face": "back", "rect": rects["back"]}]
    proof.create_products(sheet, tmp_path, "A", "first", both, barcode="123")
    with pytest.raises(proof.DuplicateArtwork):
        proof.create_products(sheet, tmp_path, "B", "lookalike", both)
    assert proof.create_products(sheet, tmp_path, "B", "lookalike", both, allow_similar=True) == ["b-front", "b-back"]
    # The barcode check is not overruled by allow_similar: one barcode, one artwork.
    with pytest.raises(proof.DuplicateArtwork) as same_barcode:
        proof.create_products(sheet, tmp_path, "C", "same barcode", both, barcode="123", allow_similar=True)
    assert "123" in str(same_barcode.value)


def test_a_pdf_proof_renders_with_its_colours_intact(proof_sheet):
    sheet, _, _ = proof_sheet
    buffer = io.BytesIO()
    Image.fromarray(cv2.cvtColor(sheet, cv2.COLOR_BGR2RGB)).save(buffer, format="PDF", resolution=proof.RENDER_DPI)
    rendered = proof.load_sheet(buffer.getvalue())
    # PDF sizes are in 1/72 inch, so a pixel can be gained in rounding.
    assert abs(rendered.shape[0] - sheet.shape[0]) <= 1 and abs(rendered.shape[1] - sheet.shape[1]) <= 1
    height, width = min(rendered.shape[0], sheet.shape[0]), min(rendered.shape[1], sheet.shape[1])
    # Blue must stay blue: rendering once swapped red and blue, which breaks the colour rules.
    assert np.abs(rendered[:height, :width].astype(int) - sheet[:height, :width].astype(int)).mean() < 3


def test_proof_page_and_api(proof_sheet, tmp_path):
    sheet, rects, _ = proof_sheet
    server.app.state.products_dir = tmp_path / "products"
    server.app.state.runs_dir = tmp_path / "runs"
    server.app.state.proofs_dir = tmp_path / "proofs"
    server.app.state.database_path = tmp_path / "test.sqlite"
    (tmp_path / "products").mkdir()
    ok, png = cv2.imencode(".png", sheet)
    with TestClient(server.app) as client:
        assert client.get("/artworks/new").status_code == 200
        uploaded = client.post("/api/proofs", files={"file": ("proof.png", png.tobytes(), "image/png")}).json()
        proof_id = uploaded["proof_id"]
        assert (uploaded["width"], uploaded["height"]) == (sheet.shape[1], sheet.shape[0])
        assert client.get(f"/api/proofs/{proof_id}/sheet.png").status_code == 200

        body = {"pack": "DEMO-PROOF", "name": "Demo dog food", "faces": [{"face": name, "rect": rect} for name, rect in rects.items()]}
        created = client.post(f"/api/proofs/{proof_id}/products", json=body)
        assert created.status_code == 200
        assert created.json()["product_ids"] == ["demo-proof-front", "demo-proof-back"]
        assert client.get("/api/products/demo-proof-front").json()["has_markup"] is True
        assert client.get("/api/products/demo-proof-front/markup.png").status_code == 200
        again = client.post(f"/api/proofs/{proof_id}/products", json=body)
        assert again.status_code == 409 and again.json()["detail"]["can_override"] is False

        assert client.get("/api/proofs/..%2F..%2Fproducts/sheet.png").status_code == 404
        assert client.post("/api/proofs", files={"file": ("x.pdf", b"not a proof", "application/pdf")}).status_code == 400
    for name in ("products_dir", "runs_dir", "proofs_dir", "database_path"):
        delattr(server.app.state, name)


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah
