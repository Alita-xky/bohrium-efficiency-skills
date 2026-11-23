# source-bound-mechanism-pathway-builder — Skill for「生物机制图与通路图」

Part of a family of source-bound figure Skills (same toolkit, task-specific procedure). Workorder
type: `biological_mechanism_pathway`.

## Problem it solves
Agents asked to draw research figures from papers/data often produce figures that look fine but
cannot be used: wrong arrow direction or sign, values that appear nowhere in the source, colours
without legend, flat PNGs that cannot be edited, no record of provenance. This Skill makes the agent
(1) collect evidence with page/paragraph locators first, (2) describe the figure as a JSON spec in which
every element carries its source, (3) run automatic audits, and (4) deliver a layered editable SVG +
PDF/PNG + element→source map + contract-complete `result.json`; edit requests change only the
addressed ids of the original SVG and log before/after values.

## Focus: mechanism and pathway figures
- Compartments as panels in the order the biology implies (extracellular → membrane → cytoplasm →
  nucleus, or tissue → cell → organelle); entities inside the compartment the source places them in.
- Entity categories (gene, protein, metabolite, small molecule, cell type, process, phenotype) →
  fill colour + legend. Use the source's names and casing (gene italics are not required).
- Every mechanistic arrow needs `source.quote` stating direction AND sign: activates/promotes (→),
  inhibits (⊣), produces/converts (dashed →, label enzyme or condition), binds (dotted, no
  direction). `fig_check.py --mode mechanism` fails arrows without a quote.
- Do not chain inferences the paper does not state; mark hypothesised links as `associates` with the
  label "proposed" when the source calls them proposed.
- Feedback loops: draw them with `connection: "arc3,rad=0.3"` to avoid overlapping arrows.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | procedure (budget-aware, 6 steps, exception table) |
| `scripts/extract_materials.py` | PDFs (pdftotext → PyMuPDF → pdfplumber → OCR), tables, xlsx→CSV, SVG inventory, images; locators `[p3:12]`; page previews |
| `scripts/fig_check.py` | audit spec vs materials: provenance, quotes, number-in-source, legend, units, mode rules (`--mode mechanism`) |
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
python3 $SKILL/scripts/fig_check.py --spec $SKILL/examples/minimal/spec.json --mode mechanism \
   --materials "$W/materials/*.txt" $SKILL/examples/minimal/*.csv
python3 $SKILL/scripts/fig_render.py --spec $SKILL/examples/minimal/spec.json --outdir $W/out --name mechanism
```
Expected: `{"status": "pass", ...}` then `{"status": "ok", ...}` and `mechanism.svg/.pdf/.png` in `$W/out`.

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
