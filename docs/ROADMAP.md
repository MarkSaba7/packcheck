# Roadmap

Work in this order. Each milestone has a definition of done (DoD): stop when it is met,
write down what you learned, then move on. The order is by RISK: the things most likely
to kill the project come first, the things any web developer could build come later.

Status as of 2026-09-17: the CODE for every milestone exists and is tested on the synthetic
demo. What remains is what only the company can supply: real artwork, real photos, real
decisions. Each milestone below says what is built and what is still open.

Milestone 0 (done): the pipeline works end to end on a synthetic product, with tests, a
command line and a phone page.

---

## M1 - The reality check: real photos of one real product

Everything so far was tuned on simulated photos. This milestone finds out what real ones do.

1. Get the company's OK to use one artwork file and to photograph packs for testing.
2. Pick ONE product with a flat, rigid face (a box is ideal). Leave bags for later.
3. `scripts/render_artwork.py` -> crop one face exactly on its fold/cut lines ->
   `products/<id>/reference.png`. Write its `product.json` with the date/lot zones
   (or draw them at `/products/<id>/zones`).
4. Take 20-30 photos of a GOOD pack: different angles, distances, lighting, two phones.
5. Plant real defects cheaply: a small coloured sticker, correction tape over a word, a
   marker dot, a strip of tape over an icon. Photograph each a few times.
6. `scripts/evaluate.py`: run a folder of photos whose file names say what they contain
   (`good_*.jpg`, `defect_*.jpg`, optional `labels.json` with the defect boxes) and print
   how many false alarms and misses occur.
7. Tune the thresholds: `--strong-threshold` etc. on evaluate.py, one at a time; write the
   winners into the product's `compare_settings`.
8. Write `docs/m1-findings.md` (template in place): what caused false alarms, which
   defects were missed, and what the numbers are.

**Built:** steps 6 and 7 (tooling), the findings template.
**Open:** steps 1-5 and 8 need the company and a phone in hand.
**DoD:** measured false-alarm rate and miss rate on at least 30 real photos, and a written
answer to: "do we need a fixed photo spot (stand + constant light) for this to be usable?"

## M2 - Read the variable zones (dates, lot codes)

1. Zones are cut from the FULL-resolution photo (`align.crop_zone_from_original`).
2. OCR with RapidOCR (`reading.py`); lines under `MIN_CONFIDENCE` are never used.
3. `product.json` says what is expected in each variable zone: a date with formats and
   min/max days ahead, a code pattern.
4. `result.json` carries `text`, `lines`, `readings`, `valid`, `reason` per zone and a
   `readings_status` for the pack.

**Built:** all four, tested on the demo's clean print (front: best-before + lot; back: packed-on).
**Open:** real dot-matrix / inkjet codes. Expect to add preprocessing in `reading._prepare`.
**DoD:** on the M1 photo set, at least 95% of dates are read correctly, and every unreadable
one comes back as "could not read - check by hand" instead of a wrong guess.

## M3 - Save inspections and print the work-order report

1. SQLite (`storage.py`): `inspections`, `differences`, `decisions`, `readings` (+ users,
   sessions). `runs/` folders stay; the database indexes them.
2. Confirm / Dismiss with a note on the phone page, stored with who and when. Every
   decision is kept; the latest is shown.
3. `/report?day=...` or `?work_order=...`: printable HTML, "print to PDF" is the export.

**Built:** all three, tested through the API.
**DoD:** at the end of a test day, one click prints a report a supervisor can sign. (Needs a test day.)

## M4 - The real workflow

- QR hand-off: `/desk` shows a QR code with a session link; the phone that scans it sends
  inspections with that session; the desk polls and shows them. **Built.** The phone page
  also shows its own plain "open on your phone" QR (`GET /api/qr.png?text=<page url>`,
  no session) so a worker at a desktop can jump straight to the phone page.
- Choosing the product: **no picker by default.** The photo alone identifies it
  (`identify.py`: tried against every product's reference, same point-matching as
  alignment). If no candidate stands out clearly, the worker is shown the plausible ones
  to tap, or can search by name / photograph the barcode - never a silent guess. **Built**
  and tested, including the genuinely-ambiguous case (two near-identical references).
- Several faces per product: `pack` + `face` in product.json; one button per face on the
  phone; faces share a `pack_id`; the report groups them. **Built** (demo has front + back).
- Zone editor at `/products/<id>/zones`. **Built.**
- Users and login (name + PIN, `cli add-user`). **Built.**
- All interface text in `web/strings.json`, EN and FR. **Built.**

**DoD:** someone who has never seen the tool inspects five packs without help. (Needs that someone.)

## M5 - Colour you can trust

- A fixed photo spot or a colour reference card. **Open: furniture and procedure.**
- A second reference per product: `golden_photo` in product.json. Layout/text are judged
  against the artwork, colour against the golden photo. **Built** and tested: a print whose
  badge differs from the artwork but matches the golden photo passes; a badge that differs
  from the golden photo is flagged.
- CIEDE2000 (`compare.delta_e_2000`) for the weak pass and per-region `delta_e_2000`;
  per-product `compare_settings`. **Built.**
- Glare detection (`compare.find_glare`): saturated colourless blobs are left out and
  reported; over 2% of the face asks for a retake. **Built.**

**DoD:** a measured statement such as "colour shifts above delta E X are caught at the
photo spot, with fewer than Y false alarms per 100 packs". (Needs the photo spot and real packs.)

## M6 - Machine learning, where it pays

- A false-alarm filter trained on the Confirm / Dismiss decisions collected since M3.
  **Built: the data collection** (`scripts/export_decisions.py`). **Open: the model**, on
  purpose; there are zero real decisions to train on yet.
- Anomaly detection (PatchCore via `anomalib`) for flexible packs. **Open.**
- A vision-language model to describe each difference in words (only with the company's
  agreement if images leave their network). **Open.**
- Automatic product recognition from the photo. **Partly built**: `identify.py` already
  does this with classical matching (no training needed), which is enough for tens of
  products. What is still open is the SCALE-UP: with hundreds of products, matching the
  photo against every reference one by one gets slow, and a fast shortlist step first
  (an embedding or perceptual hash, narrowing to a handful of candidates before the exact
  match) is where a trained/learned step would actually pay for itself.

**DoD:** each model is compared against the rule-based version on held-out photos and is
only kept if it is measurably better.

## M7 - Deployment

HTTPS (also unlocks a live camera preview), hosting on the company's own server or
approved cloud, backups, access control, an audit log, and a retention rule for photos.

**Built:** `docs/DEPLOYMENT.md` (checklist with the plainest option for each point),
`scripts/prune_runs.py` (retention), login (access), decisions/logins tables (audit).
**Open:** doing it, and the IT review.
**DoD:** the company's IT person has reviewed and accepted the setup.
