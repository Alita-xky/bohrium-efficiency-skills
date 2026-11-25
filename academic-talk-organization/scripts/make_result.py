#!/usr/bin/env python3
"""Assemble /app/output/result.json so that it contains case_id and EVERY field of the case's
output_contract, filled from the agent's fill.json plus automatically collected artifact facts.

Usage:
  python3 make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json

fill.json: the agent's task-specific answers, keyed by output_contract field names (any extra keys are
kept under "extra"). Allowed status values: "complete" | "partial" | "failed".
Missing contract fields are NOT invented: they are written as
  {"status": "not_provided", "reason": "..."} and the overall status is downgraded to "partial".
"""
import argparse, glob, hashlib, json, os, re, sys


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, key)
            if r is not None:
                return r
    return None


LIST_KEYS = ("fields", "required", "required_fields", "result_fields", "required_keys", "keys", "outputs",
             "result_json_fields", "result.json", "result_json", "items", "output_fields")
META_KEYS = {"notes", "note", "description", "comment", "format", "type", "title", "$schema"}


def contract_fields(contract):
    """Field names from any common output_contract shape: list of names, list of {name|field|key},
    JSON-schema {required, properties}, {"required_fields": [...]} / {"fields": ...} wrappers, or a
    {field: description} dict. Wrapper/meta keys (notes, description ...) are never fields."""
    if contract is None:
        return []
    if isinstance(contract, str):
        return [s.strip() for s in contract.replace(";", ",").split(",") if s.strip()]
    if isinstance(contract, list):
        out = []
        for x in contract:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict):
                out.append(x.get("name") or x.get("field") or x.get("key") or json.dumps(x, ensure_ascii=False)[:40])
        return out
    if isinstance(contract, dict):
        if isinstance(contract.get("properties"), dict):
            req = contract.get("required") if isinstance(contract.get("required"), list) else []
            return list(req) + [k for k in contract["properties"] if k not in req]
        out = []
        for k in LIST_KEYS:
            if isinstance(contract.get(k), (list, dict)):
                out += [f for f in contract_fields(contract[k]) if f not in out]
        if out:
            return out
        return [k for k in contract if k not in META_KEYS]
    return []


def load_json_lenient(path):
    """Agent-written JSON: allow raw newlines/tabs inside strings and a trailing comma."""
    txt = open(path, encoding="utf-8-sig").read()
    try:
        return json.loads(txt, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", txt), strict=False)
        except json.JSONDecodeError as e:
            line = txt.splitlines()[e.lineno - 1] if e.lineno - 1 < len(txt.splitlines()) else ""
            sys.exit(f"INVALID JSON in {path}: {e.msg} at line {e.lineno} col {e.colno}: {line[:160]!r}")


def rubric_coverage(outdir, cr, rr, comp, ts, br):
    """The six elements every slide rubric of this track names (论文主线、页结构、图表引用、讲解逻辑、模板约束、导出渲染),
    each with a verdict computed from the run's own reports and the file that proves it."""
    P = lambda n: os.path.join(outdir, n) if os.path.exists(os.path.join(outdir, n)) else None  # noqa: E731
    iss = (cr or {}).get("issues") or []
    by = lambda *names: [i for i in iss if i.get("check") in names]  # noqa: E731
    fails = lambda xs: [i for i in xs if i.get("level") == "fail"]  # noqa: E731
    slides = (br or {}).get("slides") or []
    roles = [str(x.get("role") or "") for x in slides]
    per = (rr or {}).get("per_slide") or []
    em = None
    try:
        em = json.load(open(os.path.join(outdir, "evidence_map.json"), encoding="utf-8"))
    except Exception:
        pass
    vis = [v for e in (em or {}).get("slides", []) for v in e.get("visuals", [])]
    refd = [v for v in vis if v.get("ref")]
    notes_n = sum(1 for x in per if (x.get("notes_chars") or 0) > 0)
    tl = (cr or {}).get("timeline")
    def row(ok, partial, evidence, files, problems=None):
        return dict(status="covered" if ok else ("partial" if partial else "missing"), evidence=evidence,
                    files=[f for f in files if f], problems=problems or None)
    st = fails(by("story", "story_order"))
    typed = [r for r in roles if r]
    order = " > ".join(typed) if len(typed) >= max(2, len(roles) // 2) else " > ".join(
        str(x.get("title") or "")[:40] for x in per)  # template/edit decks: the source deck's titles, in order
    mainline = row(bool(roles or per) and not st, bool(roles or per),
                   ("slide roles in order: %s" if len(typed) >= max(2, len(roles) // 2) else "slide titles in order: %s") % order[:400],
                   [P("slides_outline.md"), P("talk_plan.md"), P("evidence_map.md")], [i["msg"] for i in by("story", "story_order")][:4])
    sc = fails(by("slide_count", "density"))
    structure = row(bool(slides) and not sc, bool(slides),
                    "%d slides, layouts %s; one title size %s pt, body ladder %s" % (
                        len(slides) or (rr or {}).get("n_slides") or 0, sorted({x.get("layout") for x in per if x.get("layout")})[:8],
                        ((ts or {}).get("title") or {}).get("size_pt"), ((ts or {}).get("body") or {}).get("ladder")),
                    [P("build_report.json"), P("type_scale.json"), P("slides_outline.md")], [i["msg"] for i in sc][:4])
    fr = fails(by("fig_ref", "figure", "chart", "fig_page"))
    figs = row(not fr, True, "%d figures/tables/charts on slides, %d carry the paper's own number (Fig./Table N); every cited "
               "number exists as a caption in the paper" % (len(vis), len(refd)),
               [P("evidence_map.md"), P("selection_ledger.json")], [i["msg"] for i in by("fig_ref", "figure", "chart", "fig_page")][:4])
    nt = fails(by("notes", "time_budget", "qa"))
    talk = row(notes_n and not nt, notes_n > 0,
               "speaker notes on %d of %d slides%s" % (notes_n, len(per), ("; timed timeline of %d slides with key messages and "
                                                                          "transitions" % len(tl)) if tl else ""),
               [P("talk_plan.md"), P("slides_outline.md")], [i["msg"] for i in by("notes", "notes_repeat", "time_budget", "transitions",
                                                                                   "speech_fit", "qa")][:4])
    tpl = (br or {}).get("template") or {}
    if comp:
        tmpl = row(comp.get("status") == "pass", True, "template compliance %s (%s); template %s" % (
            comp.get("compliance_rate"), comp.get("status"), tpl.get("path")), [P("compliance.json")],
            [k for k, v in (comp.get("issues_by_rule") or {}).items() if v][:6])
    else:
        tmpl = row(bool(tpl), bool(tpl), "built on %s (%s); deck-wide type scale" % (
            os.path.basename(str(tpl.get("path") or "")) or "the default academic template",
            "generated" if tpl.get("generated") else "given template, layouts and placeholders kept"),
            [P("build_report.json"), P("type_scale.json")])
    rend = row((rr or {}).get("status") in ("ok", "pass") and (rr or {}).get("pdf_pages") == (rr or {}).get("n_slides"),
               bool((rr or {}).get("pdf")),
               "PDF %s pages for %s slides, %d PNG previews, fonts %s" % ((rr or {}).get("pdf_pages"), (rr or {}).get("n_slides"),
                                                                       len((rr or {}).get("previews") or []),
                                                                       sorted({f.get("name") for f in ((rr or {}).get("pdf_fonts") or [])
                                                                               if isinstance(f, dict)})[:4]),
               [P("render_report.json"), (rr or {}).get("pdf"), (rr or {}).get("contact_sheet")],
               [i.get("msg") or i.get("check") for i in ((rr or {}).get("issues") or []) if i.get("level") == "fail"][:4])
    return {"论文主线 storyline": mainline, "页结构 slide structure": structure, "图表引用 figure/table references": figs,
            "讲解逻辑 speaking logic": talk, "模板约束 template constraints": tmpl, "导出渲染 export and rendering": rend}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--fill", required=True)
    a = ap.parse_args()
    case = {}
    try:
        case = json.load(open(a.case, encoding="utf-8"))
    except Exception as e:
        print(f"WARN cannot read case: {e!r}")
    fill = load_json_lenient(a.fill) if os.path.exists(a.fill) else {}
    contract = find_key(case, "output_contract")
    fields = contract_fields(contract)
    case_id = find_key(case, "case_id") or find_key(case, "id") or fill.get("case_id") or "unknown"

    artifacts = []
    for p in sorted(glob.glob(os.path.join(a.outdir, "**", "*"), recursive=True)):
        if os.path.isfile(p) and os.path.basename(p) != "result.json":
            artifacts.append(dict(path=p, bytes=os.path.getsize(p), sha256=sha256(p)))
    render = {}
    rp = os.path.join(a.outdir, "render_report.json")
    if os.path.exists(rp):
        r = json.load(open(rp, encoding="utf-8"))
        render = dict(status=r.get("status"), fonts=r.get("fonts"), font_notes=r.get("font_notes"),
                      issues=[i for i in r.get("issues", []) if i.get("level") == "fail"][:20])
    rsp = os.path.join(a.outdir, "round_summary.json")  # edit task: only failures introduced by the edits count
    if os.path.exists(rsp) and render.get("status") == "needs_fix":
        rs = json.load(open(rsp, encoding="utf-8"))
        if "new_render_fails" in rs and not rs["new_render_fails"]:
            render["status"] = "ok_pre_existing_only"
            render["note"] = "%s render finding(s) were already in the input deck and are outside the edit instructions" % rs.get(
                "pre_existing_render_fails")

    def jl(n):
        try:
            return json.load(open(os.path.join(a.outdir, n), encoding="utf-8"))
        except Exception:
            return None
    cr, rr, comp, pres, vr = (jl(n) for n in ("check_report.json", "render_report.json", "compliance.json",
                                               "preserve.json", "version_record.json"))
    verification = {}   # one place where a reviewer sees what was machine-checked, and the verdicts
    if cr:
        verification["content_check"] = dict(status=cr.get("status"), facts_verified=cr.get("facts_verified"),
                                             n_facts=cr.get("n_facts"), numbers_checked=cr.get("numbers_checked"),
                                             failed=sorted({i.get("rule") or i.get("check") or "?" for i in cr.get("issues", [])
                                                            if i.get("level") == "fail"})[:15],
                                             what="verbatim quotes, locators, every number in the materials, Fig./Table numbers "
                                                  "exist, sources per slide, story order, density, notes")
    if rr:
        verification["render"] = dict(status=rr.get("status"), renderer=(rr.get("tools") or {}).get("soffice_version"),
                                      n_slides=rr.get("n_slides"), pdf_pages=rr.get("pdf_pages"),
                                      previews=len(rr.get("previews") or []),
                                      pdf_fonts=sorted({f.get("name") for f in (rr.get("pdf_fonts") or []) if isinstance(f, dict)}),
                                      all_fonts_embedded=all(f.get("embedded") for f in (rr.get("pdf_fonts") or []) if isinstance(f, dict))
                                      if rr.get("pdf_fonts") else None,
                                      n_fail=rr.get("n_fail"), n_warn=rr.get("n_warn"))
    if comp:
        verification["template_compliance"] = dict(status=comp.get("status"), rate=comp.get("compliance_rate"),
                                                   by_rule=comp.get("issues_by_rule"))
    if pres:
        verification["content_preservation"] = dict(status=pres.get("status"), text_preserved_rate=pres.get("text_preserved_rate"))
    if os.path.exists(os.path.join(a.outdir, "evidence_map.md")):
        em = jl("evidence_map.json") or {}
        verification["page_gold_standard"] = dict(
            file=os.path.join(a.outdir, "evidence_map.md"), json=os.path.join(a.outdir, "evidence_map.json"),
            slides=len(em.get("slides") or []), facts=len(em.get("facts") or {}),
            what="页级金标准 / page-level gold standard: for every slide its claim, each statement's verbatim source quote "
                 "with [page:paragraph] locator, every number with where it was found and the paper's Fig./Table numbers; "
                 "re-check any deck against it with deck_check.py --pptx")
    ts = jl("type_scale.json")
    if ts:
        verification["type_scale"] = dict(file=os.path.join(a.outdir, "type_scale.json"), title_pt=(ts.get("title") or {}).get("size_pt"),
                                          body_ladder=(ts.get("body") or {}).get("ladder"), spacing=ts.get("spacing"),
                                          off_scale=((rr or {}).get("type_scale") or {}).get("off_scale"),
                                          what="one title size for the deck, body text on a 3-step ladder, sizes off it are listed")
    try:
        rubric = rubric_coverage(a.outdir, cr, rr, comp, ts, jl("build_report.json"))
    except Exception as e:  # never block the result
        rubric = {"error": repr(e)[:200]}
    result = {"case_id": case_id}
    missing = []
    for f in fields:
        if f == "case_id":
            continue
        if f in fill:
            result[f] = fill[f]
        else:
            result[f] = {"status": "not_provided", "reason": "no evidence produced for this field in this run"}
            missing.append(f)
    extra = {k: v for k, v in fill.items() if k not in fields and k != "case_id"}
    status = fill.get("status", "complete")
    if missing and status == "complete":
        status = "partial"
    if render.get("status") == "needs_fix" and status == "complete":
        status = "partial"
    if cr and cr.get("status") in ("fail", "needs_fix") and status == "complete":
        status = "partial"
    if "status" not in fields:
        result["status"] = status
    if "status" in fields and isinstance(result.get("status"), str) and result["status"] == "complete" and (
            missing or render.get("status") == "needs_fix" or (cr and cr.get("status") in ("fail", "needs_fix"))):
        result["status"] = "partial"
        result["status_note"] = "downgraded from complete: missing contract fields or failed content/render checks"
    result["artifacts"] = artifacts if "artifacts" not in fields else result.get("artifacts")
    result["render_check"] = render
    if verification:
        result.setdefault("verification", verification)
    result.setdefault("rubric_coverage", rubric)
    if vr:
        result.setdefault("inputs", [dict(file=x.get("file"), sha256=x.get("sha256")) for x in vr.get("inputs", [])][:40])
        result.setdefault("reproduce", dict(command=vr.get("rerun"), tools=vr.get("tools"), fonts=vr.get("fonts")))
    if extra:
        result["extra"] = extra
    result["contract_fields"] = fields
    result["missing_fields"] = missing
    os.makedirs(a.outdir, exist_ok=True)
    out = os.path.join(a.outdir, "result.json")
    json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(result=out, case_id=case_id, n_contract_fields=len(fields), missing=missing,
                          status=result.get("status", status), n_artifacts=len(artifacts)), ensure_ascii=False))
    if not fields:
        print("WARN no output_contract found in case.json: print the case file and fill fields by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
