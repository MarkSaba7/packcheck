"""Turn a printer's proof sheet into products: one reference image per face.

What a proof looks like: the printer sends ONE sheet (usually a PDF, often a scan of the
signed paper) with every face of the pack side by side - "FRONT", "BACK" - plus a title
block, dimension arrows, notes and signatures around them. Each face is drawn with:

    bleed         extra artwork outside the cut line, cut away in production
    trim line     a thin magenta line: where the pack is really cut
    seal lines    dashed magenta lines: where the pack is heat-sealed
    copy safety   a thin green box, "keep all text and logos inside this area", with
                  "8mm"-style labels and arrows in the margin around it

None of that is printed on the real pack. Compared with a photo, every arrow and line
would be reported as a "missing element". So this module does two things:

  1. cut_face        cut one face out of the sheet, exactly along the rectangle a person
                     drew around its trim line (proof sheets differ too much between
                     printers to find the faces reliably by themselves - the never-guess
                     rule says ask, so the proof page lets a person draw them)
  2. find_markup     find the proof markup INSIDE that face, as a mask of pixels the
                     pipeline must not compare (pipeline.py reads it as "markup_mask")

How markup is found: a pixel is markup when it is
    - magenta or green (the colours printers use for trim and safety lines), and
    - part of a THIN or SMALL shape (lines, arrows, "8mm" labels - not a large filled
      area such as a green background running to the edge), and
    - in the outer MARGIN of the face, outside the copy-safety box.
The margin rule is what keeps real artwork safe: the printer's own rule is that no text or
logo goes outside the copy-safety box, so a thin green shape there is a label, while the
same green inside the box (a green brand name, a parrot's feathers) is print and is kept.

Honest limits:
  - Handwritten approval marks (ticks next to each checked line) are grey like printed
    text; no colour rule can tell them apart. Draw "ignore" zones over them in the zone
    editor - or, better, ask the printer for the unsigned print-ready PDF.
  - Markup in other colours, or drawn inside the copy-safety box, is not found. The zone
    editor shows the mask over the artwork so a person can see what is skipped.
  - A scan of a proof is not the artwork: its colours went through a printer and a
    scanner. Text and layout compare fine; for colour, use a golden photo (milestone 5).
"""

import json
import re
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from .align import extract_features, match_features
from .identify import REFERENCE_SIDE, _cached_reference_features   # the same features photos are matched with
from .product import list_products

# 300 dpi: what proof scans are usually stored at. Rendering lower throws detail away
# (small ingredient text becomes unreadable); rendering higher only adds empty pixels.
RENDER_DPI = 300

# The outer margin, as a fraction of the face's width / height, where markup is looked for.
# On the proofs seen so far the copy-safety box sits 8 mm inside a 125 mm face (6.4%); a
# little more catches labels drawn right on its line.
MARGIN_FRACTION = 0.08

# Colours of proof markup, in OpenCV's HSV (hue 0-180, saturation and value 0-255).
# Magenta: trim and seal lines. Green: the copy-safety box, its labels and arrows. The
# green reaches hue 94 because on a blue background the scanner blends the dark green
# labels towards blue (measured: hue 81-94 on the labels, 98-99 on the blue itself).
MAGENTA = {"hue": (150, 178), "min_saturation": 50, "value": (90, 255)}
GREEN = {"hue": (45, 94), "min_saturation": 120, "value": (30, 210)}

# A shape counts as thin when at least this share of it disappears when we keep only what a
# disc of THICK_PX pixels fits into. Lines and small text vanish completely; filled areas
# (a green background, a logo) mostly stay.
THICK_PX = 11
THIN_SHARE = 0.8

# A shape also counts as markup, thin or not, when it fits in a box this fraction of the
# face's size: the scan blurs a bold "8mm" label on a dark background into one solid blob.
SMALL_FRACTION = 0.10

# The mask is grown by this many pixels: the scan blurs every line into a soft halo.
GROW_PX = 7


def load_sheet(file_bytes, page=1):
    """A proof file (PDF, or a PNG / JPEG scan) -> the sheet as a BGR image."""
    if file_bytes[:5] == b"%PDF-":
        document = pdfium.PdfDocument(file_bytes)
        if not 1 <= page <= len(document):
            raise ValueError(f"The PDF has {len(document)} page(s); page {page} does not exist.")
        # PDF units are 1/72 inch. Through PIL (always RGB, like render_artwork.py) because
        # pdfium's own buffer is already in a blue-first order that is easy to convert twice.
        rgb = np.array(document[page - 1].render(scale=RENDER_DPI / 72).to_pil().convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    image = cv2.imdecode(np.frombuffer(file_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("That file is neither a PDF nor an image the server can read.")
    return image


def cut_face(sheet, rect):
    """Cut the face inside `rect` ([x, y, w, h] as fractions of the sheet) out of the sheet."""
    height, width = sheet.shape[:2]
    x, y, w, h = rect
    if not all(0.0 <= value <= 1.0 for value in rect) or x + w > 1.0001 or y + h > 1.0001:
        raise ValueError(f"The face rectangle must lie inside the sheet, got {rect}.")
    left, top = round(x * width), round(y * height)
    right, bottom = round((x + w) * width), round((y + h) * height)
    if right - left < 100 or bottom - top < 100:
        raise ValueError("The face rectangle is too small: draw it along the trim line of the whole face.")
    return sheet[top:bottom, left:right].copy()


def find_markup(face):
    """The proof markup inside one face, as a uint8 mask: 255 = markup, do not compare."""
    hsv = cv2.cvtColor(face, cv2.COLOR_BGR2HSV)
    coloured = _colour_mask(hsv, MAGENTA) | _colour_mask(hsv, GREEN)
    # Join the pixels of one stroke (a scan breaks thin lines into dots) before judging shapes.
    coloured = cv2.morphologyEx(coloured, cv2.MORPH_CLOSE, _disc(3))

    height, width = face.shape[:2]
    margin = np.ones((height, width), np.uint8) * 255
    band_x, band_y = round(MARGIN_FRACTION * width), round(MARGIN_FRACTION * height)
    margin[band_y:height - band_y, band_x:width - band_x] = 0

    markup = _thin_or_small_shapes(coloured, max_side=SMALL_FRACTION * max(width, height)) & margin
    return cv2.dilate(markup, _disc(GROW_PX))


def _colour_mask(hsv, colour):
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    inside = ((hue >= colour["hue"][0]) & (hue <= colour["hue"][1]) & (saturation >= colour["min_saturation"])
              & (value >= colour["value"][0]) & (value <= colour["value"][1]))
    return inside.astype(np.uint8) * 255


def _thin_or_small_shapes(mask, max_side):
    """Keep the connected shapes of `mask` that are thin almost everywhere (see THIN_SHARE)
    or that fit in a max_side x max_side box. Large filled areas are dropped."""
    thick = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _disc(THICK_PX))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    thin_pixels = np.bincount(labels[(mask > 0) & (thick == 0)], minlength=count)
    is_thin = thin_pixels >= THIN_SHARE * stats[:, cv2.CC_STAT_AREA]
    is_small = np.maximum(stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT]) <= max_side
    keep = is_thin | is_small
    keep[0] = False   # label 0 is the background
    return (keep[labels] & (mask > 0)).astype(np.uint8) * 255


def _disc(diameter):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))


def product_id_for(pack, face):
    """"80520S" + "front" -> "80520s-front": lower case, letters, digits and dashes only, so it
    is safe as a folder name and in a URL."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{pack}-{face}".lower()).strip("-")
    if not slug or slug == "-":
        raise ValueError("The pack id and face name must contain letters or digits.")
    return slug


# Two references count as the SAME artwork when at least this share of the smaller one's
# points agree. Measured on the Hagen proofs: the same proof uploaded twice shares 42-96%;
# the 2 mm and 4 mm pouches, different products of one range, share 6-23%.
# Honest limit: a NEW scan of the same paper may share less; the code and barcode checks
# below catch that case when the same codes are typed.
SAME_ARTWORK_SHARE = 0.35


class DuplicateArtwork(Exception):
    """The artwork is already in the database. `can_override` is True only for the visual
    check: a person may know better ("it looks the same, but it is a new version")."""

    def __init__(self, message, can_override=False):
        super().__init__(message)
        self.can_override = can_override


def find_duplicate(products, pack, barcode, faces_bgr):
    """Raise DuplicateArtwork if this artwork, its code or its barcode is already known.
    `faces_bgr` maps face name -> the new face image."""
    for product in products:
        if product.pack and product.pack.lower() == pack.lower():
            raise DuplicateArtwork(f"This artwork is already in the database: {product.name} (code {product.pack}).")
        if barcode and product.barcode == barcode:
            raise DuplicateArtwork(f"The barcode {barcode} already belongs to {product.name}.")
    for face_name, face in faces_bgr.items():
        scale = REFERENCE_SIDE / max(face.shape[:2])
        small = cv2.resize(face, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else face
        new = extract_features(small)
        for product in products:
            if product.face != face_name:
                continue
            known = _cached_reference_features(product)
            match = match_features(new, known)
            share = match.inliers / max(1, min(len(new.keypoints), len(known.keypoints)))
            if match.ok and share >= SAME_ARTWORK_SHARE:
                raise DuplicateArtwork(f"The {face_name} looks the same as {product.name}: {share:.0%} of its details match.",
                                       can_override=True)


def create_products(sheet, products_dir, pack, name, faces, source="", barcode=None, label_code=None, allow_similar=False):
    """Write one product folder per face: reference.png, markup.png and product.json.

    `faces` is a list of {"face": "front", "rect": [x, y, w, h]} (fractions of the sheet), and
    must contain a front AND a back: a scan always photographs both. Nothing is written if
    the artwork is already in the database (see find_duplicate) - unless the only reason is
    that it LOOKS the same and `allow_similar` says a person checked. Returns the product ids.
    """
    products_dir = Path(products_dir)
    pack, name, barcode = pack.strip(), name.strip(), (barcode or "").strip() or None
    if not pack:
        raise ValueError("Give the artwork a product code (for example the item number).")
    names = sorted(entry["face"].strip().lower() for entry in faces)
    if names != ["back", "front"]:
        raise ValueError("Draw exactly two faces: the front and the back.")
    planned = []
    for entry in faces:
        face_name = entry["face"].strip().lower()
        planned.append((product_id_for(pack, face_name), face_name, cut_face(sheet, entry["rect"]), entry["rect"]))

    existing = list_products(products_dir)
    try:
        find_duplicate(existing, pack, barcode, {face_name: face for _, face_name, face, _ in planned})
    except DuplicateArtwork as error:
        if not (error.can_override and allow_similar):
            raise
    for product_id, *_ in planned:
        if (products_dir / product_id).exists():   # a folder with no valid product.json in it
            raise DuplicateArtwork(f"A folder called {product_id} already exists in products/. Pick another product code.")

    for product_id, face_name, face, rect in planned:
        folder = products_dir / product_id
        folder.mkdir(parents=True)
        cv2.imwrite(str(folder / "reference.png"), face)
        cv2.imwrite(str(folder / "markup.png"), find_markup(face))
        (folder / "product.json").write_text(json.dumps({
            "product_id": product_id,
            "name": f"{name or pack} - {face_name}",
            "pack": pack,
            "face": face_name,
            "barcode": barcode,
            "label_code": (label_code or "").strip() or None,
            "reference_image": "reference.png",
            "markup_mask": "markup.png",   # proof markup: never compared (see proof.py)
            "zones": [],
            # Where this reference came from, so anyone can check the cut later.
            "source": {"proof": source, "rect_on_sheet": rect},
        }, indent=2), encoding="utf-8")
    return [product_id for product_id, *_ in planned]
