# Measured gain (SYNTHETIC example)

Generated 2026-11-24 15:31 by `python3 scripts/gain_check.py --out <dir>` on the shipped synthetic example (`examples/minimal`). Not an official case result and not the platform's baseline-agent comparison; it measures, on identical input, what the scripts add to a plain python-pptx workflow. Python 3.14.7.


## Same single text edit (replace_text on slide 4: '28.1%' → '27.9%')

| measured | A: python-pptx open → edit → save | B: deck_edit.py round |
|---|---|---|
| package parts byte-identical to the input | 51/52 | 51/52 |
| edit applied | 1 | 1 |
| version snapshots (rollback) | 0 | 2 |
| render / evidence check / result.json status | – / – / – | ok / pass / complete |

For this single text edit python-pptx also leaves the untouched parts byte-identical; the difference is the version snapshot (rollback), the re-render, the evidence check and the result record.

| injected edit defect | A | B flagged | B status | B evidence |
|---|---|---|---|---|
| edit_writes_unsupported_number — the edit writes a value that is in no material | deck written, no message | True | partial | check FAIL number slide 4: number 31.6 (title) is not in any material; use the paper's value or declare it in slide.deri |
| edit_addresses_missing_slide — the edit targets a slide that does not exist | script crashed (IndexError) | True | partial | ops_errors: [[1, "replace_text", "slide 99 out of range 1..6"]] |

## output_contract shapes (fields filled automatically by deck_fill.py)

| contract shape | fields | filled | case_id kept |
|---|---|---|---|
| dict_of_descriptions | 5 | 5 | True |
| list_of_names | 6 | 6 | True |
| json_schema | 4 | 4 | True |
| required_fields_wrapper | 5 | 5 | True |
| list_of_objects | 4 | 4 | True |
| chinese_names | 5 | 5 | True |

## Limits of this evidence

- One synthetic example; the platform's 3 hidden cases differ. A is a plain script, not the DeepSeek baseline agent or a careful human; an agent could add some of B's steps by hand.
- Checks are text/geometry level: whether a slide's emphasis or story is good is not machine-verified.
