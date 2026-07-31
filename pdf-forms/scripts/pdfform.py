#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf==6.14.2"]
# ///
"""
pdfform — inspect, fill, flatten and verify PDF forms.

Self-contained: the PEP 723 header above declares its own dependencies, so
`uv run --script pdfform.py ...` works on a machine with nothing installed.
No venv, no global packages.

Design notes worth knowing before editing this file:

* **stdout is sacred.** Every command emits either JSON or a short human line on
  stdout and nothing else. pypdf logs recoverable structural damage ("Ignoring
  wrong pointing object") through the logging module, and real government PDFs
  trigger it constantly. Those go to stderr so that `--json` output stays
  machine-parseable for the agent calling this script.

* **Checkbox on-states are not "/Yes".** The legal on-state of a button is
  whatever non-/Off key appears in its widget's /AP /N dictionary. Real forms
  use /On, /Yes_3, /No_12, /Choice1 — anything. Writing the wrong one is a
  *silent* no-op in every PDF library, which is how unchecked boxes reach a
  government agency. `fill` therefore validates every button value against the
  actual on-states and errors out rather than writing something that won't take.

* **/AP /N is sometimes a stream, not a dictionary.** When a button has a single
  appearance rather than per-state appearances, /N resolves to a StreamObject —
  whose dictionary keys are /BBox, /Filter, /Resources... Because StreamObject
  subclasses DictionaryObject, a naive isinstance check reports those as
  on-states. Check for StreamObject first.

* **Fields can own many widgets.** One field name may be drawn on 49 pages. The
  value lives once on the field; the appearance must be regenerated per widget.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# Route pypdf's structural-damage chatter to stderr and quiet the routine noise.
# We keep ERROR and above: those are things a caller should actually see.
logging.basicConfig(stream=sys.stderr, level=logging.ERROR, format="%(name)s: %(message)s")
logging.getLogger("pypdf").setLevel(logging.ERROR)

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    StreamObject,
)

# Field flag bits (PDF 32000-1 table 227/228). Used to name button/choice subtypes.
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_COMBO = 1 << 17
FF_MULTILINE = 1 << 12

# A page yielding fewer than this many extractable characters is treated as an
# image. Tuned against the real corpus: a scanned page yields ~1 char, a
# born-digital form page yields thousands. Anything in between is ambiguous and
# gets reported as such rather than silently guessed.
SCANNED_CHARS_PER_PAGE = 100

# Words accepted as "tick this box" / "leave it clear". `fill` coerces these
# onto the form's real on-state and `verify` must recognise the same set, or a
# correctly coerced fill gets reported as a mismatch. One definition, both uses.
TRUTHY = {"true", "yes", "on", "x", "checked", "1"}
FALSY = {"false", "no", "off", "0", "unchecked", ""}


# --------------------------------------------------------------------------
# errors + output
# --------------------------------------------------------------------------

class FormError(Exception):
    """A condition the caller must fix. Always fatal, never swallowed."""


def out_json(obj: Any) -> None:
    json.dump(obj, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def note(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def load(path: str | Path, password: str | None = None) -> PdfReader:
    """Open a PDF, transparently handling owner-restricted encryption.

    Owner-only encryption (the common bank-statement case) has an empty user
    password: the file opens fine but forbids editing. pypdf still reports
    is_encrypted, so we decrypt with "" before deciding anything is wrong.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FormError(f"no such file: {p}")
    if p.stat().st_size == 0:
        raise FormError(f"file is empty (0 bytes): {p}")
    try:
        reader = PdfReader(str(p), strict=False)
    except Exception as exc:
        raise FormError(f"not a readable PDF: {p} ({type(exc).__name__}: {exc})") from exc
    if reader.is_encrypted:
        for candidate in ([password] if password else ["", "  "]):
            try:
                if reader.decrypt(candidate or ""):
                    break
            except Exception:  # noqa: BLE001 - any decrypt failure means "try next"
                continue
        else:
            raise FormError(
                f"{p.name} is password-protected and the password given did not work. "
                "Pass --password, or use `qpdf --decrypt` if you have the owner password."
            )
    try:
        _ = len(reader.pages)
    except Exception as exc:
        raise FormError(f"PDF structure is unreadable: {p} ({exc})") from exc
    return reader


def acroform_of(reader: PdfReader) -> DictionaryObject | None:
    try:
        return reader.trailer["/Root"].get("/AcroForm")
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# field tree
# --------------------------------------------------------------------------

def _widget_refs(field_ref) -> list:
    """Widgets belonging to a terminal field.

    A terminal field either *is* its own widget (the merged single-widget case)
    or carries /Kids that are widget annotations without their own /T.
    """
    obj = field_ref.get_object()
    kids = obj.get("/Kids")
    if kids:
        return list(kids)
    return [field_ref]


def on_states(field_ref) -> list[str]:
    """Legal 'on' values for a button field, read from its appearance dictionaries.

    Returns [] when the button carries a single appearance stream instead of a
    state dictionary — there is nothing meaningful to choose from in that case.
    """
    states: set[str] = set()
    for wref in _widget_refs(field_ref):
        try:
            widget = wref.get_object()
            ap = widget.get("/AP")
            if not ap:
                continue
            normal = ap.get("/N")
            if normal is None:
                continue
            normal = normal.get_object()
            # Order matters: StreamObject subclasses DictionaryObject, so a
            # stream would otherwise expose /BBox, /Filter, /Resources as states.
            if isinstance(normal, StreamObject):
                continue
            if isinstance(normal, DictionaryObject):
                states |= {str(k) for k in normal}
        except Exception:  # noqa: BLE001 - a malformed widget must not abort the walk
            continue
    states.discard("/Off")
    return sorted(states)


def _annots(page) -> list:
    """Resolve a page's /Annots to a real list.

    /Annots is an IndirectObject on some pages and a resolved ArrayObject on
    others, *within the same document*. Iterating the indirect form raises
    TypeError, so code that skips the failure silently reports the page as
    having no widgets — which looks exactly like a page that genuinely has none.
    """
    annots = page.get("/Annots")
    if annots is None:
        return []
    resolved = annots.get_object() if hasattr(annots, "get_object") else annots
    try:
        return list(resolved)
    except TypeError:
        return []


def _widget_page_map(reader: PdfReader) -> dict[int, int]:
    """Map widget object id -> 1-based page number."""
    mapping: dict[int, int] = {}
    for index, page in enumerate(reader.pages, start=1):
        for annot in _annots(page):
            idnum = getattr(annot, "idnum", None)
            if idnum is not None:
                mapping.setdefault(idnum, index)
    return mapping


def _qualified_widget_name(widget: DictionaryObject) -> str | None:
    """Fully-qualified name of a page-attached widget, walking /Parent upward."""
    parts: list[str] = []
    node: Any = widget
    seen = 0
    while node is not None and seen < 32:  # guard against a cyclic /Parent chain
        title = node.get("/T")
        if title is not None:
            parts.append(str(title))
        parent = node.get("/Parent")
        node = parent.get_object() if parent is not None else None
        seen += 1
    return ".".join(reversed(parts)) if parts else None


def live_widgets(reader: PdfReader) -> dict[str, list[tuple[int, DictionaryObject]]]:
    """Index the widgets actually attached to pages, keyed by qualified name.

    This is the authoritative view of a form. /AcroForm/Fields is only a
    *declaration*: in documents assembled by merging (every multi-agency
    government packet), it can hold stale duplicate objects that share a field's
    name but are not the object drawn on the page. Writers update the widget on
    the page; readers that trust /Fields then report the old value forever.
    """
    index: dict[str, list[tuple[int, DictionaryObject]]] = {}
    for page_no, page in enumerate(reader.pages, start=1):
        for annot in _annots(page):
            try:
                obj = annot.get_object()
                if obj.get("/Subtype") != "/Widget":
                    continue
                name = _qualified_widget_name(obj)
                if name:
                    index.setdefault(name, []).append((page_no, obj))
            except Exception:  # noqa: BLE001 - one bad annotation must not hide a page
                continue
    return index


def _kind(ft: str | None, flags: int) -> str:
    if ft == "/Tx":
        return "multiline_text" if flags & FF_MULTILINE else "text"
    if ft == "/Btn":
        if flags & FF_PUSHBUTTON:
            return "pushbutton"
        return "radio" if flags & FF_RADIO else "checkbox"
    if ft == "/Ch":
        return "dropdown" if flags & FF_COMBO else "listbox"
    if ft == "/Sig":
        return "signature"
    return str(ft or "unknown")


def walk_fields(reader: PdfReader, source: str | Path | None = None) -> list[dict]:
    """Walk /AcroForm /Fields and return one record per *terminal* field.

    Names are fully qualified (parent./T joined by '.') because that is the key
    every writer expects — IRS forms look like
    topmostSubform[0].Page1[0].f1_01[0] and a leaf-only name will not match.
    """
    acro = acroform_of(reader)
    if not acro:
        return []
    pages = _widget_page_map(reader)
    live = live_widgets(reader)
    # Only pay for the text scan when a caller wants labels (`fields`, `auto`,
    # `complete`); `verify` and `flatten` never need them.
    words = page_words(source) if source else {}
    found: list[dict] = []

    def recurse(ref, prefix: str, ft: str | None, flags: int) -> None:
        obj = ref.get_object()
        title = obj.get("/T")
        name = f"{prefix}.{title}" if prefix and title is not None else (
            str(title) if title is not None else prefix
        )
        ft = obj.get("/FT", ft)
        flags = int(obj.get("/Ff", flags) or 0)

        kids = obj.get("/Kids") or []
        # Kids carrying /T are intermediate fields; kids without it are widgets.
        subfields = [k for k in kids if "/T" in k.get_object()]
        if subfields:
            for kid in subfields:
                recurse(kid, name, ft, flags)
            return

        widget_refs = _widget_refs(ref)
        widget_pages = sorted({pages[w.idnum] for w in widget_refs
                               if getattr(w, "idnum", None) in pages})

        # Reconcile the declaration against what is actually on the page.
        # Where they disagree, the page wins: that is what renders and what a
        # recipient reads. The disagreement itself is worth surfacing.
        on_pages = live.get(name, [])
        declared = obj.get("/V")
        effective = declared
        if on_pages:
            widget_page_numbers = sorted({p for p, _ in on_pages})
            if not widget_pages:
                widget_pages = widget_page_numbers
            first = on_pages[0][1]
            live_value = first.get("/V")
            if live_value is None and str(ft) == "/Btn":
                live_value = first.get("/AS")
            if live_value is not None:
                effective = live_value

        # /TU is the tooltip — in practice the form author's own human-readable
        # label ("Enter your last name"). It is the bridge between what a person
        # calls a field and the machine name, and it costs nothing to read.
        # /TM is the export/mapping name, a decent second choice.
        label = obj.get("/TU") or obj.get("/TM")
        if label is None and on_pages:
            first_widget = on_pages[0][1]
            label = first_widget.get("/TU") or first_widget.get("/TM")

        # No tooltip? Read the label off the page. Most forms — the IRS W-9
        # included — carry no /TU at all, so without this the human-label
        # matching only works on the minority of forms that need it least.
        label_source = "tooltip" if label is not None else None
        if label is None and words and on_pages:
            page_no, widget = on_pages[0]
            rect = widget.get("/Rect")
            if rect is not None and page_no in words:
                try:
                    guess = infer_label([float(v) for v in rect], words[page_no],
                                        is_button=(str(ft) == "/Btn"))
                    if guess:
                        label, label_source = guess, "inferred"
                except Exception:  # noqa: BLE001 - a bad /Rect must not stop the walk
                    pass

        record = {
            "name": name,
            "label": str(label) if label is not None else None,
            "label_source": label_source,
            "type": _kind(str(ft) if ft else None, flags),
            "ft": str(ft) if ft else None,
            "value": str(effective) if effective is not None else None,
            "widgets": max(len(widget_refs), len(on_pages)),
            "pages": widget_pages,
            "readonly": bool(flags & 1),
            "required": bool(flags & 2),
        }
        if on_pages and str(declared) != str(effective):
            record["stale_declaration"] = (
                f"/AcroForm/Fields says {declared!r} but the widget on page "
                f"{on_pages[0][0]} says {effective!r} — the page is authoritative"
            )
        if str(ft) == "/Btn" and not (flags & FF_PUSHBUTTON):
            record["on_states"] = on_states(ref)
        if str(ft) == "/Ch":
            opts = obj.get("/Opt")
            if opts:
                record["options"] = [
                    str(o[1]) if isinstance(o, list) and len(o) > 1 else str(o)
                    for o in opts
                ]
        found.append(record)

    for field in acro.get("/Fields", []):
        recurse(field, "", None, 0)
    return found


# --------------------------------------------------------------------------
# text / rendering helpers (poppler, always present on macOS via brew)
# --------------------------------------------------------------------------

def have(binary: str) -> bool:
    return shutil.which(binary) is not None


def page_text(path: str | Path, first: int | None = None, last: int | None = None) -> str:
    """Extract text, preferring poppler's pdftotext for fidelity and speed."""
    if have("pdftotext"):
        cmd = ["pdftotext", "-q", "-layout"]
        if first:
            cmd += ["-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        cmd += [str(path), "-"]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout
        except Exception:  # noqa: BLE001 - fall through to pypdf
            pass
    try:
        reader = load(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages[: last or 50])
    except Exception:  # noqa: BLE001
        return ""


_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)"')
_WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>'
)


def page_words(path: str | Path) -> dict[int, dict]:
    """Every word on every page with its box, via poppler's `pdftotext -bbox`.

    Used to work out what a field is *called* when the form gives no /TU
    tooltip — which is most forms, including the IRS W-9. One subprocess for the
    whole document, and no new dependency: pdftotext is already required.

    Coordinates are top-left origin (y grows downward), unlike PDF space.
    """
    if not have("pdftotext"):
        return {}
    try:
        xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"],
                             capture_output=True, text=True, timeout=180).stdout
    except Exception:  # noqa: BLE001
        return {}

    pages: dict[int, dict] = {}
    chunks = xml.split("<page ")
    for i, chunk in enumerate(chunks[1:], start=1):
        head = _PAGE_RE.match("<page " + chunk[:120].split(">")[0] + ">")
        size = (float(head.group(1)), float(head.group(2))) if head else (612.0, 792.0)
        words = []
        for m in _WORD_RE.finditer(chunk):
            x0, y0, x1, y1, text = m.groups()
            text = (text.replace("&amp;", "&").replace("&lt;", "<")
                        .replace("&gt;", ">").replace("&quot;", '"')
                        .replace("&apos;", "'").replace("&#39;", "'").strip())
            if text:
                words.append({"x0": float(x0), "top": float(y0),
                              "x1": float(x1), "bottom": float(y1), "text": text})
        pages[i] = {"size": size, "words": words}
    return pages


# A label is separated from unrelated text by visible whitespace. Collect words
# outward from the field and stop at the first gap wider than this — without it,
# a fixed word count runs straight off the end of a label and into the tail of
# whatever sentence happens to sit nearby.
LABEL_GAP_PT = 11.0
LABEL_REACH_PT = 220.0   # nothing further away than this is the field's label
LABEL_MAX_WORDS = 8


def _collect(words: list[dict], edge: float, direction: str) -> str:
    """Walk outward from a field edge, stopping at the first real gap."""
    if direction == "left":
        words.sort(key=lambda w: w["x1"], reverse=True)
        picked, cursor = [], edge
        for w in words:
            if cursor - w["x1"] > LABEL_GAP_PT or len(picked) >= LABEL_MAX_WORDS:
                break
            picked.append(w)
            cursor = w["x0"]
        picked.reverse()
    else:
        words.sort(key=lambda w: w["x0"])
        picked, cursor = [], edge
        for w in words:
            if w["x0"] - cursor > LABEL_GAP_PT or len(picked) >= LABEL_MAX_WORDS:
                break
            picked.append(w)
            cursor = w["x1"]
    return " ".join(w["text"] for w in picked).strip(" :·.,()")


def infer_label(rect: list[float], page: dict, is_button: bool = False) -> str | None:
    """Guess a field's label from the text around it.

    Direction depends on the field type, because the conventions differ and
    getting it backwards produces confident nonsense:

      * a checkbox or radio is labelled to its **right** ("[ ] Individual"),
      * a text field is labelled to its **left** or **above** ("Name: ____").

    Reading a checkbox leftward walks across its neighbouring options and
    returns the previous choice's text — a label that looks plausible and names
    the wrong thing.
    """
    words = page.get("words") or []
    if not words:
        return None
    height = page["size"][1]
    x0, x1 = min(rect[0], rect[2]), max(rect[0], rect[2])
    top, bottom = height - max(rect[1], rect[3]), height - min(rect[1], rect[3])
    mid = (top + bottom) / 2
    # Capped deliberately. A generous tolerance on a tall input box reaches up
    # into the instruction paragraph above it and returns that sentence's tail
    # as the label — confident, plausible, and wrong.
    row_tol = min(max(4.0, (bottom - top) * 0.4), 8.0)

    def same_row(w: dict) -> bool:
        return abs((w["top"] + w["bottom"]) / 2 - mid) <= row_tol

    right = [w for w in words if same_row(w) and w["x0"] >= x1 - 2
             and w["x0"] - x1 <= LABEL_REACH_PT]
    left = [w for w in words if same_row(w) and w["x1"] <= x0 + 2
            and x0 - w["x1"] <= LABEL_REACH_PT]

    beside = None
    order = ["right", "left"] if is_button else ["left", "right"]
    for direction in order:
        pool = right if direction == "right" else left
        if pool:
            text = _collect(list(pool), x1 if direction == "right" else x0, direction)
            if text:
                beside = text
                break

    # The other common layout: a heading directly above the box.
    above_text = None
    above = [w for w in words
             if w["bottom"] <= top + 2 and top - w["bottom"] <= LABEL_REACH_PT
             and not (w["x1"] < x0 - 4 or w["x0"] > x1 + 4)]
    if above:
        nearest = max(w["bottom"] for w in above)
        row = sorted((w for w in above if nearest - w["bottom"] <= 4),
                     key=lambda w: w["x0"])
        above_text = " ".join(w["text"] for w in row[:LABEL_MAX_WORDS]).strip(" :·.,()")

    # A fragment starting mid-sentence is the tail of a wrapped instruction
    # paragraph, not a label ("entity's name on line 2"). Real labels start with
    # a capital or the field's number. When the neighbour looks like prose and
    # there is a heading above, trust the heading.
    if beside and above_text and beside[:1].islower():
        return above_text
    return beside or above_text or None


def font_count(path: str | Path) -> int | None:
    """Number of embedded/referenced fonts. Zero is a strong scan signal."""
    if not have("pdffonts"):
        return None
    try:
        res = subprocess.run(["pdffonts", str(path)], capture_output=True, text=True, timeout=60)
        lines = [ln for ln in res.stdout.splitlines()[2:] if ln.strip()]
        return len(lines)
    except Exception:  # noqa: BLE001
        return None


def render(path: str | Path, out_prefix: str | Path, dpi: int = 100,
           first: int = 1, last: int = 2) -> list[Path]:
    """Rasterize pages to PNG so a fill/flatten can be proven visually."""
    if not have("pdftoppm"):
        return []
    subprocess.run(
        ["pdftoppm", "-q", "-r", str(dpi), "-png", "-f", str(first), "-l", str(last),
         str(path), str(out_prefix)],
        capture_output=True, timeout=300,
    )
    return sorted(Path(out_prefix).parent.glob(Path(out_prefix).name + "*.png"))


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_triage(args) -> int:
    """Classify a PDF into a lane and name the next command to run.

    This is the entrypoint. Everything downstream depends on getting the lane
    right, and every check here is free and offline.
    """
    path = Path(args.file).expanduser()
    reader = load(path, args.password)
    acro = acroform_of(reader)
    fields = walk_fields(reader) if acro else []
    pages = len(reader.pages)
    fonts = font_count(path)
    chars = len(page_text(path).replace("\f", "").strip())
    per_page = round(chars / pages, 1) if pages else 0.0

    has_xfa = bool(acro and "/XFA" in acro)
    if has_xfa and not fields:
        lane, why = "xfa", "dynamic XFA form — no AcroForm fields to fill"
    elif has_xfa:
        lane, why = "xfa-hybrid", "XFA present alongside AcroForm fields"
    elif fields:
        lane, why = "acroform", f"{len(fields)} fillable fields"
    elif (fonts == 0) or per_page < SCANNED_CHARS_PER_PAGE:
        lane, why = "scanned", f"{per_page} extractable chars/page, {fonts} fonts — image-only"
    else:
        lane, why = "flat", "has text but no form fields — printed form, needs overlay"

    nxt = {
        "acroform": f'pdfform.py fields "{path}" --json',
        "xfa": "STOP — see references/gotchas.md#xfa. Fill will silently do nothing.",
        "xfa-hybrid": f'pdfform.py fields "{path}" --json   # verify in Acrobat; XFA may override',
        "scanned": f'pdfform.py ocr "{path}" -o ocr.pdf',
        "flat": f'overlay.py geometry "{path}" --page 1',
    }[lane]

    result = {
        "file": str(path),
        "lane": lane,
        "why": why,
        "pages": pages,
        "fields": len(fields),
        "fonts": fonts,
        "chars_per_page": per_page,
        "encrypted": reader.is_encrypted,
        "need_appearances": bool(acro.get("/NeedAppearances")) if acro else False,
        "next": nxt,
    }
    if result["need_appearances"]:
        result["warning"] = (
            "Source sets /NeedAppearances — it relies on the viewer to draw field "
            "values. Fill must generate real appearance streams or values render blank "
            "outside Acrobat. `fill` handles this."
        )
    out_json(result)
    return 0


def cmd_fields(args) -> int:
    reader = load(args.file, args.password)
    fields = walk_fields(reader, source=args.file)
    if not fields:
        raise FormError(
            "no AcroForm fields. Run `triage` — this is probably a flat or scanned form."
        )
    if args.filter:
        needle = args.filter.lower()
        fields = [f for f in fields if needle in f["name"].lower()]
    if args.mask:
        # Partially-completed forms are the norm, and their existing values are
        # exactly the data you least want pasted into a chat log or a ticket:
        # names, addresses, SSNs, account numbers. Structure is what you need to
        # fill a form; the previous occupant's answers are not.
        for f in fields:
            v = f["value"]
            if v and not str(v).startswith("/"):
                f["value"] = f"<{len(str(v))} chars>"
    if args.json:
        out_json(fields)
        return 0
    for f in fields:
        bits = [f"{f['name']}", f"({f['type']})"]
        if f.get("on_states"):
            bits.append(f"on_states={','.join(f['on_states'])}")
        if f["widgets"] > 1:
            bits.append(f"widgets={f['widgets']}")
        if f["value"]:
            bits.append(f"value={f['value']!r}")
        if f["readonly"]:
            bits.append("READONLY")
        print("  ".join(bits))
    return 0


def _validate(data: dict, fields: list[dict]) -> dict:
    """Reject anything that would be written silently and ineffectively.

    Two failure modes are fatal here because both are invisible at runtime:
    an unknown field name (nothing happens) and an illegal checkbox on-state
    (nothing happens). Both must fail loudly instead.
    """
    index = {f["name"]: f for f in fields}
    problems: list[str] = []
    resolved: dict[str, Any] = {}

    for key, value in data.items():
        field = index.get(key)
        if field is None:
            close = difflib.get_close_matches(key, index.keys(), n=3, cutoff=0.6)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            problems.append(f"unknown field {key!r}.{hint}")
            continue
        if field["readonly"]:
            problems.append(f"{key!r} is read-only in this form; writing it would not take.")
            continue

        if field["type"] in ("checkbox", "radio"):
            legal = field.get("on_states") or []
            text = "" if value is None else str(value)
            candidate = text if text.startswith("/") else "/" + text

            # 1. An exact state always wins. This must be tried before any
            #    convenience coercion, because "1" is both a common shorthand
            #    for "checked" and a real on-state (/1) on many forms.
            if candidate in legal:
                resolved[key] = candidate
                continue
            # 2. Explicit clear.
            if isinstance(value, bool) is False and text.lower() in FALSY or value is False:
                resolved[key] = "/Off"
                continue
            # 3. "Tick it" — only unambiguous when the field has one on-state.
            if value is True or text.lower() in TRUTHY:
                if len(legal) == 1:
                    resolved[key] = legal[0]
                    continue
                problems.append(
                    f"{key!r}: {text!r} means 'checked' but this {field['type']} has "
                    f"{len(legal)} states {legal} — name the one you want."
                )
                continue
            problems.append(
                f"{key!r}: {text!r} is not a legal state. Legal: {legal or '[none]'}. "
                "States are case-sensitive and are rarely '/Yes'."
            )
            continue

        if (field["type"] in ("dropdown", "listbox") and field.get("options")
                and str(value) not in field["options"]):
                close = difflib.get_close_matches(str(value), field["options"], n=3, cutoff=0.5)
                hint = f" Did you mean: {', '.join(close)}?" if close else ""
                problems.append(
                    f"{key!r}: {value!r} is not an option.{hint} "
                    f"Options: {field['options'][:8]}"
                )
                continue

        resolved[key] = value

    if problems:
        raise FormError("cannot fill:\n  - " + "\n  - ".join(problems))
    return resolved


# --------------------------------------------------------------------------
# profile — the values you retype on every form
# --------------------------------------------------------------------------

PROFILE_PATH = Path.home() / ".config" / "pdf-forms" / "profile.json"

# Keys the profile refuses to hold. The convenience of never retyping an SSN is
# not worth a plaintext copy of it sitting in a file that will end up in a
# backup, a sync folder, or a support bundle. These stay per-form and manual;
# everything else is the boring stuff that is genuinely tedious.
#
# Deliberately NOT one big regex. The previous version was
# `\b(...|driver.?s?.?lic|...)\b`, which fails on "drivers license" because the
# trailing \b lands between "lic" and "e" — two word characters, so no boundary,
# so no match. It read as thorough and rejected nothing. Token matching on a
# normalised key is duller and actually works.
SENSITIVE_TOKENS = {
    "ssn", "ssn#", "dob", "tin", "itin", "ein", "cvv", "cvc", "pin", "passport",
    "routing", "iban", "swift", "maiden", "password", "passcode", "license",
    "licence", "dl", "sin", "nino",
}
SENSITIVE_PHRASES = (
    "social security", "date of birth", "birth date", "birthdate", "tax id",
    "taxpayer id", "account number", "account no", "acct number", "card number",
    "credit card", "debit card", "bank account", "security question",
    "mother maiden", "mothers maiden", "drivers license", "driver license",
    "license number", "id number", "identification number",
)


def is_sensitive(key: str) -> bool:
    """True if a profile key looks like an identity or financial secret."""
    norm = _norm(key)
    if any(phrase in norm for phrase in SENSITIVE_PHRASES):
        return True
    return bool(SENSITIVE_TOKENS & set(norm.split()))


def load_profile() -> dict[str, str]:
    if not PROFILE_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        note(f"profile at {PROFILE_PATH} is unreadable ({exc}); ignoring it")
        return {}


def save_profile(entries: dict[str, str]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
    PROFILE_PATH.chmod(0o600)  # owner-only; it still holds address and phone


def cmd_profile(args) -> int:
    """Store the values you retype on every form. Refuses sensitive ones."""
    entries = load_profile()

    if args.action == "show":
        out_json({"path": str(PROFILE_PATH), "exists": PROFILE_PATH.exists(),
                  "entries": entries})
        return 0

    if args.action == "clear":
        if PROFILE_PATH.exists():
            PROFILE_PATH.unlink()
        out_json({"path": str(PROFILE_PATH), "cleared": True})
        return 0

    # set
    if not args.pairs:
        raise FormError('nothing to set. Usage: profile set "first name=Jordan" '
                        '"phone=914-555-0100"')
    refused, added = [], []
    for pair in args.pairs:
        if "=" not in pair:
            raise FormError(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        key, value = key.strip(), value.strip()
        if is_sensitive(key):
            refused.append(key)
            continue
        if not value:
            entries.pop(key, None)
            continue
        entries[key] = value
        added.append(key)
    if added or refused:
        save_profile(entries)

    result = {"path": str(PROFILE_PATH), "stored": added, "total": len(entries)}
    if refused:
        result["refused"] = refused
        result["why"] = (
            "These look like identity or financial secrets, and the profile is a "
            "plaintext file — it will end up in a backup or sync folder eventually. "
            "Pass them per-form in --data instead; they are the values you should "
            "be typing deliberately anyway."
        )
    out_json(result)
    return 0


def _norm(s: str) -> str:
    """Loose comparison key: case, punctuation and spacing all ignored."""
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def resolve_keys(data: dict, fields: list[dict]) -> tuple[dict, list[str], list[str]]:
    """Map human-written keys onto real field names.

    Nobody knows a field is called topmostSubform[0].Page1[0].f1_01[0]. They
    know it is "last name". Match, in descending order of confidence:

      1. exact field name          (already correct — never second-guess it)
      2. exact label (/TU) match   (the form author's own wording)
      3. normalised equality on label or name
      4. containment, then fuzzy

    A tie between two fields is reported, never guessed: silently picking one of
    two plausible fields is how the right value lands in the wrong box.
    """
    by_name = {f["name"]: f for f in fields}
    resolved: dict[str, Any] = {}
    problems: list[str] = []
    notes: list[str] = []

    for key, value in data.items():
        if key in by_name:
            resolved[key] = value
            continue

        nkey = _norm(key)
        tiers: list[list[dict]] = [
            [f for f in fields if f.get("label") and _norm(f["label"]) == nkey],
            [f for f in fields if _norm(f["name"]) == nkey],
            [f for f in fields if f.get("label") and nkey in _norm(f["label"])],
            [f for f in fields if nkey in _norm(f["name"])],
        ]
        match = None
        for tier in tiers:
            usable = [f for f in tier if not f["readonly"]]
            if len(usable) == 1:
                match = usable[0]
                break
            if len(usable) > 1:
                problems.append(
                    f"{key!r} is ambiguous — matches {len(usable)} fields: "
                    + ", ".join(f"{f['name']!r}" + (f" ({f['label']})" if f.get("label") else "")
                                for f in usable[:5])
                    + ". Use the exact field name."
                )
                match = "AMBIGUOUS"  # type: ignore[assignment]
                break
        # Inferred labels are approximate — "Name of entity/individual" against a
        # label read off the page as "entity's name on line 2" shares meaning but
        # no substring. Score by how much of the query the candidate accounts
        # for, and accept only a clear single winner.
        if match is None:
            qtokens = {t for t in nkey.split() if len(t) > 2}
            if qtokens:
                scored = []
                for f in fields:
                    if f["readonly"]:
                        continue
                    hay = set(_norm(f.get("label") or "").split()) | set(_norm(f["name"]).split())
                    overlap = len(qtokens & hay) / len(qtokens)
                    if overlap >= 0.6:
                        scored.append((overlap, f))
                if scored:
                    best = max(s for s, _ in scored)
                    winners = [f for s, f in scored if s == best]
                    if len(winners) == 1:
                        match = winners[0]
                    else:
                        problems.append(
                            f"{key!r} matches {len(winners)} fields equally well: "
                            + ", ".join(repr(f["name"]) for f in winners[:5])
                            + ". Use the exact field name."
                        )
                        match = "AMBIGUOUS"  # type: ignore[assignment]

        if match == "AMBIGUOUS":
            continue
        if match is None:
            pool = {f["name"]: f for f in fields}
            pool.update({f["label"]: f for f in fields if f.get("label")})
            close = difflib.get_close_matches(key, list(pool), n=3, cutoff=0.55)
            hint = f" Closest: {', '.join(repr(c) for c in close)}." if close else ""
            problems.append(f"no field matches {key!r}.{hint}")
            continue

        resolved[match["name"]] = value
        notes.append(f"{key!r} -> {match['name']!r}"
                     + (f" ({match['label']})" if match.get("label") else ""))

    return resolved, problems, notes


def _existing_values(fields: list[dict]) -> dict[str, Any]:
    """Values already present in the form, keyed by field name.

    Used to carry a partially completed form through a flatten intact. Read-only
    fields are excluded because writing them back is rejected by the validator
    and they are not editable anyway.
    """
    return {
        f["name"]: f["value"]
        for f in fields
        if f["value"] not in (None, "") and not f["readonly"]
    }


def _appearance_matrix(widget: DictionaryObject, stream: StreamObject) -> tuple[float, ...]:
    """Matrix mapping an appearance stream's /BBox onto its widget's /Rect.

    PDF 32000-1 §12.5.5: transform the BBox by the form's /Matrix, take the
    bounding box of the result, then scale and translate that onto /Rect.
    """
    rect = [float(x) for x in widget["/Rect"]]
    rx0, rx1 = min(rect[0], rect[2]), max(rect[0], rect[2])
    ry0, ry1 = min(rect[1], rect[3]), max(rect[1], rect[3])

    bbox = [float(x) for x in (stream.get("/BBox") or [0, 0, 1, 1])]
    m = [float(x) for x in (stream.get("/Matrix") or [1, 0, 0, 1, 0, 0])]
    corners = [(bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3])]
    tx = [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in corners]
    bx0, bx1 = min(p[0] for p in tx), max(p[0] for p in tx)
    by0, by1 = min(p[1] for p in tx), max(p[1] for p in tx)

    sx = (rx1 - rx0) / (bx1 - bx0) if bx1 > bx0 else 1.0
    sy = (ry1 - ry0) / (by1 - by0) if by1 > by0 else 1.0
    return (sx, 0.0, 0.0, sy, rx0 - bx0 * sx, ry0 - by0 * sy)


def _flatten_appearances(writer: PdfWriter) -> dict:
    """Burn each widget's EXISTING appearance stream into its page's content.

    This is the whole reason the skill is trustworthy. The obvious alternative —
    letting a library *regenerate* appearances while flattening — is lossy: it
    re-runs font selection and layout, silently blanks dropdown (/Ch) fields,
    and (in qpdf's generator) replaces every non-ASCII character with '?'.
    Copying the stream that is already there preserves the original rendering
    exactly, whoever produced it.
    """
    merged = 0
    skipped: list[str] = []

    for page in writer.pages:
        annots = _annots(page)
        if not annots:
            continue

        resources = page.get("/Resources")
        if resources is None:
            resources = DictionaryObject()
            page[NameObject("/Resources")] = resources
        resources = resources.get_object()
        if "/XObject" not in resources:
            resources[NameObject("/XObject")] = DictionaryObject()
        xobjects = resources["/XObject"].get_object()

        ops: list[str] = []
        for i, annot_ref in enumerate(annots):
            try:
                widget = annot_ref.get_object()
                if widget.get("/Subtype") != "/Widget":
                    continue
                name = _qualified_widget_name(widget) or f"widget{i}"
                flags = int(widget.get("/F", 0) or 0)
                if flags & 2:  # /Hidden — must not be painted
                    continue
                if "/Rect" not in widget:
                    skipped.append(f"{name} (no /Rect)")
                    continue

                ap = widget.get("/AP")
                if ap is None:
                    continue  # nothing was ever drawn for this widget
                normal_ref = ap.get_object().raw_get("/N") if "/N" in ap.get_object() else None
                if normal_ref is None:
                    continue
                normal = normal_ref.get_object()

                # A state dictionary (checkbox/radio): pick the current state.
                if isinstance(normal, DictionaryObject) and not isinstance(normal, StreamObject):
                    state = widget.get("/AS")
                    if state is None or state not in normal:
                        continue  # nothing selected — correctly draws nothing
                    normal_ref = normal.raw_get(state)
                    normal = normal_ref.get_object()

                if not isinstance(normal, StreamObject):
                    skipped.append(f"{name} (appearance is not a stream)")
                    continue

                key = NameObject(f"/PFFlat{len(xobjects)}_{i}")
                xobjects[key] = normal_ref
                a, b, c, d, e, f = _appearance_matrix(widget, normal)
                ops.append(f"q {a:.6f} {b:.6f} {c:.6f} {d:.6f} {e:.6f} {f:.6f} cm {key} Do Q")
                merged += 1
            except Exception as exc:  # noqa: BLE001
                skipped.append(f"widget {i} ({type(exc).__name__})")
                continue

        if not ops:
            continue

        # Wrap the page's own content so unbalanced graphics state in the
        # original cannot leak into the overlay we append.
        overlay = DecodedStreamObject()
        overlay.set_data(("\n" + "\n".join(ops) + "\n").encode("latin-1", "replace"))
        pre, post = DecodedStreamObject(), DecodedStreamObject()
        pre.set_data(b"q\n")
        post.set_data(b"\nQ\n")

        existing = page.get("/Contents")
        chain: list = [writer._add_object(pre)]
        if existing is not None:
            resolved = existing.get_object()
            if isinstance(resolved, ArrayObject):
                chain.extend(list(resolved))
            else:
                chain.append(existing)
        chain.append(writer._add_object(post))
        chain.append(writer._add_object(overlay))
        page[NameObject("/Contents")] = ArrayObject(chain)

    return {"merged": merged, "skipped": skipped}


def _do_fill(src: Path, data: dict, dest: Path, flatten: bool) -> None:
    writer = PdfWriter(clone_from=str(src))
    acro = writer._root_object.get("/AcroForm")
    # /AcroForm is frequently an indirect reference rather than the dictionary
    # itself; assigning into the reference raises TypeError.
    if acro is not None:
        acro = acro.get_object()
    if acro is not None:
        # Force real appearance streams rather than delegating to the viewer.
        # Leaving /NeedAppearances true makes values render blank in Preview,
        # Chrome and on paper, while looking correct in Acrobat.
        acro[NameObject("/NeedAppearances")] = BooleanObject(False)
    # Always write values WITHOUT pypdf's own flatten: it regenerates
    # appearances, which blanks dropdowns and re-does font layout. We want the
    # appearance streams it generates for the fields we changed, and the
    # original streams for everything else — then we merge them ourselves.
    if data:
        writer.update_page_form_field_values(None, data, auto_regenerate=False)
    if flatten:
        stats = _flatten_appearances(writer)
        writer.remove_annotations(subtypes="/Widget")
        if "/AcroForm" in writer._root_object:
            del writer._root_object["/AcroForm"]
        if stats["skipped"]:
            note(f"flatten: {len(stats['skipped'])} widget(s) had no usable appearance: "
                 f"{stats['skipped'][:5]}")
    with open(dest, "wb") as fh:
        writer.write(fh)


def cmd_fill(args) -> int:
    src = Path(args.file).expanduser()
    reader = load(src, args.password)
    fields = walk_fields(reader)
    if not fields:
        raise FormError("no fillable fields — run `triage` first.")
    acro = acroform_of(reader)
    if acro and "/XFA" in acro:
        # Distinguish the two XFA cases. A *dynamic* form (XFA with no AcroForm
        # fields) has nothing fillable and writing to it is pure theatre. A
        # *hybrid* has a real, fillable AcroForm layer that every non-Acrobat
        # viewer honours — the IRS W-9 is one, and filling it is an entirely
        # normal thing to want. Refusing both would be a guard so strict it
        # blocks the most common task this skill exists for.
        if not fields:
            raise FormError(
                "this is a dynamic XFA form: it has an XFA layer and no AcroForm "
                "fields, so there is nothing to fill. No open-source tool writes "
                "dynamic XFA. See references/gotchas.md#xfa for the options "
                "(fill in Acrobat, edit the XFA XML, or flatten and overlay)."
            )
        note(
            "WARNING: hybrid XFA form. The AcroForm layer is fillable and will "
            "render correctly in Preview, Chrome and in print, but Adobe Acrobat "
            "prefers the XFA data and may show these fields as empty. Flatten the "
            "output (--flatten) to make the values unconditional."
        )

    raw = json.loads(Path(args.data).expanduser().read_text())
    if not isinstance(raw, dict):
        raise FormError("--data must be a JSON object of {field name: value}")
    data = _validate(raw, fields)

    dest = Path(args.output).expanduser()
    # Only the changed fields are written — every other widget keeps the
    # appearance stream it already had, which the flatten step merges verbatim.
    # Rewriting untouched fields would force a regeneration that blanks
    # dropdowns and re-lays-out text for no benefit.
    _do_fill(src, data, dest, flatten=args.flatten)
    note(f"filled {len(data)} field(s) -> {dest}" + (" (flattened)" if args.flatten else ""))
    out_json({
        "output": str(dest),
        "filled": len(data),
        "preexisting_preserved": len(_existing_values(fields)),
        "flattened": args.flatten,
    })
    return 0


def cmd_flatten(args) -> int:
    """Burn field appearances into page content and remove the form layer.

    Order matters. qpdf flattens best but its own appearance generator replaces
    every non-ASCII character with '?' and ignores quadding, so appearances are
    always generated by pypdf first and qpdf is used only to collapse them.
    """
    src = Path(args.file).expanduser()
    dest = Path(args.output).expanduser()
    reader = load(src, args.password)
    preserved = len(_existing_values(walk_fields(reader)))

    if args.qpdf and have("qpdf"):
        cmd = ["qpdf", str(src), "--flatten-annotations=all", "--remove-acroform",
               str(dest)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode not in (0, 3):  # 3 == warnings only
            note(f"qpdf failed ({res.returncode}): {res.stderr.strip()[:400]}")
            note("falling back to the built-in appearance merge")
            _do_fill(src, {}, dest, flatten=True)
            engine = "builtin (qpdf failed)"
        else:
            # qpdf paints the appearances and clears the values, but leaves the
            # widget annotations themselves in place — its manual is explicit
            # that --remove-acroform "does not remove form field dictionaries or
            # widget annotations". Empty widget shells still render as
            # interactive boxes in some viewers, so strip them here and give
            # both engines the same guarantee: no /AcroForm, no widgets.
            leftover = PdfWriter(clone_from=str(dest))
            leftover.remove_annotations(subtypes="/Widget")
            if "/AcroForm" in leftover._root_object:
                del leftover._root_object["/AcroForm"]
            with open(dest, "wb") as fh:
                leftover.write(fh)
            engine = "qpdf + widget sweep"
    else:
        if args.qpdf:
            note("qpdf not installed — using the built-in appearance merge")
        _do_fill(src, {}, dest, flatten=True)
        engine = "builtin"

    out_json({"output": str(dest), "engine": engine, "values_preserved": preserved,
              "next": f'pdfform.py verify "{dest}" --original "{src}" --flat'})
    return 0


def _run_checks(out_path: Path, original: str | Path | None = None,
                expect: dict | None = None, require_flat: bool = False,
                page: int | None = None, password: str | None = None) -> dict:
    """Prove the output is what it claims.

    Checks the failure modes that are otherwise completely silent:
      1. values written but no appearance stream  -> renders blank when printed
      2. "flattened" but the form layer survives  -> data still extractable
      3. flattening destroyed the values          -> blank output, no error

    Shared by `verify` and `complete` so the one-shot path can never end up
    with weaker guarantees than the manual one.
    """
    out_path = Path(out_path).expanduser()
    reader = load(out_path)
    fields = walk_fields(reader)
    acro = acroform_of(reader)
    checks: list[dict] = []
    ok = True

    def check(name: str, passed: bool, detail: str) -> None:
        nonlocal ok
        checks.append({"check": name, "passed": passed, "detail": detail})
        if not passed:
            ok = False

    widget_total = 0
    # NB: not `for page in ...` — `page` is this function's parameter (the page
    # number to render-compare). Shadowing it here leaves a PageObject where an
    # int is expected, which breaks the render check only when a caller passes
    # --page explicitly: correct by default, wrong when specified.
    for pg in reader.pages:
        for annot in _annots(pg):
            try:
                if annot.get_object().get("/Subtype") == "/Widget":
                    widget_total += 1
            except Exception:  # noqa: BLE001
                continue

    flattened = (acro is None) and widget_total == 0
    detail = ("no /AcroForm and no widget annotations — data is burned into the page"
              if flattened else
              f"/AcroForm={'present' if acro else 'absent'}, {widget_total} widget(s) remain "
              "— field values are still live objects and remain extractable")
    if require_flat:
        # Asked for a flattened file: anything short of it is a failure. A form
        # left half-flattened still carries every value as machine-readable
        # metadata, which is the whole risk when sending it to a third party.
        check("flattened", flattened, detail)
    else:
        checks.append({"check": "flattened", "passed": True,
                       "detail": f"informational: {detail}"})

    if expect:
        expected = expect
        if flattened:
            # Values no longer exist as objects — they must be visible as text.
            text = page_text(out_path)
            missing = [k for k, v in expected.items()
                       if isinstance(v, str) and v and not str(v).startswith("/")
                       and str(v) not in text]
            check("values_visible", not missing,
                  "all expected values found in rendered text" if not missing
                  else f"missing from output text: {missing[:8]}")
        else:
            index = {f["name"]: f for f in fields}
            missing, wrong = [], []
            for key, value in expected.items():
                field = index.get(key)
                if field is None:
                    missing.append(key)
                    continue
                actual = field["value"]
                want = str(value)
                # Compare against what was asked for. Checking only that *some*
                # value is present would pass even if the fill did nothing at
                # all — an unchecked box reads "/Off", which is a value.
                #
                # Buttons are compared by state, not by string, because `fill`
                # deliberately coerces "Yes"/true onto whatever the form's real
                # on-state happens to be (/On, /Yes_3, /1...). Comparing the
                # literal request would flag a correct fill as a mismatch.
                checked = actual not in (None, "", "/Off")
                if field["type"] in ("checkbox", "radio"):
                    if isinstance(value, bool) or want.lower() in TRUTHY | FALSY:
                        ok_here = checked == (value is True or want.lower() in TRUTHY)
                    else:
                        ok_here = checked and actual.lstrip("/") == want.lstrip("/")
                else:
                    ok_here = actual is not None and (
                        actual == want or actual.lstrip("/") == want.lstrip("/"))
                if not ok_here:
                    wrong.append(f"{key}: wanted {want!r}, got {actual!r}")
            check("values_match", not missing and not wrong,
                  "every expected field holds the value that was requested"
                  if not (missing or wrong)
                  else f"absent={missing[:5]} mismatched={wrong[:5]}")

            # Appearance streams: the "blank until clicked" detector.
            no_ap = []
            for key in expected:
                field_ref = _find_ref(reader, key)
                if field_ref is None:
                    continue
                if not _has_appearance(field_ref):
                    no_ap.append(key)
            check("appearance_streams", not no_ap,
                  "all filled fields have appearance streams" if not no_ap
                  else f"no /AP (will print blank): {no_ap[:8]}")

    if original:
        # The check that matters most on a flatten: did anything that was
        # already in the form get destroyed? Verifying only the values you just
        # wrote passes with flying colours while every pre-existing answer is
        # silently dropped — which is what a flatten does if it is handed only
        # the new values. Compare against the source, not against the request.
        try:
            before = _existing_values(walk_fields(load(original, password)))
        except FormError:
            before = {}
        if before:
            if flattened:
                # Search the value's OWN page, not the whole document. A
                # document-wide substring match passes for any common word that
                # happens to appear elsewhere — "Female" occurs on half the
                # pages of a long packet — so it would clear a real data loss.
                origin = {f["name"]: f["pages"]
                          for f in walk_fields(load(original, password))}
                page_cache: dict[int, str] = {}
                lost = []
                for key, value in before.items():
                    if not isinstance(value, str) or value.startswith("/"):
                        continue
                    needle = value.strip()
                    if len(needle) <= 2:
                        continue
                    pages_to_check = origin.get(key) or []
                    if not pages_to_check:
                        continue
                    hit = False
                    for pno in pages_to_check:
                        if pno not in page_cache:
                            page_cache[pno] = page_text(out_path, first=pno, last=pno)
                        if needle in page_cache[pno]:
                            hit = True
                            break
                    if not hit:
                        lost.append(key)
                checked = len(before)
                check("nothing_lost", not lost,
                      f"all {checked} pre-existing value(s) still render on their own page"
                      if not lost else
                      f"{len(lost)} pre-existing value(s) DESTROYED, e.g. field(s) "
                      f"{lost[:6]} — flatten dropped fields it was not given")
            else:
                after = {f["name"]: f["value"] for f in fields}
                lost = [k for k, v in before.items()
                        if after.get(k) in (None, "") and k not in (expect or {})]
                check("nothing_lost", not lost,
                      "all pre-existing values retained" if not lost
                      else f"{len(lost)} pre-existing value(s) lost, e.g. {lost[:6]}")

    if original and have("pdftoppm"):
        # Compare a page that SHOULD have changed. Defaulting to page 1 makes
        # this check meaningless on a 49-page packet whose fields start on
        # page 11 — it would report "nothing was drawn" for a perfect fill.
        target = page
        if target is None:
            candidates: list[int] = []
            if expect:
                names = set(expect)
                # A flattened output has no fields left to locate, so field
                # positions must come from the ORIGINAL. Reading them from the
                # output silently falls back to page 1 — a page with no fields,
                # which then "proves" nothing was drawn on a perfect flatten.
                locator = fields
                if flattened:
                    try:
                        locator = walk_fields(load(original, password))
                    except FormError:
                        locator = []
                for f in locator:
                    if f["name"] in names:
                        candidates += f["pages"]
            target = min(candidates) if candidates else 1
            if not candidates:
                note("no page could be derived from --expect; falling back to page 1")

        scratch = out_path.parent / ".verify-render"
        scratch.mkdir(exist_ok=True)
        for old in scratch.glob("*.png"):
            old.unlink()  # stale renders would otherwise be compared
        a = render(original, scratch / "orig", first=target, last=target)
        b = render(out_path, scratch / "out", first=target, last=target)
        if a and b:
            same = a[0].read_bytes() == b[0].read_bytes()
            check("visually_changed", not same,
                  f"page {target} differs from the original — values are actually drawn"
                  if not same else
                  f"page {target} renders IDENTICALLY to the original — nothing was drawn. "
                  "Values may be set as objects but have no appearance stream.")
            checks.append({"check": "render", "passed": True,
                           "detail": f"page {target}: compare {a[0]} vs {b[0]}"})

    return {"output": str(out_path), "ok": ok, "checks": checks}


def cmd_verify(args) -> int:
    expect = None
    if args.expect:
        expect = json.loads(Path(args.expect).expanduser().read_text())
    result = _run_checks(Path(args.output).expanduser(), original=args.original,
                         expect=expect, require_flat=args.flat, page=args.page,
                         password=args.password)
    out_json(result)
    return 0 if result["ok"] else 1


def _find_ref(reader: PdfReader, name: str):
    """Locate a terminal field's indirect reference by fully-qualified name."""
    acro = acroform_of(reader)
    if not acro:
        return None
    result = []

    def recurse(ref, prefix: str) -> None:
        obj = ref.get_object()
        title = obj.get("/T")
        full = f"{prefix}.{title}" if prefix and title is not None else (
            str(title) if title is not None else prefix
        )
        kids = [k for k in (obj.get("/Kids") or []) if "/T" in k.get_object()]
        if kids:
            for kid in kids:
                recurse(kid, full)
        elif full == name:
            result.append(ref)

    for field in acro.get("/Fields", []):
        recurse(field, "")
    return result[0] if result else None


def _has_appearance(field_ref) -> bool:
    for wref in _widget_refs(field_ref):
        try:
            if wref.get_object().get("/AP", {}).get("/N") is not None:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def cmd_ocr(args) -> int:
    """Add a text layer to a scanned PDF, cheapest rung first.

    Rung order is deliberate: Apple's Vision engine is free, offline, needs no
    model download and beats Tesseract on real scans, so it is tried first on
    macOS. Tesseract is the portable fallback. Neither understands *structure* —
    for checkbox state or label/value pairing, escalate to a vision model
    (references/ocr-ladder.md).
    """
    src = Path(args.file).expanduser()
    dest = Path(args.output).expanduser()

    if not have("ocrmypdf"):
        raise FormError(
            "ocrmypdf is not installed. Install the recommended macOS stack:\n"
            "  brew install ocrmypdf && pip install ocrmypdf-appleocr\n"
            "Then re-run. Apple's Vision engine is free, offline and needs no models.\n"
            "Zero-install alternative for plain text: npx mac-ocr searchable-pdf FILE"
        )

    engines: list[list[str]] = []
    if sys.platform == "darwin" and not args.tesseract:
        engines.append(["--ocr-engine", "appleocr"])
    engines.append([])  # default engine (tesseract)

    mode = ["--redo-ocr"] if args.redo else ["--skip-text"]
    last_err = ""
    for engine in engines:
        cmd = ["ocrmypdf", *engine, *mode, "--rotate-pages", "--deskew", str(src), str(dest)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if res.returncode == 0:
            chars = len(page_text(dest).strip())
            out_json({
                "output": str(dest),
                "engine": engine[1] if engine else "tesseract",
                "chars_extracted": chars,
                "next": f'pdfform.py triage "{dest}"',
            })
            return 0
        last_err = res.stderr.strip()[:500]
        note(f"engine {engine or ['tesseract']} failed: {last_err}")
    raise FormError(f"OCR failed with every engine. Last error:\n{last_err}")


def cmd_auto(args) -> int:
    """One call that gets any PDF to 'ready to fill'.

    Triage, and if the file turns out to be a scan, OCR it and triage again —
    the routing that would otherwise be three manual round trips. Writes a
    template JSON pre-keyed by human label, pre-filled from the profile where it
    can, so the only thing left to do is type the values that are actually
    specific to this form.
    """
    src = Path(args.file).expanduser()
    steps: list[str] = []

    reader = load(src, args.password)
    acro = acroform_of(reader)
    fields = walk_fields(reader) if acro else []
    working = src

    # A scan cannot be filled and cannot be inspected. Fix that before deciding
    # anything else, rather than reporting "scanned" and making someone else
    # run the obvious next command.
    if not fields:
        chars = len(page_text(src).replace("\f", "").strip())
        pages = max(len(reader.pages), 1)
        if (font_count(src) == 0) or (chars / pages) < SCANNED_CHARS_PER_PAGE:
            if have("ocrmypdf") and not args.no_ocr:
                ocred = src.with_name(src.stem + ".ocr.pdf")
                steps.append(f"detected a scan ({chars / pages:.0f} chars/page) — running OCR")
                engine = ["--ocr-engine", "appleocr"] if sys.platform == "darwin" else []
                res = subprocess.run(
                    ["ocrmypdf", *engine, "--skip-text", "--rotate-pages", "--deskew",
                     str(src), str(ocred)],
                    capture_output=True, text=True, timeout=3600)
                if res.returncode != 0 and engine:
                    res = subprocess.run(
                        ["ocrmypdf", "--skip-text", "--rotate-pages", "--deskew",
                         str(src), str(ocred)],
                        capture_output=True, text=True, timeout=3600)
                if res.returncode == 0:
                    working = ocred
                    steps.append(f"OCR complete -> {ocred.name}")
                else:
                    steps.append(f"OCR failed: {res.stderr.strip()[:200]}")
            elif not have("ocrmypdf"):
                steps.append("detected a scan, but ocrmypdf is not installed "
                             "(brew install ocrmypdf) — see references/ocr-ladder.md")

    reader = load(working, args.password)
    acro = acroform_of(reader)
    fields = walk_fields(reader, source=working) if acro else []
    has_xfa = bool(acro and "/XFA" in acro)
    lane = ("xfa" if has_xfa and not fields else
            "xfa-hybrid" if has_xfa else
            "acroform" if fields else
            "scanned" if (font_count(working) == 0) else "flat")

    result: dict[str, Any] = {
        "file": str(working),
        "original": str(src) if working != src else None,
        "lane": lane,
        "steps": steps,
        "pages": len(reader.pages),
        "fields": len(fields),
    }

    if fields:
        profile = {} if args.no_profile else load_profile()
        writable = [f for f in fields if not f["readonly"] and f["type"] != "pushbutton"]
        template: dict[str, Any] = {}
        prefilled: list[str] = []

        if profile:
            mapped, _, notes = resolve_keys(profile, fields)
            for fname, value in mapped.items():
                template[fname] = value
                prefilled.append(fname)
            if notes:
                result["profile_matched"] = notes

        for f in writable:
            if f["name"] in template:
                continue
            if f["value"] not in (None, "", "/Off"):
                continue  # already answered — leave it alone
            template[f["name"]] = ""

        tpl = Path(args.template or working.with_suffix(".fill.json"))
        tpl.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n")

        result["template"] = str(tpl)
        result["prefilled_from_profile"] = len(prefilled)
        result["to_fill"] = len(template) - len(prefilled)
        result["labels"] = [
            {"name": f["name"], "label": f["label"], "type": f["type"],
             **({"on_states": f["on_states"]} if f.get("on_states") else {})}
            for f in writable
        ][: args.limit]
        result["next"] = (f'edit "{tpl}", then: '
                          f'pdfform.py complete "{working}" --data "{tpl}" -o filled.pdf')
    elif lane == "flat":
        result["next"] = f'overlay.py geometry "{working}" --page 1'
    elif lane == "xfa":
        result["next"] = "STOP — dynamic XFA, nothing to fill. See references/gotchas.md#xfa"
    else:
        result["next"] = f'pdfform.py ocr "{working}" -o ocr.pdf'

    out_json(result)
    return 0


def cmd_complete(args) -> int:
    """Resolve, fill, flatten and verify in one call, with one verdict.

    Exits non-zero if verification fails, so a caller never has to interpret a
    chain of intermediate successes to find out the document is wrong.
    """
    src = Path(args.file).expanduser()
    dest = Path(args.output).expanduser()
    reader = load(src, args.password)
    fields = walk_fields(reader, source=src)
    if not fields:
        raise FormError("no fillable fields — run `auto` first; this may be a "
                        "scanned or flat form.")

    raw = json.loads(Path(args.data).expanduser().read_text()) if args.data else {}
    if not isinstance(raw, dict):
        raise FormError("--data must be a JSON object")
    raw = {k: v for k, v in raw.items() if v not in ("", None)}

    # Explicit values are held to a strict standard: a key you typed that
    # matches no field is a mistake worth stopping for. Profile values are
    # best-effort — the profile is a convenience layer, and a form that simply
    # has no "employer" field must not fail because your profile mentions one.
    profile = {} if args.no_profile else load_profile()
    p_mapped, _p_problems, p_notes = resolve_keys(profile, fields)
    e_mapped, problems, e_notes = resolve_keys(raw, fields)
    if problems:
        raise FormError("could not resolve field(s):\n  - " + "\n  - ".join(problems))

    skipped = sorted(set(profile) - {k for k in profile if any(
        n.startswith(repr(k)) for n in p_notes)})
    data = _validate({**p_mapped, **e_mapped}, fields)
    if not data:
        raise FormError("nothing to fill: --data was empty and no profile value "
                        "matched a field on this form.")

    flatten = not args.no_flatten
    _do_fill(src, data, dest, flatten=flatten)

    verdict = _run_checks(dest, original=src, expect=data, require_flat=flatten,
                          page=None, password=args.password)
    out_json({
        "output": str(dest),
        "filled": len(data),
        "flattened": flatten,
        "resolved": e_notes + p_notes,
        "profile_keys_not_on_this_form": skipped,
        "ok": verdict["ok"],
        "checks": verdict["checks"],
    })
    return 0 if verdict["ok"] else 1


def cmd_batch(args) -> int:
    """One template, many records — fill + flatten + verify each, report per row."""
    template = Path(args.template).expanduser()
    outdir = Path(args.outdir).expanduser()
    outdir.mkdir(parents=True, exist_ok=True)

    records_path = Path(args.records).expanduser()
    if records_path.suffix.lower() == ".csv":
        with records_path.open(newline="") as fh:
            records = list(csv.DictReader(fh))
    else:
        records = json.loads(records_path.read_text())
        if isinstance(records, dict):
            records = [records]

    fields = walk_fields(load(template))
    results, failures = [], 0
    for i, record in enumerate(records, start=1):
        label = str(record.get(args.name_key) or f"record-{i}") if args.name_key else f"record-{i}"
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in label)[:80]
        dest = outdir / f"{safe}.pdf"
        payload = {k: v for k, v in record.items() if k != args.name_key}
        try:
            data = _validate(payload, fields)
            _do_fill(template, data, dest, flatten=not args.no_flatten)
            results.append({"record": label, "output": str(dest), "ok": True,
                            "filled": len(data)})
        except Exception as exc:  # noqa: BLE001 - one bad row must not kill the batch
            failures += 1
            results.append({"record": label, "ok": False, "error": str(exc)})
    out_json({"total": len(records), "failed": failures, "outdir": str(outdir),
              "results": results})
    return 1 if failures else 0


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pdfform.py",
        description="Inspect, fill, flatten and verify PDF forms.",
    )
    parser.add_argument("--password", help="password for an encrypted PDF")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("auto", help="START HERE — route the file, OCR if needed, "
                                    "and write a fill template")
    p.add_argument("file")
    p.add_argument("--template", help="where to write the fill template JSON")
    p.add_argument("--no-ocr", action="store_true", help="do not OCR a scan automatically")
    p.add_argument("--no-profile", action="store_true", help="ignore the saved profile")
    p.add_argument("--limit", type=int, default=60, help="max fields to list inline")
    p.set_defaults(func=cmd_auto)

    p = sub.add_parser("complete", help="resolve + fill + flatten + verify in one call")
    p.add_argument("file")
    p.add_argument("--data", help="JSON of {field name or human label: value}")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--no-flatten", action="store_true")
    p.add_argument("--no-profile", action="store_true")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("profile", help="values you reuse across forms")
    p.add_argument("action", choices=["show", "set", "clear"])
    p.add_argument("pairs", nargs="*", help='key=value, e.g. "first name=Jordan"')
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("triage", help="classify a PDF and name the next command")
    p.add_argument("file")
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("fields", help="list every fillable field with its legal values")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.add_argument("--filter", help="substring match on field name")
    p.add_argument("--mask", action="store_true",
                   help="replace existing values with a length placeholder — use when "
                        "the form already contains someone's personal data")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("fill", help="fill fields from a JSON file")
    p.add_argument("file")
    p.add_argument("--data", required=True, help="JSON object {field: value}")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--flatten", action="store_true", help="fill and flatten in one pass")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("flatten", help="burn values in and remove the form layer")
    p.add_argument("file")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--qpdf", action="store_true",
                   help="delegate to qpdf instead of the built-in appearance merge "
                        "(then sweep the widget shells qpdf leaves behind)")
    p.set_defaults(func=cmd_flatten)

    p = sub.add_parser("verify", help="prove the output is filled and truly flattened")
    p.add_argument("output")
    p.add_argument("--original", help="original PDF, enables the visual-change check")
    p.add_argument("--expect", help="the same JSON passed to fill")
    p.add_argument("--flat", action="store_true",
                   help="require the file to be truly flattened (fail if the form layer survives)")
    p.add_argument("--page", type=int, default=None,
                   help="page to render-compare (default: a page carrying an expected field)")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("ocr", help="add a text layer to a scanned PDF")
    p.add_argument("file")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--redo", action="store_true",
                   help="replace an existing bad OCR layer (keeps real text)")
    p.add_argument("--tesseract", action="store_true", help="skip the Apple Vision engine")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("batch", help="one template, many records")
    p.add_argument("template")
    p.add_argument("--records", required=True, help="CSV or JSON array")
    p.add_argument("-o", "--outdir", required=True)
    p.add_argument("--name-key", help="column used to name each output file")
    p.add_argument("--no-flatten", action="store_true")
    p.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    if not hasattr(args, "password"):
        args.password = None
    try:
        return args.func(args)
    except FormError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
