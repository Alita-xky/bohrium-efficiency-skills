#!/usr/bin/env python3
"""fig_make.py - ONE call from a written spec to a complete, checked deliverable with result.json.

Create mode (you wrote /app/work/spec.json):
  python3 fig_make.py --spec /app/work/spec.json --mode chart --name chart
Edit mode (a figure source is given and must be changed, never redrawn):
  python3 fig_make.py --edit-svg /input/figure.svg --ops /app/work/ops.json --name figure_edited
Common options:
  --case /input/case.json --input /input --work /app/work --outdir /app/output
  --materials '/app/work/materials/*.txt' /input/*.csv     (default: work materials + every CSV found)
  --fill /app/work/fill.json        your task-specific answers keyed by contract field (they win)
  --set field=value | field=@file.json      --uncertainty "text" (repeatable)   --status partial
  --no-rerun                        skip the reproducibility re-render

Steps (every report lands in --outdir):
  1 fig_check.py   -> check_report.json   provenance, verbatim quotes, numbers in materials, legend, mode rules
  2 fig_render.py  -> <name>.svg/.pdf/.png, render_report.json, layer_map.json
    (edit mode: svg_edit.py apply + export -> edit_log.json with per-op verification and unchanged ids)
  3 re-render into <work>/_rerun and compare SHA-256 of SVG/PDF/PNG (same input -> same files?)
  4 copies the spec (and facts.json) next to the figure; version_record.json (inputs + sha256, tools,
    fonts, export parameters, re-run commands) and reproduce.sh
  5 maps every output_contract field to the evidence produced (by keywords in the field name), merges
    your fill.json on top, runs make_result.py -> result.json (case_id + every contract field)
Output: one JSON summary line, then FAIL / ISSUE / FIX lines. The deliverable is written even when a
check fails (status becomes partial): fix the FAILs in the spec and run the same command again.
"""
import argparse, glob, hashlib, json, os, platform, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_result as MR  # noqa: E402  (contract parsing + lenient JSON, shared with step 6)

CHECK, RENDER, EDIT, EXTRACT = (os.path.join(HERE, n) for n in
                                ("fig_check.py", "fig_render.py", "svg_edit.py", "extract_materials.py"))


def run(args, timeout=600):
    try:
        p = subprocess.run([sys.executable, "-B"] + args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT after %ss: %s" % (timeout, " ".join(args[:2]))


def first_json(out):
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def jload(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def sha(p):
    try:
        return MR.sha256(p)
    except Exception:
        return None


def pdf_fonts(path):
    """Embedded font kinds of a PDF without external tools (matplotlib/LibreOffice keep font dicts plain)."""
    try:
        b = open(path, "rb").read()
    except Exception:
        return None
    names = sorted({m.decode("latin-1") for m in re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-_,.]+)", b)})[:12]
    return dict(truetype_embedded=b.count(b"/FontFile2"), opentype_embedded=b.count(b"/FontFile3"),
                type3=b.count(b"/Type3"), names=names,
                note="TrueType/OpenType embedded = text stays selectable and editable; Type3 = glyph drawings")


def svg_facts(path):
    try:
        s = open(path, encoding="utf-8").read()
    except Exception:
        return None
    layers = re.findall(r'inkscape:groupmode="layer"[^>]*inkscape:label="([^"]+)"', s) + \
        re.findall(r'inkscape:label="([^"]+)"[^>]*inkscape:groupmode="layer"', s)
    return dict(text_elements=s.count("<text"), groups_with_id=len(re.findall(r"<g [^>]*id=", s)),
                layers=sorted(set(layers)), raster_images=s.count("<image"), bytes=len(s.encode("utf-8")))


def tool_versions():
    v = dict(python=platform.python_version())
    for mod in ("matplotlib", "numpy"):
        try:
            v[mod] = __import__(mod).__version__
        except Exception:
            v[mod] = None
    for t in ("rsvg-convert", "cairosvg", "inkscape", "soffice", "pdftoppm", "pdftotext", "tesseract", "fc-list"):
        v[t] = bool(shutil.which(t))
    return v


def short(s, n):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


RULES = [  # (evidence kind, regex on the contract field name); several hits -> dict of all of them
    ("status", r"^(run_)?status$|状态"),
    ("uncertainty", r"uncertain|limitation|limit|caveat|risk|unknown|unresolved|不确定|局限|限制|风险"),
    ("editable", r"editable|svg|vector|layer|source_file|figure_source|源文件|可编辑|矢量|图层"),
    ("exports", r"export|png|pdf|preview|raster|render|resolution|dpi|导出|预览|分辨率|渲染"),
    ("changes", r"change|edit(?!able)|modif|history|version|diff|log|revision|修改|版本|变更|日志|记录"),
    ("provenance", r"source|provenance|evidence|trace|locator|citation|element|mapping|map|来源|溯源|证据|定位|映射|元素"),
    ("data", r"data|value|column|series|数据|数值|字段"),
    ("checks", r"check|validat|(^|_)qa|qc|audit|verif|consisten|校验|检查|核验|审查|验证"),
    ("inputs", r"input|case|material|材料|输入"),
    ("figure", r"figure|chart|diagram|abstract|schematic|pathway|route|main|result|deliverable|output|artifact|图"),
]
STRONG_PROV = r"provenance|evidence|trace|locator|citation|element|map|来源|溯源|证据|定位|映射|元素"


def map_field(f):
    hits = [k for k, rx in RULES if re.search(rx, f, re.I)]
    if "editable" in hits and "provenance" in hits and not re.search(STRONG_PROV, f, re.I):
        hits.remove("provenance")           # editable_source = the SVG, not the source map
    if len(hits) > 1:
        hits = [h for h in hits if h not in ("figure", "inputs")] or hits
    if len(hits) > 1 and hits[0] not in ("status", "uncertainty"):
        hits = [h for h in hits if h not in ("status", "uncertainty")] or hits
    return hits


def _cycles(edges):
    """simple cycles (as node lists) of a small directed graph, at most 10"""
    g = {}
    for u, v in edges:
        g.setdefault(u, []).append(v)
    out, seen = [], set()
    def dfs(start, node, path):
        for nx in g.get(node, []):
            if nx == start and len(path) > 1:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key)
                    out.append(path + [start])
            elif nx not in path and len(out) < 10 and len(path) < 12:
                dfs(start, nx, path + [nx])
    for n in list(g):
        dfs(n, n, [n])
    return out


def rubric_coverage(mode, lm, spec, cr, rr, data_map, glyph, exports, edit):
    """The completeness checkpoints the figure rubrics name, each with a verdict from this run's own reports and the
    file that proves it (chart/setup: 科学实体、空间关系、数据编码、图例标注、可编辑源文件、导出格式; mechanism: 实体、
    相互作用、并行分支与反馈环、图例项、导出格式; schematic: 流程/结构/机制三类要素 + 图例、标注、单位、子图编号;
    graphical abstract: 研究问题、方法、核心结果、结论 + 图例/单位/比例尺)."""
    res = (cr or {}).get("results", [])
    iss = (rr or {}).get("issues", [])
    bad = lambda names: [r.get("check") for r in res + iss if r.get("level") == "fail" and r.get("check") in names]  # noqa: E731
    ents = {k: v for k, v in lm.items() if isinstance(v, dict) and v.get("type") in ("entity", "icon")}
    arrows = {k: v for k, v in lm.items() if isinstance(v, dict) and v.get("type") == "arrow"}
    panels = {k: v for k, v in lm.items() if k.startswith("panel:")}
    legends = [k for k, v in lm.items() if isinstance(v, dict) and v.get("type") == "legend"]
    sourced = lambda d: sum(1 for v in d.values() if isinstance(v.get("source"), dict) and  # noqa: E731
                            (v["source"].get("file") or v["source"].get("quote") or v["source"].get("loc")))
    kinds = {}
    for v in arrows.values():
        kinds[v.get("kind") or "?"] = kinds.get(v.get("kind") or "?", 0) + 1
    quoted = sum(1 for v in arrows.values() if isinstance(v.get("source"), dict) and v["source"].get("quote"))
    layout_bad = [i.get("check") for i in iss if i.get("level") in ("fail", "warn") and i.get("check") in (
        "arrow_through_entity", "arrows_cross", "text_overlap", "label_overlap", "legend_overlap", "out_of_canvas",
        "chart_out_of_canvas", "chart_overlaps_text", "chart_outside_panel", "tick_labels_overlap", "entity_overlap")]
    svg = (exports or {}).get("svg") or {}
    def row(ok, evidence, files, problems=None, partial=True):
        return dict(status="covered" if ok else ("partial" if partial else "missing"), evidence=evidence,
                    files=[f for f in files if f], problems=problems or None)
    files = lambda *n: [x for x in n if x]  # noqa: E731
    editable = row(bool(svg.get("path")) and not bad(["export", "svg_layer_ids"]),
                   "layered SVG (one Inkscape layer per panel, %s real <text> elements, every element addressable by id)" % svg.get(
                       "text_elements"), files(svg.get("path"), (exports or {}).get("layer_map")), bad(["export", "svg_layer_ids"]))
    png = (exports or {}).get("png") or {}
    export = row(bool((exports or {}).get("pdf", {}).get("path")) and bool(png.get("path")) and not bad(["export"]),
                 "SVG + PDF (TrueType fonts embedded) + PNG %s px at %s dpi, sRGB, byte-identical re-run" % (png.get("pixels"), png.get("dpi")),
                 files((exports or {}).get("pdf", {}).get("path"), png.get("path"), "render_report.json"), bad(["export"]))
    LEG = ["legend_color_mismatch", "legend_missing_category", "series_colours_repeat", "legend_absent", "legend_duplicate_color",
           "legend_missing_arrow_kind", "chart_axis_label_missing"]
    legend_ok = not bad(LEG)
    charts = [d for d in data_map or []]
    units = [d["id"] for d in charts if not any("(" in str((d.get("axis") or {}).get(k) or "") for k in ("y",))]
    legend = row(legend_ok, "%d legend block(s)%s; axis labels with units on %d of %d charts; meaningful colours = categories" % (
        len(legends) + sum(1 for d in charts if (d.get("n_series") or 0) > 1), " + chart legends" if charts else "",
        len(charts) - len(units), len(charts)), files("check_report.json"),
        bad(LEG) + (["no unit on: %s" % units] if units else []))
    entities = row(len(ents) == 0 or sourced(ents) == len(ents),
                   "%d entities/icons, %d with a material source%s" % (len(ents), sourced(ents), (
                       "; chart series/categories are the CSV's own names" if charts else "")),
                   files("layer_map.json", "check_report.json"), bad(["provenance_missing", "icon_without_source", "icon_not_named_in_materials", "number_without_source"]))
    spatial = row(not [x for x in layout_bad if x in ("arrow_through_entity", "text_overlap", "out_of_canvas")],
                  "%d panels, %d arrows; layout issues left: %s" % (len(panels), len(arrows), sorted(set(layout_bad)) or "none"),
                  files("render_report.json", "layer_map.json"), sorted(set(layout_bad)))
    if glyph:
        enc = row(not glyph.get("mismatches"), "%d of %d drawn glyphs read back from the figure equal their CSV cell; %d n.r." % (
            glyph.get("verified"), glyph.get("glyphs"), glyph.get("not_reported")), files(glyph.get("glyph_map_csv")), glyph.get("mismatches"))
    else:
        enc = row(not bad(["number_not_in_materials", "chart_csv_missing", "chart_column_missing"]),
                  "every number printed on the figure checked against the %s numbers found in the materials (none unmatched "
                  "unless listed); colour = category, arrow kind = relation type" % ((cr or {}).get("n_material_numbers")),
                  files("check_report.json"), bad(["number_not_in_materials", "chart_csv_missing", "chart_column_missing"]))
    if edit:
        return {"可编辑源文件 editable source": editable, "导出格式 export": export,
                "修改范围 edit scope": row(True, "only the requested ids changed; unchanged ids byte-identical (unchanged_check)",
                                       files("edit_log.json"))}
    if mode == "mechanism":
        edges = [(v.get("frm"), v.get("to")) for v in arrows.values() if v.get("kind") not in ("next",)]
        outd, ind = {}, {}
        for u, w in edges:
            outd[u] = outd.get(u, 0) + 1
            ind[w] = ind.get(w, 0) + 1
        branches = sorted(k for k, n in outd.items() if n >= 2) + sorted(k for k, n in ind.items() if n >= 2)
        loops = _cycles(edges)
        return {"实体 entities": entities,
                "相互作用 interactions": row(quoted == len(arrows), "%d arrows by kind %s, %d with a verbatim quote" % (len(arrows), kinds, quoted),
                                         files("layer_map.json", "check_report.json"), bad(["relation_without_quote", "arrow_endpoint_unknown", "arrow_kind_invalid"])),
                "并行分支与反馈环 branches and feedback loops": row(True, "branch points (≥2 outputs or inputs): %s; feedback loops: %s" % (
                    branches[:8] or "none", [" -> ".join(map(str, c)) for c in loops][:5] or "none in the source"), files("layer_map.json")),
                "图例项 legend": legend, "可编辑源文件 editable source": editable, "导出格式 export": export}
    if mode == "schematic":
        flow = kinds.get("next", 0) + kinds.get("flow", 0)
        causal = sum(n for k, n in kinds.items() if k not in ("next", "flow"))
        groups = [k for k, v in lm.items() if isinstance(v, dict) and v.get("type") == "group"]
        sub = [str(v.get("title") or "")[:30] for v in panels.values()]
        return {"结构要素 structure (entities, containment)": row(entities["status"] == "covered", "%d entities, %d groups/panels (containment)" % (
                    len(ents), len(groups) + len(panels)), files("layer_map.json")),
                "流程要素 process steps": row(True, "%d sequential/flow arrows" % flow, files("layer_map.json")),
                "机制/关系要素 relations": row(quoted >= causal, "%d relation arrows %s, %d quoted" % (causal, kinds, quoted),
                                          files("check_report.json")),
                "图例与标注 legend and labels": legend, "单位 units": row(not units, "units on axes/scale bars: %s" % ("all" if not units else
                                                                                                 "missing on %s" % units), files("check_report.json")),
                "子图编号 panel labels": row(len(panels) != 1, "panels: %s" % (sub or "single panel"), files("layer_map.json")),
                "可编辑源文件 editable source": editable, "导出格式 export": export}
    if mode in ("graphical_abstract", "ga"):
        roles = {str(v.get("role")): str(v.get("title") or "") for v in panels.values()}
        four = {"研究问题 question": ("problem", "question"), "方法 method": ("method", "approach"),
                "核心结果 key result": ("result", "results"), "结论 conclusion": ("conclusion", "takeaway")}
        out = {k: row(any(r in roles for r in alts), "panel %s" % next((roles[r] for r in alts if r in roles), "missing"),
                      files("layer_map.json"), partial=False) for k, alts in four.items()}
        out.update({"图例/单位/比例尺 legend, units, scale": legend, "可编辑源文件 editable source": editable, "导出格式 export": export})
        return out
    if charts and not ents:   # data chart: the scientific entities are the series and categories of the data
        entities = row(True, "; ".join("%s: series %s from %s, categories from column %s" % (
            d["id"], d.get("y") if isinstance(d.get("y"), list) else [d.get("y")], os.path.basename(str(d.get("csv") or "inline values")),
            d.get("x") or d.get("group")) for d in charts)[:400], files("layer_map.json", "glyph_map.csv"))
        spatial = row(not layout_bad, "%d chart panel(s) on one canvas; axes, tick labels, legends and value labels checked for "
                      "overlap and clipping: %s" % (len(charts), sorted(set(layout_bad)) or "none left"),
                      files("render_report.json"), sorted(set(layout_bad)))
    return {"科学实体 scientific entities": entities, "空间关系 spatial relations": spatial, "数据编码 data encoding": enc,
            "图例标注 legend and labels": legend, "可编辑源文件 editable source": editable, "导出格式 export": export}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--spec", default=None)
    ap.add_argument("--mode", default="schematic",
                    choices=["schematic", "chart", "mechanism", "setup", "graphical_abstract"])
    ap.add_argument("--name", default="figure")
    ap.add_argument("--edit-svg", default=None, help="edit mode: the given figure source")
    ap.add_argument("--ops", default=None, help="edit mode: ops.json for svg_edit.py apply")
    ap.add_argument("--dpi", type=int, default=300, help="edit mode export dpi")
    ap.add_argument("--case", default="/input/case.json")
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--materials", nargs="+", default=None)
    ap.add_argument("--fill", default=None)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--uncertainty", action="append", default=[])
    ap.add_argument("--status", default=None, choices=["complete", "partial", "failed"])
    ap.add_argument("--no-rerun", action="store_true")
    a = ap.parse_args()
    edit = bool(a.edit_svg)
    if not edit and not a.spec:
        ap.error("give --spec (create mode) or --edit-svg + --ops (edit mode)")
    if edit and not a.ops:
        ap.error("edit mode needs --ops ops.json (see svg_edit.py inventory)")
    od, wd = os.path.abspath(a.outdir), os.path.abspath(a.work)
    os.makedirs(od, exist_ok=True)
    os.makedirs(wd, exist_ok=True)
    t0 = time.time()
    fails, warns, issues, cmds, steps = [], [], [], [], {}

    # materials: extract them if step 1 was skipped
    if not glob.glob(os.path.join(wd, "materials", "*.txt")) and os.path.isdir(a.input):
        c = [EXTRACT, "--input", a.input, "--work", wd]
        rc, out = run(c, timeout=300)
        steps["extract"] = dict(rc=rc, note="materials were not extracted yet; ran extract_materials.py")
        cmds.append(c)
    mats = a.materials or (glob.glob(os.path.join(wd, "materials", "*.txt")) +
                           glob.glob(os.path.join(a.input, "**", "*.csv"), recursive=True) +
                           glob.glob(os.path.join(a.input, "**", "*.tsv"), recursive=True) +
                           glob.glob(os.path.join(wd, "*.csv")) + glob.glob(os.path.join(wd, "materials", "*.csv")))
    mats = sorted(set(mats))

    svg_out = os.path.join(od, a.name + ".svg")
    png_out, pdf_out = os.path.join(od, a.name + ".png"), os.path.join(od, a.name + ".pdf")
    spec = None
    if not edit:
        spec = MR.load_json_lenient(a.spec)
        c = [CHECK, "--spec", a.spec, "--mode", a.mode, "--out", os.path.join(od, "check_report.json")]
        if mats:
            c += ["--materials"] + mats
        rc, out = run(c)
        cmds.append(c)
        steps["check"] = first_json(out)
        fails += ["check: " + l.strip()[5:] for l in out.splitlines() if l.startswith("FAIL")]
        warns += ["check: " + l.strip()[5:] for l in out.splitlines() if l.startswith("WARN")]
        if not steps["check"]:
            fails.append("check: fig_check.py did not run: " + short(out.strip()[-400:], 400))
        c = [RENDER, "--spec", a.spec, "--outdir", od, "--name", a.name]
        rc, out = run(c)
        cmds.append(c)
        steps["render"] = first_json(out)
        issues = [l.strip()[6:] for l in out.splitlines() if l.startswith("ISSUE")]
        for l in issues:
            lvl = first_json(l).get("level")
            if lvl == "fail":
                fails.append("render: " + l)
            elif lvl == "warn":
                warns.append("render: " + l)
        if not steps["render"]:
            fails.append("render: fig_render.py did not run: " + short(out.strip()[-600:], 600))
    else:
        c = [EDIT, "apply", "--svg", a.edit_svg, "--ops", a.ops, "--out", svg_out, "--log", os.path.join(od, "edit_log.json")]
        rc, out = run(c)
        cmds.append(c)
        steps["edit"] = first_json(out)
        e = steps["edit"]
        if not e:
            fails.append("edit: svg_edit.py apply failed: " + short(out.strip()[-600:], 600))
        for x in e.get("not_applied_or_unverified") or []:
            fails.append("edit: op not applied/verified: " + json.dumps(x, ensure_ascii=False)[:200])
        for x in e.get("warnings") or []:
            warns.append("edit: " + json.dumps(x, ensure_ascii=False)[:200])
        unexp = (e.get("unchanged_check") or {}).get("unexpected_changes")
        if unexp:
            fails.append("edit: elements the ops did not address changed: %s (re-apply from the ORIGINAL file)" % unexp[:10])
        if os.path.exists(svg_out):
            c = [EDIT, "export", "--svg", svg_out, "--png", png_out, "--pdf", pdf_out, "--dpi", str(a.dpi)]
            rc, out = run(c)
            cmds.append(c)
            steps["export"] = first_json(out)
            if not (os.path.exists(png_out) and os.path.exists(pdf_out)):
                fails.append("export: PNG/PDF not produced: " + short(out.strip()[-300:], 300))

    # reproducibility: same input -> same bytes?
    rerun = None
    if not a.no_rerun and os.path.exists(svg_out):
        rd = os.path.join(wd, "_rerun")
        shutil.rmtree(rd, ignore_errors=True)
        os.makedirs(rd, exist_ok=True)
        if not edit:
            run([RENDER, "--spec", a.spec, "--outdir", rd, "--name", a.name])
        else:
            run([EDIT, "apply", "--svg", a.edit_svg, "--ops", a.ops, "--out", os.path.join(rd, a.name + ".svg"),
                 "--log", os.path.join(rd, "edit_log.json")])
        rerun = {}
        for ext in ("svg", "pdf", "png") if not edit else ("svg",):
            p1, p2 = os.path.join(od, a.name + "." + ext), os.path.join(rd, a.name + "." + ext)
            if os.path.exists(p1) and os.path.exists(p2):
                rerun[ext] = sha(p1) == sha(p2)
        rerun = dict(identical=all(rerun.values()) if rerun else None, per_format=rerun,
                     method="second independent run from the same input, SHA-256 compared")
        shutil.rmtree(rd, ignore_errors=True)
        if rerun["identical"] is False:
            warns.append("reproducibility: a re-run produced different bytes: %s" % rerun["per_format"])

    # records next to the figure
    spec_copy = facts_copy = None
    if spec is not None:
        spec_copy = os.path.join(od, a.name + "_spec.json")
        if os.path.abspath(a.spec) != spec_copy:
            shutil.copyfile(a.spec, spec_copy)
    fp = os.path.join(wd, "facts.json")
    if os.path.exists(fp):
        facts_copy = os.path.join(od, "facts.json")
        shutil.copyfile(fp, facts_copy)
    inputs = []
    if os.path.isdir(a.input):
        for root, _, names in os.walk(a.input):
            for n in sorted(names):
                p = os.path.join(root, n)
                inputs.append(dict(file=p, bytes=os.path.getsize(p), sha256=sha(p)))
    rr = jload(os.path.join(od, "render_report.json")) or {}
    cr = jload(os.path.join(od, "check_report.json")) or {}
    el = jload(os.path.join(od, "edit_log.json")) if edit else None
    sf = svg_facts(svg_out) if os.path.exists(svg_out) else None
    pf = pdf_fonts(pdf_out) if os.path.exists(pdf_out) else None
    png_px = None
    if os.path.exists(png_out):
        try:
            with open(png_out, "rb") as f:
                h = f.read(24)
            png_px = [int.from_bytes(h[16:20], "big"), int.from_bytes(h[20:24], "big")]
        except Exception:
            pass
    canvas = (spec or {}).get("canvas", {})
    if spec is not None:   # the copy in outdir is what a reviewer has: re-run commands point at it
        spec_copy = os.path.join(od, a.name + "_spec.json")
        cmds = [[spec_copy if x == a.spec else x for x in c] for c in cmds]
    rerun_cmds = [" ".join(["python3"] + [x if " " not in x else "'%s'" % x for x in c]) for c in cmds]
    vr = dict(created=time.strftime("%Y-%m-%d %H:%M:%S"), mode="edit" if edit else "create", figure_mode=a.mode,
              inputs=inputs, spec=dict(path=spec_copy, sha256=sha(spec_copy)) if spec_copy else None,
              facts=facts_copy, original_svg=dict(path=a.edit_svg, sha256=sha(a.edit_svg)) if edit else None,
              outputs={ext: dict(path=p, sha256=sha(p)) for ext, p in (("svg", svg_out), ("pdf", pdf_out), ("png", png_out))
                       if os.path.exists(p)},
              export_parameters=dict(width_in=canvas.get("width_in"), height_in=canvas.get("height_in"),
                                     dpi=canvas.get("dpi", 300) if not edit else a.dpi, png_pixels=png_px,
                                     svg_text="real <text> elements (svg.fonttype none)", pdf_fonts="TrueType embedded (fonttype 42)",
                                     colour_space="sRGB", timestamps="removed (byte-identical re-runs)"),
              fonts=dict(used=rr.get("fonts"), notes=rr.get("font_notes")), tools=tool_versions(),
              reproducibility=rerun, rerun_commands=rerun_cmds, manual_edits="none: every output file is generated by the commands above")
    with open(os.path.join(od, "version_record.json"), "w", encoding="utf-8") as f:
        json.dump(vr, f, ensure_ascii=False, indent=1)
    with open(os.path.join(od, "reproduce.sh"), "w", encoding="utf-8") as f:
        f.write("#!/bin/sh\n# re-create every output of this run from the same inputs (written by fig_make.py)\nset -e\n")
        f.write("\n".join(rerun_cmds) + "\n")

    # evidence values for the contract fields
    lm = jload(os.path.join(od, "layer_map.json")) or {}
    prov_rows, no_src = [], []
    for k, v in lm.items():
        s = v.get("source") if isinstance(v, dict) else None
        if isinstance(s, dict) and s.get("type") == "design":
            continue
        if isinstance(s, dict) and (s.get("file") or s.get("quote") or s.get("loc")):
            prov_rows.append(dict(id=k, type=v.get("type"), label=short(v.get("label") or v.get("title") or v.get("kind"), 60),
                                  source=" ".join(str(s.get(x)) for x in ("file", "loc") if s.get(x)),
                                  quote=short(s.get("quote"), 110) or None))
        elif v.get("type") in ("entity", "icon", "arrow", "chart"):
            no_src.append(k)
    data_map = []
    for elx in _iter_elements(spec or {}):
        if elx.get("type") == "chart":
            d = elx.get("data", {})
            m = lm.get(elx.get("id"), {})
            data_map.append(dict(id=elx.get("id"), kind=elx.get("kind", "bar"),
                                 csv=_resolve(d.get("csv"), a.spec) if d.get("csv") else None,
                                 csv_sha256=sha(_resolve(d.get("csv"), a.spec)) if d.get("csv") else None,
                                 x=d.get("x"), y=d.get("y"), err=d.get("err"), group=d.get("group"),
                                 inline_values=bool(d.get("values")), n_points=m.get("n_points"),
                                 n_series=m.get("n_series"), not_reported=m.get("missing") or m.get("n_missing"),
                                 axis=dict(x=elx.get("xlabel"), y=elx.get("ylabel"), yscale=elx.get("yscale"))))
    check_fail = [r.get("check") for r in cr.get("results", []) if r.get("level") == "fail"]
    render_fail = [i.get("check") for i in rr.get("issues", []) if i.get("level") == "fail"]
    val = {}
    val["editable"] = dict(svg=svg_out if os.path.exists(svg_out) else None, spec=spec_copy, layers=(sf or {}).get("layers"),
                           text_elements=(sf or {}).get("text_elements"), raster_images=(sf or {}).get("raster_images"),
                           note=("source-preserving edit of %s: only the addressed ids changed (edit_log.json)" % a.edit_svg) if edit else
                           "one Inkscape layer per panel; every element keeps its spec id; all text is editable <text>; "
                           "re-generate from %s" % os.path.basename(spec_copy or "spec.json"))
    val["exports"] = dict(pdf=pdf_out if os.path.exists(pdf_out) else None, png=png_out if os.path.exists(png_out) else None,
                          png_pixels=png_px, dpi=vr["export_parameters"]["dpi"],
                          size_in=[canvas.get("width_in"), canvas.get("height_in")] if canvas else None,
                          pdf_fonts=pf, colour_space="sRGB",
                          status=(rr.get("status") if not edit else ("ok" if os.path.exists(png_out) and os.path.exists(pdf_out) else "failed")),
                          converter=({k: v.get("via") for k, v in ((steps.get("export") or {}).get("results") or {}).items()}
                                     if edit else "matplotlib (svg/pdf/agg backends)"))
    if edit:
        val["changes"] = dict(edit_log=os.path.join(od, "edit_log.json"), original=a.edit_svg,
                              changes=[dict(op=(c.get("op") or {}).get("op"), id=(c.get("op") or {}).get("id"),
                                            before=c.get("before"), after=c.get("after"), verified=c.get("verified"),
                                            reason=(c.get("op") or {}).get("reason") or (c.get("op") or {}).get("source"))
                                       for c in (el or {}).get("changes", [])][:40],
                              unchanged_check=(el or {}).get("unchanged_check"), version_record=os.path.join(od, "version_record.json"))
    else:
        val["changes"] = dict(version_record=os.path.join(od, "version_record.json"), reproduce=os.path.join(od, "reproduce.sh"),
                              spec=spec_copy, change_log=(spec or {}).get("change_log") or (spec or {}).get("changes"),
                              manual_edits="none (all files generated from the spec by the recorded commands)")
    val["provenance"] = dict(layer_map=os.path.join(od, "layer_map.json") if lm else None, facts=facts_copy,
                             n_elements_with_source=len(prov_rows), elements_without_source=no_src or None,
                             table=prov_rows[:60], chart_data_binding=data_map or None) if not edit else \
        dict(edit_log=os.path.join(od, "edit_log.json"), note="every change is addressed by element id and verified on the output file",
             sources_of_new_values=[dict(id=(c.get("op") or {}).get("id"), source=(c.get("op") or {}).get("source"))
                                    for c in (el or {}).get("changes", []) if (c.get("op") or {}).get("source")] or None)
    ga = (rr.get("glyph_audit") or {}) if rr else {}
    if ga and os.path.exists(os.path.join(od, "glyph_map.json")):
        gsum = ga.get("charts") or {}
        glyph_binding = dict(glyph_map_json=os.path.join(od, "glyph_map.json"), glyph_map_csv=os.path.join(od, "glyph_map.csv"),
                             per_chart=gsum, glyphs=sum(v.get("glyphs", 0) for v in gsum.values()),
                             verified=sum(v.get("verified", 0) for v in gsum.values()),
                             not_reported=sum(v.get("not_reported", 0) for v in gsum.values()),
                             how="each bar/point has its own SVG id; its value is read back from the rendered axes and "
                                 "compared with its CSV cell (file:line:column); by_cell gives the reverse lookup")
    else:
        glyph_binding = None
    if glyph_binding and val.get("provenance"):
        val["provenance"]["glyph_to_cell"] = glyph_binding
    val["data"] = dict(charts=data_map, layer_map=os.path.join(od, "layer_map.json"), glyph_to_cell=glyph_binding) if data_map else None
    val["checks"] = dict(spec_audit=dict(status=cr.get("status"), fails=check_fail, n_warn=cr.get("n_warn"),
                                         report=os.path.join(od, "check_report.json")) if cr else None,
                         render_layout=dict(status=rr.get("status"), fails=render_fail,
                                            n_warn=sum(1 for i in rr.get("issues", []) if i.get("level") == "warn"),
                                            report=os.path.join(od, "render_report.json")) if rr else None,
                         edit_verification=dict(applied=(steps.get("edit") or {}).get("applied"),
                                                not_applied_or_unverified=(steps.get("edit") or {}).get("not_applied_or_unverified"),
                                                unchanged_check=(el or {}).get("unchanged_check")) if edit else None,
                         glyph_readback=dict(verified=glyph_binding["verified"], glyphs=glyph_binding["glyphs"],
                                             not_reported=glyph_binding["not_reported"],
                                             mismatches=[i.get("id") for i in rr.get("issues", []) if i.get("check") == "glyph_mismatch"][:20],
                                             map=glyph_binding["glyph_map_csv"]) if glyph_binding else None,
                         export=dict(svg_text_elements=(sf or {}).get("text_elements"), png_pixels=png_px, pdf_fonts=pf),
                         reproducibility=rerun)
    val["inputs"] = dict(case=a.case, files=[dict(file=x["file"], sha256=x["sha256"]) for x in inputs][:40])
    val["figure"] = dict(svg=val["editable"]["svg"], pdf=val["exports"]["pdf"], png=val["exports"]["png"],
                         title=(spec or {}).get("title"), mode=a.mode)
    unc = list(a.uncertainty)
    unc += ["check FAIL: " + f[7:][:160] for f in fails if f.startswith("check:")][:8]
    unc += ["render FAIL: " + f[8:][:160] for f in fails if f.startswith("render:")][:8]
    unc += ["edit: " + f[6:][:160] for f in fails if f.startswith("edit:")][:8]
    soft = ("quote_not_found", "provenance_missing", "legend_color_mismatch", "number_sign_differs", "legend_missing_category",
            "chart_axis_unit", "number_not_in_materials")
    unc += ["check WARN %s: %s" % (r.get("check"), r.get("id") or r.get("category") or r.get("number") or "")
            for r in cr.get("results", []) if r.get("level") == "warn" and r.get("check") in soft][:8]
    for d in data_map:
        if d.get("not_reported"):
            unc.append("chart %s: values not reported in the source drawn as 'n.r.': %s" % (d["id"], d["not_reported"]))
    unc += [n for n in (rr.get("font_notes") or []) if "NO installed font" in n or "not installed" in n]
    if no_src:
        unc.append("elements without a source (design-only or missing citation): %s" % no_src[:10])
    if rerun and rerun.get("identical") is False:
        unc.append("re-run not byte-identical: %s" % rerun.get("per_format"))
    val["uncertainty"] = unc or ["no automatic check failed; the scientific choice of what to show is not machine-verifiable"]
    have_files = os.path.exists(svg_out) and os.path.exists(pdf_out) and os.path.exists(png_out)
    status = a.status or ("complete" if have_files and not fails else ("partial" if os.path.exists(svg_out) else "failed"))
    val["status"] = status

    case = jload(a.case) or {}
    fields = [f for f in MR.contract_fields(MR.find_key(case, "output_contract")) if f and f != "case_id"]
    auto, mapping = {}, {}
    for f in fields:
        hits = [h for h in map_field(f) if val.get(h) not in (None, [], {})]
        if hits:
            auto[f] = val[hits[0]] if len(hits) == 1 else {h: val[h] for h in hits}
            mapping[f] = "+".join(hits)
    fill = {}
    if a.fill and os.path.exists(a.fill):
        fill = MR.load_json_lenient(a.fill)
    merged = dict(auto)
    for k, v in fill.items():   # the agent's own answer wins; dicts are merged so the evidence stays attached
        merged[k] = {**auto[k], **v} if isinstance(v, dict) and isinstance(auto.get(k), dict) else v
        mapping[k] = "fill.json" if k not in auto else mapping[k] + "+fill.json"
    for s in a.set:
        k, _, v = s.partition("=")
        if v.startswith("@") and os.path.exists(v[1:]):
            v = MR.load_json_lenient(v[1:]) if v.endswith(".json") else open(v[1:], encoding="utf-8").read()
        merged[k] = v
        mapping[k] = "--set"
    if "status" not in fields or "status" not in fill:
        merged["status"] = fill.get("status") or status
    if fill.get("status") == "complete" and status != "complete":
        merged["status"] = status   # never claim complete over a failed check
    if "uncertainty" not in fields:
        merged.setdefault("uncertainty", val["uncertainty"])
    elif isinstance(merged.get("uncertainty"), list) and merged["uncertainty"] is not val["uncertainty"]:
        merged["uncertainty"] = merged["uncertainty"] + [u for u in val["uncertainty"] if u not in merged["uncertainty"]]
    merged.setdefault("provenance_summary", dict(elements_with_source=len(prov_rows), layer_map=val["provenance"].get("layer_map")
                                                 if not edit else None))
    try:
        merged.setdefault("rubric_coverage", rubric_coverage(a.mode, lm, spec, cr, rr, data_map, glyph_binding,
                                                             dict((rr or {}).get("outputs") or {}, layer_map=os.path.join(od, "layer_map.json")),
                                                             edit))
    except Exception as e:  # never block the result
        merged.setdefault("rubric_coverage", {"error": repr(e)[:200]})
    fill_path = os.path.join(wd, "fill_merged.json")
    with open(fill_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    rc, out = run([os.path.join(HERE, "make_result.py"), "--case", a.case, "--outdir", od, "--fill", fill_path])
    res = first_json(out)
    fix = [l for l in out.splitlines() if l.startswith("FIX") or l.startswith("WARN")]
    summary = dict(status=res.get("status", status), result=res.get("result"), svg=val["editable"]["svg"],
                   pdf=val["exports"]["pdf"], png=val["exports"]["png"], png_pixels=png_px,
                   check=(steps.get("check") or {}).get("status"), render=(steps.get("render") or {}).get("status"),
                   edit_applied=(steps.get("edit") or {}).get("applied") if edit else None,
                   reproducible=(rerun or {}).get("identical"), n_fail=len(fails), n_warn=len(warns),
                   contract_mapping=mapping, unmapped=[f for f in fields if f not in merged],
                   seconds=round(time.time() - t0, 1))
    print(json.dumps(summary, ensure_ascii=False))
    for x in fails[:30]:
        print("FAIL " + x)
    for x in warns[:15]:
        print("WARN " + x)
    for x in fix:
        print(x)
    if summary["unmapped"]:
        print("FIX: contract fields with no automatic evidence: %s -> put your answer for each in %s and rerun"
              % (summary["unmapped"], a.fill or "/app/work/fill.json (--fill)"))
    if fails:
        print("NEXT: fix each FAIL in the spec (see SKILL.md table), then run this same command again")
    return 0


def _resolve(p, spec_path):
    """the data file the renderer used (same lookup as find_data_file in fig_render.py)"""
    if not p:
        return None
    sd = os.path.dirname(os.path.abspath(spec_path))
    cands = [p] if os.path.isabs(p) else [os.path.join(sd, p), os.path.abspath(p), os.path.join("/input", p),
                                           os.path.join("/app/work", p)]
    for c in cands:
        if os.path.exists(c):
            return os.path.abspath(c)
    for root in ("/input", "/app/work", sd):
        hits = sorted(glob.glob(os.path.join(root, "**", os.path.basename(p)), recursive=True))
        if hits:
            return hits[0]
    return p


def _iter_elements(spec):
    def walk(els):
        for e in els or []:
            yield e
            if e.get("type") == "group":
                yield from walk(e.get("elements"))
    yield from walk(spec.get("elements"))
    for p in spec.get("panels", []) or []:
        yield from walk(p.get("elements"))


if __name__ == "__main__":
    sys.exit(main())
