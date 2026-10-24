"""A fake product to develop against - no confidential artwork needed.

We draw a made-up package face (the "artwork"), then fake two phone photos of it:

    photo_good.jpg   a correct pack: same print, plus the date/lot that every real pack has
    photo_bad.jpg    a pack with 3 planted defects:
                       - the round badge is a slightly different orange   (colour change)
                       - "NET WT 2.0 kg" became "2.5 kg"                  (text change)
                       - the third feature icon is missing                (missing element)

Each fake photo gets what a real one has: perspective, a table behind the pack, uneven
lighting, a warm colour cast, blur, noise, JPEG compression. Because we planted the
defects ourselves we know the right answer, so tests can check the pipeline finds them.
"""

import json
from pathlib import Path

import cv2
import numpy as np

ART_W, ART_H = 1000, 1400

# Colours are BGR (OpenCV order).
PAPER = (226, 238, 244)
INK = (40, 45, 50)
FOREST = (62, 104, 34)
ORANGE = (40, 140, 240)
ORANGE_OFF = (40, 112, 236)   # the "slightly wrong" badge orange
SKY = (190, 150, 60)
WHITE = (255, 255, 255)

FONT = cv2.FONT_HERSHEY_SIMPLEX
FONT_BOLD = cv2.FONT_HERSHEY_DUPLEX

# Where things sit on the face (x, y, w, h) in artwork pixels. Shared with the tests.
DATE_BOX = (590, 1170, 360, 170)
BADGE_BOX = (730, 250, 200, 200)
NET_WEIGHT_BOX = (60, 1255, 330, 60)
ICON_BOXES = [(90, 800, 130, 130), (270, 800, 130, 130), (450, 800, 130, 130)]
PLANTED_DEFECTS = {"badge_colour": BADGE_BOX, "net_weight_text": NET_WEIGHT_BOX, "missing_icon": ICON_BOXES[2]}

# What the simulated packs have printed in the date box. Far in the future so that the demo's
# "at least 180 days ahead" rule keeps passing for years; tests pin "today" anyway.
DEMO_DATE_TEXT = "2030-03-15"
DEMO_LOT_TEXT = "L26091 B"


def draw_face(badge_colour=ORANGE, net_weight="NET WT 2.0 kg", icons=3, date_text=None, lot_text=None):
    """Draw the package face. The defaults are the approved artwork; arguments plant changes."""
    face = np.full((ART_H, ART_W, 3), PAPER, np.uint8)

    # Brand band.
    cv2.rectangle(face, (0, 0), (ART_W, 190), FOREST, -1)
    cv2.putText(face, "DEMO PET FOODS", (60, 120), FONT_BOLD, 2.4, WHITE, 5, cv2.LINE_AA)
    cv2.putText(face, "since never - this brand does not exist", (64, 165), FONT, 0.8, (200, 225, 200), 2, cv2.LINE_AA)

    # Product name.
    cv2.putText(face, "CHICKEN", (60, 330), FONT_BOLD, 3.2, INK, 7, cv2.LINE_AA)
    cv2.putText(face, "& BROWN RICE", (60, 420), FONT_BOLD, 2.0, FOREST, 4, cv2.LINE_AA)
    cv2.putText(face, "Complete food for adult dogs", (64, 480), FONT, 1.1, INK, 2, cv2.LINE_AA)

    # Round badge (its colour is one of the planted defects).
    bx, by, bw, bh = BADGE_BOX
    centre = (bx + bw // 2, by + bh // 2)
    cv2.circle(face, centre, 95, badge_colour, -1)
    cv2.circle(face, centre, 95, WHITE, 4)
    cv2.putText(face, "NEW", (centre[0] - 62, centre[1] - 5), FONT_BOLD, 1.6, WHITE, 4, cv2.LINE_AA)
    cv2.putText(face, "recipe", (centre[0] - 52, centre[1] + 40), FONT, 1.0, WHITE, 2, cv2.LINE_AA)

    # "Illustration": a bowl made of simple shapes, to give the face some picture-like detail.
    cv2.rectangle(face, (60, 530), (940, 760), SKY, -1)
    cv2.ellipse(face, (500, 700), (260, 70), 0, 0, 360, (70, 90, 150), -1)
    cv2.ellipse(face, (500, 665), (260, 60), 0, 0, 360, (90, 130, 200), -1)
    kibble = np.random.default_rng(7)
    for _ in range(90):
        kx = int(kibble.integers(290, 710))
        ky = int(kibble.integers(630, 690))
        shade = int(kibble.integers(40, 110))
        cv2.circle(face, (kx, ky), int(kibble.integers(7, 14)), (shade, shade + 30, shade + 80), -1)

    # Feature icons (the third one can go missing).
    labels = [("A", "Vitamin A"), ("Ca", "Calcium"), ("O3", "Omega 3")]
    for (ix, iy, iw, ih), (symbol, caption) in list(zip(ICON_BOXES, labels))[:icons]:
        icon_centre = (ix + iw // 2, iy + 50)
        cv2.circle(face, icon_centre, 46, FOREST, 5)
        cv2.putText(face, symbol, (icon_centre[0] - 14 * len(symbol), icon_centre[1] + 14), FONT_BOLD, 1.2, FOREST, 3, cv2.LINE_AA)
        cv2.putText(face, caption, (ix, iy + 125), FONT, 0.7, INK, 2, cv2.LINE_AA)

    # Small print.
    small_print = [
        "Ingredients: chicken meal, brown rice, barley, chicken fat, dried beet pulp,",
        "flaxseed, natural flavour, salt, potassium chloride, vitamins, minerals.",
        "Feeding guide per day: 10 kg dog 150 g, 20 kg dog 260 g, 30 kg dog 350 g.",
        "Store in a cool dry place. Keep fresh water available at all times.",
    ]
    for line_number, line in enumerate(small_print):
        cv2.putText(face, line, (60, 1000 + 38 * line_number), FONT, 0.68, INK, 2, cv2.LINE_AA)

    # Net weight (its text is one of the planted defects).
    nx, ny, nw, nh = NET_WEIGHT_BOX
    cv2.putText(face, net_weight, (nx, ny + 45), FONT_BOLD, 1.4, INK, 3, cv2.LINE_AA)

    # Barcode-like stripes (real packs have one, and repeated patterns are hard for matching).
    stripes = np.random.default_rng(3)
    x = 60
    while x < 380:
        bar = int(stripes.integers(2, 7))
        cv2.rectangle(face, (x, 1335), (x + bar, 1385), INK, -1)
        x += bar + int(stripes.integers(2, 6))

    # Best-before box. EMPTY in the artwork: the date and lot are printed on each batch.
    dx, dy, dw, dh = DATE_BOX
    cv2.rectangle(face, (dx, dy), (dx + dw, dy + dh), WHITE, -1)
    cv2.rectangle(face, (dx, dy), (dx + dw, dy + dh), INK, 3)
    cv2.putText(face, "BEST BEFORE / LOT", (dx + 5, dy - 12), FONT, 0.8, INK, 2, cv2.LINE_AA)
    if date_text:
        cv2.putText(face, date_text, (dx + 25, dy + 72), FONT, 1.5, INK, 3, cv2.LINE_AA)
    if lot_text:
        cv2.putText(face, lot_text, (dx + 25, dy + 138), FONT, 1.3, INK, 3, cv2.LINE_AA)

    return face


def simulate_photo(face, seed):
    """Turn a flat face into something that looks like a handheld phone photo of it."""
    rng = np.random.default_rng(seed)
    canvas_w, canvas_h = 1500, 2000
    face_h, face_w = face.shape[:2]

    # A table: brown, with soft blotches.
    table = rng.normal(0, 18, (canvas_h // 20, canvas_w // 20)).astype(np.float32)
    table = cv2.resize(table, (canvas_w, canvas_h), interpolation=cv2.INTER_CUBIC)
    canvas = np.clip(np.dstack([table + 70, table + 95, table + 125]), 0, 255).astype(np.uint8)

    # Where the 4 corners of the pack land: smaller, rotated a few degrees, each corner nudged
    # separately (that is what perspective does).
    scale = 0.78 * min(canvas_w / face_w, canvas_h / face_h)
    angle = np.deg2rad(rng.uniform(-8, 8))
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    corners = np.float32([[0, 0], [face_w, 0], [face_w, face_h], [0, face_h]])
    centred = (corners - [face_w / 2, face_h / 2]) * scale
    placed = centred @ rotation.T + [canvas_w / 2, canvas_h / 2] + rng.uniform(-45, 45, (4, 2))
    transform = cv2.getPerspectiveTransform(corners, np.float32(placed))

    pack = cv2.warpPerspective(face, transform, (canvas_w, canvas_h), flags=cv2.INTER_AREA)
    pack_mask = cv2.warpPerspective(np.full((face_h, face_w), 255, np.uint8), transform, (canvas_w, canvas_h))
    canvas[pack_mask > 0] = pack[pack_mask > 0]

    # Uneven lighting: darker towards one corner.
    ys, xs = np.mgrid[0:canvas_h, 0:canvas_w].astype(np.float32)
    lighting = 0.80 + 0.30 * (0.6 * xs / canvas_w + 0.4 * ys / canvas_h)
    photo = canvas.astype(np.float32) * lighting[..., None]

    # Warm indoor light, a little blur, sensor noise.
    photo *= np.float32([0.92, 1.0, 1.06])
    photo = cv2.GaussianBlur(photo, (0, 0), 0.9)
    photo += rng.normal(0, 3.0, photo.shape).astype(np.float32)
    return np.clip(photo, 0, 255).astype(np.uint8)


# ----------------------------------------------------------------- back face

# The back face has one variable zone: the packing date. A packed-on date must be today or
# earlier, the opposite of a best-before date.
PACKED_ON_BOX = (560, 1230, 380, 110)
DEMO_PACKED_ON_TEXT = "2026-09-01"

# A made-up EAN-style number, shared by both faces because it belongs to the PACK. Used only to
# pick the product on the phone (milestone 4), never to verify anything.
DEMO_BARCODE = "4001234567890"


def draw_back_face(packed_on_text=None):
    """The back of the same pack: feeding table, ingredients, a packing-date box, a small logo.

    The brand logo is deliberately SMALL and elsewhere than on the front: a shared big logo
    would let a front photo "align" onto the back reference using the logo alone.
    """
    face = np.full((ART_H, ART_W, 3), PAPER, np.uint8)

    cv2.putText(face, "FEEDING GUIDE", (60, 120), FONT_BOLD, 2.0, FOREST, 5, cv2.LINE_AA)
    cv2.putText(face, "Grams per day, adult dogs", (64, 165), FONT, 0.9, INK, 2, cv2.LINE_AA)
    # A table: lines plus text.
    rows = [("Dog weight", "Normal", "Active"), ("5 kg", "90 g", "110 g"), ("10 kg", "150 g", "185 g"),
            ("20 kg", "260 g", "310 g"), ("30 kg", "350 g", "420 g"), ("40 kg", "430 g", "520 g")]
    top, row_height, columns = 210, 62, (60, 400, 680, 940)
    for row_number, row in enumerate(rows):
        y = top + row_number * row_height
        cv2.line(face, (columns[0], y), (columns[-1], y), INK, 2)
        for column_number, cell in enumerate(row):
            font = FONT_BOLD if row_number == 0 else FONT
            cv2.putText(face, cell, (columns[column_number] + 16, y + 44), font, 0.95, INK, 2, cv2.LINE_AA)
    cv2.line(face, (columns[0], top + len(rows) * row_height), (columns[-1], top + len(rows) * row_height), INK, 2)

    cv2.putText(face, "INGREDIENTS", (60, 680), FONT_BOLD, 1.3, FOREST, 3, cv2.LINE_AA)
    ingredients = [
        "Chicken meal (26%), brown rice (24%), barley, chicken fat, dried beet pulp,",
        "flaxseed, natural flavour, salt, potassium chloride, fish oil, dried kelp,",
        "vitamins (A, D3, E), minerals (zinc, iron, copper, manganese, iodine, selenium).",
    ]
    for line_number, line in enumerate(ingredients):
        cv2.putText(face, line, (60, 730 + 38 * line_number), FONT, 0.68, INK, 2, cv2.LINE_AA)

    cv2.putText(face, "ANALYTICAL CONSTITUENTS", (60, 900), FONT_BOLD, 1.3, FOREST, 3, cv2.LINE_AA)
    constituents = ["Protein 26%  Fat 15%  Fibre 3%  Ash 7%  Moisture 9%", "Calcium 1.2%  Phosphorus 0.9%  Omega 3 0.5%"]
    for line_number, line in enumerate(constituents):
        cv2.putText(face, line, (60, 950 + 38 * line_number), FONT, 0.68, INK, 2, cv2.LINE_AA)

    # A green info panel with an icon, to give the face some large-scale structure.
    cv2.rectangle(face, (60, 1050), (500, 1340), FOREST, -1)
    cv2.putText(face, "Made in", (90, 1120), FONT, 1.0, WHITE, 2, cv2.LINE_AA)
    cv2.putText(face, "Nowhere", (90, 1170), FONT_BOLD, 1.4, WHITE, 3, cv2.LINE_AA)
    cv2.circle(face, (280, 1270), 45, WHITE, 4)
    cv2.putText(face, "www.demo.example", (90, 1320), FONT, 0.7, WHITE, 2, cv2.LINE_AA)

    # Packed-on box (variable zone), empty in the artwork.
    px, py, pw, ph = PACKED_ON_BOX
    cv2.rectangle(face, (px, py), (px + pw, py + ph), WHITE, -1)
    cv2.rectangle(face, (px, py), (px + pw, py + ph), INK, 3)
    cv2.putText(face, "PACKED ON", (px + 5, py - 12), FONT, 0.8, INK, 2, cv2.LINE_AA)
    if packed_on_text:
        cv2.putText(face, packed_on_text, (px + 25, py + 75), FONT, 1.5, INK, 3, cv2.LINE_AA)

    # Small logo in the footer.
    cv2.putText(face, "DEMO PET FOODS", (560, 1385), FONT_BOLD, 0.9, FOREST, 2, cv2.LINE_AA)
    return face


# ----------------------------------------------------------------- proof sheet

# What a printer's proof adds around and on top of each face (see packcheck/proof.py).
MAGENTA = (140, 40, 220)       # trim and seal lines
PROOF_GREEN = (38, 96, 26)     # copy-safety box and its labels (measured on a real proof scan)
PROOF_MARGIN = 160             # blank margin around the demo artwork, so all its print is "copy safe"
PROOF_BLEED = 60               # artwork continued outside the trim line
PROOF_SHEET = (2400, 3600)     # sheet height, width in pixels
PROOF_FACE_ORIGINS = {"front": (300, 540), "back": (1950, 540)}   # top-left of each trimmed face on the sheet


def proof_face(face):
    """The demo artwork as it sits inside a proof's trim line: with a blank margin around it,
    like a real face whose text and logos all stay inside the copy-safety box."""
    return cv2.copyMakeBorder(face, PROOF_MARGIN, PROOF_MARGIN, PROOF_MARGIN, PROOF_MARGIN, cv2.BORDER_CONSTANT, value=PAPER)


def _draw_proof_markup(face):
    """Draw on a face what a proof draws inside the trim: copy-safety box, labels, arrows,
    dashed seal lines. None of it is ever printed on a pack."""
    height, width = face.shape[:2]
    inset_x, inset_y = round(0.065 * width), round(0.065 * height)
    cv2.rectangle(face, (inset_x, inset_y), (width - inset_x, height - inset_y), PROOF_GREEN, 2)
    cv2.arrowedLine(face, (width - 140, 4), (width - 140, inset_y), PROOF_GREEN, 2, tipLength=0.2)
    cv2.putText(face, "8mm", (width - 260, 70), FONT, 1.1, PROOF_GREEN, 2, cv2.LINE_AA)
    cv2.putText(face, "8mm", (12, height // 2), FONT, 0.9, PROOF_GREEN, 2, cv2.LINE_AA)
    cv2.putText(face, "8mm", (width // 2, height - 30), FONT, 1.1, PROOF_GREEN, 2, cv2.LINE_AA)
    for x in (45, width - 45):   # dashed seal lines
        for y in range(0, height, 32):
            cv2.line(face, (x, y), (x, min(y + 20, height - 1)), MAGENTA, 2)
    return face


def draw_proof_sheet():
    """A synthetic printer's proof: front and back of the demo pack on one sheet, with bleed,
    trim lines, proof markup, a title block and headings - the layout of a real proof, made of
    nothing confidential.

    Returns (sheet, {face name: [x, y, w, h] fractions of the sheet, drawn along the trim},
             {face name: the face as really printed, without markup}).
    """
    sheet_h, sheet_w = PROOF_SHEET
    sheet = np.full((sheet_h, sheet_w, 3), WHITE, np.uint8)
    cv2.rectangle(sheet, (100, 60), (sheet_w - 100, 260), INK, 2)
    cv2.putText(sheet, "DEMO PRINTERS - PROOF  DOCKET 0000  APPROVED BY: ____", (140, 180), FONT, 1.6, INK, 3, cv2.LINE_AA)

    rects, printed = {}, {}
    for name, artwork in (("front", draw_face()), ("back", draw_back_face())):
        face = proof_face(artwork)
        printed[name] = face.copy()
        height, width = face.shape[:2]
        left, top = PROOF_FACE_ORIGINS[name]
        bled = cv2.copyMakeBorder(face, PROOF_BLEED, PROOF_BLEED, PROOF_BLEED, PROOF_BLEED, cv2.BORDER_REPLICATE)
        sheet[top - PROOF_BLEED:top + height + PROOF_BLEED, left - PROOF_BLEED:left + width + PROOF_BLEED] = bled
        sheet[top:top + height, left:left + width] = _draw_proof_markup(face.copy())
        cv2.rectangle(sheet, (left - 3, top - 3), (left + width + 2, top + height + 2), MAGENTA, 3)   # trim line, just outside the face
        cv2.putText(sheet, name.upper(), (left + width // 2 - 120, top - 140), FONT_BOLD, 2.5, (190, 110, 20), 5, cv2.LINE_AA)
        cv2.putText(sheet, "10mm Bleed", (left + width // 2 - 150, top - 75), FONT, 1.3, MAGENTA, 3, cv2.LINE_AA)
        rects[name] = [left / sheet_w, top / sheet_h, width / sheet_w, height / sheet_h]
    return sheet, rects, printed


def _fraction(box):
    x, y, w, h = box
    return [round(x / ART_W, 4), round(y / ART_H, 4), round(w / ART_W, 4), round(h / ART_H, 4)]


def write_demo_product(products_dir):
    """Create products/demo-dog-food-front/ and products/demo-dog-food-back/ with reference,
    product.json and simulated photos. Returns the front folder."""
    products_dir = Path(products_dir)

    # Front: reference + a good photo + a photo with 3 planted defects.
    front = products_dir / "demo-dog-food-front"
    front.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(front / "reference.png"), draw_face())
    good_pack = draw_face(date_text=DEMO_DATE_TEXT, lot_text=DEMO_LOT_TEXT)
    bad_pack = draw_face(badge_colour=ORANGE_OFF, net_weight="NET WT 2.5 kg", icons=2,
                         date_text=DEMO_DATE_TEXT, lot_text=DEMO_LOT_TEXT)
    cv2.imwrite(str(front / "photo_good.jpg"), simulate_photo(good_pack, seed=1), [cv2.IMWRITE_JPEG_QUALITY, 85])
    cv2.imwrite(str(front / "photo_bad.jpg"), simulate_photo(bad_pack, seed=2), [cv2.IMWRITE_JPEG_QUALITY, 85])
    (front / "product.json").write_text(json.dumps({
        "product_id": "demo-dog-food-front",
        "name": "Demo dog food 2 kg - front",
        "pack": "demo-dog-food",
        "face": "front",
        "barcode": DEMO_BARCODE,
        "reference_image": "reference.png",
        "zones": [
            {
                "name": "best_before_lot",
                "kind": "variable",
                "rect": _fraction(DATE_BOX),
                # What must be printed here, and the rules it must pass (see product.py).
                "expect": [
                    {"name": "best_before", "kind": "date", "formats": ["%Y-%m-%d"], "min_days_ahead": 180},
                    {"name": "lot", "kind": "code", "pattern": "L[0-9]{5}[A-Z]"},
                ],
            }
        ],
    }, indent=2), encoding="utf-8")

    # Back: reference + one good photo.
    back = products_dir / "demo-dog-food-back"
    back.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(back / "reference.png"), draw_back_face())
    cv2.imwrite(str(back / "photo_good.jpg"), simulate_photo(draw_back_face(DEMO_PACKED_ON_TEXT), seed=3), [cv2.IMWRITE_JPEG_QUALITY, 85])
    (back / "product.json").write_text(json.dumps({
        "product_id": "demo-dog-food-back",
        "name": "Demo dog food 2 kg - back",
        "pack": "demo-dog-food",
        "face": "back",
        "barcode": DEMO_BARCODE,
        "reference_image": "reference.png",
        "zones": [
            {
                "name": "packed_on",
                "kind": "variable",
                "rect": _fraction(PACKED_ON_BOX),
                "expect": [{"name": "packed_on", "kind": "date", "formats": ["%Y-%m-%d"], "max_days_ahead": 0}],
            }
        ],
    }, indent=2), encoding="utf-8")

    return front
