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
- Draw the biology, not just boxes (design_rules §6): membrane = panel `"shape":"membrane"` with
  receptors/channels as `icon` elements in it (`receptor`, `channel` — they snap across the bilayer,
  binding side up); nucleus = panel `"shape":"nucleus"`; genes `"icon":"dna"`, ligands/hormones
  `molecule` or `peptide`, enzymes/proteins `protein`, cells `cell`, pathogens `virus`/`bacterium`,
  `antibody`, `mitochondrion`. Only when the source states what the entity is and where it sits; an
  entity of unstated type or location stays a plain box. Template: membrane + cytoplasm + nucleus.
- Entity categories (gene, protein, metabolite, small molecule, cell type, process, phenotype) →
  fill/edge colour + legend, one distinct colour per category. Use the source's names and casing
  (TNFα, IL-10, Ca^{2+} → Ca²⁺); properties the source states (e.g. "autocrine, short range") can go
  into the entity label instead of loose text under it.
- Every mechanistic arrow needs `source.quote` stating direction AND sign: activates/promotes (→),
  inhibits (⊣), produces/converts (dashed →, label enzyme or condition), binds (dotted, no
  direction). `fig_check.py --mode mechanism` fails arrows without a quote. "Modulates/regulates" with
  a stated decrease = inhibits; without a sign = associates + label "modulates".
- Do not chain inferences the paper does not state; mark hypothesised links as `associates` with the
  label "proposed" when the source calls them proposed. Say "functional, not necessarily direct" if
  the source does.
- Feedback loops / cross-regulation (A→B and B⊣A) are drawn as two separate arcs automatically.
  Place entities so that long arrows do not have to pass other boxes (signal → integrator → output
  top-to-bottom, regulators at the side); the render ISSUE lines tell you when they do.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | procedure (budget-aware, 6 steps, exception table) |
| `scripts/extract_materials.py` | PDFs in reading order (two-column safe; pdftotext → PyMuPDF → pdfplumber → OCR), one paragraph per line with locator `[p3:12]`, column-preserving `.layout.txt` for tables, caption list, xlsx→CSV, SVG inventory; `--find` prints quotable sentences with locators |
| `scripts/fig_check.py` | audit spec vs materials: provenance, verbatim quotes, number-in-source (handles 2.3e19 / 2.3×10^19 / PDF-flattened 2.3 · 1019 / 10¹⁸, unicode minus, IL-10-style names), legend coverage and colour consistency, units, missing values, icon names / icon provenance / icon object named in the materials, mode rules (`--mode mechanism`) |
| `scripts/fig_render.py` | spec → SVG (Inkscape layer per panel, editable centred text) + PDF + PNG; grouped/horizontal bars from wide or long tables, log axes, n.r. markers, highlights; per-label shrink, CJK/Latin wrapping, glyph-checked CJK font; arrow auto-routing; built-in vector icon library (29 flat icons: biology, lab apparatus, generic; one SVG group each) and membrane / nucleus / cell compartments; geometric ISSUE report (overlaps, crossings, overflow) for text-only agents; `layer_map.json`, `render_report.json` |
| `scripts/svg_edit.py` | inventory (text, colours, bbox) / source-preserving edits (`recolor`, deep `set_style`, centred `set_text`, font-matched `add_text`) with per-op verification and unchanged-id check / export via rsvg, cairosvg, inkscape, Chromium, LibreOffice + pdftoppm |
| `scripts/fig_make.py` | **one command** for the whole deliverable: check → render → byte-identical re-run check → spec copy, `version_record.json`, `reproduce.sh` → contract fields mapped to evidence → `result.json`; edit mode `--edit-svg --ops` (apply + export + verification) |
| `scripts/make_result.py` | `--list-fields` (contract keys from any contract shape); `result.json` with `case_id` + every output_contract field, `verification`, `inputs` (sha256), `reproduce`; broken-path check; honest status |
| `scripts/gain_check.py` | measured A/B on the synthetic example (library defaults vs this Skill, injected defects, edit fidelity, contract shapes) → `gain_report.json` + markdown |
| `references/measured_gain.md` | the output of `gain_check.py` for this Skill (numbers below) |
| `templates/` | layouts: schematic, chart, mechanism, setup (apparatus/route), graphical abstract |
| `references/design_rules.md` | visual grammar, palette, layouts, typography, icon list and when to use icons (§6) |
| `examples/minimal/` | SYNTHETIC example materials + filled spec (interface demo, not a formal case result) |

## Measured gain (synthetic example; reproduce with `python3 scripts/gain_check.py --mode mechanism`)

| measured | A: matplotlib defaults | B: this Skill |
|---|---|---|
| editable `<text>` in SVG | 0 | 31 |
| spec elements addressable by id | 0/15 | 15/15 |
| PDF fonts TrueType-embedded / Type 3 | 0 / 2 | 3 / 0 |
| element→source map, result.json | no, no | yes, yes (6/6 contract fields) |
| re-run byte-identical | False | True |
| injected defects flagged before delivery | 0/8 (1 crashed) | 8/8 |
| edit: untouched elements byte-identical | – | 81/81 (2/2 ops verified) |
| contract shapes fully filled (6 tested) | – | 6/6 |

Full table and limits: `references/measured_gain.md`. This is **not** the platform's baseline-agent
comparison (that is run by the organiser on hidden cases); it shows what the pipeline adds to a general
plotting library on identical input, measured, with the defects it does and does not catch.

## Evidence map for reviewers
| Rubric point | Where to look / how to re-check |
|---|---|
| completion, completeness | `result.json` (`case_id`, every contract field, `status`), SVG/PDF/PNG, `layer_map.json` |
| correctness | `check_report.json`: every printed number found in the materials, verbatim quotes, arrow ends, legend colours, units; charts: `glyph_map.csv` (each bar/point read back from the rendered figure = its CSV cell) |
| verifiability, stability | `version_record.json` (inputs + sha256, tools, export parameters), `reproduce.sh`, `result.json.verification.reproducibility` (two runs, SHA-256 equal) |
| quality / efficiency gain, gain verifiability | `references/measured_gain.md`, `scripts/gain_check.py` (one command, same input for A and B) |
| new capability, method | two-way glyph ↔ data-cell map with read-back verification (charts), spec-bound drawing (each element keeps its id + source), automatic layout checks in words for text-only models, source-preserving SVG edits with per-op verification, CJK font discovery |
| exception handling | SKILL.md exception table; defect rows in `measured_gain.md` (what is flagged, what only warns) |

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
The full pipeline in one call is shown in `examples/minimal/README.md` (`fig_make.py`).

## Validation done
The example runs end to end with one command (`fig_make.py`: extract → check → render → re-run → result)
and in edit mode (inventory → apply → export → result). Dogfooded on real
arXiv papers (q-bio.MN 1607.07244 dendritic-cell signalling; cs.CL 1706.03762 Table 2; 43-page
q-bio 1111.0201 for extraction) with organiser-style cases: graphical abstract with Chinese title,
mechanism redraw of Fig. 3, 10-model grouped chart with missing cells and log axis, and a
source-preserving edit of a generated SVG (rename, recolour inhibition arrows + legend, delete, add note).
Fault injections caught: number absent from the materials, arrow to an unknown id, mechanistic arrow
without quote, chart without data binding, unconnected route stages, template TODO left in place,
missing contract field (status downgraded to partial), edit addressed to a missing id, recolour that
did not take effect (verification), new text overlapping existing text, arrow through a box,
crossing arrows, legend colour not matching its category, CJK font without the glyphs, unknown icon
name, biology/lab icon without a material source, icon of an object no material mentions; charts
(v1.4 glyph audit, 6-chart fault file): bars clipped by `ylim` (2 caught), two series in one colour
(caught), empty cells (7 of 7 listed as n.r.), log-scale bars, horizontal bars, line and scatter points
(all 33 drawn glyphs read back and matched to their CSV line/column).

## Limits
Shapes are boxes/ellipses/arrows/charts/legends/scale bars/embedded images plus a built-in set of 29
flat vector icons and three compartment shapes; anything outside that set (organs, animals, tissue
sections, specific instrument models) must come from provided images with sources or stay a labelled box. Arrow routing tries a few arcs, it is not a full graph-layout engine;
crossings it cannot avoid are reported. Text-width estimates are approximate (checked after drawing).
Edit mode is SVG-native; inventory bboxes ignore transforms other than translate; text converted to
paths cannot be re-typed. Number provenance is string-level; derived values must declare
`derived:{formula,inputs}`. CJK output needs an installed CJK font (reported if absent).

## Maintenance
v1.4 (2026-11-25: chart glyph audit — per-bar/point SVG ids, `glyph_map.json/.csv` with source
line/column, read-back value, tick, legend colour and error bar; clipping and repeated series colours
fail). v1.3 (2026-11-24: built-in vector icon library — standalone `icon` elements and icons inside entities,
membrane/nucleus/cell compartments with receptor snapping, icon checks). v1.2 (2026-11-23: one-command fig_make.py, byte-identical re-runs, contract auto-mapping, measured
gain report, data-path lookup, CJK font discovery). v1.1 (dogfood fixes). stdlib + matplotlib only. Add a layout by adding a template; checkers are
layout-agnostic.
