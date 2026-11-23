---
name: talk-organizer-evidence-deck
description: "Organise an academic talk (lab meeting, defence, review, conference) from a paper or research materials: a checkable talk goal, audience profile, section time budget, one key message per slide with evidence strength, transitions, cuts with reasons and Q&A preparation with backup slides, delivered as an editable .pptx with speaker-script notes, real PDF/PNG renders, a page-level evidence map, talk_plan.md and a contract-complete result.json. Use whenever a task asks to plan, structure, script or time an academic presentation for a given audience and duration."
---

# Talk organiser: goal → audience → order → key messages → Q&A, all evidence-bound

Goal: a talk that can be given tomorrow: the **order serves the stated audience and goal**, the **slide times add
up to the slot**, every slide leaves **one key message** whose wording matches the **strength of its evidence**,
likely questions have **answers with sources and backup slides**, and every claim traces to the materials.
Deliver an editable `.pptx` (notes = speaker script), PDF/PNG renders, `talk_plan.json/.md`, evidence map, records.

Budget: 64 requests / 900 s / 16k tokens per reply. Target ≈ 16 requests; deliverable after step 5.

```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -f "$SKILL/scripts/deck_build.py" ] || SKILL=$(dirname "$(dirname "$(find / -name deck_build.py -path '*scripts*' 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
```

## 1. Prepare (1 call)
```bash
cat task.md /input/task.md 2>/dev/null | head -150; python3 -c "import json;print(json.dumps(json.load(open('/input/case.json')),ensure_ascii=False,indent=1)[:5000])"
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
```
Write down from task.md: **occasion, audience, duration (minutes), slide limits, template, language**, and the
output_contract field names. The prepare output gives the paper map (sections, figure/table captions with
crops, key sentences), template layouts and tools.

## 2. Read the evidence (2–4 calls)
Key sentences from `/app/work/paper_map.json`, targeted `grep -n` in `/app/work/materials/*.txt`, one look at
`/app/work/figs/fig_candidates.png`. Collect what this audience needs: the problem in their terms, the idea,
2–3 results with numbers, limits, and answers to their likely doubts.

## 3. `/app/work/facts.json` — the evidence table (1 call)
Rows `{"id","kind","text","source":{"file","loc","ref","quote"}}` with **verbatim** quotes (≤ 30 words) and
`[pN:M]` locators (template: `$SKILL/templates/facts_template.json`). Missing items → `"status":"not_in_materials"`.

## 4. `/app/work/talk_plan.json` + `/app/work/spec.json` (2 calls)
Plan (template `$SKILL/templates/talk_plan_template.json`, method `references/talk_patterns.md`):
- `goal` as a checkable outcome; `audience` {who, knows, cares_about, likely_doubts};
- `sections` with minutes (sum = duration), slide ids, key message, `evidence_strength` strong|moderate|weak;
- `qa`: ≥ 3 questions (mechanism, data, controls/baselines, limits, cost) with answer points, fact ids and a
  backup slide id where useful; `cuts`: what was left out / moved to backup and why.
Spec (template `$SKILL/templates/spec_template.json`, fields `references/formats.md`, rules `references/slide_rules.md`):
- every main slide has `time_s` (sum within ±10 % of the duration; backup slides excluded), `key_message`,
  `transition` (to the next slide), `notes` (spoken script, not the bullets), optional `if_asked`;
- titles are claims; ≤ 5 short bullets; figures/tables are the paper's own (`ref` = its numbering, `kind`
  data|schematic); backup slides have `role:"backup"` and are referenced from `qa`;
- wording follows evidence strength ("reduces … (Table 1)" vs "the authors suggest …").

## 5. Build, check, render, look (2–4 calls)
```bash
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json --facts /app/work/facts.json --outdir /app/output \
  --mode talk --plan /app/work/talk_plan.json --duration-min <M> [--max-slides N] [--template /input/<template>]
```
Talk mode adds: goal/audience present, time budget ±10 %, key messages and transitions, ≥ 3 Q&A with valid
fact ids and backup slides, cuts recorded, numbers in the plan's key messages and answers found in the
materials. Outputs `talk_plan.md` (goal, audience, section budget, slide timeline with start times, Q&A, cuts)
plus deck, PDF, PNGs, evidence map. Fix every FAIL, re-run (max twice), look at `previews/contact_sheet.png` once.

## 6. Records and result.json (1–2 calls)
```bash
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
deck_fill copies `talk_plan.json` to the output, writes `version_record.json` and maps contract fields (talk
plan, Q&A, timeline, deck, evidence map, render/version records, uncertainty, status); add unmapped ones with
`--set field=value` — never invent.

## Exception handling
| Situation | Action |
|---|---|
| duration or audience not given | assume and say so: state the assumption in `talk_plan.goal`/uncertainty (e.g. 15 min, peers) |
| too much content for the slot | cut by the goal: move to backup / notes, record in `cuts`; never shrink text below 12 pt |
| slot too long for the material | add a worked example or the method figure detail from the paper — not invented content |
| question the paper cannot answer | answer point "not reported in the paper"; no speculation as fact |
| paper lacks a figure for a claim | use its table or text quote; mark evidence_strength accordingly |
| figure low resolution / formula as image | higher-dpi crop (`paper_map.py crop --dpi 300`); formulas as image crops |
| template conflicts with content (no two-column layout etc.) | builder falls back and records it; keep template fonts/colours |
| font missing / export fails / timeout | see render_report; deliver pptx + partial status; never fake previews |

## Final checklist
1. `/app/output`: `deck.pptx`, `deck.pdf`, `previews/`, `talk_plan.json`, `talk_plan.md`, `evidence_map.json/.md`,
   `check_report.json`, `render_report.json`, `slides_outline.md`, `version_record.json`, `result.json`.
2. Times add up; every main slide has a key message and transition; ≥ 3 Q&A with evidence.
3. No FAIL left (or listed in uncertainty with `status: partial`); `result.json` has every contract field.
