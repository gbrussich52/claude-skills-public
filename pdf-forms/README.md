# pdf-forms

A Claude Code skill for filling PDF forms end to end — extract fields, OCR
scanned pages, fill, flatten, and **verify the result is actually right**.

Niche until you're handed a government or bank form. Then it's the one you
reach for constantly.

## Why this exists

The three ways a PDF form goes wrong are all silent:

1. **A value is set but never drawn.** It looks correct in Adobe Acrobat and
   prints blank everywhere else, because the field has a value but no
   *appearance stream*.
2. **A checkbox is written with a state name that doesn't exist.** Real forms
   use `/On`, `/1`, `/Yes_3`, `/No_12` — almost never the `/Yes` you'd guess.
   Every PDF library accepts the wrong one and does nothing.
3. **"Flatten" doesn't flatten.** Many tools just set fields read-only. Your
   answers are still live objects that anyone receiving the file can extract or
   revert — including the SSN on the form you just emailed a bank.

No exceptions are raised for any of these. You find out when the form is
rejected. This skill is built to make them loud.

## Install

Drop the `pdf-forms` directory into `~/.claude/skills/`. That's it — the scripts
declare their own dependencies inline ([PEP 723][pep723]) and run under
`uv run --script` with nothing installed globally. No venv, no `pip install`.

Requires [uv][uv] and, for rendering and text extraction, poppler
(`brew install poppler`).

[pep723]: https://peps.python.org/pep-0723/
[uv]: https://docs.astral.sh/uv/

## Use

```bash
# 1. Classify the file — it prints the exact next command
uv run --script scripts/pdfform.py triage form.pdf

# 2. Discover fields, including each checkbox's real on-states
uv run --script scripts/pdfform.py fields form.pdf --json --mask

# 3. Fill and flatten
uv run --script scripts/pdfform.py fill form.pdf --data values.json -o out.pdf --flatten

# 4. Prove it worked
uv run --script scripts/pdfform.py verify out.pdf --expect values.json \
      --original form.pdf --flat
```

Also: `ocr` for scanned pages, `batch` for one template × many records, and
`overlay.py` for printed forms that have no fields at all.

## What `verify` checks

The reason the skill is worth having. It asserts the things that fail quietly:

| Check | Catches |
|---|---|
| `values_match` | A field holding *some* value instead of *your* value — an unticked box holds `/Off`, which passes a naive presence check |
| `appearance_streams` | The blank-when-printed bug |
| `flattened` | A "flattened" file whose form layer, and therefore whose data, survives |
| `nothing_lost` | A flatten that destroyed answers already in the form — verified **per page**, since a document-wide search passes on any common word |
| `visually_changed` | A fill that changed no pixels, checked on a page that *should* have changed |

## Flattening

Existing appearance streams are copied into the page content, never
regenerated. Measured **pixel-identical** to the live filled form on a 49-page
government application. Regeneration is the common approach and it is lossy: it
blanks dropdown fields, re-does font layout, and in qpdf's generator replaces
every non-ASCII character with `?` (their manual says so outright).

## Design notes

- **pypdf only** for the core. BSD-licensed, pure Python, no system
  dependencies. PyMuPDF is faster but AGPL; `pdf-lib` has been unmaintained
  since 2021 (the maintained fork is `@cantoo/pdf-lib`).
- **qpdf is optional**, not required. `flatten --qpdf` delegates to it if you
  prefer, but the built-in path needs nothing installed.
- **The page is authoritative, not `/AcroForm/Fields`.** In documents assembled
  by merging, the field table can hold stale duplicates that never update. Field
  state is read from the widgets attached to pages.
- **OCR escalates cheapest-first** — Apple Vision (free, offline, no models) →
  Tesseract → neural OCR → a vision model, and only the last rung understands
  *structure* like "is this box ticked".

## Test

```bash
bash tests/smoke.sh /path/to/any/acroform.pdf
```

15 assertions against a real form, including regressions for two bugs found
during development that silently destroyed user data.

## Docs

- `references/gotchas.md` — the silent failures and their fixes
- `references/acroform-anatomy.md` — field trees, widgets, on-state discovery
- `references/ocr-ladder.md` — when to escalate, and the `--force-ocr` footgun
- `references/flat-forms.md` — forms with no fields, and coordinate spaces

## License

MIT
