# Minimal call example (SYNTHETIC — illustrates the interface, not a formal case result)

`make_example_pdf.py` writes a made-up 3-page paper (`paper_synthetic.pdf`: abstract, method with a schematic
Fig. 1, Table 1, data Fig. 2, limitations, conclusion) and its data file `wer_by_hours.csv`. `case.json` asks for
an 8–12 slide deck. `facts.json` (evidence table) and `spec.json` (deck spec) are filled examples; `spec.json`
uses paths relative to a `work/` folder next to `input/`.

```bash
SKILL=../..                       # the skill root
E=/tmp/p2p_example; mkdir -p $E/input $E/work $E/output
python3 make_example_pdf.py $E/input && cp case.json $E/input/ && cp facts.json spec.json $E/work/
python3 $SKILL/scripts/deck_prepare.py --input $E/input --work $E/work
python3 $SKILL/scripts/deck_make.py --spec $E/work/spec.json --facts $E/work/facts.json --outdir $E/output \
   --materials "$E/work/materials/*.txt" $E/input/wer_by_hours.csv --paper-map $E/work/paper_map.json --max-slides 12
python3 $SKILL/scripts/deck_fill.py --case $E/input/case.json --outdir $E/output --work $E/work --input $E/input --out $E/work/fill.json
python3 $SKILL/scripts/make_result.py --case $E/input/case.json --outdir $E/output --fill $E/work/fill.json
```

Expected (illustrative) console output:
```
{"status": "ok", "pptx": ".../deck.pptx", "pdf": ".../deck.pdf", "n_fail": 0, "check": "pass", "build": "ok", "backend": "python-pptx", "render": "ok"}
{"fill": ".../fill.json", "status": "complete", "mapped": {"deck": "deck", "evidence_map": "evidence", ...}, "unmapped": []}
{"result": ".../result.json", "case_id": "example-synthetic-p2p-001", "missing": [], "status": "complete"}
```
Produced: `deck.pptx` (9 slides: title, motivation, contributions, method with Fig. 1 crop, setup, Table 1 as a
native table, Fig. 2 redrawn from the CSV, limitations, conclusion; footers, slide numbers, source notes,
speaker notes), `deck.pdf`, `previews/slide-01..09.png`, `contact_sheet.png`, `evidence_map.json/.md`,
`check_report.json`, `render_report.json`, `build_report.json`, `slides_outline.md`, `version_record.json`,
`result.json`. Run with `DECK_BACKEND=xml` to use the stdlib writer instead of python-pptx.

Fault demo: change the S6 title to "... 27.5% vs 31.4% ..." and re-run deck_make — deck_check reports
`number 27.5 ... is not in any material`; change `"ref": "Fig. 1"` on S4 to `"Fig. 5"` — it reports that the
paper has no Figure 5.
