"""One inspection from start to finish: photo in, result folder out.

    runs/<time>_<product_id>/
        photo.*            the original photo (when it came through the web page)
        comparison.jpg     reference (left) and straightened photo (right), differences boxed
        difference_<n>.jpg close-up of difference n: reference (left), photo (right)
        zone_<name>.png    one crop per variable zone (date, lot code...), cut from the original photo
        result.json        everything above as data - this is what reports will be built from

Two verdicts live in result.json, because they answer two different questions:

    status            is the FIXED print right?      ok | differences_found | alignment_failed
    readings_status   is the VARIABLE print right?   ok | invalid | check_by_hand | None (no zones)
"""

import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .align import AlignmentError, align_photo, crop_zone_from_original
from .compare import GLARE_RETAKE_FRACTION, find_differences, find_glare
from .reading import read_zone

# Working sizes: big enough to see small text, small enough to run in a second or two.
REFERENCE_SIDE = 1400   # the reference is resized so its longest side is this many pixels
PHOTO_SIDE = 2000       # same for the photo (phone photos are 4000+ px, more than we need)

BORDER_FRACTION = 0.012  # ignore a thin band around the face: real packs are never cut exactly like the artwork
MIN_COVERAGE = 0.97      # warn when the photo shows less than this share of the face

# Drawing colours (BGR). Same meaning everywhere, including the web page.
MAGENTA = (111, 0, 214)  # a difference
CYAN = (182, 137, 0)     # a variable zone (read it, don't compare it)
GREY = (150, 150, 150)   # an ignored zone


def new_run_dir(runs_dir, product_id):
    """Create runs/<time>_<product_id>/ and return it. The name sorts by time."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = Path(runs_dir) / f"{stamp}_{product_id}"
    run_dir.mkdir(parents=True)
    return run_dir


def inspect_photo(photo_path, product, out_dir, compare_settings=None, today=None, read_zones=True):
    """Compare one photo with one product. Writes the result files and returns the result dict.

    `compare_settings` overrides the thresholds of `compare.find_differences` for this one run
    (for example {"strong_threshold": 16.0}). scripts/evaluate.py uses it to try values
    without editing the code. `today` is the date the best-before rules are checked against
    (tests pass a fixed one). `read_zones=False` skips OCR, for callers that only want
    the comparison.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "product_id": product.product_id,
        "product_name": product.name,
        "photo": Path(photo_path).name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "status": None,            # "ok" | "differences_found" | "alignment_failed"   (the fixed print)
        "readings_status": None,   # "ok" | "invalid" | "check_by_hand" | None          (the variable print)
        "message": "",
        "warnings": [],
        "differences": [],
        "variable_zones": [],
    }

    reference, _ = _load_image(product.reference_path, REFERENCE_SIDE)
    original_photo = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
    if original_photo is None:
        raise ValueError(f"Could not read the image: {photo_path}")
    photo, photo_scale = _shrink(original_photo, PHOTO_SIDE)

    # Step 1: straighten the photo onto the reference.
    try:
        alignment = align_photo(photo, reference)
    except AlignmentError as error:
        result["status"] = "alignment_failed"
        result["message"] = str(error)
        _save_result(result, out_dir)
        return result
    result["alignment_inliers"] = alignment.inliers

    height, width = reference.shape[:2]
    zones_px = [(zone, zone.to_pixels(width, height)) for zone in product.zones]

    # Where does comparing make sense? Where the photo has pixels, minus the zones, minus the border.
    mask = cv2.erode(alignment.valid, np.ones((9, 9), np.uint8))
    coverage = float((mask > 0).mean())
    if coverage < MIN_COVERAGE:
        result["warnings"].append(f"The photo shows only {coverage:.0%} of the face. Retake it with the whole face visible.")
    for _, (x, y, w, h) in zones_px:
        mask[y:y + h, x:x + w] = 0
    # Proof markup (trim lines, "8mm" labels...) is in the reference but never on a pack.
    if product.markup_path is not None:
        markup = cv2.imread(str(product.markup_path), cv2.IMREAD_GRAYSCALE)
        markup = cv2.resize(markup, (width, height), interpolation=cv2.INTER_NEAREST)
        mask[markup > 0] = 0
    border = max(1, round(BORDER_FRACTION * max(width, height)))
    mask[:border, :] = 0
    mask[-border:, :] = 0
    mask[:, :border] = 0
    mask[:, -border:] = 0

    # Reflections: leave them out of the comparison and say so, instead of flagging them.
    glare_mask, glare_boxes, glare_fraction = find_glare(alignment.image, reference, mask)
    mask[glare_mask > 0] = 0
    retake = glare_fraction > GLARE_RETAKE_FRACTION
    result["glare"] = {"fraction": round(glare_fraction, 4), "rects_px": [list(box) for box in glare_boxes], "retake_recommended": retake}
    if glare_boxes:
        result["warnings"].append(f"Reflections cover {glare_fraction:.1%} of the face; those spots were not compared."
                                  + (" Retake the photo away from the light." if retake else ""))

    # The colour reference: a photo of a known-good pack when the product has one, else the artwork.
    golden = _aligned_golden_photo(product, reference, result)

    # Step 2: find the differences. Thresholds: defaults < product.json < this call.
    settings = dict(product.compare_settings)
    settings.update(compare_settings or {})
    differences = _find_differences_against_both(alignment.image, reference, golden, mask, settings)
    for number, (difference, compared_with) in enumerate(differences, start=1):
        x, y, w, h = difference.rect
        closeup_name = f"difference_{number}.jpg"
        shown_reference = golden.image if compared_with == "golden_photo" else reference
        cv2.imwrite(str(out_dir / closeup_name), _draw_closeup(shown_reference, alignment.image, difference.rect))
        result["differences"].append({
            "number": number,
            "kind": difference.kind,   # "content" or "colour"
            "rect_px": [x, y, w, h],
            "rect_fraction": [round(x / width, 4), round(y / height, 4), round(w / width, 4), round(h / height, 4)],
            "mean_delta_e": difference.mean_delta_e,
            "max_delta_e": difference.max_delta_e,
            "delta_e_2000": difference.delta_e_2000,
            "pixels": difference.pixels,
            "compared_with": compared_with,   # "artwork" or "golden_photo": what the close-up shows on the left
            "closeup": closeup_name,
        })
    differences = [difference for difference, _ in differences]

    # Step 3: variable zones. Cut each one out of the ORIGINAL photo (full detail), then read it.
    for zone, rect in zones_px:
        if zone.kind != "variable":
            continue
        crop = crop_zone_from_original(original_photo, alignment.homography, photo_scale, rect)
        crop_name = f"zone_{zone.name}.png"
        cv2.imwrite(str(out_dir / crop_name), crop)
        entry = {"name": zone.name, "crop": crop_name, "text": None, "valid": None, "reason": "", "lines": [], "readings": []}
        if read_zones:
            entry.update(read_zone(crop, zone.expect, today))
        result["variable_zones"].append(entry)
    result["readings_status"] = _readings_status(result["variable_zones"]) if read_zones else None

    comparison = _draw_comparison(reference, alignment.image, differences, zones_px)
    cv2.imwrite(str(out_dir / "comparison.jpg"), comparison, [cv2.IMWRITE_JPEG_QUALITY, 88])
    result["comparison_image"] = "comparison.jpg"

    result["status"] = "differences_found" if differences else "ok"
    result["message"] = f"{len(differences)} difference(s) found." if differences else "No differences found."
    _save_result(result, out_dir)
    return result


def _aligned_golden_photo(product, reference, result):
    """Load the product's golden photo (a known-good pack at the photo spot) and straighten it
    onto the reference, exactly like the inspected photo. None if there is none, or if it
    cannot be aligned (then colour is judged against the artwork, with a warning)."""
    if product.golden_path is None:
        return None
    golden, _ = _load_image(product.golden_path, PHOTO_SIDE)
    try:
        return align_photo(golden, reference)
    except AlignmentError as error:
        result["warnings"].append(f"The golden photo could not be aligned ({error}); colour was judged against the artwork instead.")
        return None


def _find_differences_against_both(photo, reference, golden, mask, settings):
    """Content is judged against the ARTWORK, colour against the GOLDEN PHOTO when there is one.

    Why: artwork is built for a press (CMYK, Pantone). Its on-screen colours never exactly
    match a correct print, so a colour verdict taken from it is only as good as that
    approximation. A photo of a pack everyone agrees is correct, taken at the same photo
    spot, is a far better colour reference - but it is a worse reference for text and
    layout (blur, its own tiny defects). So each reference judges what it is good at.

    Returns [(Difference, "artwork" | "golden_photo")] in reading order.
    """
    against_artwork, _ = find_differences(photo, reference, mask, **settings)
    if golden is None:
        return [(difference, "artwork") for difference in against_artwork]

    golden_mask = mask.copy()
    golden_mask[cv2.erode(golden.valid, np.ones((9, 9), np.uint8)) == 0] = 0
    against_golden, _ = find_differences(photo, golden.image, golden_mask, **settings)

    kept = [(difference, "artwork") for difference in against_artwork if difference.kind == "content"]
    kept += [(difference, "golden_photo") for difference in against_golden if difference.kind == "colour"]
    kept.sort(key=lambda item: (item[0].rect[1] // 50, item[0].rect[0]))
    return kept


def _readings_status(variable_zones):
    """Summarise the variable zones: one failed rule makes the pack "invalid"; one unreadable
    zone makes it "check_by_hand"; only all-valid is "ok". No zones at all: None."""
    if not variable_zones:
        return None
    if any(zone["valid"] is False for zone in variable_zones):
        return "invalid"
    if any(zone["valid"] is None for zone in variable_zones):
        return "check_by_hand"
    return "ok"


def _load_image(path, longest_side):
    """Read an image file and shrink it to the working size. Returns (image, scale applied)."""
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read the image: {path}")
    return _shrink(image, longest_side)


def _shrink(image, longest_side):
    scale = longest_side / max(image.shape[:2])
    if scale >= 1.0:   # only ever shrink; enlarging adds no information
        return image, 1.0
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def _draw_comparison(reference, aligned, differences, zones_px):
    """Reference on the left, straightened photo on the right, the same boxes on both."""
    panels = []
    for panel in (reference.copy(), aligned.copy()):
        for zone, (x, y, w, h) in zones_px:
            colour = CYAN if zone.kind == "variable" else GREY
            cv2.rectangle(panel, (x, y), (x + w, y + h), colour, 2)
            label_y = y + h + 24 if y + h + 30 < panel.shape[0] else y - 10   # under the box, or above if no room
            cv2.putText(panel, zone.name, (x, label_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA)
        for number, difference in enumerate(differences, start=1):
            x, y, w, h = difference.rect
            cv2.rectangle(panel, (x, y), (x + w, y + h), MAGENTA, 3)
            cv2.putText(panel, str(number), (x, max(y - 10, 28)), cv2.FONT_HERSHEY_SIMPLEX, 1.1, MAGENTA, 3, cv2.LINE_AA)
        panels.append(panel)
    gap = np.full((reference.shape[0], 24, 3), 255, np.uint8)
    return np.hstack([panels[0], gap, panels[1]])


def _draw_closeup(reference, aligned, rect, padding=40, min_height=220):
    """The same small area cut from both images, side by side: reference left, photo right."""
    x, y, w, h = rect
    top, bottom = max(y - padding, 0), min(y + h + padding, reference.shape[0])
    left, right = max(x - padding, 0), min(x + w + padding, reference.shape[1])
    pair = [reference[top:bottom, left:right], aligned[top:bottom, left:right]]
    zoom = max(1.0, min_height / (bottom - top))   # enlarge tiny areas (a single digit) so they are readable
    pair = [cv2.resize(image, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC) for image in pair]
    gap = np.full((pair[0].shape[0], 12, 3), 255, np.uint8)
    return np.hstack([pair[0], gap, pair[1]])


def _save_result(result, out_dir):
    (out_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
