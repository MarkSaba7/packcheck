"""Is this phone photo good enough to send? Checked the moment it is taken, on the phone page.

Why: a blurry or dark photo only failed later, on the computer, after the worker had
already moved on to the next pack. Checking each photo right away lets the worker retake it
while the pack is still in their hands.

Three checks, each a single number compared with a limit:
    blurry      - sharp edges are missing (variance of the Laplacian is low)
    too_dark    - the average brightness is low
    too_bright  - a large share of the photo is blown out to (nearly) pure white

The result only WARNS. The worker can still send the photo: the limits below were set on
the demo's simulated photos, not on real phone photos yet, so a person decides.

Not checked here: "is the whole face in the frame?". That cannot be judged without knowing
which artwork the photo is of, and alignment (align.py) already refuses later when too
little of the face matches.
"""

import cv2

# Every photo is shrunk to this many pixels on its long side before measuring, so the
# numbers mean the same thing whatever the phone's camera resolution is (a 12 MP photo has
# much finer detail per pixel than a 3 MP one, which would change the sharpness number).
MEASURE_LONG_SIDE = 1000

# Sharpness = variance of the Laplacian (how strongly brightness changes from one pixel to
# the next). Sharp print has many strong edges: a high number. Below this: "blurry".
# Set on the demo's simulated photos (measured 460-670), NOT on real phone photos yet.
# A Gaussian blur of sigma 2 already drops the demo to about 70, sigma 5 to about 5.
MIN_SHARPNESS = 50.0

# Average grey level, 0 (black) to 255 (white). Below this: "too dark".
# Set on the demo's simulated photos (measured 144-163), NOT on real phone photos yet.
# Darkening the demo photo to a third of its brightness gives about 50.
MIN_BRIGHTNESS = 50.0

# A pixel at or above this grey level counts as "nearly white": blown out, no detail left.
NEARLY_WHITE = 250
# Share of the photo (0 to 1) that may be nearly white. Above this: "too bright / glare".
# Set on the demo's simulated photos (measured 1.2%-2.7%), NOT on real phone photos yet.
# Kept lenient on purpose: without knowing the artwork, white paper photographed in bright
# light looks the same as glare. Small glare spots are still found later by
# compare.find_glare, which knows where the artwork itself is white.
MAX_WHITE_SHARE = 0.20


def check_photo(image_bgr):
    """The problems found in one photo, as a list of {"code", "value", "limit"} dicts.
    An empty list means the photo looks fine.

    The order matters for the phone page, which shows the first problem: a dark or blown-out
    photo also LOOKS blurry to the sharpness number (fewer strong edges), so the lighting
    problems come first - fixing the light is what the worker should do."""
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    scale = MEASURE_LONG_SIDE / max(gray.shape)
    # INTER_AREA averages pixels when shrinking, so it does not add or remove sharpness.
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    brightness = float(gray.mean())
    white_share = float((gray >= NEARLY_WHITE).mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    problems = []
    if brightness < MIN_BRIGHTNESS:
        problems.append({"code": "too_dark", "value": brightness, "limit": MIN_BRIGHTNESS})
    if white_share > MAX_WHITE_SHARE:
        problems.append({"code": "too_bright", "value": white_share, "limit": MAX_WHITE_SHARE})
    if sharpness < MIN_SHARPNESS:
        problems.append({"code": "blurry", "value": sharpness, "limit": MIN_SHARPNESS})
    return problems
