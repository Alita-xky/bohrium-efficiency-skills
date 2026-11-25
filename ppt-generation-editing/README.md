# ppt-source-preserving-editor — revise academic decks by instruction, with versions and evidence

## What problem it solves
Slides are revised many times (group meeting → proposal → defence → review). Agents typically regenerate the
whole deck for a one-line request, round or mistype numbers, overwrite template fonts and colours, break
the master while editing one slide, and leave no record of what changed or how to go back. This Skill turns
an edit request into an auditable round:

1. **inventory** — every slide (`sld_id`, layout) and shape (id, name, placeholder type, font size, text, notes);
2. **addressed ops** — `replace_text` (run-level, formatting kept), `set_text` (paragraph/run formatting reused
   per level), `set_notes`, `set_font`, `replace_image` (aspect kept), `add_slide` (built on the deck's own
   layouts from a deck_build slide spec), `duplicate/move/delete/reorder`, `apply_theme`, `move/delete_shape`;
   python-pptx for text ops when present, otherwise (and for all structural ops) a stdlib XML engine that copies
   untouched package parts byte-for-byte;
3. **one-call round** — snapshot v0 → apply → render (PDF/PNGs) → slide diff vs the previous version (text per
   shape, moved/added/removed slides, % pixels changed) → number/evidence check of every slide against the paper
   → template-integrity audit vs v0 → snapshot vN; rollback = copy the previous version;
4. **records** — `edit_log.json` (per op: reason, source, before/after, parts changed/unchanged),
   `versions/versions.json`, `slide_diff.md`, `evidence_map.md`, `version_record.json`, contract-complete result.json.

## Files
| Path | Purpose |
|---|---|
| `SKILL.md` | the 6-step procedure (budget-aware) + exception table |
| `scripts/deck_prepare.py` | one-call setup: classify inputs, extract text, paper map, template/deck inventory, tools, fonts |
| `scripts/extract_materials.py` | text extraction with `[pN:M]` locators (pdftotext → PyMuPDF → pdfplumber → OCR), tables, page previews |
| `scripts/paper_map.py` | sections (two-column aware), captions, figure/table crops (caption above or below, running heads excluded), table cells `table_rows` (super/subscripts kept), numbered display-equation crops, key sentences, overview image (also without PIL/matplotlib); `crop` and `find` (verbatim sentences + `[pN:M]`) sub-commands |
| `scripts/deck_build.py` | spec → .pptx (python-pptx or stdlib backend), font fitting ladder, charts from CSV, tables, figures, notes |
| `scripts/deck_design.py` | type scale (one title size, 3-step body ladder → `type_scale.json`) + layout engine: cards, big-number callouts, agenda, section numbers, quote, takeaway bar |
| `scripts/deck_render.py` | .pptx → PDF → PNGs + contact sheet; layout and font checks |
| `scripts/deck_check.py` | spec (or existing .pptx) vs materials audit; writes the evidence map |
| `scripts/deck_make.py` | check → build → render (→ template audit) in one call; `--case` also writes records + result.json |
| `scripts/deck_edit.py` | inventory / source-preserving edits / snapshots / slide diff (for revisions) |
| `scripts/deck_template.py` | template inspect (+ layout gallery), audit, fix, deck→spec, content preservation |
| `scripts/deck_fill.py` | version_record.json + contract mapping for result.json (`--fill` merges your answers, `--result` runs make_result) |
| `scripts/make_result.py` | contract-complete result.json (any contract shape) with `verification`, `inputs`, `reproduce` |
| `scripts/gain_check.py` | measured comparison on the synthetic example (plain python-pptx vs this Skill, injected defects, contract shapes) |
| `references/measured_gain.md` | the output of `gain_check.py` for this Skill (summary below) |
| `scripts/pptkit.py` | shared stdlib PresentationML library (package I/O, default template, catalog, inventory, XML builders) |
| `templates/` | `ops_template.json` (all ops with reason/src), `spec_template.json` + `facts_template.json` (generate-then-edit) |
| `references/` | `edit_rules.md` (rules, instruction → ops cookbook, records per round), `slide_rules.md`, `formats.md` |
| `examples/minimal/` | SYNTHETIC example: generator for a draft deck with known problems + paper, case.json (6 instructions), ops.json |

## Measured gain (synthetic example; reproduce with `python3 scripts/gain_check.py`)

| measured | without the Skill | with the Skill |
|---|---|---|
| same text edit: package parts byte-identical | 51/52 (python-pptx open/save) | 51/52 |
| version snapshots, re-render, evidence check | 0, no, no | 2, ok, pass |
| injected edit defects flagged | 0/2 | 2/2 |
| contract shapes fully filled | – | 6/6 |

Full tables and limits: `references/measured_gain.md`. Not the platform's baseline-agent comparison (the organiser
runs that on hidden cases); it shows, measured on identical input, what the scripts add and which defects they flag.

## Evidence map for reviewers
| Rubric point | Where to look / how to re-check |
|---|---|
| completion, completeness | `result.json` (`case_id`, every contract field, `status`), `deck.pptx`, `deck.pdf`, `previews/` |
| correctness | `check_report.json` + `evidence_map.json/.md`: verbatim quotes, every number in the materials, Fig./Table numbers exist |
| verifiability | `version_record.json` (inputs + sha256, tools, fonts requested vs embedded, re-run command), `result.json.verification` |
| quality / efficiency gain, gain verifiability | `references/measured_gain.md`, `scripts/gain_check.py` (one command, same input for both sides) |
| exception handling | SKILL.md exception table; defect rows in `measured_gain.md` |

## Requirements
Python 3.8+. Used when present (all optional, detected at run time): python-pptx (else stdlib writer),
matplotlib (charts from CSV, contact sheets), Pillow/numpy (crop trimming, pixel diffs), LibreOffice `soffice`
(PDF export), poppler `pdftotext/pdftoppm/pdffonts/pdfinfo` (text, crops, previews, font check) or PyMuPDF,
`fc-match/fc-list` (font availability), tesseract (OCR). No network, no binary templates: the default
academic 16:9 template (master + 6 layouts + theme + notes master) is generated in code.

## Quick start
```bash
SKILL=/path/to/paper2ppt
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work            # prints the deck inventory
# write /app/work/ops.json (templates/ops_template.json)
python3 $SKILL/scripts/deck_edit.py round --pptx /input/deck.pptx --ops /app/work/ops.json --outdir /app/output \
   --materials /app/work/materials/*.txt /input/*.csv --note "instructions 1-6"
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```

## How it was validated (synthetic example + purpose-built faults; not formal case results)
- `examples/minimal`: 6-slide draft with a wrong number (28.1 % vs Table 1's 27.9 %), 5 long bullets, missing
  notes and conclusion-before-limitations; 6 instructions → 6 ops in one round (≈ 4 s + LibreOffice): 0 op errors,
  7 package parts changed / 45 byte-identical with the default XML engine (12 / 40 with `--engine pptx`), slide_diff shows exactly the addressed slides (5 changed incl. the
  moved one, 1 unchanged, 1 added backup), check pass (the draft's 28.1 % fails the same check), template audit
  pass vs v0, versions v0/v1 with rollback, Chinese title rendered with an embedded CJK font.
- Both engines (python-pptx / stdlib) give pixel-identical renders for the same ops; in a venv without
  python-pptx all ops run on the stdlib engine.
- Faults handled: shape name not on the slide (op error logged, deck otherwise written), colour that is not hex
  or a theme name, CJK text on a renderer without CJK fonts (render FAIL "without any CJK font").

## Limits
- Overflow detection is an estimate (wrap simulation with average glyph widths); the renders are the truth —
  look at the contact sheet once.
- Figure regions are found from caption positions and text-free gaps (pdftotext bbox); unusual layouts
  (side captions, multi-panel figures across columns) need a manual bbox.
- Ops address placeholders, text boxes, pictures and tables; text inside grouped shapes, SmartArt and native
  charts can be found by `replace_text` only when it is plain DrawingML text; chart data is not edited.
- `set_text` rebuilds the paragraphs of one shape with the formatting of its old paragraphs per level; mixed
  inline formatting inside a paragraph is reduced to the first run's format (logged before/after).
- The evidence check is string-level: it proves that quotes and numbers exist in the materials, not that a claim
  is a fair summary — the agent still has to read the cited paragraph.

## Maintenance
Version 1.4 (2026-11-25: `result.json` carries `rubric_coverage` — the six rubric elements (storyline, slide structure, figure/table references, speaking logic, template constraints, export/rendering), each with a verdict from this run's own reports and the file that proves it). Version 1.3 (2026-11-24: automatic slide design and deck-wide type scale). Version 1.2 (2026-11-23: one-command `--case` builds/rounds that also write result.json, robust contract
parsing, `verification` block, measured gain report). Version 1.0 (2026-11). Shared toolkit identical across the four PPT Skills (paper2ppt, talk_organize,
ppt_template, ppt_edit); only SKILL.md, templates, references and examples differ.

## Tested on real papers (dogfooding log, 2026-11)
Run on two arXiv papers as the evaluator would (case.json + task.md + PDF, procedure followed literally): a two-column ICML paper with equations and tables above/below captions, and a single-column NeurIPS paper with scientific-notation tables and an internal number conflict (41.8 vs 41.0 BLEU). Fixed from that run: column-interleaved text extraction, zero crops on PDFs whose math fonts emit control characters, table captions below tables, running heads in crops, garbled formulas (now equation crops), sub/superscripts, exponent checking (2.3 · 10^19 vs 10^18), contract fields such as `editable_deck` mapped to the wrong record, template text boxes/captions/charts in `to_spec`, zero-size layout placeholders, edit rounds that flagged requested font changes and pre-existing issues as failures, and stdlib-only runs without contact sheets or charts.
