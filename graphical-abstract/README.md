# source-bound-figure-builder — Skill for traceable, editable research figures

## What problem it solves
When an AI agent is asked to turn a paper, an experiment record or a data table into a graphical
abstract (or schematic, mechanism diagram, chart, apparatus/route figure), the usual failure is not
"cannot draw" but "cannot be used": entities mislabelled, arrows pointing the wrong way, numbers that
appear nowhere in the source, colours without a legend, a flat PNG that nobody can edit, and no record
of where anything came from. This Skill makes the agent work in four checkable stages:

1. **evidence first** — every entity/relation/number is collected with a page-paragraph locator
   before anything is drawn (`facts.json`);
2. **figure as data** — the figure is a JSON spec (panels, entities, arrows, charts bound to CSV
   columns, legend, scale bars), each element carrying its source;
3. **automatic audits** — provenance, number-in-source, legend coverage, units, four-part
   completeness (problem/method/result/conclusion), text overflow, canvas bounds, overlaps, export;
4. **editable delivery** — layered SVG (one `<g id>` per spec element, text kept as text) + PDF + PNG
   from the same drawing, `layer_map.json`, check reports and a contract-complete `result.json`.
   Edit tasks change only the addressed ids of the original SVG and log before/after values.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | the procedure the agent follows (budget-aware: 8–12 calls, 6 steps, ISSUE→fix table, exception table) |
| `scripts/extract_materials.py` | one-call extraction of PDFs in reading order (two-column safe; pdftotext → PyMuPDF → pdfplumber → OCR), one paragraph per line with locator `[p3:12]`, `.layout.txt` for tables, caption list, xlsx→CSV, SVG inventory; `--find` prints quotable sentences with locators |
| `scripts/ga_check.py` | pre-render audit: provenance, verbatim quotes, numbers in source (2.3e19 / 2.3×10^19 / PDF-flattened `2.3 · 1019` / 10¹⁸, unicode minus, IL-10-style names ignored), legend coverage + colour consistency, units, missing values, four-part roles |
| `scripts/ga_render.py` | spec → SVG (one Inkscape layer per panel, centred editable text) + PDF + PNG; per-label shrink, CJK/Latin word-aware wrapping, glyph-checked CJK font; arrow auto-routing (opposite pairs as arcs); charts from wide/long CSV, barh, log axes, n.r. markers, highlights, exact value labels; geometric ISSUE report (overlaps, crossings, overflow) for text-only agents |
| `scripts/svg_edit.py` | `inventory` (text, colours, bbox) / `apply` (set_text keeps centring, recolor, deep set_style, set_attr, translate, delete, font-matched add_text; every op verified, untouched ids compared) / `export` (rsvg-convert → cairosvg → inkscape → Chromium → LibreOffice + pdftoppm at the requested dpi) |
| `scripts/make_result.py` | `--list-fields` (contract keys from any contract shape); writes `result.json` with `case_id` and every `output_contract` field; unfilled fields become `not_provided`, broken paths reported, status downgraded honestly |
| `templates/spec_template_ga.json` | 4-panel graphical-abstract layout with placeholders (check fails while any TODO remains) |
| `templates/spec_template_{schematic,chart,mechanism,setup}.json` | layouts for the other figure types (same scripts, `--mode`) |
| `references/design_rules.md` | visual grammar (one meaning per channel), palette, layouts for other figure types, typography |
| `examples/minimal/` | synthetic example: materials, filled spec, `fill.json`, commands (example only, not a formal case result) |

## Requirements
Python 3.9+ with matplotlib and numpy (standard scientific Python). Optional, auto-detected:
`pdftotext`/`pdftoppm` (poppler), `tesseract`, `rsvg-convert`/`cairosvg`/`inkscape`/Chromium/LibreOffice
for SVG export in edit mode, `openpyxl` for xlsx. No network access needed.

## Quick start
```bash
SKILL=/path/to/this/skill
python3 $SKILL/scripts/extract_materials.py --input /input --task task.md --work /app/work
cp $SKILL/templates/spec_template_ga.json /app/work/spec.json   # then fill it from facts.json
python3 $SKILL/scripts/ga_check.py --spec /app/work/spec.json --materials '/app/work/materials/*.txt' /input/*.csv --out /app/output/check_report.json
python3 $SKILL/scripts/ga_render.py --spec /app/work/spec.json --outdir /app/output --name graphical_abstract
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```

## How it was validated
The synthetic example in `examples/minimal` runs end to end (extract → check → render → edit → export
→ result, status `complete`). Dogfooded on real arXiv papers with organiser-style cases (Chinese
`task.md`, varied `output_contract` shapes): a graphical abstract with Chinese title from q-bio.MN
1607.07244 (dendritic-cell signal integration), a Fig. 3 mechanism redraw, a 10-model chart from
Table 2 of 1706.03762 (missing cells, merged cells, ×10^19 values, log axis) and a source-preserving
edit of a generated SVG. Fixed from that run: interleaved two-column text, locators not on grep
lines, CJK rendered as boxes plus a 30 k-character warning flood, Latin words split by CJK wrapping,
contract `{"required_fields": [...]}` misread, arrows through boxes/labels on top of each other
unreported, recolour of arrows silently ineffective, new text in a serif font over the footnote,
96-dpi PNG export, TODO check tripped by the template comment, scientific-notation numbers rejected.
Fault injections caught: number absent from the materials, arrow to an unknown id, template TODO left
in place, missing contract field, edit addressed to a missing id, unverified edit, overlap/crossing
geometry, legend colour mismatch, CJK font without glyphs.

## Limits
- The renderer draws boxes, ellipses, arrows, charts, legends, scale bars and embedded raster images;
  it does not draw detailed biological icons. Use `image` elements for provided icons (with source).
- Arrow routing tries a few arcs; crossings it cannot avoid are reported, re-ordering entities is the
  agent's job. Text widths are estimated before drawing and checked after.
- Edit mode works on SVG; text already converted to paths cannot be re-typed (reported by
  `inventory`); PPTX/DOCX sources are previewed via LibreOffice and the limitation is reported.
- Number provenance is string-level: derived values must declare `derived:{formula, inputs}`.
- CJK output needs an installed CJK font; if none covers the characters the render FAILs with
  `cjk_font_missing` and the agent switches to English labels.

## Maintenance
Version 1.1 (2026-11-23, real-paper dogfood fixes). Scripts are stdlib + matplotlib only. To add a figure type, add a template in
`templates/` and a layout note in `references/design_rules.md`; the checkers are type-agnostic.
