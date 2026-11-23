# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`make_example_template.py OUTDIR` writes made-up inputs: `univ_template.potx` (4:3, Georgia, dark-red/gold theme,
6 layouts), `draft_deck.pptx` (16:9, 5 slides, with injected violations: hard-coded Comic Sans at 10 pt, magenta
title, moved title, stretched figure, a slide without notes), `paper_synthetic.pdf` and `wer_by_hours.csv`
(the draft's scientific source). `case.json` asks to adapt the draft to the template, keep all content and report
per-slide deviations.

```bash
SKILL=../..
E=/tmp/tpl_example; mkdir -p $E/input $E/work $E/output
python3 make_example_template.py $E/input && cp case.json $E/input/
python3 $SKILL/scripts/deck_prepare.py --input $E/input --work $E/work --gallery
python3 $SKILL/scripts/deck_template.py audit --pptx $E/input/draft_deck.pptx --template $E/input/univ_template.potx \
   --out $E/output/compliance_before.json
python3 $SKILL/scripts/deck_template.py to_spec --pptx $E/input/draft_deck.pptx --out $E/work/spec.json \
   --previews $E/work/start_render/previews
python3 $SKILL/scripts/deck_make.py --spec $E/work/spec.json --outdir $E/output --template $E/input/univ_template.potx \
   --mode template --materials "$E/work/materials/*.txt" --preserve-from $E/input/draft_deck.pptx
python3 $SKILL/scripts/deck_fill.py --case $E/input/case.json --outdir $E/output --work $E/work --input $E/input --out $E/work/fill.json
python3 $SKILL/scripts/make_result.py --case $E/input/case.json --outdir $E/output --fill $E/work/fill.json
```
Expected (illustrative): before `{"status": "needs_fix", "compliance_rate": 0.0, "fails": 13, ...}`; after
`{"status": "ok", ..., "template": "pass", "compliance_rate": 1.0, "preserve": "pass"}`; result `"status": "complete"`.
Route C instead of A: `deck_template.py fix --pptx draft_deck.pptx --template univ_template.potx --out deck.pptx
--log fix_log.json` (here the slide size differs, so route A is the right choice; C is for decks already on the master).
