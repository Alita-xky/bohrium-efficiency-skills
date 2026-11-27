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
   numbers, notes, density, order) and render checks (overflow, fonts, min size); since v1.4 also the
   spoken-script length against each slide's time, audience doubts → answering Q&A/slide, and the selection
   ledger (every paper Fig./Table → main slide, backup or recorded cut: `selection_ledger.json`); the Q&A crib
   goes into the speaker notes of the Q&A / last main slide;
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
| `templates/` | `talk_plan_template.json`, `spec_template.json` (time/key message/transition fields), `facts_template.json` |
| `references/` | `talk_patterns.md` (goal, occasions, audience, order, evidence strength, Q&A), `slide_rules.md`, `formats.md` |
| `examples/minimal/` | SYNTHETIC example: made-up paper generator, case.json (8-min lab meeting), facts.json, talk_plan.json, spec.json |

## Measured gain (synthetic example; reproduce with `python3 scripts/gain_check.py`)

| measured | without the Skill | with the Skill |
|---|---|---|
| figure crops / tables placed | 0 / 0 (plain python-pptx script) | 2 / 2 |
| facts with verified verbatim quotes | 0/15 | 15/15 |
| numbers checked against the materials | 0 | 44 |
| rendered PDF pages / PNG previews | – / 0 | 9 / 9 |
| evidence map, result.json | no, no | yes, yes (7/7 contract fields) |
| injected defects flagged before delivery | 0/8 | 8/8 |
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
Version 1.15 (2026-11-27: one polish round — when the deck is complete but a reviewer would notice something, `deck_make.py` prints `POLISH (once)` with those slides instead of `DONE` (only on the first round and with less than 55 % of the 900 s budget used); the next run ends with `DONE`. Layout items in every mode, from render_report.json: text below the minimum size, shrink-on-overflow, overlapping shapes, slides less than 40 % filled. Paper and talk decks also get wording items from check_report.json: topic-label titles, overclaims, notes that repeat the slide, thin or dense slides, missing key messages/transitions, story order, figure references. Template and edit decks are polished in layout only — the source wording is never changed. v1.11 ended every complete run at once, which saved calls but left these visible problems in place.)

Version 1.16 (2026-11-27: every run with `--case` also writes `REVIEW.md`, a reviewer's guide generated from the run's own records: each delivered file and what it is for, a slide-by-slide table (claim title, role, evidence strength, the fact ids with section and page behind it), three steps to verify the deck (rendered PDF and fonts, verbatim quotes in evidence_map.md with the verified/checked counts, editable text and native charts in deck.pptx) and the uncertainty list from result.json. Nothing in it is hand-written, so it always matches the files.)

Version 1.17 (2026-11-27: every run with `--case` also writes `speaker_script.md`: the spoken text of every slide read back from the saved deck.pptx notes, on a clock — start time of each slide, planned seconds (time_s) next to the estimated speaking seconds (~140 words/min English, ~240 characters/min Chinese), slides whose notes run much longer than planned marked, backup slides kept off the clock — so the speaker can rehearse against the time budget from one file. REVIEW.md lists it.)

Version 1.14 (2026-11-27: evidence strength per slide — `deck_check.py` grades every content slide from its own sources: strong = a verbatim quantitative quote found in the paper or a data figure/table on the slide, moderate = verbatim qualitative quote, tentative = the paper itself hedges (may/suggest/preliminary/可能/初步…) or no verified quote. Slide wording stronger than the source (prove, significant without a reported statistic, first, solve, 证明/显著/首次…) is a warning `overclaim` that names the paper's own hedge to use instead. The grade is written to `evidence_map.md/.json`, `check_report.json` → `evidence_strength`, `result.json` → `verification.evidence_strength`, and as a tag on the Evidence line of each slide's speaker notes, e.g. `证据: Table 2 (p.5) [证据强度: 强]`.)

Version 1.13 (2026-11-27: the TALK BUDGET keeps backup slides inside the task's slide limit — main slides + backup slides = at most the maximum, because the checker and a reader count every slide in the file; extra Q&A that does not fit goes into the notes of the last slide. v1.12 suggested 2–3 backup slides on top, which pushed a 10-slide talk to 12 and cost a fix round.)

Version 1.12 (2026-11-27: talk budget and spec in parts — `deck_prepare.py` prints a TALK BUDGET computed from the task's minutes and slide limits (main slides, seconds per slide, minutes and slides per section, notes length at 140 wpm / 240 字/min); the spec may be written as `spec.json` (plan + first slides) plus `spec_part2.json`, `spec_part3.json` … (more slides), which `deck_make.py` and `deck_fill.py` merge in order — smaller writes keep every model reply under the 16k-token limit.)

Version 1.11 (2026-11-27: shorter runs — the DeepSeek harness allows 900 s, and each model call costs 15–30 s, so the quick path now targets 5–8 calls instead of 12–16. `deck_prepare.py` builds the evidence table itself (F1..F11: problem, idea, method, main results, data, cost, limits, claims; each a verbatim sentence with `[pN:M]` and section, never two rows from one sentence, table number dumps ranked last). One file `spec.json` may carry the talk plan (`"plan"`), contract answers (`"fill"`) and open issues (`"uncertainty"`). `deck_make.py` finds spec, facts, plan, case, minutes and a template given in /input by itself and ends with `NEXT` (round n of 3), `STOP` or `DONE`. SKILL.md lists what not to spend calls on (reading scripts, --help, templates, single crops).)

Version 1.10 (2026-11-26: evidence rows by pattern — `paper_map.py fact "kind::regex[::label[::n]]"` copies the matching sentence verbatim (≤ 30 words around the match) into `facts.json` with its `[pN:M]` locator and section heading; the kind steers the search to the fitting section (result → results, method → method, limit → limitations/conclusion), the reference list and author footnotes rank last, two alternatives are printed and `--drop F7` swaps a wrong pick. The agent no longer retypes quotes, so no quote fails the verbatim check and far fewer output tokens go into facts.json. Proxy run, talk case: 39 facts written by pattern, 39/39 verified verbatim, deck complete with 0 FAIL.)

Version 1.9 (2026-11-26: talk mode — `deck_check.py` lists every technical term (acronym, model or metric name) on a main slide with where the spoken script first says and explains it (`audience_terms` in check_report.json and result.json; a term the script never says is a warning unless `audience.knows` covers it), and writes `qa_rehearsal.md`: per likely question the spoken answer with its time, the audience doubt it answers, the slide to jump to and the verbatim evidence with locators). Version 1.8 (2026-11-26: safety draft — `deck_prepare.py` starts `deck_draft.py` in the background, so within the first minute `/app/output` holds a complete, honest partial talk deliverable (deck from the paper map with slide times, key messages and transitions, a minimal talk plan whose Q&A answers are verbatim paper sentences, result.json with status partial); the agent's `deck_make.py` run stops the draft and replaces it; `[budget]` lines report the seconds used of the 900 s run budget; slide labels follow the task language). Version 1.4 (2026-11-25: `rubric_coverage` in result.json (six rubric elements -> evidence), script-length vs slot check, audience-doubt coverage, selection ledger, Q&A crib in
the notes; example notes rewritten as a spoken script). Version 1.3 (2026-11-24: automatic slide design and
deck-wide type scale). Version 1.2 (2026-11-23: one-command `--case` builds/rounds that also write result.json, robust contract
parsing, `verification` block, measured gain report). Version 1.0 (2026-11). Shared toolkit identical across the four PPT Skills (paper2ppt, talk_organize,
ppt_template, ppt_edit); only SKILL.md, templates, references and examples differ.

## Tested on real papers (dogfooding log, 2026-11)
Run on two arXiv papers as the evaluator would (case.json + task.md + PDF, procedure followed literally): a two-column ICML paper with equations and tables above/below captions, and a single-column NeurIPS paper with scientific-notation tables and an internal number conflict (41.8 vs 41.0 BLEU). Fixed from that run: column-interleaved text extraction, zero crops on PDFs whose math fonts emit control characters, table captions below tables, running heads in crops, garbled formulas (now equation crops), sub/superscripts, exponent checking (2.3 · 10^19 vs 10^18), contract fields such as `editable_deck` mapped to the wrong record, template text boxes/captions/charts in `to_spec`, zero-size layout placeholders, edit rounds that flagged requested font changes and pre-existing issues as failures, and stdlib-only runs without contact sheets or charts.
