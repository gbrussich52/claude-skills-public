#!/usr/bin/env bash
# Regression suite for pdfform.py.
#
# Every case here corresponds to a failure that was observed on a real form —
# not a hypothetical. Cases marked [REGRESSION] reproduce a bug that shipped in
# an earlier revision of this script and would have destroyed user data.
#
# Usage:  bash tests/smoke.sh /path/to/an/acroform.pdf
# The PDF argument is required and is never committed: point it at any form
# with text fields and checkboxes. Results go to a temp dir that is removed on
# exit, so no filled document (or its contents) is left behind.

set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PDFFORM="$SKILL_DIR/scripts/pdfform.py"
FORM="${1:-}"

if [[ -z "$FORM" || ! -f "$FORM" ]]; then
  echo "usage: bash tests/smoke.sh /path/to/an/acroform.pdf" >&2
  exit 64
fi

# Bare `mktemp -d` on macOS ignores $TMPDIR and uses /var/folders, which is not
# writable under some sandboxes — it then returns an empty string and every path
# below silently becomes "/name", so the write tests fail while the "expect an
# error" tests PASS for entirely the wrong reason. Pin the template, then prove
# the directory is real before running anything.
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pdfform.XXXXXX")" || WORK=""
if [[ -z "$WORK" || ! -d "$WORK" ]] || ! touch "$WORK/.probe" 2>/dev/null; then
  echo "cannot create a writable temp dir (TMPDIR=${TMPDIR:-unset}) — aborting" >&2
  exit 70
fi
rm -f "$WORK/.probe"
trap 'rm -rf "$WORK"' EXIT
export UV_CACHE_DIR="${UV_CACHE_DIR:-$WORK/.uv}"

pass=0 fail=0
run() { uv run --script "$PDFFORM" "$@" 2>&1 | grep -v '^Ignoring'; }

ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s\n    %s\n' "$1" "${2:-}"; fail=$((fail+1)); }

expect_error() {  # description, then command
  local desc="$1"; shift
  if out="$("$@" 2>&1)"; then bad "$desc" "expected a non-zero exit, got success"
  else ok "$desc"; fi
}
expect_ok() {
  local desc="$1"; shift
  if out="$("$@" 2>&1)"; then ok "$desc"
  else bad "$desc" "$(echo "$out" | tail -2)"; fi
}
# Note on style below: every assertion uses an explicit if/then/else rather than
# `cmd && ok "..." || bad "..."`. In that idiom `bad` also runs whenever `ok`
# itself returns non-zero, which silently double-counts. A suite whose purpose
# is catching quiet failures should not contain one.

echo "== triage =="
expect_ok "classifies the form without error" run triage "$FORM"
LANE=$(run triage "$FORM" | python3 -c 'import json,sys;print(json.load(sys.stdin)["lane"])')
if [[ "$LANE" == "acroform" ]]; then ok "lane == acroform"
else bad "lane == acroform" "got $LANE"; fi

echo "== field discovery =="
run fields "$FORM" --json --mask > "$WORK/fields.json"
if python3 - "$WORK/fields.json" <<'PY'
import json,sys
f=json.load(open(sys.argv[1]))
assert f, "no fields found"
assert all("name" in x and "type" in x for x in f)
# Buttons must expose their real on-states: filling one without knowing them
# is the single most common silent no-op in PDF form work.
btns=[x for x in f if x["type"] in ("checkbox","radio")]
assert not btns or any(x.get("on_states") for x in btns), "no button exposed on_states"
PY
then ok "field records are well-formed"; else bad "field records are well-formed"; fi

if python3 - "$WORK/fields.json" <<'PY'
import json,sys
for x in json.load(open(sys.argv[1])):
    v=x.get("value")
    if v and not str(v).startswith("/"):
        assert v.startswith("<") and v.endswith("chars>"), f"unmasked: {v!r}"
PY
then ok "existing values are masked with --mask"; else bad "--mask leaks values"; fi

# Pick a live text field and a checkbox to drive the write tests.
read -r TEXTFIELD CHECKBOX CBSTATE <<<"$(python3 - "$WORK/fields.json" <<'PY'
import json,sys
f=json.load(open(sys.argv[1]))
t=next((x["name"] for x in f if x["type"]=="text" and not x["readonly"]), "")
c=next((x for x in f if x["type"]=="checkbox" and x.get("on_states") and not x["readonly"]), None)
print(t.replace(" ","\x01"), (c["name"].replace(" ","\x01") if c else ""), (c["on_states"][0] if c else ""))
PY
)"
TEXTFIELD=${TEXTFIELD//$'\x01'/ }; CHECKBOX=${CHECKBOX//$'\x01'/ }

echo "== validation (must fail loudly, never no-op) =="
python3 -c "import json,sys;json.dump({'__nope__':'x'},open(sys.argv[1],'w'))" "$WORK/unknown.json"
expect_error "unknown field name is rejected" run fill "$FORM" --data "$WORK/unknown.json" -o "$WORK/x.pdf"

if [[ -n "$CHECKBOX" ]]; then
  python3 -c "import json,sys;json.dump({sys.argv[2]:'/definitely_not_a_state'},open(sys.argv[1],'w'))" \
    "$WORK/badstate.json" "$CHECKBOX"
  expect_error "illegal checkbox state is rejected" \
    run fill "$FORM" --data "$WORK/badstate.json" -o "$WORK/x.pdf"
fi

echo "== fill =="
# Unicode probe: qpdf's appearance generator replaces non-ASCII with '?', so a
# surviving 'ñ' proves the appearance came from a Unicode-capable path.
python3 - "$WORK/data.json" "$TEXTFIELD" "$CHECKBOX" "$CBSTATE" <<'PY'
import json,sys
d={sys.argv[2]:"Muñoz-Ünicode"}
if sys.argv[3]: d[sys.argv[3]]=sys.argv[4]
json.dump(d,open(sys.argv[1],"w"))
PY
expect_ok "fills without error" run fill "$FORM" --data "$WORK/data.json" -o "$WORK/filled.pdf"
expect_ok "verify: values match and have appearance streams" \
  run verify "$WORK/filled.pdf" --expect "$WORK/data.json" --original "$FORM"

echo "== flatten =="
expect_ok "fill + flatten in one pass" \
  run fill "$FORM" --data "$WORK/data.json" -o "$WORK/flat.pdf" --flatten
expect_ok "[REGRESSION] flatten preserves pre-existing values (nothing_lost)" \
  run verify "$WORK/flat.pdf" --expect "$WORK/data.json" --original "$FORM" --flat

# A "flattened" file that still carries field objects is not flattened: the
# data remains extractable by anyone who receives it.
expect_error "flattened file exposes no fillable fields" run fields "$WORK/flat.pdf"

# qpdf alone leaves widget shells behind (its --remove-acroform only touches the
# catalog entry), so the qpdf path must sweep them. Verified as strictly as the
# built-in path, or the flag would quietly produce a weaker guarantee.
if command -v qpdf >/dev/null 2>&1; then
  run fill "$FORM" --data "$WORK/data.json" -o "$WORK/pre-q.pdf" >/dev/null 2>&1
  expect_ok "flatten --qpdf runs" run flatten "$WORK/pre-q.pdf" -o "$WORK/q.pdf" --qpdf
  expect_ok "[REGRESSION] qpdf output is fully flat (no leftover widgets)" \
    run verify "$WORK/q.pdf" --expect "$WORK/data.json" --original "$FORM" --flat
else
  printf '  \033[33mSKIP\033[0m qpdf engine cases (qpdf not installed)\n'
fi

if python3 - "$WORK/flat.pdf" <<'PY'
import subprocess,sys
t=subprocess.run(["pdftotext","-q",sys.argv[1],"-"],capture_output=True,text=True).stdout
assert "Muñoz-Ünicode" in t, "expected ñ/Ü to survive; got substitution or loss"
PY
then ok "unicode survives flattening (no '?' substitution)"
else bad "unicode mangled by flatten"; fi

echo "== profile (must refuse secrets) =="
# The profile is a plaintext file. The refusal list is the only thing standing
# between convenience and an SSN on disk, so it is tested adversarially. An
# earlier regex version matched none of these: `\b...driver.?s?.?lic\b` cannot
# match "drivers license" because the trailing \b falls between "lic" and "e".
export PDFFORM_PROFILE_HOME="$WORK"   # keep the real profile untouched
if python3 - "$PDFFORM" <<'PY'
import subprocess, sys, json, os, pathlib, tempfile
script = sys.argv[1]
home = pathlib.Path(tempfile.mkdtemp())
env = {**os.environ, "HOME": str(home)}
secrets = ["SSN=1","ssn#=1","Social Security Number=1","DOB=1","Date of Birth=1",
           "birthdate=1","drivers license=1","Driver's License #=1","DL=1",
           "license number=1","passport=1","Account Number=1","routing=1",
           "credit card=1","CVV=1","PIN=1","mother's maiden name=1","Tax ID=1","EIN=1"]
benign  = ["first name=Jordan","city=Anytown","printing=ok","tint=ok"]
r = subprocess.run(["uv","run","--script",script,"profile","set",*secrets,*benign],
                   capture_output=True, text=True, env=env)
out = json.loads(r.stdout)
refused, stored = set(out.get("refused",[])), set(out.get("stored",[]))
missed = [s.split("=")[0] for s in secrets if s.split("=")[0] not in refused]
assert not missed, f"SECRETS ACCEPTED: {missed}"
# False positives matter too: "printing" contains "pin", "tint" contains "tin".
assert {"printing","tint","first name","city"} <= stored, f"benign keys rejected: {stored}"
prof = home/".config"/"pdf-forms"/"profile.json"
body = prof.read_text() if prof.exists() else ""
assert "1" not in json.loads(body or "{}").values(), "a refused value reached disk"
PY
then ok "[REGRESSION] profile refuses every secret, keeps benign lookalikes"
else bad "[REGRESSION] profile refuses every secret"; fi
unset PDFFORM_PROFILE_HOME

echo "== auto / complete (the frictionless path) =="
expect_ok "auto routes and writes a fill template" \
  run auto "$FORM" --template "$WORK/auto.json" --no-profile
if [[ -s "$WORK/auto.json" ]]; then ok "auto template is non-empty"
else bad "auto template is non-empty"; fi

# Human labels must resolve to real fields, or the whole point is lost.
if LBL=$(run auto "$FORM" --template "$WORK/a2.json" --no-profile \
         | python3 -c '
import json,sys
d=json.load(sys.stdin)
c=[f for f in (d.get("labels") or []) if f.get("label") and f["type"]=="text"]
print(c[0]["label"] if c else "")'); [[ -n "$LBL" ]]; then
  python3 -c "import json,sys;json.dump({sys.argv[2]:'LabelResolved'},open(sys.argv[1],'w'))" \
    "$WORK/bylabel.json" "$LBL"
  expect_ok "complete accepts a human label instead of a field name" \
    run complete "$FORM" --data "$WORK/bylabel.json" -o "$WORK/bylabel.pdf" --no-profile
else
  printf '  \033[33mSKIP\033[0m label resolution (no labelled text field on this form)\n'
fi

echo "== OCR =="
# Builds its own scanned specimen so the case needs no private fixture: render a
# page to an image, wrap the image back into a PDF, and that PDF has no text
# layer at all. The assertion that matters is the lane FLIP — a lane that stays
# `scanned` after OCR means the OCR did nothing, whatever it reported.
if command -v ocrmypdf >/dev/null 2>&1; then
  if pdftoppm -q -r 150 -png -f 1 -l 1 "$FORM" "$WORK/pg" \
     && uv run --quiet --with img2pdf img2pdf -o "$WORK/scanlike.pdf" "$WORK"/pg-*.png 2>/dev/null; then
    BEFORE=$(run triage "$WORK/scanlike.pdf" | python3 -c 'import json,sys;print(json.load(sys.stdin)["lane"])')
    if [[ "$BEFORE" == "scanned" ]]; then ok "image-only PDF classified as scanned"
    else bad "image-only PDF classified as scanned" "got $BEFORE"; fi

    if run ocr "$WORK/scanlike.pdf" -o "$WORK/ocred.pdf" >/dev/null 2>&1; then
      AFTER=$(run triage "$WORK/ocred.pdf" | python3 -c 'import json,sys;print(json.load(sys.stdin)["lane"])')
      if [[ "$AFTER" != "scanned" ]]; then ok "OCR adds a text layer (lane $BEFORE -> $AFTER)"
      else bad "OCR adds a text layer" "still classified scanned — OCR reported success but did nothing"; fi
    else
      bad "ocr runs" "see ocrmypdf output"
    fi
  else
    printf '  \033[33mSKIP\033[0m OCR cases (could not build a scanned specimen)\n'
  fi
else
  printf '  \033[33mSKIP\033[0m OCR cases (ocrmypdf not installed)\n'
fi

echo "== hybrid XFA (live IRS W-9) =="
# The W-9 is an XFA/AcroForm hybrid. It must NOT be refused: its AcroForm layer
# is genuinely fillable, and filling a W-9 is among the most common reasons to
# reach for this skill. Skipped without network — never silently, so a green
# run cannot hide an unrun case.
if curl -sS --max-time 25 -o "$WORK/fw9.pdf" https://www.irs.gov/pub/irs-pdf/fw9.pdf 2>/dev/null \
   && head -c 4 "$WORK/fw9.pdf" | grep -q '%PDF'; then
  W9LANE=$(run triage "$WORK/fw9.pdf" | python3 -c 'import json,sys;print(json.load(sys.stdin)["lane"])')
  if [[ "$W9LANE" == "xfa-hybrid" ]]; then ok "W-9 classified as xfa-hybrid"
  else bad "W-9 classified as xfa-hybrid" "got $W9LANE"; fi

  # Hierarchical IRS naming plus a /1 on-state — both the documented traps.
  cat > "$WORK/w9.json" <<'JSON'
{"topmostSubform[0].Page1[0].f1_01[0]": "Muñoz Holdings LLC",
 "topmostSubform[0].Page1[0].Boxes3a-b_ReadOrder[0].c1_1[0]": "/1"}
JSON
  expect_ok "fills a hybrid XFA form instead of refusing" \
    run fill "$WORK/fw9.pdf" --data "$WORK/w9.json" -o "$WORK/w9.pdf" --flatten
  expect_ok "W-9 output verifies" \
    run verify "$WORK/w9.pdf" --expect "$WORK/w9.json" --original "$WORK/fw9.pdf" --flat
else
  printf '  \033[33mSKIP\033[0m W-9 cases (irs.gov unreachable)\n'
fi

echo "== malformed input =="
printf 'definitely not a pdf' > "$WORK/corrupt.pdf"
: > "$WORK/empty.pdf"
expect_error "corrupt file"    run triage "$WORK/corrupt.pdf"
expect_error "zero-byte file"  run triage "$WORK/empty.pdf"
expect_error "missing file"    run triage "$WORK/does-not-exist.pdf"

echo
printf 'passed %d, failed %d\n' "$pass" "$fail"
[[ $fail -eq 0 ]]
