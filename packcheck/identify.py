"""Step 0 (optional) of the pipeline: which product IS this photo of?

Why this exists: originally a worker always picked the product from a list before
photographing. That is one more thing to get right, and workers already know it from
looking at the pack - they shouldn't have to find it in a list too. This module answers
"which product?" the same way align.py answers "how is it rotated?": find the reference
whose distinctive points line up with the photo's.

How it works: try the photo against EVERY product's reference, the same point-matching
align.py uses to straighten a photo (align.extract_features, align.match_features), and
rank the ones that pass by how many points agreed. Then apply the same rule as everywhere
else in this project: never guess. If the best match is not clearly ahead of the next
best, we refuse to pick and return every plausible candidate instead, so a person chooses.

Honest limit: this tries every product for every photo, so the time it takes grows with
the number of products (roughly 50-150 ms per product on a modern CPU, once reference
features are cached - see _reference_features_cache below). Fine for tens of products; a
company with hundreds would need a fast first pass to shortlist candidates (a perceptual
hash or image embedding) before this exact matching step - that is milestone 6 territory,
noted in the roadmap as "automatic product recognition... when there are hundreds of
products". Two products whose faces are genuinely very similar (not just sharing a detail,
but looking alike overall) can also both pass and tie: see MIN_MARGIN below.
"""

from dataclasses import dataclass, field

import cv2

from .align import extract_features, match_features

# The best candidate must have at least this many TIMES the inliers of the next-best
# candidate to be picked automatically. Below that, both are shown and a person decides.
# Chosen so that two clearly different faces (which typically differ by 3x or more on the
# demo pack) separate cleanly, while two similar-looking products do not get auto-picked.
MIN_MARGIN = 1.5

# Matching only needs the RELATIVE positions of points, not full resolution, and a huge
# reference image (a high-DPI artwork render) makes SIFT slower for no benefit. Same value
# pipeline.py uses to size the reference for comparison, so a product behaves identically
# whether it was found by identify_product or chosen by hand.
REFERENCE_SIDE = 1400

_reference_features_cache = {}   # resolved path -> (mtime, Features), see _cached_reference_features


@dataclass
class Candidate:
    product_id: str
    product_name: str
    inliers: int
    spread_x: float
    spread_y: float


@dataclass
class IdentifyResult:
    candidates: list = field(default_factory=list)   # every product that matched at all, best first
    product_id: str = None                             # set only when one candidate clearly won
    message: str = ""                                   # why, in words - shown when product_id is None


def identify_product(photo_bgr, products):
    """Try `photo_bgr` against every product in `products` (from product.list_products).

    Returns an IdentifyResult. `product_id` is set when a candidate outscored every other
    by at least MIN_MARGIN; otherwise it is None and `candidates` lists everything that
    matched at all (possibly empty), for a person to choose from.
    """
    photo_features = extract_features(photo_bgr)
    if photo_features.descriptors is None:
        return IdentifyResult(message="No usable detail found in the photo. Retake it in better light, closer, and in focus.")

    candidates = []
    for product in products:
        reference_features = _cached_reference_features(product)
        match = match_features(photo_features, reference_features)
        if match.ok:
            candidates.append(Candidate(product.product_id, product.name, match.inliers, match.spread_x, match.spread_y))
    candidates.sort(key=lambda candidate: candidate.inliers, reverse=True)

    if not candidates:
        return IdentifyResult(message="This photo did not match any known product. Retake it with the whole face visible, or pick the product by hand.")
    if len(candidates) == 1 or candidates[0].inliers >= MIN_MARGIN * candidates[1].inliers:
        return IdentifyResult(candidates=candidates, product_id=candidates[0].product_id)
    return IdentifyResult(candidates=candidates, message=(
        f"{len(candidates)} products look like a possible match and none stands out clearly. Please pick the right one."
    ))


def _cached_reference_features(product):
    """SIFT on a reference image takes ~100-300 ms; a reference does not change between
    inspections, so we compute it once per file and reuse it. Keyed by the resolved path
    and the file's modification time, so editing a reference.png (a new artwork version)
    is picked up automatically instead of silently matching against a stale image."""
    path = product.reference_path.resolve()
    mtime = path.stat().st_mtime
    cached = _reference_features_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    reference_bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    scale = REFERENCE_SIDE / max(reference_bgr.shape[:2])
    if scale < 1.0:
        reference_bgr = cv2.resize(reference_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    features = extract_features(reference_bgr)
    _reference_features_cache[path] = (mtime, features)
    return features
