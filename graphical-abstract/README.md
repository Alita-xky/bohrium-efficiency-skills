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
| `SKILL.md` | the procedure: a copy-paste quick path (6–10 calls, one make command), then details, ISSUE→fix table, exception table |
| `scripts/extract_materials.py` | one-call extraction of PDFs in reading order (two-column safe; pdftotext → PyMuPDF → pdfplumber → OCR), one paragraph per line with locator `[p3:12]`, `.layout.txt` for tables, caption list, xlsx→CSV, SVG inventory; `--find` prints quotable sentences with locators |
| `scripts/ga_check.py` | pre-render audit: provenance, verbatim quotes, numbers in source (2.3e19 / 2.3×10^19 / PDF-flattened `2.3 · 1019` / 10¹⁸, unicode minus, IL-10-style names ignored), legend coverage + colour consistency, units, missing values, four-part roles |
| `scripts/ga_render.py` | spec → SVG (one Inkscape layer per panel, centred editable text) + PDF + PNG; per-label shrink, CJK/Latin word-aware wrapping, glyph-checked CJK font; arrow auto-routing (opposite pairs as arcs); charts from wide/long CSV, barh, log axes, n.r. markers, highlights, exact value labels; geometric ISSUE report (overlaps, crossings, overflow) for text-only agents |
| `scripts/svg_edit.py` | `inventory` (text, colours, bbox) / `apply` (set_text keeps centring, recolor, deep set_style, set_attr, translate, delete, font-matched add_text; every op verified, untouched ids compared) / `export` (rsvg-convert → cairosvg → inkscape → Chromium → LibreOffice + pdftoppm at the requested dpi) |
| `scripts/ga_make.py` | **one command** for the whole deliverable: check → render → byte-identical re-run check → spec copy, `version_record.json` (inputs + sha256, tools, export parameters), `reproduce.sh` → every contract field mapped to its evidence → `result.json`; edit mode `--edit-svg --ops` (apply + export + per-op verification) |
| `scripts/make_result.py` | `--list-fields` (contract keys from any contract shape); writes `result.json` with `case_id`, every `output_contract` field, `verification`, `inputs`, `reproduce`; unfilled fields become `not_provided`, broken paths reported, status downgraded honestly |
| `scripts/gain_check.py` | measured A/B on the synthetic example: matplotlib defaults vs this Skill on the same input, one injected defect per run, edit fidelity, 6 contract shapes → `gain_report.json` + markdown |
| `references/measured_gain.md` | the output of `gain_check.py --mode graphical_abstract` (summary below) |
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
python3 $SKILL/scripts/extract_materials.py --input /input --work /app/work
cp $SKILL/templates/spec_template_ga.json /app/work/spec.json   # then fill it from facts.json
python3 $SKILL/scripts/ga_make.py --spec /app/work/spec.json --mode graphical_abstract --name graphical_abstract
# edit task: python3 $SKILL/scripts/ga_make.py --edit-svg /input/<figure>.svg --ops /app/work/ops.json --name figure_edited
```
`ga_make.py` runs ga_check → ga_render → a second render (SHA-256 compared) → records → `result.json`;
the individual scripts can still be run one by one (see SKILL.md).

## Measured gain (synthetic example; reproduce with `python3 scripts/gain_check.py --mode graphical_abstract`)

| measured | A: matplotlib defaults | B: this Skill |
|---|---|---|
| editable `<text>` in SVG | 0 | 39 |
| spec elements addressable by id | 0/17 | 17/17 |
| PDF fonts TrueType-embedded / Type 3 | 0 / 2 | 3 / 0 |
| element→source map, result.json | no, no | yes, yes (6/6 contract fields) |
| re-run byte-identical | False | True |
| injected defects flagged before delivery | 0/8 (1 crashed) | 8/8 |
| edit: untouched elements byte-identical | – | 93/93 (2/2 ops verified) |
| contract shapes fully filled (6 tested) | – | 6/6 |

Full table and limits: `references/measured_gain.md`. This is **not** the platform's baseline-agent
comparison (the organiser runs that on hidden cases); it shows, measured on identical input, what the
pipeline adds to a general plotting library, and which defects it flags.

## Evidence map for reviewers
| Rubric point | Where to look / how to re-check |
|---|---|
| completion, completeness | `result.json` (`case_id`, every contract field, `status`), SVG/PDF/PNG, `layer_map.json`, four roles checked by `ga_check.py` |
| correctness | `check_report.json`: every printed number found in the materials, verbatim quotes (elements and arrows), arrow ends, legend colours, units |
| verifiability, stability | `version_record.json` (inputs + sha256, tools, export parameters), `reproduce.sh`, `result.json.verification.reproducibility` (two runs, SHA-256 equal) |
| quality / efficiency gain, gain verifiability | `references/measured_gain.md`, `scripts/gain_check.py` (one command, same input for A and B) |
| new capability, method | spec-bound drawing (each element keeps its id + source), layout checks reported in words for text-only models, source-preserving SVG edits with per-op verification, CJK font discovery |
| exception handling | SKILL.md exception table; defect rows in `measured_gain.md` |

## How it was validated
The synthetic example in `examples/minimal` runs end to end with one command (`ga_make.py`: extract →
check → render → re-run → result, status `complete`) and in edit mode (inventory → apply → export → result). Dogfooded on real arXiv papers with organiser-style cases (Chinese
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
Version 1.2 (2026-11-23: one-command ga_make.py, byte-identical re-runs, contract auto-mapping,
measured gain report, verbatim quote check on arrows, data-path lookup, CJK font discovery).
Version 1.1 (real-paper dogfood fixes). Scripts are stdlib + matplotlib only. To add a figure type, add a template in
`templates/` and a layout note in `references/design_rules.md`; the checkers are type-agnostic.
