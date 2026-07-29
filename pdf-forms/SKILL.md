---
name: pdf-forms
description: >-
  Use when working with any PDF that has to be filled in, read out, or made
  final — government, bank, tax, legal, insurance, HR, medical or real-estate
  forms. Covers extracting field names and values, filling fields, checking
  boxes, OCR-ing scanned pages, placing text on printed forms that have no
  fields, flattening so values cannot be edited or extracted, and batch-filling
  one template from many records. Use it whenever the user says fill out this
  PDF, complete this application, what fields does this have, this scan isn't
  searchable, make this non-editable, flatten the signature, why is my PDF blank
  when I print it, or mentions AcroForm, XFA, W-9, 1040, I-9, I-765, or a form
  number — and use it even when they never say the word "form".
license: MIT
---

# pdf-forms

Fill any PDF form end to end and **prove** the result is right.

The three ways a PDF form goes wrong are all silent: a value that is set but
never drawn, a checkbox written with the wrong state name that no library
complains about, and a "flatten" that leaves every answer extractable — or
deletes them. Nothing errors. You find out when the agency rejects it. Every
command here is built to make those failures loud.

## Start here, always

```bash
uv run --script scripts/pdfform.py triage FILE.pdf
```

It classifies the file and prints the exact next command. Do not skip it: the
right approach differs completely per lane, and guessing wastes a cycle.

| Lane | Means | Do |
|---|---|---|
| `acroform` | Real fillable fields | `fields` → `fill` → `verify` |
| `flat` | Has text, no fields (printed form) | `overlay.py geometry` → look → `draw` |
| `scanned` | Image only, no text layer | `ocr` first, then re-`triage` |
| `xfa` | Dynamic LiveCycle form, no fields | **Stop.** Read `references/gotchas.md#xfa` |
| `xfa-hybrid` | XFA *and* real fields (e.g. IRS W-9) | Fill normally, but `--flatten` so Acrobat can't prefer the XFA data |
| `encrypted` | Password or permissions | `--password`, or `qpdf --decrypt` |

## Filling a fillable form

```bash
uv run --script scripts/pdfform.py fields FILE.pdf --json --mask   # discover
uv run --script scripts/pdfform.py fill  FILE.pdf --data d.json -o out.pdf --flatten
uv run --script scripts/pdfform.py verify out.pdf --expect d.json --original FILE.pdf --flat
```

Read `fields` output before writing `d.json`. It gives the **fully-qualified**
field name and, for every checkbox and radio, its **legal on-states**. Those are
rarely `/Yes` — real forms use `/On`, `/1`, `/Yes_3`, `/No_12`. Writing a state
that does not exist is the most common silent no-op in PDF work, so `fill`
rejects it rather than writing something that will not take. `true` / `"Yes"` /
`"X"` are accepted and coerced onto the form's real on-state when there is only
one; when a field has several, name the one you want.

Use `--mask` whenever the form already contains someone's answers. Forms are
full of SSNs, DOBs and account numbers, and there is no reason to pull them into
a transcript just to learn the structure.

**Always run `verify`.** It is the point of this skill, and it checks the things
that fail quietly: that each field holds the value you asked for (not merely
*some* value — an unchecked box holds `/Off`), that appearance streams exist so
values actually print, that a flattened file has no form layer left, that
pre-existing answers were not destroyed, and that the page visibly changed.

## Flattening

`--flatten` merges each widget's **existing appearance stream** into the page,
then removes the widgets and the `/AcroForm` dictionary. Existing streams are
copied, never regenerated — measured pixel-identical to the live form on a
49-page government application, which regeneration is not: it blanks dropdowns
and re-lays-out text.

Flattening matters beyond editability. Until you flatten, every answer is a
live object in the file — anyone receiving it can extract the values
programmatically, or revert them. "Set the fields read-only" is not flattening.

## Scanned forms

`ocr` runs the cheapest rung that works, escalating only when it must. On macOS
that starts with Apple's Vision engine: free, offline, no model download, and
better than Tesseract on real scans. Read `references/ocr-ladder.md` before
reaching for a vision model — and note that OCR gives you *text*, never *meaning*.
For "is this box ticked" or "which option did they choose", a vision model is
the right tool and the local engines are not.

## Printed forms with no fields

There is nothing to set, so text is drawn at coordinates:

```bash
uv run --script scripts/overlay.py geometry FILE.pdf --page 1   # + renders a PNG
# look at the PNG, decide where values go, then:
uv run --script scripts/overlay.py draw FILE.pdf --boxes b.json -o out.pdf --space image
```

`--space image` takes pixel coordinates measured on that render, so numbers read
off the picture can be used directly. **Then render the result and look at it.**
Coordinate placement has no failure signal — text two inches too low raises
nothing.

## Batch

```bash
uv run --script scripts/pdfform.py batch TPL.pdf --records rows.csv -o out/ --name-key client
```

CSV headers or JSON keys are field names. Each row is validated before anything
is written, and one bad row does not kill the run.

## Scripts

- **`scripts/pdfform.py`** — `triage`, `fields`, `fill`, `flatten`, `verify`,
  `ocr`, `batch`. Depends only on pypdf.
- **`scripts/overlay.py`** — `geometry`, `draw` for field-less forms. Pulls
  pdfplumber and reportlab, which is why it is a separate file.

Both declare their own dependencies inline (PEP 723) and run under
`uv run --script` with nothing installed. No venv, no global packages.

## Reference files

- `references/gotchas.md` — read when something silently does not work: XFA,
  blank-until-clicked, doubled text after flattening, encrypted files, stale
  field tables in merged documents.
- `references/acroform-anatomy.md` — read when field names or checkbox states
  are not behaving: hierarchical names, widgets vs fields, on-state discovery.
- `references/ocr-ladder.md` — read before OCR-ing anything, and before paying
  for a vision model.
- `references/flat-forms.md` — read when there are no fields to fill.

## Verify before saying it is done

```bash
bash tests/smoke.sh /path/to/any/acroform.pdf
```

15 checks over a real form, including regressions for two bugs that shipped
during development and destroyed data.
