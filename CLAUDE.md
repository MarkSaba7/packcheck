# PackCheck

A worker photographs a package with a phone; PackCheck compares the photo with the approved
artwork for that product, lists the differences, reads the dates and lot codes, and saves
everything so it can be printed in a work-order report. It is being built for a company
that receives animal-product packaging, where packs sometimes arrive with small print
differences (colour, text, missing elements) and with batch data to verify (best-before
dates, lot codes, shipping dates).

"PackCheck" is a working name. Rename freely.

## How to work with the developer

The developer is a student building this partly to learn, and must be able to explain
every line to someone else.

- Work in small steps: one module or one function at a time, then stop and explain it.
- Say WHY before HOW. Each new step in the pipeline should come with the problem it solves.
- Prefer the simple, explicit approach over the clever one. If two designs work, take the
  one that is easier to explain, even if it is longer or slower.
- No new dependency, framework or pattern without saying what it is for and what the
  plainer alternative would be.
- Comments explain why, in plain words. Descriptive names, no abbreviations.
- Every behaviour change comes with a test. Run `python -m pytest` before calling it done.

## Hard rules

1. **Company artwork and photos are confidential.** They live in `private/` and in
   `products/<anything but the demo>/`, which git ignores, and in `runs/` and
   `packcheck.sqlite`, also ignored. Never commit them, never paste them into issues, never
   send them to any external API or cloud service unless the developer confirms the company
   has agreed. Develop and test against the synthetic demo.
2. **Never guess a reading.** If a date or code cannot be read with confidence, the result
   is "could not read - check by hand", never a best guess (`reading.MIN_CONFIDENCE`; a
   rule that finds no matching line gives `valid: None`, never `False`). Same for alignment:
   refuse rather than compare badly (`align.MIN_INLIERS`, `align.MIN_INLIER_SPREAD`).
3. **Every flag must be explainable.** A worker has to be able to see why something was
   flagged (box, close-up, number, which reference it was compared with). No black-box verdicts.
4. **Be honest about limits** in code comments, docs and UI text. See "Known limits".

## Commands

```
pip install -r requirements.txt
python -m pytest                                   # 63 tests, about a minute and a half
python -m packcheck.cli demo                       # (re)create the synthetic demo product (front + back)
python -m packcheck.cli inspect --product products/demo-dog-food-front/product.json \
                                --photo products/demo-dog-food-front/photo_bad.jpg
python -m packcheck.cli add-user "Name" --pin 1234 # from then on the pages require a login
uvicorn packcheck.server:app --host 0.0.0.0 --port 8000    # open http://localhost:8000: Start scanning (/desk) / Add an artwork (/artworks/new)
python scripts/render_artwork.py private/artwork.pdf --out private/sheet.png
python scripts/evaluate.py --product products/<id>/product.json --photos private/photos/<id>/   # milestone 1
python scripts/export_decisions.py --out private/decisions.csv                                  # milestone 6 data
python scripts/prune_runs.py --keep-days 90                                                     # retention
```

On this developer's machine `python` on PATH is an msys build without the packages; use
the Windows Store Python or a `.venv` made from it (see README quick start).

## Architecture

```
home (web/home.html): 1 Start scanning -> desk (web/desk.html: QR, polls the session) <-- phone (web/phone.html: front + back)
                      2 Add an artwork -> web/artwork.html -> proof.py (cut faces, markup, duplicate check) -> products/
phone ---front + back---> POST /api/packs/inspect (server.py) -> identify_pack (both photos together) -> 2x inspect_photo
desk / history (web/history.html) ---> /report/pack/<id>: r79.py (fill_form decides, render_form draws) reads storage
zone editor (web/zones.html) --zones-->     |
                          (no product_id?)  |
                           identify.py -----+---> pipeline.inspect_photo()
                       "which product IS         |
                        this photo of?"      product.py       align.py            compare.py                      reading.py
                       (try every reference,  reference image  straighten photo    glare -> lighting -> delta E    zone crop from ORIGINAL photo
                        same match_features   + zones + rules  onto reference      strong / weak (CIEDE2000)       -> OCR (RapidOCR) -> rules
                        as align.py; refuses  + golden photo   (SIFT + homography  -> "content" vs artwork,        -> text / valid / reason
                        to guess when close)                    + spread check)       "colour" vs golden photo
                                                                      |
                                            runs/<time>_<product>/result.json + images  --->  storage.py (SQLite index,
                                                                                              decisions, readings, users, sessions)
```

- A **product** is ONE flat face of one package: `products/<id>/product.json` + `reference.png`.
  `pack` + `face` group faces; `barcode` only picks the product; `golden_photo` is the colour
  reference; `compare_settings` are per-product thresholds.
- **A scan is one physical pack = front + back** (same `pack_id` on both inspections, front
  saved first). Its result is the company's **R79 form** (`r79.py`), one per scan.
- **Products usually come from a printer's proof** (`proof.py`, page `/artworks/new`): one sheet
  with every face, bleed, trim/seal lines, copy-safety box, labels, signatures, often a
  scan. A person draws each face along its trim line; `markup_mask` (markup.png) marks the
  proof markup found in the face's margin, which the pipeline never compares.
- **Zones** (fractions of the reference, `[x, y, w, h]`): `variable` = printed per batch
  (dates, lot codes): never compared, cut out and read; `expect` rules say what must be there.
  `ignore` = never reported.
- The pages only talk to the JSON API, so other clients can be added without touching the pipeline.
- `result.json` is the contract between the pipeline and everything downstream (page,
  database, report). Add fields; do not rename existing ones casually. Two verdicts:
  `status` (fixed print) and `readings_status` (variable print).
- All page and report text is in `packcheck/web/strings.json` (en, fr).
- Every page starts with `<div id="app-bar" data-active="...">` and calls `mountAppBar()`
  (common.js): one shared top bar, sign-in box and icon set (`data-icon="name"`). The look
  lives in `web/packcheck.css` (light theme, design tokens at the top); no inline styles.

## Decisions already made (and why)

- **Classical computer vision first, no trained model.** There are no labelled defect
  examples to train on, and every flag must be explainable. ML comes later, where it pays
  (a false-alarm filter trained on workers' confirm/dismiss decisions, which
  `scripts/export_decisions.py` already collects).
- **Fixed print vs variable print are two different problems.** The artwork cannot say
  what the best-before date should be. Fixed areas are compared with the reference;
  variable zones are read and checked against rules in `product.json`.
- **Artwork is the reference for layout and text; a photo of a known-good pack is the
  reference for colour** (`golden_photo`). Artwork is CMYK/Pantone for a press and its
  on-screen colours never exactly match a correct print.
- **RapidOCR** because it installs with pip alone and returns a confidence per line (the
  never-guess rule needs it). Tesseract is the plainer swap if it is installed.
- **The product is identified FROM the photo, by trying it against every product's
  reference** (`identify.py`), reusing the exact point-matching `align.py` uses to
  straighten a photo - no separate "recognition" model. The never-guess rule applies here
  too: if the best match is not clearly ahead of the next one, the worker is asked instead
  of the system picking wrong silently. This is the classical-CV version of what the
  roadmap called "automatic product recognition"; it works well for tens of products and
  gets slower as more are added (see `identify.py`'s docstring) - an embeddings-based
  shortlist is the scale-up path, not a redesign.
- **Web page, not a native app.** No app store, works on any phone, heavy work stays on
  the server. The page uses `<input type="file" capture>` because it works over plain HTTP
  on a local network; live camera APIs (`getUserMedia`) require HTTPS.
- **SQLite, one connection per request, polling instead of WebSockets, HTML built with
  `html.escape` instead of a template engine, a random login ticket in a cookie instead of
  a signing library.** Each is the plainest thing that works at this size.
- **Python** because OpenCV and the OCR ecosystem live there.
- **The R79's 12 points are not judged one by one** - nothing here can read "legible". A
  point is ticked when both faces match the approved artwork AND every flagged difference
  was dismissed by a person; crossed when a person confirmed a difference and chose that
  point (`decisions.checklist_item`); blank + "pending" otherwise. UPC also compares the
  barcode read in the photos (not reading it is never a cross). Same never-guess rule.
- **The pack is identified from BOTH photos** (`identify.identify_pack`, inliers summed):
  the 2 mm and 4 mm Hagen pouches share a back almost word for word; their fronts decide.
- **"Already in the database"** = same product code, same barcode, or >= 35% of the
  smaller reference's points agree (`proof.SAME_ARTWORK_SHARE`, measured: same proof twice
  42-96%, 2 mm vs 4 mm 6-23%). Only the visual check can be overruled by a person.
- **Proof faces are drawn by a person, not detected.** On the real Hagen / Rootree proofs the
  magenta trim outline is complete on the white front but almost invisible on the blue
  back: detection would work on half the faces (never-guess rule). Proof markup IS found
  automatically, by colour + thin-or-small + "outside the copy-safety box": colour alone
  would also mask green print (the demo's own green text is the proof labels' green).

## Known limits (do not hide these)

- All thresholds in `compare.find_differences` were tuned on SIMULATED photos whose camera
  behaves exactly like the lighting model. Real photos will be messier. Milestone 1
  (`scripts/evaluate.py`, `docs/m1-findings.md`) exists to measure by how much; nobody has
  run it on real photos yet.
- A colour shift covering most of the face is invisible: it is indistinguishable from
  different lighting. Small local shifts are only caught if they exceed what the camera and
  lighting themselves introduce. A golden photo helps only with a fixed photo spot.
- Alignment assumes a FLAT face (boxes, flat labels). Wrinkled bags and curved cans will
  align only approximately and produce false alarms along the distortions.
- Two products with nearly identical faces will pass alignment for each other. The spread
  check only catches "one shared detail", not "almost the same design". Auto-identify
  (`identify.py`) at least refuses to silently pick between them (see `MIN_MARGIN`) and
  asks the worker, but cannot tell them apart on its own.
- Auto-identify tries the photo against EVERY product's reference, so it gets slower as
  products are added (roughly 50-150 ms each, reference features are cached). Fine for
  tens of products; hundreds would need a fast shortlist step first (milestone 6).
- Glare detection only catches blown-out white blobs; a soft reflection is still reported.
- OCR is tested on the demo's clean print. Dot-matrix and inkjet codes will need
  preprocessing; until then they come back as "check by hand". Pipeline messages are English only.
- The barcode detector is tested with QR codes only (OpenCV cannot generate EAN barcodes for the demo).
- No trained model, no HTTPS out of the box (see `docs/DEPLOYMENT.md`).
- Proofs: handwritten approval ticks are grey like print and are NOT masked; they need
  ignore zones (or the printer's unsigned PDF). Markup in other colours or inside the
  copy-safety box is not found. A scanned proof's colours are not the print's colours.
- Small print: references are compared at 1400 px. A single changed character of small text
  can fall under `strong_min_pixels` when the artwork is small in that frame (tests/test_proof.py
  uses a missing icon for this reason). "2 mm" vs "4 mm" on the Hagen fronts IS caught.

## Where things are

| Path | What |
|---|---|
| `packcheck/identify.py` | Step 0 (optional): which product IS this photo of, tried against every reference |
| `packcheck/align.py` | Step 1: straighten the photo onto the reference; cut zones from the original photo |
| `packcheck/compare.py` | Step 2: glare, lighting correction, delta E map, CIEDE2000, regions, content/colour |
| `packcheck/reading.py` | Step 3: OCR of variable zones and the date / code rules |
| `packcheck/pipeline.py` | One inspection end to end; golden photo; writes `runs/...`; drawing helpers |
| `packcheck/product.py` | `product.json` loading and validation, zones, rules, `save_zones` |
| `packcheck/proof.py` | Proof sheet -> products: render at 300 dpi, cut each face, find proof markup, duplicates |
| `packcheck/r79.py` | The R79 form for one scanned pack: `fill_form` (what each box says), `render_form` (the page) |
| `packcheck/storage.py` | SQLite: inspections, differences, decisions, readings, sessions, users, logins |
| `packcheck/report.py` | The printable HTML report |
| `packcheck/codes.py` | QR code generation, barcode / QR decoding (OpenCV) |
| `packcheck/demo.py` | Synthetic artwork (front + back) and simulated photos with planted defects |
| `packcheck/server.py`, `packcheck/web/` | API and the pages (home, desk, phone, history, artwork, zones editor), strings.json |
| `scripts/` | render_artwork.py, evaluate.py, export_decisions.py, prune_runs.py |
| `tests/` | end-to-end tests on the demo, one file per milestone |
| `docs/ROADMAP.md` | Milestones, each with a definition of done and its current status |
| `docs/m1-findings.md` | Template to fill in with the real-photo measurements |
| `docs/DEPLOYMENT.md` | HTTPS, hosting, backups, access, audit, retention checklist |
