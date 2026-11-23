# source-bound-apparatus-route-builder — Skill for「实验装置与技术路线图」

Part of a family of source-bound figure Skills (same toolkit, task-specific procedure). Workorder
type: `apparatus_and_technical_route`.

## Problem it solves
Agents asked to draw research figures from papers/data often produce figures that look fine but
cannot be used: wrong arrow direction or sign, values that appear nowhere in the source, colours
without legend, flat PNGs that cannot be edited, no record of provenance. This Skill makes the agent
(1) collect evidence with page/paragraph locators first, (2) describe the figure as a JSON spec in which
every element carries its source, (3) run automatic audits, and (4) deliver a layered editable SVG +
PDF/PNG + element→source map + contract-complete `result.json`; edit requests change only the
addressed ids of the original SVG and log before/after values.

## Focus: apparatus and technical-route figures
- Technical route: stages left→right (or top→bottom) in the order of the methods section, joined by
  `next` arrows; parallel branches as separate rows; decision points as `ellipse` entities.
- Apparatus: components as entities placed in their physical order along the sample/fluid/light/signal
  path, connections as `flow` arrows labelled with what flows; group sub-systems with `group`.
- Conditions (temperature, time, flow rate, wavelength, concentration, model numbers) appear only if
  the source states them, copied with units; otherwise write "not reported" (see example note).
- Dimensions or scale bars only if reported; never imply a scale.
- `fig_check.py --mode setup` fails a route whose stages are not connected in order.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | procedure (budget-aware, 6 steps, exception table) |
| `scripts/extract_materials.py` | PDFs (pdftotext → PyMuPDF → pdfplumber → OCR), tables, xlsx→CSV, SVG inventory, images; locators `[p3:12]`; page previews |
| `scripts/fig_check.py` | audit spec vs materials: provenance, quotes, number-in-source, legend, units, mode rules (`--mode setup`) |
| `scripts/fig_render.py` | spec → layered SVG/PDF/PNG; grouped bars, log axes; overflow/canvas/overlap checks; CJK font pick; `layer_map.json`, `render_report.json` |
| `scripts/svg_edit.py` | inventory / source-preserving edits / export via rsvg, cairosvg, inkscape, Chromium, LibreOffice |
| `scripts/make_result.py` | `result.json` with `case_id` + every output_contract field; honest status |
| `templates/` | layouts: schematic, chart, mechanism, setup (apparatus/route), graphical abstract |
| `references/design_rules.md` | visual grammar, palette, layouts, typography |
| `examples/minimal/` | SYNTHETIC example materials + filled spec (interface demo, not a formal case result) |

## Requirements
Python 3.9+ with matplotlib and numpy. Optional (auto-detected): poppler (`pdftotext`, `pdftoppm`),
`tesseract`, SVG converters, `openpyxl`. No network access.

## Quick start (synthetic example)
```bash
SKILL=$PWD; W=$(mktemp -d)
python3 $SKILL/scripts/extract_materials.py --input $SKILL/examples/minimal --work $W
python3 $SKILL/scripts/fig_check.py --spec $SKILL/examples/minimal/spec.json --mode setup \
   --materials "$W/materials/*.txt" $SKILL/examples/minimal/*.csv
python3 $SKILL/scripts/fig_render.py --spec $SKILL/examples/minimal/spec.json --outdir $W/out --name setup_route
```
Expected: `{"status": "pass", ...}` then `{"status": "ok", ...}` and `setup_route.svg/.pdf/.png` in `$W/out`.

## Validation done
The example runs end to end (extract → check → render → edit → export → result). Fault injections
caught: number absent from the materials, arrow to an unknown id, mechanistic arrow without quote,
chart without data binding, unconnected route stages, template TODO left in place, missing contract
field (status downgraded to partial), edit addressed to a missing id (logged, file otherwise intact),
multi-line label replacement in a matplotlib SVG.

## Limits
Shapes are boxes/ellipses/arrows/charts/legends/scale bars/embedded images — no detailed icon
library (use provided images with sources). Overflow fixing = global font shrink + wrapping. Edit
mode is SVG-native; other formats are previewed and limitations reported. Number provenance is
string-level; derived values must declare `derived:{formula,inputs}`.

## Maintenance
v1.0 (2026-11). stdlib + matplotlib only. Add a layout by adding a template; checkers are
layout-agnostic.
