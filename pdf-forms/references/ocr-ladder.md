# OCR ladder

Cheapest rung that works. Escalate only on a stated trigger. The important
framing: **rungs 0–3 give you text and coordinates; only rung 4 gives you
meaning.** They are not substitutes.

## Rung 0 — does it need OCR at all?

Free, instant, already installed:

```bash
pdffonts scan.pdf                       # zero fonts on a page ⇒ pure image
pdftotext -layout scan.pdf - | awk 'BEGIN{RS="\f"}{print NR": "length($0)" chars"}'
```

A born-digital page yields thousands of characters; a scan yields ~1. Measured
on a real 38-page appraisal scan: **0 fonts, 38 characters total.** The
threshold used by `triage` is 100 chars/page — a convention, not a spec.

**Stop here** if there is already good text. **Escalate** if fonts are 0, or
chars/page is below the threshold, or the pages are a mix.

The heuristic is verified in both directions — the same file before and after
`ocr`:

| | fonts | chars/page | `triage` lane |
|---|---|---|---|
| before | 0 | 0.0 | `scanned` |
| after | 2 | 1295.0 | `flat` |

A lane that does not change after OCR means the OCR did nothing, whatever it
reported on the way out.

## Rung 1 — Apple Vision (macOS default)

Free, offline, no model download, and **measurably** better than Tesseract on
real scans.

```bash
brew install ocrmypdf          # also brings tesseract, ghostscript, qpdf, unpaper
ocrmypdf --ocr-engine appleocr --skip-text scan.pdf out.pdf
```

**Installing the plugin — the non-obvious part.** Homebrew builds ocrmypdf's
virtualenv with `--without-pip`, so there is no `pip` inside it and the README's
`pip install ocrmypdf-appleocr` has nothing to run. Inject it into that
interpreter instead:

```bash
V=$(brew --prefix ocrmypdf)/libexec
uv pip install --python "$V/bin/python" ocrmypdf-appleocr
ocrmypdf --help | grep 'ocr-engine {'   # confirm: {auto,tesseract,none,appleocr}
```

`appleocr` does not appear in `--ocr-engine`'s choices until the plugin
registers itself, which makes this easy to verify and easy to miss. Note a
`brew upgrade ocrmypdf` replaces the Cellar directory and drops the plugin —
re-run the two commands above.

Requires ocrmypdf ≥ 17 for `--ocr-engine`. Known limitation: multi-language
(`-l eng+fra`) is not supported in the default livetext mode.

### Measured, not assumed

Verified 2026-07-29 on a real 3-page scanned business letter (0 fonts, 3
extractable characters before OCR) with ocrmypdf 17.8.1 and tesseract 5.5.3 on
Apple Silicon:

| | Apple Vision | Tesseract |
|---|---|---|
| Wall clock, 3 pages | 4s | 2s |
| Characters recovered | 2,148 | 2,168 |
| **OCR artifacts** | **0** | **3** |

All three Tesseract errors were in the first 500 characters, and all three are
its classic failure shapes:

| Shape | Example |
|---|---|
| stray punctuation inside a number | `NY 12553` → `NY .12553` |
| `ll` read as `li` | a place name silently altered |
| `I` read as `\|` | `I am writing` → `\| am writing` |

Apple Vision got all three right. Note that similar character *volume* is not
similar *accuracy* — the counts differ by 1% while one output is clean and the
other has three errors, so **never use character count as a quality signal.**
The `ll`→`li` class is the dangerous one: it produces a real-looking word that
no spellcheck or heuristic will flag.

Tesseract is faster on small jobs. Accuracy wins.

Zero-install alternative, a prebuilt Swift binary using the same engine:

```bash
npx mac-ocr searchable-pdf scan.pdf     # → scan.ocr.pdf
npx mac-ocr scan.pdf --format jsonl     # text + bboxes + confidence per page
```

For text and boxes without a PDF round-trip: `pip install ocrmac`, then
`ocrmac.OCR('page.png', framework="livetext").recognize()` returns
`[(text, confidence, bbox), ...]`.

**Escalate** if mean confidence is low, the output has a high garbage-character
ratio, or extracted text is far below the visible page density.

## Rung 2 — Tesseract (portable fallback)

```bash
ocrmypdf --oversample 400 --clean --deskew --rotate-pages scan.pdf out.pdf
ocrmypdf --sidecar out.txt scan.pdf out.pdf      # also dump plain text
```

Only reasons to choose it: not on macOS, or a language Vision does not cover.
Tesseract is mediocre on forms specifically — ruled lines, boxes and dotted
leaders break it.

**Escalate** on dense tables or multi-column layout.

## Rung 3 — neural OCR (surya)

Reach for it when layout, reading order or table structure matters, or when
rungs 1–2 failed. Costs ~1GB of models plus torch, so it is not a default.

```bash
pip install surya-ocr
```

Still returns text and boxes — not semantics.

## Rung 4 — vision model

**Escalate here the moment the task is "extract structured fields" rather than
"get the text".** This is the only rung that reliably answers:

- which of these options was selected
- is this checkbox ticked (✓ vs ✗ vs filled vs scribbled-out vs overflowing the box)
- which value belongs to which label
- what does this handwriting say

Classical OCR returns `(text, bbox, confidence)`. It cannot tell you that
`"Smith"` at (410, 622) is the *value* of `"Last Name:"` at (180, 622). That is
inference, not recognition. Layout models (LayoutLMv3, Donut) need per-form
fine-tuning, which is impractical for a tool handed an arbitrary form.

Render the page first:

```bash
pdftoppm -r 300 -png -f 1 -l 1 scan.pdf page     # → page-1.png
```

**300 DPI is the right number.** Tesseract's docs want ≥300; below 200 is
unusable; above 600 grows file size without accuracy gain. It is also right for
the vision path — current models downsample to ~2576px on the long edge anyway,
so a letter page above ~300 DPI is wasted bytes.

Cost is not the reason to keep this at rung 4 — a letter page is roughly 1–3¢,
so 200 pages is a few dollars against an hour of your time. The reasons are
determinism, offline capability, and not spending a network round-trip on a page
`pdftotext` would have read for free.

## The `--skip-text` / `--redo-ocr` / `--force-ocr` footgun

Since ocrmypdf 17 these are aliases for `--mode skip|redo|force`. By default
ocrmypdf **exits with an error** if a page already seems to have text, rather
than quietly doing nothing — which is the right default and the source of the
confusion.

| Mode | Does | Correct when |
|---|---|---|
| `skip` | Copies pages that already have text untouched, OCRs the rest | **Mixed documents** — born-digital pages interleaved with scans. The safe default for heterogeneous batches. |
| `redo` | Strips a previous *invisible* OCR layer, keeps genuine visible text, re-OCRs the images | **A PDF that was already OCR'd badly.** Non-destructive to real text. People reach for this far too late. |
| `force` | **Rasterizes every page**, discarding all existing text, then OCRs | **Last resort.** Correct for damaged character maps (text extracts as mojibake) and for destroying redacted content. **Lossy — you permanently lose selectable born-digital text.** |

The footgun: people hit "page seems to have text", reach for `--force-ocr`
because it always "works", and it works by destroying the document. Escalate
`skip` → `redo` → `force`, and you should almost never arrive at `force`.

Second footgun: when a scanner or earlier tool stuffed a garbage text layer in,
`--skip-text` skips the page *because it has text* — `redo` is what you want.

## Making a scanned form genuinely fillable

Optional, not a dependency. `commonforms` (jbarrow/commonforms) does object
detection on rendered page images and injects real AcroForm widgets, so it works
on scans. Two caveats before adopting: it pulls torch + transformers + rfdetr +
ultralytics, and **the repository declares no license**, which is a real blocker
for anything you redistribute.

Usually unnecessary. If the goal is a completed document rather than a reusable
template, `overlay.py` draws the values directly and skips the whole problem.
