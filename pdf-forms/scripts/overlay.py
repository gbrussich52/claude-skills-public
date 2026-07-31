#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf==6.14.2", "pdfplumber==0.11.10", "reportlab==4.4.4"]
# ///
"""
overlay — put values on a form that has no form fields.

For flat (printed) and scanned PDFs. There is no field to set, so text is drawn
at coordinates. The workflow this is built for:

    1. overlay.py geometry form.pdf --page 1
         Emits the page's words, ruled lines and small squares as JSON, and
         renders the page to PNG. Small near-square rectangles are checkbox
         candidates; long thin ones are write-on lines.

    2. Look at the PNG and decide where each value goes.
         A vision model reads the image and returns pixel coordinates. That is
         far more reliable than inferring intent from geometry alone, and it
         needs no ML dependency here — the model already sees the picture.

    3. overlay.py draw form.pdf --boxes boxes.json -o filled.pdf --space image
         Draws the values. --space image converts the pixel coordinates from
         the rendered PNG into PDF points, so numbers read off the image can be
         used directly.

    4. overlay.py draw ... then look at the result.
         Coordinate placement has no failure signal. Nothing errors when text
         lands two inches too low. Always re-render and look.

Kept separate from pdfform.py so that inspecting or filling an AcroForm does not
pay for pdfplumber and reportlab.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(stream=sys.stderr, level=logging.ERROR)
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# A rectangle roughly this size, and roughly square, is almost always a
# checkbox. Below the floor it is a rule or a speck; above the ceiling it is a
# table cell. Derived from surveying real government forms: boxes cluster at
# 8-14pt (about 0.11"-0.19").
CHECKBOX_MIN_PT = 6.0
CHECKBOX_MAX_PT = 20.0
CHECKBOX_MAX_ASPECT = 1.45

# A rectangle much wider than it is tall, and short, is a write-on line.
RULE_MAX_HEIGHT_PT = 3.0
RULE_MIN_WIDTH_PT = 40.0


def note(msg: str) -> None:
    print(msg, file=sys.stderr)


def cmd_geometry(args) -> int:
    """Dump a page's text and vector geometry, and render it for inspection."""
    import pdfplumber

    src = Path(args.file).expanduser()
    with pdfplumber.open(str(src)) as pdf:
        if args.page < 1 or args.page > len(pdf.pages):
            print(f"error: page {args.page} out of range (1-{len(pdf.pages)})", file=sys.stderr)
            return 2
        page = pdf.pages[args.page - 1]
        width, height = float(page.width), float(page.height)

        words = [
            {"text": w["text"], "x0": round(w["x0"], 1), "x1": round(w["x1"], 1),
             "top": round(w["top"], 1), "bottom": round(w["bottom"], 1)}
            for w in page.extract_words()
        ]

        checkboxes, rules = [], []
        for r in page.rects:
            w_pt = abs(float(r["x1"]) - float(r["x0"]))
            h_pt = abs(float(r["bottom"]) - float(r["top"]))
            if h_pt <= 0 or w_pt <= 0:
                continue
            aspect = max(w_pt, h_pt) / min(w_pt, h_pt)
            box = {"x0": round(float(r["x0"]), 1), "top": round(float(r["top"]), 1),
                   "width": round(w_pt, 1), "height": round(h_pt, 1)}
            if CHECKBOX_MIN_PT <= w_pt <= CHECKBOX_MAX_PT and \
               CHECKBOX_MIN_PT <= h_pt <= CHECKBOX_MAX_PT and aspect <= CHECKBOX_MAX_ASPECT:
                checkboxes.append(box)
            elif h_pt <= RULE_MAX_HEIGHT_PT and w_pt >= RULE_MIN_WIDTH_PT:
                rules.append(box)

        for ln in page.lines:
            w_pt = abs(float(ln["x1"]) - float(ln["x0"]))
            if w_pt >= RULE_MIN_WIDTH_PT:
                rules.append({"x0": round(float(ln["x0"]), 1),
                              "top": round(float(ln["top"]), 1),
                              "width": round(w_pt, 1), "height": 0.0})

    png = None
    if not args.no_render:
        prefix = args.render_prefix or str(src.with_suffix("")) + f"-p{args.page}"
        res = subprocess.run(
            ["pdftoppm", "-q", "-r", str(args.dpi), "-png",
             "-f", str(args.page), "-l", str(args.page), str(src), prefix],
            capture_output=True,
        )
        if res.returncode == 0:
            found = sorted(Path(prefix).parent.glob(Path(prefix).name + "*.png"))
            png = str(found[-1]) if found else None
        else:
            note("pdftoppm failed; continuing without a rendered image")

    result: dict[str, Any] = {
        "file": str(src),
        "page": args.page,
        "page_size_pt": {"width": round(width, 1), "height": round(height, 1)},
        "render": png,
        "render_dpi": args.dpi,
        # Pixel size of the render, so image coordinates can be converted back.
        "render_size_px": {"width": round(width * args.dpi / 72), "height": round(height * args.dpi / 72)},
        "coordinate_note": (
            "words/checkboxes/rules use pdfplumber space: origin TOP-left, y grows "
            "downward, units are points. `draw --space image` accepts pixel "
            "coordinates measured on `render` instead."
        ),
        "words": words if args.words else f"{len(words)} words (pass --words to include)",
        "checkbox_candidates": checkboxes,
        "rule_candidates": rules[:200],
    }
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_draw(args) -> int:
    """Draw values onto a PDF at given coordinates and merge them in."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    src = Path(args.file).expanduser()
    boxes = json.loads(Path(args.boxes).expanduser().read_text())
    if isinstance(boxes, dict):
        boxes = boxes.get("boxes", [])
    if not isinstance(boxes, list) or not boxes:
        print("error: --boxes must be a non-empty JSON array (or {\"boxes\": [...]})",
              file=sys.stderr)
        return 2

    reader = PdfReader(str(src))
    writer = PdfWriter(clone_from=str(src))

    by_page: dict[int, list[dict]] = {}
    for b in boxes:
        try:
            page_no = int(b.get("page", 1))
        except (TypeError, ValueError):
            print(f"error: box has a non-numeric page: {b}", file=sys.stderr)
            return 2
        if page_no < 1 or page_no > len(reader.pages):
            print(f"error: box references page {page_no}, document has "
                  f"{len(reader.pages)}", file=sys.stderr)
            return 2
        by_page.setdefault(page_no, []).append(b)

    drawn = 0
    for page_no, items in by_page.items():
        page = writer.pages[page_no - 1]
        media = page.mediabox
        pw, ph = float(media.width), float(media.height)

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(pw, ph))
        for b in items:
            text = str(b.get("text", ""))
            if not text and not b.get("mark"):
                continue
            size = float(b.get("size", args.size))
            x_raw, y_raw = float(b.get("x", 0)), float(b.get("y", 0))

            if args.space == "image":
                # Pixel coordinates measured on the rendered PNG: origin is the
                # TOP-left and y grows downward, so the y axis must be flipped
                # as well as scaled. Getting this wrong mirrors the whole page
                # vertically, which looks like a plausible layout and is not.
                scale = 72.0 / args.dpi
                x = x_raw * scale
                y = ph - (y_raw * scale)
                # A pixel row is the TOP of the glyph; shift down by the cap
                # height so the text sits on the line rather than above it.
                y -= size * 0.8
            elif args.space == "topleft":
                x, y = x_raw, ph - y_raw - size * 0.8
            else:  # native PDF space, origin bottom-left
                x, y = x_raw, y_raw

            c.setFillColor(HexColor(b.get("color", args.color)))
            if b.get("mark"):
                mark = str(b["mark"])
                c.setFont(args.font, size * 1.1)
                c.drawString(x, y, "X" if mark.lower() in ("x", "true", "yes", "on") else mark)
            else:
                c.setFont(args.font, size)
                align = b.get("align", "left")
                if align == "center":
                    c.drawCentredString(x, y, text)
                elif align == "right":
                    c.drawRightString(x, y, text)
                else:
                    c.drawString(x, y, text)
            drawn += 1
        c.save()
        buf.seek(0)
        page.merge_page(PdfReader(buf).pages[0])

    dest = Path(args.output).expanduser()
    with open(dest, "wb") as fh:
        writer.write(fh)

    json.dump({
        "output": str(dest),
        "drawn": drawn,
        "pages_touched": sorted(by_page),
        "next": (f'pdftoppm -r 100 -png -f {min(by_page)} -l {min(by_page)} '
                 f'"{dest}" check && open check-*.png   # LOOK at it — '
                 "misplaced text raises no error"),
    }, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="overlay.py",
        description="Place values on PDFs that have no form fields.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("geometry", help="dump words, checkbox and rule candidates; render the page")
    p.add_argument("file")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--words", action="store_true", help="include every word (verbose)")
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--render-prefix")
    p.set_defaults(func=cmd_geometry)

    p = sub.add_parser("draw", help="draw text/marks at coordinates and merge")
    p.add_argument("file")
    p.add_argument("--boxes", required=True,
                   help='JSON array of {page,x,y,text|mark,size?,align?,color?}')
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--space", choices=["pdf", "image", "topleft"], default="pdf",
                   help="pdf = points from bottom-left (default); image = pixels on "
                        "the render at --dpi; topleft = points from top-left")
    p.add_argument("--dpi", type=int, default=150, help="dpi of the render, for --space image")
    p.add_argument("--size", type=float, default=10.0, help="default font size")
    p.add_argument("--font", default="Helvetica")
    p.add_argument("--color", default="#000000")
    p.set_defaults(func=cmd_draw)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
