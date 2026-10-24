# PackCheck

Photograph a package with a phone; PackCheck compares the photo with the approved artwork
and shows what differs: changed text, missing elements, colour shifts. Dates and lot codes,
which legitimately change on every batch, are read instead of compared and checked against
the product's rules. Every inspection is stored; a printable report collects a day or a
work order.

Milestones 0 to 7 of [docs/ROADMAP.md](docs/ROADMAP.md) are built and tested on a synthetic
demo product. What is NOT done is everything that needs the company's real artwork and
photos: measuring the thresholds on real packs (milestone 1), and training anything
(milestone 6). See "What it cannot do yet".

## Quick start

Python 3.10 or newer.

```
python -m venv .venv
.venv\Scripts\activate            # Windows      (macOS / Linux: source .venv/bin/activate)
pip install -r requirements.txt
python -m pytest                  # 37 tests, about a minute (the first run downloads nothing: OCR models ship with the package)
```

Run an inspection from the command line:

```
python -m packcheck.cli inspect --product products/demo-dog-food-front/product.json --photo products/demo-dog-food-front/photo_bad.jpg
```

It prints 3 differences, the date and lot it read, and a folder in `runs/`. Open
`comparison.jpg` in that folder. Try `photo_good.jpg` too: it must report none.

Run it from your phone:

```
uvicorn packcheck.server:app --host 0.0.0.0 --port 8000
```

Find your computer's IP address (`ipconfig` on Windows, `ifconfig` on macOS / Linux), put
the phone on the same Wi-Fi and open `http://<that-ip>:8000`. No phone handy? Open
`http://localhost:8000` on the computer and choose `photo_bad.jpg` as the "photo".
For a first real-camera test, display `products/demo-dog-food-front/reference.png` full
screen on a monitor and photograph it with the phone.

## The pages

| URL | Who | What |
|---|---|---|
| `/` | worker with a phone | photograph the pack - the product is identified FROM the photo, no picker needed; if it is genuinely unclear which product it is, pick from the short list shown, search by name, or photograph the barcode; see the verdict, Confirm / Dismiss each difference |
| `/desk` | a screen next to the packs | shows a QR code; the phone that scans it becomes that screen's camera and results appear on the screen |
| `/report?day=2026-09-17` or `/report?work_order=WO-7` | supervisor | printable report: every pack, confirmed differences with close-ups, dates and lots read; "print to PDF" is the export |
| `/products/<id>/zones` | whoever sets up products | draw the variable / ignore zones on the artwork instead of typing fractions |

Add `&lang=fr` to the report, or press FR on a page: all text lives in
`packcheck/web/strings.json`.

Login is optional: run `python -m packcheck.cli add-user "Name" --pin 1234` and from then on
inspecting, deciding and editing zones require a name + PIN.

## How it works

0. **Identify the product** (`identify.py`, skipped if the worker picked one). Try the
   photo against every product's reference, the same point-matching alignment uses, and
   rank them by how many points agree. If the best match is not clearly ahead of the
   runner-up, nothing is auto-picked: the worker sees the plausible candidates and taps
   one, or falls back to search / barcode - the SAME photo is reused, no retake needed.
1. **Straighten** (`align.py`). Find distinctive points in the photo and in the reference,
   pair them up, compute the perspective transform that maps one onto the other, and warp
   the photo so it lies exactly on top of the reference. Too few matching points, or points
   that all sit on one shared detail (the front and back of a pack share an ingredients
   line), means wrong product or unusable photo: the inspection is refused.
2. **Leave out glare** (`compare.find_glare`). Blobs that are pure white in the photo where
   the artwork is not white are reflections. They are excluded and reported, and a retake
   is asked for when they cover more than 2% of the face.
3. **Correct the lighting** (`compare.py`). For each colour channel, estimate how much
   brighter the photo is than the reference around each spot, and divide it out. This
   removes exposure, white balance, shadows and gradients in one step.
4. **Measure** (`compare.py`). Convert both to LAB colours and compute delta E, the colour
   distance as a human would see it, for every pixel, forgiving a couple of pixels of
   misalignment.
5. **Group** (`compare.py`). A strong pass finds big differences on few pixels; a weak pass
   finds small differences spread over an area, measured with CIEDE2000 on
   neighbourhood-averaged colours. Each region is then named: same shapes in both images
   means "colour", otherwise "content". When the product has a **golden photo** (a photo of
   a known-good pack), colour is judged against it and content against the artwork.
6. **Read** (`reading.py`). Each variable zone is cut from the ORIGINAL photo at full
   detail (`align.crop_zone_from_original`), read with OCR (RapidOCR), and checked against
   the zone's rules in `product.json`: a date format and "at least N days ahead", a lot-code
   pattern. Anything the OCR is unsure about comes back as "could not read - check by
   hand", never as a guess.
7. **Save** (`pipeline.py`, `storage.py`). Everything lands in `runs/<time>_<product>/`:
   the side-by-side image, one close-up per difference, one crop per variable zone, and
   `result.json`. The database `packcheck.sqlite` indexes the runs, and stores the
   workers' decisions and the readings for the report.

Each module starts with a longer explanation of what it does and why.

## Adding a real product

Company artwork and photos are confidential: keep them in `private/` and `products/`, which
git ignores (only the demo product is tracked). Do not upload them anywhere.

1. Render the artwork: `python scripts/render_artwork.py private/artwork.pdf --out private/sheet.png`
2. The sheet shows the whole unfolded pack. In any image editor, crop ONE face exactly along
   its fold / cut lines and save it as `products/<product_id>/reference.png`.
3. Copy `products/demo-dog-food-front/product.json` next to it and edit it:
   - `pack` and `face` group several faces of one package ("front", "back").
   - `barcode` is what the pack's barcode says. It only picks the product on the phone.
   - `zones`: draw them at `/products/<id>/zones`, or type them: `[x, y, width, height]` as
     fractions of the reference image. `variable` zones are read, not compared; they can
     carry `expect` rules (see `product.py`). `ignore` zones are never reported.
   - `golden_photo` (later, milestone 5): a photo of a known-good pack at the photo spot.
   - `compare_settings`: the tuned thresholds for this product, once milestone 1 gave them.
4. Inspect a photo of a good pack. Ideally: no differences. Whatever gets flagged tells you
   what to work on: that is milestone 1, with `scripts/evaluate.py` and `docs/m1-findings.md`.

## What it cannot do yet

- The thresholds were tuned on simulated photos. Expect false alarms on real ones until
  milestone 1 is done. `scripts/evaluate.py` measures them; nobody has run it on real
  photos yet.
- A colour shift over most of the face looks the same as different lighting, so it is not
  detected. Reliable colour checking needs a fixed photo spot and a golden photo; the code
  supports the golden photo, the photo spot is furniture.
- Only flat faces align well. Bags and curved cans will produce false alarms.
- OCR is tested on the demo's clean print only. Dot-matrix and inkjet codes will need
  preprocessing; until then they come back as "check by hand".
- The barcode detector is tested with QR codes only (the demo has no real EAN barcode).
- Auto-identify tries the photo against every product's reference, so it slows down as
  products are added (fine for tens; hundreds need a shortlist step first, see roadmap M6).
- No trained model anywhere. `scripts/export_decisions.py` collects the Confirm / Dismiss
  decisions that a future false-alarm filter would learn from (milestone 6).
- Pipeline messages (warnings, alignment errors) are English only; page text is translated.

## Layout

```
packcheck/        the code: identify, align, compare, reading, pipeline, product, storage, report, codes, demo, cli, server, web/
products/         one folder per product; only the demo (front + back) is tracked by git
private/          raw artwork, test photos, exports; ignored by git
runs/             one folder per inspection; ignored by git
packcheck.sqlite  the inspection database; ignored by git
scripts/          render_artwork.py, evaluate.py, export_decisions.py, prune_runs.py
tests/            end-to-end tests on the demo
docs/             ROADMAP.md, m1-findings.md (template), DEPLOYMENT.md
CLAUDE.md         project context for Claude Code
```
