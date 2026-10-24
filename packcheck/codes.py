"""QR codes and barcodes, both with OpenCV alone (no extra library).

Two uses, neither of which VERIFIES anything:

  make_qr_png(text)      the desk screen shows a QR code with its session link, so the phone
                         becomes that screen's camera without typing a URL (milestone 4).
  decode_codes(image)    the worker photographs the pack's barcode to PICK the product, which
                         saves scrolling through a long list. A barcode says which product
                         the pack claims to be; the artwork comparison still does the checking.

Honest limit: the barcode detector is tested here only with QR codes (the demo has no real
EAN barcode; OpenCV can encode QR but not EAN). Real EAN-13 codes on packs go through the
same call; how well it reads them is a milestone 1 question.
"""

import cv2
import numpy as np


def make_qr_png(text, module_px=8, margin_modules=4):
    """Return the bytes of a PNG showing `text` as a QR code."""
    modules = cv2.QRCodeEncoder.create().encode(text)          # one pixel per QR "module"
    image = cv2.resize(modules, None, fx=module_px, fy=module_px, interpolation=cv2.INTER_NEAREST)
    margin = margin_modules * module_px                         # QR readers need a quiet zone around the code
    image = cv2.copyMakeBorder(image, margin, margin, margin, margin, cv2.BORDER_CONSTANT, value=255)
    ok, png = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("could not encode the QR code as PNG")
    return png.tobytes()


def decode_codes(image_bgr):
    """Every barcode or QR code readable in the image, as a list of strings (may be empty)."""
    found = []

    ok, decoded, _types, _points = cv2.barcode.BarcodeDetector().detectAndDecodeWithType(image_bgr)
    if ok:
        found.extend(value for value in decoded if value)

    ok, decoded, _points, _straight = cv2.QRCodeDetector().detectAndDecodeMulti(image_bgr)
    if ok:
        found.extend(value for value in decoded if value)

    # Phone photos are big; the detectors work better on a moderate size. Try once shrunk too.
    longest = max(image_bgr.shape[:2])
    if not found and longest > 1600:
        scale = 1600 / longest
        smaller = cv2.resize(image_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        return decode_codes(smaller)

    return list(dict.fromkeys(found))   # unique, in the order found


def decode_png_bytes(png_bytes):
    """Small helper for tests: PNG bytes -> BGR image."""
    return cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
