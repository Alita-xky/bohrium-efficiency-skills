# Measured gain (SYNTHETIC example)

Generated 2026-11-24 15:30 by `python3 scripts/gain_check.py --out <dir>` on the shipped synthetic example (`examples/minimal`). Not an official case result and not the platform's baseline-agent comparison; it measures, on identical input, what the scripts add to a plain python-pptx workflow. Python 3.14.7.

## Same spec, two pipelines

A = a python-pptx script writing the same titles, bullets and notes into the default template (images, tables and charts left out). B = `deck_make.py --case` (check → build → LibreOffice render → records → result.json).

| measured | A: python-pptx script | B: this Skill |
|---|---|---|
| slides | 9 | 9 |
| slides with speaker notes | 9 | 9 |
| pictures (paper figure crops) | 0 | 2 |
| tables | 0 | 1 |
| slides using a title placeholder | 9 | 9 |
| evidence map (slide → locator + verbatim quote) | False | True |
| facts with verified quotes | 0/15 | 15/15 |
| numbers checked against the materials | 0 | 40 |
| PDF pages rendered | None | 9 |
| PNG previews | 0 | 9 |
| result.json | False | True |
| contract fields filled | 0/5 | 5/5 |
| wall time (s) | 0.1 | 2.8 |

## One injected defect per run (same spec otherwise)

Flagged before delivery by B: **8/8**; A: 0 (the script writes whatever it is given).

| defect | A | B | B status | B evidence |
|---|---|---|---|---|
| fabricated_number — a bullet states a number that is in no material | deck written, no message | FAIL | partial | FAIL check: number slide 2: number 37.9 (bullet 4) is not in any material; use the paper's value or declare it in slide. |
| figure_not_in_paper — a slide cites a figure number the paper does not have | deck written, no message | FAIL | partial | FAIL check: fig_ref slide 2: Fig. 9 is cited but the paper has no such caption |
| quote_paraphrased — an evidence quote is no longer verbatim | deck written, no message | FAIL | partial | FAIL check: fact_quote slide None: fact F0 quote not found verbatim in materials (longest match 25%): copy it exactly fr |
| slide_without_sources — a content slide cites no evidence | deck written, no message | FAIL | partial | FAIL check: sources slide 2: slide has no sources (cite fact ids) |
| too_many_bullets — 11 bullets on one slide | deck written, no message | FAIL | partial | FAIL check: density slide 2: 11 bullets (max 9; aim <= 6): split the slide |
| slide_limit_exceeded — more slides than the task allows | deck written, no message | FAIL | partial | FAIL check: slide_count slide None: deck has 18 slides, maximum is 12 |
| speaker_notes_missing — a content slide has no speaker notes | deck written, no message | FAIL | partial | FAIL check: notes slide 2: no speaker notes |
| paragraph_bullet — an 80-word paragraph pasted as one bullet | deck written, no message | WARN | complete | WARN check: density slide 2: long bullet (503 chars > 110): shorten to a phrase |

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
