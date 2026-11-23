---
name: source-bound-figure-builder
description: Build or edit a scientific figure (graphical abstract, schematic, mechanism/pathway diagram, data chart, apparatus/roadmap figure) strictly from the provided case materials, delivering a layered editable SVG plus rendered PDF/PNG, an element-to-source map, layout/legend/number checks and a contract-complete result.json. Use whenever a task asks to draw, redraw, edit or export a research figure from papers, data files or an existing figure source.
---

# Source-bound figure builder

Goal: a figure in which **every entity, arrow, number, colour and label can be traced to the input
materials**, delivered as an **editable layered SVG** + **PDF/PNG previews**, with an honest
`result.json`. Looks matter less than correctness, traceability and editability.

Budget per case: 64 model requests, 900 s. Follow the steps in order, keep tool calls few and
**save a first complete deliverable by step 5 (≈ request 25)**, then improve.

```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -d "$SKILL/scripts" ] || SKILL=$(dirname "$(dirname "$(find / -name ga_render.py 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
```

## 1. Read the task and the contract (1–2 calls)
`cat /input/case.json task.md` (task.md may be in the working dir or /input). Note: the figure type,
the **output_contract fields** (result.json must contain every one, plus `case_id`), required
formats/sizes/dpi/fonts, and whether this is **create** or **edit** (an existing SVG/PPTX/figure source
is provided → edit mode, step 4b).

## 2. Extract materials with locators (1 call)
```bash
python3 $SKILL/scripts/extract_materials.py --input /input --task task.md --work /app/work
```
Text goes to `/app/work/materials/*.txt` with paragraph tags `[p3:12]` (page 3, paragraph 12).
Tables are listed with columns; PDF pages are rendered to `materials/pages/*.png` so you can look at the
original figures. Read only what you need (`grep -n` for entity names, numbers, "Fig").

## 3. Write `/app/work/facts.json` — the evidence table (no drawing yet)
One row per thing that will appear on the figure:
`{"id":"E1","kind":"entity|relation|number|claim|unit","text":"...","source":{"file":"...","loc":"p3:12","quote":"<= 25 words copied verbatim"}}`
Rules:
- Use the paper's own names and numbers. **Never invent a value, unit, sample size or direction.**
- Relation direction/sign (activates vs inhibits, A→B vs B→A) must be stated in the quote.
- If something the task asks for is not in the materials, add a row with `"status":"not_in_materials"`
  and plan a visible placeholder ("value not reported") instead of a made-up number.
- For a graphical abstract collect the four parts: research **problem**, **method**, key **result**
  (with its numbers), **conclusion**.

## 4a. Create mode — write `/app/work/spec.json`
Start from `$SKILL/templates/spec_template_ga.json` (four panels: problem → method → result →
conclusion; already laid out without overlaps). Other figure types: keep the canvas, replace panels
(see `$SKILL/references/design_rules.md` §Layouts). Spec essentials:
- coordinates are **fractions, top-left origin**, relative to the panel for elements inside a panel;
- element types: `entity` (box/ellipse with label), `text`, `chart` (data from CSV:
  `"data":{"csv":"/input/x.csv","x":"col","y":"col","err":"col"}`), `legend`, `scalebar`, `image`, `group`;
- arrows: `{"id","from","to","kind":"activates|inhibits|flow|produces|converts|binds|next","label"}`;
  arrows may connect entities or whole panels (`"from":"panel:P1"`);
- every scientific element gets `"source":{"file","loc"}` copied from facts.json; purely visual items
  use `"source":{"type":"design"}`;
- colour encodes a category → give entities `"category"` and add a `legend`; use the colour-blind-safe
  palette in design_rules.md; units in axis labels `"Time (h)"`.

## 4b. Edit mode — keep the original source
```bash
python3 $SKILL/scripts/svg_edit.py inventory --svg <given.svg> --out /app/work/inventory.json
```
Write `/app/work/ops.json` with only the requested changes, addressed by existing ids
(`set_text`, `set_attr`, `set_style`, `translate`, `delete`, `add_text`), then
```bash
python3 $SKILL/scripts/svg_edit.py apply --svg <given.svg> --ops /app/work/ops.json \
  --out /app/output/figure_edited.svg --log /app/output/edit_log.json
python3 $SKILL/scripts/svg_edit.py export --svg /app/output/figure_edited.svg \
  --png /app/output/figure_edited.png --pdf /app/output/figure_edited.pdf
```
Never redraw an edit task from scratch: unchanged elements must keep their ids and attributes.
If the given source is not SVG (PPTX/DOCX), convert a copy for preview with
`soffice --headless --convert-to pdf`, edit the original format only if a tool for it exists, and
state the limitation.

## 5. Check, render, look (2–4 calls) — save a deliverable now
```bash
python3 $SKILL/scripts/ga_check.py --spec /app/work/spec.json --materials '/app/work/materials/*.txt' /input/*.csv \
  --mode graphical_abstract --out /app/output/check_report.json
python3 $SKILL/scripts/ga_render.py --spec /app/work/spec.json --outdir /app/output --name graphical_abstract
```
(`--mode` = graphical_abstract | schematic | chart | mechanism | setup; only graphical_abstract
requires the four roles.) Fix every **FAIL** (missing source, unknown arrow endpoint, number not
in materials, text overflow, out of canvas). Read `render_report.json`; open the PNG once to look for
crossing arrows, cramped text or wrong colours. Re-run at most twice — do not polish endlessly.

## 6. result.json (1–2 calls)
Write `/app/work/fill.json` with one key per output_contract field (paths to the SVG/PDF/PNG,
`source_map` = `layer_map.json` path or content, element→source table, checks, edits, uncertainty,
`status`: complete|partial|failed). Then
```bash
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
It copies `case_id`, adds artifact hashes and render checks, and marks unfilled contract fields as
`not_provided` (never fabricate them).

## Exception handling (decide, then state it in result.json)
| Situation | Action |
|---|---|
| key number/unit missing in materials | draw "not reported" placeholder, `status: partial`, list it under uncertainty |
| entity name ambiguous / two sources disagree | use the paper's main-text wording, record both with locators |
| text overflow, font missing, CJK boxes | renderer shrinks fonts (≥6.5 pt) and wraps; if still failing shorten labels using source wording; report font notes |
| canvas too small / size required | set `canvas.width_in/height_in/dpi` from the task; report final pixels |
| export fails | keep the SVG, try `svg_edit.py export`, report which converters were tried |
| PDF without text layer | extract_materials runs OCR; cite OCR text as lower confidence |
| task outside figure work (e.g. statistics, writing) | do only the figure part, say what was out of scope |

## Final checklist (before stopping)
1. `/app/output` has: editable SVG, PDF, PNG, `layer_map.json`, `render_report.json`,
   `check_report.json` (and `edit_log.json` in edit mode), `result.json`.
2. No number on the figure is absent from the materials (or it is marked derived with formula).
3. Every colour/arrow style that carries meaning is in the legend; units on axes and scale bars.
4. `result.json` has `case_id` and every contract field; status honest; limits listed.
