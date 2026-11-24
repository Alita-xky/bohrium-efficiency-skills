#!/usr/bin/env python3
"""gain_check.py - measured, reproducible comparison on SYNTHETIC input (not an official case result).

  python3 gain_check.py --mode chart [--example ../examples/minimal] [--out DIR] [--md report.md]

A = the same figure content drawn by matplotlib with library defaults (what a general plotting library
    gives when no rule asks for more): no audit, no records.
B = this Skill: fig_make.py (fig_check.py -> fig_render.py -> re-run -> records -> result.json).
Measured on identical inputs:
  1 deliverable: editable <text> in the SVG, spec ids addressable in the SVG, layers, PDF font type,
    element->source map, contract-complete result.json, byte-identical re-run
  2 injected defects (the failure types the task statement names): does a check flag it before delivery?
  3 source-preserving edit: ops verified, untouched elements byte-identical
  4 output_contract robustness: 6 contract shapes -> fields filled automatically
Everything is measured on this run and written to gain_report.json; nothing is asserted. It does NOT run
the platform's DeepSeek baseline agent and says nothing about model quality.
"""
import argparse, copy, csv, glob, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAKE, EDIT = os.path.join(HERE, "fig_make.py"), os.path.join(HERE, "svg_edit.py")
FABRICATED = "Yield fell by 63.7% (n = 19)"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def run(args, timeout=600):
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


def walk(spec):
    """(container, element) for every element; arrows separately."""
    for e in spec.get("elements", []):
        yield spec, e
    for p in spec.get("panels", []):
        for e in p.get("elements", []):
            yield p, e


def arrows(spec):
    return list(spec.get("arrows", [])) + [a for p in spec.get("panels", []) for a in p.get("arrows", [])]


# ------------------------------------------------------------------ A: library defaults
def baseline_draw(spec, spec_dir, out_base):
    """Draw the spec content with plain matplotlib defaults; returns paths. Raises on bad input (like
    a hand-written script would)."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcdefaults()
    import matplotlib.pyplot as plt
    cv = spec.get("canvas", {})
    fig = plt.figure(figsize=(cv.get("width_in", 10), cv.get("height_in", 6)))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(1, 0)
    ax.axis("off")
    if spec.get("title"):
        ax.text(0.5, 0.04, spec["title"], ha="center", fontsize=14)
    centers = {}

    def box(cont, e):
        if cont is spec:
            return e.get("x", 0), e.get("y", 0), e.get("w", 0.1), e.get("h", 0.1)
        return (cont["x"] + e.get("x", 0) * cont["w"], cont["y"] + e.get("y", 0) * cont["h"],
                e.get("w", 0.1) * cont["w"], e.get("h", 0.1) * cont["h"])
    for p in spec.get("panels", []):
        ax.add_patch(matplotlib.patches.Rectangle((p["x"], p["y"]), p["w"], p["h"], fill=False))
        ax.text(p["x"] + 0.01, p["y"] + 0.03, p.get("title", ""))
        centers["panel:" + p["id"]] = (p["x"] + p["w"] / 2, p["y"] + p["h"] / 2)
    for cont, e in walk(spec):
        x, y, w, h = box(cont, e)
        t = e.get("type", "entity")
        if t in ("entity", "icon"):     # a plain library has no icons: the object is drawn as a labelled box
            ax.text(x + w / 2, y + h / 2, e.get("label", "") or e.get("name", ""), ha="center", va="center",
                    bbox=dict(boxstyle="round", fc=e.get("fill", "white")))
            centers[e["id"]] = (x + w / 2, y + h / 2)
        elif t == "text":
            ax.text(x, y + h / 2, e.get("text", ""))
        elif t == "chart":
            d = e.get("data", {})
            cax = fig.add_axes([x, 1 - y - h, w, h])
            if d.get("csv"):
                path = d["csv"] if os.path.isabs(d["csv"]) else os.path.join(spec_dir, d["csv"])
                rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
                ys = d["y"] if isinstance(d.get("y"), list) else [d.get("y")]
                cats = [r[d["x"]] for r in rows]
                for col in ys:
                    vals = [float(r[col]) for r in rows]          # a typical script: float() on every cell
                    (cax.bar if e.get("kind", "bar") == "bar" else cax.plot)(cats, vals, label=col)
            elif d.get("values"):
                cax.bar([str(v) for v in d["values"].get("x", [])], d["values"].get("y", []))
            cax.set_xlabel(e.get("xlabel", ""))
            cax.set_ylabel(e.get("ylabel", ""))
            cax.legend()
    for a in arrows(spec):
        ax.annotate("", xy=centers[a["to"]], xytext=centers[a["from"]], arrowprops=dict(arrowstyle="->"))
    out = {}
    for ext in ("svg", "pdf", "png"):
        p = out_base + "." + ext
        fig.savefig(p, dpi=cv.get("dpi", 200) if ext == "png" else None)
        out[ext] = p
    plt.close(fig)
    return out


def svg_metrics(svg, spec_ids):
    s = open(svg, encoding="utf-8").read()
    ids = set(re.findall(r'id="([^"]+)"', s))
    return dict(svg_text_elements=s.count("<text"),
                spec_ids_addressable=sum(1 for i in spec_ids if i in ids), spec_ids_total=len(spec_ids),
                named_layers=len(re.findall(r'inkscape:groupmode="layer"', s)))


def pdf_metrics(pdf):
    b = open(pdf, "rb").read()
    return dict(pdf_truetype_embedded=b.count(b"/FontFile2") + b.count(b"/FontFile3"), pdf_type3_fonts=b.count(b"/Type3"))


# ------------------------------------------------------------------ case setup
def make_case(example, base, tag, spec, contract=None, csv_edit=None):
    I, W, O = (os.path.join(base, tag, x) for x in ("input", "work", "output"))
    for d in (I, W):
        os.makedirs(d, exist_ok=True)
    for f in glob.glob(os.path.join(example, "*")):
        n = os.path.basename(f)
        if os.path.isfile(f) and n not in ("spec.json", "fill.json", "README.md", "gain_report.json"):
            shutil.copy(f, os.path.join(I, n))
    case = json.load(open(os.path.join(I, "case.json"), encoding="utf-8"))
    if contract is not None:
        case["output_contract"] = contract
        json.dump(case, open(os.path.join(I, "case.json"), "w", encoding="utf-8"), ensure_ascii=False)
    if csv_edit:
        csv_edit(I)
    spec = copy.deepcopy(spec)
    for _, e in walk(spec):
        d = e.get("data") if isinstance(e.get("data"), dict) else None
        if d and d.get("csv") and not os.path.isabs(d["csv"]):
            d["csv"] = os.path.join(I, os.path.basename(d["csv"]))
    sp = os.path.join(W, "spec.json")
    json.dump(spec, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return I, W, O, sp, spec


def skill_run(mode, name, I, W, O, sp, extra=()):
    rc, out = run([MAKE, "--spec", sp, "--mode", mode, "--name", name, "--case", os.path.join(I, "case.json"),
                   "--input", I, "--work", W, "--outdir", O] + list(extra))
    return first_json(out), out


# ------------------------------------------------------------------ defects
def first(spec, pred):
    for cont, e in walk(spec):
        if pred(e):
            return cont, e
    return None, None


def a_quote(spec):
    for _, e in walk(spec):
        q = (e.get("source") or {}).get("quote") if isinstance(e.get("source"), dict) else None
        if q:
            return e["source"]
    for a in arrows(spec):
        if isinstance(a.get("source"), dict) and a["source"].get("quote"):
            return a["source"]
    return None


def d_fabricated(spec):
    src = copy.deepcopy(a_quote(spec) or {"file": "paper_excerpt.txt", "loc": "p1:4"})
    spec.setdefault("elements", []).append(dict(id="inj_num", type="text", text=FABRICATED, x=0.6, y=0.9, w=0.35, h=0.05,
                                                source=src))
    return True


def d_unknown_end(spec):
    _, e = first(spec, lambda e: e.get("type", "entity") == "entity")
    if not e:
        return False
    spec.setdefault("arrows", []).append(dict(id="inj_ar", **{"from": e["id"]}, to="ghost_entity", kind="activates",
                                              source=copy.deepcopy(a_quote(spec) or {})))
    return True


def d_no_source(spec):
    _, e = first(spec, lambda e: e.get("type", "entity") in ("entity", "chart") and isinstance(e.get("source"), dict)
                 and e["source"].get("type") != "design")
    if not e:
        return False
    e.pop("source")
    return True


def d_paraphrase(spec):
    s = a_quote(spec)
    if not s:
        return False
    words = s["quote"].split()
    s["quote"] = " ".join(reversed(words)) + " (paraphrased)"
    return True


def d_legend(spec):
    _, lg = first(spec, lambda e: e.get("type") == "legend" and e.get("entries"))
    if not lg:
        return False
    cats = {e.get("category") for _, e in walk(spec) if e.get("category")}
    for en in lg["entries"]:
        if any(c and c.lower() in (en.get("label") or "").lower() for c in cats) and not str(en.get("marker", "")).startswith("arrow"):
            en["color"] = "#7F7F7F" if (en.get("color") or "").lower() != "#7f7f7f" else "#123456"
            return True
    return False


def d_overflow(spec):
    _, e = first(spec, lambda e: e.get("type", "entity") == "entity")
    if not e:
        return False
    e["label"] = " ".join(["drought-induced abscisic acid signalling from root to shoot"] * 9)
    return True


def d_mode(mode):
    def f(spec):
        if mode == "chart":
            _, c = first(spec, lambda e: e.get("type") == "chart")
            if not c:
                return False
            c.pop("ylabel", None) if c.get("kind") != "barh" else c.pop("xlabel", None)
            return True
        if mode == "mechanism":
            for a in arrows(spec):
                if isinstance(a.get("source"), dict) and a["source"].get("quote"):
                    a["source"].pop("quote")
                    return True
            return False
        if mode == "setup":
            for holder in [spec] + spec.get("panels", []):
                holder["arrows"] = [a for a in holder.get("arrows", []) if a.get("kind") not in ("next", "flow")]
            return True
        if mode == "graphical_abstract":
            for p in spec.get("panels", []):
                if p.get("role") == "conclusion":
                    p["role"] = "other"
                    return True
            return False
        for holder in [spec] + spec.get("panels", []):   # schematic: relations dropped
            holder["arrows"] = []
        return True
    return f


MODE_DEFECT = {"chart": ("value_axis_label_removed", "the value axis has no 'Quantity (unit)' label", ["chart_axis_label_missing"]),
               "mechanism": ("arrow_quote_removed", "a mechanistic arrow keeps its direction but loses the quote that states it",
                             ["relation_without_quote"]),
               "setup": ("route_order_removed", "the stages are no longer connected in method order", ["route_order_missing"]),
               "graphical_abstract": ("conclusion_panel_missing", "the four-part story lacks its conclusion panel", ["ga_role_missing"]),
               "schematic": ("relations_removed", "the schematic shows entities but none of the stated relations", ["no_relations"])}


def defects(mode):
    m = MODE_DEFECT[mode]
    return [
        ("fabricated_number", "a number that is in no material is printed, with a real-looking citation", d_fabricated,
         ["number_not_in_materials"]),
        ("unknown_arrow_end", "an arrow points to an entity that does not exist", d_unknown_end, ["arrow_endpoint_unknown",
                                                                                                  "arrow_endpoint"]),
        ("source_removed", "a scientific element has no source", d_no_source, ["provenance_missing", "number_without_source",
                                                                                "chart_values_unverifiable"]),
        ("quote_paraphrased", "a quote is not verbatim any more", d_paraphrase, ["quote_not_found"]),
        ("legend_colour_mismatch", "a legend swatch no longer matches its category colour", d_legend, ["legend_color_mismatch"]),
        ("label_too_long", "an 80-word label (unreadable even if it fits)", d_overflow, ["text_overflow", "label_too_long"]),
        (m[0], m[1], d_mode(mode), m[2]),
    ]


def blank_plotted_value(spec):
    """csv_edit: empty the first plotted value (y column of the first CSV-bound chart) in the input copy."""
    _, c = first(spec, lambda e: e.get("type") == "chart" and isinstance(e.get("data"), dict) and e["data"].get("csv"))

    def edit(I):
        if not c:
            return False
        p = os.path.join(I, os.path.basename(c["data"]["csv"]))
        col = c["data"]["y"][0] if isinstance(c["data"].get("y"), list) else c["data"].get("y")
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        if not rows or col not in rows[0]:
            return False
        rows[0][col] = ""
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return True
    return edit


def d_column(spec):
    _, c = first(spec, lambda e: e.get("type") == "chart" and isinstance(e.get("data"), dict) and e["data"].get("csv"))
    if not c:
        return False
    y = c["data"].get("y")
    c["data"]["y"] = (y[0] if isinstance(y, list) else y) + "_typo"
    return True


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", required=True, choices=sorted(MODE_DEFECT))
    ap.add_argument("--example", default=os.path.join(ROOT, "examples", "minimal"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--md", default=None, help="also write a markdown summary here")
    a = ap.parse_args()
    base = a.out or tempfile.mkdtemp(prefix="gain_check_")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    spec0 = json.load(open(os.path.join(a.example, "spec.json"), encoding="utf-8"))
    name = {"graphical_abstract": "graphical_abstract", "setup": "setup_route"}.get(a.mode, a.mode)
    t0 = time.time()
    rep = dict(generated=time.strftime("%Y-%m-%d %H:%M"), mode=a.mode, example=os.path.relpath(a.example, ROOT),
               python=sys.version.split()[0], synthetic=True,
               what_this_measures="same synthetic input; A = matplotlib library defaults drawing the same content, "
                                  "B = this Skill's pipeline; defect rows inject one error into the same spec",
               not_measured="the platform's DeepSeek baseline agent, model quality, human judgement of design")

    # 1 deliverable comparison
    I, W, O, sp, spec = make_case(a.example, base, "clean", spec0)
    spec_ids = [e["id"] for _, e in walk(spec) if e.get("type", "entity") in ("entity", "icon", "chart", "text")] + \
               [x["id"] for x in arrows(spec)]
    tb = time.time()
    summ, out = skill_run(a.mode, name, I, W, O, sp)
    tb = time.time() - tb
    res = json.load(open(os.path.join(O, "result.json"), encoding="utf-8"))
    fields = res.get("contract_fields", [])
    B = dict(svg_metrics(os.path.join(O, name + ".svg"), spec_ids), **pdf_metrics(os.path.join(O, name + ".pdf")),
             element_source_map=os.path.exists(os.path.join(O, "layer_map.json")),
             result_json=True, contract_fields_filled="%d/%d" % (sum(1 for f in fields if not (isinstance(res.get(f), dict)
                                                                    and res[f].get("status") == "not_provided")), len(fields)),
             rerun_byte_identical=summ.get("reproducible"), status=summ.get("status"),
             automatic_checks=["spec vs materials (fig_check.py)", "layout/export (fig_render.py)", "re-run hash", "contract"],
             seconds=round(tb, 1))
    A = {}
    try:
        ta = time.time()
        pa = baseline_draw(spec, os.path.dirname(sp), os.path.join(base, "clean", "A_baseline"))
        pa2 = baseline_draw(spec, os.path.dirname(sp), os.path.join(base, "clean", "A_baseline_rerun"))
        A = dict(svg_metrics(pa["svg"], spec_ids), **pdf_metrics(pa["pdf"]), element_source_map=False, result_json=False,
                 contract_fields_filled="0/%d" % len(fields),
                 rerun_byte_identical=all(sha(pa[e]) == sha(pa2[e]) for e in ("svg", "pdf", "png")),
                 automatic_checks=[], seconds=round(time.time() - ta, 1))
    except Exception as e:
        A = dict(error=repr(e)[:200])
    rep["deliverable"] = dict(A_library_defaults=A, B_skill=B)

    # 2 injected defects
    rows = []
    cases = defects(a.mode)
    if a.mode == "chart":
        cases.append(("data_column_misnamed", "the chart is bound to a column that is not in the data file", d_column,
                      ["chart_column_missing"]))
        cases.append(("empty_data_cell", "a plotted value is missing (empty cell) in the data file", None, ["n.r.", "not reported"]))
    for key, desc, mut, expect in cases:
        sd = copy.deepcopy(spec0)
        if mut is not None and not mut(sd):
            rows.append(dict(defect=key, description=desc, applicable=False))
            continue
        I, W, O, sp, sdd = make_case(a.example, base, key, sd, csv_edit=blank_plotted_value(sd) if mut is None else None)
        summ, out = skill_run(a.mode, name, I, W, O, sp, extra=["--no-rerun"])
        flagged = [l for l in out.splitlines() if l.startswith(("FAIL", "WARN")) and any(x in l for x in expect)]
        if mut is None:
            r = json.load(open(os.path.join(O, "result.json"), encoding="utf-8"))
            flagged = [u for u in (r.get("uncertainty") or []) if any(x in str(u) for x in expect)] if isinstance(
                r.get("uncertainty"), list) else []
            flagged += [l for l in out.splitlines() if "n.r." in l]
        level = "FAIL" if any(l.startswith("FAIL") for l in flagged) else ("WARN" if flagged else
                                                                          ("reported" if mut is None and flagged else "none"))
        if mut is None and flagged:
            level = "reported in uncertainty"
        try:
            baseline_draw(sdd, os.path.dirname(sp), os.path.join(base, key, "A_baseline"))
            a_out = "drawn and shipped, no message"
        except Exception as e:
            a_out = "script crashed (%s), no deliverable" % type(e).__name__
        rows.append(dict(defect=key, description=desc, applicable=True, A_library_defaults=a_out,
                         B_skill=level, B_detected=bool(flagged), B_status=summ.get("status"),
                         B_evidence=(flagged[0][:180] if flagged else None)))
    # contract field that no evidence can fill
    I, W, O, sp, _ = make_case(a.example, base, "contract_gap", spec0, contract={
        "editable_source": "", "exports": "", "reviewer_signoff": "", "status": ""})
    summ, out = skill_run(a.mode, name, I, W, O, sp, extra=["--no-rerun"])
    rows.append(dict(defect="contract_field_without_answer", description="a required result field has no answer",
                     applicable=True, A_library_defaults="no result.json is written",
                     B_skill="FIX line + field written as not_provided" if "reviewer_signoff" in (summ.get("unmapped") or []) else "none",
                     B_detected="reviewer_signoff" in (summ.get("unmapped") or []), B_status=summ.get("status")))
    app = [r for r in rows if r.get("applicable")]
    rep["defects"] = rows
    rep["defect_detection"] = dict(B_detected=sum(1 for r in app if r.get("B_detected")), n=len(app),
                                   A_detected=0, A_crashed=sum(1 for r in app if "crashed" in str(r.get("A_library_defaults"))),
                                   A_note="the library has no audit: a defect is either drawn silently or the script crashes")

    # 3 source-preserving edit on B's SVG
    O = os.path.join(base, "clean", "output")
    svg = os.path.join(O, name + ".svg")
    ops = [dict(op="set_text", id="title", text="Edited title (synthetic edit test)")]
    tgt = next((e["id"] for _, e in walk(spec) if e.get("type", "entity") in ("entity", "chart")), None)
    if tgt:
        rc, inv = run([EDIT, "inventory", "--svg", svg])
        row = next((json.loads(l) for l in inv.splitlines() if l.startswith("{") and '"id": "%s"' % tgt in l), {})
        if row.get("colors"):
            ops.append(dict(op="recolor", id=tgt, to="#D55E00"))
    opsf = os.path.join(base, "clean", "ops.json")
    json.dump(ops, open(opsf, "w"), indent=1)
    rc, out = run([EDIT, "apply", "--svg", svg, "--ops", opsf, "--out", os.path.join(base, "clean", "edited.svg"),
                   "--log", os.path.join(base, "clean", "edit_log.json")])
    e = first_json(out)
    uc = e.get("unchanged_check") or {}
    rep["edit"] = dict(ops=len(ops), verified=e.get("applied"), untouched_identical=uc.get("n_untouched_identical"),
                       untouched_total=(uc.get("n_ids_input", 0) - uc.get("n_touched", 0)) if uc else None,
                       unexpected_changes=uc.get("unexpected_changes"),
                       note="A has no element ids to address: an edit means re-running the drawing script")

    # 4 contract shapes
    shapes = {
        "dict_of_descriptions": {"editable_source": "svg", "exports": "png/pdf", "element_sources": "map", "checks": "", "uncertainty": "", "status": ""},
        "list_of_names": ["editable_svg", "export_files", "source_mapping", "validation", "uncertainty", "status"],
        "json_schema": {"type": "object", "required": ["figure_source", "preview_png", "provenance", "status"],
                        "properties": {"figure_source": {}, "preview_png": {}, "provenance": {}, "status": {}}},
        "required_fields_wrapper": {"required_fields": ["layered_svg", "pdf", "evidence_map", "qa_checks", "limitations"]},
        "list_of_objects": [{"name": "source_file"}, {"name": "export_report"}, {"name": "change_log"}, {"name": "status"}],
        "chinese_names": {"可编辑源文件": "", "导出件": "", "来源与修改记录": "", "不确定性": "", "状态": ""},
    }
    srows = []
    for k, c in shapes.items():
        I, W, O, sp, _ = make_case(a.example, base, "contract_" + k, spec0, contract=c)
        summ, out = skill_run(a.mode, name, I, W, O, sp, extra=["--no-rerun"])
        r = json.load(open(os.path.join(O, "result.json"), encoding="utf-8"))
        fl = r.get("contract_fields", [])
        filled = [f for f in fl if not (isinstance(r.get(f), dict) and r[f].get("status") == "not_provided")]
        srows.append(dict(shape=k, fields=len(fl), filled_automatically=len(filled), case_id_kept=r.get("case_id") not in (None, "unknown")))
    rep["contract_shapes"] = srows
    rep["seconds_total"] = round(time.time() - t0, 1)
    rep["reproduce"] = "python3 scripts/gain_check.py --mode %s --out <dir>" % a.mode
    jp = os.path.join(base, "gain_report.json")
    json.dump(rep, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = to_md(rep)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(md)
    print(json.dumps(dict(report=jp, defects_detected="%d/%d" % (rep["defect_detection"]["B_detected"],
                                                                 rep["defect_detection"]["n"]),
                          deliverable_A=A, deliverable_B=B, seconds=rep["seconds_total"]), ensure_ascii=False))
    return 0


def to_md(r):
    A, B = r["deliverable"]["A_library_defaults"], r["deliverable"]["B_skill"]
    L = ["# Measured gain (SYNTHETIC example, mode `%s`)" % r["mode"], "",
         "Generated %s by `%s` on the shipped synthetic example (`%s`). It is **not** an official case "
         "result and **not** the platform's baseline-agent comparison; it measures what the pipeline adds to a "
         "general plotting library on identical input. Python %s." % (r["generated"], r["reproduce"], r["example"], r["python"]), "",
         "## 1. Same input, same content: deliverable", "",
         "| measured on the output | A: matplotlib defaults | B: this Skill |", "|---|---|---|"]
    rows = [("editable `<text>` elements in the SVG", "svg_text_elements"),
            ("spec elements addressable by id in the SVG", None), ("named layers", "named_layers"),
            ("PDF fonts embedded as TrueType/OpenType", "pdf_truetype_embedded"), ("PDF Type 3 (glyph drawing) fonts", "pdf_type3_fonts"),
            ("element → source map", "element_source_map"), ("result.json with case_id", "result_json"),
            ("contract fields filled", "contract_fields_filled"), ("re-run byte-identical (SVG, PDF, PNG)", "rerun_byte_identical"),
            ("automatic checks", "automatic_checks"), ("wall time (s)", "seconds")]
    for lab, k in rows:
        if k is None:
            va = "%s/%s" % (A.get("spec_ids_addressable"), A.get("spec_ids_total"))
            vb = "%s/%s" % (B.get("spec_ids_addressable"), B.get("spec_ids_total"))
        else:
            va, vb = A.get(k), B.get(k)
            va = ", ".join(va) if isinstance(va, list) else va
            vb = ", ".join(vb) if isinstance(vb, list) else vb
            va = "none" if va in ("", None) and k == "automatic_checks" else va
        L.append("| %s | %s | %s |" % (lab, va, vb))
    dd = r["defect_detection"]
    L += ["", "## 2. One injected defect per run (same spec otherwise)", "",
          "Flagged before delivery by B: **%d/%d**; A: %d flagged (%d runs crashed without a deliverable, the rest "
          "were drawn silently)." % (dd["B_detected"], dd["n"], dd["A_detected"], dd["A_crashed"]), "",
          "| defect | A: matplotlib defaults | B: this Skill | B evidence |", "|---|---|---|---|"]
    for x in r["defects"]:
        if not x.get("applicable"):
            L.append("| %s | – | not applicable to this example | |" % x["defect"])
            continue
        L.append("| %s — %s | %s | %s (status %s) | %s |" % (x["defect"], x["description"], x["A_library_defaults"],
                                                             x["B_skill"], x.get("B_status"),
                                                             (x.get("B_evidence") or "").replace("|", "/")[:110]))
    e = r["edit"]
    L += ["", "## 3. Source-preserving edit (svg_edit.py on B's SVG)", "",
          "%s ops, %s verified on the written file; untouched elements byte-identical: %s/%s; unexpected changes: %s. "
          "%s." % (e["ops"], e["verified"], e["untouched_identical"], e["untouched_total"], e["unexpected_changes"] or "none", e["note"]),
          "", "## 4. output_contract shapes (fields filled automatically by fig_make.py)", "",
          "| contract shape | fields | filled | case_id kept |", "|---|---|---|---|"]
    for s in r["contract_shapes"]:
        L.append("| %s | %d | %d | %s |" % (s["shape"], s["fields"], s["filled_automatically"], s["case_id_kept"]))
    L += ["", "## Limits of this evidence", "",
          "- One synthetic example per figure type; the platform's 3 hidden cases are different.",
          "- A is the library default, not a skilled human or the DeepSeek baseline agent; an agent could add some of "
          "B's settings by hand. The defect rows show what is caught automatically, not what a careful agent might notice.",
          "- Checks are string/geometry level: a wrong but quoted relation, or a poor choice of what to show, is not caught.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
