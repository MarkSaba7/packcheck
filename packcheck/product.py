"""A "product" = ONE FACE of one package: its reference image plus its zones.

Each product lives in its own folder:

    products/<product_id>/
        product.json      <- describes the product (this module reads it)
        reference.png     <- the artwork for one flat face (front, back...), cropped to that face

Zones mark the parts of the face that must NOT be compared pixel by pixel:

    "variable"  printed later and different on every batch (best-before date, lot code).
                We cut these out of the photo so they can be read - by a person today,
                by OCR later (milestone 2).
    "ignore"    parts we never want to hear about (a clear window, a price sticker area).

Zone rectangles are FRACTIONS of the reference image: [x, y, width, height], each 0..1.
Fractions stay correct when the reference is re-exported at another resolution.

A variable zone can also say what is EXPECTED in it, so the reading can be checked
(milestone 2). Each expectation is a small rule:

    {"name": "best_before", "kind": "date", "formats": ["%d.%m.%Y", "%d/%m/%Y"], "min_days_ahead": 180}
    {"name": "lot", "kind": "code", "pattern": "L[0-9]{5}[A-Z]"}

`formats` use Python's strptime codes (%d day, %m month, %Y four-digit year, %y two-digit
year, %b month name). `pattern` is a regular expression that must match the whole code;
spaces are removed before matching because OCR is unreliable about spaces.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

ZONE_KINDS = ("variable", "ignore")
EXPECTATION_KINDS = ("date", "code")


@dataclass
class Zone:
    name: str
    kind: str       # one of ZONE_KINDS
    rect: tuple     # (x, y, w, h) as fractions of the reference image
    expect: list = field(default_factory=list)   # rules for what is printed here (variable zones only)

    def to_pixels(self, image_width, image_height):
        """Convert the fractional rectangle to pixels for an image of the given size."""
        x, y, w, h = self.rect
        return (
            round(x * image_width),
            round(y * image_height),
            round(w * image_width),
            round(h * image_height),
        )


@dataclass
class Product:
    product_id: str
    name: str
    reference_path: Path
    zones: list
    json_path: Path = None          # where product.json lives (the zone editor writes back to it)
    pack: str = None                # groups the faces of one package: "demo-dog-food" (milestone 4)
    face: str = None                # "front", "back", "side"... (milestone 4)
    barcode: str = None             # what the pack's barcode says; picks the product, and checked on the R79 form
    label_code: str = None          # the label's own code ("#Code étiquette" on the R79 form), if the company has one
    golden_path: Path = None        # photo of a known-good pack: the colour reference (milestone 5)
    markup_path: Path = None        # mask of proof markup (trim lines, labels) never compared; see proof.py
    compare_settings: dict = field(default_factory=dict)   # per-product thresholds for compare.find_differences


# The keys product.json may put under "compare_settings" (they are the parameters of find_differences).
COMPARE_SETTING_KEYS = ("tolerance_px", "strong_threshold", "strong_min_pixels", "weak_threshold", "weak_min_pixels", "merge_px")


def load_product(json_path):
    """Read a product.json file and check it for mistakes early, with clear messages."""
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))

    zones = []
    for entry in data.get("zones", []):
        if entry["kind"] not in ZONE_KINDS:
            raise ValueError(f"{json_path}: zone '{entry['name']}' has kind '{entry['kind']}', expected one of {ZONE_KINDS}")
        rect = tuple(entry["rect"])
        if len(rect) != 4 or not all(0.0 <= value <= 1.0 for value in rect):
            raise ValueError(f"{json_path}: zone '{entry['name']}' rect must be 4 fractions between 0 and 1, got {rect}")
        expect = [_check_expectation(json_path, entry["name"], rule) for rule in entry.get("expect", [])]
        zones.append(Zone(name=entry["name"], kind=entry["kind"], rect=rect, expect=expect))

    # The reference image path is written relative to the product folder.
    reference_path = json_path.parent / data["reference_image"]
    if not reference_path.exists():
        raise FileNotFoundError(f"{json_path}: reference image not found: {reference_path}")

    golden_path = None
    if data.get("golden_photo"):
        golden_path = json_path.parent / data["golden_photo"]
        if not golden_path.exists():
            raise FileNotFoundError(f"{json_path}: golden photo not found: {golden_path}")

    markup_path = None
    if data.get("markup_mask"):
        markup_path = json_path.parent / data["markup_mask"]
        if not markup_path.exists():
            raise FileNotFoundError(f"{json_path}: markup mask not found: {markup_path}")

    compare_settings = data.get("compare_settings", {})
    unknown = set(compare_settings) - set(COMPARE_SETTING_KEYS)
    if unknown:
        raise ValueError(f"{json_path}: unknown compare_settings {sorted(unknown)}; allowed: {COMPARE_SETTING_KEYS}")

    return Product(
        product_id=data["product_id"],
        name=data.get("name", data["product_id"]),
        reference_path=reference_path,
        zones=zones,
        json_path=json_path,
        pack=data.get("pack"),
        face=data.get("face"),
        barcode=str(data["barcode"]) if data.get("barcode") else None,
        label_code=str(data["label_code"]) if data.get("label_code") else None,
        golden_path=golden_path,
        markup_path=markup_path,
        compare_settings=compare_settings,
    )


def save_zones(json_path, zones):
    """Write a new zone list into an existing product.json, keeping every other field.

    `zones` is the list as it appears in the file (dicts with name, kind, rect, expect).
    The list is validated by loading the result; on error the file is left as it was."""
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    original = data.get("zones", [])
    data["zones"] = zones
    json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        return load_product(json_path)
    except (ValueError, KeyError) as error:
        data["zones"] = original
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        raise ValueError(str(error))


def _check_expectation(json_path, zone_name, rule):
    """Validate one `expect` rule of a variable zone; return it unchanged."""
    where = f"{json_path}: zone '{zone_name}'"
    if rule.get("kind") not in EXPECTATION_KINDS:
        raise ValueError(f"{where}: expectation kind must be one of {EXPECTATION_KINDS}, got {rule.get('kind')!r}")
    if not rule.get("name"):
        raise ValueError(f"{where}: every expectation needs a 'name'")
    if rule["kind"] == "date":
        formats = rule.get("formats")
        if not formats or not all(isinstance(item, str) for item in formats):
            raise ValueError(f"{where}: a date expectation needs 'formats', a list of strptime formats such as [\"%d.%m.%Y\"]")
        for key in ("min_days_ahead", "max_days_ahead"):
            if key in rule and not isinstance(rule[key], int):
                raise ValueError(f"{where}: '{key}' must be a whole number of days")
    if rule["kind"] == "code":
        try:
            re.compile(rule.get("pattern", ""))
        except re.error as error:
            raise ValueError(f"{where}: 'pattern' is not a valid regular expression: {error}")
        if not rule.get("pattern"):
            raise ValueError(f"{where}: a code expectation needs a 'pattern'")
    return rule


def list_products(products_dir):
    """Load every products/<something>/product.json found in the folder."""
    products = []
    for json_path in sorted(Path(products_dir).glob("*/product.json")):
        products.append(load_product(json_path))
    return products
