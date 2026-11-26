---
name: ppt-source-preserving-editor
description: "Generate or revise an academic slide deck (.pptx) by instruction without rebuilding it: inventory shapes, apply addressed edits (fix numbers, compress/expand/translate/re-target text, insert paper figures or data tables, reorder/split/add backup slides, notes, theme changes) with python-pptx or a byte-preserving stdlib XML engine, keep numbered versions with rollback, a per-op change log with before/after and sources, per-slide text+pixel diffs, number/evidence checks against the paper, template-integrity audit, real PDF/PNG renders and a contract-complete result.json. Use for any PPT edit, revision, iteration or 'update these slides' task, and for generating a first version that will be revised."
---

# Source-preserving deck editor: addressed edits, versions, diffs, evidence

Goal: the requested changes — **and only those** — applied to the given deck, every change **logged with
reason, source and before/after**, every version **kept and reversible**, numbers **checked against the paper**,
the template intact, and real renders of the result.

## Quick path (copy these; details in §1–§6) — budget 64 requests / 900 s, target ≈ 8–12 calls
```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -f "$SKILL/scripts/deck_build.py" ] || SKILL=$(dirname "$(dirname "$(find / -name deck_build.py -path '*scripts*' 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
# 1 task (the numbered instructions) + contract + deck inventory + paper map                      [1 call]
cat /input/task.md 2>/dev/null | head -150; cat /input/case.json | head -c 5000
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
#   step 4 prints [budget] (s used of 900): past 60 % fix only ops_errors/FAILs and finish, past 80 % stop.
# 2 values the instructions need, with locators: python3 $SKILL/scripts/paper_map.py find "..."   [1-3 calls]
#   (new content that must cite the paper: paper_map.py fact "kind::regex[::label]" writes facts.json rows verbatim)
# 3 write /app/work/ops.json: one op per instruction, addressed by slide + shape, with reason/src [1 call]
# 4 ONE command: snapshot v0 -> apply -> render -> diff -> evidence check -> audit -> result.json [1 call]
python3 $SKILL/scripts/deck_edit.py round --pptx /input/<deck>.pptx --ops /app/work/ops.json --outdir /app/output \
  --materials /app/work/materials/*.txt /input/*.csv --note "instructions 1-6" --case /input/case.json
# 5 more instructions or a fix: ops2.json, round --pptx /app/output/deck.pptx ... (v2); roll back with versions/
```
Step 4 always leaves a complete, honest deliverable (edited deck, PDF, previews, `edit_log.json`, versions,
`slide_diff`, `result.json` with every contract field). Unaddressed slides stay byte-identical.


## 1. Prepare (1 call)
```bash
cat task.md /input/task.md 2>/dev/null | head -150; python3 -c "import json;print(json.dumps(json.load(open('/input/case.json')),ensure_ascii=False,indent=1)[:5000])"
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work
```
For a starting deck it prints the **inventory**: every slide with `sld_id`, layout, and each shape's id, name,
kind (placeholder type / textbox / picture / table), font size and text; it also renders the deck
(`/app/work/start_render/previews/contact_sheet.png`). The paper (if any) is mapped as in paper2ppt
(`paper_map.json`, crops of every Fig./Table). Number the instructions in task.md 1…n.

## 2. Find the values you need (1–3 calls)
For every instruction that changes content, find the exact source text: `grep -n` in
`/app/work/materials/*.txt` (numbers must be copied exactly), the figure crop/bbox from paper_map.json, the CSV
for data tables. Look once at the starting deck's contact sheet if layout matters.

## 3. Write `/app/work/ops.json` (1 call)
One op per change, in order (template: `$SKILL/templates/ops_template.json`; cookbook:
`references/edit_rules.md`; full op list in the header of `scripts/deck_edit.py`). Each op gets
`"reason": "instruction k: …"` and, for content, `"src": ["paper.pdf p5:2 Table 1"]`. Address slides by
`slide_id` (from the inventory; stable across moves — every op accepts it) or put text ops before moves/inserts.
Relative image/CSV paths resolve against ops.json's folder, the deck's folder and /input. Typical mappings:
wrong number → `replace_text` (works inside tables); "fonts: Chinese X, Latin Y" → `apply_theme` + `set_font
{slide:"all", font:Y, ea_font:X}` (covers titles, bodies and table cells); "title on one line" → shorter
`set_text` (+ `set_font size` if still 2 lines); new table slide → `add_slide` with `table.rows` taken from
`paper_map.json` `table_rows` (symbols as `ε_HOMO`, `bohr^3`) and `notes`.

## 4. Run the round (1 call) — complete deliverable after this
```bash
python3 $SKILL/scripts/deck_edit.py round --pptx /input/<deck>.pptx --ops /app/work/ops.json --outdir /app/output \
  --materials /app/work/materials/*.txt /input/*.csv --note "instructions 1-6" --case /input/case.json
```
First round snapshots the input as `versions/v0.pptx` (+ renders); then applies the ops with the byte-preserving
XML engine (`--engine pptx` to use python-pptx for text ops) → `/app/output/deck.pptx`
+ `edit_log.json` (per op: status, before/after, parts changed); renders PDF/PNGs; writes `slide_diff.json/.md`
(text diff per shape, moved/added/removed slides, % pixels changed per slide vs the previous version); runs
`deck_check --pptx` (every number on every slide found in the materials, slide text matched to paper paragraphs
→ `evidence_map.json/.md`); audits the master/theme against v0 (`compliance.json`); snapshots `versions/v1.pptx`.
Check the printed summary: `ops_errors` empty, `slides_changed` = the slides you meant, "render: 0 new
failure(s)", check/template `pass`. Findings already present in v0 and theme/font changes the instructions asked
for are reclassified (pre-existing / intended), so only regressions FAIL; style warnings on slides the
instructions do not address are not yours to fix (leave them). Open `previews/contact_sheet.png` once.

## 5. Iterate or roll back (0–2 calls)
Wrong or incomplete? Write `/app/work/ops2.json` and run `round --pptx /app/output/deck.pptx --ops /app/work/ops2.json …`
(→ v2, diff vs v1). A round that broke something: `cp /app/output/versions/v<N-1>.pptx /app/output/deck.pptx`
and redo. No starting deck? Generate v0 first exactly like paper2ppt (facts.json via `paper_map.py fact` + spec.json →
`deck_make.py --outdir /app/work/gen`), then run rounds on `/app/work/gen/deck.pptx`.

## 6. Records and result.json (0–2 calls)
Already written by the round when it has `--case /input/case.json` (it runs the two commands below).
Run them by hand only to change answers: `--set field=value`, `--uncertainty "..."`, or `--fill /app/work/fill.json`
(your values win) on deck_fill, then make_result. `result.json` also carries `verification` (check/render/template
verdicts), `inputs` (sha256) and `reproduce`.
```bash
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
Change-log / version / diff fields get `edit_log.json`, `versions/versions.json` (with rollback commands),
`slide_diff.*`; others get the deck, renders, evidence map, version_record.json. State in `--uncertainty` any
instruction you could not do and why. Add unmapped contract fields with `--set field=value`.

## Exception handling
| Situation | Action |
|---|---|
| running out of requests / time | a complete deliverable is already on disk (safety draft or your last run); run step 4 once with what you have, never start new polishing |
| shape/slide not found (`ops_errors`) | re-read the inventory (`deck_edit.py inventory --pptx …`), fix the address, re-run the round |
| instruction conflicts with the paper (e.g. "change 27.9 to 28") | keep the paper's value, report the conflict in uncertainty |
| number in instruction not in materials | do not insert it unless the task explicitly provides it; flag in uncertainty |
| text overflows after edit (render FAIL) | shorten, or `duplicate_slide` + split; do not shrink below 12 pt |
| picture is a bitmap and values are asked | read only what the paper text/tables state; never estimate from pixels |
| template slots insufficient for a new slide | `add_slide` uses the deck's own layouts (fallbacks recorded); never edit the master |
| new slide should match the deck | `add_slide` keeps the deck's fonts/colours (text in its placeholders, template sizes, large figure); other slides are never restyled |
| CJK text renders as blanks (render FAIL "without any CJK font") | the pptx is fine; report missing CJK font, status partial for previews |
| python-pptx missing | ops run on the stdlib XML engine automatically |
| .ppt / .odp input | deck_prepare converts with LibreOffice to .pptx first; edit the converted copy and say so |

**Status rule.** `complete` = every output_contract deliverable exists and no check is left at FAIL. Caveats you deliberately preserved or cannot fix here — an error in the given source deck/paper, issues that were already in the input and are outside the instructions, a font the preview renderer substitutes — go under `uncertainty`; they do not make the deliverable `partial`. `partial` = a required deliverable is missing or a FAIL remains.

## Final checklist
1. `/app/output`: `deck.pptx`, `deck.pdf`, `previews/`, `edit_log.json`, `slide_diff.json/.md`, `versions/`
   (v0 … vN + versions.json), `evidence_map.*`, `check_report.json`, `compliance.json`, `version_record.json`, `result.json`.
2. Every instruction maps to ≥ 1 logged op (or is explained in uncertainty); no unintended slide in slide_diff.
3. No number on any slide is missing from the materials; template audit pass; `result.json` complete.
