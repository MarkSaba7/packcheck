"""Step 2 of the pipeline: find where the straightened photo differs from the reference.

The naive version ("subtract the two images") drowns in false alarms, because a photo
differs from the artwork EVERYWHERE a little: lighting, white balance, camera noise,
alignment that is off by a pixel. Each step below removes one harmless cause, so that
what is left over is (hopefully) a real difference.

  1. blur a little           -> removes camera noise and print dots
  2. correct the lighting    -> removes exposure, white balance, shadows and gradients
  3. convert to LAB colours  -> distances between colours match what a human sees ("delta E")
  4. shift-tolerant delta E  -> forgives alignment errors of a couple of pixels
  5. two passes over the resulting difference map:
       strong pass   a big difference on a few pixels      (changed text, missing element)
       weak pass     a small difference over a whole area  (an ink that is slightly off),
                     measured with CIEDE2000 on neighbourhood-averaged colours
  6. name each region: same shapes in both images -> "colour", otherwise -> "content"

Also here: find_glare() spots blown-out reflections so the pipeline can leave them out
(milestone 5), and delta_e_2000() is the CIEDE2000 formula.

Honest limit: step 2 cannot tell "the light was warmer" from "the ink is warmer". A colour
shift covering most of the face is therefore invisible here, and so is any shift smaller
than what the camera + lighting themselves introduce. Getting past that needs a controlled
photo spot, and a photo of a known-good pack as the colour reference (product.json
"golden_photo"), which the pipeline supports.
"""

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Difference:
    kind: str             # "content" (shapes differ) or "colour" (same shapes, different colour)
    rect: tuple           # (x, y, w, h) in reference pixels
    mean_delta_e: float   # average colour distance in the region (about 2 = barely visible, 10 = obvious)
    max_delta_e: float
    pixels: int           # size of the region that is over the threshold
    delta_e_2000: float = 0.0   # CIEDE2000 distance between the region's average colours (photo vs reference)


# ------------------------------------------------------------------- glare

# A glare pixel is (nearly) pure white in the photo: every channel this high...
GLARE_MIN_CHANNEL = 245
# ...and colourless: the channels within this much of each other.
GLARE_MAX_SPREAD = 15
# Only where the reference is NOT white itself (white paper that photographs white is not glare).
GLARE_REFERENCE_MAX_CHANNEL = 200
# Smaller blobs are ignored (a highlight on a letter edge, not a reflection).
GLARE_MIN_PIXELS = 150
# Above this share of the face, the photo is not worth judging: ask for a retake.
GLARE_RETAKE_FRACTION = 0.02


def find_glare(photo_bgr, reference_bgr, mask):
    """Where is the photo blown out by a reflection? Returns (glare mask, boxes, share of the face).

    Why: on glossy packs a lamp reflects as a white patch. Comparing it with the artwork
    would flag it as a "missing element" every time. Glare has a simple signature - pixels
    saturated to white in all channels where the artwork is not white - so we find those
    blobs, leave them out of the comparison, and tell the worker to retake if they are big.
    """
    saturated = photo_bgr.min(axis=2) >= GLARE_MIN_CHANNEL
    colourless = (photo_bgr.max(axis=2).astype(np.int16) - photo_bgr.min(axis=2)) <= GLARE_MAX_SPREAD
    reference_not_white = reference_bgr.min(axis=2) < GLARE_REFERENCE_MAX_CHANNEL
    glare = (saturated & colourless & reference_not_white & (mask > 0)).astype(np.uint8) * 255
    glare = cv2.morphologyEx(glare, cv2.MORPH_OPEN, _disc(5))

    boxes = _regions(glare, GLARE_MIN_PIXELS, merge_px=5)
    kept = np.zeros_like(glare)
    for x, y, w, h in boxes:
        kept[y:y + h, x:x + w] = glare[y:y + h, x:x + w]
    # Grow a little so the soft edge of the reflection is left out too.
    kept = cv2.dilate(kept, _disc(9))
    face_pixels = max(1, int((mask > 0).sum()))
    return kept, boxes, float((kept > 0).sum()) / face_pixels


# --------------------------------------------------------------- CIEDE2000

def delta_e_2000(lab_a, lab_b):
    """Colour distance per pixel with the CIEDE2000 formula (Sharma et al., 2005).

    Plain delta E (CIE76) is the straight-line distance in LAB. It over-estimates
    differences between saturated colours and under-estimates them for greys and
    blues, so a shift that looks small on the orange badge can score higher than a
    shift that looks obvious on a blue background. CIEDE2000 corrects for that with
    weighting for lightness, chroma and hue, at the cost of a long formula. Every step
    below follows the published equations; the variable names are theirs.
    """
    L1, a1, b1 = [lab_a[..., i].astype(np.float64) for i in range(3)]
    L2, a2, b2 = [lab_b[..., i].astype(np.float64) for i in range(3)]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    mean_C = (C1 + C2) / 2.0
    G = 0.5 * (1.0 - np.sqrt(mean_C ** 7 / (mean_C ** 7 + 25.0 ** 7)))
    a1_prime = (1.0 + G) * a1
    a2_prime = (1.0 + G) * a2
    C1_prime = np.hypot(a1_prime, b1)
    C2_prime = np.hypot(a2_prime, b2)
    h1_prime = np.degrees(np.arctan2(b1, a1_prime)) % 360.0
    h2_prime = np.degrees(np.arctan2(b2, a2_prime)) % 360.0

    delta_L = L2 - L1
    delta_C = C2_prime - C1_prime
    h_diff = h2_prime - h1_prime
    h_diff = np.where(h_diff > 180.0, h_diff - 360.0, h_diff)
    h_diff = np.where(h_diff < -180.0, h_diff + 360.0, h_diff)
    h_diff = np.where(C1_prime * C2_prime == 0.0, 0.0, h_diff)
    delta_H = 2.0 * np.sqrt(C1_prime * C2_prime) * np.sin(np.radians(h_diff / 2.0))

    mean_L = (L1 + L2) / 2.0
    mean_C_prime = (C1_prime + C2_prime) / 2.0
    h_sum = h1_prime + h2_prime
    mean_h = np.where(np.abs(h1_prime - h2_prime) > 180.0, (h_sum + 360.0) / 2.0, h_sum / 2.0)
    mean_h = np.where(mean_h >= 360.0, mean_h - 360.0, mean_h)
    mean_h = np.where(C1_prime * C2_prime == 0.0, h_sum, mean_h)

    T = (1.0 - 0.17 * np.cos(np.radians(mean_h - 30.0)) + 0.24 * np.cos(np.radians(2.0 * mean_h))
         + 0.32 * np.cos(np.radians(3.0 * mean_h + 6.0)) - 0.20 * np.cos(np.radians(4.0 * mean_h - 63.0)))
    S_L = 1.0 + 0.015 * (mean_L - 50.0) ** 2 / np.sqrt(20.0 + (mean_L - 50.0) ** 2)
    S_C = 1.0 + 0.045 * mean_C_prime
    S_H = 1.0 + 0.015 * mean_C_prime * T
    delta_theta = 30.0 * np.exp(-(((mean_h - 275.0) / 25.0) ** 2))
    R_C = 2.0 * np.sqrt(mean_C_prime ** 7 / (mean_C_prime ** 7 + 25.0 ** 7))
    R_T = -np.sin(np.radians(2.0 * delta_theta)) * R_C

    return np.sqrt((delta_L / S_L) ** 2 + (delta_C / S_C) ** 2 + (delta_H / S_H) ** 2
                   + R_T * (delta_C / S_C) * (delta_H / S_H))


def correct_lighting(photo_bgr, reference_bgr, mask, sigma):
    """Undo the lighting of the photo, using the reference as the guide.

    Physics: the colour of the light multiplies each channel (blue, green, red) by its own
    factor, and the amount of light multiplies all of them, changing slowly across the
    photo (a shadow, a lamp on one side). So for each channel we ask: "around this spot,
    how many times brighter is the photo than the reference?" - the ratio of two heavily
    blurred images - and divide the photo by that.

    This only holds for values proportional to light. Image files are not (they are
    "gamma encoded"), hence the ** 2.2 on the way in and ** (1 / 2.2) on the way out.

    Returns the corrected photo as float BGR in 0..1.
    """
    photo_light = (photo_bgr.astype(np.float32) / 255.0) ** 2.2
    reference_light = (reference_bgr.astype(np.float32) / 255.0) ** 2.2

    # Lighting changes slowly, so we can estimate it on a small copy of the images (8x smaller
    # each way = much faster) and stretch the result back to full size afterwards.
    # Only pixels inside the mask are averaged. Using the same mask on both sides keeps the
    # ratio fair near the edges and next to masked-out zones.
    inside = (mask > 0).astype(np.float32)[..., None]
    shrink = 1 / 8
    photo_small = cv2.resize(photo_light * inside, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA)
    reference_small = cv2.resize(reference_light * inside, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA)
    photo_average = cv2.GaussianBlur(photo_small, (0, 0), sigma * shrink)
    reference_average = cv2.GaussianBlur(reference_small, (0, 0), sigma * shrink)

    gain_small = np.ones_like(photo_average)
    known = reference_average > 1e-4
    gain_small[known] = photo_average[known] / reference_average[known]
    gain_small = np.clip(gain_small, 0.25, 4.0)
    height, width = photo_bgr.shape[:2]
    gain = cv2.resize(gain_small, (width, height), interpolation=cv2.INTER_LINEAR)

    corrected = np.clip(photo_light / gain, 0.0, 1.0)
    return corrected ** (1 / 2.2)


def to_lab(image_bgr_float):
    """Float BGR in 0..1 -> LAB with L in 0..100 and a, b roughly in -127..127.

    L = lightness, a = green<->red, b = blue<->yellow. In this space the straight-line
    distance between two colours (delta E) roughly matches how different they LOOK.
    """
    return cv2.cvtColor(image_bgr_float.astype(np.float32), cv2.COLOR_BGR2LAB)


def tolerant_delta_e(photo_lab, reference_lab, tolerance_px):
    """Colour distance per pixel that forgives being misaligned by up to `tolerance_px`.

    Alignment is never perfect. With a plain pixel-by-pixel distance, every letter edge that
    is 1 px off lights up as a "difference". So a photo pixel is accepted if its colour fits
    within the range of reference colours found up to `tolerance_px` around it:

        erode  = smallest value in the neighbourhood (per channel)
        dilate = largest value in the neighbourhood (per channel)
        distance = how far the pixel falls OUTSIDE [smallest, largest]   (0 if inside)

    We do it in both directions (photo vs reference range, reference vs photo range) so that
    a thin ADDED line and a thin MISSING line are both caught.
    Try tolerance_px=0 to see the plain version, and all the false alarms it produces.
    """
    if tolerance_px <= 0:
        return np.linalg.norm(photo_lab - reference_lab, axis=2)

    size = 2 * tolerance_px + 1
    kernel = np.ones((size, size), np.uint8)

    def distance_outside_range(image, other):
        smallest = cv2.erode(other, kernel)
        largest = cv2.dilate(other, kernel)
        below = np.maximum(smallest - image, 0.0)
        above = np.maximum(image - largest, 0.0)
        return np.linalg.norm(below + above, axis=2)

    return np.maximum(
        distance_outside_range(photo_lab, reference_lab),
        distance_outside_range(reference_lab, photo_lab),
    )


def find_differences(
    photo_bgr,
    reference_bgr,
    mask,
    tolerance_px=2,            # forgiven misalignment, in pixels
    strong_threshold=14.0,     # strong pass: delta E a single pixel must exceed...
    strong_min_pixels=40,      # ...on at least this many pixels (one changed digit in small print is only ~60)
    weak_threshold=6.0,        # weak pass: delta E a whole neighbourhood must exceed on average...
    weak_min_pixels=1500,      # ...over at least this large an area
    merge_px=15,               # different pixels closer than this belong to the same region
):
    """Return (list of Difference, delta E map). `mask` is 255 where comparing makes sense.

    The thresholds are starting values tuned on the synthetic demo. Real photos will need
    their own tuning (milestone 1) - raise them if you get false alarms, lower them if you
    miss real defects.
    """
    # 1. Blur both the same way, so they stay comparable.
    photo_blurred = cv2.GaussianBlur(photo_bgr, (0, 0), 1.5)
    reference_blurred = cv2.GaussianBlur(reference_bgr, (0, 0), 1.5)

    # 2-3. Correct the lighting, then move to LAB.
    lighting_sigma = 0.15 * max(reference_bgr.shape[:2])
    photo_lab = to_lab(correct_lighting(photo_blurred, reference_blurred, mask, lighting_sigma))
    reference_lab = to_lab(reference_blurred.astype(np.float32) / 255.0)

    # 4. The difference map.
    delta = tolerant_delta_e(photo_lab, reference_lab, tolerance_px)
    delta[mask == 0] = 0.0

    # 5a. Strong pass. Remove specks, then grow the blobs so the fragments of one defect
    #     (e.g. the strokes of a changed digit) merge into a single region.
    strong = (delta > strong_threshold).astype(np.uint8) * 255
    strong = cv2.morphologyEx(strong, cv2.MORPH_OPEN, _disc(3))
    rects = _regions(strong, strong_min_pixels, merge_px)

    # 5b. Weak pass. A tiny difference on one pixel is noise; the same tiny difference on every
    #     pixel of an area is a colour shift. So we average the COLOURS over a neighbourhood
    #     first, then measure the distance between the two averages with CIEDE2000, the
    #     formula that best matches how different two colours look.
    #     Areas the strong pass already found are skipped, so nothing is reported twice.
    photo_average = cv2.GaussianBlur(photo_lab, (0, 0), 6)
    reference_average = cv2.GaussianBlur(reference_lab, (0, 0), 6)
    neighbourhood_delta = delta_e_2000(photo_average, reference_average)
    neighbourhood_delta[mask == 0] = 0.0
    weak = (neighbourhood_delta > weak_threshold).astype(np.uint8) * 255
    weak = cv2.erode(weak, _disc(9))    # the blur bleeds a little over the edge of a masked-out zone; trim it back
    pad = 3 * merge_px
    for x, y, w, h in rects:
        weak[max(y - pad, 0):y + h + pad, max(x - pad, 0):x + w + pad] = 0
    rects += _regions(weak, weak_min_pixels, merge_px)

    # 6. Describe and name each region.
    differences = []
    for x, y, w, h in rects:
        values = delta[y:y + h, x:x + w]
        over = values[values > weak_threshold]
        kind = "colour" if _same_shapes(photo_lab, reference_lab, (x, y, w, h)) else "content"
        inside = mask[y:y + h, x:x + w] > 0
        if inside.any():
            photo_mean = photo_lab[y:y + h, x:x + w][inside].mean(axis=0)
            reference_mean = reference_lab[y:y + h, x:x + w][inside].mean(axis=0)
            region_delta_2000 = float(delta_e_2000(photo_mean[None, :], reference_mean[None, :])[0])
        else:
            region_delta_2000 = 0.0
        differences.append(Difference(
            kind=kind,
            rect=(x, y, w, h),
            mean_delta_e=round(float(over.mean()), 1) if over.size else 0.0,
            max_delta_e=round(float(values.max()), 1),
            pixels=int(over.size),
            delta_e_2000=round(region_delta_2000, 1),
        ))

    # Reading order (top to bottom, then left to right), so box numbers are easy to find.
    differences.sort(key=lambda d: (d.rect[1] // 50, d.rect[0]))
    return differences, delta


def _disc(diameter):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (diameter, diameter))


def _regions(binary, min_pixels, merge_px):
    """Group the white pixels of `binary` into regions; return their boxes as (x, y, w, h)."""
    merged = cv2.dilate(binary, _disc(merge_px))
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rects = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if int((binary[y:y + h, x:x + w] > 0).sum()) >= min_pixels:
            rects.append((x, y, w, h))
    return rects


def _same_shapes(photo_lab, reference_lab, rect):
    """Do both images show the same shapes in this box? Then only the colour changed.

    We compare the light/dark PATTERN (the L channel), not the colours: correlation is 1.0
    when the patterns match, near 0 when they have nothing in common (a missing icon,
    different text). Two flat, pattern-less patches also count as "same shapes".
    """
    x, y, w, h = rect
    photo_pattern = photo_lab[y:y + h, x:x + w, 0].ravel()
    reference_pattern = reference_lab[y:y + h, x:x + w, 0].ravel()
    photo_is_flat = photo_pattern.std() < 2.0
    reference_is_flat = reference_pattern.std() < 2.0
    if photo_is_flat or reference_is_flat:
        return photo_is_flat and reference_is_flat
    return float(np.corrcoef(photo_pattern, reference_pattern)[0, 1]) > 0.9
