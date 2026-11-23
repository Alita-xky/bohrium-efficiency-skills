#!/usr/bin/env python3
"""Generate the SYNTHETIC 3-page example paper (paper_synthetic.pdf) and its data file (wer_by_hours.csv).

Everything in it is made up to exercise the Skill (figure crop, caption lookup, table numbers, chart from CSV).
It is NOT a real publication and NOT a formal test case. Requires matplotlib only.
Usage: python3 make_example_pdf.py [outdir]
"""
import csv, os, sys, textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT, exist_ok=True)
ROWS = [("1", "48.2", "41.5"), ("5", "36.9", "31.8"), ("10", "31.4", "27.9"), ("50", "18.6", "18.4")]
with open(os.path.join(OUT, "wer_by_hours.csv"), "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["hours", "full_finetune_wer", "gatelite_wer"])
    w.writerows(ROWS)

TITLE = "GateLite: Sparse Gated Adapters for Low-Resource Speech Recognition"
AUTH = "A. Example and B. Sample (SYNTHETIC EXAMPLE PAPER - not a real publication)"
P = {
 "abs": "Abstract. Fine-tuning large speech encoders for low-resource languages is expensive and overfits when fewer "
        "than 10 hours of labelled audio are available. We propose GateLite, a sparse gated adapter that activates "
        "only 2 of 8 adapter experts per layer. On five low-resource languages, GateLite reduces the average word "
        "error rate (WER) from 31.4% to 27.9% while training 3.1% of the encoder parameters. Gains are largest below "
        "5 hours of data and vanish above 50 hours.",
 "intro1": "Speech recognition for low-resource languages depends on adapting encoders pre-trained on high-resource "
           "data. Full fine-tuning of a 300M-parameter encoder overfits when only a few hours of transcribed speech "
           "exist, and dense adapters spend capacity on every frame even when most frames are easy.",
 "intro2": "Our contributions are: (1) a sparse gated adapter that routes each frame to 2 of 8 small experts; "
           "(2) a load-balancing regulariser that keeps expert usage uniform; (3) an evaluation on five languages "
           "with 1 to 50 hours of training data.",
 "method": "GateLite inserts an adapter block after each of the 24 encoder layers. Each block holds 8 bottleneck "
           "experts of width 64; a router selects the top-2 experts per frame (Figure 1). A load-balancing loss with "
           "weight 0.01 keeps expert usage uniform. Only the adapters and the CTC head are trained; the encoder stays frozen.",
 "exp": "We train on 1, 5, 10 and 50 hours per language and report WER on the official test sets. Table 1 lists the "
        "average WER over five languages with 10 hours of data. Figure 2 shows WER as a function of training hours.",
 "lim": "Limitations. GateLite was evaluated only on read speech; spontaneous speech was not tested. Routing adds 6% "
        "inference latency on a single GPU.",
 "concl": "Conclusion. Sparse gated adapters cut WER by 3.5 points on average at 10 hours of data while training 3.1% "
          "of the parameters. The benefit shrinks as training data grows and is negligible at 50 hours.",
}


def page():
    fig = plt.figure(figsize=(8.5, 11))
    return fig


def para(fig, y, text, size=10, width=100, bold=False):
    for line in textwrap.wrap(text, width):
        fig.text(0.1, y, line, fontsize=size, family="DejaVu Sans", weight="bold" if bold else "normal", va="top")
        y -= size * 1.45 / 792
    return y - 0.018


with PdfPages(os.path.join(OUT, "paper_synthetic.pdf")) as pdf:
    fig = page()
    y = para(fig, 0.93, TITLE, 15, 58, True)
    y = para(fig, y, AUTH, 10, 100)
    y = para(fig, y - 0.01, P["abs"])
    y = para(fig, y, "1 Introduction", 12, bold=True)
    y = para(fig, y, P["intro1"])
    y = para(fig, y, P["intro2"])
    pdf.savefig(fig)
    plt.close(fig)

    fig = page()
    y = para(fig, 0.93, "2 Method", 12, bold=True)
    y = para(fig, y, P["method"])
    ax = fig.add_axes([0.12, y - 0.27, 0.76, 0.25])
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4)
    ax.axis("off")
    for x, lab, c in ((0.2, "Frozen encoder\nlayer l", "#dddddd"), (3.4, "Router\n(top-2 of 8)", "#9ecae1"),
                      (6.6, "8 bottleneck experts\nwidth 64", "#fdd0a2")):
        ax.add_patch(FancyBboxPatch((x, 1.2), 3.0, 1.6, boxstyle="round,pad=0.05", fc=c, ec="black"))
        ax.text(x + 1.5, 2.0, lab, ha="center", va="center", fontsize=10)
    for x0 in (3.2, 6.4):
        ax.annotate("", (x0 + 0.2, 2.0), (x0 - 0.0, 2.0), arrowprops=dict(arrowstyle="->", lw=1.5))
    y = para(fig, y - 0.29, "Figure 1: Overview of GateLite. A router picks 2 of 8 bottleneck experts after every encoder "
                            "layer; the encoder stays frozen.", 9, 110)
    y = para(fig, y, "3 Experiments", 12, bold=True)
    y = para(fig, y, P["exp"])
    y = para(fig, y, "Table 1: Average WER (%) on five languages, 10 h of training data.", 9, 110)
    rows = [("Method", "Trained params (%)", "WER (%)"), ("Full fine-tuning", "100", "31.4"), ("LoRA (r=8)", "1.2", "30.2"),
            ("Dense adapter", "4.0", "29.1"), ("GateLite (ours)", "3.1", "27.9")]
    for r in rows:
        for x, cell in zip((0.14, 0.45, 0.72), r):
            fig.text(x, y, cell, fontsize=10, family="DejaVu Sans", va="top")
        y -= 0.022
    pdf.savefig(fig)
    plt.close(fig)

    fig = page()
    ax = fig.add_axes([0.15, 0.62, 0.7, 0.28])
    hrs = [r[0] for r in ROWS]
    xs = range(len(hrs))
    ax.bar([x - 0.2 for x in xs], [float(r[1]) for r in ROWS], 0.4, label="Full fine-tuning", color="#999999")
    ax.bar([x + 0.2 for x in xs], [float(r[2]) for r in ROWS], 0.4, label="GateLite", color="#3182bd")
    ax.set_xticks(list(xs))
    ax.set_xticklabels(hrs)
    ax.set_xlabel("Training data (hours)")
    ax.set_ylabel("WER (%)")
    ax.legend(frameon=False)
    y = para(fig, 0.57, "Figure 2: WER (%) versus hours of training data. GateLite is best at 1 h and 5 h; at 50 h the "
                        "two methods are within 0.3 points.", 9, 110)
    y = para(fig, y, "4 Limitations", 12, bold=True)
    y = para(fig, y, P["lim"])
    y = para(fig, y, "5 Conclusion", 12, bold=True)
    y = para(fig, y, P["concl"])
    pdf.savefig(fig)
    plt.close(fig)
print("wrote", os.path.join(OUT, "paper_synthetic.pdf"), "and wer_by_hours.csv")
