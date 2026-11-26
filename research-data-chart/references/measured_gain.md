# Measured gain (SYNTHETIC example, mode `chart`)

Generated 2026-11-27 01:57 by `python3 scripts/gain_check.py --mode chart --out <dir>` on the shipped synthetic example (`examples/minimal`). It is **not** an official case result and **not** the platform's baseline-agent comparison; it measures what the pipeline adds to a general plotting library on identical input. Python 3.14.7.

## 1. Same input, same content: deliverable

| measured on the output | A: matplotlib defaults | B: this Skill |
|---|---|---|
| editable `<text>` elements in the SVG | 0 | 30 |
| spec elements addressable by id in the SVG | 0/2 | 2/2 |
| named layers | 0 | 1 |
| PDF fonts embedded as TrueType/OpenType | 0 | 2 |
| PDF Type 3 (glyph drawing) fonts | 2 | 0 |
| element → source map | False | True |
| result.json with case_id | False | True |
| contract fields filled | 0/6 | 6/6 |
| re-run byte-identical (SVG, PDF, PNG) | False | True |
| automatic checks | none | spec vs materials (fig_check.py), layout/export (fig_render.py), re-run hash, contract |
| wall time (s) | 1.0 | 2.8 |

## 2. One injected defect per run (same spec otherwise)

Flagged before delivery by B: **6/6**; A: 0 flagged (2 runs crashed without a deliverable, the rest were drawn silently).

| defect | A: matplotlib defaults | B: this Skill | B evidence |
|---|---|---|---|
| fabricated_number — a number that is in no material is printed, with a real-looking citation | drawn and shipped, no message | FAIL (status partial) | FAIL check: {"level": "fail", "check": "number_not_in_materials", "id": "inj_num", "number": "63.7", "hint": " |
| unknown_arrow_end | – | not applicable to this example | |
| source_removed — a scientific element has no source | drawn and shipped, no message | WARN (status complete) | WARN check: {"level": "warn", "check": "provenance_missing", "id": "c_aba", "hint": "add source:{file,loc,quot |
| quote_paraphrased | – | not applicable to this example | |
| legend_colour_mismatch | – | not applicable to this example | |
| label_too_long | – | not applicable to this example | |
| value_axis_label_removed — the value axis has no 'Quantity (unit)' label | drawn and shipped, no message | FAIL (status partial) | FAIL check: {"level": "fail", "check": "chart_axis_label_missing", "id": "c_aba", "hint": "the value axis need |
| data_column_misnamed — the chart is bound to a column that is not in the data file | script crashed (KeyError), no deliverable | FAIL (status partial) | FAIL check: {"level": "fail", "check": "chart_column_missing", "id": "c_aba", "column": "root_aba_ng_g_typo",  |
| empty_data_cell — a plotted value is missing (empty cell) in the data file | script crashed (ValueError), no deliverable | reported in uncertainty (status complete) | chart c_aba: values not reported in the source drawn as 'n.r.': ['Well-watered/0'] |
| contract_field_without_answer — a required result field has no answer | no result.json is written | FIX line + field written as not_provided (status partial) |  |

## 3. Source-preserving edit (svg_edit.py on B's SVG)

2 ops, 2 verified on the written file; untouched elements byte-identical: 52/52; unexpected changes: none. A has no element ids to address: an edit means re-running the drawing script.

## 4. output_contract shapes (fields filled automatically by fig_make.py)

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


| | without Skill | with Skill v1.4 | with Skill v1.5 |
|---|---|---|---|
| case | Table 2 of arXiv:1706.03762 (BLEU + training cost, 10 models, blank cells) | same | same |
| values on the figure | correct, blank cells marked | correct, blank cells "n.r." | correct, blank cells "n.r." |
| look | polished: shared model axis, one legend, shaded Transformer rows, visible log bars | plainer: model names and legend twice, smallest log bars nearly invisible, thick black emphasis borders | shared category axis + one legend, light highlight band, log axis one decade below the smallest bar, value-label head-room |
| proof value ↔ cell | none | `glyph_map.csv`: 33 drawn glyphs read back = their CSV cells | same + `rubric_coverage` |
| status | complete | complete | complete |
What it changed: v1.5 is the response to this comparison — the Skill now matches the hand-drawn look and keeps
the per-glyph proof the baseline lacks.
