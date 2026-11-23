# source-bound-schematic-builder — Skill for「科研示意图生成与编辑」

Part of a family of source-bound figure Skills (same toolkit, task-specific procedure). Workorder
type: `editable_relationship_schematic`.

## Problem it solves
Agents asked to draw research figures from papers/data often produce figures that look fine but
cannot be used: wrong arrow direction or sign, values that appear nowhere in the source, colours
without legend, flat PNGs that cannot be edited, no record of provenance. This Skill makes the agent
(1) collect evidence with page/paragraph locators first, (2) describe the figure as a JSON spec in which
every element carries its source, (3) run automatic audits, and (4) deliver a layered editable SVG +
PDF/PNG + element→source map + contract-complete `result.json`; edit requests change only the
addressed ids of the original SVG and log before/after values.

## Focus: relationship schematics
- Entities = the nouns the source names (samples, groups, components, variables, concepts); relations =
  what the source says connects them. Every relation arrow needs `source.quote`; pick the arrow kind
  from the quote (activates/inhibits/produces/binds/associates/next) — `associates` when the source
  only reports a correlation.
- Group what belongs together (`group` or `panel`), keep reading order left→right / top→bottom,
  ≤ 12 entities per panel, labels ≤ 8 words in the source's terms.
- Colour = category only, with a legend; arrow kinds that carry meaning appear in the legend.
- Edit requests on a provided SVG: never redraw — use `svg_edit.py` and keep all other ids.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | procedure (budget-aware, 6 steps, exception table) |
| `scripts/extract_materials.py` | PDFs (pdftotext → PyMuPDF → pdfplumber → OCR), tables, xlsx→CSV, SVG inventory, images; locators `[p3:12]`; page previews |
| `scripts/fig_check.py` | audit spec vs materials: provenance, quotes, number-in-source, legend, units, mode rules (`--mode schematic`) |
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
python3 $SKILL/scripts/fig_check.py --spec $SKILL/examples/minimal/spec.json --mode schematic \
   --materials "$W/materials/*.txt" $SKILL/examples/minimal/*.csv
python3 $SKILL/scripts/fig_render.py --spec $SKILL/examples/minimal/spec.json --outdir $W/out --name schematic
```
Expected: `{"status": "pass", ...}` then `{"status": "ok", ...}` and `schematic.svg/.pdf/.png` in `$W/out`.

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
