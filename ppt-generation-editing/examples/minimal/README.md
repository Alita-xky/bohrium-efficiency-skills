# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`make_example_deck.py OUTDIR` writes made-up inputs: `paper_synthetic.pdf` + `wer_by_hours.csv` (the source) and
`draft_deck.pptx` (6 slides with known problems: slide 4 says 28.1 % but Table 1 says 27.9 %, slide 2 has five long
bullets, slide 3 has no speaker notes, the conclusion comes before the limitations). `case.json` lists six
revision instructions; `ops.json` is one possible set of ops implementing them (each with its reason).

```bash
SKILL=../..
E=/tmp/edit_example; mkdir -p $E/input $E/work $E/output
python3 make_example_deck.py $E/input && cp case.json $E/input/ && cp ops.json $E/work/
python3 $SKILL/scripts/deck_prepare.py --input $E/input --work $E/work          # inventory: shape names/ids
python3 $SKILL/scripts/deck_edit.py round --pptx $E/input/draft_deck.pptx --ops $E/work/ops.json --outdir $E/output \
   --materials "$E/work/materials/*.txt" $E/input/wer_by_hours.csv --note "review-panel revision (instructions 1-6)"
python3 $SKILL/scripts/deck_fill.py --case $E/input/case.json --outdir $E/output --work $E/work --input $E/input --out $E/work/fill.json
python3 $SKILL/scripts/make_result.py --case $E/input/case.json --outdir $E/output --fill $E/work/fill.json
```
Expected (illustrative) round summary:
```
{"label": "v1", "parent": "v0", "ops_ok": 6, "ops_errors": [], "parts_changed": 7, "parts_unchanged": 45,
 "slides_changed": 5, "slides_unchanged": 1, "render": "ok", "check": "pass", "template": "pass", ...}
```
`slide_diff.md` then shows: slide 2 body 5 → 3 bullets, slide 3 notes added, slide 4 title 28.1% → 27.9%,
limitations moved to 5, conclusion title in Chinese at 6, backup table added as slide 7.
Check the draft itself: `deck_check.py --pptx draft_deck.pptx --mode edit --materials ...` → FAIL for 28.1.
Roll back: `cp $E/output/versions/v0.pptx $E/output/deck.pptx`.
