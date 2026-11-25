#!/usr/bin/env python3
"""Safety draft: a complete, honest deliverable in /app/output within the first minute of a run.

deck_prepare.py starts this in the background. It turns paper_map.json (title, key sentences with [pN:M]
locators, figure/table crops) into draft facts + spec, runs deck_make.py into <work>/draft_out and copies the
result to the output directory, marked status "partial" with an uncertainty note. If the run ends before the
agent's own deck is built, this draft is what gets delivered; the agent's deck_make run replaces it (deck_make
stops a running draft first and removes draft-only files at the end).

  deck_draft.py --work /app/work --input /input --outdir /app/output [--mode auto|paper|talk|template|edit]
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
NOTE = ("Automatic safety draft built from the paper map at the start of the run (key sentences and figure crops "
        "copied verbatim with locators); the agent's final deck replaces it. If this note is present, the run ended "
        "before the final deck was built, so wording, order and depth were not edited for the audience.")


def short(t, n=18):
    w = t.split()
    if len(w) <= n:
        return t.strip()
    cut = " ".join(w[:n])
    m = re.search(r"^(.{40,}?[;,:])\s", cut)
    return (m.group(1) if m and len(m.group(1).split()) >= 10 else cut).rstrip(",;:") + " …"


def detect_mode(prep):
    try:
        name = open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read(400)
    except Exception:
        name = ""
    if "talk-organizer" in name:
        return "talk"
    if "source-preserving-editor" in name or (prep.get("decks") and not prep.get("paper")):
        return "edit"
    if prep.get("templates"):
        return "template"
    return "paper"


LAB = {"en": dict(outline="Outline", motivation="Problem and motivation", method="Approach", results="Results",
                  limitations="Limitations stated by the authors", conclusion="Conclusion"),
       "zh": dict(outline="目录", motivation="研究问题与动机", method="方法", results="结果",
                  limitations="作者说明的局限", conclusion="结论")}


def build(prep, pm, mode, minutes, max_slides, lang="en"):
    L = LAB["zh" if lang.startswith("zh") else "en"]
    pdf = os.path.basename(pm.get("pdf") or prep.get("paper") or "paper.pdf")
    ks = pm.get("key_sentences") or []
    by = {}
    for k in ks:
        by.setdefault(k.get("role") or "context", []).append(k)
    for v in by.values():
        v.sort(key=lambda k: -(k.get("score") or 0))
    facts, fid = [], [0]

    def fact(k, kind):
        fid[0] += 1
        q = " ".join(k["text"].split()[:30])
        facts.append(dict(id="F%d" % fid[0], kind=kind, text=k["text"],
                          source=dict(file=pdf, loc=k.get("loc"), ref=k.get("ref") or "", quote=q)))
        return facts[-1]["id"]

    title = (pm.get("title_guess") or "").strip() or os.path.splitext(pdf)[0]
    figs = [f for f in (pm.get("figures_tables") or []) if f.get("crop_png") and os.path.exists(f["crop_png"])]
    RES = re.compile(r"error|accura|perform|result|compar|ablat|score|loss|mse|benchmark|speed|time|evaluat", re.I)
    METH = re.compile(r"overview|architecture|framework|pipeline|illustrat|schematic|diagram|example of|method|approach", re.I)
    pics = [f for f in figs if f.get("kind") == "figure"]
    method_fig = next((f for f in pics if METH.search(f.get("caption") or "") and not RES.search(f.get("caption") or "")), None)
    method_fig = method_fig or next((f for f in pics if not RES.search(f.get("caption") or "")), None)
    rest = [f for f in figs if f is not method_fig]
    result_vis = sorted(rest, key=lambda f: (0 if RES.search(f.get("caption") or "") else 1, f.get("page") or 0))[:3]
    result_vis.sort(key=lambda f: f.get("page") or 0)

    def bullets(roles, n):
        out = []
        for r in roles:
            for k in by.get(r, []):
                if len(out) >= n:
                    break
                if any(k["text"] == o[1] for o in out):
                    continue
                out.append((fact(k, r if r in ("method", "results", "conclusion") else "claim"), k["text"]))
        return out

    slides = []

    def add(role, title_, pts=None, vis=None, note=None):
        s = dict(id="S%d" % (len(slides) + 1), role=role, title=title_)
        if pts:
            s["bullets"] = [dict(text=short(t), src=[i]) for i, t in pts]
            s["sources"] = [i for i, _ in pts]
        if vis:
            s["figure"] = dict(image=vis["crop_png"], ref=vis.get("ref"), caption=short(vis.get("caption") or "", 22),
                               kind="table_image" if vis.get("kind") == "table" else ("schematic" if vis is method_fig else "data"))
        s["notes"] = note or " ".join(t for _, t in (pts or [])) or (vis or {}).get("caption") or title_
        slides.append(s)
        return s

    add("title", title, note="Draft deck generated from %s." % pdf)
    secs = [s.get("heading") for s in (pm.get("sections") or []) if s.get("heading") and re.match(r"^\d+\.?\s", s["heading"])]
    if len(secs) >= 3:
        add("outline", L["outline"], pts=None)
        slides[-1]["bullets"] = [dict(text=re.sub(r"^\d+\.?\s*", "", h)) for h in secs[:6]]
    add("motivation", L["motivation"], bullets(["motivation", "summary", "context"], 3))
    add("method", L["method"], bullets(["method", "summary"], 3), vis=method_fig)
    res = bullets(["results"], 6)
    if result_vis:
        for j, v in enumerate(result_vis):
            add("results", "%s: %s" % (L["results"], v.get("ref") or ""), res[j * 2:(j + 1) * 2] or None, vis=v)
    elif res:
        add("results", L["results"], res[:4])
    lim = bullets(["limitation", "limitations"], 2)
    if lim:
        add("limitations", L["limitations"], lim)
    add("conclusion", L["conclusion"], bullets(["conclusion", "summary"], 3))
    if max_slides:
        while len(slides) > max_slides and any(s["role"] == "results" for s in slides[3:]):
            idx = max(i for i, s in enumerate(slides) if s["role"] == "results")
            slides.pop(idx)
    if mode == "talk":
        main = [s for s in slides if s["role"] != "title"]
        per = int(round(minutes * 60 / max(1, len(slides))))
        for i, s in enumerate(slides):
            s["time_s"] = per
            s["key_message"] = s["title"]
            if i + 1 < len(slides):
                s["transition"] = "Next: " + slides[i + 1]["title"]
    for i, s in enumerate(slides):
        s["id"] = "S%d" % (i + 1)
    spec = dict(meta=dict(title=title, lang=lang, duration_min=minutes, draft=True), slides=slides)
    plan = None
    if mode == "talk":
        plan = talk_plan(prep, slides, facts, by, minutes)
    return spec, dict(facts=facts), plan


def talk_plan(prep, slides, facts, by, minutes):
    """Minimal plan: sections from slide roles, Q&A answered only with verbatim paper sentences (fact ids)."""
    cons = prep.get("constraints") or {}
    aud = (cons.get("audience") or {}).get("value") or "not stated in the task (assumed: peers in the field)"
    groups = []
    for s in slides:
        if groups and groups[-1][0] == s["role"]:
            groups[-1][1].append(s)
        else:
            groups.append((s["role"], [s]))
    secs = [dict(name=r, minutes=round(sum(x.get("time_s", 0) for x in ss) / 60.0, 2), slides=[x["id"] for x in ss],
                 key_message=ss[0]["title"], evidence_strength="strong" if r in ("results", "method") else "moderate")
            for r, ss in groups]
    fid = {f["text"]: f["id"] for f in facts}
    qa, doubts = [], []
    for typ, roles, q in (("mechanism", ["method", "summary"], "How does the method work, in one sentence?"),
                          ("data", ["results"], "What is the strongest quantitative result?"),
                          ("limits", ["limitation", "limitations", "conclusion"], "What does the paper not show / what are the limits?")):
        k = next((k for r in roles for k in by.get(r, []) if k["text"] in fid), None)
        if k is None:
            k = next((k for r in roles for k in by.get(r, [])), None)
            if k is None:
                continue
            facts.append(dict(id="F%d" % (len(facts) + 1), kind=typ, text=k["text"],
                              source=dict(file=facts[0]["source"]["file"] if facts else "", loc=k.get("loc"), ref="",
                                          quote=" ".join(k["text"].split()[:30]))))
            fid[k["text"]] = facts[-1]["id"]
        doubts.append(q)
        qa.append(dict(type=typ, doubt=len(doubts) - 1, question=q, answer_points=[short(k["text"], 24)],
                       evidence=[fid[k["text"]]], backup_slide=None))
    return dict(goal="Draft: the audience can state the paper's problem, method and main result after %s minutes" % minutes,
                occasion="talk", duration_min=minutes, audience=dict(who=aud, knows="not stated", cares_about="not stated",
                likely_doubts=doubts), sections=secs, qa=qa, cuts=[])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--mode", default="auto")
    a = ap.parse_args()
    t0 = time.time()
    log = open(os.path.join(a.work, "draft.log"), "a", encoding="utf-8")

    def say(*x):
        print(*x, file=log, flush=True)

    try:
        prep = json.load(open(os.path.join(a.work, "prepare.json"), encoding="utf-8"))
    except Exception as e:
        say("no prepare.json:", e)
        return 1
    mode = detect_mode(prep) if a.mode == "auto" else a.mode
    pm_path = os.path.join(a.work, "paper_map.json")
    if mode == "edit" or not os.path.exists(pm_path):
        say("draft skipped: mode=%s paper_map=%s" % (mode, os.path.exists(pm_path)))
        return 0
    pm = json.load(open(pm_path, encoding="utf-8"))
    cons = prep.get("constraints") or {}

    def cval(k):
        v = (cons.get(k) or {}).get("value")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", json.dumps(v))]
        return int(max(nums)) if nums else None
    minutes = cval("minutes") or 15
    max_slides = cval("slides")
    lang = str((cons.get("language") or {}).get("value") or (cons.get("lang") or {}).get("value") or "")
    if not lang:
        try:
            tt = open(prep.get("task") or "", encoding="utf-8", errors="ignore").read(4000)
        except Exception:
            tt = ""
        lang = "zh" if len(re.findall(r"[\u4e00-\u9fff]", tt)) > 200 else "en"
    lang = "zh" if re.search(r"zh|中文|chinese", lang, re.I) else "en"
    spec, facts, plan = build(prep, pm, mode, minutes, max_slides, lang)
    dw = os.path.join(a.work, "draft")
    os.makedirs(dw, exist_ok=True)
    sp, fp = os.path.join(dw, "spec.json"), os.path.join(dw, "facts.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(facts, open(fp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pp = None
    if plan:
        pp = os.path.join(dw, "talk_plan.json")
        json.dump(plan, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    src_deck = (prep.get("decks") or [None])[0] if mode == "template" else None
    if src_deck:   # the task's own draft deck moved into the template (text kept exactly) beats a deck from the paper
        args = [sys.executable, os.path.join(HERE, "deck_template.py"), "to_spec", "--pptx", src_deck, "--out", sp,
                "--media", os.path.join(dw, "media")]
        prev = os.path.join(a.work, "start_render", "previews")
        if os.path.isdir(prev):
            args += ["--previews", prev]
        r = subprocess.run(args, capture_output=True, text=True, timeout=300)
        say("to_spec:", (r.stdout + r.stderr)[-800:])
        if r.returncode != 0 or not os.path.exists(sp):
            json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            src_deck = None
    out = os.path.join(a.work, "draft_out")
    if os.path.isdir(out):
        shutil.rmtree(out)
    case = os.path.join(a.input, "case.json")
    cmd = [sys.executable, os.path.join(HERE, "deck_make.py"), "--spec", sp, "--outdir", out,
           "--work", dw, "--input", a.input, "--mode", {"talk": "talk", "template": "template"}.get(mode, "paper"),
           "--status", "partial", "--uncertainty", NOTE, "--draft"]
    if src_deck:
        cmd += ["--preserve-from", src_deck]
    else:
        cmd += ["--facts", fp]
    if pp:
        cmd += ["--plan", pp]
    if os.path.exists(case):
        cmd += ["--case", case]
    if mode == "template" and prep.get("templates"):
        cmd += ["--template", prep["templates"][0]]
    if mode == "talk":
        cmd += ["--duration-min", str(minutes)]
    if os.path.exists(pm_path):
        cmd += ["--paper-map", pm_path]
    mats = glob.glob(os.path.join(a.work, "materials", "*.txt")) + glob.glob(os.path.join(a.input, "**", "*.csv"), recursive=True)
    if mats:
        cmd += ["--materials"] + mats
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    say(r.stdout[-3000:], r.stderr[-1500:])
    # copy only while no final run has started and the output holds nothing but an earlier draft
    if os.path.exists(os.path.join(a.work, ".final_started")):
        say("final run started: draft not copied")
        return 0
    if os.path.exists(os.path.join(a.outdir, "result.json")) and not os.path.exists(os.path.join(a.outdir, "draft_manifest.json")):
        say("output already holds a final result: draft not copied")
        return 0
    if not os.path.exists(os.path.join(out, "deck.pptx")):
        say("draft build failed")
        return 1
    os.makedirs(a.outdir, exist_ok=True)
    copied = []
    for root, _, names in os.walk(out):
        for n in names:
            src = os.path.join(root, n)
            rel = os.path.relpath(src, out)
            dst = os.path.join(a.outdir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)
    for n in ("spec.json", "facts.json"):
        shutil.copy2(os.path.join(dw, n), os.path.join(a.outdir, "draft_" + n))
        copied.append("draft_" + n)
    json.dump(dict(note=NOTE, files=sorted(copied), seconds=round(time.time() - t0, 1)),
              open(os.path.join(a.outdir, "draft_manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    say("draft delivered: %d files in %.0f s" % (len(copied), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
