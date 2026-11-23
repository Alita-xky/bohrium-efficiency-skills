---
name: source-value-chart-builder
description: "Create or edit a scientific data chart whose every plotted value comes from the case's own data (CSV/XLSX tables or tables copied from the paper with locators), delivering a layered editable SVG, PDF/PNG previews, a value-to-source map, axis/unit/legend/number checks and a contract-complete result.json. Use for any request to plot, replot, restyle or correct a research chart."
---

# 科研数据图表生成与编辑 — source-bound figure Skill

Goal: a figure in which **every entity, arrow, value, colour and label can be traced to the input
materials**, delivered as an **editable layered SVG** + **PDF/PNG previews**, with an honest
`result.json`. Correctness, traceability and editability matter more than decoration.
Organiser workorder type: `source_value_chart`.

Budget per case: 64 model requests, 900 s. A normal run needs **8–12 tool calls**; the call count per
step is given in brackets. Save a first complete deliverable (step 6) before polishing.

```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -d "$SKILL/scripts" ] || SKILL=$(dirname "$(dirname "$(find / -name fig_render.py 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
```

## Focus: source-value charts
- Plot only values that exist in the materials. Data files → bind the chart to the CSV columns
  directly; a wide table needs no reshaping (`"y":["col_A","col_B"]`, `series_labels`). A table inside
  the PDF → copy its rows from `<file>.layout.txt` into `/app/work/table_<n>.csv` exactly as printed
  (flattened superscripts: `2.3 · 1019` → `2.3e19`) and cite "Table n (p..)"; `fig_check.py` verifies
  every number printed on the figure against text and CSVs.
- Never read values off a raster figure unless the task explicitly asks for digitisation; then label
  them "digitised (±error)" and say so in result.json.
- Choose the chart by the data: categories → bars from 0 (`barh` when > 6 categories or long names);
  time/dose series → `line` (error bars from `err`); two continuous variables → `scatter`; several
  series → grouped (legend above the plot automatically). Orders of magnitude → `yscale:"log"`
  (`xscale` for barh) and say "log scale" in the axis label.
- Empty cells = not reported → drawn as "n.r."; never fill, interpolate or drop the category silently.
  Use `value_labels:true` when exact values matter (the CSV strings are printed unchanged, e.g. 26.30)
  and `highlight` for the rows the task asks to emphasise.
- Axis labels `Quantity (unit)`; state n and the error type (SD/SEM/95% CI) exactly as the source does.
- Edit requests (restyle, fix a value, change units) on a provided SVG: change only what is asked
  with `svg_edit.py` (`recolor`, `set_text`), or re-render from the corrected CSV and log before/after.

## 1. Task, contract, materials [1 call]
```bash
cat /input/case.json /input/task.md task.md 2>/dev/null
python3 $SKILL/scripts/make_result.py --case /input/case.json --list-fields
python3 $SKILL/scripts/extract_materials.py --input /input --work /app/work   # finds task.md itself
```
`--list-fields` prints `case_id` and the exact **output_contract field names** (any contract shape is
understood) — these become the keys of `fill.json` in step 6. Decide **create** or **edit** (a figure
source such as .svg is provided and the task asks to change it → step 4b). Note required size/dpi/
format/language. `extract_materials` writes `/app/work/materials/<file>.txt` (reading order, two-column
PDFs untangled, **one paragraph per line starting with its locator `[p3:12]`**), `<file>.layout.txt`
(column-preserving, for tables), CSV/XLSX summaries, and prints every figure/table caption with its locator.

## 2. Find the evidence [1–2 calls]
```bash
python3 $SKILL/scripts/extract_materials.py --work /app/work --find "inhibits" "3.3 ng" "Table 2"
grep -n -A15 "Table 2" /app/work/materials/*.layout.txt      # tables: rows stay on one line
```
`--find` prints each matching **sentence with its locator** — paste it verbatim as `source.quote`.
PDF text flattens superscripts: `2.3 · 1019` means 2.3×10^19, `ng.mL−1` means ng/mL.

## 3. Evidence table + spec [1 call: write both files]
`/app/work/facts.json`: one row per thing that will appear: `{"id","kind":"entity|relation|value|condition|claim",
"text","source":{"file","loc","quote"}}`. Never invent a value, unit, n, direction or name. Missing
but requested → row with `"status":"not_in_materials"` and a visible "not reported" placeholder.

## 4a. Create mode — `/app/work/spec.json`
Copy `$SKILL/templates/spec_template_chart.json` and replace every TODO (other layouts in the same folder: schematic,
chart, mechanism, setup, ga; a filled example: `$SKILL/examples/minimal/spec.json`). Spec essentials:
- fractions, **top-left origin**, relative to the panel for elements inside a panel;
- types: `entity` (box/ellipse + label, `category` → legend), `text`, `chart`, `legend`, `scalebar`, `image`, `group`;
- arrows `{"id","from","to","kind","label","source"}`, kinds activates|promotes|inhibits|flow|produces|
  converts|binds|associates|next; ends may be entities or whole panels (`"panel:P1"`). Leave out
  `connection`: opposite pairs (A→B, B→A) become two arcs and arrows are routed around other boxes
  automatically; set `"connection":"arc3,rad=0.3"` only to override;
- `chart.data`: `{"csv","x","y","err","group"}`; **`"y"` may be a list of columns** (wide table, one
  series per column, names via `series_labels`) — no reshaping needed; no data file → copy the
  reported numbers into `"values":{"x":[..],"y":[..]}` with a `source.quote` that contains them;
  options `kind` bar|barh|line|scatter (**barh when > 6 categories or long names**), `value_labels`
  (prints the CSV strings exactly), `highlight` [categories], `yscale`/`xscale` "log", `ylim`;
  empty cells are drawn as **"n.r."** (never fill them);
- text notation: `10^18`, `10^{-3}`, `Ca^{2+}`, `CO_{2}` render as real super/subscripts; Greek
  and CJK work (a font with the glyphs is picked automatically);
- every scientific element has `"source":{"file","loc"}` (+`quote` for relations and numbers);
  purely visual items use `"source":{"type":"design"}`;
- legend swatch colour = the `edge` (or `fill`) colour of that category's entities; one colour per category.

## 4b. Edit mode — keep the original source [3–4 calls]
```bash
python3 $SKILL/scripts/svg_edit.py inventory --svg <given.svg> --out /app/work/inventory.json
```
Rows show id, text, colours used inside the element and an approximate bbox (SVG units; canvas size
on the first line). Write `/app/work/ops.json` with only the requested changes, addressed by id:
`set_text` (keeps the block centred), `recolor` (`{"op":"recolor","id":"arrow3","to":"#D55E00"}` —
changes the element's main colour in all its paths; also recolour matching legend handles and labels),
`set_style`, `set_attr`, `translate`, `delete`, `add_text` (`x`,`y` from free space in the bboxes;
font defaults to the figure's font).
```bash
python3 $SKILL/scripts/svg_edit.py apply --svg <given.svg> --ops /app/work/ops.json \
  --out /app/output/figure_edited.svg --log /app/output/edit_log.json
python3 $SKILL/scripts/svg_edit.py export --svg /app/output/figure_edited.svg \
  --png /app/output/figure_edited.png --pdf /app/output/figure_edited.pdf --dpi 300
```
`apply` re-reads its output and marks each op `verified`; anything in `not_applied_or_unverified` or a
`warning` (e.g. new text overlapping) must be fixed and re-applied **from the original file**.
`unchanged_check` proves untouched ids are byte-identical — copy it into result.json. Never redraw an
edit task from scratch. Text already converted to paths (inventory note) cannot be re-typed: delete
that group and `add_text` at its bbox, and say so. Non-SVG sources (PPTX/DOCX/PNG): preview with
`soffice --headless --convert-to pdf`, redraw only what is asked as a new layered SVG, state the limitation.
Edit mode then goes straight to step 6 (steps 3–5 are for new figures).

## 5. Check and render [1 call, repeat at most twice]
```bash
python3 $SKILL/scripts/fig_check.py --spec /app/work/spec.json --mode chart \
  --materials '/app/work/materials/*.txt' /input/*.csv /app/work/*.csv --out /app/output/check_report.json
python3 $SKILL/scripts/fig_render.py --spec /app/work/spec.json --outdir /app/output --name chart
```
**FAIL** must be fixed (missing source/quote, number not in materials, unknown arrow end, text that does
not fit even at 6.5 pt, out of canvas, glyph boxes). The renderer already shrinks only the overflowing
labels, rotates crowded ticks and routes arrows. Its **ISSUE lines describe the picture in words**, so a
model that cannot view images can still fix the layout:

| ISSUE | fix |
|---|---|
| arrow_through_entity / arrows_cross | move one entity (change x/y) or reorder a row; or give the arrow `connection` rad ±0.3 |
| arrow_through_text / text_overlap / label_overlap | move the text element, set the arrow's `label_pos` (0–1), or shorten |
| legend_overlap | move the legend into free space (another panel corner) |
| chart_out_of_canvas / tick_labels_overlap | reduce chart `h`, or use `kind:"barh"` |
| text_overflow (FAIL) | shorten with source wording or enlarge the box |
| glyph_missing / cjk_font_missing | replace the character (`->`, `alpha`), or use English labels and report it |
| quote_not_found (warn) | re-copy the quote from `--find` output |

If you can view images, open the PNG once. Stop after two fix rounds; list what remains under uncertainty.

## 6. result.json [1–2 calls]
Write `/app/work/fill.json` with **one key per contract field from step 1** (paths to SVG/PDF/PNG,
`layer_map.json` for element→source, `check_report.json`/`render_report.json` for checks, edit_log /
unchanged_check, uncertainty incl. every "n.r." and every warn left, `status`: complete|partial|failed), then
```bash
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
It copies `case_id`, adds artifact hashes and render checks, writes `not_provided` for missing fields
(and prints `FIX:` lines naming them and any path that does not exist) — fix and rerun until clean.

## Exception handling (decide, then state it in result.json)
| Situation | Action |
|---|---|
| value/unit/n missing in materials | "n.r."/"not reported" placeholder, `status: partial` only if the task required it, list under uncertainty |
| names ambiguous / sources disagree | main-text wording, record both locators |
| text overflow, font missing, CJK boxes | automatic shrink ≥ 6.5 pt + wrap; then shorten; report `font_notes` |
| size/dpi/format required | set `canvas.width_in/height_in/dpi`; report `png_pixels` |
| export fails | keep the SVG, use `svg_edit.py export` (rsvg → cairosvg → inkscape → chromium → LibreOffice+pdftoppm) and report converters tried |
| PDF without text layer | OCR runs automatically if tesseract exists; cite OCR text as lower confidence |
| request outside figure work | do only the figure part; say what was out of scope |

## Final checklist
1. `/app/output`: editable SVG (one Inkscape layer per panel), PDF, PNG, `layer_map.json`,
   `render_report.json`, `check_report.json` (+ `edit_log.json` in edit mode), `result.json`.
2. No value on the figure is absent from the materials (or it is marked derived with formula).
3. Meaningful colours/arrow kinds are in the legend; units on axes and scale bars; "n.r." explained.
4. `result.json` has `case_id` and every contract field; status honest; limits listed.
