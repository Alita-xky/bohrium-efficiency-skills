# talk-organizer-evidence-deck — plan and script an academic talk, evidence-bound

## What problem it solves
An academic talk is not a compressed paper. The same study is told differently to a lab meeting, a defence
committee, a review panel or a conference room; audience, minutes, slide count, template and Q&A pressure decide
what goes on a slide, in what order and with what single sentence. General agents typically paste the abstract
and the Results section page by page, ignore the time slot, attach conclusions that the figure does not support,
leave notes empty or identical to the slide, and have nothing prepared for questions. This Skill makes the agent
produce a reviewable **talk plan** and an **evidence-bound deck** in checkable stages:

1. **paper map + evidence table** — sections, captions with crops, key sentences; `facts.json` with verbatim
   quotes and `[page:paragraph]` locators (verified);
2. **talk_plan.json** — checkable goal, audience profile and likely doubts, section time budget, key message +
   evidence strength per section, cuts with reasons, ≥ 3 prepared questions with answer points, fact ids and
   backup slides (`references/talk_patterns.md`: occasion patterns, audience adaptation, strength wording);
3. **spec.json** — one claim per slide with `time_s`, `key_message`, `transition`, `if_asked`, sources;
   speaker notes become a script (key message / transition / time / if asked / evidence lines appended);
4. **checks in talk mode** — time budget ±10 %, key messages and transitions, Q&A references valid, numbers in
   the plan's messages and answers found in the materials, plus all paper checks (quotes, numbers, figure
   numbers, notes, density, order) and render checks (overflow, fonts, min size);
5. **records** — `talk_plan.md` (timeline with start times, Q&A table, cuts), evidence map, version record,
   contract-complete `result.json`.

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
| `templates/` | `talk_plan_template.json`, `spec_template.json` (time/key message/transition fields), `facts_template.json` |
| `references/` | `talk_patterns.md` (goal, occasions, audience, order, evidence strength, Q&A), `slide_rules.md`, `formats.md` |
| `examples/minimal/` | SYNTHETIC example: made-up paper generator, case.json (8-min lab meeting), facts.json, talk_plan.json, spec.json |

## Requirements
Python 3.8+. Used when present (all optional, detected at run time): python-pptx (else stdlib writer),
matplotlib (charts from CSV, contact sheets), Pillow/numpy (crop trimming, pixel diffs), LibreOffice `soffice`
(PDF export), poppler `pdftotext/pdftoppm/pdffonts/pdfinfo` (text, crops, previews, font check) or PyMuPDF,
`fc-match/fc-list` (font availability), tesseract (OCR). No network, no binary templates: the default
academic 16:9 template (master + 6 layouts + theme + notes master) is generated in code.

## Quick start
```bash
SKILL=/path/to/paper2ppt
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
# write /app/work/facts.json and /app/work/spec.json (see templates/ and references/formats.md)
# + /app/work/talk_plan.json
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --outdir /app/output \
   --mode talk --plan /app/work/talk_plan.json --duration-min 15 [--template T.potx]
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```

## How it was validated (synthetic example + purpose-built faults; not formal case results)
- `examples/minimal`: 8-minute lab-meeting talk on a 3-page synthetic paper → 8 main slides + 1 backup, slide
  times 460 s (within ±10 % of 480 s), 3 Q&A entries, 2 cuts; check pass, render ok, `talk_plan.md` with timeline,
  result.json complete with all 7 contract fields (talk_plan, deck, evidence_map, qa_preparation, …).
- Faults detected on purpose: time budget off by 30 %, only 2 Q&A entries, Q&A pointing to a non-existent backup
  slide, unknown fact id, a number in a Q&A answer that is not in the paper, missing goal/audience.
- Toolkit validation shared with the other PPT Skills: both build backends (python-pptx / stdlib) render
  pixel-identically; PowerPoint-made .potx templates are used with real layout inheritance.

## Limits
- Overflow detection is an estimate (wrap simulation with average glyph widths); the renders are the truth —
  look at the contact sheet once.
- Figure regions are found from caption positions and text-free gaps (pdftotext bbox); unusual layouts
  (side captions, multi-panel figures across columns) need a manual bbox.
- Charts are matplotlib images bound to CSV data (data + chart JSON saved next to the deck), not native Office
  charts; tables are native and editable.
- The evidence check is string-level: it proves that quotes and numbers exist in the materials, not that a claim
  is a fair summary — the agent still has to read the cited paragraph.
- Timing is the planned budget (seconds per slide), not a measured rehearsal; evidence-strength labels are the
  agent's judgement following references/talk_patterns.md and are shown for review, not machine-verified.

## Maintenance
Version 1.0 (2026-11). Shared toolkit identical across the four PPT Skills (paper2ppt, talk_organize,
ppt_template, ppt_edit); only SKILL.md, templates, references and examples differ.

## Tested on real papers (dogfooding log, 2026-11)
Run on two arXiv papers as the evaluator would (case.json + task.md + PDF, procedure followed literally): a two-column ICML paper with equations and tables above/below captions, and a single-column NeurIPS paper with scientific-notation tables and an internal number conflict (41.8 vs 41.0 BLEU). Fixed from that run: column-interleaved text extraction, zero crops on PDFs whose math fonts emit control characters, table captions below tables, running heads in crops, garbled formulas (now equation crops), sub/superscripts, exponent checking (2.3 · 10^19 vs 10^18), contract fields such as `editable_deck` mapped to the wrong record, template text boxes/captions/charts in `to_spec`, zero-size layout placeholders, edit rounds that flagged requested font changes and pre-existing issues as failures, and stdlib-only runs without contact sheets or charts.
