# Milestone 1 findings: real photos of one real product

> STATUS: TEMPLATE. Nothing below is measured yet. Fill it in after photographing a real pack.
> The tool cannot do this step by itself: it needs the company's OK, one artwork file and a
> phone in your hand. Everything marked `TO FILL IN` is waiting for that.

## What was tested

| | |
|---|---|
| Product | TO FILL IN (one flat, rigid face; a box is ideal) |
| Reference made with | `scripts/render_artwork.py` at TO FILL IN dpi, cropped on the fold lines |
| Phones used | TO FILL IN |
| Lighting | TO FILL IN (window, ceiling light, desk lamp...) |
| Number of good photos | TO FILL IN (aim for 20-30) |
| Number of defect photos | TO FILL IN, defects planted with: sticker / correction tape / marker / tape over icon |

Photos live in `private/photos/<product_id>/` and are named `good_*.jpg` / `defect_*.jpg`.
Optional `labels.json` there says where each planted defect is (see `scripts/evaluate.py`).

## How to measure

```
python scripts/evaluate.py --product products/<id>/product.json --photos private/photos/<id>/
```

Change ONE threshold at a time and re-run, for example `--strong-threshold 16`. Keep a row
per attempt below. The thresholds live in `compare.find_differences`; once a set is chosen,
write it into the product's `product.json` under `"compare_settings"` (milestone 5 added
that) so the tuned values travel with the product.

## Numbers

| Attempt | Settings changed | Good photos | False alarms | Defect photos | Found | Missed | Align failed |
|---|---|---|---|---|---|---|---|
| 1 | defaults | TO FILL IN | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |

## What caused the false alarms

Open the `comparison.jpg` of each false alarm in `runs/evaluate/<photo>/` and sort them:

- Glare: TO FILL IN (count). Milestone 5 masks saturated colourless blobs; check whether it catches these.
- Wrinkles / not flat: TO FILL IN
- Colour (lighting correction not enough): TO FILL IN
- Alignment off along edges: TO FILL IN
- Pack cut differently from the artwork (border): TO FILL IN, and whether `BORDER_FRACTION` in `pipeline.py` needs to grow
- Other: TO FILL IN

## Which defects were missed, and why

TO FILL IN. Typical reasons: the defect is smaller than `strong_min_pixels`, its colour
distance is below `strong_threshold`, or it landed in a variable/ignore zone.

## The question this milestone must answer

**Do we need a fixed photo spot (stand + constant light) for this to be usable?**

TO FILL IN. Rule of thumb: if false alarms on good photos are mostly glare and colour,
a photo spot fixes them; if they are wrinkles and alignment, the problem is the pack
shape, and a photo spot will not help (see milestone 6, anomaly detection).

## What to carry forward

- Chosen thresholds: TO FILL IN
- `BORDER_FRACTION`: TO FILL IN
- Photo instructions for workers (distance, angle, what to avoid): TO FILL IN
