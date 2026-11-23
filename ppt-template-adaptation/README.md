# ppt-template-adapter — apply a template for real, audit every slide

## What problem it solves
"Put this into our template" usually goes wrong in ways that only show later: text pasted into the default
template while master and layouts are not inherited; titles drawn as text boxes; fonts and colours typed onto
every run; overflowing text squeezed to unreadable sizes; pictures stretched; only the first three slides fixed;
fonts replaced and pictures lost after PDF export. This Skill makes template application checkable:

1. **template anatomy** — `deck_template.py inspect`: masters, layouts, placeholders (type/idx/box), theme
   fonts/colours, size ladder, content→layout mapping and gaps, plus a rendered gallery (one slide per layout);
2. **before audit** — per-slide deviations of the starting deck (layout not from template, theme mismatch,
   hard-coded fonts, off-palette colours, title as text box / moved, missing slide numbers, empty placeholders,
   overflow, < 12 pt, stretched pictures, off-slide shapes);
3. **adaptation** — route A (deck → spec with exact text/pictures/tables/notes/captions → rebuild on the template's
   real layouts), route B (generate from materials), route C (rule-based in-place fixes with a before/after log);
4. **explainable sizing** — placeholder text inherits template sizes; overflow shrinks along a logged ladder,
   never below 12 pt; charts adopt the template's accent colours; pictures contain-fit;
5. **after audit + preservation + renders** — `compliance.json`, `preserve.json` (every paragraph, number,
   picture and note of the source deck found in the result), LibreOffice PDF/PNGs, fonts requested vs embedded;
6. **records** — evidence map (carried content auto-matched to the paper), version record, result.json.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | the 6-step procedure (budget-aware) + exception table |
| `scripts/deck_prepare.py` | one-call setup: classify inputs, extract text, paper map, template/deck inventory, tools, fonts |
| `scripts/extract_materials.py` | text extraction with `[pN:M]` locators (pdftotext → PyMuPDF → pdfplumber → OCR), tables, page previews |
| `scripts/paper_map.py` | sections (two-column aware), captions, figure/table crops (caption above or below, running heads excluded), table cells `table_rows` (super/subscripts kept), numbered display-equation crops, key sentences, overview image (also without PIL/matplotlib); `crop` and `find` (verbatim sentences + `[pN:M]`) sub-commands |
| `scripts/deck_build.py` | spec → .pptx (python-pptx or stdlib backend), font fitting ladder, charts from CSV, tables, figures, notes |
| `scripts/deck_render.py` | .pptx → PDF → PNGs + contact sheet; layout and font checks |
| `scripts/deck_check.py` | spec (or existing .pptx) vs materials audit; writes the evidence map |
| `scripts/deck_make.py` | check → build → render (→ template audit) in one call |
| `scripts/deck_edit.py` | inventory / source-preserving edits / snapshots / slide diff (for revisions) |
| `scripts/deck_template.py` | template inspect (+ layout gallery), audit, fix, deck→spec, content preservation |
| `scripts/deck_fill.py` | version_record.json + contract mapping for result.json |
| `scripts/make_result.py` | contract-complete result.json (copied unchanged from the sibling figure Skill) |
| `scripts/pptkit.py` | shared stdlib PresentationML library (package I/O, default template, catalog, inventory, XML builders) |
| `templates/` | `spec_template.json` (route B, with `layout_name` hints), `facts_template.json` |
| `references/` | `template_rules.md` (anatomy, layout mapping, size ladder, fonts/colours, consistency, routes, fixes), `slide_rules.md`, `formats.md` |
| `examples/minimal/` | SYNTHETIC example: generator for a 4:3 .potx template + a 16:9 draft deck with violations + paper, case.json |

## Requirements
Python 3.8+. Used when present (all optional, detected at run time): python-pptx (else stdlib writer),
matplotlib (charts from CSV, contact sheets), Pillow/numpy (crop trimming, pixel diffs), LibreOffice `soffice`
(PDF export), poppler `pdftotext/pdftoppm/pdffonts/pdfinfo` (text, crops, previews, font check) or PyMuPDF,
`fc-match/fc-list` (font availability), tesseract (OCR). No network, no binary templates: the default
academic 16:9 template (master + 6 layouts + theme + notes master) is generated in code.

## Quick start
```bash
SKILL=/path/to/paper2ppt
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work --gallery
python3 $SKILL/scripts/deck_template.py audit --pptx draft.pptx --template T.potx --out /app/output/compliance_before.json
python3 $SKILL/scripts/deck_template.py to_spec --pptx draft.pptx --out /app/work/spec.json --previews /app/work/start_render/previews
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --outdir /app/output --template T.potx --mode template --preserve-from draft.pptx
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```

## How it was validated (synthetic example + purpose-built faults; not formal case results)
- `examples/minimal`: 16:9 draft (5 slides, violations injected with deck_edit) → 4:3 university template (.potx):
  compliance before 0 % (13 FAIL: theme fonts/colours ×8, slide size, hard-coded Comic Sans, 10 pt body,
  stretched picture, overflowing moved title; warnings: moved title, magenta text, missing notes), after route A
  100 % (0 FAIL); preserve pass (all paragraphs, numbers, picture, notes,
  captions, footer); result.json complete with 6 contract fields.
- A PowerPoint-made 4:3 template (11 layouts incl. Comparison, Picture with Caption, vertical layouts; Calibri;
  given as .potx) used by both backends: real placeholder inheritance, theme colours in charts, audit 100 %.
- Route C on a python-pptx-made messy deck: fixes logged (theme, hard-coded font, off-palette colour, 10 pt → 12 pt,
  stretched picture, 2 slide numbers), audit 0 % → 100 %; to_spec turns its text-box title into the slide title.
- Both build backends render pixel-identically; edits by the stdlib engine keep untouched package parts byte-identical.

## Limits
- Overflow detection is an estimate (wrap simulation with average glyph widths); the renders are the truth —
  look at the contact sheet once.
- Figure regions are found from caption positions and text-free gaps (pdftotext bbox); unusual layouts
  (side captions, multi-panel figures across columns) need a manual bbox.
- Route A rebuilds slides: custom drawings (arrows, grouped shapes) of the draft are carried as image crops when
  renders are given, otherwise listed in `not_carried_over`; native charts become images.
- Compliance rules cover the inheritance chain, fonts, colours, positions, numbering, sizes and pictures; they do
  not judge aesthetic choices the template leaves open.
- The evidence check is string-level: it proves that quotes and numbers exist in the materials, not that a claim
  is a fair summary — the agent still has to read the cited paragraph.

## Maintenance
Version 1.0 (2026-11). Shared toolkit identical across the four PPT Skills (paper2ppt, talk_organize,
ppt_template, ppt_edit); only SKILL.md, templates, references and examples differ.

## Tested on real papers (dogfooding log, 2026-11)
Run on two arXiv papers as the evaluator would (case.json + task.md + PDF, procedure followed literally): a two-column ICML paper with equations and tables above/below captions, and a single-column NeurIPS paper with scientific-notation tables and an internal number conflict (41.8 vs 41.0 BLEU). Fixed from that run: column-interleaved text extraction, zero crops on PDFs whose math fonts emit control characters, table captions below tables, running heads in crops, garbled formulas (now equation crops), sub/superscripts, exponent checking (2.3 · 10^19 vs 10^18), contract fields such as `editable_deck` mapped to the wrong record, template text boxes/captions/charts in `to_spec`, zero-size layout placeholders, edit rounds that flagged requested font changes and pre-existing issues as failures, and stdlib-only runs without contact sheets or charts.
