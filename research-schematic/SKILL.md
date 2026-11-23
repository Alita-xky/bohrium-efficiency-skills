---
name: source-bound-schematic-builder
description: "Create or edit an editable research schematic (entities and the relations between them: study design, components, interactions, workflows) strictly from the case materials, with a layered SVG, PDF/PNG previews, an element-to-source map, relation/legend/number checks and a contract-complete result.json. Use for any request to draw, redraw or modify a scientific schematic or relationship diagram."
---

# 科研示意图生成与编辑 — source-bound figure Skill

Goal: a figure in which **every entity, arrow, value, colour and label can be traced to the input
materials**, delivered as an **editable layered SVG** + **PDF/PNG previews**, with an honest
`result.json`. Correctness, traceability and editability matter more than decoration.
Organiser workorder type: `editable_relationship_schematic`.

Budget per case: 64 model requests, 900 s. Keep tool calls few and **save a first complete
deliverable by step 5 (≈ request 25)**, then improve.

```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -d "$SKILL/scripts" ] || SKILL=$(dirname "$(dirname "$(find / -name fig_render.py 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
```

## Focus: relationship schematics
- Entities = the nouns the source names (samples, groups, components, variables, concepts); relations =
  what the source says connects them. Every relation arrow needs `source.quote`; pick the arrow kind
  from the quote (activates/inhibits/produces/binds/associates/next) — `associates` when the source
  only reports a correlation.
- Group what belongs together (`group` or `panel`), keep reading order left→right / top→bottom,
  ≤ 12 entities per panel, labels ≤ 8 words in the source's terms.
- Colour = category only, with a legend; arrow kinds that carry meaning appear in the legend.
- Edit requests on a provided SVG: never redraw — use `svg_edit.py` and keep all other ids.

## 1. Read the task and the contract (1–2 calls)
`cat /input/case.json task.md` (task.md may be in the working dir or /input). Note the figure type,
the **output_contract fields** (result.json must contain every one, plus `case_id`), required
formats/size/dpi/fonts, and whether this is **create** or **edit** (a figure source is provided →
step 4b).

## 2. Extract materials with locators (1 call)
```bash
python3 $SKILL/scripts/extract_materials.py --input /input --task task.md --work /app/work
```
Text → `/app/work/materials/*.txt` with paragraph tags `[p3:12]`; tables listed with columns (xlsx
sheets exported to CSV); PDF pages rendered to `materials/pages/*.png` to look at original figures.
Read only what you need (`grep -n` for names, numbers, "Fig", "Table").

## 3. Evidence table `/app/work/facts.json` (no drawing yet)
One row per thing that will appear: `{"id","kind":"entity|relation|value|condition|claim","text",
"source":{"file","loc","quote"}}` (quote ≤ 25 words, verbatim). Never invent a value, unit, n,
direction or name. Missing but requested → row with `"status":"not_in_materials"` and a visible
"not reported" placeholder later.

## 4a. Create mode — `/app/work/spec.json`
Start from `$SKILL/templates/spec_template_schematic.json` (laid out without overlaps; other layouts in the same folder:
schematic, chart, mechanism, setup, ga). Spec essentials:
- fractions, **top-left origin**, relative to the panel for elements inside a panel;
- types: `entity` (box/ellipse + label), `text`, `chart` (`"data":{"csv","x","y","err","group"}`),
  `legend`, `scalebar`, `image`, `group`;
- arrows `{"id","from","to","kind","label","source"}`, kinds activates|promotes|inhibits|flow|produces|
  converts|binds|associates|next; arrows may join entities or whole panels (`"panel:P1"`);
- every scientific element has `"source":{"file","loc"}` (+`quote` for relations and numbers);
  purely visual items use `"source":{"type":"design"}`.
A filled synthetic example is in `$SKILL/examples/minimal/spec.json`.

## 4b. Edit mode — keep the original source
```bash
python3 $SKILL/scripts/svg_edit.py inventory --svg <given.svg> --out /app/work/inventory.json
# write /app/work/ops.json: set_text | set_attr | set_style | translate | delete | add_text (by id)
python3 $SKILL/scripts/svg_edit.py apply --svg <given.svg> --ops /app/work/ops.json \
  --out /app/output/figure_edited.svg --log /app/output/edit_log.json
python3 $SKILL/scripts/svg_edit.py export --svg /app/output/figure_edited.svg \
  --png /app/output/figure_edited.png --pdf /app/output/figure_edited.pdf
```
Never redraw an edit task from scratch; untouched ids keep their attributes. Non-SVG sources
(PPTX/DOCX/PNG): preview with `soffice --headless --convert-to pdf`, redraw only what is asked as a
new layered SVG, and state the limitation.

## 5. Check, render, look (2–4 calls) — save a deliverable now
```bash
python3 $SKILL/scripts/fig_check.py --spec /app/work/spec.json --mode schematic \
  --materials '/app/work/materials/*.txt' /input/*.csv /app/work/*.csv --out /app/output/check_report.json
python3 $SKILL/scripts/fig_render.py --spec /app/work/spec.json --outdir /app/output --name schematic
```
Fix every **FAIL** (missing source/quote, number not in materials, unknown arrow endpoint, text
overflow, out of canvas, mode-specific rules). Read `render_report.json`; open the PNG once for
crossing arrows, cramped text or misleading colours. Re-run at most twice.

## 6. result.json (1–2 calls)
Write `/app/work/fill.json` with one key per output_contract field (paths to SVG/PDF/PNG, the
`layer_map.json` element→source table, checks, edit log, uncertainty, `status`: complete|partial|failed),
then
```bash
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
It copies `case_id`, adds artifact hashes and render checks, and marks unfilled contract fields as
`not_provided` (never fabricate them).

## Exception handling (decide, then state it in result.json)
| Situation | Action |
|---|---|
| value/unit/n missing in materials | "not reported" placeholder, `status: partial`, list under uncertainty |
| names ambiguous / sources disagree | main-text wording, record both locators |
| text overflow, font missing, CJK boxes | renderer shrinks (≥ 6.5 pt) and wraps; shorten with source wording; report font notes |
| size/dpi/format required | set `canvas.width_in/height_in/dpi`; report final pixels |
| export fails | keep the SVG, try `svg_edit.py export`, report converters tried |
| PDF without text layer | OCR runs automatically; cite OCR text as lower confidence |
| request outside figure work | do only the figure part; say what was out of scope |

## Final checklist
1. `/app/output`: editable SVG, PDF, PNG, `layer_map.json`, `render_report.json`, `check_report.json`
   (+ `edit_log.json` in edit mode), `result.json`.
2. No value on the figure is absent from the materials (or it is marked derived with formula).
3. Meaningful colours/arrow kinds are in the legend; units on axes and scale bars.
4. `result.json` has `case_id` and every contract field; status honest; limits listed.
