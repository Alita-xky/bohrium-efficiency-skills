# Measured gain (SYNTHETIC example, mode `graphical_abstract`)

Generated 2026-11-26 11:08 by `python3 scripts/gain_check.py --mode graphical_abstract --out <dir>` on the shipped synthetic example (`examples/minimal`). It is **not** an official case result and **not** the platform's baseline-agent comparison; it measures what the pipeline adds to a general plotting library on identical input. Python 3.14.7.

## 1. Same input, same content: deliverable

| measured on the output | A: matplotlib defaults | B: this Skill |
|---|---|---|
| editable `<text>` elements in the SVG | 0 | 43 |
| spec elements addressable by id in the SVG | 0/17 | 17/17 |
| named layers | 0 | 6 |
| PDF fonts embedded as TrueType/OpenType | 0 | 3 |
| PDF Type 3 (glyph drawing) fonts | 2 | 0 |
| element → source map | False | True |
| result.json with case_id | False | True |
| contract fields filled | 0/6 | 6/6 |
| re-run byte-identical (SVG, PDF, PNG) | False | True |
| automatic checks | none | spec vs materials (ga_check.py), layout/export (ga_render.py), re-run hash, contract |
| wall time (s) | 0.5 | 1.6 |

## 2. One injected defect per run (same spec otherwise)

Flagged before delivery by B: **8/8**; A: 0 flagged (1 runs crashed without a deliverable, the rest were drawn silently).

| defect | A: matplotlib defaults | B: this Skill | B evidence |
|---|---|---|---|
| fabricated_number — a number that is in no material is printed, with a real-looking citation | drawn and shipped, no message | FAIL (status partial) | FAIL check: {"level": "fail", "check": "number_not_in_materials", "id": "inj_num", "number": "63.7", "hint": " |
| unknown_arrow_end — an arrow points to an entity that does not exist | script crashed (KeyError), no deliverable | FAIL (status partial) | FAIL check: {"level": "fail", "check": "arrow_endpoint_unknown", "id": "inj_ar", "end": "to", "ref": "ghost_en |
| source_removed — a scientific element has no source | drawn and shipped, no message | WARN (status complete) | WARN check: {"level": "warn", "check": "provenance_missing", "id": "P1_q", "hint": "add source:{file,loc,quote |
| quote_paraphrased — a quote is not verbatim any more | drawn and shipped, no message | WARN (status complete) | WARN check: {"level": "warn", "check": "quote_not_found", "id": "P3_inh", "quote": "opening stomatal inhibits  |
| legend_colour_mismatch — a legend swatch no longer matches its category colour | drawn and shipped, no message | WARN (status complete) | WARN check: {"level": "warn", "check": "legend_color_mismatch", "category": "Problem", "legend_color": "#7f7f7 |
| label_too_long — an 80-word label (unreadable even if it fits) | drawn and shipped, no message | WARN (status complete) | WARN check: {"level": "warn", "check": "label_too_long", "id": "P1_q", "words": 72, "hint": "keep the key phra |
| conclusion_panel_missing — the four-part story lacks its conclusion panel | drawn and shipped, no message | FAIL (status partial) | FAIL check: {"level": "fail", "check": "ga_role_missing", "role": "conclusion", "hint": "add a panel with this |
| contract_field_without_answer — a required result field has no answer | no result.json is written | FIX line + field written as not_provided (status partial) |  |

## 3. Source-preserving edit (svg_edit.py on B's SVG)

2 ops, 2 verified on the written file; untouched elements byte-identical: 113/113; unexpected changes: none. A has no element ids to address: an edit means re-running the drawing script.

## 4. output_contract shapes (fields filled automatically by ga_make.py)

| contract shape | fields | filled | case_id kept |
|---|---|---|---|
| dict_of_descriptions | 6 | 6 | True |
| list_of_names | 6 | 6 | True |
| json_schema | 4 | 4 | True |
| required_fields_wrapper | 5 | 5 | True |
| list_of_objects | 4 | 4 | True |
| chinese_names | 5 | 5 | True |

## Limits of this evidence

- One synthetic example per figure type; the platform's 3 hidden cases are different.
- A is the library default, not a skilled human or the DeepSeek baseline agent; an agent could add some of B's settings by hand. The defect rows show what is caught automatically, not what a careful agent might notice.
- Checks are string/geometry level: a wrong but quoted relation, or a poor choice of what to show, is not caught.

## Side-by-side runs with a real agent (2026-11-25)

Same case, same prompt and budget wording as the platform (64 requests / 900 s), one agent run **without**
and one **with** this Skill; agent = Claude Haiku 5.5 headless (a stand-in: the platform uses a DeepSeek
harness), cases written by us from public arXiv papers (`benchmarks/side_by_side/` in the repository). We
compared the delivered files and their renders by eye and by the run's own reports. One run per arm — read it
as a qualitative check, not a score.


| | without Skill | with Skill v1.4 | with Skill v1.5/1.6 |
|---|---|---|---|
| case | arXiv:1607.07244, Chinese four-part graphical abstract | same | same |
| look | dense: header band, numbered steps with doses, mini chart, limits box | sparse: four plain panels, one sentence per box | numbered colour headers, steps with conditions, key numbers with units, model diagram; v1.6 parameter cards |
| proof | none | element → source map, chart → CSV binding | same + glyph read-back + `rubric_coverage` (question / method / result / conclusion) |
