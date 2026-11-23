# File formats (facts.json, spec.json, talk_plan.json, ops.json, outputs)

All paths may be absolute or relative to the JSON file's folder. Text is UTF-8; Chinese and English both work.

## facts.json — the evidence table (write it BEFORE the slides)

```json
{"facts": [
  {"id": "F1", "kind": "claim|number|method|result|limitation|context|meta|data",
   "text": "short note in your own words (not shown on slides)",
   "source": {"file": "paper.pdf", "loc": "p3:12", "ref": "Fig. 2 | Table 1 | Sec. 4.2 | Abstract",
              "quote": "verbatim words copied from materials/paper.pdf.txt (<= 30 words)"}},
  {"id": "F9", "kind": "number", "text": "dataset size not reported", "status": "not_in_materials"}
]}
```
- `loc` is the paragraph tag printed by extract_materials (`[p3:12]` = page 3, paragraph 12). For CSV data use `row0`, `row3`.
- `quote` must be copied character for character: `python3 $SKILL/scripts/paper_map.py find "keyword" "regex2"` prints
  matching sentences with their `[pN:M]` tag (or `grep -n` in `/app/work/materials/*.txt`). deck_check
  fails a quote that is not in the materials and suggests the right locator when it is elsewhere.
- Something the task asks for but the materials do not contain: `"status": "not_in_materials"`; never invent it.

## spec.json — the deck as data (deck_build.py renders it)

```json
{"meta": {"title": "...", "lang": "en|zh", "audience": "...", "duration_min": 15, "goal": "...",
          "min_slides": 10, "max_slides": 15, "min_body_pt": 12, "source_notes": true},
 "footer": {"text": "Speaker · Venue · 2026", "slide_numbers": true, "on_title": false},
 "theme": {"fonts": {"latin": "Arial", "ea": "Noto Sans CJK SC"}, "colors": {"accent1": "0072B2"}},
 "slides": [ { ...slide... } ]}
```
`theme` is used only when no template is given (the built-in academic 16:9 template). With `--template`,
the template's master, layouts, fonts and colours are used as they are.

### Slide fields
| field | meaning |
|---|---|
| `id` | stable id (S1, S2 …) used by the evidence map, Q&A and edits |
| `role` | title, outline, motivation, problem, background, method, setup, results, analysis, discussion, limitations, conclusion, qa, backup, section, references |
| `title` | the slide's claim (one sentence) — or the deck title on the title slide |
| `subtitle` | string or list (title / section slides) |
| `bullets` | list of `"text"` or `{"text", "level": 0-2, "src": ["F3"]}`; `**x**` = emphasis (bold, accent colour); `x^2`, `x^{l+1}`, `10^-4` = superscript, `ε_HOMO`, `d_{model}` = subscript (also in table cells; `\_` keeps a literal underscore; `meta.math_markup: false` turns it off) |
| `figure` | `{"pdf", "page", "bbox":[x0,y0,x1,y1] page fractions}` or `{"image": "path.png"}` plus `"ref": "Fig. 3"`, `"caption"` (verbatim, may be shortened to its first sentence), `"kind": "data|schematic|photo|table_image"`, `"src"` |
| `figures` | list of figure dicts shown side by side (2–4) |
| `chart` | `{"csv", "x", "y": [cols], "kind": "bar|line|scatter|barh", "err", "xlabel", "ylabel" (with unit), "labels": true, "highlight": [x values], "series_labels": {col: name}, "ref", "caption", "src"}` — only from data files in the inputs |
| `table` | `{"rows": [[header…], [cells…]]}` or `{"csv", "columns", "max_rows"}`, plus `"ref"`, `"caption"`, `"highlight": [[r,c]]`, `"src"`. Paper tables: start from `paper_map.json` → `figures_tables[].table_rows` (cells read from the PDF, super/subscripts as `^`/`_`), check them against the crop, keep the rows/columns you need |
| equations | a display equation is shown as its crop: `"figure": {"image": "figs/eq3_6_p2.png", "ref": "Eq. 3-6", "kind": "schematic", "caption": "…"}` (`paper_map.json` → `equations[]`); the PDF text layer garbles math, so never retype it from `materials/*.txt` |
| `left`, `right` | two-column slide: each `{"heading", "bullets"}` or with a `figure`/`chart`/`table` |
| `arrange` | `right` (visual left, text right; default) or `below`; `visual_share` 0.5–0.8 |
| `layout` | force a kind: title, section, bullets, two_column, figure, figures, chart, table, blank |
| `layout_name` | force a template layout by name or index (see template_report.json) |
| `sources` | fact ids behind the slide as a whole (title claim) |
| `derived` | `[{"value": "3.5", "formula": "31.4 - 27.9", "inputs": ["F8"]}]` numbers computed, not quoted |
| `notes` | speaker notes: what to SAY that is not on the slide |
| `key_message`, `transition`, `time_s`, `if_asked` | talk organisation fields (appended to the notes) |

Layout choice is automatic: title → Title Slide layout, bullets → Title and Content, two_column → Two
Content, figure/chart/table → Title Only + computed boxes inside the template's text area, section → Section
Header. Missing layouts fall back in a fixed order and the fallback is written to build_report.json.

## talk_plan.json (talk organisation)
```json
{"goal": "make the committee accept that ...", "occasion": "thesis defence", "duration_min": 15,
 "audience": {"who": "...", "knows": "...", "cares_about": "...", "likely_doubts": ["..."]},
 "sections": [{"name": "Problem", "minutes": 2, "slides": ["S2", "S3"], "key_message": "...", "evidence_strength": "strong|moderate|weak"}],
 "qa": [{"question": "...", "answer_points": ["..."], "evidence": ["F7"], "backup_slide": "S14"}],
 "cuts": [{"content": "ablation on X (Table 5)", "reason": "not needed for this audience; kept as backup S15"}]}
```

## ops.json (edits of an existing deck) — see `scripts/deck_edit.py` header for all ops
```json
[{"op": "replace_text", "slide": 3, "shape": "title", "find": "28.1%", "replace": "27.9%", "reason": "Table 1 value", "src": ["F8"]},
 {"op": "set_notes", "slide": 3, "text": "..."},
 {"op": "move_slide", "slide": 7, "to": 4}]
```
Slides are positions at the time the op runs; `slide_id` (from inventory) is stable across moves.

## Outputs (all in /app/output)
| file | from | content |
|---|---|---|
| `deck.pptx` | deck_build / deck_edit | the editable deck (text boxes, placeholders, notes) |
| `deck.pdf`, `previews/slide-NN.png`, `previews/contact_sheet.png` | deck_render | real LibreOffice renders |
| `build_report.json`, `slides_outline.md` | deck_build | backend, template, layout per slide, font overrides, warnings; outline with notes |
| `check_report.json`, `evidence_map.json`, `evidence_map.md` | deck_check | page-level evidence map + audit |
| `render_report.json` | deck_render | overflow, min font, fonts requested vs embedded, tools |
| `compliance.json`, `fix_log.json`, `preserve.json` | deck_template | template audit, fixes, content preservation |
| `edit_log.json`, `slide_diff.json/.md`, `versions/` | deck_edit | change log with before/after, per-slide diff, snapshots |
| `talk_plan.md` | deck_check --mode talk | goal, audience, time budget, Q&A, cuts |
| `version_record.json`, `result.json` | deck_fill / make_result | sources+hashes+tools; contract-complete result |
