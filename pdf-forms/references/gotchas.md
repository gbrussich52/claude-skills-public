# Gotchas — the failures that produce no error

Every entry here is a case where the tooling reports success and the document is
wrong. They are ordered by how often they bite.

## Contents

- [Values are set but render blank (NeedAppearances)](#values-render-blank)
- ["Flattened" but the data is still extractable](#flatten-vs-readonly)
- [Flatten destroyed the answers that were already there](#flatten-destroys)
- [Checkbox written, nothing happens](#checkbox-no-op)
- [The field table disagrees with the page (merged documents)](#stale-field-table)
- [XFA forms](#xfa)
- [Doubled or offset text after flattening](#doubled-text)
- [Encrypted and permission-restricted files](#encrypted)
- [Non-ASCII characters become `?`](#unicode)

---

<a id="values-render-blank"></a>
## Values are set but render blank

**Symptom:** you fill the form, the values are definitely in the file, and it
looks right in Adobe Acrobat — but the fields are empty in Preview, Chrome,
Firefox, on a phone, and **on paper**.

**Cause:** you wrote `/V` (the value) but no `/AP` (the appearance stream). A
PDF viewer draws the appearance stream, not the value. Acrobat regenerates
appearances on the fly and so hides the bug; nothing else does.

**The wrong fix:** setting `/NeedAppearances true`. It is advisory, widely
ignored, and triggers a spurious "save changes?" prompt. pypdf's own docs say to
use `auto_regenerate=False` — that parameter *is* the NeedAppearances flag.

**The right fix:** generate real appearance streams at fill time.
`scripts/pdfform.py fill` does this and additionally sets `/NeedAppearances`
false, because a source form that ships with it true is relying on the viewer.
`verify` has an `appearance_streams` check for exactly this.

**Seen in the wild:** the NY pistol permit application (PPB-3) ships with
`/NeedAppearances: true`. `triage` warns when it sees this.

---

<a id="flatten-vs-readonly"></a>
## "Flattened" but the data is still extractable

**Symptom:** you flattened a form and emailed it. The recipient can still select
the values as objects, extract them with any library, or re-enable editing.

**Cause:** many tools "flatten" by setting each field's ReadOnly flag (`/Ff` bit
1). That is not flattening. The values remain live objects in the file.

**Why it matters:** on a form carrying an SSN or an account number, the
difference is whether that number is still machine-readable metadata in the
document you just sent to a third party.

**Real flattening is four steps:**
1. every widget has a correct appearance stream,
2. those streams are painted into the page's content stream,
3. the widget annotations are removed from `/Annots`,
4. `/AcroForm` is removed from the document catalog.

**Check it:** `verify OUT --flat` fails unless `/AcroForm` is gone *and* zero
widget annotations remain. `fields OUT` on a properly flattened file must error
with "no AcroForm fields".

---

<a id="flatten-destroys"></a>
## Flatten destroyed the answers that were already there

**Symptom:** you filled in three fields on a partly-completed form, flattened,
and the output has *only* your three values. Everything that was already in the
form is gone. No error.

**Cause:** libraries that flatten by *regenerating* appearances only regenerate
for the fields you passed in, then remove all widgets. Any field not in your
payload loses its widget and never gets content painted for it.

**The fix used here:** copy each widget's **existing** appearance stream into
the page instead of regenerating anything. Values you did not touch are
preserved because their appearance is already correct — whoever filled them made
it. This also preserves original fonts, dropdown values and non-ASCII text.

**Check it:** `verify OUT --original SRC --flat` runs a `nothing_lost` check
that confirms every pre-existing value still renders **on its own page**. A
whole-document search is not good enough — a common word like "Female" appears
on many pages of a long packet and would mask a real loss.

---

<a id="checkbox-no-op"></a>
## Checkbox written, nothing happens

**Symptom:** you set a checkbox to `"Yes"` / `true` / `"X"` / `1`. No error. The
box is unticked.

**Cause:** a button's on-state is whatever non-`/Off` key exists in its
appearance dictionary. Per ISO 32000-1 the *off* state must be named `Off`; the
on state is only *recommended* to be `Yes`. Real forms use `/On`, `/1`,
`/Choice1`, `/Yes_3`, `/No_12`, `/CB_3`. Values are case-sensitive: writing
`"yes"` where the state is `/Yes` does nothing.

**Measured:** the NY PPB-3 form has 105 button fields whose on-states include
`/On`, `/Yes_3` … `/Yes_23`, and `/No_2` … `/No_22`. `"Yes"` is wrong for nearly
all of them.

**The fix:** read the on-states before writing. `fields --json` reports
`on_states` per button; `fill` refuses any value that is not one of them (after
coercing the obvious "check it" words when the field has exactly one on-state).

---

<a id="stale-field-table"></a>
## The field table disagrees with the page

**Symptom:** you fill a field, the write succeeds, and reading the file back
shows the old value. Or a form reports a checkbox as unticked when it visibly is
ticked.

**Cause:** `/AcroForm/Fields` is a *declaration*. In documents assembled by
merging — every multi-agency packet — it can contain stale duplicate objects
that share a field's name but are **not** the object drawn on the page. Writers
update the widget attached to the page; readers that trust `get_fields()` then
report the stale copy forever.

**Measured:** on the PPB-3 form, the `Black` checkbox in `/AcroForm/Fields` is a
different object from the widget on page 11. After a successful fill the page
widget reads `/On` and the `/Fields` copy still reads `/Off`.

**The fix:** treat the page as authoritative. `walk_fields()` indexes widgets by
fully-qualified name from the page tree and prefers their `/V` (or `/AS` for
buttons), reporting a `stale_declaration` note when the two disagree.

---

<a id="xfa"></a>
## XFA (Adobe LiveCycle) forms

**Symptom:** the fill reports success and the PDF opens unchanged in Acrobat.

**Cause:** XFA is a separate XML form layer. If `/XFA` is present with an empty
`/Fields`, it is a *dynamic* XFA form and the AcroForm values you wrote are
ignored. If both are populated it is a hybrid, and Acrobat still prefers the XFA
data — so your fill can appear to work and then be overridden.

XFA was excluded from the PDF 2.0 standard and no open-source tool fills it.
pypdf carries an open issue from 2024-09-01 for exactly this, and its reader has
a bare `# TODO: No error but this may be extended for XFA Forms`.

**What to do — the two cases behave very differently:**

| `triage` lane | Means | Behaviour |
|---|---|---|
| `xfa` | `/XFA` present, **no** AcroForm fields — dynamic XFA | `fill` refuses. There is genuinely nothing to write. |
| `xfa-hybrid` | `/XFA` present **with** AcroForm fields | `fill` warns loudly and proceeds. |

Do not refuse hybrids. Their AcroForm layer is real and fillable, and it renders
correctly in Preview, Chrome, and in print — only Acrobat prefers the XFA data.
**The IRS W-9 is a hybrid**, and filling one is about as common as PDF tasks get.
Blocking it would be a guard so strict it defeats the purpose.

For a hybrid, `--flatten` makes the result unconditional: once the values are
painted into the page and the form layer is gone, no viewer can prefer anything
else. This is the recommended way to send one.

**Verified end to end** on the live `irs.gov/pub/irs-pdf/fw9.pdf`: hierarchical
names (`topmostSubform[0].Page1[0].f1_01[0]`) resolve, the classification
checkbox's on-state is **`/1`** (not `/Yes`), and a filled + flattened output
renders correctly with non-ASCII text intact.

For true dynamic XFA, in order of practicality: fill it in Acrobat by hand;
extract the XFA XML datasets, edit and re-inject; or **rasterise it and treat it
as a flat form** (`overlay.py`), which is usually fastest when the recipient
only needs a printed result.

---

<a id="doubled-text"></a>
## Doubled or offset text after flattening

**Symptom:** after flattening, some values appear twice with a slight offset.

**Cause:** the appearance was painted into the page *and* the form dictionary
survived, so some viewers draw the field as well.

**Fix:** remove `/AcroForm` too. qpdf documents this exact case: "after
flattening annotations … some viewers may show some field values twice with a
slight offset. In this situation it may help to remove the AcroForm entry."
`scripts/pdfform.py` always removes it; with `--qpdf`, pass `--remove-acroform`.

---

<a id="encrypted"></a>
## Encrypted and permission-restricted files

Two different cases:

- **Owner-restricted only** — the overwhelmingly common bank-statement case. The
  file opens with no password but forbids editing, because the *user* password
  is empty. `load()` handles this automatically; `qpdf --decrypt in out` also
  works with no password.
- **User-password protected** — you need the real password:
  `--password PASS`, or `qpdf --password=PASS --decrypt`.

`qpdf --requires-password` is an exit-code probe, useful for preflight. Note
`--remove-restrictions` invalidates digital signatures while leaving their
visual appearance — which can be misleading to a recipient, so say so if used.

This is for documents you are authorised to work with. qpdf's own manual states
the boundary: it "is not intended to be used for bypassing copyright
restrictions or other restrictions placed on files by their producers."

---

<a id="unicode"></a>
## Non-ASCII characters become `?`

**Symptom:** `Muñoz` comes out as `Mu?oz` after flattening.

**Cause:** qpdf's `--generate-appearances`. Its manual is explicit: characters
outside US-ASCII or a detected Windows-ANSI/MacRoman encoding "will be replaced
by the `?` character", auto-sized fonts become a fixed size, and "quadding is
ignored".

**Fix:** never let qpdf generate appearances. Generate them with pypdf (which
gained CID-font support in 2025-11 / 2026-05) or copy the existing streams,
which is what `--flatten` does here. `tests/smoke.sh` has a regression case that
fails if `ñ`/`Ü` do not survive a flatten.
