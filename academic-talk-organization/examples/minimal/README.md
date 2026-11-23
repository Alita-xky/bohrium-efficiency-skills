# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`make_example_pdf.py` writes a made-up 3-page paper (`paper_synthetic.pdf`) and `wer_by_hours.csv`. `case.json`
asks for an 8-minute lab-meeting talk for colleagues who know CTC but not mixture-of-experts.
`facts.json` (evidence table), `talk_plan.json` (goal, audience, section budget, 3 Q&A, 2 cuts) and `spec.json`
(8 main slides with time_s / key_message / transition + 1 backup) are filled examples; `spec.json` uses paths
relative to a `work/` folder next to `input/`.

```bash
SKILL=../..
E=/tmp/talk_example; mkdir -p $E/input $E/work $E/output
python3 make_example_pdf.py $E/input && cp case.json $E/input/ && cp facts.json talk_plan.json spec.json $E/work/
python3 $SKILL/scripts/deck_prepare.py --input $E/input --work $E/work
python3 $SKILL/scripts/deck_make.py --spec $E/work/spec.json --facts $E/work/facts.json --outdir $E/output \
   --materials "$E/work/materials/*.txt" $E/input/wer_by_hours.csv --paper-map $E/work/paper_map.json \
   --mode talk --plan $E/work/talk_plan.json --duration-min 8
python3 $SKILL/scripts/deck_fill.py --case $E/input/case.json --outdir $E/output --work $E/work --input $E/input --out $E/work/fill.json
python3 $SKILL/scripts/make_result.py --case $E/input/case.json --outdir $E/output --fill $E/work/fill.json
```
Expected (illustrative): `{"status": "ok", ..., "check": "pass", "render": "ok"}`, then
`mapped: {"talk_plan": "plan+outline", "qa_preparation": "qa", ...}, "unmapped": []` and `"status": "complete"`.
Produced: `deck.pptx` (speaker notes contain the key message, transition, time, if-asked and evidence lines),
`deck.pdf`, `previews/`, `talk_plan.json`, `talk_plan.md` (section budget, slide timeline 0:00 … 7:40, Q&A, cuts),
`evidence_map.md`, `check_report.json`, `render_report.json`, `version_record.json`, `result.json`.

Fault demo: set S6 `time_s` to 300 → `time_budget` FAIL (slide times sum to 11.3 min, talk is 8 min); delete two
`qa` entries → `qa` FAIL; change a Q&A answer to "dense adapter trains 4.5%" → number FAIL.
