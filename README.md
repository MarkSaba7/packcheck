# PackCheck

Photograph the front and back of a delivered pack with a phone; PackCheck finds which
approved artwork it is, compares both faces with it and shows what differs on the computer
screen: changed text, missing elements, colour shifts. Each scan fills in the company's
**R79 form** ("Validation des matériaux imprimés à la réception"), ready to view or print.
Dates and lot codes, which legitimately change on every batch, are read instead of
compared and checked against the product's rules.

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
python -m pytest                  # 74 tests, about three minutes (the first run downloads nothing: OCR models ship with the package)
```

Run an inspection from the command line:

```
python -m packcheck.cli inspect --product products/demo-dog-food-front/product.json --photo products/demo-dog-food-front/photo_bad.jpg
```

It prints 3 differences, the date and lot it read, and a folder in `runs/`. Open
`comparison.jpg` in that folder. Try `photo_good.jpg` too: it must report none.

Run the app:

```
uvicorn packcheck.server:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` on the computer and click **Start scanning**. A QR code
appears: scan it with a phone on the same Wi-Fi (if the phone's page never loads, see the
Windows Firewall note in `docs/DEPLOYMENT.md`). The computer says "phone connected"; on the
phone, photograph the front, then the back, and press Send. The result appears on the
computer, with **Scan another pack**, **See the report** and **Print the report**.
To try it with the demo, photograph `products/demo-dog-food-front/reference.png` and
`products/demo-dog-food-back/reference.png` shown full screen on a monitor.

## How a scan works, from the worker's side

1. **Home** (`/`): two buttons, **Start scanning** and **Add an artwork**.
2. **Start scanning** (`/desk`, on the computer): a QR code; when the phone opens it, the
   screen says "phone connected". The history of scans (today / this week / this month)
   is one click away.
3. **The phone** (`/phone`, opened from the QR code): one slot for the front, one for the
   back; Send works only once both are taken. Each photo is checked the moment it is taken
   (`photo_quality.py`): a blurry, dark or blown-out one says so on its slot, and Send
   becomes "Send anyway" - a warning, never a block. If the photos do not clearly point to
   one artwork, the phone asks which one instead of guessing.
4. **The result** (back on the computer): each face, its differences with close-ups.
   For each difference: **Confirm** (and say which R79 point it breaks), **Dismiss**, or
   **Always ignore this spot**: dismiss it and add an ignore zone there to that artwork, so
   later scans are not asked about it again (for approval ticks, signatures and other
   things that are not on the real pack; the zone can be removed in the zone editor).
5. **The R79** (`/report/pack/<id>`): the company's form, filled in. A point is ticked
   when both faces match the approved artwork and every flagged difference was dismissed
   by a person; it is crossed when a confirmed difference was put on it; it stays blank,
   with "pending" and the reason, while anything is still undecided. "Présence du code
   UPC" also compares the barcode read in the photos with the artwork's, when it has one.
   Page 2 is the evidence: both comparison images and every difference.

## The pages

| URL | Who | What |
|---|---|---|
| `/` | everyone | home: Start scanning / Add an artwork / history |
| `/desk` | the computer next to the packs | QR code, "phone connected", result of each scan, review, next scan / report |
| `/phone?session=...` | the worker's phone | front + back photos, Send |
| `/history?range=day\|week\|month` | supervisor | every scanned pack with its R79 verdict, View / Print |
| `/report/pack/<id>` | supervisor | the R79 form of one scan (`&print=1` opens the print dialog, `&lang=en` in English) |
| `/artworks/new` | whoever adds artworks | upload a proof, draw its front and back, name, product code, barcode; refuses an artwork already in the database |
| `/products/<id>/zones` | whoever sets up products | draw the variable / ignore zones on the artwork instead of typing fractions |
| `/report?day=2026-09-17` or `/report?work_order=WO-7` | supervisor | the older summary report of a whole day |

Add `&lang=fr` to the report, or press FR on a page: all text lives in
`packcheck/web/strings.json`.

Login is optional: run `python -m packcheck.cli add-user "Name" --pin 1234` and from then on
inspecting, deciding and editing zones require a name + PIN (`users` lists them,
`remove-user "Name"` removes one, `add-user` on an existing name changes the PIN).

## Running it at the company

`docs/DEPLOYMENT.md` has the full steps. In short, on the company's Windows PC, from an
elevated PowerShell in the project folder:

```
powershell -ExecutionPolicy Bypass -File scripts\windows_setup.ps1 -BackupFolder E:\packcheck-backups
```

It makes the server start with the PC (even before anyone logs in, restarted if it
crashes), backs up the database, artwork and proofs every night (`scripts/backup.py`),
prunes old inspection images every week, and opens the firewall port.
`scripts\windows_uninstall.ps1` undoes it.

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

### From a printer's proof (the usual case)

A proof is one sheet with every face side by side, plus bleed, trim and seal lines, a green
copy-safety box with "8mm" labels, a title block and signatures (often a scan of the signed
paper). None of that is on the real pack, so the proof cannot be used as-is.

1. On the home page, click **Add an artwork** (`/artworks/new`). Upload the proof PDF (or a
   PNG / JPEG scan). It is rendered at 300 dpi and kept in `private/proofs/`.
2. Drag a box exactly along the **magenta trim line** of the front, then of the back (zoom
   in to hit it). Type the artwork's name and product code (the item number); the label
   code and the barcode (typed, or read from a photo of it) are optional.
   "Add to the database" refuses an artwork that is already there: same product code,
   same barcode, or the same artwork (35% or more of its details match - only this last
   one can be overruled, for a lookalike that really is a different product). Otherwise it
   writes `products/<code>-front/` and `products/<code>-back/`: `reference.png` (the face
   inside the trim), `markup.png` and `product.json`.
3. `markup.png` marks the proof markup that was found in the face's margin - magenta or
   green, thin or small, outside the copy-safety box (see `proof.py`). Those pixels are
   never compared. The zone editor shows them in pink.
4. Open each face's zone editor (`/products/<id>/zones`) and draw:
   - an **ignore** zone over each handwritten approval tick: they are grey like the print
     and cannot be found automatically. Quicker: scan a good pack and press **Always ignore
     this spot** on each tick it flags. Better still, ask the printer for the unsigned
     print-ready PDF - then there are no ticks to cover, and the print is sharper.
   - the **variable** zone where the date / lot is printed (on the Hagen pouches: the
     white box at the bottom right of the back).
5. Inspect a photo of a good pack, then go on with step 4 below.

### From a plain artwork file

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
  photos yet. The same goes for the phone's photo check (blurry / dark / bright).
- A colour shift over most of the face looks the same as different lighting, so it is not
  detected. Reliable colour checking needs a fixed photo spot and a golden photo; the code
  supports the golden photo, the photo spot is furniture.
- Only flat faces align well. Bags and curved cans will produce false alarms. Pouches lie
  flat enough for a first try; their heat-sealed edges may need ignore zones.
- From a proof: handwritten ticks are not found automatically (ignore zones, or get the
  unsigned PDF); markup in other colours or inside the copy-safety box is not found either.
  A scanned proof's colours went through a printer and a scanner: trust it for text and
  layout, use a golden photo for colour.
- Small print: every reference is shrunk to 1400 px on its longest side for comparing. On a
  ~125 mm face that is still enough to catch "2 mm" vs "4 mm"; on a larger face, or one with a
  wide blank margin, a single changed character of small text can fall under the thresholds.
- OCR is tested on the demo's clean print only. Dot-matrix and inkjet codes will need
  preprocessing; until then they come back as "check by hand".
- The barcode detector is tested with QR codes only (the demo has no real EAN barcode).
- Auto-identify tries the photo against every product's reference, so it slows down as
  products are added (fine for tens; hundreds need a shortlist step first, see roadmap M6).
- The R79 cannot judge "legible" by itself: its ticks mean "matches the approved artwork,
  and a person reviewed every flagged difference". "Nouvelle version" is never ticked:
  replacing an artwork with a new version is not built yet (add it under a new code).
- A pack needs both a front and a back artwork to be scanned.
- No trained model anywhere. `scripts/export_decisions.py` collects the Confirm / Dismiss
  decisions that a future false-alarm filter would learn from (milestone 6).
- Pipeline messages (warnings, alignment errors) are English only; page text is translated.

## Layout

```
packcheck/        the code: identify, align, compare, reading, pipeline, product, proof, storage, report, codes, demo, cli, server, web/
products/         one folder per product; only the demo (front + back) is tracked by git
private/          raw artwork, test photos, exports; ignored by git
runs/             one folder per inspection; ignored by git
packcheck.sqlite  the inspection database; ignored by git
scripts/          render_artwork.py, evaluate.py, export_decisions.py, prune_runs.py, backup.py,
                  windows_setup.ps1 / windows_uninstall.ps1, server_logging.json
tests/            end-to-end tests on the demo
docs/             ROADMAP.md, m1-findings.md (template), DEPLOYMENT.md
CLAUDE.md         project context for Claude Code
```
