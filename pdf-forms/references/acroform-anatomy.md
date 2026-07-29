# AcroForm anatomy

What you need to know about the object model to make field names and checkbox
states behave. Read this when `fields` output looks strange or a write does not
take.

## The two-tree problem

A form exists in two places at once:

- **`/AcroForm /Fields`** — the document's declaration of what fields exist.
  A tree: intermediate nodes carry `/T` (a name part) and `/Kids`.
- **each page's `/Annots`** — the *widgets*, the rectangles actually drawn.

For a simple form these coincide: a terminal field object is also its own
widget, appearing in both. For anything assembled by merging, they can diverge —
see `gotchas.md#stale-field-table`. **The page is authoritative** for values and
appearance; the field tree is authoritative for structure, types and flags.

`/Annots` is an `IndirectObject` on some pages and a resolved `ArrayObject` on
others *within the same document*. Always `.get_object()` before iterating, or
half the pages silently look empty.

## Fully-qualified names

The key a writer expects is the path from the root, joined with `.`:

```
topmostSubform[0].Page1[0].f1_01[0]      <- standard IRS naming
```

Built by concatenating each ancestor's `/T`. A leaf-only name (`f1_01[0]`) will
not match. Not all forms are hierarchical — the NY PPB-3 form has 356 fields and
zero dotted names — but IRS and USCIS forms always are.

## Terminal fields vs widgets

A node is a **terminal field** if it has no `/Kids`, *or* its kids have no `/T`
(kids without `/T` are widget annotations, not sub-fields). This distinction is
the whole algorithm:

```python
kids = obj.get("/Kids") or []
subfields = [k for k in kids if "/T" in k.get_object()]
if subfields:  # intermediate node — recurse
else:          # terminal field — its widgets are kids, or itself
```

One field can own **many widgets** — the same value drawn in several places. On
the PPB-3 form, 199 of 356 fields have more than one widget (754 widgets total);
the applicant's surname is drawn on 14 pages. The value lives once on the field;
every widget needs its own appearance.

pypdf's docs put the trap plainly: `reader.get_fields()` returns the *parent*
(the radio group) while `page.annotations` returns the *children* (the
individual buttons). You need both views.

## Field types

`/FT` plus flag bits in `/Ff`:

| `/FT` | Bit set | Is |
|---|---|---|
| `/Tx` | — | text |
| `/Tx` | 13 (`1<<12`) | multiline text |
| `/Btn` | 17 (`1<<16`) | pushbutton (an action — nothing to fill) |
| `/Btn` | 16 (`1<<15`) | radio group |
| `/Btn` | neither | checkbox |
| `/Ch` | 18 (`1<<17`) | dropdown / combo |
| `/Ch` | — | list box |
| `/Sig` | — | signature |

`/Ff` bit 1 is ReadOnly, bit 2 is Required. Both are inherited from ancestors,
so resolve them while walking down.

## Discovering checkbox and radio on-states

The single most important thing in this file.

The legal "on" value is **whatever non-`/Off` key exists in the widget's
`/AP /N` dictionary**. Not `/Yes`. Read them:

```python
ap = widget.get("/AP")
normal = ap.get_object().raw_get("/N").get_object()
if isinstance(normal, StreamObject):
    ...        # single appearance, no states to choose from
elif isinstance(normal, DictionaryObject):
    states = {str(k) for k in normal.keys()} - {"/Off"}
```

**Order the isinstance checks this way round.** `StreamObject` subclasses
`DictionaryObject`, so testing for a dictionary first reports a stream's own
keys — `/BBox`, `/Filter`, `/Resources`, `/Subtype` — as if they were valid
checkbox states.

`/V` holds the field's value; `/AS` holds the widget's current *appearance
state*. For buttons they should agree, and `/AS` is what actually renders — so
when they disagree, `/AS` is what the reader sees.

## Radio groups

A radio group is one field (`/Btn` with the radio bit) whose kids are the
individual buttons. Each kid has its own on-state; selecting one means setting
the parent's `/V` to that kid's state, and that kid's `/AS` to the same value
while every sibling goes to `/Off`. Export values default to `Choice1`,
`Choice2`… when the author did not name them.

## Choice fields

`/Opt` is either a list of strings, or a list of `[export_value, display_value]`
pairs — when it is pairs, `/V` must be the **export** value, not what the user
sees on screen.

Dropdowns are the field type most often lost by tools that *regenerate*
appearances while flattening: the regenerated stream comes out as an empty box.
Copying the existing appearance stream avoids this entirely.

## Appearance streams

`/AP /N` is a form XObject with its own `/BBox` and optional `/Matrix`. To paint
it into the page (this is what flattening is), map the BBox onto the widget's
`/Rect` per PDF 32000-1 §12.5.5:

1. transform the four BBox corners by `/Matrix`,
2. take the bounding box of the result,
3. scale and translate that box onto `/Rect`.

Then emit `q <a> 0 0 <d> <e> <f> cm /Name Do Q` into the page content with the
XObject registered in the page's `/Resources /XObject`.

Skip widgets with the Hidden flag (`/F` bit 2) — painting them makes invisible
content visible, which on a form usually means exposing something deliberately
suppressed.
