---
name: paper2ppt-evidence-deck
description: "Turn a research paper (PDF + optional data/template) into an editable academic slide deck (.pptx) whose every slide is a claim tied to the paper's own figures, tables, sections and numbers, with speaker notes, real LibreOffice PDF/PNG renders, a page-level evidence map (verbatim quotes with page:paragraph locators), automatic number/figure/overflow/font checks and a contract-complete result.json. Use for any paper-to-slides, paper-to-PPT, journal-club or conference-talk deck task."
---

# Paper → evidence-bound academic deck

Goal: a deck whose **main line follows the paper's contributions**, where **every slide can be traced** to a
figure, table, section or data row of the paper, delivered as an **editable .pptx** (template placeholders,
speaker notes) + **PDF/PNG renders**, an **evidence map** and an honest `result.json`.
Correctness and traceability beat decoration.

## Quick path (copy these; details in §1–§6) — budget 64 requests / 900 s, target ≈ 12–15 calls
```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -f "$SKILL/scripts/deck_build.py" ] || SKILL=$(dirname "$(dirname "$(find / -name deck_build.py -path '*scripts*' 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
# 1 task + contract + paper map (text with [pN:M] locators, figure/table crops, tables, equations)   [1 call]
cat /input/task.md 2>/dev/null | head -150; cat /input/case.json | head -c 5000
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
# 2 verbatim quotes with locators                                                                  [1-3 calls]
python3 $SKILL/scripts/paper_map.py find "we propose" "outperform" "limitation" "Table 2"
# 3 write /app/work/facts.json and /app/work/spec.json (templates/; rules in references/)            [2-3 calls]
# 4 ONE command: check -> build -> LibreOffice render -> records -> result.json                     [1 call]
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --mode paper \
  --max-slides 15 --case /input/case.json [--template /input/<template>.pptx]
# 5 fix every FAIL in the spec, run step 4 again (max 2 rounds); open previews/contact_sheet.png once
```
Step 4 always leaves a complete, honest deliverable: `deck.pptx`, `deck.pdf`, `previews/`, `evidence_map`,
`version_record.json`, `result.json` (`case_id`, every contract field, status). Task-specific answers go in
`/app/work/fill.json` keyed by contract field → add `--fill /app/work/fill.json` to step 4.


## 1. Prepare (1 call)
```bash
cat task.md /input/task.md 2>/dev/null | head -150; python3 -c "import json;print(json.dumps(json.load(open('/input/case.json')),ensure_ascii=False,indent=1)[:5000])"
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
```
It extracts all text in reading order with paragraph locators (`materials/*.txt`, tags `[p3:12]`; two-column
papers come out column by column; `materials/layout/*.layout.txt` keeps table rows aligned), maps the paper
(`paper_map.json`: sections with roles, every Figure/Table caption with page + crop box + a ready PNG crop,
**table cells** `table_rows`, numbered display **equations** with crops, where each figure is discussed, 40 key
sentences), inspects a template (`template_report.json`) and a starting deck, and lists tools (python-pptx or
the stdlib writer is chosen automatically). Note from task.md: slide limits, minutes, audience, language,
template, and the **output_contract** field names.

## 2. Read the evidence (2–4 calls)
- Key sentences + tables in one call:
  `python3 -c "import json;d=json.load(open('/app/work/paper_map.json'));[print(k['loc'],k['role'],k['text'][:230]) for k in d['key_sentences']];[print(f['ref'],f['page'],f.get('crop_png'),f.get('table_rows')) for f in d['figures_tables']]"`
- Verbatim quotes with locators: `python3 $SKILL/scripts/paper_map.py find "we propose" "outperform" "limitation" "Table 2"`
  (prints `[pN:M] file :: sentence` — paste these into facts.json).
- Look once at `/app/work/figs/fig_candidates.png` (red boxes = auto crops of each Fig./Table). If a box is
  wrong, fix it in the spec (`bbox` page fractions) or with `paper_map.py crop`.
Fill the five slots of `references/storyline.md` (problem, gap/idea, method, evidence, limits+take-home).

## 3. Write `/app/work/facts.json` — the evidence table (1 call)
Start from `$SKILL/templates/facts_template.json`. One row per fact that will appear on a slide:
`{"id","kind","text","source":{"file","loc","ref","quote"}}`. **Copy quotes verbatim** (≤ 30 words) from
`materials/*.txt`; `ref` = "Fig. 3" / "Table 2" / "Sec. 4.1" / "Abstract". A requested item that the paper
does not contain gets `"status":"not_in_materials"` — it is reported, never invented.

## 4. Write `/app/work/spec.json` (1–2 calls)
Start from `$SKILL/templates/spec_template.json`; full field list in `references/formats.md`; rules in
`references/slide_rules.md`. Essentials:
- 10–15 slides unless the task fixes the count; roles in story order: title → motivation → method → (setup) →
  results → limitations → conclusion (→ backup). Titles are **claims**, not topics.
- Each slide: `sources` (fact ids) and per-bullet `src`; ≤ 5 short bullets; `notes` = what to say that is not
  on the slide + transition (never the bullets again).
- Paper figures: `"figure":{"image":"<crop_png from paper_map>","ref":"Fig. N","caption":"<first sentence, verbatim>","kind":"data|schematic","src":[...]}`
  (or `"pdf"`+`"page"`+`"bbox"`). Keep the paper's numbering. Tables: start from `table_rows` (check against the
  crop; keep the rows/columns the claim needs) into `"table":{"rows":…}`, or show the table crop. Equations: the
  crop `figs/eqA_B_pP.png` as a figure with `"ref":"Eq. N"` — never retype math from the text layer. Charts only
  from input CSV/XLSX (`"chart"`), labelled "redrawn from data"; bar/line charts become **editable PowerPoint
  charts** (data in the embedded workbook), charts with error bars or log axes are drawn as images.
- Depth: every content slide carries the paper's concrete evidence — ≥ 3 points with numbers and units,
  conditions or Sec./Table locators, or a figure/table with 1–3 callouts. `deck_check` warns `thin_slide` on a
  content slide with fewer than 3 short points and no visual: add evidence or merge it.
- Symbols: `ε_HOMO`, `x^2`, `2.3 · 10^19` render as real sub/superscripts (`\_` = literal underscore).
- Numbers exactly as in the paper (precision, unit). Computed values → `derived` with formula.
- Chinese task → Chinese slide text, `meta.lang:"zh"`, technical terms/numbers unchanged.
- Long spec? Write slides 1–7 in spec.json and the rest in spec2.json (`--spec` twice).
- Design is automatic (`references/slide_rules.md` §8, sizes in `type_scale.json`): 1–4 short bullets become
  cards, a results bullet holding one number with a unit or % (27.9%, 3.5×) a big-number card, figures get ≥ 55 %
  of the width, bars start at 0 with the paper's method highlighted. So: one idea and at most one key number per
  bullet, "Head: detail" phrasing.
  Optional: `layout` cards | bignumber | quote | two_column, `role` outline (numbered agenda) / section
  (divider), `takeaway` (one-sentence bottom bar; its numbers are checked) on results and conclusion slides.

## 5. Build, check, render, look (2–4 calls) — a full deliverable exists after the first run
```bash
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --outdir /app/output \
  --mode paper --max-slides 15 --case /input/case.json [--template /input/<template>.pptx|.potx]
```
(`--materials` defaults to `/app/work/materials/*.txt` + input CSVs; `--paper-map` to `/app/work/paper_map.json`.)
It runs deck_check (quotes verbatim, locators, every number in the materials, Fig./Table numbers exist,
sources, story order, density, notes) → deck_build → deck_render (LibreOffice PDF, PNG per slide,
`previews/contact_sheet.png`, overflow / < 12 pt / off-slide / stretched picture / fonts embedded) → template
audit. Fix every **FAIL** in the spec and re-run (max twice). Open `previews/contact_sheet.png` once and check:
crops show the right figure, nothing overflows, titles fit. Do not polish endlessly.

## 6. Records and result.json (0–2 calls)
Already written by the build step when it has `--case /input/case.json` (it runs the two commands below).
Run them by hand only to change answers: `--set field=value`, `--uncertainty "..."`, or `--fill /app/work/fill.json`
(your values win) on deck_fill, then make_result. `result.json` also carries `verification` (check/render/template
verdicts), `inputs` (sha256) and `reproduce`.
```bash
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json \
  [--uncertainty "Fig. 5 is a bitmap: values not re-checked"] [--set field=value]
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
deck_fill writes `version_record.json` (inputs + sha256, deck/PDF/PNGs, template, fonts requested vs embedded,
tools, backend, re-run command) and maps every output_contract field to the produced files
(deck, evidence map, render/version records, notes, uncertainty, status); unmapped fields are listed —
add them with `--set`, otherwise make_result marks them `not_provided` (never fabricated).

## Exception handling (decide, then state it in result.json → uncertainty)
| Situation | Action |
|---|---|
| PDF has no text layer / scanned | extract_materials OCRs (tesseract); cite OCR text as lower confidence; check numbers against page images |
| PDF encrypted / unreadable | try `qpdf --decrypt in.pdf out.pdf`; else build from other materials, `status: partial` |
| figure crop wrong / caption not found | set `bbox` by hand (look at the page PNG) or `paper_map.py crop`; whole page as last resort |
| formula only as image / garbled in text | use the `equations[]` crop (or `paper_map.py crop`); never retype it from the text layer |
| table cells look wrong (merged/missing cells) | `table_rows` is a candidate: compare with the crop, fix cells by hand, or show the crop (`kind: table_image`) |
| two-column paper, quote "not found" | quote from `paper_map.py find` output (reading-order text); deck_check suggests the right `[pN:M]` |
| value requested but not in paper | fact with `not_in_materials`; slide says "not reported"; listed in uncertainty |
| text overflow FAIL | shorten to phrases / split slide; never below 12 pt body |
| table too large | keep key rows/columns (say so in caption) or show the paper's table crop |
| slide limit exceeded | merge minor results, move details to notes; drop backup first |
| template lacks a layout / placeholders | builder falls back (recorded in build_report); never draw fake title boxes |
| font missing | render_report shows requested vs embedded fonts; keep template fonts, note substitution |
| python-pptx missing / fails | stdlib OOXML writer is used automatically (same XML, same render); contact sheets and crop overviews are drawn without PIL/matplotlib |
| soffice missing / export fails | deliver the .pptx, status `partial`, report the missing renderer; no fake previews |
| running out of time | run deck_make + deck_fill + make_result with what exists (`status: partial`) |

**Status rule.** `complete` = every output_contract deliverable exists and no check is left at FAIL. Caveats you deliberately preserved or cannot fix here — an error in the given source deck/paper, issues that were already in the input and are outside the instructions, a font the preview renderer substitutes — go under `uncertainty`; they do not make the deliverable `partial`. `partial` = a required deliverable is missing or a FAIL remains.

## Final checklist
1. `/app/output`: `deck.pptx`, `deck.pdf`, `previews/*.png`, `evidence_map.json/.md`, `check_report.json`,
   `render_report.json`, `build_report.json`, `slides_outline.md`, `version_record.json`, `result.json`.
2. check_report has no FAIL (or every remaining FAIL is listed in uncertainty with status `partial`).
3. Every Fig./Table number on a slide is the paper's own; every number is in the materials.
4. `result.json` has `case_id` and every contract field; status honest.
