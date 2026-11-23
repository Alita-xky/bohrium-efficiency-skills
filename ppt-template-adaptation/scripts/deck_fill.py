#!/usr/bin/env python3
"""deck_fill.py - write version_record.json and a pre-filled fill.json for make_result.py.

Usage:
  python3 deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json \
      [--status complete|partial|failed] [--uncertainty "text" ...] [--set field=value | field=@file.json ...]

version_record.json (in outdir): input files + sha256, deck/PDF/previews + sha256, template, fonts requested vs
rendered, tools and versions, build backend, edit log / versions manifest - i.e. what is needed to re-run and
re-check the deck. fill.json: every output_contract field is mapped to produced artifacts by keywords in its
name (deck/pptx, evidence/source/mapping, render/export/preview, version/change/log, notes/script,
outline/storyline, template/format, qa, timeline, uncertainty, status). Fields it cannot map are listed and left
out - make_result.py then marks them not_provided (never invented). Review the printed mapping.
"""
import argparse, glob, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402


def jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


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


def fields_of(contract):
    if isinstance(contract, dict):
        if isinstance(contract.get("fields"), (list, dict)):
            return fields_of(contract["fields"])
        if isinstance(contract.get("properties"), dict):
            return list(contract["properties"].keys())
        return list(contract.keys())
    if isinstance(contract, list):
        return [x if isinstance(x, str) else (x.get("name") or x.get("field") or x.get("key")) for x in contract]
    if isinstance(contract, str):
        return [s.strip() for s in re.split(r"[,;]", contract) if s.strip()]
    return []


RULES = [
    ("status", r"^status$|状态"),
    ("uncertainty", r"uncertain|limit|caveat|risk|unknown|不确定|局限|限制|风险"),
    ("evidence", r"evidence|source|mapping|map|trace|citation|provenance|grounding|证据|来源|映射|溯源|引用"),
    ("version", r"version|change|edit(?!able)|log|record|history|diff|修改|版本|记录|变更"),
    ("render", r"render|export|preview|pdf|png|image|导出|渲染|预览"),
    ("qa", r"(^|[^a-z])qa([^a-z]|$)|q&a|question|answer|问答|追问"),
    ("plan", r"talk_plan|plan|goal|audience|occasion|计划|目标|听众|受众"),
    ("timeline", r"time|timeline|schedule|budget|pacing|时长|时间"),
    ("notes", r"note|script|speaker|transcript|讲稿|备注|讲解"),
    ("outline", r"outline|storyline|structure|narrative|(?<!hi)story|plan|主线|大纲|结构|叙事|页序"),
    ("template", r"template|format|style|layout|compliance|consisten|preserv|模板|格式|版式|一致|保留"),
    ("deck", r"deck|pptx|ppt|slide|presentation|editable|file|artifact|output|演示|幻灯|文件"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--out", default="/app/work/fill.json")
    ap.add_argument("--status", default=None, choices=[None, "complete", "partial", "failed"])
    ap.add_argument("--uncertainty", action="append", default=[])
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--deck", default=None, help="the final deck (default: newest *.pptx in outdir, templates excluded)")
    a = ap.parse_args()
    case = jload(a.case) or {}
    fields = [f for f in fields_of(find_key(case, "output_contract")) if f and f != "case_id"]
    od = a.outdir
    P = lambda n: os.path.join(od, n) if os.path.exists(os.path.join(od, n)) else None  # noqa: E731
    decks = [p for p in glob.glob(os.path.join(od, "**", "*.pptx"), recursive=True)
             if "template" not in os.path.basename(p) and "/versions/" not in p and "_render" not in p]
    deck = a.deck or (max(decks, key=os.path.getmtime) if decks else None)
    name = os.path.splitext(os.path.basename(deck))[0] if deck else "deck"
    pdf = os.path.join(od, name + ".pdf") if deck and os.path.exists(os.path.join(od, name + ".pdf")) else None
    rr = jload(os.path.join(od, "render_report.json")) or {}
    br = jload(os.path.join(od, "build_report.json")) or {}
    cr = jload(os.path.join(od, "check_report.json")) or {}
    comp = jload(os.path.join(od, "compliance.json"))
    elog = jload(os.path.join(od, "edit_log.json"))
    vers = jload(os.path.join(od, "versions", "versions.json"))
    pres = jload(os.path.join(od, "preserve.json"))
    plan = jload(os.path.join(a.work, "talk_plan.json")) or jload(os.path.join(od, "talk_plan.json"))
    if plan and not os.path.exists(os.path.join(od, "talk_plan.json")):
        with open(os.path.join(od, "talk_plan.json"), "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
    inputs = []
    for root, _, names in os.walk(a.input):
        for n in sorted(names):
            p = os.path.join(root, n)
            inputs.append(dict(file=p, bytes=os.path.getsize(p), sha256=K.sha256(p)))
    previews = sorted(glob.glob(os.path.join(od, "previews", "slide-*.png")))
    vr = dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), inputs=inputs,
              deck=dict(pptx=deck, sha256=K.sha256(deck) if deck else None, pdf=pdf, pdf_sha256=K.sha256(pdf) if pdf else None,
                        previews=previews, contact_sheet=rr.get("contact_sheet"), n_slides=rr.get("n_slides"), pdf_pages=rr.get("pdf_pages")),
              template=br.get("template") or (dict(path=(comp or {}).get("template"), sha256=(comp or {}).get("template_sha256")) if comp else None),
              build=dict(backend=br.get("backend"), backend_version=br.get("backend_version"), note=br.get("backend_note"),
                         font_overrides=br.get("font_overrides")) if br else None,
              fonts=dict(requested=rr.get("fonts"), embedded_in_pdf=rr.get("pdf_fonts"), notes=rr.get("font_notes")),
              tools=rr.get("tools"), render_status=rr.get("status"), check_status=cr.get("status"),
              template_compliance=dict(status=comp.get("status"), rate=comp.get("compliance_rate"), by_rule=comp.get("issues_by_rule")) if comp else None,
              edit_log=os.path.join(od, "edit_log.json") if elog else None, versions=vers,
              content_preservation=dict(status=pres.get("status"), text_preserved_rate=pres.get("text_preserved_rate")) if pres else None,
              rerun="python3 scripts/deck_make.py --spec <work>/spec.json --facts <work>/facts.json --outdir <out>" +
                    (" --template %s" % (br.get("template") or {}).get("path") if (br.get("template") or {}).get("generated") is False else ""))
    with open(os.path.join(od, "version_record.json"), "w", encoding="utf-8") as f:
        json.dump(vr, f, ensure_ascii=False, indent=1)
    unc = list(a.uncertainty)
    if cr:
        unc += ["check %s: %s" % (x["level"], x["msg"]) for x in cr.get("issues", []) if x["level"] == "fail"][:10]
        if cr.get("facts_not_in_materials"):
            unc.append("facts marked not_in_materials: %s" % cr["facts_not_in_materials"])
    unc += [x for x in (rr.get("font_notes") or [])][:5]
    if rr.get("status") != "ok":
        unc += ["render %s: %s" % (x["level"], x["msg"]) for x in rr.get("issues", []) if x["level"] == "fail"][:10]
    rs = jload(os.path.join(od, "round_summary.json"))  # edit rounds: judge only what this task changed
    render_ok = rr.get("status") == "ok"
    if rs and "new_render_fails" in rs:
        render_ok = not rs["new_render_fails"] and (comp or {}).get("status", "pass") == "pass" and not rs.get("ops_errors")
        if rs.get("pre_existing_render_fails"):
            unc.append("%d render finding(s) already present in the input deck (v0) on slides/shapes the instructions did not "
                       "address; left unchanged on purpose" % rs["pre_existing_render_fails"])
        unc = [u for u in unc if not u.startswith("render fail")]
    status = a.status or ("complete" if (deck and pdf and cr.get("status") == "pass" and render_ok) else "partial")
    if not deck:
        status = "failed"
    notes_n = sum(1 for p in rr.get("per_slide", []) if p.get("notes_chars"))
    spec = jload(os.path.join(a.work, "spec.json"))
    slide_map = []  # output slide -> source slide (origin) -> template layout
    for i, p in enumerate(rr.get("per_slide", []) or [], 1):
        sl = ((spec or {}).get("slides") or [])[i - 1] if spec and i <= len(spec.get("slides") or []) else {}
        slide_map.append(dict(slide=i, id=sl.get("id"), origin=sl.get("origin"), title=p.get("title"), layout=p.get("layout")))
    val = {
        "deck": dict(pptx=deck, pdf=pdf, previews=previews, contact_sheet=rr.get("contact_sheet"), n_slides=rr.get("n_slides"),
                     template=(br.get("template") or {}).get("path"), backend=br.get("backend"), outline=P("slides_outline.md")),
        "evidence": dict(evidence_map_json=P("evidence_map.json"), evidence_map_md=P("evidence_map.md"),
                         facts_verified=cr.get("facts_verified"), n_facts=cr.get("n_facts"), numbers_checked=cr.get("numbers_checked"),
                         check_status=cr.get("status"), check_report=P("check_report.json")),
        "render": dict(pdf=pdf, previews=previews, contact_sheet=rr.get("contact_sheet"), status=rr.get("status"),
                       pdf_pages=rr.get("pdf_pages"), fonts=rr.get("fonts"), report=P("render_report.json")),
        "version": dict(version_record=os.path.join(od, "version_record.json"), edit_log=P("edit_log.json"),
                        slide_diff=P("slide_diff.json"), versions=vers, build_report=P("build_report.json"), fix_log=P("fix_log.json")),
        "notes": dict(slides_with_notes=notes_n, n_slides=rr.get("n_slides"), where="speaker notes inside the pptx; text also in slides_outline.md",
                      outline=P("slides_outline.md")),
        "outline": dict(outline_md=P("slides_outline.md"), slides=[dict(slide=p["slide"], title=p["title"], layout=p["layout"]) for p in rr.get("per_slide", [])]),
        "template": dict(compliance=P("compliance.json"), status=(comp or {}).get("status"), rate=(comp or {}).get("compliance_rate"),
                         compliance_before=P("compliance_before.json"),
                         rate_before=(jload(os.path.join(od, "compliance_before.json")) or {}).get("compliance_rate"),
                         fix_log=P("fix_log.json"),
                         template=(br.get("template") or {}).get("path"), layouts_used=sorted({p["layout"] for p in rr.get("per_slide", []) if p.get("layout")}),
                         slide_layout_map=slide_map or None,
                         preserve=P("preserve.json"), preserve_status=(pres or {}).get("status"),
                         text_preserved_rate=(pres or {}).get("text_preserved_rate"),
                         not_carried_over=(spec or {}).get("not_carried_over") or None),
        "qa": dict(questions=(plan or {}).get("qa"), talk_plan_md=P("talk_plan.md")) if (plan or {}).get("qa") else None,
        "plan": dict(talk_plan_json=P("talk_plan.json"), talk_plan_md=P("talk_plan.md"), goal=(plan or {}).get("goal"),
                     audience=(plan or {}).get("audience"), duration_min=(plan or {}).get("duration_min"),
                     sections=(plan or {}).get("sections"), cuts=(plan or {}).get("cuts")) if plan else None,
        "timeline": cr.get("timeline") or (plan or {}).get("sections"),
        "uncertainty": unc or ["none detected by automatic checks; scientific judgement of slide emphasis not machine-verified"],
        "status": status,
    }
    fill = {}
    mapping = {}
    deck_head = re.compile(r"^(deck|pptx?|slides?|presentation|file|演示文件|演示|幻灯片?|文件|ppt文件)$", re.I)
    for f in fields:
        hits = [key for key, rx in RULES if re.search(rx, f, re.I) and val.get(key) not in (None, [], {})]
        head = [t for t in re.split(r"[_\-\s./]+|(?<=[a-z])(?=[A-Z])", str(f)) if t][-1:] or [""]
        if deck_head.match(head[0]) or re.search(r"(可编辑|编辑后|修改后|适配后).*(演示|幻灯|ppt|文件)", str(f), re.I):
            hits = ["deck"]  # the noun decides: editable_deck / revised_deck / adapted_pptx / slides -> the deck itself
        if not hits:
            continue
        if len(hits) > 1 and hits[0] not in ("status", "uncertainty"):
            hits = [h for h in hits if h not in ("status", "uncertainty", "deck")] or hits[:1]
        fill[f] = val[hits[0]] if len(hits) == 1 else {h: val[h] for h in hits}
        mapping[f] = "+".join(hits)
    for s in a.set:
        k, _, v = s.partition("=")
        if v.startswith("@") and os.path.exists(v[1:]):
            v = jload(v[1:]) if v.endswith(".json") else open(v[1:], encoding="utf-8").read()
        fill[k] = v
        mapping[k] = "--set"
    fill["status"] = fill.get("status", status) if "status" in fields else status
    fill.setdefault("uncertainty", val["uncertainty"]) if "uncertainty" not in fields else None
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(fill, f, ensure_ascii=False, indent=1)
    unmapped = [f for f in fields if f not in fill]
    print(json.dumps(dict(fill=a.out, status=status, version_record=os.path.join(od, "version_record.json"),
                          mapped=mapping, unmapped=unmapped), ensure_ascii=False))
    if unmapped:
        print("UNMAPPED contract fields (add with --set field=value, else they become not_provided): %s" % unmapped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
