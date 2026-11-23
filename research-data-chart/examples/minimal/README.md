# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`paper_excerpt.txt` (made-up 4-paragraph text), `yield.csv`, `aba_timecourse.csv` and `case.json`
are synthetic. `spec.json` is a filled `chart` spec built only from them; `fill.json` shows one key per
output_contract field (paths relative to the output folder).

```bash
SKILL=../..; W=$(mktemp -d)
python3 $SKILL/scripts/extract_materials.py --input . --work $W
python3 $SKILL/scripts/fig_check.py --spec spec.json --mode chart --materials "$W/materials/*.txt" *.csv \
   --out $W/out/check_report.json
python3 $SKILL/scripts/fig_render.py --spec spec.json --outdir $W/out --name chart
python3 $SKILL/scripts/make_result.py --case case.json --list-fields
python3 $SKILL/scripts/extract_materials.py --work $W --find "ABA" "yield"     # quotes with locators
python3 $SKILL/scripts/make_result.py --case case.json --outdir $W/out --fill fill.json
```
Expected: check `pass`, render `ok` (0 fails), make_result `missing: []`, status `complete`.
Same thing in one call (what the Skill does on a real case):
```bash
mkdir -p $W/in && cp case.json *.txt *.csv $W/in/
python3 $SKILL/scripts/fig_make.py --spec spec.json --mode chart --name chart --case $W/in/case.json    --input $W/in --work $W/work --outdir $W/out2
```
Expected first line: `"status": "complete"`, `"reproducible": true`, `"unmapped": []`.
Fault demos: change a number in a label (e.g. 41 → 45) → `number_not_in_materials` FAIL; move an
entity onto an arrow's path → `arrow_through_entity` ISSUE; delete a key from fill.json → `FIX:` line
and status `partial`.
