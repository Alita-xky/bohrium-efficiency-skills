#!/usr/bin/env python3
"""Generate the SYNTHETIC inputs of the ppt_template example (everything made up; not a formal test case):

  univ_template.potx  a 4:3 "university" template (Georgia headings, dark-red/gold theme) built with pptkit
  draft_deck.pptx     a 16:9 draft deck on another template with typical violations: hard-coded Comic Sans,
                      off-palette magenta, 10 pt body text, a moved title, a stretched picture, no notes on slide 1
  paper_synthetic.pdf + wer_by_hours.csv  (via make_example_pdf.py) as the scientific source of the draft

Usage: python3 make_example_template.py OUTDIR      (needs matplotlib; python-pptx optional)
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", "..", "scripts"))
sys.path.insert(0, SCRIPTS)
import pptkit as K  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else HERE
os.makedirs(out, exist_ok=True)
py = sys.executable
subprocess.run([py, os.path.join(HERE, "make_example_pdf.py"), out], check=True)

# 1. the template (.potx): 4:3, own fonts and colours
tpl_pptx = os.path.join(out, "_univ_template.pptx")
K.make_default_template(tpl_pptx, {"latin": "Georgia", "ea": "Noto Serif CJK SC"},
                        {"accent1": "8C1515", "accent2": "B58500", "dk2": "2E2D29", "accent3": "175E54"},
                        size=(9144000, 6858000))
pkg = K.Package(tpl_pptx)
d, o = pkg.content_types()
o["/ppt/presentation.xml"] = K.CT_TEMPLATE
pkg.write_content_types(d, o)
pkg.save(os.path.join(out, "univ_template.potx"))
os.remove(tpl_pptx)

# 2. the draft deck (16:9 default template) built from a small spec
work = os.path.join(out, "_draft_work")
os.makedirs(work, exist_ok=True)
spec = {"meta": {"title": "GateLite draft", "lang": "en", "design": False}, "footer": {"text": "Draft - synthetic example"},
        "slides": [
            {"id": "S1", "role": "title", "title": "GateLite: Sparse Gated Adapters for Low-Resource Speech Recognition",
             "subtitle": ["A. Example and B. Sample"]},
            {"id": "S2", "role": "motivation", "title": "Full fine-tuning overfits below 10 h of labelled speech",
             "bullets": ["A 300M-parameter encoder overfits with only a few hours of transcribed speech",
                         "Dense adapters spend capacity on every frame"], "notes": "Stress the data regime."},
            {"id": "S3", "role": "method", "title": "A router picks the top-2 of 8 experts after each of the 24 frozen layers",
             "figure": {"pdf": os.path.join(out, "paper_synthetic.pdf"), "page": 2, "bbox": [0.1216, 0.2864, 0.8608, 0.4045],
                        "ref": "Fig. 1", "caption": "Overview of GateLite", "kind": "schematic"},
             "notes": "Walk through the figure."},
            {"id": "S4", "role": "results", "title": "At 10 h GateLite reaches 27.9% WER vs 31.4% for full fine-tuning",
             "table": {"ref": "Table 1", "rows": [["Method", "Trained params (%)", "WER (%)"], ["Full fine-tuning", "100", "31.4"],
                                                  ["Dense adapter", "4.0", "29.1"], ["GateLite (ours)", "3.1", "27.9"]]},
             "notes": "Point at the last row."},
            {"id": "S5", "role": "limitations", "title": "Only read speech was tested; routing costs 6% latency",
             "bullets": ["Spontaneous speech was not evaluated", "Routing adds 6% inference latency on a single GPU"],
             "notes": "Say both limits."}]}
with open(os.path.join(work, "spec.json"), "w") as f:
    json.dump(spec, f)
draft0 = os.path.join(work, "draft0.pptx")
subprocess.run([py, os.path.join(SCRIPTS, "deck_build.py"), "--spec", os.path.join(work, "spec.json"), "--out", draft0,
                "--report", os.path.join(work, "build_report.json")], check=True)
ops = [{"op": "set_font", "slide": 2, "shape": "Body", "font": "Comic Sans MS", "size": 10},
       {"op": "set_font", "slide": 5, "shape": "title", "color": "FF00FF"},
       {"op": "move_shape", "slide": 4, "shape": "title", "box": [2.0, 0.1, 9.0, 0.9]},
       {"op": "move_shape", "slide": 3, "shape": "Figure 1", "box": [0.8, 1.8, 11.5, 4.6]},
       {"op": "set_notes", "slide": 1, "text": ""}]
with open(os.path.join(work, "ops.json"), "w") as f:
    json.dump(ops, f)
subprocess.run([py, os.path.join(SCRIPTS, "deck_edit.py"), "apply", "--pptx", draft0, "--ops", os.path.join(work, "ops.json"),
                "--out", os.path.join(out, "draft_deck.pptx"), "--engine", "xml"], check=True)
import shutil  # noqa: E402
shutil.rmtree(work, ignore_errors=True)
print("wrote univ_template.potx, draft_deck.pptx, paper_synthetic.pdf, wer_by_hours.csv in", out)
