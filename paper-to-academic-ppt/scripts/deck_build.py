#!/usr/bin/env python3
"""deck_build.py - deck spec JSON -> editable .pptx (template placeholders, notes, figures, charts, tables).

Usage:
  python3 deck_build.py --spec /app/work/spec.json [--spec part2.json] [--facts /app/work/facts.json]
         [--template given.pptx|.potx] --out /app/output/deck.pptx [--report /app/output/build_report.json]
         [--backend auto|pptx|xml] [--keep-template-slides]

Backends: python-pptx when importable (auto), otherwise a pure-stdlib OOXML writer (env DECK_BACKEND=xml forces
it). If the python-pptx build raises, the stdlib writer is used and the reason is recorded. Both backends write
the same XML for text (placeholders keep template inheritance; text boxes carry explicit bullets), so renders match.
Without --template a clean academic 16:9 template is generated in code and saved next to the deck.
Font sizes: placeholder text inherits the template sizes; only if the estimated text height overflows the box is
a smaller size from the ladder written (never below meta.min_body_pt, default 12). Every override is reported.
"""
import argparse, csv, json, os, re, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402
import deck_design as D  # noqa: E402

EMU_IN, EMU_PT = K.EMU_IN, K.EMU_PT
KIND_TO_LAYOUT = {"title": "title", "section": "section", "bullets": "content", "two_column": "two",
                  "figure": "title_only", "figures": "title_only", "chart": "title_only", "table": "title_only",
                  "blank": "blank", "cards": "title_only", "bignumber": "title_only", "statement": "title_only",
                  "quote": "title_only", "agenda": "title_only"}
LABELS = {"en": dict(source="Source: ", time="Time", transition="Transition", key="Key message", evidence="Evidence",
                     ifasked="If asked", missing="Figure not available", qa="Prepared Q&A (talk_plan.json)",
                     backup="backup"),
          "zh": dict(source="来源：", time="时长", transition="过渡", key="核心信息", evidence="证据", ifasked="若被追问",
                     missing="图像不可用", qa="问答预案（talk_plan.json）", backup="备用页")}


# ----------------------------------------------------------------------------- spec helpers
def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_specs(paths):
    spec = None
    for p in paths:
        d = load_json(p)
        if isinstance(d, list):
            d = {"slides": d}
        K.resolve_spec_paths(d.get("slides", []), os.path.dirname(os.path.abspath(p)))
        if spec is None:
            spec = d
            spec.setdefault("slides", [])
        else:
            for k, v in d.items():
                if k == "slides":
                    spec["slides"].extend(v)
                elif k not in spec:
                    spec[k] = v
    return spec or {"slides": []}


def load_facts(path):
    if not path or not os.path.exists(path):
        return {}
    d = load_json(path)
    rows = d.get("facts", d) if isinstance(d, dict) else d
    if isinstance(rows, dict):
        return {k: dict(v, id=k) for k, v in rows.items()}
    return {r.get("id"): r for r in rows if isinstance(r, dict) and r.get("id")}


def slide_kind(sl):
    k = sl.get("layout")
    if k:
        return {"content": "bullets", "text": "bullets", "two": "two_column", "image": "figure",
                "conclusion": "bullets", "closing": "bullets", "comparison": "two_column", "big_number": "bignumber",
                "bignumbers": "bignumber", "callout": "bignumber", "card": "cards", "outline": "agenda",
                "list": "bullets"}.get(k, k)
    r = sl.get("role", "")
    if r == "title":
        return "title"
    if r == "section":
        return "section"
    for key, kind in (("figures", "figures"), ("figure", "figure"), ("chart", "chart"), ("table", "table")):
        if sl.get(key):
            return kind
    if sl.get("left") or sl.get("right"):
        return "two_column"
    return "bullets"


def norm_bullets(items):
    out = []
    for b in items or []:
        if isinstance(b, str):
            out.append(dict(text=b, level=0))
        elif isinstance(b, dict):
            d = dict(b)
            d["text"] = str(d.get("text", ""))
            d["level"] = int(d.get("level", 0) or 0)
            out.append(d)
        elif isinstance(b, (list, tuple)) and b:
            out.append(dict(text=str(b[0]), level=int(b[1]) if len(b) > 1 else 0))
    return out


def source_refs(sl, facts):
    """short human labels for the slide's sources: 'Fig. 3 (p.6)'"""
    refs = []
    ids = list(sl.get("sources") or [])
    for b in sl.get("bullets") or []:
        if isinstance(b, dict):
            ids += b.get("src") or []
    for key in ("figure", "chart", "table"):
        v = sl.get(key)
        if isinstance(v, dict):
            ids += v.get("src") or []
            if v.get("ref"):
                refs.append(v["ref"] + (" (p.%s)" % v["page"] if v.get("page") else ""))
    for f in sl.get("figures") or []:
        if isinstance(f, dict) and f.get("ref"):
            refs.append(f["ref"] + (" (p.%s)" % f["page"] if f.get("page") else ""))
    for s in ids:
        src = facts.get(s, {}).get("source", {}) if isinstance(s, str) else (s.get("source", s) if isinstance(s, dict) else {})
        if not src:
            continue
        loc = str(src.get("loc", ""))
        m = re.match(r"p(\d+)", loc)
        page = " (p.%s)" % m.group(1) if m else (" (%s)" % loc if loc else "")
        lab = (src.get("ref") or "").strip()
        refs.append((lab + page) if lab else ("%s%s" % (os.path.basename(str(src.get("file", ""))), page)).strip())
    seen, out = set(), []
    for r in refs:
        r = r.strip()
        if r and r not in seen and not any(o != r and o.startswith(r + " (") for o in refs):
            seen.add(r)
            out.append(r)
    return out


def compose_notes(sl, lab, refs):
    parts = []
    if sl.get("notes"):
        parts.append(str(sl["notes"]).strip())
    if sl.get("key_message"):
        parts.append("%s: %s" % (lab["key"], sl["key_message"]))
    if sl.get("transition"):
        parts.append("%s: %s" % (lab["transition"], sl["transition"]))
    if sl.get("time_s"):
        t = int(sl["time_s"])
        parts.append("%s: ~%d:%02d" % (lab["time"], t // 60, t % 60))
    for qa in sl.get("if_asked") or []:
        parts.append("%s: %s" % (lab["ifasked"], qa))
    if refs:
        parts.append("%s: %s" % (lab["evidence"], "; ".join(refs)))
    return "\n".join(parts)


def qa_cue_target(slides):
    """1-based index of the slide whose notes carry the Q&A cue list: the Q&A slide, else the last main slide"""
    roles = [str(x.get("role", "")).lower() for x in slides]
    for i, r in enumerate(roles, 1):
        if r in ("qa", "questions"):
            return i
    main = [i for i, r in enumerate(roles, 1) if r not in ("backup", "appendix", "references")]
    return main[-1] if main else None


def qa_cue_text(qa, lab):
    """the prepared questions as a speaker cue list: question -> first answer point (evidence; backup slide)"""
    out = [lab["qa"] + ":"]
    for k, q in enumerate(qa, 1):
        ap_ = q.get("answer_points")
        first = (ap_[0] if isinstance(ap_, list) and ap_ else ap_) or ""
        tail = ", ".join(x for x in [", ".join(q.get("evidence") or []),
                                     ("%s %s" % (lab["backup"], q["backup_slide"])) if q.get("backup_slide") else ""] if x)
        out.append("%d. %s -> %s%s" % (k, q.get("question", ""), first, (" (%s)" % tail) if tail else ""))
    return "\n".join(out)


# ----------------------------------------------------------------------------- backends
class XmlBackend:
    name = "ooxml-stdlib"
    version = "pptkit-1.0"

    def __init__(self, tpl, keep_slides=False, pkg=None):
        self.pkg = pkg if pkg is not None else K.Package(tpl)
        if not keep_slides:
            for s in K.slide_list(self.pkg):
                K.remove_slide(self.pkg, s["part"])
        self.cat = K.catalog(self.pkg)

    def new_slide(self, layout):
        return dict(layout=layout, shapes=[], media=[], next_id=2, notes=None)

    def _id(self, s):
        s["next_id"] += 1
        return s["next_id"] - 1

    def ph_text(self, s, ph, paras, name, anchor=None):
        s["shapes"].append(K.ph_sp_xml(self._id(s), name, ph["attrs"], paras, box=ph["box"] if ph.get("fixed_box") else None,
                                       anchor=anchor))

    def textbox(self, s, box, paras, name, anchor="t", fill=None, line=None):
        s["shapes"].append(K.textbox_xml(self._id(s), name, box, paras, anchor, fill, line))

    def raw(self, s, fn, back=False):
        x = fn(self._id(s))
        s["shapes"].insert(0, x) if back else s["shapes"].append(x)

    def picture(self, s, path, box, name, descr="", crop=None):
        rid = "rId%d" % (len(s["media"]) + 2)
        s["media"].append((rid, path))
        s["shapes"].append(K.pic_xml(self._id(s), name, rid, box, descr, crop=crop))

    def table(self, s, box, rows, col_w, row_h, size, header=True, highlight=()):
        s["shapes"].append(K.table_xml(self._id(s), "Table", box, rows, col_w, row_h, size, header, highlight)
                           .replace('name="Table"', 'name="%s"' % K.xa(s.get("table_name", "Table")), 1))

    def notes(self, s, text):
        s["notes"] = text

    def finish(self, s):
        K.add_slide_part(self.pkg, K.slide_xml(s["shapes"]), s["layout"]["part"], s["media"], s["notes"])

    def save(self, path):
        self.pkg.save(path)


class PptxBackend:
    name = "python-pptx"

    def __init__(self, tpl, keep_slides=False):
        import pptx
        from pptx import Presentation
        from pptx.oxml import parse_xml
        self.version = pptx.__version__
        self._parse = parse_xml
        self.prs = Presentation(tpl)
        if not keep_slides:
            lst = self.prs.slides._sldIdLst
            for sid in list(lst):
                self.prs.part.drop_rel(sid.rId)
                lst.remove(sid)
        self.cat = K.catalog(K.Package(tpl))

    def new_slide(self, layout):
        pl = self.prs.slide_masters[layout["master"]].slide_layouts[layout["index"]]
        sl = self.prs.slides.add_slide(pl)
        return dict(layout=layout, slide=sl, used=set())

    def _frag(self, xml):
        return self._parse('<w %s>%s</w>' % (K.NSDECL, xml))[0]

    def _append(self, s, xml):
        tree = s["slide"].shapes._spTree
        el = self._frag(xml)
        nid = K.max_shape_id(tree) + 1
        for c in el.iter():
            if c.tag.endswith("}cNvPr"):
                c.set("id", str(nid))
                nid += 1
        tree.append(el)
        s["used"].add(self._sid(el))
        return el

    @staticmethod
    def _sid(el):
        for c in el.iter():
            if c.tag.endswith("}cNvPr"):
                return c.get("id")
        return None

    def ph_text(self, s, ph, paras, name, anchor=None):
        target = None
        for shp in s["slide"].placeholders:
            if shp.placeholder_format.idx == ph["idx"]:
                target = shp
                break
        if target is None:  # footer-type placeholders are not cloned by python-pptx
            self._append(s, K.ph_sp_xml(2, name, ph["attrs"], paras, box=ph["box"] if ph.get("fixed_box") else None, anchor=anchor))
            return
        el = target._element
        target.name = name
        if ph.get("fixed_box"):
            K.set_xfrm(el, ph["box"])
        old = el.find(K.P + "txBody")
        new = self._frag(K.txbody_xml(paras, "ph", anchor=anchor, autofit=None))
        if old is not None:
            el.replace(old, new)
        else:
            el.append(new)
        s["used"].add(self._sid(el))

    def textbox(self, s, box, paras, name, anchor="t", fill=None, line=None):
        from pptx.util import Emu
        tb = s["slide"].shapes.add_textbox(Emu(int(box[0])), Emu(int(box[1])), Emu(int(box[2])), Emu(int(box[3])))
        tb.name = name
        el = tb._element
        el.replace(el.find(K.P + "txBody"), self._frag(K.txbody_xml(paras, "tb", anchor=anchor, autofit="none")))
        if fill:
            from pptx.dml.color import RGBColor
            tb.fill.solid()
            if fill in K.SCHEME:
                from pptx.enum.dml import MSO_THEME_COLOR
                tb.fill.fore_color.theme_color = getattr(MSO_THEME_COLOR, fill.upper(), MSO_THEME_COLOR.ACCENT_1)
            else:
                tb.fill.fore_color.rgb = RGBColor.from_string(fill.lstrip("#"))
        if line:
            from pptx.dml.color import RGBColor
            tb.line.width = 9525
            if line not in K.SCHEME:
                tb.line.color.rgb = RGBColor.from_string(line.lstrip("#"))
        s["used"].add(self._sid(el))

    def raw(self, s, fn, back=False):
        el = self._append(s, fn(2))
        if back:  # behind the placeholders that python-pptx cloned from the layout
            tree = s["slide"].shapes._spTree
            tree.remove(el)
            tree.insert(2, el)

    def picture(self, s, path, box, name, descr="", crop=None):
        from pptx.util import Emu
        pic = s["slide"].shapes.add_picture(path, Emu(int(box[0])), Emu(int(box[1])), Emu(int(box[2])), Emu(int(box[3])))
        pic.name = name
        if crop:
            pic.crop_left, pic.crop_top, pic.crop_right, pic.crop_bottom = crop
        if descr:
            pic._element.nvPicPr.cNvPr.set("descr", descr)
        s["used"].add(self._sid(pic._element))

    def table(self, s, box, rows, col_w, row_h, size, header=True, highlight=()):
        self._append(s, K.table_xml(2, s.get("table_name", "Table"), box, rows, col_w, row_h, size, header, highlight))

    def notes(self, s, text):
        if text:
            s["slide"].notes_slide.notes_text_frame.text = text

    def finish(self, s):
        tree = s["slide"].shapes._spTree
        for shp in list(s["slide"].placeholders):
            if self._sid(shp._element) not in s["used"]:
                tree.remove(shp._element)

    def save(self, path):
        self.prs.save(path)


def make_backend(kind, tpl, keep):
    reason = None
    if kind in ("auto", "pptx") and os.environ.get("DECK_BACKEND", "").lower() != "xml":
        try:
            return PptxBackend(tpl, keep), None
        except ImportError as e:
            reason = "python-pptx not importable: %s" % e
        except Exception as e:
            reason = "python-pptx failed to open template: %r" % (e,)
        if kind == "pptx":
            print("WARN " + reason)
    elif os.environ.get("DECK_BACKEND", "").lower() == "xml":
        reason = "DECK_BACKEND=xml"
    return XmlBackend(tpl, keep), reason


# ----------------------------------------------------------------------------- layout geometry
class Ctx:
    pass


def base_sizes(cat, lay, ph):
    m = cat["masters"][lay["master"]] if cat["masters"] else {"styles": {}}
    kind = K.PH_MAP_TO_MASTER.get(ph["type"], "body")
    st = dict(m.get("styles", {}).get("title" if kind == "title" else ("body" if kind == "body" else "other"), {}))
    for q in m.get("placeholders", []):
        if q["type"] == kind or q["type"] == ph["type"]:
            st.update(q.get("lst_sizes") or {})
            break
    st.update(ph.get("lst_sizes") or {})
    return st


def fit_paras(paras, box, base, min_pt, bullet_pt=22.0, line_factor=None):
    """returns (sizes per para or None if template sizes fit, fits(bool), needed_pt, avail_pt)"""
    w_pt, h_pt = box[2] / EMU_PT, box[3] / EMU_PT
    for f in (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.6, 0.55, 0.5):
        sizes = [max(min_pt, round(base.get(p.get("level", 0) + 1, base.get(1, 18.0)) * f)) for p in paras]
        ps = [dict(text=p["text"], size=sz, level=p.get("level", 0)) for p, sz in zip(paras, sizes)]
        need = K.text_height_pt(ps, w_pt, line_factor=line_factor, bullet_pt=bullet_pt)
        if need <= h_pt:
            return (None if f == 1.0 else sizes), True, need, h_pt
        if all(sz <= min_pt for sz in sizes):
            break
    return sizes, False, need, h_pt


def apply_sizes(paras, sizes):
    if not sizes:
        return paras
    out = []
    for p, sz in zip(paras, sizes):
        q = dict(p)
        q["size"] = sz
        out.append(q)
    return out


def fit_contain(box, iw, ih, valign="ctr"):
    bw, bh = box[2], box[3]
    s = min(bw / float(iw), bh / float(ih))
    w, h = int(iw * s), int(ih * s)
    x = box[0] + (bw - w) // 2
    y = box[1] + ((bh - h) // 2 if valign == "ctr" else 0)
    return [x, y, w, h]


def split_h(box, frac, gap=int(0.25 * EMU_IN)):
    w1 = int((box[2] - gap) * frac)
    return [box[0], box[1], w1, box[3]], [box[0] + w1 + gap, box[1], box[2] - w1 - gap, box[3]]


def split_v(box, frac, gap=int(0.15 * EMU_IN)):
    h1 = int((box[3] - gap) * frac)
    return [box[0], box[1], box[2], h1], [box[0], box[1] + h1 + gap, box[2], box[3] - h1 - gap]


# ----------------------------------------------------------------------------- visuals
def key_series(ch, ycols, ctx):
    """index of the series to highlight: chart.key_series / 'ours' / 'proposed' / the deck's method name"""
    labs = [str((ch.get("series_labels") or {}).get(c, c)) for c in ycols]
    ks = ch.get("key_series")
    if ks is not None:
        for j, (c, l) in enumerate(zip(ycols, labs)):
            if str(ks) in (c, l):
                return j
    for j, l in enumerate(labs):
        if re.search(r"(?i)\bours\b|\(ours\)|proposed|本文|我们的", l):
            return j
    name = getattr(ctx, "method_name", "") or ""
    if name and len(ycols) > 1:
        for j, (c, l) in enumerate(zip(ycols, labs)):
            if name.lower() in (c + " " + l).lower():
                return j
    return None


def render_chart(ch, box, out_png, ctx):
    """matplotlib chart sized to the slide box so 14 pt in the figure is 14 pt on the slide. Bars always start at
    0, carry value labels (the CSV strings) and the key series / highlighted rows use the highlight colour."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = []
    with open(ch["csv"], newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if ch.get("filter"):
        for k, v in ch["filter"].items():
            rows = [r for r in rows if str(r.get(k)) in ([str(x) for x in v] if isinstance(v, list) else [str(v)])]
    xcol = ch["x"]
    ycols = ch["y"] if isinstance(ch["y"], list) else [ch["y"]]
    errs = ch.get("err")
    errs = errs if isinstance(errs, list) else ([errs] * len(ycols) if errs else [None] * len(ycols))

    def num(v):
        try:
            return float(str(v).replace(",", "").replace("−", "-").strip().rstrip("%"))
        except ValueError:
            return float("nan")
    labels = [str(r.get(xcol, "")) for r in rows]
    w_in, h_in = box[2] / EMU_IN, box[3] / EMU_IN
    fams = [ctx.fonts.get("latin", "Arial"), ctx.fonts.get("ea") or "", "DejaVu Sans"]
    plt.rcParams.update({"font.size": 15, "font.family": "sans-serif",
                         "font.sans-serif": [f for f in fams if f] + list(plt.rcParams["font.sans-serif"]),
                         "axes.unicode_minus": False, "axes.edgecolor": "#8A8F98", "axes.labelcolor": "#333333",
                         "xtick.color": "#333333", "ytick.color": "#555555"})
    fig, ax = plt.subplots(figsize=(w_in, h_in), dpi=200)
    acc1 = "#" + ctx.colors.get("accent1", K.DEFAULT_COLORS["accent1"])
    acc2 = "#" + ctx.colors.get("accent2", K.DEFAULT_COLORS["accent2"])
    neutrals = ["#9AA5B1", "#C3CAD3", "#6B7785", "#DDE2E8"]
    kind = ch.get("kind", "bar")
    hl = set(str(x) for x in (ch.get("highlight") or []))
    n = len(ycols)
    mname = (getattr(ctx, "method_name", "") or "").lower()
    if not hl and n == 1 and ch.get("highlight") is None:
        hl = {l for l in labels if re.search(r"(?i)\bours\b|\(ours\)|proposed|本文", l) or (mname and mname in l.lower())}
    ks = key_series(ch, ycols, ctx)
    if n == 1:
        cols_s = [acc1]
    elif ks is not None:
        cols_s, k = [], 0
        for j in range(n):
            if j == ks:
                cols_s.append(acc2)
            else:
                cols_s.append(acc1 if k == 0 and n == 2 else neutrals[k % len(neutrals)])
                k += 1
        if n == 2:
            cols_s = [neutrals[0] if j != ks else acc2 for j in range(n)]
    else:
        cols_s = [acc1] + [neutrals[(j - 1) % len(neutrals)] for j in range(1, n)]
    xs = list(range(len(rows)))
    show_labels = ch.get("labels", True) if kind in ("bar", "barh") else ch.get("labels", False)
    lab_size = 13 if len(rows) * n <= 12 else 11
    ymax = 0.0
    for j, (yc, ec) in enumerate(zip(ycols, errs)):
        ys = [num(r.get(yc)) for r in rows]
        ymax = max([ymax] + [abs(v) for v in ys if v == v])
        es = [num(r.get(ec)) for r in rows] if ec else None
        lab = (ch.get("series_labels") or {}).get(yc, yc)
        if kind == "bar":
            wdt = 0.8 / n
            pos = [x - 0.4 + wdt * (j + 0.5) for x in xs]
            cols = [acc2 if labels[i] in hl else (neutrals[0] if n == 1 else cols_s[j]) for i in range(len(xs))] if hl else cols_s[j]
            bars = ax.bar(pos, ys, wdt * 0.88, yerr=es, capsize=4 if es else 0, color=cols, label=lab, zorder=3)
            if show_labels:
                for b, r in zip(bars, rows):
                    ax.annotate(str(r.get(yc)).strip(), (b.get_x() + b.get_width() / 2, b.get_height()), ha="center",
                                va="bottom", fontsize=lab_size, xytext=(0, 3), textcoords="offset points", color="#222222")
        elif kind == "barh":
            hgt = 0.8 / n
            pos = [x - 0.4 + hgt * (j + 0.5) for x in xs]
            cols = [acc2 if labels[i] in hl else (neutrals[0] if n == 1 else cols_s[j]) for i in range(len(xs))] if hl else cols_s[j]
            bars = ax.barh(pos, ys, hgt * 0.88, xerr=es, capsize=4 if es else 0, color=cols, label=lab, zorder=3)
            if show_labels:
                for b, r in zip(bars, rows):
                    ax.annotate(str(r.get(yc)).strip(), (b.get_width(), b.get_y() + b.get_height() / 2), ha="left",
                                va="center", fontsize=lab_size, xytext=(4, 0), textcoords="offset points", color="#222222")
        elif kind == "scatter":
            xv = [num(r.get(xcol)) for r in rows]
            ax.scatter(xv, ys, s=50, color=cols_s[j], label=lab, zorder=3)
        else:
            xv = [num(r.get(xcol)) for r in rows] if ch.get("numeric_x") else xs
            ax.errorbar(xv, ys, yerr=es, marker="o", lw=2.5 if j == ks else 2, ms=7, capsize=4 if es else 0,
                        color=cols_s[j], label=lab, zorder=3)
            if show_labels:
                for x_, y_, r in zip(xv, ys, rows):
                    ax.annotate(str(r.get(yc)).strip(), (x_, y_), ha="center", va="bottom", fontsize=lab_size - 1,
                                xytext=(0, 6), textcoords="offset points", color=cols_s[j])
    if kind in ("bar", "line") and not ch.get("numeric_x"):
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=ch.get("rotate", 0), ha="right" if ch.get("rotate") else "center")
    if kind == "barh":
        ax.set_yticks(xs)
        ax.set_yticklabels(labels)
        ax.invert_yaxis()
    if ch.get("xlabel"):
        ax.set_xlabel(ch["xlabel"])
    if ch.get("ylabel"):
        ax.set_ylabel(ch["ylabel"])
    elif n == 1 and kind in ("bar", "line"):
        ax.set_ylabel(str((ch.get("series_labels") or {}).get(ycols[0], ycols[0])))
    note = None
    if ch.get("logy"):
        ax.set_yscale("log")
        if kind in ("bar", "barh"):
            note = "log-scale bars have no zero baseline"
    elif kind in ("bar", "barh"):
        lo, hi = (ch.get("ylim") or [0, None])[:2]
        if lo not in (0, None):
            note = "bar axis forced to start at 0 (spec asked for %s)" % lo
        top = hi if hi is not None else ymax * (1.14 if show_labels else 1.05)
        (ax.set_xlim if kind == "barh" else ax.set_ylim)(0, top)
    elif ch.get("ylim"):
        ax.set_ylim(*ch["ylim"])
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    if kind == "bar":
        ax.spines["left"].set_visible(False)
        ax.tick_params(axis="y", length=0)
    ax.grid(axis="y" if kind != "barh" else "x", alpha=0.35, zorder=0)
    ax.set_axisbelow(True)
    if n > 1 or ch.get("legend"):
        ax.legend(frameon=False, fontsize=14, ncol=min(n, 4), loc="upper center", bbox_to_anchor=(0.5, 1.12))
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    with open(out_png[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(dict(chart=ch, rows_used=len(rows), size_in=[round(w_in, 2), round(h_in, 2)], y_from_zero=kind in ("bar", "barh")
                       and not ch.get("logy"), key_series=ks, note=note), f, ensure_ascii=False, indent=1)
    return out_png, note


def resolve_image(fig, ctx, tag):
    """returns (path or None, note)"""
    if fig.get("image"):
        p = fig["image"]
        return (p, "file") if os.path.exists(p) else (None, "image not found: %s" % p)
    if fig.get("pdf"):
        page = int(fig.get("page", 1))
        bbox = fig.get("bbox") or fig.get("crop")
        out = os.path.join(ctx.fig_dir, "%s_p%d.png" % (tag, page))
        p, how = K.crop_pdf_region(fig["pdf"], page, bbox, out, int(fig.get("dpi", 200)))
        return (p, "pdf crop via %s" % how) if p else (None, how)
    return None, "figure has neither image nor pdf"


_TRIM = {}


def trim_crop(path, fig=None):
    """(l, t, r, b) fractions of uniform white margin around a figure image, applied as the picture's display crop
    (srcRect): the embedded image bytes stay unchanged (carried-over pictures keep their hash), the aspect ratio is
    the cropped one, never distorted. None when there is nothing to trim or PIL is missing."""
    if not path or (fig or {}).get("trim") is False:
        return None
    if path in _TRIM:
        return _TRIM[path]
    crop = None
    try:
        from PIL import Image
        im = Image.open(path)
        W, H = im.size
        g = im.convert("RGBA")
        bg = Image.new("RGBA", g.size, (255, 255, 255, 255))
        g = Image.alpha_composite(bg, g).convert("L")
        bb = g.point(lambda v: 255 if v < 238 else 0).getbbox()
        if bb:
            pad = int(0.012 * max(W, H)) + 2
            bb = (max(0, bb[0] - pad), max(0, bb[1] - pad), min(W, bb[2] + pad), min(H, bb[3] + pad))
            c = (bb[0] / float(W), bb[1] / float(H), (W - bb[2]) / float(W), (H - bb[3]) / float(H))
            if (1 - c[0] - c[2]) * (1 - c[1] - c[3]) < 0.97:
                crop = tuple(round(v, 4) for v in c)
    except Exception:
        crop = None
    _TRIM[path] = crop
    return crop


def cropped_size(path, crop):
    sz = K.image_size(path) or (4, 3)
    if not crop:
        return sz
    return (sz[0] * (1 - crop[0] - crop[2]), sz[1] * (1 - crop[1] - crop[3]))


def visual_aspect(fig, ctx):
    """width/height of a figure source (image pixels after the white-margin crop, or pdf bbox), None if unknown"""
    if fig.get("image") and os.path.exists(fig["image"]):
        sz = cropped_size(fig["image"], trim_crop(fig["image"], fig))
        return sz[0] / float(sz[1]) if sz and sz[1] else None
    bb = fig.get("bbox") or fig.get("crop")
    if fig.get("pdf") and bb:
        return (bb[2] - bb[0]) / max(1e-6, (bb[3] - bb[1])) * 0.773  # US letter page aspect; refined after crop
    return None


def caption_of(fig):
    cap = ""
    if fig.get("ref") or fig.get("caption"):
        cap = (fig.get("ref") or "")
        if fig.get("page"):
            cap += " (p.%s)" % fig["page"]
        if fig.get("caption"):
            cap = (cap + ": " if cap else "") + fig["caption"]
    return fig.get("caption_text") or cap


def place_figure(be, s, fig, box, ctx, rep, tag, num, side=None):
    """image (white margins cropped, contain-fit, never stretched) + caption under it; side=(width_emu, note_text):
    caption (and source note) in a column right of the picture, so a tall figure can use the full height"""
    lab = ctx.lab
    cap = caption_of(fig)
    cap_h, cap_size, nl = 0, ctx.scale["caption"], 1
    col_w = side[0] if side else 0
    if cap and not side:
        for cap_size in (ctx.scale["caption"], ctx.scale["note"]):
            nl = K.wrap_count(cap, box[2] / EMU_PT - 14.4, cap_size)
            if nl <= 3:
                break
        cap_h = int((nl * cap_size * 1.25 + 8) * EMU_PT)
    img_area = [box[0], box[1], box[2] - (col_w + int(0.2 * EMU_IN) if side else 0), box[3] - cap_h -
                (int(0.06 * EMU_IN) if cap and not side else 0)]
    path, how = resolve_image(fig, ctx, tag)
    entry = dict(type="figure", ref=fig.get("ref"), kind=fig.get("kind"), how=how)
    if path:
        crop = trim_crop(path, fig)
        sz = cropped_size(path, crop)
        ib = fit_contain(img_area, sz[0], sz[1])
        if side:  # picture + caption column centred together
            group = ib[2] + int(0.2 * EMU_IN) + col_w
            ib[0] = box[0] + max(0, (box[2] - group) // 2)
        elif cap:  # keep caption right under the picture
            total = ib[3] + cap_h + int(0.06 * EMU_IN)
            ib[1] = box[1] + max(0, (box[3] - total) // 2)
        descr = ((fig.get("ref") or "") + " " + (fig.get("caption") or "")).strip()[:900]
        be.picture(s, path, ib, "Figure %d" % num, descr, crop=crop)
        full = K.image_size(path) or sz
        entry.update(image=path, pixels=list(full), crop=list(crop) if crop else None, box_in=[K.to_in(v) for v in ib],
                     dpi_on_slide=round(sz[0] / max(0.01, ib[2] / EMU_IN)),
                     slide_share=round(ib[2] * ib[3] / float(ctx.slide_w * ctx.slide_h), 3))
        if entry["dpi_on_slide"] < 96:
            rep["warnings"].append("%s: low resolution (%d px/in on slide)" % (fig.get("ref") or tag, entry["dpi_on_slide"]))
        cy = ib[1] + ib[3] + int(0.06 * EMU_IN)
    else:
        ph = [dict(text="%s: %s" % (lab["missing"], how), bullet=False, align="center", color="7F7F7F", size=ctx.scale["body"][-1])]
        be.textbox(s, img_area, ph, "Figure %d (missing)" % num, anchor="ctr", line="BFBFBF")
        rep["warnings"].append("FAIL figure unavailable (%s): %s" % (fig.get("ref") or tag, how))
        entry["missing"] = True
        ib = img_area
        cy = img_area[1] + img_area[3] + int(0.06 * EMU_IN)
    if side:
        paras = ([dict(text=cap, size=cap_size, color="404040", bullet=False)] if cap else []) + \
                ([dict(text=side[1], size=ctx.scale["note"], color="7F7F7F", bullet=False, spc=8)] if side[1] else [])
        if paras:
            cx = ib[0] + ib[2] + int(0.2 * EMU_IN)
            be.textbox(s, [cx, box[1], col_w, ib[1] + ib[3] - box[1]], paras, "Caption %d" % num, anchor="b")
            entry["caption_pt"] = cap_size
    elif cap:
        be.textbox(s, [box[0], cy, box[2], cap_h], [dict(text=cap, size=cap_size, color="404040", bullet=False,
                                                          align="center")], "Caption %d" % num)
        entry["caption_pt"] = cap_size
    rep["visuals"].append(entry)


def place_chart(be, s, ch, box, ctx, rep, tag, num):
    entry = dict(type="chart", csv=ch.get("csv"), ref=ch.get("ref"))
    cap = ch.get("caption") or ""
    if ch.get("ref"):
        cap = ch["ref"] + (": " + cap if cap else "")
    cap = ch.get("caption_text") or cap
    cap_h = int((12 * 1.25 * min(2, K.wrap_count(cap, box[2] / EMU_PT, 12)) + 8) * EMU_PT) if cap else 0
    area = [box[0], box[1], box[2], box[3] - cap_h]
    out = os.path.join(ctx.chart_dir, "%s_chart%d.png" % (tag, num))
    try:
        _, cnote = render_chart(ch, area, out, ctx)
        if cnote:
            rep["warnings"].append("chart %s: %s" % (tag, cnote))
        be.picture(s, out, area, "Chart %d" % num, "chart from %s (%s vs %s)" % (os.path.basename(ch["csv"]), ch.get("y"), ch.get("x")))
        entry.update(png=out, spec=out[:-4] + ".json", box_in=[K.to_in(v) for v in area])
    except Exception as e:
        fallback = None
        if isinstance(e, ImportError) and ch.get("csv") and os.path.exists(ch["csv"]):
            try:  # no plotting library: show the same data as a native (editable) table instead of a broken box
                with open(ch["csv"], newline="", encoding="utf-8-sig") as f:
                    data = list(csv.reader(f))
                cols = [ch.get("x")] + list(ch.get("y") if isinstance(ch.get("y"), list) else [ch.get("y")])
                if ch.get("err"):
                    cols += list(ch["err"] if isinstance(ch["err"], list) else [ch["err"]])
                idx = [data[0].index(c) for c in cols if c in data[0]]
                rows = [[r[i] if i < len(r) else "" for i in idx] for r in data[:16]]
                hl = [[i, j] for i, r in enumerate(rows) if i and r and r[0] in [str(v) for v in (ch.get("highlight") or [])]
                      for j in range(len(r))]
                place_table(be, s, dict(rows=rows, ref=ch.get("ref"), caption=(ch.get("caption") or "") +
                                        " (data table: chart renderer unavailable)", highlight=hl), box, ctx, rep, tag, num)
                rep["warnings"].append("chart %s shown as a data table (%r): install matplotlib for the plotted chart" % (tag, e))
                entry.update(fallback="table", error=repr(e))
                fallback = True
            except Exception as e2:
                e = e2
        if not fallback:
            be.textbox(s, area, [dict(text="Chart failed: %r" % (e,), bullet=False, color="C00000", size=14)], "Chart %d (failed)" % num,
                       anchor="ctr", line="C00000")
            rep["warnings"].append("FAIL chart %s: %r" % (tag, e))
            entry["error"] = repr(e)
        else:
            rep["visuals"].append(entry)
            return
    if cap:
        be.textbox(s, [box[0], area[1] + area[3], box[2], cap_h], [dict(text=cap, size=12, color="404040", bullet=False,
                                                                         align="center")], "Caption %d" % num)
    rep["visuals"].append(entry)


def table_rows(tb):
    if tb.get("rows"):
        return [[str(c) for c in r] for r in tb["rows"]]
    if tb.get("csv"):
        with open(tb["csv"], newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
        if tb.get("columns"):
            idx = [rows[0].index(c) for c in tb["columns"] if c in rows[0]]
            rows = [[r[i] if i < len(r) else "" for i in idx] for r in rows]
        if tb.get("max_rows"):
            rows = rows[: int(tb["max_rows"]) + 1]
        return rows
    return []


def place_table(be, s, tb, box, ctx, rep, tag, num):
    rows = table_rows(tb)
    entry = dict(type="table", ref=tb.get("ref"), n_rows=len(rows))
    if not rows:
        rep["warnings"].append("FAIL table %s has no rows" % tag)
        return
    cap = tb.get("ref") or ""
    if tb.get("caption"):
        cap = (cap + ": " if cap else "") + tb["caption"]
    cap = tb.get("caption_text") or cap
    cap_h = int((12 * 1.25 * min(3, K.wrap_count(cap, box[2] / EMU_PT - 15, 12)) + 8) * EMU_PT) if cap else 0  # long captions wrap
    area = [box[0], box[1] + cap_h, box[2], box[3] - cap_h]
    ncol = max(len(r) for r in rows)
    pad = 0.15 * 72
    chosen = None
    for size in [v for v in ctx.scale["table"] if len(rows) <= 8 or v < 24]:
        nat = [max(K.text_em(str(r[c]) if c < len(r) else "") for r in rows) * size + pad + 4 for c in range(ncol)]
        avail = area[2] / EMU_PT
        if sum(nat) <= avail:
            widths = [w + (avail - sum(nat)) / ncol for w in nat]
        else:
            mn = [min(w, max(40.0, avail / ncol * 0.6)) for w in nat]
            extra = avail - sum(mn)
            want = [w - m for w, m in zip(nat, mn)]
            tot = sum(want) or 1
            widths = [m + max(0, extra) * wv / tot for m, wv in zip(mn, want)]
        heights = []
        for r in rows:
            lines = max(K.wrap_count(str(r[c]) if c < len(r) else "", widths[c] - pad, size) for c in range(ncol))
            heights.append(lines * size * 1.22 + 0.075 * 72 + 2)
        if sum(heights) <= area[3] / EMU_PT:
            chosen = (size, widths, heights)
            break
    fits = chosen is not None
    if not chosen:
        chosen = (size, widths, heights)
        rep["warnings"].append("FAIL table %s does not fit even at 10 pt: split it or show the paper table as an image" % (tb.get("ref") or tag))
    size, widths, heights = chosen
    if fits and sum(heights) < 0.72 * area[3] / EMU_PT:  # short table: taller rows so it fills the area
        k = min(1.6, 0.72 * area[3] / EMU_PT / sum(heights))
        heights = [h * k for h in heights]
    col_w = [int(w * EMU_PT) for w in widths]
    row_h = [int(h * EMU_PT) for h in heights]
    tbox = [area[0], area[1], sum(col_w), sum(row_h)]
    tbox[0] = area[0] + max(0, (area[2] - tbox[2]) // 2)
    s["table_name"] = "Table %d" % num
    be.table(s, tbox, rows, col_w, row_h, size, tb.get("header", True), tb.get("highlight") or [])
    if cap:
        be.textbox(s, [box[0], box[1], box[2], cap_h], [dict(text=cap, size=12, color="404040", bullet=False)], "Caption %d" % num)
    entry.update(font_pt=size, fits=fits, box_in=[K.to_in(v) for v in tbox])
    if size < 12:
        rep["warnings"].append("table %s uses %d pt (below 12 pt body minimum; allowed for tables, consider splitting)" % (tb.get("ref") or tag, size))
    rep["visuals"].append(entry)


def chart_from_raster(v):
    """a native chart carried as an image (to_spec: data_csv + chart_type) -> chart spec redrawn from its data"""
    ct = str(v.get("chart_type") or "")
    if not (v.get("data_csv") and os.path.exists(v["data_csv"]) and ct in ("barChart", "bar3DChart", "lineChart", "line3DChart")):
        return None
    try:
        import matplotlib  # noqa: F401
        with open(v["data_csv"], newline="", encoding="utf-8-sig") as f:
            head = next(csv.reader(f))
    except Exception:
        return None
    if len(head) < 2:
        return None
    return dict(csv=v["data_csv"], x=head[0], y=head[1:], kind="line" if ct.startswith("line") else "bar",
                ref=v.get("ref") or "", caption=v.get("caption") or "", caption_text=v.get("caption_text"),
                highlight=v.get("highlight"), labels=True, legend=len(head) > 2)


def place_visual(be, s, sl, key, box, ctx, rep, tag, num=1):
    v = sl.get(key) if isinstance(key, str) else key
    redraw = chart_from_raster(v) if isinstance(v, dict) and v.get("redraw", True) else None
    if redraw:  # same numbers, zero baseline, value labels, theme colours (the old raster may have a cut axis)
        rep["warnings"].append("%s: native %s from the source deck redrawn from its data %s (axis from 0, value labels)"
                               % (tag, v.get("chart_type"), os.path.basename(v["data_csv"])))
        place_chart(be, s, redraw, box, ctx, rep, tag, num)
        rep["visuals"][-1].update(redrawn_from=v.get("image"), origin=v.get("origin"))
        return
    if key == "figure" or (isinstance(v, dict) and (v.get("image") or v.get("pdf"))):
        place_figure(be, s, v, box, ctx, rep, tag, num)
    elif key == "chart" or (isinstance(v, dict) and v.get("csv") and v.get("y")):
        place_chart(be, s, v, box, ctx, rep, tag, num)
    else:
        place_table(be, s, v, box, ctx, rep, tag, num)


# ----------------------------------------------------------------------------- slides
_CJK = r"[⺀-鿿＀-￯]"


def title_lines(text, box, size):
    """estimated lines of a title at `size` in its placeholder (renderers add ~1/4 em between CJK and Latin)"""
    t = re.sub(r"\*\*", "", str(text))
    em = K.text_em(t)
    gaps = len(re.findall(r"(?<=%s)[A-Za-z0-9(]|[A-Za-z0-9)](?=%s)" % (_CJK, _CJK), t))
    w = (box[2] / EMU_PT - 14.4) * 0.96 * em / max(1e-6, em + 0.25 * gaps)
    return K.wrap_count(t, w, size)


def title_fits(text, box, size):
    """at most two lines and inside the title placeholder's height"""
    n = title_lines(text, box, size)
    return n <= 2 and n * size * (1.32 if K.has_cjk(text) else 1.2) + 7.2 <= box[3] / EMU_PT * 1.08


def even(v):
    return int(2 * round(v / 2.0))


def plan_titles(cat, spec, ctx):
    """ONE title size for all content slides: the template/theme title size; a smaller step for every slide only
    when some title needs more than two lines (ladder 1, 0.86, 0.74 x). Cover and section titles keep their layout."""
    entries = []
    for sl in spec.get("slides", []):
        if not sl.get("title"):
            continue
        pl = plan_layout(cat, sl, ctx)
        if pl["kind"] in ("title", "section"):
            continue
        tph = next((p for p in pl["lay"]["placeholders"] if p["type"] == "title"), None)
        if tph and tph.get("box"):
            entries.append((sl["title"], tph["box"], base_sizes(cat, pl["lay"], tph).get(1, 28.0)))
    if not entries:
        ctx.title_pt, ctx.title_ladder, ctx.title_misfits = None, [], []
        return
    bases = [e[2] for e in entries]
    base = max(set(bases), key=bases.count)
    ladder = []
    for f in (1.0, 0.86, 0.74):
        v = max(ctx.min_title_pt, even(base * f) if f < 1 else base)
        if v not in ladder:
            ladder.append(v)
    chosen = ladder[-1]
    for v in ladder:
        if all(title_fits(t, b, v) for t, b, _ in entries):
            chosen = v
            break
    ctx.title_pt, ctx.title_ladder = chosen, ladder
    ctx.title_misfits = [t[:60] for t, b, _ in entries if not title_fits(t, b, chosen)]


def put_text(be, s, cat, lay, ph, paras, name, ctx, rep, min_pt=None, anchor=None, bullets=True, ladder=None,
             deck_title=False):
    """text into a layout placeholder (inheritance kept). deck_title: the deck-wide title size (ctx.title_pt);
    ladder=start step: body text on the type scale (largest step from `start` that fits; the template's own size
    is inherited, not written). Anything else: template sizes, shrunk along the logged ladder only on overflow."""
    base = base_sizes(cat, lay, ph)
    box = ph["box"] or K.content_area(cat)
    if ph["type"] in ("title", "ctrTitle"):
        mn = max(18, ctx.min_title_pt)
        b1 = base.get(1, 28.0)
        t = getattr(ctx, "title_pt", None)
        if deck_title and t and len(paras) == 1:
            if title_fits(paras[0]["text"], box, t):
                sizes = None if abs(t - b1) < 0.5 else [t]
                if sizes:
                    rep["font_overrides"].append(dict(shape=name, template_pt=b1, used_pt=[t], why="deck title size"))
                be.ph_text(s, ph, apply_sizes(paras, sizes), name, anchor=anchor)
                rep["shapes"].append(name)
                rep["title_pt"] = t
                return
            base = dict(base, **{1: t})
            rep["warnings"].append("title needs more than 2 lines at the deck title size %d pt: shortened size used on "
                                   "this slide only (off the type scale) - shorten the title" % t)
        bpt = 0
    else:
        mn = min_pt or ctx.min_body_pt
        bpt = 22.0 if (bullets and ph["type"] in ("obj", "body")) else 0
        if ladder is not None and bullets and ph["type"] in ("obj", "body") and paras and any(p.get("text") for p in paras):
            b1 = base.get(1, 18.0)
            r = D.ladder_list(paras, box, ctx, bullet_pt=bpt, start=ladder, base=b1, min_pt=mn)
            used = sorted({p["size"] for p in r["paras"] if p.get("size")}, reverse=True)
            if used:
                rep["font_overrides"].append(dict(shape=name, template_pt=b1, used_pt=used,
                                                  why="off the type scale (overflow)" if r["off_ladder"] else "type scale step"))
            if r["off_ladder"]:
                rep["warnings"].append("%s: %d pt is below the type scale %s (needed to fit): shorten or split the slide"
                                       % (name, r["size"], ctx.scale["body"]))
            if not r["fits"]:
                rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt even at %d pt): shorten or split "
                                       "the slide" % (name, r["need"], r["avail"], r["size"]))
            be.ph_text(s, ph, r["paras"], name, anchor=anchor)
            rep["shapes"].append(name)
            rep["body_pt"] = r["size"]
            return
    sizes, fits, need, avail = fit_paras(paras, box, base, mn, bullet_pt=bpt)
    if sizes:
        rep["font_overrides"].append(dict(shape=name, template_pt=base.get(1), used_pt=sorted(set(sizes))))
    if not fits:
        rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt even at %d pt): shorten or split the slide"
                               % (name, need, avail, mn))
    be.ph_text(s, ph, apply_sizes(paras, sizes), name, anchor=anchor)
    rep["shapes"].append(name)


def put_box_text(be, s, box, paras, name, ctx, rep, base=18, bullets=True, color=None, ladder=None, anchor="t"):
    """text box (only where the layout has no placeholder for it); ladder=start step: sizes on the type scale"""
    ps = [dict(p, bullet=(p.get("bullet", bullets))) for p in paras]
    if ladder is not None:
        r = D.ladder_list(ps, box, ctx, bullet_pt=18.0 if bullets else 0, start=ladder, base=None, min_pt=ctx.min_body_pt)
        if r["off_ladder"]:
            rep["warnings"].append("%s: %d pt is below the type scale %s (needed to fit): shorten" % (name, r["size"], ctx.scale["body"]))
        if not r["fits"]:
            rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt): shorten or split" % (name, r["need"], r["avail"]))
        be.textbox(s, box, r["paras"], name, anchor=anchor)
        rep["shapes"].append(name)
        rep.setdefault("text_pt", []).append(r["size"])
        return
    sizes, fits, need, avail = fit_paras(ps, box, {1: base, 2: base - 2, 3: base - 3}, ctx.min_body_pt,
                                         bullet_pt=18.0 if bullets else 0)
    sizes = sizes or [max(ctx.min_body_pt, base - 2 * p.get("level", 0)) for p in ps]
    if not fits:
        rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt): shorten or split" % (name, need, avail))
    be.textbox(s, box, apply_sizes(ps, sizes), name, anchor=anchor)
    rep["shapes"].append(name)


def put_body(be, s, cat, lay, box, paras, name, ctx, rep, used, ladder=1, anchor="t", idx=0):
    """body text in the layout's body placeholder (moved to `box`, inheritance kept); a text box only when the
    layout has no free body placeholder"""
    bodies = [p for p in sorted(lay["placeholders"], key=lambda p: p["idx"]) if p["type"] in ("obj", "body")
              and p["idx"] not in used]
    if len(bodies) > idx:
        ph = dict(bodies[idx])
        ph["box"], ph["fixed_box"] = [int(v) for v in box], True
        used.add(ph["idx"])
        put_text(be, s, cat, lay, ph, paras, name, ctx, rep, anchor=anchor, ladder=ladder)
    else:
        put_box_text(be, s, box, paras, name, ctx, rep, ladder=ladder, anchor=anchor)


def choose_arrangement(sl, kind, box, bullets, ctx):
    """(arrange, visual_share) for a visual + text slide: the arrangement that gives the visual the larger area
    (side by side unless a wide figure gains clearly from full width); the visual gets >= 55 % of the width"""
    gap = int(0.25 * EMU_IN)
    W, H = box[2] / EMU_PT, box[3] / EMU_PT
    if kind == "table":
        rows = table_rows(sl.get("table") or {})
        ncol = max([len(r) for r in rows] or [0])
        short = sum(K.text_em(b["text"]) for b in bullets) <= 90
        return ("right" if ncol <= 3 and len(bullets) <= 4 and short else "below"), (0.6 if ncol <= 3 else 0.72)
    if kind == "chart":
        return "right", 0.62
    ar = visual_aspect(sl.get("figure") or {}, ctx)
    if not ar:
        return "right", 0.6
    cap_h = 0.45 * 72 if ((sl.get("figure") or {}).get("caption") or (sl.get("figure") or {}).get("ref")) else 0
    sz = ctx.scale["body"][-1]
    ps = D.list_paras(bullets, ctx, sz)  # same sizes and spacing rule the body text will get
    best = []
    for share in (0.55, 0.58, 0.62, 0.66, 0.7):
        tw = (W - gap / EMU_PT) * (1 - share)
        if K.text_height_pt(ps, tw, bullet_pt=22) > H * 0.95:
            continue
        vw, vh = (W - gap / EMU_PT) * share, H - cap_h
        sc = min(vw / ar, vh)
        best.append((sc * sc * ar, "right", share))
    th = K.text_height_pt(ps, W, bullet_pt=22) + 10
    vh = H - th - 0.15 * 72 - cap_h
    if vh > 0:
        sc = min(W / ar, vh)
        best.append((sc * sc * ar / 1.15, "below", round(1 - (th + 0.15 * 72) / H, 3)))
    if not best:
        return "right", 0.55
    area, arrange, share = max(best)
    if arrange == "right":  # smallest share that gives the visual (almost) its largest area; text gets the rest
        share = min(sh for a_, ar_, sh in best if ar_ == "right" and a_ >= area * 0.98)
    return arrange, share


DESIGNS = ("cards", "bignumber", "statement", "quote", "agenda")


def plan_layout(cat, sl, ctx):
    """kind, design, bullets, takeaway and template layout of a slide (shared by the title pre-pass and the build)"""
    kind = slide_kind(sl)
    bullets = norm_bullets(sl.get("bullets"))
    tk = K.takeaway_text(sl)
    if tk:  # a 'Takeaway: ...' bullet moves into the bar
        bullets = [b for b in bullets if not (b["text"].strip() == tk and K.TAKEAWAY_PREFIX.match(b["text"]))]
    closing = kind == "bullets" and not bullets and ctx.designed and not sl.get("layout") and \
        str(sl.get("role", "")).lower() in ("qa", "questions", "thanks", "closing", "end")
    if closing:  # a bare "Questions?" / "Thank you" slide gets the divider look, without a number
        kind = "section"
    design = None
    if kind == "bullets":
        design = D.choose(sl, bullets, ctx, tk)
    elif kind in DESIGNS:
        design = kind
        if not ctx.designed and kind == "agenda":  # template: the agenda stays a list in the body placeholder
            design, kind = None, "bullets"
    want = KIND_TO_LAYOUT.get(kind, "content")
    if design in DESIGNS:
        want = "title_only"
    elif design == "list2":
        want = "two"
    elif kind in ("figure", "chart", "table", "figures") and bullets:
        want = "content"  # the text goes into the body placeholder next to / under the visual
    lay, how = None, None
    if sl.get("layout_name") is not None:
        lay = K.find_layout(cat, sl["layout_name"])
        how = "by_name" if lay else None
    if lay is None:
        lay, how = K.pick_layout(cat, want, ctx.master)
        if design == "list2" and how != "exact":
            design = "list" if ctx.designed else None
            lay, how = K.pick_layout(cat, "content", ctx.master)
    if not ctx.designed and design in DESIGNS and any(p["type"] in ("obj", "body") for p in lay["placeholders"]):
        design, kind = None, "bullets"  # template: no free text where the layout has a body placeholder
        lay, how = K.pick_layout(cat, "content", ctx.master)
    return dict(kind=kind, design=design, bullets=bullets, tk=tk, closing=closing, lay=lay, how=how)


def build_slide(be, cat, sl, no, ctx, facts):
    pl = plan_layout(cat, sl, ctx)
    kind, design, bullets, tk, closing, lay, how = (pl[k] for k in ("kind", "design", "bullets", "tk", "closing", "lay", "how"))
    rep = dict(index=no, id=sl.get("id", "S%d" % no), role=sl.get("role"), kind=kind, layout=lay["name"], layout_how=how,
               shapes=[], font_overrides=[], visuals=[], warnings=[])
    if design:
        rep["design"] = dict(layout=design)
    if how and how.startswith("fallback"):
        rep["warnings"].append("layout %s (template has no exact layout for '%s')" % (how, kind))
    s = be.new_slide(lay)
    phs = lay["placeholders"]
    title_ph = next((p for p in phs if p["type"] in ("title", "ctrTitle")), None)
    bodies = sorted([p for p in phs if p["type"] in ("obj", "body", "subTitle")], key=lambda p: p["idx"])
    used = set()
    content = list(ctx.content)
    tag = re.sub(r"[^A-Za-z0-9_-]", "_", str(rep["id"]))
    refs = source_refs(sl, facts)

    # title
    if sl.get("title"):
        tparas = [dict(text=sl["title"], bullet=False)]
        if title_ph:
            put_text(be, s, cat, lay, title_ph, tparas, "Title", ctx, rep, deck_title=kind not in ("title", "section"))
            if kind not in ("title", "section") and title_ph["box"]:
                content[1] = max(content[1], title_ph["box"][1] + title_ph["box"][3] + int(0.12 * EMU_IN))
        else:
            tb = [content[0], int(0.3 * EMU_IN), content[2], max(int(0.8 * EMU_IN), content[1] - int(0.45 * EMU_IN))]
            put_box_text(be, s, tb, [dict(text=sl["title"], bold=True, bullet=False)], "Title", ctx, rep, base=28, bullets=False)
            rep["warnings"].append("layout has no title placeholder: title drawn as text box")
    content[3] = ctx.content[1] + ctx.content[3] - content[1]
    note_text = (sl.get("source_note") or (ctx.lab["source"] + "; ".join(refs))) if (ctx.source_notes and (refs or sl.get("source_note"))) else None

    # a figure-only slide uses the full height down to the footer; a tall figure gets its caption + source beside it
    fig_only = kind == "figure" and not bullets and isinstance(sl.get("figure"), dict) and not chart_from_raster(sl["figure"])
    side = None
    if fig_only:
        content[3] = max(content[3], ctx.footer_top - int(0.1 * EMU_IN) - content[1])
        ar = visual_aspect(sl["figure"], ctx)
        if ar and ar < (content[2] / float(content[3])) * 0.72:
            side = (min(int(3.0 * EMU_IN), int(content[2] * 0.26)), note_text)

    # figure + text side by side: the figure also uses the full height, the source note sits under the text column
    arr, col_note = None, False
    if kind in ("figure", "chart", "table") and bullets:
        ext = list(content)
        if kind == "figure" and not (tk and ctx.designed):
            ext[3] = max(content[3], ctx.footer_top - int(0.1 * EMU_IN) - content[1])
        arrange, share = sl.get("arrange"), sl.get("visual_share")
        if not arrange:
            arrange, share0 = choose_arrangement(sl, kind, ext, bullets, ctx)
            share = share or share0
        arr = (arrange, share)
        if arrange != "below" and kind == "figure" and not (tk and ctx.designed):
            content, col_note = ext, True

    # source note reserve
    note_box = None
    if note_text and kind not in ("title", "section") and not side and not col_note:
        nh = int(0.3 * EMU_IN)
        note_box = [content[0], content[1] + content[3] - nh, content[2], nh]
        content[3] -= nh + int(0.06 * EMU_IN)
    # takeaway: default theme = accent bar above the source note; template = last paragraph of the body placeholder
    tk_box, tk_para = None, None
    if tk and kind not in ("title", "section"):
        if ctx.designed:
            th, tk_size = D.takeaway_height(tk, content[2], ctx)
            tk_box = [content[0], content[1] + content[3] - th, content[2], th]
            content[3] -= th + int(0.18 * EMU_IN)
        else:
            tk_para = dict(text=tk, bold=True, bullet=False, color="accent1", level=0)

    gs = D.groups_of(bullets)
    if kind == "title":
        sub = sl.get("subtitle") or []
        sub = sub if isinstance(sub, list) else [sub]
        L = ctx.scale["body"]
        if ctx.designed and sub:  # authors first (dark, bold), venue / date below (grey) - both on the type scale
            sparas = [dict(text=str(sub[0]), bullet=False, size=L[1], bold=True, color="tx2")] + \
                     [dict(text=str(t), bullet=False, size=L[-1], color=D.NEUTRAL_TEXT, spc=6) for t in sub[1:]]
        else:
            sparas = [dict(text=str(t), bullet=False) for t in sub]
        if sub and bodies:
            put_text(be, s, cat, lay, bodies[0], sparas, "Subtitle", ctx, rep, bullets=False)
        elif sub:
            put_box_text(be, s, [content[0], content[1] + content[3] // 2, content[2], content[3] // 2],
                         sparas, "Subtitle", ctx, rep, base=20, bullets=False)
    elif kind == "section":
        ctx.section_no += 0 if closing else 1
        sub = sl.get("subtitle")
        if sub and bodies:
            put_text(be, s, cat, lay, bodies[0], [dict(text=str(sub), bullet=False)], "Subtitle", ctx, rep, bullets=False)
        if ctx.designed and sl.get("number", True) is not False and not closing:
            D.draw_section_number(be, s, int(sl.get("number") or ctx.section_no), title_ph and title_ph["box"], ctx, rep)
    elif design in ("cards", "bignumber", "statement"):
        if not gs:
            rep["warnings"].append("FAIL %s layout needs bullets" % design)
        elif design == "statement" or len(gs) == 1:
            D.draw_statement(be, s, gs[0], content, ctx, rep, role=None if design == "bignumber" else sl.get("role", ""))
        elif len(gs) <= 4:
            D.draw_cards(be, s, gs, content, ctx, rep, numbers=True, force_numbers=design == "bignumber", role=sl.get("role", ""))
        else:  # 5-8 cards: two rows
            per = (len(gs) + 1) // 2
            gap = int(0.25 * EMU_IN)
            hh = (content[3] - gap) // 2
            D.draw_cards(be, s, gs[:per], [content[0], content[1], content[2], hh], ctx, rep, role=sl.get("role", ""))
            D.draw_cards(be, s, gs[per:], [content[0], content[1] + hh + gap, content[2], hh], ctx, rep, role=sl.get("role", ""))
    elif design == "agenda":
        D.draw_agenda(be, s, gs, content, ctx, rep)
    elif design == "quote":
        q = sl.get("quote") or (bullets[0]["text"] if bullets else "")
        D.draw_quote(be, s, q, sl.get("quote_src") or "", content, ctx, rep)
    elif design == "list2":
        two = [p for p in bodies if p["type"] in ("obj", "body")][:2]
        half = (sum(1 + len(g["subs"]) for g in gs) + 1) // 2
        left, cnt = [], 0
        for g in gs:
            if cnt >= half and left:
                break
            left.append(g)
            cnt += 1 + len(g["subs"])
        cols = [left, gs[len(left):]]
        boxes = [p["box"] for p in two] if len(two) == 2 and all(p["box"] for p in two) else list(split_h(content, 0.5))
        boxes = [[b[0], b[1], b[2], min(b[3], content[1] + content[3] - b[1])] for b in boxes]
        cparas = []
        for col in cols:
            paras = []
            for g in col:
                paras.append(dict(g["src"]) if isinstance(g.get("src"), dict) else dict(text=g["text"], level=0))
                paras += [dict(text=t, level=1) for t in g["subs"]]
            cparas.append(paras)
        if tk_para:
            cparas[1].append(dict(tk_para))
            tk_para = None
        L = ctx.scale["body"]
        steps = [L.index(D.ladder_list(ps, boxes[ci], ctx, bullet_pt=22.0)["size"]) if
                 D.ladder_list(ps, boxes[ci], ctx, bullet_pt=22.0)["size"] in L else len(L) - 1
                 for ci, ps in enumerate(cparas) if ps]
        start = max(steps) if steps else 0  # both columns on the same step
        for ci, paras in enumerate(cparas):
            ph = dict(two[ci]) if len(two) == 2 else None
            if ph:
                ph["fixed_box"] = ph.get("fixed_box") or ph["box"] != boxes[ci]
                ph["box"] = boxes[ci]
                put_text(be, s, cat, lay, ph, paras, ("Left" if ci == 0 else "Right") + " Body", ctx, rep, ladder=start)
            else:
                put_box_text(be, s, boxes[ci], paras, ("Left" if ci == 0 else "Right") + " Body", ctx, rep, ladder=start)
    elif kind == "bullets":
        paras = (bullets or [dict(text="", bullet=False)]) + ([tk_para] if tk_para else [])
        tk_para = None
        if bodies:
            ph = dict(bodies[0])
            if ph["box"] and (note_box or tk_box) and ph["box"][1] + ph["box"][3] > content[1] + content[3]:
                ph["box"] = [ph["box"][0], ph["box"][1], ph["box"][2], content[1] + content[3] - ph["box"][1]]
                ph["fixed_box"] = True
            put_text(be, s, cat, lay, ph, paras, "Body", ctx, rep, ladder=0)
        else:
            put_box_text(be, s, content, paras, "Body", ctx, rep, base=20, ladder=0)
    elif kind == "two_column":
        cols = [sl.get("left") or {}, sl.get("right") or {}]
        two = [p for p in bodies if p["type"] in ("obj", "body")][:2]
        vis = [next((k for k in ("figure", "chart", "table") if c.get(k)), None) for c in cols]
        if bool(vis[0]) != bool(vis[1]):  # one visual column: it gets 58 % of the width
            boxes = list(split_h(content, 0.58 if vis[0] else 0.42))
        else:
            boxes = [p["box"] for p in two] if len(two) == 2 and all(p["box"] for p in two) else list(split_h(content, 0.5))
        boxes = [[b[0], b[1], b[2], min(b[3], content[1] + content[3] - b[1])] for b in boxes]
        if ctx.designed and not any(vis) and len(two) == 2:  # comparison look: a light panel behind each column
            pad = int(0.18 * EMU_IN)
            for ci, b in enumerate(boxes):
                D.deco(be, s, b, "Column Panel %d" % (ci + 1), D.CARD_FILL, back=True)
                D.deco(be, s, [b[0], b[1], b[2], int(0.07 * EMU_IN)], "Column Accent %d" % (ci + 1), "accent1")
            boxes = [[b[0] + pad, b[1] + pad, b[2] - 2 * pad, b[3] - 2 * pad] for b in boxes]
        cparas = []
        for col in cols:
            paras = []
            if col.get("heading"):
                paras.append(dict(text=col["heading"], bold=True, bullet=False, color="accent1"))
            paras += norm_bullets(col.get("bullets"))
            cparas.append(paras)
        L = ctx.scale["body"]
        start = 0
        if not any(vis):  # two text columns: both on the same ladder step
            sizes = [D.ladder_list(ps, boxes[ci], ctx, bullet_pt=22.0)["size"] for ci, ps in enumerate(cparas) if ps]
            start = max([L.index(v) if v in L else len(L) - 1 for v in sizes] or [0])
        for ci, col in enumerate(cols):
            name = ("Left" if ci == 0 else "Right")
            paras = cparas[ci]
            vis_key = vis[ci]
            if vis_key:
                vb = boxes[ci]
                if paras:
                    hb, vb = split_v(vb, 0.28 if len(paras) <= 2 else 0.4)
                    if len(two) == 2:
                        ph = dict(two[ci])
                        ph["box"], ph["fixed_box"] = hb, True
                        put_text(be, s, cat, lay, ph, paras, name + " Text", ctx, rep, ladder=1)
                    else:
                        put_box_text(be, s, hb, paras, name + " Text", ctx, rep, ladder=1)
                place_visual(be, s, col, vis_key, vb, ctx, rep, tag + "_" + name.lower(), ci + 1)
            elif paras:
                if len(two) == 2:
                    ph = dict(two[ci])
                    ph["fixed_box"] = ph.get("fixed_box") or ph["box"] != boxes[ci]
                    ph["box"] = boxes[ci]
                    put_text(be, s, cat, lay, ph, paras, name + " Body", ctx, rep, ladder=start if not any(vis) else 1,
                             anchor="ctr" if any(vis) else None)
                else:
                    put_box_text(be, s, boxes[ci], paras, name + " Body", ctx, rep, ladder=start if not any(vis) else 1,
                                 anchor="ctr" if any(vis) else "t")
    elif kind in ("figure", "chart", "table"):
        if bullets:
            arrange, share = arr
            if arrange == "below":
                vb, tb = split_v(content, float(share or 0.72))
            else:
                vb, tb = split_h(content, float(share or 0.6))
            if col_note and note_text:
                nh = max(int(0.3 * EMU_IN), int((K.wrap_count(note_text, tb[2] / EMU_PT - 14.4, ctx.scale["note"]) *
                                                 ctx.scale["note"] * 1.25 + 9) * EMU_PT))
                note_box = [tb[0], tb[1] + tb[3] - nh, tb[2], nh]
                tb = [tb[0], tb[1], tb[2], tb[3] - nh - int(0.06 * EMU_IN)]
            place_visual(be, s, sl, kind, vb, ctx, rep, tag)
            paras = bullets + ([tk_para] if tk_para else [])
            tk_para = None
            put_body(be, s, cat, lay, tb, paras, "Body", ctx, rep, used, ladder=1, anchor="ctr" if arrange != "below" else "t")
            rep["arrange"] = [arrange, share]
        elif side:
            place_figure(be, s, sl["figure"], content, ctx, rep, tag, 1, side=side)
        else:
            place_visual(be, s, sl, kind, content, ctx, rep, tag)
    elif kind == "figures":
        figs = sl.get("figures") or []
        area = content
        if bullets:
            area, tb = split_v(content, float(sl.get("visual_share", 0.75)))
            paras = bullets + ([tk_para] if tk_para else [])
            tk_para = None
            put_body(be, s, cat, lay, tb, paras, "Body", ctx, rep, used, ladder=1)
        n = len(figs)
        cols = n if n <= 3 else 2
        rows_n = (n + cols - 1) // cols
        gap = int(0.2 * EMU_IN)
        cw = (area[2] - gap * (cols - 1)) // max(1, cols)
        rh = (area[3] - gap * (rows_n - 1)) // max(1, rows_n)
        for i, fg in enumerate(figs):
            r, c = divmod(i, cols)
            cell = [area[0] + c * (cw + gap), area[1] + r * (rh + gap), cw, rh]
            place_visual(be, s, fg, fg, cell, ctx, rep, "%s_%d" % (tag, i + 1), i + 1)
    if tk_box:
        D.draw_takeaway(be, s, tk, tk_box, tk_size, ctx, rep)
    elif tk_para:
        rep["warnings"].append("takeaway kept in the notes only: this template slide has no body text placeholder for it")
    if note_box:
        be.textbox(s, note_box, [dict(text=note_text, size=ctx.scale["note"], color="7F7F7F", bullet=False,
                                      align="right")], "Source Note", anchor="b")
        rep["shapes"].append("Source Note")
    # footer and slide number from the layout's placeholders
    if not (kind == "title" and not ctx.footer_on_title):
        for p in phs:
            if p["type"] == "ftr" and ctx.footer_text:
                be.ph_text(s, p, [dict(text=ctx.footer_text, bullet=False)], "Footer")
            elif p["type"] == "sldNum" and ctx.slide_numbers:
                be.ph_text(s, p, [dict(text=str(no), field="slidenum", bullet=False)], "Slide Number")
        if ctx.slide_numbers and not any(p["type"] == "sldNum" for p in phs):
            rep["warnings"].append("layout has no slide-number placeholder; number omitted")
    notes = compose_notes(sl, ctx.lab, refs)
    if getattr(ctx, "qa_cue_slide", None) == no and getattr(ctx, "qa_cues", None):
        notes = (notes + "\n\n" if notes else "") + qa_cue_text(ctx.qa_cues, ctx.lab)
    be.notes(s, notes)
    rep["notes_chars"] = len(notes)
    rep["sources"] = refs
    be.finish(s)
    return rep


def method_name(*titles):
    """'GateLite: Sparse ...' -> GateLite; 'GateLite (example)' -> GateLite; '... Networks (EGNN)' -> EGNN; else ''"""
    ts = [str(t or "") for t in titles if t]
    for t in ts:
        m = re.match(r"^\s*([A-Z][\w\-+]{2,24})\s*[:：]", t)
        if m:
            return m.group(1)
    for t in ts:
        m = re.match(r"^\s*([A-Z][a-z0-9]+[A-Z][\w\-+]*|[A-Z][A-Z0-9\-+]{2,12})\b", t)
        if m and m.group(1).upper() not in ("THE", "AN", "ON", "FOR", "TOWARDS"):
            return m.group(1)
    for t in ts:
        m = re.search(r"[(（]\s*([A-Z][A-Za-z0-9\-+]{1,15})\s*[)）]", t)
        if m and not m.group(1).isupper() or (m and len(m.group(1)) <= 8):
            return m.group(1)
    return ""


def make_ctx(cat, spec, outdir, generated=False):
    """layout context shared by deck_build and deck_edit add_slide. generated=True: the built-in academic template
    is used, so the full layout engine (cards, callouts, agenda, takeaway bars) applies; with a supplied template or
    an existing deck only fit/fill sizing and explicitly requested slide types (meta.design true/false overrides)."""
    meta = spec.get("meta", {})
    th = cat["masters"][0]["theme"] if cat["masters"] else {}
    ctx = Ctx()
    ctx.master = 0
    ctx.content = K.content_area(cat)
    if meta.get("math_markup") is False:  # literal ^ and _ (template adaptation keeps text exactly)
        K.MATH_MARKUP = False
    ctx.lang = meta.get("lang") or ("zh" if any(K.has_cjk(sl.get("title", "")) for sl in spec.get("slides", [])) else "en")
    ctx.lab = LABELS.get(ctx.lang, LABELS["en"])
    ctx.min_body_pt = float(meta.get("min_body_pt", 12))
    ctx.min_title_pt = float(meta.get("min_title_pt", 20))
    ctx.source_notes = meta.get("source_notes", True)
    foot = spec.get("footer") or {}
    ctx.footer_text = foot.get("text", "")
    ctx.slide_numbers = foot.get("slide_numbers", True)
    ctx.footer_on_title = foot.get("on_title", False)
    ctx.fonts = {"latin": (th.get("fonts") or {}).get("minor_latin") or "Arial", "ea": (th.get("fonts") or {}).get("minor_ea") or ""}
    ctx.colors = th.get("colors") or {}
    design = meta.get("design", "auto")
    ctx.designed = bool(generated) if design in ("auto", None) else bool(design)
    ctx.has_two = any("two" in l["kinds"] for l in cat["layouts"] if l["master"] == 0)
    ctx.section_no = 0
    ctx.method_name = meta.get("method") or method_name(next(
        (sl.get("title", "") for sl in spec.get("slides", []) if sl.get("role") == "title"), ""), meta.get("title"))
    ctx.fig_dir = os.path.join(outdir, "figures")
    ctx.chart_dir = os.path.join(outdir, "charts")
    os.makedirs(ctx.fig_dir, exist_ok=True)
    os.makedirs(ctx.chart_dir, exist_ok=True)
    ctx.slide_w, ctx.slide_h = cat["slide_w"], cat["slide_h"]
    clay, _ = K.pick_layout(cat, "content", 0)
    bph = next((p for p in (clay or {}).get("placeholders", []) if p["type"] in ("obj", "body")), None)
    bsz = base_sizes(cat, clay, bph) if (clay and bph) else {}
    ctx.inherited_sub = bsz.get(2)
    ctx.scale = D.make_scale(ctx.designed, bsz.get(1, 24.0), min_body=ctx.min_body_pt)
    ctx.scale["body_base_inherited"] = None if ctx.designed else bsz.get(1)
    feet = [p["box"][1] for p in (clay or {}).get("placeholders", []) if p["type"] in ("ftr", "sldNum", "dt") and p.get("box")
            and p["box"][1] > ctx.content[1] + ctx.content[3] // 2]
    ctx.footer_top = min(feet) if feet else min(cat["slide_h"] - int(0.4 * EMU_IN), ctx.content[1] + ctx.content[3] + int(0.25 * EMU_IN))
    plan_titles(cat, spec, ctx)
    ctx.scale["title_pt"] = ctx.title_pt
    return ctx


def type_scale_record(ctx, reports, cat):
    """type_scale.json: the deck's size and spacing ladder + what each slide used (explainable sizing)"""
    inh = set()
    for m in cat["masters"]:
        for st in (m.get("styles") or {}).values():
            inh.update(st.values())
        for q in m.get("placeholders", []):
            inh.update((q.get("lst_sizes") or {}).values())
    for l in cat["layouts"]:
        for q in l["placeholders"]:
            inh.update((q.get("lst_sizes") or {}).values())
    sc = ctx.scale
    return dict(
        mode="default theme" if ctx.designed else "template (sizes inherited where they fit)",
        title=dict(size_pt=ctx.title_pt, ladder=ctx.title_ladder, misfits=ctx.title_misfits,
                   rule="one size for every content-slide title: the template/theme title size; one ladder step smaller "
                        "for all slides only when some title needs more than two lines; cover and section titles keep "
                        "their layout size"),
        body=dict(ladder=sc["body"], sub_level="one step below its parent (the last step stays)",
                  rule="largest step (from the top; text next to a visual from the 2nd) at which the slide's text fits; "
                       "a slide that needs smaller text takes the next step; below the ladder only to avoid overflow "
                       "(warned as off-scale)"),
        spacing=sc["spacing"] if ctx.designed else "inherited from the template body style",
        spacing_rule="space before a top-level paragraph = %s x size, before a sub-paragraph = %s x size; line spacing "
                     "100 %%" % (D.SPACING["level0"], D.SPACING["sub"]) if ctx.designed else "template paragraph spacing",
        display=sc["display"], label=sc["label"], caption=[sc["caption"], sc["note"]], source_note=sc["note"],
        table=sc["table"], inherited=sorted(inh),
        slides=[dict(slide=r["index"], title_pt=r.get("title_pt"), body_pt=r.get("body_pt"),
                     off_scale=[w for w in r["warnings"] if "type scale" in w or "deck title size" in w])
                for r in reports])


def outline_md(spec, reports, facts, path):
    L = ["# Slide outline (generated from the deck spec)", ""]
    meta = spec.get("meta", {})
    for k in ("title", "audience", "duration_min", "goal"):
        if meta.get(k):
            L.append("- %s: %s" % (k, meta[k]))
    L.append("")
    for sl, r in zip(spec["slides"], reports):
        L.append("## %d. %s  `[%s | %s | %s]`" % (r["index"], sl.get("title", ""), r["id"], r.get("role") or "-", r["layout"]))
        for b in norm_bullets(sl.get("bullets")):
            L.append("%s- %s" % ("  " * b["level"], b["text"]))
        for v in r["visuals"]:
            L.append("- [%s] %s %s" % (v["type"], v.get("ref") or "", v.get("image") or v.get("csv") or ""))
        if sl.get("notes"):
            L.append("> notes: " + str(sl["notes"]).replace("\n", " "))
        if r.get("sources"):
            L.append("> sources: " + "; ".join(r["sources"]))
        L.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", required=True)
    ap.add_argument("--facts", default=None)
    ap.add_argument("--template", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    ap.add_argument("--backend", default="auto", choices=["auto", "pptx", "xml"])
    ap.add_argument("--keep-template-slides", action="store_true")
    ap.add_argument("--plan", default=None, help="talk_plan.json: its Q&A goes into the notes of the Q&A / last main slide")
    a = ap.parse_args()
    spec = load_specs(a.spec)
    facts = load_facts(a.facts)
    meta = spec.get("meta", {})
    outdir = os.path.dirname(os.path.abspath(a.out))
    os.makedirs(outdir, exist_ok=True)
    tpl_in = a.template or (spec.get("template") or {}).get("path")
    tinfo = {}
    work = tempfile.mkdtemp(prefix="deck_build_")
    fonts = {}
    if tpl_in:
        if not os.path.exists(tpl_in):
            print("FAIL template not found: %s" % tpl_in)
            return 2
        tpl = os.path.join(work, "template.pptx")
        changed = K.normalize_template(tpl_in, tpl)
        tinfo = dict(path=tpl_in, sha256=K.sha256(tpl_in), generated=False, converted_from_template_type=changed)
    else:
        pf = K.pick_fonts()
        th = spec.get("theme", {})
        fonts = {"latin": (th.get("fonts") or {}).get("latin") or pf["latin"], "ea": (th.get("fonts") or {}).get("ea") or pf["ea"]}
        tpl = os.path.join(outdir, "academic_default_template.pptx")
        K.make_default_template(tpl, fonts, th.get("colors"))
        tinfo = dict(path=tpl, sha256=K.sha256(tpl), generated=True, font_notes=pf["notes"])
    be, reason = make_backend(a.backend, tpl, a.keep_template_slides or bool((spec.get("template") or {}).get("keep_slides")))
    cat = be.cat
    th = cat["masters"][0]["theme"] if cat["masters"] else {}
    tinfo.update(fonts=th.get("fonts"), colors=th.get("colors"),
                 layouts=[dict(index=l["gindex"], name=l["name"], kinds=l["kinds"]) for l in cat["layouts"]],
                 slide_size_in=[K.to_in(cat["slide_w"]), K.to_in(cat["slide_h"])])
    ctx = make_ctx(cat, spec, outdir, generated=not tpl_in)
    lang = ctx.lang
    plan = load_json(a.plan) if a.plan and os.path.exists(a.plan) else (spec.get("plan") or {})
    ctx.qa_cues = [q for q in (plan.get("qa") or meta.get("qa") or []) if isinstance(q, dict) and q.get("question")]
    ctx.qa_cue_slide = qa_cue_target(spec["slides"]) if ctx.qa_cues else None
    def do_build(backend):
        ctx.section_no = 0
        reps = []
        for i, sl in enumerate(spec["slides"], 1):
            reps.append(build_slide(backend, backend.cat, sl, i, ctx, facts))
        backend.save(a.out)
        return reps
    try:
        reports = do_build(be)
    except Exception as e:
        if be.name == "python-pptx":
            import traceback
            reason = "python-pptx build failed (%r); rebuilt with stdlib writer. %s" % (e, traceback.format_exc(limit=2)[-300:])
            be = XmlBackend(tpl, a.keep_template_slides)
            reports = do_build(be)
        else:
            raise
    warnings = [w for r in reports for w in r["warnings"]]
    fails = [w for w in warnings if w.startswith("FAIL")]
    report = dict(status="needs_fix" if fails else "ok", backend=be.name, backend_version=getattr(be, "version", None),
                  backend_note=reason, pptx=os.path.abspath(a.out), pptx_sha256=K.sha256(a.out), n_slides=len(reports),
                  template=tinfo, min_body_pt=ctx.min_body_pt, lang=lang, slides=reports,
                  font_overrides=sum(len(r["font_overrides"]) for r in reports), n_fail=len(fails), n_warn=len(warnings) - len(fails))
    tsp = os.path.join(outdir, "type_scale.json")
    with open(tsp, "w", encoding="utf-8") as f:
        json.dump(type_scale_record(ctx, reports, be.cat), f, ensure_ascii=False, indent=1)
    report["type_scale"] = dict(path=tsp, title_pt=ctx.title_pt, body=ctx.scale["body"])
    rp = a.report or os.path.join(outdir, "build_report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    outline_md(spec, reports, facts, os.path.join(outdir, "slides_outline.md"))
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(dict(status=report["status"], backend=be.name, note=reason, slides=len(reports), fails=len(fails),
                          warns=len(warnings) - len(fails), pptx=a.out, report=rp), ensure_ascii=False))
    for w in warnings[:25]:
        print("  " + w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
