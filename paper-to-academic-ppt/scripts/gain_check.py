#!/usr/bin/env python3
"""gain_check.py - measured, reproducible comparison on the SYNTHETIC example (not an official case result).

  python3 gain_check.py [--example ../examples/minimal] [--out DIR] [--md report.md]

Sections run when the example provides their inputs (the scripts are the same in all four deck Skills):
  deck   (spec.json + facts.json): A = a python-pptx script that writes the same titles/bullets/notes into the
         default template (no checks, no records) vs B = deck_make.py --case (check, build, LibreOffice render,
         records, result.json); then one injected defect per run on the same spec: flagged before delivery?
  template (make_example_template.py): compliance of the draft vs the adapted deck, content preserved
  edit   (ops.json + make_example_deck.py): the same text edit done by python-pptx open/save (A) vs
         deck_edit.py round (B): package parts left byte-identical, ops verified, version snapshot
  contract: 6 output_contract shapes -> fields filled automatically (deck_fill + make_result)
Everything is measured on this run (gain_report.json); nothing is asserted. It does NOT run the platform's
DeepSeek baseline agent and says nothing about model quality or slide design.
"""
import argparse, copy, glob, hashlib, json, os, shutil, subprocess, sys, tempfile, time, zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = lambda n: os.path.join(HERE, n)  # noqa: E731


def run(args, timeout=900):
    p = subprocess.run([sys.executable, "-B"] + args, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def first_json(out):
    for line in out.splitlines():
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def jl(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def parts(pptx):
    with zipfile.ZipFile(pptx) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}


def deck_facts(pptx):
    """slides, notes, pictures, tables, placeholder use - read with the stdlib (zip + XML text)."""
    with zipfile.ZipFile(pptx) as z:
        names = z.namelist()
        slides = sorted(n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
        notes = [n for n in names if n.startswith("ppt/notesSlides/") and n.endswith(".xml")]
        xml = [z.read(n).decode("utf-8", "replace") for n in slides]
        nxml = [z.read(n).decode("utf-8", "replace") for n in notes]
    return dict(slides=len(slides), slides_with_notes=sum(1 for x in nxml if "<a:t>" in x),
                pictures=sum(x.count("<p:pic>") for x in xml), tables=sum(x.count("<a:tbl>") for x in xml),
                title_placeholders=sum(1 for x in xml if 'type="title"' in x or 'type="ctrTitle"' in x))


# ------------------------------------------------------------------ A: plain python-pptx script
def baseline_deck(spec, out):
    from pptx import Presentation
    prs = Presentation()
    for sl in spec.get("slides", []):
        s = prs.slides.add_slide(prs.slide_layouts[1 if sl.get("bullets") else 0])
        s.shapes.title.text = sl.get("title", "")
        body = [b["text"] if isinstance(b, dict) else str(b) for b in sl.get("bullets", []) or sl.get("subtitle", []) or []]
        if len(s.placeholders) > 1 and body:
            tf = s.placeholders[1].text_frame
            tf.text = body[0]
            for b in body[1:]:
                tf.add_paragraph().text = b
        if sl.get("notes"):
            s.notes_slide.notes_text_frame.text = sl["notes"]
    prs.save(out)
    return out


# ------------------------------------------------------------------ defects on the spec / facts
def slides(spec):
    return spec.get("slides", [])


def body_slide(spec):
    """first content slide with bullets (title / outline / section slides need no sources or claims)"""
    return next((s for s in slides(spec) if s.get("bullets") and s.get("role") not in
                 ("title", "outline", "agenda", "overview", "section", "qa", "thanks", "references")), None)


def d_number(spec, facts):
    s = body_slide(spec)
    s["bullets"].append({"text": "Relative WER fell by 37.9% on every language", "src": list(s.get("sources", []))[:1]})
    return True


def d_figref(spec, facts):
    s = body_slide(spec)
    s["bullets"].append({"text": "Fig. 9 shows the per-language breakdown", "src": list(s.get("sources", []))[:1]})
    return True


def d_quote(spec, facts):
    for f in facts.get("facts", []):
        q = (f.get("source") or {}).get("quote")
        if q and len(q.split()) > 4:
            f["source"]["quote"] = " ".join(reversed(q.split()))
            return True
    return False


def d_sources(spec, facts):
    s = body_slide(spec)
    s["sources"] = []
    for b in s.get("bullets", []):
        if isinstance(b, dict):
            b.pop("src", None)
    return True


def d_density(spec, facts):
    s = body_slide(spec)
    s["bullets"] = (s["bullets"] * 4)[:11]
    return True


def d_count(spec, facts):
    extra = [copy.deepcopy(x) for x in slides(spec) if x.get("role") == "results"]
    for i, x in enumerate(extra * 3):
        x = copy.deepcopy(x)
        x["id"] = "dup%d" % i
        spec["slides"].insert(-1, x)
    return len(spec["slides"]) > int(spec.get("meta", {}).get("max_slides", 12))


def d_notes(spec, facts):
    s = body_slide(spec)
    s.pop("notes", None)
    return True


def d_overflow(spec, facts):
    s = body_slide(spec)
    s["bullets"][0] = dict(s["bullets"][0]) if isinstance(s["bullets"][0], dict) else {"text": s["bullets"][0]}
    s["bullets"][0]["text"] = " ".join(["the sparse gated adapter routes each frame to a small subset of experts"] * 7)
    return True


DEFECTS = [
    ("fabricated_number", "a bullet states a number that is in no material", d_number, ["number"]),
    ("figure_not_in_paper", "a slide cites a figure number the paper does not have", d_figref, ["fig_ref", "Fig. 9"]),
    ("quote_paraphrased", "an evidence quote is no longer verbatim", d_quote, ["fact_quote", "quote"]),
    ("slide_without_sources", "a content slide cites no evidence", d_sources, ["sources"]),
    ("too_many_bullets", "11 bullets on one slide", d_density, ["density"]),
    ("slide_limit_exceeded", "more slides than the task allows", d_count, ["slide_count"]),
    ("speaker_notes_missing", "a content slide has no speaker notes", d_notes, ["notes"]),
    ("paragraph_bullet", "an 80-word paragraph pasted as one bullet", d_overflow, ["overflow", "density", "long bullet"]),
]


def section_deck(ex, base, rep):
    spec0, facts0 = jl(os.path.join(ex, "spec.json")), jl(os.path.join(ex, "facts.json"))
    case = os.path.join(ex, "case.json")
    mode = (spec0.get("meta") or {}).get("mode", "paper")
    extra_files = [f for f in glob.glob(os.path.join(ex, "*")) if os.path.basename(f) not in
                   ("spec.json", "facts.json", "README.md", "case.json") and not f.endswith(".py")]

    def make_case(tag, spec, facts):
        I, W, O = (os.path.join(base, tag, x) for x in ("input", "work", "output"))
        for d in (I, W):
            os.makedirs(d, exist_ok=True)
        run([os.path.join(ex, "make_example_pdf.py"), I])
        shutil.copy(case, os.path.join(I, "case.json"))
        for f in extra_files:
            if os.path.isfile(f):
                shutil.copy(f, I)
        for f in glob.glob(os.path.join(ex, "talk_plan.json")):
            shutil.copy(f, W)
        json.dump(spec, open(os.path.join(W, "spec.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(facts, open(os.path.join(W, "facts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        run([S("deck_prepare.py"), "--input", I, "--work", W])
        return I, W, O

    def make(I, W, O):
        cmd = [S("deck_make.py"), "--spec", os.path.join(W, "spec.json"), "--facts", os.path.join(W, "facts.json"),
               "--outdir", O, "--materials"] + glob.glob(os.path.join(W, "materials", "*.txt")) + glob.glob(os.path.join(I, "*.csv")) + \
              ["--paper-map", os.path.join(W, "paper_map.json"), "--mode", mode, "--max-slides",
               str((spec0.get("meta") or {}).get("max_slides", 12)), "--case", os.path.join(I, "case.json"), "--work", W, "--input", I]
        if os.path.exists(os.path.join(W, "talk_plan.json")):
            cmd += ["--plan", os.path.join(W, "talk_plan.json")]
            if (spec0.get("meta") or {}).get("duration_min"):
                cmd += ["--duration-min", str(spec0["meta"]["duration_min"])]
        t = time.time()
        rc, out = run(cmd)
        return out, time.time() - t

    I, W, O = make_case("clean", spec0, facts0)
    out, secs = make(I, W, O)
    res = jl(os.path.join(O, "result.json")) or {}
    cr = jl(os.path.join(O, "check_report.json")) or {}
    rr = jl(os.path.join(O, "render_report.json")) or {}
    emap = jl(os.path.join(O, "evidence_map.json"))
    B = dict(deck_facts(os.path.join(O, "deck.pptx")), evidence_map=bool(emap), facts_verified="%s/%s" % (cr.get("facts_verified"), cr.get("n_facts")),
             numbers_checked=cr.get("numbers_checked"), pdf_pages=rr.get("pdf_pages"), png_previews=len(rr.get("previews") or []),
             result_json=bool(res), status=res.get("status"),
             contract_fields_filled="%d/%d" % (sum(1 for f in res.get("contract_fields", []) if not (isinstance(res.get(f), dict) and
                                                    res[f].get("status") == "not_provided")), len(res.get("contract_fields", []))),
             seconds=round(secs, 1))
    A = {}
    try:
        t = time.time()
        pa = baseline_deck(spec0, os.path.join(base, "clean", "A_baseline.pptx"))
        A = dict(deck_facts(pa), evidence_map=False, facts_verified="0/%s" % cr.get("n_facts"), numbers_checked=0, pdf_pages=None,
                 png_previews=0, result_json=False, contract_fields_filled="0/%d" % len(res.get("contract_fields", [])),
                 seconds=round(time.time() - t, 1))
    except Exception as e:
        A = dict(error="python-pptx baseline not run: %r" % e)
    rep["deck"] = dict(A_python_pptx_script=A, B_skill=B)
    rows = []
    for key, desc, mut, expect in DEFECTS:
        sp, fc = copy.deepcopy(spec0), copy.deepcopy(facts0)
        if not mut(sp, fc):
            rows.append(dict(defect=key, description=desc, applicable=False))
            continue
        I, W, O = make_case(key, sp, fc)
        out, _ = make(I, W, O)
        flagged = [l.strip() for l in out.splitlines() if l.strip().startswith(("FAIL", "WARN"))
                   and any(x.lower() in l.lower() for x in expect)]
        r = jl(os.path.join(O, "result.json")) or {}
        try:
            baseline_deck(sp, os.path.join(base, key, "A.pptx"))
            a_out = "deck written, no message"
        except Exception as e:
            a_out = "script crashed (%s)" % type(e).__name__
        rows.append(dict(defect=key, description=desc, applicable=True, A_python_pptx_script=a_out,
                         B_skill=("FAIL" if any(l.startswith("FAIL") for l in flagged) else "WARN") if flagged else "none",
                         B_detected=bool(flagged), B_status=r.get("status"), B_evidence=flagged[0][:170] if flagged else None))
    app = [x for x in rows if x.get("applicable")]
    rep["deck_defects"] = rows
    rep["deck_defect_detection"] = dict(B_detected=sum(1 for x in app if x["B_detected"]), n=len(app), A_detected=0)
    return os.path.join(base, "clean")


def section_template(ex, base, rep):
    I, W, O = (os.path.join(base, "template", x) for x in ("input", "work", "output"))
    for d in (I, W, O):
        os.makedirs(d, exist_ok=True)
    run([os.path.join(ex, "make_example_template.py"), I])
    shutil.copy(os.path.join(ex, "case.json"), I)
    run([S("deck_prepare.py"), "--input", I, "--work", W])
    draft = next(iter(glob.glob(os.path.join(I, "draft*.pptx")) or glob.glob(os.path.join(I, "*.pptx"))), None)
    tpl = next(iter(glob.glob(os.path.join(I, "*.potx")) + [p for p in glob.glob(os.path.join(I, "*.pptx")) if p != draft]), None)
    if not (draft and tpl):
        rep["template"] = dict(skipped="example template/draft not found")
        return None
    rc, out = run([S("deck_template.py"), "audit", "--pptx", draft, "--template", tpl, "--out", os.path.join(O, "compliance_before.json")])
    before = jl(os.path.join(O, "compliance_before.json")) or {}
    run([S("deck_template.py"), "to_spec", "--pptx", draft, "--out", os.path.join(W, "spec.json")])
    t = time.time()
    rc, out = run([S("deck_make.py"), "--spec", os.path.join(W, "spec.json"), "--outdir", O, "--template", tpl, "--mode", "template",
                   "--materials"] + glob.glob(os.path.join(W, "materials", "*.txt")) + ["--preserve-from", draft,
                   "--case", os.path.join(I, "case.json"), "--work", W, "--input", I])
    after = jl(os.path.join(O, "compliance.json")) or {}
    pres = jl(os.path.join(O, "preserve.json")) or {}
    res = jl(os.path.join(O, "result.json")) or {}
    rep["template"] = dict(compliance_rate_draft=before.get("compliance_rate"), compliance_rate_adapted=after.get("compliance_rate"),
                           issues_draft=before.get("issues_by_rule"), issues_adapted=after.get("issues_by_rule"),
                           content_preserved=pres.get("status"), text_preserved_rate=pres.get("text_preserved_rate"),
                           draft=deck_facts(draft), adapted=deck_facts(os.path.join(O, "deck.pptx")) if os.path.exists(os.path.join(O, "deck.pptx")) else None,
                           status=res.get("status"), seconds=round(time.time() - t, 1),
                           note="A here = the draft deck before adaptation (what exists without the Skill)")
    return os.path.join(base, "template")


def section_edit(ex, base, rep):
    I, W, O = (os.path.join(base, "edit", x) for x in ("input", "work", "output"))
    for d in (I, W):
        os.makedirs(d, exist_ok=True)
    run([os.path.join(ex, "make_example_deck.py"), I])
    shutil.copy(os.path.join(ex, "case.json"), I)
    draft = next(iter(glob.glob(os.path.join(I, "*.pptx"))), None)
    ops = jl(os.path.join(ex, "ops.json")) or []
    op = next((o for o in ops if o.get("op") == "replace_text"), None)
    if not (draft and op):
        rep["edit"] = dict(skipped="example deck / replace_text op not found")
        return None
    json.dump([op], open(os.path.join(W, "ops1.json"), "w", encoding="utf-8"), ensure_ascii=False)
    run([S("deck_prepare.py"), "--input", I, "--work", W])
    rc, out = run([S("deck_edit.py"), "round", "--pptx", draft, "--ops", os.path.join(W, "ops1.json"), "--outdir", O,
                   "--materials"] + glob.glob(os.path.join(W, "materials", "*.txt")) + glob.glob(os.path.join(I, "*.csv")) +
                  ["--case", os.path.join(I, "case.json"), "--work", W, "--input", I, "--note", "gain_check single edit"])
    rj = next((json.loads(l) for l in out.splitlines() if l.startswith("{") and '"ops_ok"' in l), {})
    p0, pB = parts(draft), parts(os.path.join(O, "deck.pptx"))
    B = dict(parts_total=len(p0), parts_byte_identical=sum(1 for k, v in p0.items() if pB.get(k) == v),
             op_applied=rj.get("ops_ok"), version_snapshots=len(glob.glob(os.path.join(O, "versions", "v*.pptx"))),
             render=rj.get("render"), check=rj.get("check"), result=(jl(os.path.join(O, "result.json")) or {}).get("status"))
    A = {}
    try:
        from pptx import Presentation
        prs = Presentation(draft)
        s = prs.slides[op["slide"] - 1]
        for sh in s.shapes:
            if sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    for r in para.runs:
                        if op["find"] in r.text:
                            r.text = r.text.replace(op["find"], op["replace"])
        pa = os.path.join(base, "edit", "A_edit.pptx")
        prs.save(pa)
        pA = parts(pa)
        A = dict(parts_total=len(p0), parts_byte_identical=sum(1 for k, v in p0.items() if pA.get(k) == v), op_applied=1,
                 version_snapshots=0, render=None, result=None)
    except Exception as e:
        A = dict(error="python-pptx edit not run: %r" % e)
    rows = []
    for key, desc, bad, expect in (
            ("edit_writes_unsupported_number", "the edit writes a value that is in no material",
             dict(op, replace="31.6%"), ["number"]),
            ("edit_addresses_missing_slide", "the edit targets a slide that does not exist", dict(op, slide=99), ["error", "slide"])):
        Wd, Od = os.path.join(base, "edit_" + key, "work"), os.path.join(base, "edit_" + key, "output")
        os.makedirs(Wd, exist_ok=True)
        json.dump([bad], open(os.path.join(Wd, "ops.json"), "w", encoding="utf-8"), ensure_ascii=False)
        rc, out = run([S("deck_edit.py"), "round", "--pptx", draft, "--ops", os.path.join(Wd, "ops.json"), "--outdir", Od,
                       "--materials"] + glob.glob(os.path.join(W, "materials", "*.txt")) + glob.glob(os.path.join(I, "*.csv")) +
                      ["--case", os.path.join(I, "case.json"), "--work", Wd, "--input", I])
        rjd = next((json.loads(l) for l in out.splitlines() if l.startswith("{") and '"ops_ok"' in l), {})
        flagged = [l.strip() for l in out.splitlines() if "FAIL" in l and any(x in l.lower() for x in expect)]
        if rjd.get("ops_errors"):
            flagged.insert(0, "ops_errors: %s" % json.dumps(rjd["ops_errors"])[:150])
        try:
            from pptx import Presentation
            prs = Presentation(draft)
            sl = prs.slides[bad["slide"] - 1]
            for sh in sl.shapes:
                if sh.has_text_frame:
                    for para in sh.text_frame.paragraphs:
                        for r in para.runs:
                            r.text = r.text.replace(bad["find"], bad["replace"])
            prs.save(os.path.join(base, "edit_" + key, "A.pptx"))
            a_out = "deck written, no message"
        except Exception as e:
            a_out = "script crashed (%s)" % type(e).__name__
        rows.append(dict(defect=key, description=desc, A_python_pptx_open_save=a_out, B_detected=bool(flagged),
                         B_status=(jl(os.path.join(Od, "result.json")) or {}).get("status"), B_evidence=flagged[0][:170] if flagged else None))
    rep["edit"] = dict(op=dict(op=op["op"], slide=op["slide"], find=op["find"], replace=op["replace"]),
                       A_python_pptx_open_save=A, B_deck_edit_round=B, defects=rows)
    return os.path.join(base, "edit")


SHAPES = {
    "dict_of_descriptions": {"deck": "pptx + previews", "evidence_map": "", "render_and_version_record": "", "uncertainty": "", "status": ""},
    "list_of_names": ["editable_deck", "evidence_mapping", "render_record", "speaker_notes", "uncertainty", "status"],
    "json_schema": {"type": "object", "required": ["pptx", "preview_pdf", "source_mapping", "status"],
                    "properties": {"pptx": {}, "preview_pdf": {}, "source_mapping": {}, "status": {}}},
    "required_fields_wrapper": {"required_fields": ["slides", "evidence_map", "export", "version_record", "limitations"], "notes": "x"},
    "list_of_objects": [{"name": "presentation_file"}, {"name": "rendered_preview"}, {"name": "change_log"}, {"name": "status"}],
    "chinese_names": {"可编辑演示文件": "", "证据映射": "", "渲染与版本记录": "", "不确定性": "", "状态": ""},
}


def section_contract(run_dir, base, rep):
    rows = []
    I, W, O = (os.path.join(run_dir, x) for x in ("input", "work", "output"))
    for k, c in SHAPES.items():
        case = jl(os.path.join(I, "case.json")) or {}
        case["output_contract"] = c
        cp = os.path.join(base, "contract_%s.json" % k)
        json.dump(case, open(cp, "w", encoding="utf-8"), ensure_ascii=False)
        od = os.path.join(base, "contract_" + k)
        shutil.copytree(O, od)
        run([S("deck_fill.py"), "--case", cp, "--outdir", od, "--work", W, "--input", I, "--out", os.path.join(od, "fill.json"), "--result"])
        r = jl(os.path.join(od, "result.json")) or {}
        fl = r.get("contract_fields", [])
        rows.append(dict(shape=k, fields=len(fl), filled_automatically=sum(1 for f in fl if not (isinstance(r.get(f), dict)
                                                                                                and r[f].get("status") == "not_provided")),
                         case_id_kept=r.get("case_id") not in (None, "unknown")))
    rep["contract_shapes"] = rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--example", default=os.path.join(ROOT, "examples", "minimal"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None)
    a = ap.parse_args()
    ex = os.path.abspath(a.example)
    base = a.out or tempfile.mkdtemp(prefix="deck_gain_")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    t0 = time.time()
    rep = dict(generated=time.strftime("%Y-%m-%d %H:%M"), example=os.path.relpath(ex, ROOT), python=sys.version.split()[0],
               synthetic=True, not_measured="the platform's DeepSeek baseline agent, model quality, slide design judgement")
    run_dir = None
    if os.path.exists(os.path.join(ex, "spec.json")) and os.path.exists(os.path.join(ex, "facts.json")):
        run_dir = section_deck(ex, base, rep)
    if os.path.exists(os.path.join(ex, "make_example_template.py")):
        run_dir = section_template(ex, base, rep) or run_dir
    if os.path.exists(os.path.join(ex, "ops.json")) and os.path.exists(os.path.join(ex, "make_example_deck.py")):
        run_dir = section_edit(ex, base, rep) or run_dir
    if run_dir:
        section_contract(run_dir, base, rep)
    rep["seconds_total"] = round(time.time() - t0, 1)
    rep["reproduce"] = "python3 scripts/gain_check.py --out <dir>"
    jp = os.path.join(base, "gain_report.json")
    json.dump(rep, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(to_md(rep))
    print(json.dumps(dict(report=jp, deck=rep.get("deck"), defects=rep.get("deck_defect_detection"), template=rep.get("template"),
                          edit=rep.get("edit"), seconds=rep["seconds_total"]), ensure_ascii=False)[:3000])
    return 0


def to_md(r):
    L = ["# Measured gain (SYNTHETIC example)", "",
         "Generated %s by `%s` on the shipped synthetic example (`%s`). Not an official case result and not the "
         "platform's baseline-agent comparison; it measures, on identical input, what the scripts add to a plain "
         "python-pptx workflow. Python %s." % (r["generated"], r["reproduce"], r["example"], r["python"]), ""]
    if r.get("deck"):
        A, B = r["deck"]["A_python_pptx_script"], r["deck"]["B_skill"]
        L += ["## Same spec, two pipelines", "",
              "A = a python-pptx script writing the same titles, bullets and notes into the default template (images, "
              "tables and charts left out). B = `deck_make.py --case` (check → build → LibreOffice render → records → result.json).", "",
              "| measured | A: python-pptx script | B: this Skill |", "|---|---|---|"]
        for lab, k in (("slides", "slides"), ("slides with speaker notes", "slides_with_notes"), ("pictures (paper figure crops)", "pictures"),
                       ("tables", "tables"), ("slides using a title placeholder", "title_placeholders"),
                       ("evidence map (slide → locator + verbatim quote)", "evidence_map"), ("facts with verified quotes", "facts_verified"),
                       ("numbers checked against the materials", "numbers_checked"), ("PDF pages rendered", "pdf_pages"),
                       ("PNG previews", "png_previews"), ("result.json", "result_json"), ("contract fields filled", "contract_fields_filled"),
                       ("wall time (s)", "seconds")):
            L.append("| %s | %s | %s |" % (lab, A.get(k), B.get(k)))
        dd = r["deck_defect_detection"]
        L += ["", "## One injected defect per run (same spec otherwise)", "",
              "Flagged before delivery by B: **%d/%d**; A: %d (the script writes whatever it is given)." % (dd["B_detected"], dd["n"], dd["A_detected"]),
              "", "| defect | A | B | B status | B evidence |", "|---|---|---|---|---|"]
        for x in r["deck_defects"]:
            if not x.get("applicable"):
                L.append("| %s | – | not applicable | | |" % x["defect"])
                continue
            L.append("| %s — %s | %s | %s | %s | %s |" % (x["defect"], x["description"], x["A_python_pptx_script"], x["B_skill"],
                                                      x["B_status"], (x.get("B_evidence") or "").replace("|", "/")[:120]))
    if r.get("template") and not r["template"].get("skipped"):
        t = r["template"]
        L += ["", "## Template adaptation (draft → adapted deck)", "",
              "| measured | draft (before) | adapted by this Skill |", "|---|---|---|",
              "| template compliance rate | %s | %s |" % (t["compliance_rate_draft"], t["compliance_rate_adapted"]),
              "| issues by rule | %s | %s |" % (json.dumps(t["issues_draft"], ensure_ascii=False), json.dumps(t["issues_adapted"], ensure_ascii=False)),
              "| slides / pictures / tables / notes | %s | %s |" % (
                  "%s / %s / %s / %s" % tuple(t["draft"][k] for k in ("slides", "pictures", "tables", "slides_with_notes")),
                  "%s / %s / %s / %s" % tuple((t["adapted"] or {}).get(k) for k in ("slides", "pictures", "tables", "slides_with_notes"))),
              "| content preserved (text) | – | %s (%s) |" % (t["content_preserved"], t["text_preserved_rate"]),
              "| result.json status | – | %s |" % t["status"]]
    if r.get("edit") and not r["edit"].get("skipped"):
        e = r["edit"]
        A, B = e["A_python_pptx_open_save"], e["B_deck_edit_round"]
        L += ["", "## Same single text edit (%s on slide %s: %r → %r)" % (e["op"]["op"], e["op"]["slide"], e["op"]["find"], e["op"]["replace"]), "",
              "| measured | A: python-pptx open → edit → save | B: deck_edit.py round |", "|---|---|---|",
              "| package parts byte-identical to the input | %s/%s | %s/%s |" % (A.get("parts_byte_identical"), A.get("parts_total"),
                                                                           B.get("parts_byte_identical"), B.get("parts_total")),
              "| edit applied | %s | %s |" % (A.get("op_applied"), B.get("op_applied")),
              "| version snapshots (rollback) | %s | %s |" % (A.get("version_snapshots"), B.get("version_snapshots")),
              "| render / evidence check / result.json status | – / – / – | %s / %s / %s |" % (B.get("render"), B.get("check"), B.get("result")),
              "", "For this single text edit python-pptx also leaves the untouched parts byte-identical; the difference is "
              "the version snapshot (rollback), the re-render, the evidence check and the result record.", "",
              "| injected edit defect | A | B flagged | B status | B evidence |", "|---|---|---|---|---|"]
        for x in e.get("defects", []):
            L.append("| %s — %s | %s | %s | %s | %s |" % (x["defect"], x["description"], x["A_python_pptx_open_save"],
                                                      x["B_detected"], x["B_status"], (x.get("B_evidence") or "").replace("|", "/")[:120]))
    if r.get("contract_shapes"):
        L += ["", "## output_contract shapes (fields filled automatically by deck_fill.py)", "",
              "| contract shape | fields | filled | case_id kept |", "|---|---|---|---|"]
        for s in r["contract_shapes"]:
            L.append("| %s | %d | %d | %s |" % (s["shape"], s["fields"], s["filled_automatically"], s["case_id_kept"]))
    L += ["", "## Limits of this evidence", "",
          "- One synthetic example; the platform's 3 hidden cases differ. A is a plain script, not the DeepSeek baseline agent "
          "or a careful human; an agent could add some of B's steps by hand.",
          "- Checks are text/geometry level: whether a slide's emphasis or story is good is not machine-verified.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
