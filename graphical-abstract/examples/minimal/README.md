# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

Input (this folder): `case.json` (with an `output_contract`), `paper_excerpt.txt` (made-up
4-paragraph text), `yield.csv` (2 rows). The filled figure spec is `spec.json`.

```bash
SKILL=../..            # the skill root
mkdir -p /tmp/ga/input /tmp/ga/work /tmp/ga/output
cp case.json paper_excerpt.txt yield.csv /tmp/ga/input/ && cp spec.json yield.csv /tmp/ga/work/
python3 $SKILL/scripts/extract_materials.py --input /tmp/ga/input --work /tmp/ga/work
python3 $SKILL/scripts/make_result.py --case /tmp/ga/input/case.json --list-fields
python3 $SKILL/scripts/extract_materials.py --work /tmp/ga/work --find "ABA" "yield"   # quotes + locators
python3 $SKILL/scripts/ga_check.py --spec /tmp/ga/work/spec.json --mode graphical_abstract \
   --materials '/tmp/ga/work/materials/*.txt' /tmp/ga/input/yield.csv --out /tmp/ga/output/check_report.json
python3 $SKILL/scripts/ga_render.py --spec /tmp/ga/work/spec.json --outdir /tmp/ga/output --name graphical_abstract
echo '[{"op":"set_text","id":"P1_q","text":"Drought cuts wheat\ngrain yield"}]' > /tmp/ga/work/ops.json
python3 $SKILL/scripts/svg_edit.py apply --svg /tmp/ga/output/graphical_abstract.svg \
   --ops /tmp/ga/work/ops.json --out /tmp/ga/output/figure_edited.svg --log /tmp/ga/output/edit_log.json
python3 $SKILL/scripts/make_result.py --case /tmp/ga/input/case.json --outdir /tmp/ga/output --fill fill.json
```

The same in one call each (what the Skill does on a real case):
```bash
python3 $SKILL/scripts/ga_make.py --spec /tmp/ga/work/spec.json --mode graphical_abstract --name graphical_abstract \
   --case /tmp/ga/input/case.json --input /tmp/ga/input --work /tmp/ga/work --outdir /tmp/ga/out2
python3 $SKILL/scripts/ga_make.py --edit-svg /tmp/ga/out2/graphical_abstract.svg --ops /tmp/ga/work/ops.json \
   --name figure_edited --case /tmp/ga/input/case.json --input /tmp/ga/input --work /tmp/ga/work --outdir /tmp/ga/out3
```
First line of each: `"status": "complete"`, `"reproducible": true`, `"unmapped": []`.

Expected (illustrative) console output:
```
{"status": "pass", "fails": 0, "warns": 0}                                  # ga_check
{"status": "ok", "fails": 0, "warns": 0, "outputs": {"svg": ".../graphical_abstract.svg", ...}}  # ga_render
{"applied": 1, "not_applied_or_unverified": [], "warnings": [], "unchanged_check": {...}}  # svg_edit
{"result": ".../result.json", "case_id": "example-synthetic-001", "missing": [], "status": "complete", ...}
```
Produced files: `graphical_abstract.{svg,pdf,png}` (4 panels: research question → approach → key
result with a bar chart from `yield.csv` and an ABA ⊣ stomatal-opening relation → conclusion, legend
with category colours and the inhibition symbol), `layer_map.json`, `render_report.json`,
`check_report.json`, `figure_edited.svg`, `edit_log.json` (each op verified + unchanged-id check),
`result.json` (`fill.json` here shows one key per output_contract field).

Fault demo: change the label of `P3_num` to "ABA 12 → 45 ng/g" and rerun `ga_check.py` — it reports
`number_not_in_materials` for 45.
