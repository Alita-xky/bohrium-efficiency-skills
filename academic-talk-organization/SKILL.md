---
name: talk-organizer-evidence-deck
description: "Organise an academic talk (lab meeting, defence, review, conference) from a paper or research materials: a checkable talk goal, audience profile, section time budget, one key message per slide with evidence strength, transitions, cuts with reasons and Q&A preparation with backup slides, checked for script length vs slot, audience-doubt coverage and a selection ledger of every paper figure/table, delivered as an editable .pptx with speaker-script notes, real PDF/PNG renders, a page-level evidence map, talk_plan.md and a contract-complete result.json. Use whenever a task asks to plan, structure, script or time an academic presentation for a given audience and duration."
---

# Talk organiser: goal → audience → order → key messages → Q&A, all evidence-bound

Goal: a talk that can be given tomorrow: the **order serves the stated audience and goal**, the **slide times add
up to the slot**, every slide leaves **one key message** whose wording matches the **strength of its evidence**,
likely questions have **answers with sources and backup slides**, and every claim traces to the materials.
Deliver an editable `.pptx` (notes = speaker script), PDF/PNG renders, `talk_plan.json/.md`, evidence map, records.

## Quick path — budget 64 requests / 900 s, target 5–8 calls (details in §1–§6)
```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -f "$SKILL/scripts/deck_build.py" ] || SKILL=$(dirname "$(dirname "$(find / -name deck_build.py -path '*scripts*' 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
# 1 task + contract + TALK BUDGET (minutes/slides per section, notes length) + paper map + AUTOMATIC
#   EVIDENCE TABLE (F1..F11 copied verbatim, [pN:M] + section)                                      [1 call]
cat /input/task.md 2>/dev/null | head -150; cat /input/case.json | head -c 5000
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
#   (also starts a SAFETY DRAFT: complete deck + result.json, status partial, within ~1 min; step 4 replaces it)
# 2 ONLY if the talk needs a claim/number the table lacks (0-1 call; appends F12.. verbatim; wrong row -> --drop):
python3 $SKILL/scripts/paper_map.py fact "result::28\.4 BLEU::EN-DE BLEU 28.4" "limit::restricted attention" --drop F9
# 3 write /app/work/spec.json = "plan" (start from the printed TALK BUDGET) + slides S1..S5 citing F-ids (+ "fill",
#   "uncertainty"), then /app/work/spec_part2.json = {"slides": [the rest + backup slides]} - two smaller writes,
#   so no single reply runs into the per-reply token limit; step 4 merges them                 [2 calls]
# 4 ONE command - spec, facts, plan, case, minutes and template are found automatically:                [1 call]
python3 $SKILL/scripts/deck_make.py --mode talk        # add --max-slides N / --duration-min M if the task says so
# 5 FAIL lines -> fix only those slides (spec.json / spec_part2.json), run step 4 again; DONE -> end [0-2 calls]
```
spec.json skeleton (`plan` is the talk plan, §4; `notes` = the spoken script for `time_s` seconds, as long as the
budget line says; slides after S5 go into spec_part2.json as `{"slides": [...]}`):
```json
{"plan": {"goal": "what this audience can decide/do after the talk", "occasion": "...", "duration_min": 10,
  "audience": {"who": "...", "knows": "...", "cares_about": "...", "likely_doubts": ["...", "..."]},
  "sections": [{"name": "Problem", "minutes": 2.0, "slides": ["S1", "S2"], "key_message": "...", "evidence_strength": "strong"}],
  "qa": [{"type": "controls", "question": "...", "answer_points": ["..."], "evidence": ["F5"], "backup_slide": "B1", "doubt": 0}],
  "cuts": [{"content": "Table 3 rows C-E", "reason": "beyond a 10-min slot", "ref": "Table 3"}]},
 "slides": [
  {"id": "S1", "role": "title", "title": "...", "subtitle": ["authors · venue"], "sources": ["F11"], "notes": "...", "time_s": 30},
  {"id": "S2", "role": "motivation", "title": "One claim sentence", "bullets": [{"text": "...", "src": ["F1"]}],
   "sources": ["F1"], "key_message": "...", "notes": "...", "time_s": 60, "transition": "..."},
  {"id": "S5", "role": "results", "title": "...", "figure": {"image": "/app/work/figs/tab2_p8.png", "ref": "Table 2",
   "caption": "...", "src": ["F6"]}, "bullets": [{"text": "...", "src": ["F6"]}], "sources": ["F6"], "notes": "...", "time_s": 90},
  {"id": "B1", "role": "backup", "title": "...", "table": {"ref": "Table 3", "caption": "...", "rows": [["..."]], "src": ["F7"]}}],
 "fill": {"limitations": ["..."]}, "uncertainty": ["..."]}
```
**Budget rules** (each model call costs 15–30 s of the 900 s): never read `scripts/*.py` or run `--help` (every
flag you need is above), never `cat` templates/examples (the skeleton below is enough), never rewrite a whole file
to change one slide (edit those slides only), open at most one image (`previews/contact_sheet.png`). The make
command prints `NEXT` (fix those FAILs, max 3 rounds), `POLISH` (once: fix the listed items a reader sees, run the command one more time, then end), `STOP` or `DONE` — after `DONE`, report and end.
Every run leaves a complete, honest deliverable (deck, PDF, previews, evidence map, records, `result.json` with
`case_id`, every contract field and status), so a run cut short still delivers.

## 1. Prepare (1 call)
```bash
cat task.md /input/task.md 2>/dev/null | head -150; python3 -c "import json;print(json.dumps(json.load(open('/input/case.json')),ensure_ascii=False,indent=1)[:5000])"
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
```
Write down from task.md: **occasion, audience, duration (minutes), slide limits, template, language**, and the
output_contract field names. The prepare output gives the paper map (sections, figure/table captions with
crops, key sentences), template layouts and tools.

## 2. Read the evidence (2–4 calls)
- `python3 -c "import json;d=json.load(open('/app/work/paper_map.json'));[print(k['loc'],k['role'],k['text'][:230]) for k in d['key_sentences']];[print(f['ref'],f['page'],f.get('crop_png'),f.get('table_rows')) for f in d['figures_tables']]"`
- `python3 $SKILL/scripts/paper_map.py find "parallel" "limitation" "Table 2" …` → verbatim sentences with `[pN:M]`
  (one pattern per likely question of this audience) - optional: step 3 prints the matches anyway.
- One look at `/app/work/figs/fig_candidates.png` (crops of every Fig./Table; equations are in `paper_map.json`
  → `equations`, crops `figs/eq*.png`).
Collect what this audience needs: the problem in their terms, the idea, 2–3 results with numbers, limits, and
answers to their likely doubts. If the paper states one number twice with different values (abstract vs text),
report both (Q&A + uncertainty) — never pick silently.

## 3. `/app/work/facts.json` — the evidence table (built by prepare; add rows by pattern, 0–1 call)
`deck_prepare.py` already wrote F1..F11 (problem, idea, method, main results, data, cost, limits, claims), each a
verbatim sentence with `[pN:M]` and section. Add only what the deck still needs:
```bash
python3 $SKILL/scripts/paper_map.py fact "problem::long-range dependenc" "method::we propose::core idea" \
  "result::28\.4 BLEU::EN-DE BLEU 28.4" "result::Table 2" "cost::GPU|hours|days" "limit::limitation|future work|fail"
```
Each `kind::regex[::short label[::n]]` appends one row `{"id","kind","text","source":{"file","loc","ref","quote"}}`:
the matching sentence **copied verbatim** (≤ 30 words around the match), its `[pN:M]` locator and section heading;
`kind` steers the search to the fitting section (result → results, method → method, limit → limitations/
conclusion). It prints `F7 result [p8:12] 6.1 Machine Translation :: …` plus up to two alternatives (`::2`, `::3`);
a wrong pick → `--drop F7 "result::…::label::2"` in the next call. Write one spec per claim, number or answer the
talk needs (typically 12–25), cite the printed ids in plan and spec. Re-running appends (ids continue, duplicates
skipped). Only an item the materials do not contain is typed by hand: `{"id","kind","text","status":"not_in_materials"}`.

## 4. Plan + slides in ONE file `/app/work/spec.json` (`"plan"` key; a separate talk_plan.json also works) (1 call)
Plan (template `$SKILL/templates/talk_plan_template.json`, method `references/talk_patterns.md`):
- `goal` as a checkable outcome; `audience` {who, knows, cares_about, likely_doubts};
- `sections` with minutes (sum = duration), slide ids, key message, `evidence_strength` strong|moderate|weak;
- `qa`: ≥ 3 questions (mechanism, data, controls/baselines, limits, cost) with answer points, fact ids, a
  backup slide id where useful and `doubt`: the index (0-based) of the `audience.likely_doubts` entry it answers —
  every likely doubt gets an answer; `cuts`: what was left out / moved to backup and why, with `ref` ("Fig. 4")
  when it is a paper figure/table (the selection ledger lists every Fig./Table: main slide, backup or cut).
Spec (template `$SKILL/templates/spec_template.json`, fields `references/formats.md`, rules `references/slide_rules.md`):
- every main slide has `time_s` (sum within ±10 % of the duration; backup slides excluded), `key_message`,
  `transition` (to the next slide), `notes` (the spoken script, not the bullets: what you say, in full sentences,
  about as long as the slot — ~2.3 English words or ~4 Chinese characters per second; the check warns below 30 %
  or above 150 % of `time_s`), optional `if_asked`;
- titles are claims; ≤ 5 short bullets; figures/tables are the paper's own (`ref` = its numbering, `kind`
  data|schematic); backup slides have `role:"backup"` and are referenced from `qa`;
- wording follows evidence strength ("reduces … (Table 1)" vs "the authors suggest …").
- depth: a content slide without a figure/table needs ≥ 3 points with the paper's numbers, conditions or
  locators (`thin_slide` warning otherwise); CSV charts become editable PowerPoint charts automatically.
- make the structure visible (design is automatic, `references/slide_rules.md` §8): slide 2 `role: outline` with
  the section names as bullets (numbered agenda); if the slide limit allows, a `role: section` divider (title =
  section name, `time_s` 5–10) before each section of a ≥ 12-minute talk. `key_message` on results, analysis and
  conclusion slides is drawn as a takeaway bar (numbers checked): one short "so what" sentence, not the title
  again. Short text slides become cards / big-number callouts; optional `layout` cards | bignumber | quote.
- tables from `table_rows` (check against the crop), equations as their crop (`"ref":"Eq. N"`), symbols with
  `^`/`_` markup (`O(n^2 · d)`, `d_k`, `2.3 · 10^19`); field details in `references/formats.md`.

## 5. Build, check, render, look (2–4 calls)
```bash
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --outdir /app/output \
  --mode talk --plan /app/work/talk_plan.json --duration-min <M> --case /input/case.json [--max-slides N] [--template /input/<template>]
```
Talk mode adds: goal/audience present, time budget ±10 %, key messages and transitions, ≥ 3 Q&A with valid
fact ids and backup slides, cuts recorded, numbers in the plan's key messages and answers found in the
materials, **script length vs slide time** (per slide and whole talk), **audience doubts → answering Q&A/slide**,
and the **selection ledger** (`selection_ledger.json`: every Fig./Table of the paper → main slide, backup or
recorded cut), **audience terms** (an acronym or model/metric name on a main slide that the script never says → warn: explain it in that slide's notes unless `audience.knows` covers it) and **`qa_rehearsal.md`** (per question: the spoken answer with its time, the slide to jump to, the verbatim evidence). The Q&A cue list (question → first answer point, evidence, backup slide) is written into the
speaker notes of the Q&A slide, or of the last main slide, so the presenter has it on stage. Outputs `talk_plan.md` (goal, audience, section budget, slide timeline with start times, Q&A, cuts)
plus deck, PDF, PNGs, evidence map. Fix every FAIL, re-run (max twice), look at `previews/contact_sheet.png` once.

## 6. Records and result.json (0–2 calls)
Already written by the build step when it has `--case /input/case.json` (it runs the two commands below).
Run them by hand only to change answers: `--set field=value`, `--uncertainty "..."`, or `--fill /app/work/fill.json`
(your values win) on deck_fill, then make_result. `result.json` also carries `verification` (check/render/template
verdicts), `inputs` (sha256) and `reproduce`.
```bash
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill_auto.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill_auto.json
```
deck_fill copies `talk_plan.json` to the output, writes `version_record.json` and maps contract fields (talk
plan, Q&A, timeline, deck, evidence map, render/version records, uncertainty, status); add unmapped ones with
`--set field=value` — never invent.

## Exception handling
| Situation | Action |
|---|---|
| running out of requests / time | a complete deliverable is already on disk (safety draft or your last run); run step 4 once with what you have, never start new polishing |
| duration or audience not given | assume and say so: state the assumption in `talk_plan.goal`/uncertainty (e.g. 15 min, peers) |
| too much content for the slot | cut by the goal: move to backup / notes, record in `cuts`; never shrink text below 12 pt |
| slot too long for the material | add a worked example or the method figure detail from the paper — not invented content |
| question the paper cannot answer | answer point "not reported in the paper"; no speculation as fact |
| paper lacks a figure for a claim | use its table or text quote; mark evidence_strength accordingly |
| figure low resolution / formula as image | higher-dpi crop (`paper_map.py crop --dpi 300`); formulas: `equations[]` crops, never retyped from the text layer |
| paper contradicts itself (e.g. abstract 41.8 vs text 41.0) | use the table value on the slide, show both on a backup slide / Q&A, list in uncertainty |
| template conflicts with content (no two-column layout etc.) | builder falls back and records it; keep template fonts/colours |
| font missing / export fails / timeout | see render_report; deliver pptx + partial status; never fake previews |

**Status rule.** `complete` = every output_contract deliverable exists and no check is left at FAIL. Caveats you deliberately preserved or cannot fix here — an error in the given source deck/paper, issues that were already in the input and are outside the instructions, a font the preview renderer substitutes — go under `uncertainty`; they do not make the deliverable `partial`. `partial` = a required deliverable is missing or a FAIL remains.

## Final checklist
1. `/app/output`: `deck.pptx`, `deck.pdf`, `previews/`, `talk_plan.json`, `talk_plan.md`, `evidence_map.json/.md`,
   `check_report.json`, `render_report.json`, `selection_ledger.json`, `slides_outline.md`, `version_record.json`,
   `result.json`.
2. Times add up; every main slide has a key message, transition and a spoken script that fits its time; ≥ 3 Q&A
   with evidence; every likely doubt answered; every paper Fig./Table placed or its cut explained.
3. No FAIL left (or listed in uncertainty with `status: partial`); `result.json` has every contract field.
