# paper2ppt-evidence-deck — paper → editable, evidence-bound academic slides

## What problem it solves
Asked to "turn this paper into slides", a general agent usually produces something that looks like a deck but
cannot enter a real talk: abstract and paragraphs pasted in section order, figure numbers that do not match the
paper, numbers rounded or invented, a method diagram presented as a result, text overflowing narrow boxes or
shrunk to unreadable sizes, the template's title/footer/colours overwritten, notes that repeat the slide, and
no way to check where anything came from. This Skill makes the agent work in checkable stages:

1. **map the paper** — `paper_map.py` lists sections with roles, every Figure/Table caption with page,
   caption-anchored crop box and a ready crop, where each figure is discussed, and key claim sentences;
2. **evidence first** — `facts.json`: every fact used on a slide with a verbatim quote and `[page:paragraph]`
   locator (verified automatically);
3. **deck as data** — `spec.json`: one claim per slide, roles in story order, figures/tables/charts bound to the
   paper's own numbering or to input data files, per-bullet sources, speaker notes;
4. **deterministic build** — `deck_build.py` writes a real template-based .pptx (title/body placeholders keep
   master/layout inheritance; footers and slide numbers from layout placeholders; notes slides), with python-pptx
   or, if it is missing, a pure-stdlib OOXML writer that produces the same XML;
5. **automatic audits** — `deck_check.py` (quotes verbatim, locator correct, every number in the materials,
   Fig./Table numbers exist, schematic-as-data, claim titles, density, notes not repeating, story order, slide
   count) and `deck_render.py` (LibreOffice PDF + PNG per slide + contact sheet, estimated overflow, font
   minimum, off-slide shapes, stretched pictures, fonts requested vs embedded in the PDF);
6. **records** — `evidence_map.json/.md` (slide item → fact → file/locator/quote → check),
   `version_record.json` (inputs and outputs with sha256, template, fonts, tools, re-run command) and a
   contract-complete `result.json` that never fabricates missing fields.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | the 6-step procedure (budget-aware) + exception table |
| `scripts/deck_prepare.py` | one-call setup: classify inputs, extract text, paper map, template/deck inventory, tools, fonts |
| `scripts/extract_materials.py` | text extraction with `[pN:M]` locators (pdftotext → PyMuPDF → pdfplumber → OCR), tables, page previews |
| `scripts/paper_map.py` | sections, captions, figure/table crop candidates (+ overview image), key sentences; `crop` sub-command |
| `scripts/deck_build.py` | spec → .pptx (python-pptx or stdlib backend), font fitting ladder, charts from CSV, tables, figures, notes |
| `scripts/deck_render.py` | .pptx → PDF → PNGs + contact sheet; layout and font checks |
| `scripts/deck_check.py` | spec (or existing .pptx) vs materials audit; writes the evidence map |
| `scripts/deck_make.py` | check → build → render (→ template audit) in one call |
| `scripts/deck_edit.py` | inventory / source-preserving edits / snapshots / slide diff (for revisions) |
| `scripts/deck_template.py` | template inspect (+ layout gallery), audit, fix, deck→spec, content preservation |
| `scripts/deck_fill.py` | version_record.json + contract mapping for result.json |
| `scripts/make_result.py` | contract-complete result.json (copied unchanged from the sibling figure Skill) |
| `scripts/pptkit.py` | shared stdlib PresentationML library (package I/O, default template, catalog, inventory, XML builders) |
| `templates/` | `spec_template.json`, `facts_template.json` (TODO markers fail the check until replaced) |
| `references/` | `storyline.md` (paper → main line, slide budgets), `slide_rules.md`, `formats.md` |
| `examples/minimal/` | SYNTHETIC example: generator for a 3-page made-up paper + CSV, case.json, facts.json, spec.json |

## Requirements
Python 3.8+. Used when present (all optional, detected at run time): python-pptx (else stdlib writer),
matplotlib (charts from CSV, contact sheets), Pillow/numpy (crop trimming, pixel diffs), LibreOffice `soffice`
(PDF export), poppler `pdftotext/pdftoppm/pdffonts/pdfinfo` (text, crops, previews, font check) or PyMuPDF,
`fc-match/fc-list` (font availability), tesseract (OCR). No network, no binary templates: the default
academic 16:9 template (master + 6 layouts + theme + notes master) is generated in code.

## Quick start
```bash
SKILL=/path/to/paper2ppt
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
# write /app/work/facts.json and /app/work/spec.json (see templates/ and references/formats.md)
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --outdir /app/output [--template T.potx]
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```

## How it was validated (synthetic example + purpose-built faults; not formal case results)
- End to end on `examples/minimal` (3-page synthetic paper): prepare → facts/spec → make → fill → result.json,
  9 slides, check pass, render ok, 24 artifacts with hashes; total script time ≈ 3 s plus LibreOffice.
- Both backends (python-pptx 1.0.2 and stdlib writer, forced with `DECK_BACKEND=xml` and in a venv without
  python-pptx) produce pixel-identical LibreOffice renders; stdlib output re-opens in python-pptx.
- A PowerPoint-made 4:3 template (11 layouts, Calibri) given as `.potx`: layouts, inherited placeholders,
  theme colours in charts, compliance audit 100 %.
- Faults detected on purpose: number not in the paper, quote not verbatim, wrong locator, Fig. 5 that does not
  exist, slide without sources/notes, TODO left, 10 bullets, overflow, 10 pt body text, stretched picture,
  hard-coded font, off-palette colour, title drawn as a text box, missing slide number.

## Limits
- Overflow detection is an estimate (wrap simulation with average glyph widths); the renders are the truth —
  look at the contact sheet once.
- Figure regions are found from caption positions and text-free gaps (pdftotext bbox); unusual layouts
  (side captions, multi-panel figures across columns) need a manual bbox.
- Charts are matplotlib images bound to CSV data (data + chart JSON saved next to the deck), not native Office
  charts; tables are native and editable.
- The evidence check is string-level: it proves that quotes and numbers exist in the materials, not that a claim
  is a fair summary — the agent still has to read the cited paragraph.

## Maintenance
Version 1.0 (2026-11). Shared toolkit identical across the four PPT Skills (paper2ppt, talk_organize,
ppt_template, ppt_edit); only SKILL.md, templates, references and examples differ.
