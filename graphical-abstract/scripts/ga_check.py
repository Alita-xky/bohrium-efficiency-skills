#!/usr/bin/env python3
"""Pre-render audit of a figure spec against the source materials.

Usage:
  python3 ga_check.py --spec spec.json [--materials /app/work/text/*.txt data.csv ...] \
                      [--mode graphical_abstract|schematic|chart|mechanism|setup] [--out check_report.json]

Checks (fail = must fix before rendering; warn = fix or explain in result.json):
  * schema: unique ids, required geometry, arrow endpoints exist, valid arrow kinds
  * provenance: every scientific element (entity/arrow/chart/text with numbers) carries a source
  * number provenance: each number printed on the figure appears in the materials (text or CSV)
  * chart data: CSV columns exist, values numeric, axis labels carry units
  * legend coverage: every encoding color/arrow kind used is explained in a legend
  * graphical abstract completeness: panels with roles problem / method / result / conclusion
"""
import argparse, csv, glob, json, os, re, sys

NUM_RE = re.compile(r"(?<![A-Za-z_])[-+]?\d+(?:[.,]\d+)?(?:\s?[×x]\s?10\^?-?\d+)?")
ARROW_KINDS = {"activates", "promotes", "inhibits", "flow", "produces", "converts", "binds", "associates", "next"}
ROLES_GA = ["problem", "method", "result", "conclusion"]
SCI_TYPES = {"entity", "arrow", "chart", "image", "scalebar"}


def norm_num(s):
    s = s.replace(",", ".").replace(" ", "")
    try:
        return float(re.sub(r"[×x]10\^?(-?\d+)$", r"e\1", s))
    except ValueError:
        return None


def load_material_numbers(paths):
    nums, texts = set(), []
    for p in paths:
        try:
            if p.lower().endswith(".csv"):
                with open(p, newline="", encoding="utf-8-sig") as f:
                    for row in csv.reader(f):
                        for cell in row:
                            texts.append(cell)
            else:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    texts.append(f.read())
        except OSError:
            continue
    blob = "\n".join(texts)
    for m in NUM_RE.finditer(blob):
        v = norm_num(m.group(0))
        if v is not None:
            nums.add(round(v, 6))
    return nums, blob


def iter_elements(spec):
    for el in spec.get("elements", []):
        yield None, el
        for sub in el.get("elements", []) if el.get("type") == "group" else []:
            yield None, sub
    for p in spec.get("panels", []):
        for el in p.get("elements", []):
            yield p["id"], el
            for sub in el.get("elements", []) if el.get("type") == "group" else []:
                yield p["id"], sub


def iter_arrows(spec):
    for a in spec.get("arrows", []):
        yield None, a
    for p in spec.get("panels", []):
        for a in p.get("arrows", []):
            yield p["id"], a


def has_source(x):
    s = x.get("source")
    if not s:
        return False
    if isinstance(s, dict):
        return bool(s.get("file") or s.get("loc") or s.get("type") == "design")
    return bool(str(s).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--materials", nargs="*", default=[])
    ap.add_argument("--mode", default="graphical_abstract")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    spec_dir = os.path.dirname(os.path.abspath(a.spec))
    mats = []
    for m in a.materials:
        mats += glob.glob(m) or [m]
    mat_nums, mat_blob = load_material_numbers(mats)
    res = []

    def add(level, check, **kw):
        res.append(dict(level=level, check=check, **kw))

    ids = {}
    for pid, el in iter_elements(spec):
        if "id" not in el:
            add("fail", "schema_id_missing", element=el.get("label") or el.get("text"))
            continue
        if el["id"] in ids:
            add("fail", "schema_duplicate_id", id=el["id"])
        ids[el["id"]] = el
        t = el.get("type", "entity")
        need = {"entity": ["x", "y", "w", "h"], "chart": ["x", "y", "w", "h"], "image": ["x", "y", "w", "h", "path"],
                "text": ["x", "y", "text"], "legend": ["x", "y", "entries"], "scalebar": ["x", "y", "label"],
                "group": ["x", "y", "w", "h"]}.get(t, [])
        for k in need:
            if k not in el:
                add("fail", "schema_field_missing", id=el["id"], field=k)
        for k in ("x", "y", "w", "h"):
            v = el.get(k)
            if isinstance(v, (int, float)) and not (-0.001 <= v <= 1.001):
                add("fail", "schema_coord_range", id=el["id"], field=k, value=v)
        if t in SCI_TYPES and not has_source(el):
            add("warn", "provenance_missing", id=el["id"], hint="add source:{file,loc,quote} or source:{type:'design'}")
        text = el.get("label") or el.get("text") or ""
        nums_here = [m.group(0) for m in NUM_RE.finditer(text)]
        if nums_here and not has_source(el):
            add("fail", "number_without_source", id=el["id"], numbers=nums_here)
        if nums_here and mat_nums:
            for n in nums_here:
                v = norm_num(n)
                if v is not None and round(v, 6) not in mat_nums and not el.get("derived"):
                    add("fail", "number_not_in_materials", id=el["id"], number=n,
                        hint="copy the exact value from the source, or set derived:{formula,inputs}")
        if t == "chart":
            d = el.get("data", {})
            if "csv" in d:
                p = d["csv"] if os.path.isabs(d["csv"]) else os.path.join(spec_dir, d["csv"])
                if not os.path.exists(p):
                    add("fail", "chart_csv_missing", id=el["id"], path=p)
                else:
                    with open(p, newline="", encoding="utf-8-sig") as f:
                        rows = list(csv.DictReader(f))
                    cols = rows[0].keys() if rows else []
                    for k in ("x", "y", "err", "group"):
                        if d.get(k) and d[k] not in cols:
                            add("fail", "chart_column_missing", id=el["id"], column=d[k], available=list(cols))
                    if d.get("y") in cols:
                        bad = [r[d["y"]] for r in rows if not re.fullmatch(r"\s*[-+]?\d+(\.\d+)?([eE][-+]?\d+)?\s*", r[d["y"]] or "")]
                        if bad:
                            add("warn", "chart_nonnumeric_values", id=el["id"], examples=bad[:5])
            elif "values" in d:
                if not has_source(el):
                    add("fail", "chart_inline_without_source", id=el["id"])
            else:
                add("fail", "chart_no_data", id=el["id"])
            if el.get("ylabel") and not re.search(r"\(.*\)|\[.*\]|/|%|unitless|a\.u\.", el["ylabel"]):
                add("warn", "chart_axis_unit", id=el["id"], axis="y", label=el["ylabel"])
        if t == "scalebar" and not re.search(r"\d", el.get("label", "")):
            add("fail", "scalebar_value_missing", id=el["id"])

    for p in spec.get("panels", []):
        ids["panel:" + p["id"]] = p
    blob = json.dumps(spec, ensure_ascii=False)
    for tok in ("TODO", "PLACEHOLDER", "<fill"):
        if tok in blob:
            add("fail", "template_placeholder_left", token=tok, hint="replace every template placeholder with sourced content or delete the element")
    kinds_used, colors_used = set(), set()
    for pid, ar in iter_arrows(spec):
        if "id" not in ar:
            add("fail", "schema_id_missing", element=f"arrow {ar.get('from')}->{ar.get('to')}")
            continue
        if ar["id"] in ids:
            add("fail", "schema_duplicate_id", id=ar["id"])
        ids[ar["id"]] = ar
        for end in ("from", "to"):
            if ar.get(end) not in ids:
                add("fail", "arrow_endpoint_unknown", id=ar["id"], end=end, ref=ar.get(end))
        k = ar.get("kind", "activates")
        if k not in ARROW_KINDS:
            add("fail", "arrow_kind_invalid", id=ar["id"], kind=k, allowed=sorted(ARROW_KINDS))
        kinds_used.add(k)
        if not has_source(ar):
            add("warn", "provenance_missing", id=ar["id"], hint="direction/causality must be supported by the source")
    for pid, el in iter_elements(spec):
        if el.get("type", "entity") == "entity" and el.get("category"):
            colors_used.add((el["category"], el.get("fill")))

    legends = [el for _, el in iter_elements(spec) if el.get("type") == "legend"]
    legend_labels = " ".join(e.get("label", "") for L in legends for e in L.get("entries", [])).lower()
    legend_markers = {e.get("marker", "") for L in legends for e in L.get("entries", [])}
    for cat, fill in sorted(colors_used, key=str):
        if cat.lower() not in legend_labels:
            add("warn", "legend_missing_category", category=cat, fill=fill)
    meaningful = kinds_used & {"inhibits", "produces", "converts", "binds", "associates"}
    for k in meaningful:
        if f"arrow_{k}" not in legend_markers and k not in legend_labels:
            add("warn", "legend_missing_arrow_kind", kind=k)
    fills = {(el.get("fill") or "").lower() for _, el in iter_elements(spec) if el.get("type", "entity") == "entity"}
    if len(fills - {""}) >= 3 and not legends:
        add("warn", "legend_absent", detail=f"{len(fills)} fill colors used without any legend")

    if a.mode == "graphical_abstract":
        roles = [p.get("role") for p in spec.get("panels", [])]
        for r in ROLES_GA:
            if r not in roles:
                add("fail", "ga_role_missing", role=r, hint="add a panel with this role or justify in result.json uncertainty")
    if not spec.get("title"):
        add("warn", "title_missing")

    fails = [r for r in res if r["level"] == "fail"]
    out = dict(status="pass" if not fails else "fail", n_fail=len(fails), n_warn=len(res) - len(fails),
               materials=mats, n_material_numbers=len(mat_nums), results=res)
    if a.out:
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=out["status"], fails=out["n_fail"], warns=out["n_warn"]), ensure_ascii=False))
    for r in res[:20]:
        print(("FAIL " if r["level"] == "fail" else "WARN ") + json.dumps(r, ensure_ascii=False))
    return 0 if not fails else 2


if __name__ == "__main__":
    sys.exit(main())
