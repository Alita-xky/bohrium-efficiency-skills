# Measured gain (SYNTHETIC example)

Generated 2026-11-24 15:31 by `python3 scripts/gain_check.py --out <dir>` on the shipped synthetic example (`examples/minimal`). Not an official case result and not the platform's baseline-agent comparison; it measures, on identical input, what the scripts add to a plain python-pptx workflow. Python 3.14.7.


## Template adaptation (draft → adapted deck)

| measured | draft (before) | adapted by this Skill |
|---|---|---|
| template compliance rate | 0.0 | 1.0 |
| issues by rule | {"render": 6, "theme": 8, "slide_size": 1, "hard_font": 1, "title_position": 1, "off_palette": 1} | {"render": 4} |
| slides / pictures / tables / notes | 5 / 1 / 1 / 4 | 5 / 1 / 1 / 4 |
| content preserved (text) | – | pass (1.0) |
| result.json status | – | complete |

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
