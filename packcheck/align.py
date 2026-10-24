"""Step 1 of the pipeline: line the photo up with the reference.

A phone photo is never taken perfectly straight: the package is rotated, tilted,
closer or further away. Before any pixel can be compared, we have to undo that and
warp the photo so it sits exactly on top of the reference image.

How it works:
  1. Find distinctive points (letter corners, logo edges...) in both images.    -> SIFT
  2. Pair up the points that look the same in both images.                      -> matcher + ratio test
  3. Find the perspective transform (a 3x3 matrix called a "homography") that
     maps the photo points onto the reference points, ignoring the bad pairs.   -> RANSAC
  4. Apply that transform to the whole photo.                                   -> warpPerspective

Steps 1-3 (find points, pair them, agree on a transform) are also exactly what is needed
to answer a different question: "which of our products IS this photo of?" (identify.py).
So they live in `extract_features` and `match_features` below, and `align_photo` is steps
1-3 plus step 4 (the warp), which only the chosen product needs.

Important limit: a homography is only exact for a FLAT surface. It works for boxes and
flat labels. A wrinkled bag or a curved can will only line up approximately.
"""

from dataclasses import dataclass

import cv2
import numpy as np

# A pair of points is kept only if its best match is clearly better than its second best
# (Lowe's ratio test). Lower = stricter.
RATIO_TEST = 0.75

# Below this many agreeing point pairs we refuse to compare at all. This doubles as a
# "wrong product / unusable photo" detector.
MIN_INLIERS = 25

# The agreeing points must also be SPREAD over the reference: at least this fraction of its
# width and of its height. Why: the front and back of a pack share small text (ingredients,
# a logo). Those shared bits alone can give 25+ agreeing points, all on one line - enough
# to pass the count, but they only say where that line is, not where the face is. The
# resulting warp is garbage and everything else gets flagged. Measured on the demo: a
# right match spreads over 80-90% of the face, a wrong one over 3% or 0%.
MIN_INLIER_SPREAD = 0.3


class AlignmentError(Exception):
    """The photo could not be matched to the reference (wrong product, blurry, too far...)."""


@dataclass
class Features:
    """The distinctive points of one image (a photo or a reference), found once and then
    reused: identify.py matches one photo's features against many products' reference
    features without re-running SIFT on the reference every time."""
    keypoints: list
    descriptors: np.ndarray   # None if the image had no usable detail at all
    shape: tuple              # (height, width) of the image these points were found in


@dataclass
class Match:
    """The result of trying to match one Features against another.

    `ok=False` means the two images should NOT be treated as the same face: either too few
    points agree, or they agree but only on a small shared area (see MIN_INLIER_SPREAD).
    `reason` explains which, in words a worker or a log can show as-is.
    """
    ok: bool
    reason: str = ""
    homography: np.ndarray = None
    inliers: int = 0
    spread_x: float = 0.0
    spread_y: float = 0.0


@dataclass
class Alignment:
    image: np.ndarray       # the photo, warped into the reference frame (same size as the reference)
    valid: np.ndarray       # uint8 mask: 255 where the warped photo has real pixels, 0 where it has none
    inliers: int            # number of point pairs that agree with the transform = our confidence
    homography: np.ndarray  # the 3x3 transform photo -> reference, kept so zones can be cut from the original photo


# A zone is never enlarged more than this, whatever the photo's resolution: bigger crops only
# cost OCR time without adding detail.
MAX_ZONE_SCALE = 4.0


def extract_features(bgr_image):
    """Find this image's distinctive points once. Cheap to call for a photo (once per
    inspection); identify.py caches the result for reference images (used for every photo)."""
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=5000)
    keypoints, descriptors = sift.detectAndCompute(gray, None)
    return Features(keypoints=keypoints, descriptors=descriptors, shape=bgr_image.shape[:2])


def match_features(photo_features, reference_features):
    """Do these two images show the same face? See the module docstring for the 3 steps."""
    if photo_features.descriptors is None or reference_features.descriptors is None:
        return Match(ok=False, reason="No usable detail found in the photo or in the reference.")

    # 2. For every photo point, find its 2 most similar reference points, keep clear winners only.
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    candidates = matcher.knnMatch(photo_features.descriptors, reference_features.descriptors, k=2)
    good = []
    for pair in candidates:
        if len(pair) == 2 and pair[0].distance < RATIO_TEST * pair[1].distance:
            good.append(pair[0])
    # A reference point may be paired only once: keep its closest photo point. Otherwise a
    # repeated pattern (barcode stripes, kibble) pairs dozens of photo points with the same
    # reference point, and those bogus pairs can outvote the real ones.
    best_per_reference_point = {}
    for match in good:
        current = best_per_reference_point.get(match.trainIdx)
        if current is None or match.distance < current.distance:
            best_per_reference_point[match.trainIdx] = match
    good = list(best_per_reference_point.values())
    if len(good) < MIN_INLIERS:
        return Match(ok=False, reason=(
            f"Only {len(good)} matching points between the photo and the reference (need {MIN_INLIERS}). "
            "Wrong product, or the photo is too blurry, too dark or too far away."
        ))

    # 3. The transform that most pairs agree on. RANSAC tries many random subsets of pairs and
    #    keeps the transform with the most supporters ("inliers"); wrong pairs get outvoted.
    from_photo = np.float32([photo_features.keypoints[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    to_reference = np.float32([reference_features.keypoints[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography, inlier_mask = cv2.findHomography(from_photo, to_reference, cv2.RANSAC, 3.0)
    inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
    if homography is None or inliers < MIN_INLIERS:
        return Match(ok=False, reason=(
            f"Only {inliers} matching points agree on how the package sits in the photo (need {MIN_INLIERS}). "
            "Wrong product, or the package is not flat enough in the photo."
        ))

    # 3b. Are the agreeing points spread over the face, or all on one shared line?
    height, width = reference_features.shape
    agreeing = to_reference.reshape(-1, 2)[inlier_mask.ravel() > 0]
    spread_x = (agreeing[:, 0].max() - agreeing[:, 0].min()) / width
    spread_y = (agreeing[:, 1].max() - agreeing[:, 1].min()) / height
    if spread_x < MIN_INLIER_SPREAD or spread_y < MIN_INLIER_SPREAD:
        return Match(ok=False, inliers=inliers, spread_x=spread_x, spread_y=spread_y, reason=(
            f"The {inliers} agreeing points cover only {spread_x:.0%} x {spread_y:.0%} of the face "
            f"(need {MIN_INLIER_SPREAD:.0%}). Probably the wrong face or product: only a shared detail matched."
        ))

    return Match(ok=True, homography=homography, inliers=inliers, spread_x=spread_x, spread_y=spread_y)


def align_photo(photo_bgr, reference_bgr):
    photo_features = extract_features(photo_bgr)
    reference_features = extract_features(reference_bgr)
    match = match_features(photo_features, reference_features)
    if not match.ok:
        raise AlignmentError(match.reason)

    # 4. Warp the photo into the reference frame. Warping an all-white mask the same way tells
    #    us which reference pixels the photo actually covers.
    height, width = reference_bgr.shape[:2]
    warped = cv2.warpPerspective(photo_bgr, match.homography, (width, height))
    white = np.full(photo_bgr.shape[:2], 255, np.uint8)
    valid = cv2.warpPerspective(white, match.homography, (width, height), flags=cv2.INTER_NEAREST)

    return Alignment(image=warped, valid=valid, inliers=match.inliers, homography=match.homography)


def crop_zone_from_original(original_photo, homography, photo_scale, rect):
    """Cut one zone out of the ORIGINAL photo, straightened, at the detail the photo really has.

    Why: the pipeline works on a shrunken copy of the photo (2000 px), fine for spotting
    differences but too coarse for reading small dot-matrix dates. The original phone photo
    has 2-4x more pixels. Instead of warping the whole original (slow, huge), we warp just
    the zone rectangle.

    `homography` maps the WORKING photo onto the reference; `photo_scale` is how much the
    working photo was shrunk (0.5 = half size). `rect` is (x, y, w, h) in reference pixels.
    """
    x, y, w, h = rect

    # 1. How many original-photo pixels does the zone cover? Reference -> working photo
    #    (inverse homography) -> original photo (divide by the shrink factor).
    corners_reference = np.float32([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]).reshape(-1, 1, 2)
    corners_original = cv2.perspectiveTransform(corners_reference, np.linalg.inv(homography)).reshape(4, 2) / photo_scale
    top_width = np.linalg.norm(corners_original[1] - corners_original[0])
    left_height = np.linalg.norm(corners_original[3] - corners_original[0])
    photo_pixels_per_reference_pixel = min(top_width / w, left_height / h)
    factor = float(np.clip(photo_pixels_per_reference_pixel, 1.0, MAX_ZONE_SCALE))

    # 2. One transform from the original photo straight to the crop. Matrices apply right to
    #    left: shrink to working size, then the homography, then move the zone's corner to
    #    (0, 0) and enlarge by `factor`.
    shrink = np.diag([photo_scale, photo_scale, 1.0])
    move_and_enlarge = np.array([[factor, 0.0, -x * factor], [0.0, factor, -y * factor], [0.0, 0.0, 1.0]])
    transform = move_and_enlarge @ homography @ shrink

    crop_size = (max(1, round(w * factor)), max(1, round(h * factor)))
    return cv2.warpPerspective(original_photo, transform, crop_size, flags=cv2.INTER_LINEAR)
