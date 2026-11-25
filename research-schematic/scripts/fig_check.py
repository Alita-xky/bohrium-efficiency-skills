#!/usr/bin/env python3
"""Pre-render audit of a figure spec against the source materials.

Usage:
  python3 fig_check.py --spec spec.json [--materials /app/work/text/*.txt data.csv ...] \
                      [--mode graphical_abstract|schematic|chart|mechanism|setup] [--out check_report.json]

Checks (fail = must fix before rendering; warn = fix or explain in result.json):
  * schema: unique ids, required geometry, arrow endpoints exist, valid arrow kinds
  * provenance: every scientific element (entity/arrow/chart/text with numbers) carries a source
  * number provenance: each number printed on the figure appears in the materials (text or CSV)
  * chart data: CSV columns exist, values numeric, axis labels carry units
  * legend coverage: every encoding color/arrow kind used is explained in a legend
  * graphical abstract completeness: panels with roles problem / method / result / conclusion
  * icons: known names only (the list is printed on failure); an icon of a scientific object (biology / lab
    group) needs a real source on its element; warn when nothing in the materials names that object
"""
import argparse, csv, glob, json, os, re, sys

SUPER = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
MANT = r"[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?"
POW = r"\s?[×x·*]\s?10\s?\^?\{?[-+]?\d{1,3}\}?"
NUM_RE = re.compile(r"(?<![A-Za-z0-9_.\-])(?:10\^\{?[-+]?\d{1,3}\}?|" + MANT + "(?:" + POW + ")?)")
ARROW_KINDS = {"activates", "promotes", "inhibits", "flow", "produces", "converts", "binds", "associates", "next",
               "modifies", "transports", "catalyzes"}
ROLES_GA = ["problem", "method", "result", "conclusion"]
SCI_TYPES = {"entity", "arrow", "chart", "image", "scalebar"}
# keep in sync with ICON_GROUPS / ICON_ALIASES in fig_render.py
ICON_GROUPS = {
    "biology": ["cell", "nucleus", "mitochondrion", "receptor", "channel", "protein", "peptide", "molecule", "dna",
                "rna", "virus", "bacterium", "antibody", "plant"],
    "lab": ["flask", "beaker", "cuvette", "laser", "lens", "detector", "microscope", "pump", "tube", "computer",
            "database", "chip"],
    "generic": ["document", "chart", "gear"],
}
ICON_ALIASES = {"membrane_receptor": "receptor", "ion_channel": "channel", "transporter": "channel", "blob": "protein",
                "small_molecule": "molecule", "gene": "dna", "sample_cell": "cuvette", "sample": "cuvette",
                "camera": "detector", "pipe": "tube", "tubing": "tube", "monitor": "computer", "cylinder": "database",
                "sensor": "chip", "paper": "document", "process": "gear", "seedling": "plant"}
SCI_ICONS = set(ICON_GROUPS["biology"]) | set(ICON_GROUPS["lab"])
# words that show the materials name the object an icon depicts (lower-case substrings); None = names vary too much
ICON_TERMS = dict(
    cell=["cell"], nucleus=["nucle"], mitochondrion=["mitochond"], receptor=["receptor"],
    channel=["channel", "transporter", "pore", "pump"], dna=["dna", "gene", "genom", "promoter", "chromatin", "plasmid"],
    rna=["rna", "transcript"], virus=["virus", "viral", "phage", "virion"],
    bacterium=["bacteri", "microb", "coli", "strain", "bacill"], antibody=["antibod", "igg", "immunoglobulin", "nanobod"],
    plant=["plant", "seedling", "wheat", "rice", "maize", "arabidopsis", "tobacco", "crop", "leaf", "leaves", "root", "shoot"],
    flask=["flask", "culture", "incubat"], beaker=["beaker", "solution", "vessel", "container"],
    cuvette=["cuvette", "sample", "flow cell"], laser=["laser"], lens=["lens", "objective", "optic"],
    detector=["detector", "camera", "ccd", "cmos", "photodiode", "photomultiplier", "pmt", "sensor", "spectrometer"],
    microscope=["microscop"], pump=["pump", "perfus", "syringe"], tube=["tube", "tubing", "pipe", "capillar"],
    computer=["computer", "software", "workstation", "server", "analys", "algorithm", "code"],
    database=["database", "repository", "dataset", "archive"],
    chip=["chip", "sensor", "microfluidic", "array", "electrode", "circuit"])


def icon_canonical(name):
    n = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ICON_ALIASES.get(n, n)


def valid_icon_names():
    return sorted(n for g in ICON_GROUPS.values() for n in g) + ["aliases: " + ", ".join(
        f"{a}->{b}" for a, b in sorted(ICON_ALIASES.items()))]




def quote_found(q, mat_squash):
    """Verbatim test on normalised text: the whole quote (short) or its first and last 60 characters (long)."""
    parts = [squash(x) for x in re.split(r"\.\.\.|…|\[\.\.\.\]", q)]
    for k in [x for x in parts if len(x) >= 8] or [squash(q)]:   # "A ... B": each kept part must be verbatim
        if len(k) <= 120:
            if k not in mat_squash:
                return False
        elif not (k[:60] in mat_squash and k[-60:] in mat_squash):
            return False
    return True


def find_data_file(p, spec_dir):
    """Resolve a data path the way an agent means it: absolute, next to the spec, cwd, /input, /app/work,
    then the same file name anywhere under /input or /app/work. Returns the first existing path (or the
    spec-relative path when nothing exists, so the error names it)."""
    if not p:
        return p
    cands = [p] if os.path.isabs(p) else [os.path.join(spec_dir, p), os.path.abspath(p), os.path.join("/input", p),
                                           os.path.join("/app/work", p)]
    for c in cands:
        if os.path.exists(c):
            return c
    import glob as _glob
    base = os.path.basename(p)
    for root in ("/input", "/app/work", spec_dir):
        hits = sorted(_glob.glob(os.path.join(root, "**", base), recursive=True))
        if hits:
            return hits[0]
    return cands[0]

def load_json_lenient(path):
    """Agent-written JSON: allow raw newlines/tabs inside strings and a trailing comma; on failure
    exit with the line/column and the offending line instead of a traceback."""
    txt = open(path, encoding="utf-8-sig").read()
    try:
        return json.loads(txt, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", txt), strict=False)
        except json.JSONDecodeError as e:
            line = txt.splitlines()[e.lineno - 1] if e.lineno - 1 < len(txt.splitlines()) else ""
            sys.exit(f"INVALID JSON in {path}: {e.msg} at line {e.lineno} col {e.colno}: {line[:160]!r}")

def norm_text(s):
    """Unify number spellings: unicode minus, superscript exponents (10¹⁸ -> 10^18), dot operators."""
    s = s.replace("−", "-").replace("–", "-").replace("⋅", "·").replace(" ", " ").replace(" ", " ")
    s = re.sub(r"10([⁺⁻]?[⁰¹²³⁴-⁹]+)", lambda m: "10^" + m.group(1).translate(SUPER), s)
    return s


def num_keys(tok):
    """All plausible values of a number token -> set of canonical strings ('%.6g').
    Handles 2.3e19, 2.3 × 10^19, 2.3 · 1019 (PDF-flattened superscript), 10^-3, 1,000 / 3,3."""
    tok = tok.strip()
    m = re.fullmatch(r"10\^\{?([-+]?\d{1,3})\}?", tok)
    if m:
        return {"%.6g" % (10.0 ** int(m.group(1)))}
    m = re.fullmatch(r"([-+]?\d+(?:[.,]\d+)?)(?:[eE]([-+]?\d+))?(?:\s?[×x·*]\s?10\s?\^?\{?([-+]?\d{1,3})\}?)?", tok)
    if not m:
        return set()
    mant, e1, e2 = m.groups()
    cands = {mant.replace(",", ".")}
    if re.fullmatch(r"[-+]?\d{1,3},\d{3}", mant):
        cands.add(mant.replace(",", ""))
    out = set()
    for c in cands:
        try:
            v = float(c) * 10.0 ** (int(e1 or 0) + int(e2 or 0))
            out.add("%.6g" % v)
        except (ValueError, OverflowError):
            pass
    return out


def numbers_in(text):
    return [m.group(0) for m in NUM_RE.finditer(norm_text(text or ""))]


def norm_num(s):  # kept for compatibility
    k = num_keys(s)
    return float(next(iter(k))) if k else None


def squash(s):
    """Loose form for quote matching: lowercase, letters/digits/Greek/CJK only."""
    s = norm_text(s or "").lower().replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"[^0-9a-zͰ-Ͽ一-鿿]+", "", s)


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
    for tok in numbers_in(blob):
        nums |= num_keys(tok)
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


def real_source(x):
    """A source pointing into the materials (not just {"type": "design"})."""
    s = x.get("source")
    if isinstance(s, dict):
        return s.get("type") != "design" and bool(s.get("file") or s.get("loc") or s.get("quote"))
    return bool(s and str(s).strip())


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
    spec = load_json_lenient(a.spec)
    spec_dir = os.path.dirname(os.path.abspath(a.spec))
    mats = []
    for m in a.materials:
        mats += glob.glob(m) or [m]
    mat_nums, mat_blob = load_material_numbers(mats)
    mat_squash = squash(mat_blob)
    res = []

    def add(level, check, **kw):
        res.append(dict(level=level, check=check, **kw))

    ids = {}
    auto_ct = {p_.get("id") for p_ in spec.get("panels", []) if p_.get("layout") == "auto"} | (
        {None} if spec.get("layout") == "auto" else set())
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
                "group": ["x", "y", "w", "h"], "icon": ["x", "y", "w", "h", "name"]}.get(t, [])
        if pid in auto_ct and t in ("entity", "icon"):   # positions come from the automatic layered layout
            need = [k for k in need if k not in ("x", "y", "w", "h")]
        for k in need:
            if k not in el:
                add("fail", "schema_field_missing", id=el["id"], field=k)
        for k in ("x", "y", "w", "h"):
            v = el.get(k)
            if isinstance(v, (int, float)) and not (-0.001 <= v <= 1.001):
                add("fail", "schema_coord_range", id=el["id"], field=k, value=v)
        if t in SCI_TYPES and not has_source(el):
            add("warn", "provenance_missing", id=el["id"], hint="add source:{file,loc,quote} or source:{type:'design'}")
        icon_raw = el.get("name") if t == "icon" else (el.get("icon") if t == "entity" else None)
        if t == "icon" or icon_raw:
            ic = icon_canonical(icon_raw)
            if ic not in {n for g in ICON_GROUPS.values() for n in g}:
                add("fail", "icon_unknown", id=el["id"], icon=icon_raw, valid=valid_icon_names(),
                    hint="use one of these names (or an alias), or remove the icon")
            elif ic in SCI_ICONS and not real_source(el):
                add("fail", "icon_without_source", id=el["id"], icon=ic,
                    hint="an icon of a scientific object needs source:{file,loc} naming that object; "
                         "draw icons only for objects the materials name, never as decoration")
            elif ic in SCI_ICONS and mat_blob and ICON_TERMS.get(ic) and \
                    not any(w in mat_blob.lower() for w in ICON_TERMS[ic]):
                add("warn", "icon_not_named_in_materials", id=el["id"], icon=ic, looked_for=ICON_TERMS[ic][:6],
                    hint="no material mentions this object: remove the icon (it would imply an unstated fact) "
                         "or pick the icon of the object the source does name")
            elif t == "icon" and not has_source(el):
                add("warn", "provenance_missing", id=el["id"], hint="generic icon: add source:{type:'design'}")
        text = " ".join(str(el.get(k) or "") for k in ("label", "text", "title", "xlabel", "ylabel"))
        text += " " + " ".join(str(e.get("label", "")) for e in el.get("entries", []))
        nums_here = numbers_in(text)
        if nums_here and not has_source(el) and t != "legend":
            add("fail", "number_without_source", id=el["id"], numbers=nums_here)
        if nums_here and mat_nums:
            for n in nums_here:
                k = num_keys(n)
                if k and not (k & mat_nums) and not el.get("derived"):
                    if num_keys(n.lstrip("+-")) & mat_nums:
                        add("warn", "number_sign_differs", id=el["id"], number=n,
                            hint="the source states the magnitude without this sign: make sure the direction is stated there")
                    else:
                        add("fail", "number_not_in_materials", id=el["id"], number=n,
                            hint="copy the exact value from the source, or set derived:{formula,inputs}")
        if t == "entity":
            lab = str(el.get("label") or "")
            nwords = len(lab.split()) + len(re.findall("[\u4e00-\u9fff]", lab)) // 2
            if nwords > 25:
                add("warn", "label_too_long", id=el["id"], words=nwords,
                    hint="keep the key phrase in the source's wording (<= ~20 words); details go to a footnote or result.json")
        q = el.get("source", {}).get("quote") if isinstance(el.get("source"), dict) else None
        if q and mat_squash and not quote_found(q, mat_squash):
            add("warn", "quote_not_found", id=el["id"], quote=q[:70],
                hint="copy the sentence printed by extract_materials.py --find (verbatim, same locator)")
        if t == "chart":
            d = el.get("data", {})
            if "csv" in d:
                p = find_data_file(d["csv"], spec_dir)
                if not os.path.exists(p):
                    add("fail", "chart_csv_missing", id=el["id"], path=p)
                else:
                    with open(p, newline="", encoding="utf-8-sig") as f:
                        rows = list(csv.DictReader(f))
                    cols = rows[0].keys() if rows else []
                    for k in ("x", "y", "err", "group"):
                        for c in (d.get(k) if isinstance(d.get(k), list) else [d.get(k)]):
                            if c and c not in cols:
                                add("fail", "chart_column_missing", id=el["id"], column=c, available=list(cols))
                    for yc in (d.get("y") if isinstance(d.get("y"), list) else [d.get("y")]):
                        if yc not in cols:
                            continue
                        vals = [(r.get(yc) or "").strip() for r in rows]
                        empty = sum(1 for v in vals if v in ("", "NA", "NaN", "nan", "-", "n.r.", "—"))
                        bad = [v for v in vals if v not in ("", "NA", "NaN", "nan", "-", "n.r.", "—") and
                               not re.fullmatch(r"[-+\u2212]?\d+(\.\d+)?([eE][-+]?\d+)?", v.replace(",", ""))]
                        if bad:
                            add("warn", "chart_nonnumeric_values", id=el["id"], column=yc, examples=bad[:5])
                        if empty:
                            add("info", "chart_missing_values", id=el["id"], column=yc, n=empty,
                                hint="rendered as 'n.r.' markers; never fill them; state it in footnote and result.json")
                        if "log" in (el.get("yscale"), el.get("xscale")) and any(
                                re.fullmatch(r"[-+]?\d+(\.\d+)?([eE][-+]?\d+)?", v) and float(v) <= 0 for v in vals):
                            add("fail", "log_scale_nonpositive", id=el["id"], column=yc)
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
    def strip_meta(o):
        if isinstance(o, dict):
            return {k: strip_meta(v) for k, v in o.items() if not str(k).startswith("_")}
        if isinstance(o, list):
            return [strip_meta(v) for v in o]
        return o
    blob = json.dumps(strip_meta(spec), ensure_ascii=False)
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
        if el.get("type", "entity") in ("entity", "icon") and el.get("category"):
            colors_used.add((el["category"], el.get("fill") or el.get("color")))

    loose = [("title", (spec.get("title") or {}).get("text", "")), ("footnote", spec.get("footnote") or "")]
    loose += [("panel:" + p["id"], p.get("title") or "") for p in spec.get("panels", [])]
    loose += [(ar.get("id"), ar.get("label") or "") for _, ar in iter_arrows(spec)]
    for lid, txt in loose:
        for n in numbers_in(txt):
            k = num_keys(n)
            if k and mat_nums and not (k & mat_nums):
                add("warn", "number_not_in_materials", id=lid, number=n,
                    hint="numbers in titles/footnotes/arrow labels must also come from the materials")
    legends = [el for _, el in iter_elements(spec) if el.get("type") == "legend"]
    cat_cols = {}
    for _, el in iter_elements(spec):
        if el.get("type", "entity") in ("entity", "icon") and el.get("category"):
            cat_cols.setdefault(el["category"], set()).update(
                c.lower() for c in (el.get("fill"), el.get("edge"), el.get("color")) if c)
    seen_cols = {}
    for L in legends:
        for e in L.get("entries", []):
            lab = (e.get("label") or "").lower()
            col = (e.get("color") or "").lower()
            if e.get("marker", "square").startswith("arrow"):
                continue
            for cat, cols in cat_cols.items():
                if cat.lower() in lab and col and col not in cols and (e.get("fill") or "").lower() not in cols:
                    add("warn", "legend_color_mismatch", category=cat, legend_color=col, entity_colors=sorted(cols),
                        hint="legend swatch must use the entity fill or edge colour of that category")
            if col and col in seen_cols and seen_cols[col] != lab:
                add("warn", "legend_duplicate_color", color=col, labels=[seen_cols[col], lab],
                    hint="two categories with one colour cannot be told apart")
            seen_cols.setdefault(col, lab)
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

    ents = [el for _, el in iter_elements(spec) if el.get("type", "entity") == "entity"
            or (el.get("type") == "icon" and real_source(el))]   # icons of named objects count as entities
    charts = [el for _, el in iter_elements(spec) if el.get("type") == "chart"]
    arrows = [ar for _, ar in iter_arrows(spec)]
    sci_arrows = [ar for ar in arrows if not (isinstance(ar.get("source"), dict) and ar["source"].get("type") == "design")]
    for ar in sci_arrows:   # every mode: a quoted relation must be quoted verbatim
        src = ar.get("source") if isinstance(ar.get("source"), dict) else {}
        if src.get("quote") and mat_squash and not quote_found(src["quote"], mat_squash):
            add("warn", "quote_not_found", id=ar.get("id"), quote=src["quote"][:70],
                hint="copy the sentence printed by extract_materials.py --find (verbatim)")
    if a.mode == "chart":
        if not charts:
            add("fail", "chart_missing", hint="a source_value_chart needs at least one chart element bound to data")
        for c in charts:
            if "csv" not in c.get("data", {}) and not (isinstance(c.get("source"), dict) and c["source"].get("quote")):
                add("fail", "chart_values_unverifiable", id=c["id"], hint="bind to a CSV extracted from the materials, or give source.quote with the exact values")
            value_label = c.get("xlabel") if c.get("kind") == "barh" else c.get("ylabel")
            if not value_label:
                add("fail", "chart_axis_label_missing", id=c["id"],
                    hint="the value axis needs 'Quantity (unit)' (ylabel; xlabel for barh)")
    if a.mode in ("mechanism", "schematic"):
        if len(ents) < 3:
            add("fail", "too_few_entities", n=len(ents))
        if not sci_arrows:
            add("fail", "no_relations", hint="draw the source-stated relations as arrows with kinds")
        for ar in sci_arrows:
            src = ar.get("source") if isinstance(ar.get("source"), dict) else {}
            if a.mode == "mechanism" and not src.get("quote"):
                add("fail", "relation_without_quote", id=ar.get("id"),
                    hint="direction and sign of each mechanistic arrow must be backed by a verbatim quote")
        orphans = {e["id"] for e in ents} - {ar.get("from") for ar in arrows} - {ar.get("to") for ar in arrows}
        if orphans and len(orphans) > len(ents) // 2:
            add("warn", "many_unconnected_entities", ids=sorted(orphans)[:10])
    if a.mode == "mechanism":
        if not spec.get("panels"):
            add("warn", "no_compartments", hint="use panels for compartments (extracellular / membrane / cytoplasm / nucleus) when the source names them")
    if a.mode == "setup":
        steps = [ar for ar in arrows if ar.get("kind") in ("next", "flow")]
        if len(ents) >= 3 and not steps:
            add("fail", "route_order_missing", hint="connect stages/components in the order stated in the methods with next/flow arrows")
    if a.mode == "graphical_abstract":
        roles = [p.get("role") for p in spec.get("panels", [])]
        for r in ROLES_GA:
            if r not in roles:
                add("fail", "ga_role_missing", role=r, hint="add a panel with this role or justify in result.json uncertainty")
    if not spec.get("title"):
        add("warn", "title_missing")

    fails = [r for r in res if r["level"] == "fail"]
    out = dict(status="pass" if not fails else "fail", n_fail=len(fails),
               n_warn=sum(1 for r in res if r["level"] == "warn"),
               materials=mats, n_material_numbers=len(mat_nums), results=res)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=out["status"], fails=out["n_fail"], warns=out["n_warn"]), ensure_ascii=False))
    for r in sorted(res, key=lambda r: {"fail": 0, "warn": 1}.get(r["level"], 2))[:25]:
        print(r["level"].upper() + " " + json.dumps(r, ensure_ascii=False))
    return 0 if not fails else 2


if __name__ == "__main__":
    sys.exit(main())
