# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`paper_excerpt.txt` (made-up 4-paragraph text), `yield.csv`, `aba_timecourse.csv` and `case.json`
are synthetic. `spec.json` is a filled `chart` spec built only from them.

```bash
SKILL=../..; W=$(mktemp -d)
python3 $SKILL/scripts/extract_materials.py --input . --work $W
python3 $SKILL/scripts/fig_check.py --spec spec.json --mode chart --materials "$W/materials/*.txt" *.csv
python3 $SKILL/scripts/fig_render.py --spec spec.json --outdir $W/out --name chart
echo '{"status":"complete"}' > $W/fill.json
python3 $SKILL/scripts/make_result.py --case case.json --outdir $W/out --fill $W/fill.json
```
Fault demo: change a number in a label (e.g. 41 → 45) and rerun `fig_check.py`: it reports
`number_not_in_materials` as FAIL.
