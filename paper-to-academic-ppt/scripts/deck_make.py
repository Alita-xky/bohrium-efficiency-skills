#!/usr/bin/env python3
"""deck_make.py - one call for check -> build -> render (-> template audit): the deliverable loop.

Usage:
  python3 deck_make.py --spec /app/work/spec.json [--spec part2.json] --facts /app/work/facts.json \
      --outdir /app/output [--name deck] [--template T.pptx] [--mode paper|talk|template|edit] \
      [--materials '/app/work/materials/*.txt' /input/*.csv] [--paper-map /app/work/paper_map.json] [--plan talk_plan.json] \
      [--min-slides N] [--max-slides N] [--duration-min M] [--backend auto|pptx|xml]
      [--case /input/case.json [--fill /app/work/fill.json] [--uncertainty "..."] [--set field=value]]

Runs deck_check.py (spec vs materials, evidence_map.json/.md), deck_build.py (deck .pptx, build_report.json,
slides_outline.md), deck_render.py (PDF, previews/, contact_sheet.png, render_report.json) and, with a template,
deck_template.py audit (compliance.json); with --case also deck_fill.py (version_record.json, contract mapping)
and make_result.py (result.json). The deck is always built - even when the check fails - so an honest
partial deliverable exists early. Prints every FAIL in one list; fix them in the spec and run again.
"""
import argparse, glob, json, os, signal, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET_S = 900


def budget_line(work):
    """Seconds since deck_prepare started (the run's clock) and what to do with the rest of the budget."""
    try:
        used = time.time() - float(open(os.path.join(work, ".t0")).read().strip())
    except Exception:
        return None
    if used > 0.8 * BUDGET_S:
        tip = "stop now: the deliverable above is final; at most one more run of this command if a FAIL blocks a file"
    elif used > 0.6 * BUDGET_S:
        tip = "finish: fix only FAILs, run this command once more, then stop (no new polishing)"
    else:
        tip = "time left for one fix round and one look at the previews"
    return "[budget] ~%d s of %d s used since prepare; %s" % (used, BUDGET_S, tip)


def stop_draft(work):
    """The agent's own run takes over /app/output: stop a still-running safety draft (deck_draft.py)."""
    open(os.path.join(work, ".final_started"), "w").write(str(time.time()))
    pidf = os.path.join(work, "draft.pid")
    if os.path.exists(pidf):
        try:
            os.killpg(int(open(pidf).read().strip()), signal.SIGTERM)
        except Exception:
            pass


def drop_draft_leftovers(outdir, t_start):
    """Remove files only the safety draft wrote (older than this run), so /app/output holds one coherent deliverable."""
    mp = os.path.join(outdir, "draft_manifest.json")
    if not os.path.exists(mp):
        return 0
    n = 0
    try:
        files = json.load(open(mp, encoding="utf-8")).get("files") or []
    except Exception:
        files = []
    for rel in files:
        p = os.path.join(outdir, rel)
        if os.path.isfile(p) and os.path.getmtime(p) < t_start:
            os.remove(p)
            n += 1
    os.remove(mp)
    return n


def run(args, timeout=600):
    p = subprocess.run([sys.executable] + args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def first_json(out):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def _jload(path):
    try:
        sys.path.insert(0, HERE)
        from make_result import load_json_lenient
        return load_json_lenient(path)
    except Exception:
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            return None


def apply_defaults(a):
    """One short command: every path the run needs is found in /app/work and /input, and the spec may carry the talk
    plan ("plan"), extra contract answers ("fill") and uncertainty notes ("uncertainty") so the agent writes ONE file."""
    notes = []
    if not a.spec:
        a.spec = [os.path.join(a.work, "spec.json")]
    if not a.facts and os.path.exists(os.path.join(a.work, "facts.json")):
        a.facts = os.path.join(a.work, "facts.json")
        notes.append("--facts " + a.facts)
    if not a.case and not a.draft and os.path.exists(os.path.join(a.input, "case.json")):
        a.case = os.path.join(a.input, "case.json")
        notes.append("--case " + a.case + " (records + result.json in this run)")
    spec = _jload(a.spec[0]) if os.path.exists(a.spec[0]) else None
    if isinstance(spec, dict):
        plan = spec.get("plan") or spec.get("talk_plan")
        if isinstance(plan, dict) and not a.plan:
            a.plan = os.path.join(a.work, "talk_plan.json")
            json.dump(plan, open(a.plan, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            notes.append("talk plan taken from spec.json -> " + a.plan)
        fill = spec.get("fill")
        if isinstance(fill, dict) and fill and not a.fill:
            own_p = os.path.join(a.work, "fill.json")          # the agent's own answers file (if any) wins over spec "fill"
            own = _jload(own_p) if os.path.exists(own_p) else None
            fill = dict(fill, **own) if isinstance(own, dict) else fill
            a.fill = os.path.join(a.work, "fill_from_spec.json")
            json.dump(fill, open(a.fill, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            notes.append("contract answers taken from spec.json \"fill\" (%s)" % ", ".join(list(fill)[:8]))
        unc = spec.get("uncertainty")
        if isinstance(unc, list):
            a.uncertainty += [u if isinstance(u, str) else json.dumps(u, ensure_ascii=False) for u in unc if u]
    if not a.fill and not a.draft and os.path.exists(os.path.join(a.work, "fill.json")):
        a.fill = os.path.join(a.work, "fill.json")
        notes.append("--fill " + a.fill)
    if not a.plan and a.mode == "talk" and os.path.exists(os.path.join(a.work, "talk_plan.json")):
        a.plan = os.path.join(a.work, "talk_plan.json")
        notes.append("--plan " + a.plan)
    prep = _jload(os.path.join(a.work, "prepare.json")) or {}
    if not a.duration_min and a.mode == "talk":
        m = str(((prep.get("constraints") or {}).get("minutes") or {}).get("value") or "")
        if m.strip().isdigit():
            a.duration_min = m.strip()
            notes.append("--duration-min %s (from task; pass the flag if the task says otherwise)" % a.duration_min)
    if not a.template and a.mode != "edit" and prep.get("templates"):
        t = prep["templates"][0]
        if t and str(t).lower() != "none" and os.path.exists(t):
            a.template = t
            notes.append("--template %s (given in /input; the deck is built on it)" % t)
    if a.template and str(a.template).lower() == "none":
        a.template = None
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", default=None, help="default /app/work/spec.json")
    ap.add_argument("--facts", default=None)
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--name", default="deck")
    ap.add_argument("--template", default=None)
    ap.add_argument("--mode", default="paper")
    ap.add_argument("--materials", nargs="+", default=None)
    ap.add_argument("--paper-map", default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--min-slides", default=None)
    ap.add_argument("--max-slides", default=None)
    ap.add_argument("--duration-min", default=None)
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--keep-template-slides", action="store_true")
    ap.add_argument("--preserve-from", default=None, help="source deck whose content must survive (writes preserve.json)")
    ap.add_argument("--case", default=None, help="also write version_record.json + result.json (deck_fill + make_result)")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--fill", default=None, help="your own answers keyed by contract field (win over the automatic ones)")
    ap.add_argument("--uncertainty", action="append", default=[])
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--status", default=None, choices=["complete", "partial", "failed"])
    ap.add_argument("--draft", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    t_start = time.time()
    for note in apply_defaults(a):
        print("default: " + note)
    if not a.draft and os.path.isdir(a.work):
        stop_draft(a.work)
    os.makedirs(a.outdir, exist_ok=True)
    specs = sum([["--spec", s] for s in a.spec], [])
    facts = ["--facts", a.facts] if a.facts else []
    mats = a.materials or (glob.glob("/app/work/materials/*.txt") + glob.glob("/input/*.csv") + glob.glob("/input/**/*.csv", recursive=True))
    pm = a.paper_map or ("/app/work/paper_map.json" if os.path.exists("/app/work/paper_map.json") else None)
    summary = {}
    fails = []
    chk = [os.path.join(HERE, "deck_check.py")] + specs + facts + ["--materials"] + mats + ["--mode", a.mode, "--out",
          os.path.join(a.outdir, "check_report.json"), "--map", os.path.join(a.outdir, "evidence_map.json")]
    for flag, v in (("--paper-map", pm), ("--plan", a.plan), ("--min-slides", a.min_slides), ("--max-slides", a.max_slides),
                    ("--duration-min", a.duration_min)):
        if v:
            chk += [flag, str(v)]
    rc, out = run(chk)
    summary["check"] = first_json(out)
    fails += ["check: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("FAIL")]
    warns = ["check: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("WARN")]
    pptx = os.path.join(a.outdir, a.name + ".pptx")
    bld = [os.path.join(HERE, "deck_build.py")] + specs + facts + ["--out", pptx, "--backend", a.backend]
    if a.template:
        bld += ["--template", a.template]
    if a.keep_template_slides:
        bld += ["--keep-template-slides"]
    if a.plan and a.mode == "talk":
        bld += ["--plan", a.plan]
    rc, out = run(bld)
    summary["build"] = first_json(out)
    if rc != 0 or not os.path.exists(pptx):
        print(json.dumps(dict(status="build_failed", log=out[-2000:]), ensure_ascii=False))
        return 1
    fails += ["build: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("FAIL")]
    rc, out = run([os.path.join(HERE, "deck_check.py"), "scale", "--pptx", pptx])  # sizes vs type_scale.json
    summary["type_scale"] = first_json(out)
    warns += ["check: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("WARN")]
    rnd = [os.path.join(HERE, "deck_render.py"), "--pptx", pptx, "--outdir", a.outdir, "--name", a.name]
    rc, out = run(rnd)
    summary["render"] = first_json(out)
    fails += ["render: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("FAIL")]
    warns += ["render: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("WARN")]
    if a.template:
        rc, out = run([os.path.join(HERE, "deck_template.py"), "audit", "--pptx", pptx, "--template", a.template,
                       "--out", os.path.join(a.outdir, "compliance.json")])
        summary["template_audit"] = first_json(out)
        fails += ["template: " + l.strip()[5:] for l in out.splitlines() if l.strip().startswith("FAIL") and " render " not in l]
    if a.preserve_from:
        rc, out = run([os.path.join(HERE, "deck_template.py"), "preserve", "--a", a.preserve_from, "--b", pptx,
                       "--out", os.path.join(a.outdir, "preserve.json")])
        summary["preserve"] = first_json(out)
        if summary["preserve"].get("status") != "pass":
            fails += ["preserve: " + l.strip() for l in out.splitlines() if l.strip().startswith("MISSING")] or \
                     ["preserve: content of the source deck missing (see preserve.json)"]
    status = "ok" if not fails else "needs_fix"
    print(json.dumps(dict(status=status, pptx=pptx, pdf=summary["render"].get("pdf_pages") and os.path.join(a.outdir, a.name + ".pdf"),
                          contact_sheet=summary["render"].get("contact_sheet"), n_fail=len(fails), n_warn=len(warns),
                          check=summary["check"].get("status"), build=summary["build"].get("status"),
                          backend=summary["build"].get("backend"), render=summary["render"].get("status"),
                          template=(summary.get("template_audit") or {}).get("status"),
                          compliance_rate=(summary.get("template_audit") or {}).get("compliance_rate"),
                          preserve=(summary.get("preserve") or {}).get("status")), ensure_ascii=False))
    for f in fails[:40]:
        print("  FAIL " + f)
    for w in warns[:15]:
        print("  WARN " + w)
    with open(os.path.join(a.outdir, "make_summary.json"), "w", encoding="utf-8") as f:
        json.dump(dict(status=status, steps=summary, fails=fails, warns=warns), f, ensure_ascii=False, indent=1)
    if a.case:   # records + result.json in the same call: a complete, honest deliverable exists after one command
        fl = [os.path.join(HERE, "deck_fill.py"), "--case", a.case, "--outdir", a.outdir, "--work", a.work, "--input", a.input,
              "--out", os.path.join(a.work, "fill_auto.json"), "--deck", pptx, "--result"]
        fl += sum([["--uncertainty", u] for u in a.uncertainty], []) + sum([["--set", x] for x in a.set], [])
        if a.status:
            fl += ["--status", a.status]
        if a.fill:
            fl += ["--fill", a.fill]
        os.makedirs(a.work, exist_ok=True)
        rc, out = run(fl)
        for line in out.splitlines():
            if line.startswith("{") and '"result"' in line:
                r = json.loads(line)
                print("RESULT " + json.dumps(dict(result=r.get("result"), status=r.get("status"), missing=r.get("missing")),
                                             ensure_ascii=False))
            elif line.startswith("{") and '"mapped"' in line:
                r = json.loads(line)
                print("CONTRACT mapped=%s unmapped=%s" % (r.get("mapped"), r.get("unmapped")))
            elif line.startswith(("UNMAPPED", "WARN", "FIX")):
                print(line)
        if "RESULT" not in out and '"result"' not in out:
            print("FAIL result: deck_fill/make_result did not run: " + out[-600:])
    if not a.draft:
        n = drop_draft_leftovers(a.outdir, t_start)
        if n:
            print("safety draft replaced (%d draft-only files removed)" % n)
    rounds = 1
    if not a.draft:
        rp = os.path.join(a.work, ".make_rounds")
        try:
            rounds = int(open(rp).read().strip() or 0) + 1
        except Exception:
            rounds = 1
        try:
            open(rp, "w").write(str(rounds))
        except Exception:
            pass
    if fails and rounds < 3:
        print("NEXT (round %d of max 3): fix each FAIL in spec.json (only those slides), then run this same command again" % rounds)
    elif fails:
        print("STOP: round %d - leave the remaining FAILs; they are recorded in check_report.json / result.json. Report and end." % rounds)
    elif not a.draft:
        print("DONE: no FAIL - the deliverable in %s is complete. Optionally look at contact_sheet.png once; then report and end "
              "(do not rebuild for polish)." % a.outdir)
    b = budget_line(a.work)
    if b and not a.draft:
        print(b)
    return 0


if __name__ == "__main__":
    sys.exit(main())
