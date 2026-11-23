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
| `SKILL.md` | the procedure the agent follows (budget-aware, 6 steps + exception table) |
| `scripts/extract_materials.py` | one-call inventory/extraction of PDFs (pdftotext → PyMuPDF → pdfplumber → OCR), tables, xlsx, SVG, images; paragraph locators `[p3:12]`; page previews |
| `scripts/ga_check.py` | pre-render audit of the spec against the materials |
| `scripts/ga_render.py` | spec → layered SVG/PDF/PNG with auto font shrink, overflow/out-of-canvas/overlap checks, CJK font pick, `render_report.json`, `layer_map.json` |
| `scripts/svg_edit.py` | `inventory` / `apply` (set_text, set_attr, set_style, translate, delete, add_text) / `export` (rsvg-convert → cairosvg → inkscape → headless Chromium → LibreOffice) |
| `scripts/make_result.py` | writes `result.json` with `case_id` and every `output_contract` field; unfilled fields become `not_provided`, status downgraded honestly |
| `templates/spec_template_ga.json` | 4-panel graphical-abstract layout with placeholders (check fails while any TODO remains) |
| `references/design_rules.md` | visual grammar (one meaning per channel), palette, layouts for other figure types, typography |
| `examples/minimal/` | synthetic example: materials, filled spec, commands (example only, not a formal case result) |

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
→ result). Checks verified on purpose-built faults: a number absent from the materials, an arrow to an
unknown id, a template TODO left in place, a missing contract field (status downgraded to partial),
an edit addressed to a non-existent id (logged as error, file otherwise unchanged), multi-line label
replacement in a matplotlib-generated SVG.

## Limits
- The renderer draws boxes, ellipses, arrows, charts, legends, scale bars and embedded raster images;
  it does not draw detailed biological icons. Use `image` elements for provided icons (with source),
  or keep shapes simple — scientific correctness is preferred over decoration.
- Automatic overflow fixing is global font shrinking plus wrapping; very long labels must be shortened
  by the agent (using source wording).
- Edit mode works on SVG; PPTX/DOCX sources are previewed via LibreOffice and edited only if a tool
  for that format exists in the environment — otherwise the limitation is reported.
- Number provenance is string-level: derived values must declare `derived:{formula, inputs}`.

## Maintenance
Version 1.0 (2026-11). Scripts are stdlib + matplotlib only. To add a figure type, add a template in
`templates/` and a layout note in `references/design_rules.md`; the checkers are type-agnostic.
