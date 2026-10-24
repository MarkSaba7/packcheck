"""Step 3 of the pipeline: read the variable zones (dates, lot codes) and check them.

The artwork cannot say what today's best-before date should be, so variable zones are not
compared with the reference. Instead:

  1. read the text in the zone crop with OCR                     -> RapidOCR
  2. throw away any line the OCR is not confident about          -> MIN_CONFIDENCE
  3. look for what the product's rules expect (a date, a code)   -> check_expectations()
  4. check each finding against its rule (not too soon, pattern)

The hard rule from CLAUDE.md: NEVER GUESS. A line under the confidence threshold is not
"probably 2027-03-15", it is "could not read - check by hand". A rule that finds no matching
line returns valid=None (a person must look), never valid=False; False is reserved for a
confident reading that breaks its rule (a date that is too soon, already past...).

Why RapidOCR: it installs with pip alone (Tesseract needs a separate program installed on
the computer), it comes with its own text detector (it finds the lines itself, so we do
not have to), and it returns a confidence per line, which the never-guess rule needs. The
plainer alternative, pytesseract, is a fine swap if Tesseract is available: only
`read_lines` would change.

Known limit: dot-matrix and inkjet codes are the hard case for any OCR. Expect to add
preprocessing (contrast, thresholding, a slight blur to join the dots) after milestone 1
shows what real codes look like. Until then, unreadable ones fall back to "check by hand",
which is the safe outcome.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime

import cv2

# A line is used only if the OCR is at least this sure about it. Tuned on the demo; expect
# to revisit it on real photos (a wrong reading with 0.91 confidence is worse than a "check by hand").
MIN_CONFIDENCE = 0.90

# Text touching the edge of a crop is often missed by the detector; a white margin fixes that.
MARGIN_PX = 24

# Small crops are enlarged to at least this height before OCR (letters under ~20 px read badly).
MIN_CROP_HEIGHT = 120

COULD_NOT_READ = "could not read - check by hand"

_engine = None


@dataclass
class Line:
    text: str
    confidence: float   # 0..1, from the OCR


def read_lines(crop_bgr):
    """Run OCR on one zone crop. Returns every line found, confident or not."""
    prepared = _prepare(crop_bgr)
    result, _ = _get_engine()(prepared)
    if not result:
        return []
    lines = []
    for box, text, confidence in result:
        text = text.strip()
        if text:
            lines.append(Line(text=text, confidence=round(float(confidence), 3)))
    return lines


def _prepare(crop_bgr):
    """Enlarge tiny crops and add a white margin: two cheap steps that help most OCR engines."""
    height = crop_bgr.shape[0]
    if height < MIN_CROP_HEIGHT:
        zoom = MIN_CROP_HEIGHT / height
        crop_bgr = cv2.resize(crop_bgr, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(crop_bgr, MARGIN_PX, MARGIN_PX, MARGIN_PX, MARGIN_PX, cv2.BORDER_CONSTANT, value=(255, 255, 255))


def _get_engine():
    """Load the OCR model once (it takes a second) and keep it for the next zones."""
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR   # imported here so `import packcheck` stays fast
        _engine = RapidOCR()
    return _engine


def read_zone(crop_bgr, expectations, today=None):
    """Read one zone and check it. Returns the dict that goes into result.json:

        text      the confident lines joined with " / ", or None if nothing could be read
        lines     every line the OCR saw, with its confidence (so a person can judge)
        readings  one entry per expectation: {name, kind, text, valid, reason}
        valid     True (all rules pass) / False (a rule failed) / None (someone must check)
        reason    why it is not valid, in plain words
    """
    lines = read_lines(crop_bgr)
    confident = [line for line in lines if line.confidence >= MIN_CONFIDENCE]
    readings = check_expectations(confident, expectations, today or date.today())

    entry = {
        "text": " / ".join(line.text for line in confident) if confident else None,
        "lines": [{"text": line.text, "confidence": line.confidence} for line in lines],
        "readings": readings,
    }
    if not confident:
        entry["valid"] = None
        entry["reason"] = COULD_NOT_READ
    elif not readings:
        entry["valid"] = None
        entry["reason"] = "no rule configured for this zone - check by hand"
    elif any(reading["valid"] is False for reading in readings):
        entry["valid"] = False
        entry["reason"] = "; ".join(reading["reason"] for reading in readings if reading["valid"] is False)
    elif any(reading["valid"] is None for reading in readings):
        entry["valid"] = None
        entry["reason"] = "; ".join(reading["reason"] for reading in readings if reading["valid"] is None)
    else:
        entry["valid"] = True
        entry["reason"] = ""
    return entry


def check_expectations(confident_lines, expectations, today):
    """Match each rule of the zone against the confident lines."""
    readings = []
    for rule in expectations:
        if rule["kind"] == "date":
            readings.append(_check_date(confident_lines, rule, today))
        else:
            readings.append(_check_code(confident_lines, rule))
    return readings


def _check_date(lines, rule, today):
    reading = {"name": rule["name"], "kind": "date", "text": None, "valid": None, "reason": ""}
    found = None
    for line in lines:
        found = parse_date(line.text, rule["formats"])
        if found:
            reading["text"] = found.isoformat()
            break
    if not found:
        reading["reason"] = f"{rule['name']}: no line reads as a date ({', '.join(rule['formats'])}) - check by hand"
        return reading

    # Days between today and the date read. Negative = in the past. The rules say what is
    # allowed: a best-before date wants min_days_ahead (say 180), a packed-on date wants
    # max_days_ahead 0 (today or earlier).
    days_ahead = (found - today).days
    when = _describe_days(days_ahead)
    if "min_days_ahead" in rule and days_ahead < rule["min_days_ahead"]:
        reading["valid"] = False
        reading["reason"] = f"{rule['name']} {found.isoformat()} is {when} (rule: at least {rule['min_days_ahead']} days ahead)"
    elif "max_days_ahead" in rule and days_ahead > rule["max_days_ahead"]:
        reading["valid"] = False
        reading["reason"] = f"{rule['name']} {found.isoformat()} is {when} (rule: at most {rule['max_days_ahead']} days ahead)"
    else:
        reading["valid"] = True
    return reading


def _describe_days(days_ahead):
    if days_ahead > 0:
        return f"in {days_ahead} days"
    if days_ahead < 0:
        return f"{-days_ahead} days ago"
    return "today"


def parse_date(text, formats):
    """Try the whole line, then each word, against each format. Returns a date or None.

    strptime is strict: "2027-03-15" matches "%Y-%m-%d" and nothing else, "2O27-03-15"
    (letter O) matches nothing. That strictness is on purpose: it is what stops us guessing.
    """
    candidates = [text] + text.split()
    for candidate in candidates:
        for date_format in formats:
            try:
                return datetime.strptime(candidate, date_format).date()
            except ValueError:
                continue
    return None


def _check_code(lines, rule):
    reading = {"name": rule["name"], "kind": "code", "text": None, "valid": None, "reason": ""}
    pattern = re.compile(rule["pattern"])
    for line in lines:
        without_spaces = line.text.replace(" ", "")
        if pattern.fullmatch(without_spaces):
            reading["text"] = without_spaces
            reading["valid"] = True
            return reading
    reading["reason"] = f"{rule['name']}: no line matches the expected pattern - check by hand"
    return reading
