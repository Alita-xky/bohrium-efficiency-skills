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
- Entity type must match the source (`"etype"`): `gene` (label in italics, as convention), `protein`,
  `complex` (double border), `family` (stacked boxes, e.g. "TNFR1/2"), `small_molecule`, `cell`,
  `process`, `phenotype` — never draw a gene where the source means its protein, or a family as one member.
- Step 1 also prints **candidate arrows** (`A → B (activates|inhibits|binds|…)`, each a facts row with its
  verbatim quote) and writes `/app/work/edges.json` (entity ids + arrows citing those rows): paste the arrows whose
  quote really states that direction and sign into the spec, drop or flip the rest, add what is missing with `--fact`.
- Every mechanistic arrow needs `source.quote` stating direction AND sign: activates/promotes (→),
  inhibits (⊣), produces/converts (dashed →, label enzyme or condition), binds (dotted, no
  direction), `modifies` (dash-dot →, e.g. phosphorylation), `catalyzes` (dotted →), `transports`
  (block arrow, translocation between compartments) — each kind used gets a legend sample.
- No compartments: give the panel (or the figure) `"layout":"auto"` and leave out entity x/y/w/h —
  nodes are layered by the arrows (upstream on top, feedback arrows kept) and ordered to avoid crossings;
  `"pin": true` keeps one hand-placed node. `fig_check.py --mode mechanism` fails arrows without a quote. "Modulates/regulates" with
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
v1.13 (2026-11-27: mechanism safety draft — when step 1 found candidate arrows, the background draft now draws a real mechanism figure from them (auto layout, pairs with conflicting signs left out, every arrow citing its verbatim sentence, relation table filled, status partial with the caveat that the arrows were not reviewed), so a run cut short still delivers a figure, not only result.json.)

v1.15 (2026-11-27: (1) one polish round — when the figure is complete but `render_report.json` lists something a reader sees (crossing arrows, an arrow through a box or a label, overlapping labels, a chart cut off), `fig_make.py` prints `POLISH (once)` with those items instead of `DONE`, if less than 55 % of the 900 s budget is used; the next run ends with `DONE`. (2) Charts with error bars say what they are: when neither the chart title nor its caption/note/axis label names SD/SEM/CI, the type read from the error column's name is appended to the chart title ("error bars: SD"); a name that does not tell is reported as a warning (`errbar_note`; `"errbar_note": false` turns it off). (3) Technical-route safety draft — the background draft reads the methods' passive procedure sentences ('Total RNA was extracted …') in text order, one box per operation (up to 7, two per paragraph), each citing its verbatim sentence and [pN:M], snake layout; status partial until the agent's own route replaces it.)

v1.16 (2026-11-27: every run also writes `figure_legend.md` — a journal-style legend generated from the spec: title, one line per panel (A, B, ...) with its entities, the meaning of every arrow kind used (solid arrow = activates, T-bar = inhibits, ...), per chart what is plotted against what, the error-bar column and the data file, and the cited sources with page:line — and `REVIEW.md`, a reviewer's guide: every delivered file and how to open/edit it, three steps to verify (legend, element -> verbatim quote in layer_map.json, glyph_map.csv or reproduce.sh), the colour-vision result, a source table and the uncertainty list. Both are generated from the run's own records, never hand-written.)

v1.17 (2026-11-27: alt text — the SVG carries the figure title (<title>) and a text description (metadata dc:description), the PDF carries Title/Subject/Keywords; the description is built from the spec only: each panel with its entities, every relation as 'A activates B', each chart as 'y by x'. Screen readers and journal submission systems read what the figure shows without the picture; REVIEW.md and result.json (checks.export.alt_text) repeat it.)

v1.14 (2026-11-27: colour-vision safety — every render simulates protanopia, deuteranopia and tritanopia (Machado 2009, CIE76 dE). Chart series colours chosen in the spec that look different but merge for colour-blind readers are replaced by Okabe-Ito colours that stay distinct for everyone (`"cvd_fix": false` on the chart keeps them); inhibition arrows without an explicit colour are drawn in vermillion so they differ from activation by colour as well as by the bar head (`canvas.kind_colors: false` turns this off). `render_report.json` → `colour_vision`, `result.json` → `checks.colour_vision`.)

v1.12 (2026-11-27: mechanism figures — step 1 also reads candidate arrows from the materials: sentences naming two entities around a relation verb become `A → B (activates|inhibits|binds|converts|modifies)` rows in facts.json (verbatim quote + locator) and arrows in `edges.json`; passive voice is reversed, perturbation sentences ('blocking A decreased B') flip the sign, conflicting signs are flagged. A contract field named like a relation table is filled from the spec's arrows (from, to, kind, verbatim evidence). fig_make accepts a plain-string title.)

v1.11 (2026-11-27: shorter runs — the DeepSeek harness allows 900 s, each model call costs 15–30 s, so the quick path targets 5–7 calls. `extract_materials.py` builds the evidence table itself (F1..F10: relations, values, steps, setup, results, claims; verbatim with locators, one row per sentence, number dumps ranked last); step 1 also prints the spec skeleton. `fig_make.py` finds `/app/work/spec.json` (and fill.json) by itself, takes `"fill"` and `"uncertainty"` from the spec, and ends with `NEXT` (round n of 3), `STOP` or `DONE`.)

v1.10 (2026-11-26: evidence by id — `extract_materials.py --fact "kind::regex[::label[::n]]"` appends rows to `facts.json` with the matching sentence copied verbatim (≤ 30 words) and its `[pN:M]` locator (reference list and footnotes rank last, two alternatives printed, `--drop F7` swaps a pick); the spec cites evidence as `"source": "F3"` and `fig_make.py` fills file, loc and quote from facts.json before checking (the filled spec is what is checked, rendered and copied to the output). Typed quotes still work. Test: a mechanism spec with 7 typed quotes replaced by fact ids gives the same check result, 0 FAIL). v1.9.1 (2026-11-26: bar value labels given in e-notation in the source, e.g. `2.3e19`, are drawn as `2.3×10¹⁹` — the source's digits unchanged, only the notation, matching the log axis). v1.9 (2026-11-26: automatic layout repairs in `fig_render.py`, logged in render_report.json `auto_tidy` — boxes that run into a panel title are moved below it (children compressed together, arrangement kept); step cards much taller than their text shrink to fit (same-row cards keep one height); legend colour squares take the fill/edge the matching boxes were actually drawn with and legend arrow samples the colour of their arrow kind; arrow labels try positions along and beside the line and avoid boxes first, other labels second, other lines last). v1.8 (2026-11-26: safety draft — `extract_materials.py` starts `fig_draft.py` in the background, so `/app/output` holds an honest partial deliverable within the first minute (chart: a chart bound to the CSV cells; otherwise result.json with every contract field marked not provided); `fig_make.py` stops the draft and replaces it; `[budget]` lines report the seconds used of the 900 s run budget).
v1.7 (2026-11-25: `"layout":"auto"` layered placement from the arrows, entity types gene/complex/family
drawn by convention, arrow kinds modifies/catalyzes/transports, journal size presets, parameter cards
(v1.6)). v1.5 (2026-11-25: visual quality after a side-by-side test against a figure drawn without the Skill —
filled category nodes with an optional `sub` line, numbered colour headers for problem/method/result/
conclusion panels, real arrow samples in legends, orthogonal elbow routing, opposite pairs as parallel
lines, charts with one shared category axis and legend, highlight bands, visible log-scale bars, value-label
head-room, title and legend kept inside the chart box; `rubric_coverage` in result.json maps each rubric
checkpoint to its evidence file). v1.4 (2026-11-25: chart glyph audit — per-bar/point SVG ids, `glyph_map.json/.csv` with source
line/column, read-back value, tick, legend colour and error bar; clipping and repeated series colours
fail). v1.3 (2026-11-24: built-in vector icon library — standalone `icon` elements and icons inside entities,
membrane/nucleus/cell compartments with receptor snapping, icon checks). v1.2 (2026-11-23: one-command fig_make.py, byte-identical re-runs, contract auto-mapping, measured
gain report, data-path lookup, CJK font discovery). v1.1 (dogfood fixes). stdlib + matplotlib only. Add a layout by adding a template; checkers are
layout-agnostic.
