#!/usr/bin/env python3
"""Safety draft for figure tasks: an honest deliverable in /app/output within the first minute of a run.

extract_materials.py starts this in the background after the first extraction.
- chart skills with CSV data: a chart bound to the CSV columns (ga_make.py --mode chart, status partial);
- otherwise: result.json with case_id, every contract field marked not_provided, status partial.
The agent's own ga_make.py run replaces it (it stops a running draft first and removes draft-only files).

  fig_draft.py --work /app/work --input /input --outdir /app/output
"""
import argparse, csv, glob, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
NOTE = ("Automatic safety draft written at the start of the run; the agent's final figure replaces it. If this note "
        "is present, the run ended before the final figure was built.")
ERR = re.compile(r"(^|[_\s\-(])(sd|std|stdev|se|sem|err|error|ci|ci95)([_\s\-)]|$)", re.I)


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def chart_spec(csvs):
    els, srcs = [], []
    csvs = [c for c in csvs if os.path.getsize(c) < 2_000_000][:2]
    for j, path in enumerate(csvs):
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.reader(f))
        if len(rows) < 3:
            continue
        head, body = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
        cols = list(zip(*[r + [""] * (len(head) - len(r)) for r in body]))
        numeric = [i for i, c in enumerate(cols) if sum(num(v) is not None for v in c if v.strip()) >= 0.8 * max(1, sum(1 for v in c if v.strip()))
                   and any(v.strip() for v in c)]
        cat = [i for i in range(len(head)) if i not in numeric]
        if not numeric:
            continue
        xi = cat[0] if cat else numeric[0]
        ys = [i for i in numeric if i != xi and not ERR.search(head[i])]
        if not ys:
            continue

        def mag(i):
            v = sorted(abs(num(x)) for x in cols[i] if num(x) is not None)
            return v[len(v) // 2] if v else 0.0
        m0 = mag(ys[0]) or 1.0
        ys = [i for i in ys if m0 / 10 <= (mag(i) or m0) <= m0 * 10][:3]   # one value axis: similar magnitudes only
        errs = []
        for i in ys:
            e = next((k for k in numeric if ERR.search(head[k]) and head[i].lower().split("_")[0] in head[k].lower()), None)
            errs.append(head[e] if e is not None else None)
        x_numeric = xi in numeric
        n = len(body)
        kind = "line" if x_numeric else ("barh" if n > 8 or max(len(r[xi]) for r in body) > 14 else "bar")
        if n > 30 and not x_numeric:
            body, kind = body[:30], "barh"
        w = 0.84 / max(1, len(csvs))
        lab = max(len(r[xi]) for r in body) if kind == "barh" else 0
        pad = min(0.28, 0.012 * lab) if kind == "barh" else 0.0        # room for long category labels left of the bars
        vname = head[ys[0]] if len(ys) == 1 else "value (%s)" % ", ".join(head[i] for i in ys)
        el = dict(id="c_draft%d" % (j + 1), type="chart", kind=kind, x=0.08 + j * (w + 0.04) + pad, y=0.16,
                  w=w - 0.04 - pad, h=0.62 if kind == "barh" else 0.58,
                  data=dict(csv=path, x=head[xi], y=[head[i] for i in ys], series_labels=[head[i] for i in ys]),
                  xlabel=head[xi] if kind != "barh" else vname, ylabel=vname if kind != "barh" else "",
                  value_labels=n <= 12, title=os.path.splitext(os.path.basename(path))[0].replace("_", " "),
                  source=dict(file=os.path.basename(path), loc="header + rows 2-%d" % (n + 1), quote=",".join(head)))
        if any(errs) and len(ys) == 1:
            el["data"]["err"] = errs[0]
        els.append(el)
        srcs.append(os.path.basename(path))
    if not els:
        return None
    return dict(canvas=dict(width_in=10, height_in=5.2, dpi=300, background="#FFFFFF", font_family="auto", base_font_pt=11),
                title=dict(text="Values from %s (draft)" % ", ".join(srcs), y=0.05,
                           source=dict(file=srcs[0], loc="header", quote=els[0]["source"]["quote"])),
                elements=els, footnote="Source: %s; every glyph is bound to its CSV cell (glyph_map.json)." % ", ".join(srcs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--outdir", default="/app/output")
    a = ap.parse_args()
    t0 = time.time()
    log = open(os.path.join(a.work, "draft.log"), "a", encoding="utf-8")

    def say(*x):
        print(*x, file=log, flush=True)

    try:
        skill = open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read(400)
    except Exception:
        skill = ""
    case = os.path.join(a.input, "case.json")
    dw = os.path.join(a.work, "draft")
    out = os.path.join(a.work, "draft_out")
    for d in (dw, out):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)
    os.environ["FIG_DRAFT"] = "1"          # extract_materials.py must not start another draft from inside this one
    if os.path.isdir(os.path.join(a.work, "materials")):
        try:
            os.symlink(os.path.abspath(os.path.join(a.work, "materials")), os.path.join(dw, "materials"))
        except OSError:
            pass
    csvs = sorted(glob.glob(os.path.join(a.input, "**", "*.csv"), recursive=True))
    svgs = glob.glob(os.path.join(a.input, "**", "*.svg"), recursive=True)
    spec = chart_spec(csvs) if ("chart" in skill and csvs and not svgs) else None
    if spec:
        sp = os.path.join(dw, "spec.json")
        json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        cmd = [sys.executable, os.path.join(HERE, "ga_make.py"), "--spec", sp, "--mode", "chart", "--name", "chart",
               "--outdir", out, "--work", dw, "--input", a.input, "--case", case, "--status", "partial",
               "--uncertainty", NOTE, "--no-rerun", "--draft",
               "--materials"] + glob.glob(os.path.join(a.work, "materials", "*.txt")) + csvs
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        say(r.stdout[-2500:], r.stderr[-1200:])
    if not os.path.exists(os.path.join(out, "result.json")) and os.path.exists(case):
        fill = os.path.join(dw, "fill.json")
        json.dump(dict(status="partial", uncertainty=[NOTE + " No figure was produced in this run."]),
                  open(fill, "w", encoding="utf-8"), ensure_ascii=False)
        r = subprocess.run([sys.executable, os.path.join(HERE, "make_result.py"), "--case", case, "--outdir", out,
                            "--fill", fill], capture_output=True, text=True, timeout=120)
        say("skeleton result:", (r.stdout + r.stderr)[-800:])
    if os.path.exists(os.path.join(a.work, ".final_started")):
        say("final run started: draft not copied")
        return 0
    if os.path.exists(os.path.join(a.outdir, "result.json")) and not os.path.exists(os.path.join(a.outdir, "draft_manifest.json")):
        say("output already holds a final result: draft not copied")
        return 0
    if not os.path.exists(os.path.join(out, "result.json")):
        say("no draft produced")
        return 1
    os.makedirs(a.outdir, exist_ok=True)
    copied = []
    for root, _, names in os.walk(out):
        for n in names:
            src = os.path.join(root, n)
            rel = os.path.relpath(src, out)
            os.makedirs(os.path.dirname(os.path.join(a.outdir, rel)), exist_ok=True)
            shutil.copy2(src, os.path.join(a.outdir, rel))
            copied.append(rel)
    json.dump(dict(note=NOTE, files=sorted(copied), seconds=round(time.time() - t0, 1)),
              open(os.path.join(a.outdir, "draft_manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("draft delivered: %d files in %.0f s" % (len(copied), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
