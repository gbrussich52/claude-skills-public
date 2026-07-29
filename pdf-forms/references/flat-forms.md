# Flat forms — no fields to fill

A "flat" form has text but no `/AcroForm` fields: a printed form, a PDF exported
from a word processor, or a scan after OCR. There is nothing to set, so values
are drawn at coordinates.

Be honest about the state of the art: **automatic field detection on flat PDFs
is not a solved open-source problem.** Adobe's own SDK forum answer to "auto
detect input fields in a flat pdf" is that it is not available. Commercial
services do it with vision models. So the reliable approach is to *look at the
page* — which, for an agent, is cheap and accurate.

## The loop

```bash
# 1. geometry + a rendered image
uv run --script scripts/overlay.py geometry form.pdf --page 1

# 2. look at the PNG it rendered; decide coordinates

# 3. draw, using pixel coordinates measured on that image
uv run --script scripts/overlay.py draw form.pdf --boxes boxes.json \
      -o filled.pdf --space image --dpi 150

# 4. render the RESULT and look at it again
pdftoppm -r 100 -png -f 1 -l 1 filled.pdf check && open check-1.png
```

Step 4 is not optional. Coordinate placement has no failure signal: text two
inches too low, mirrored vertically, or off the page raises nothing at all.

## boxes.json

```json
[
  {"page": 1, "x": 300, "y": 400, "text": "Jordan Rivera"},
  {"page": 1, "x": 512, "y": 400, "text": "2026-07-29", "size": 9},
  {"page": 1, "x": 190, "y": 455, "mark": "x"},
  {"page": 2, "x": 300, "y": 120, "text": "Total", "align": "right", "color": "#0a0a0a"}
]
```

`mark` draws a checkmark-style `X` slightly larger than body text. `align` may
be `left` (default), `center` or `right` — right-alignment is what you want for
currency columns.

## Coordinate spaces — the thing that goes wrong

Three spaces are in play and mixing them mirrors the page:

| `--space` | Origin | y direction | Use when |
|---|---|---|---|
| `pdf` (default) | bottom-left | up | you computed points yourself |
| `image` | top-left of the render | down | **normal** — you read pixels off the PNG |
| `topleft` | top-left, in points | down | you have points from pdfplumber |

`geometry` reports words and rectangles in **pdfplumber space** (top-left
origin, points), and also reports `render_size_px` so image pixels can be
converted. `--space image` does the conversion for you: `x_pt = x_px × 72/dpi`
and `y_pt = page_height − y_px × 72/dpi`, then drops the baseline by ~0.8 × font
size so text sits *on* the line rather than floating above it.

Pass the same `--dpi` you rendered at. Rendering at 150 and drawing at 300
halves every coordinate, which produces a plausible-looking page with everything
in the wrong place.

**Verified:** image pixel (300, 400) at 150 DPI on a 612×792pt page lands at PDF
(144, 590.4) — `300 × 72/150 = 144`, `792 − 192 − 9.6 = 590.4`.

## Reading the geometry output

- **`rule_candidates`** — long, thin rectangles and lines: the write-on lines.
  Draw text a few points *above* a rule's `top`, not on it.
- **`checkbox_candidates`** — small near-square rectangles, 6–20pt with aspect
  ≤ 1.45. Tuned against real government forms, where boxes cluster at 8–14pt.
  Table cells and specks fall outside that band.
- **`words`** — omitted by default (pass `--words`) because a dense page is
  thousands of entries. Useful for anchoring: find the label, then place the
  value at a fixed offset from its `x1`/`top`.

These are *candidates*, deliberately. A geometric heuristic cannot know that a
box means "check here if you are a veteran". Use them to narrow where to look,
and use the image to decide what each one is.

## Why not detect checkbox state with OpenCV

You will find tutorials for it. The honest comparison (Jeremias Rodriguez's
OpenCV-vs-YOLO writeup) is that OpenCV works on standardised forms but needs
per-template parameter tuning — thresholds, filters and geometric heuristics
recalibrated for every new form — while a small trained detector generalises
with none. For a general-purpose tool the tuning burden is a maintenance trap
that fails on the first unfamiliar form.

Split the problem instead: use geometry for **location** (free, local,
deterministic) and a vision model for **state** (is it ticked, and with what —
✓, ✗, a fill, a scribble, or a mark overflowing the box). That is the only
approach that survives real-world mess.

## When to make it a real form instead

If you need a reusable fillable template rather than one completed document,
`commonforms` injects real AcroForm widgets — see `ocr-ladder.md` for the
dependency weight and the licensing caveat. For a one-off, drawing is faster,
lighter and produces a document that is already effectively flattened.
