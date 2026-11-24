#!/usr/bin/env python3
"""Generate the SYNTHETIC inputs of the ppt_edit example (made up; not a formal test case):
  paper_synthetic.pdf + wer_by_hours.csv   the source paper (via make_example_pdf.py)
  draft_deck.pptx  a 6-slide draft with typical problems to be fixed by edit instructions:
                   wrong number on slide 4 (28.1% instead of Table 1's 27.9%), 5 long bullets on slide 2,
                   no notes on slide 3, conclusion placed before limitations.
Usage: python3 make_example_deck.py OUTDIR      (needs matplotlib; python-pptx optional)
"""
import json, os, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "scripts"))
out = sys.argv[1] if len(sys.argv) > 1 else HERE
os.makedirs(out, exist_ok=True)
subprocess.run([sys.executable, os.path.join(HERE, "make_example_pdf.py"), out], check=True)
pdf = os.path.join(out, "paper_synthetic.pdf")
spec = {"meta": {"title": "GateLite draft", "lang": "en", "design": False}, "footer": {"text": "GateLite - draft for review"},
        "slides": [
            {"id": "S1", "role": "title", "title": "GateLite: Sparse Gated Adapters for Low-Resource Speech Recognition",
             "subtitle": ["A. Example and B. Sample"], "notes": "Open with the low-resource problem."},
            {"id": "S2", "role": "motivation", "title": "Full fine-tuning overfits below 10 h of labelled speech",
             "bullets": ["Encoders are pre-trained on high-resource data and then adapted to each new language separately",
                         "A 300M-parameter encoder overfits when only a few hours of transcribed speech exist",
                         "Dense adapters spend capacity on every frame even when most frames are easy",
                         "Low-resource languages often have fewer than 10 hours of labelled audio",
                         "Training cost matters because every language needs its own adaptation run"],
             "notes": "Stress the data regime."},
            {"id": "S3", "role": "method", "title": "A router picks the top-2 of 8 experts after each of the 24 frozen layers",
             "figure": {"pdf": pdf, "page": 2, "bbox": [0.1216, 0.2864, 0.8608, 0.4045], "ref": "Fig. 1",
                        "caption": "Overview of GateLite", "kind": "schematic"}},
            {"id": "S4", "role": "results", "title": "At 10 h GateLite reaches 28.1% WER vs 31.4% for full fine-tuning",
             "table": {"ref": "Table 1", "rows": [["Method", "Trained params (%)", "WER (%)"], ["Full fine-tuning", "100", "31.4"],
                                                  ["LoRA (r=8)", "1.2", "30.2"], ["Dense adapter", "4.0", "29.1"],
                                                  ["GateLite (ours)", "3.1", "27.9"]], "highlight": [[4, 0], [4, 1], [4, 2]]},
             "notes": "Point at the last row."},
            {"id": "S5", "role": "conclusion", "title": "Sparse routing cuts WER by 3.5 points at 10 h of data",
             "bullets": ["Trains 3.1% of the parameters", "Benefit negligible at 50 h"], "notes": "Take-home number."},
            {"id": "S6", "role": "limitations", "title": "Only read speech was tested; routing costs 6% latency",
             "bullets": ["Spontaneous speech was not evaluated", "Routing adds 6% inference latency on a single GPU"],
             "notes": "Name both limits."}]}
work = os.path.join(out, "_draft_work")
os.makedirs(work, exist_ok=True)
with open(os.path.join(work, "spec.json"), "w") as f:
    json.dump(spec, f)
subprocess.run([sys.executable, os.path.join(SCRIPTS, "deck_build.py"), "--spec", os.path.join(work, "spec.json"),
                "--out", os.path.join(work, "draft_deck.pptx")], check=True)
shutil.copyfile(os.path.join(work, "draft_deck.pptx"), os.path.join(out, "draft_deck.pptx"))
shutil.rmtree(work, ignore_errors=True)
print("wrote draft_deck.pptx, paper_synthetic.pdf, wer_by_hours.csv in", out)
