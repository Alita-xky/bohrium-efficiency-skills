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

EMU_IN, EMU_PT = K.EMU_IN, K.EMU_PT
KIND_TO_LAYOUT = {"title": "title", "section": "section", "bullets": "content", "two_column": "two",
                  "figure": "title_only", "figures": "title_only", "chart": "title_only", "table": "title_only",
                  "blank": "blank"}
LABELS = {"en": dict(source="Source: ", time="Time", transition="Transition", key="Key message", evidence="Evidence",
                     ifasked="If asked", missing="Figure not available"),
          "zh": dict(source="来源：", time="时长", transition="过渡", key="核心信息", evidence="证据", ifasked="若被追问",
                     missing="图像不可用")}


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
                "conclusion": "bullets", "closing": "bullets", "comparison": "two_column"}.get(k, k)
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
        s["shapes"].append(K.ph_sp_xml(self._id(s), name, ph["attrs"], paras, anchor=anchor))

    def textbox(self, s, box, paras, name, anchor="t", fill=None, line=None):
        s["shapes"].append(K.textbox_xml(self._id(s), name, box, paras, anchor, fill, line))

    def picture(self, s, path, box, name, descr=""):
        rid = "rId%d" % (len(s["media"]) + 2)
        s["media"].append((rid, path))
        s["shapes"].append(K.pic_xml(self._id(s), name, rid, box, descr))

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
            self._append(s, K.ph_sp_xml(2, name, ph["attrs"], paras, anchor=anchor))
            return
        el = target._element
        target.name = name
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

    def picture(self, s, path, box, name, descr=""):
        from pptx.util import Emu
        pic = s["slide"].shapes.add_picture(path, Emu(int(box[0])), Emu(int(box[1])), Emu(int(box[2])), Emu(int(box[3])))
        pic.name = name
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
def render_chart(ch, box, out_png, ctx):
    """matplotlib chart sized to the slide box so 14 pt in the figure is 14 pt on the slide"""
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
    plt.rcParams.update({"font.size": 14, "font.family": "sans-serif",
                         "font.sans-serif": [f for f in fams if f] + list(plt.rcParams["font.sans-serif"]),
                         "axes.unicode_minus": False})
    fig, ax = plt.subplots(figsize=(w_in, h_in), dpi=200)
    pal = ["#" + ctx.colors.get("accent%d" % i, K.DEFAULT_COLORS["accent%d" % i]) for i in range(1, 7)]
    kind = ch.get("kind", "bar")
    hl = set(str(x) for x in (ch.get("highlight") or []))
    n = len(ycols)
    xs = list(range(len(rows)))
    for j, (yc, ec) in enumerate(zip(ycols, errs)):
        ys = [num(r.get(yc)) for r in rows]
        es = [num(r.get(ec)) for r in rows] if ec else None
        lab = (ch.get("series_labels") or {}).get(yc, yc)
        if kind == "bar":
            wdt = 0.8 / n
            pos = [x - 0.4 + wdt * (j + 0.5) for x in xs]
            cols = [pal[(j + 1) % 6] if (hl and labels[i] in hl) else pal[j % 6] for i in range(len(xs))] if hl else pal[j % 6]
            bars = ax.bar(pos, ys, wdt * 0.92, yerr=es, capsize=4 if es else 0, color=cols, label=lab)
            if ch.get("labels"):
                for b, r in zip(bars, rows):
                    ax.annotate(str(r.get(yc)), (b.get_x() + b.get_width() / 2, b.get_height()), ha="center",
                                va="bottom", fontsize=12, xytext=(0, 3), textcoords="offset points")
        elif kind == "barh":
            hgt = 0.8 / n
            pos = [x - 0.4 + hgt * (j + 0.5) for x in xs]
            ax.barh(pos, ys, hgt * 0.92, xerr=es, capsize=4 if es else 0, color=pal[j % 6], label=lab)
        elif kind == "scatter":
            xv = [num(r.get(xcol)) for r in rows]
            ax.scatter(xv, ys, s=40, color=pal[j % 6], label=lab)
        else:
            xv = [num(r.get(xcol)) for r in rows] if ch.get("numeric_x") else xs
            ax.errorbar(xv, ys, yerr=es, marker="o", lw=2, ms=6, capsize=4 if es else 0, color=pal[j % 6], label=lab)
    if kind in ("bar", "line") and not ch.get("numeric_x"):
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=ch.get("rotate", 0), ha="right" if ch.get("rotate") else "center")
    if kind == "barh":
        ax.set_yticks(xs)
        ax.set_yticklabels(labels)
    if ch.get("xlabel"):
        ax.set_xlabel(ch["xlabel"])
    if ch.get("ylabel"):
        ax.set_ylabel(ch["ylabel"])
    if ch.get("ylim"):
        ax.set_ylim(*ch["ylim"])
    if ch.get("logy"):
        ax.set_yscale("log")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y" if kind != "barh" else "x", alpha=0.3)
    if n > 1 or ch.get("legend"):
        ax.legend(frameon=False, fontsize=13)
    fig.tight_layout()
    fig.savefig(out_png, dpi=200)
    plt.close(fig)
    with open(out_png[:-4] + ".json", "w", encoding="utf-8") as f:
        json.dump(dict(chart=ch, rows_used=len(rows), size_in=[round(w_in, 2), round(h_in, 2)]), f, ensure_ascii=False, indent=1)
    return out_png


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


def visual_aspect(fig, ctx):
    """width/height of a figure source (image pixels or pdf bbox), None if unknown"""
    if fig.get("image") and os.path.exists(fig["image"]):
        sz = K.image_size(fig["image"])
        return sz[0] / float(sz[1]) if sz and sz[1] else None
    bb = fig.get("bbox") or fig.get("crop")
    if fig.get("pdf") and bb:
        return (bb[2] - bb[0]) / max(1e-6, (bb[3] - bb[1])) * 0.773  # US letter page aspect; refined after crop
    return None


def place_figure(be, s, fig, box, ctx, rep, tag, num):
    """image (contain-fit) + caption under it; returns nothing, records in rep"""
    lab = ctx.lab
    cap = ""
    if fig.get("ref") or fig.get("caption"):
        cap = (fig.get("ref") or "")
        if fig.get("page"):
            cap += " (p.%s)" % fig["page"]
        if fig.get("caption"):
            cap = (cap + ": " if cap else "") + fig["caption"]
    if fig.get("caption_text"):
        cap = fig["caption_text"]
    cap_h = 0
    cap_size = 12
    if cap:
        for cap_size in (12, 11, 10):
            nl = K.wrap_count(cap, box[2] / EMU_PT - 14.4, cap_size)
            if nl <= 3:
                break
        cap_h = int((nl * cap_size * 1.25 + 8) * EMU_PT)
    img_area = [box[0], box[1], box[2], box[3] - cap_h - (int(0.06 * EMU_IN) if cap else 0)]
    path, how = resolve_image(fig, ctx, tag)
    entry = dict(type="figure", ref=fig.get("ref"), kind=fig.get("kind"), how=how)
    if path:
        sz = K.image_size(path) or (4, 3)
        ib = fit_contain(img_area, sz[0], sz[1])
        if cap:  # keep caption right under the picture
            total = ib[3] + cap_h + int(0.06 * EMU_IN)
            ib[1] = box[1] + max(0, (box[3] - total) // 2)
        descr = ((fig.get("ref") or "") + " " + (fig.get("caption") or "")).strip()[:900]
        be.picture(s, path, ib, "Figure %d" % num, descr)
        entry.update(image=path, pixels=list(sz), box_in=[K.to_in(v) for v in ib],
                     dpi_on_slide=round(sz[0] / max(0.01, ib[2] / EMU_IN)))
        if entry["dpi_on_slide"] < 96:
            rep["warnings"].append("%s: low resolution (%d px/in on slide)" % (fig.get("ref") or tag, entry["dpi_on_slide"]))
        cy = ib[1] + ib[3] + int(0.06 * EMU_IN)
    else:
        ph = [dict(text="%s: %s" % (lab["missing"], how), bullet=False, align="center", color="7F7F7F", size=14)]
        be.textbox(s, img_area, ph, "Figure %d (missing)" % num, anchor="ctr", line="BFBFBF")
        rep["warnings"].append("FAIL figure unavailable (%s): %s" % (fig.get("ref") or tag, how))
        entry["missing"] = True
        cy = img_area[1] + img_area[3] + int(0.06 * EMU_IN)
    if cap:
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
        render_chart(ch, area, out, ctx)
        be.picture(s, out, area, "Chart %d" % num, "chart from %s (%s vs %s)" % (os.path.basename(ch["csv"]), ch.get("y"), ch.get("x")))
        entry.update(png=out, spec=out[:-4] + ".json", box_in=[K.to_in(v) for v in area])
    except Exception as e:
        be.textbox(s, area, [dict(text="Chart failed: %r" % (e,), bullet=False, color="C00000", size=14)], "Chart %d (failed)" % num,
                   anchor="ctr", line="C00000")
        rep["warnings"].append("FAIL chart %s: %r" % (tag, e))
        entry["error"] = repr(e)
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
    cap_h = int((12 * 1.25 + 8) * EMU_PT) if cap else 0
    area = [box[0], box[1] + cap_h, box[2], box[3] - cap_h]
    ncol = max(len(r) for r in rows)
    pad = 0.15 * 72
    chosen = None
    for size in (18, 16, 14, 13, 12, 11, 10):
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


def place_visual(be, s, sl, key, box, ctx, rep, tag, num=1):
    v = sl.get(key) if isinstance(key, str) else key
    if key == "figure" or (isinstance(v, dict) and (v.get("image") or v.get("pdf"))):
        place_figure(be, s, v, box, ctx, rep, tag, num)
    elif key == "chart" or (isinstance(v, dict) and v.get("csv") and v.get("y")):
        place_chart(be, s, v, box, ctx, rep, tag, num)
    else:
        place_table(be, s, v, box, ctx, rep, tag, num)


# ----------------------------------------------------------------------------- slides
def put_text(be, s, cat, lay, ph, paras, name, ctx, rep, min_pt=None, anchor=None, bullets=True):
    base = base_sizes(cat, lay, ph)
    lf = None
    if ph["type"] in ("title", "ctrTitle"):
        mn = max(18, ctx.min_title_pt)
        bpt = 0
        lf = 1.12
    else:
        mn = min_pt or ctx.min_body_pt
        bpt = 22.0 if (bullets and ph["type"] in ("obj", "body")) else 0
    box = ph["box"] or K.content_area(cat)
    sizes, fits, need, avail = fit_paras(paras, box, base, mn, bullet_pt=bpt, line_factor=lf)
    if sizes:
        rep["font_overrides"].append(dict(shape=name, template_pt=base.get(1), used_pt=sorted(set(sizes))))
    if not fits:
        rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt even at %d pt): shorten or split the slide"
                               % (name, need, avail, mn))
    be.ph_text(s, ph, apply_sizes(paras, sizes), name, anchor=anchor)
    rep["shapes"].append(name)


def put_box_text(be, s, box, paras, name, ctx, rep, base=18, bullets=True, color=None):
    ps = [dict(p, bullet=(p.get("bullet", bullets))) for p in paras]
    sizes, fits, need, avail = fit_paras(ps, box, {1: base, 2: base - 2, 3: base - 3}, ctx.min_body_pt,
                                         bullet_pt=18.0 if bullets else 0)
    sizes = sizes or [max(ctx.min_body_pt, base - 2 * p.get("level", 0)) for p in ps]
    if not fits:
        rep["warnings"].append("FAIL text overflow in %s (needs %.0f pt of %.0f pt): shorten or split" % (name, need, avail))
    be.textbox(s, box, apply_sizes(ps, sizes), name)
    rep["shapes"].append(name)


def build_slide(be, cat, sl, no, ctx, facts):
    kind = slide_kind(sl)
    lay, how = None, None
    if sl.get("layout_name") is not None:
        lay = K.find_layout(cat, sl["layout_name"])
        how = "by_name" if lay else None
    if lay is None:
        lay, how = K.pick_layout(cat, KIND_TO_LAYOUT.get(kind, "content"), ctx.master)
    rep = dict(index=no, id=sl.get("id", "S%d" % no), role=sl.get("role"), kind=kind, layout=lay["name"], layout_how=how,
               shapes=[], font_overrides=[], visuals=[], warnings=[])
    if how and how.startswith("fallback"):
        rep["warnings"].append("layout %s (template has no exact layout for '%s')" % (how, kind))
    s = be.new_slide(lay)
    phs = lay["placeholders"]
    title_ph = next((p for p in phs if p["type"] in ("title", "ctrTitle")), None)
    bodies = sorted([p for p in phs if p["type"] in ("obj", "body", "subTitle")], key=lambda p: p["idx"])
    content = list(ctx.content)
    tag = re.sub(r"[^A-Za-z0-9_-]", "_", str(rep["id"]))
    refs = source_refs(sl, facts)

    # title
    if sl.get("title"):
        tparas = [dict(text=sl["title"], bullet=False)]
        if title_ph:
            put_text(be, s, cat, lay, title_ph, tparas, "Title", ctx, rep)
            if kind not in ("title", "section") and title_ph["box"]:
                content[1] = max(content[1], title_ph["box"][1] + title_ph["box"][3] + int(0.12 * EMU_IN))
        else:
            tb = [content[0], int(0.3 * EMU_IN), content[2], max(int(0.8 * EMU_IN), content[1] - int(0.45 * EMU_IN))]
            put_box_text(be, s, tb, [dict(text=sl["title"], bold=True, bullet=False)], "Title", ctx, rep, base=28, bullets=False)
            rep["warnings"].append("layout has no title placeholder: title drawn as text box")
    content[3] = ctx.content[1] + ctx.content[3] - content[1]

    # source note reserve
    note_box = None
    if ctx.source_notes and (refs or sl.get("source_note")) and kind not in ("title", "section"):
        nh = int(0.3 * EMU_IN)
        note_box = [content[0], content[1] + content[3] - nh, content[2], nh]
        content[3] -= nh + int(0.06 * EMU_IN)

    bullets = norm_bullets(sl.get("bullets"))
    if kind == "title":
        sub = sl.get("subtitle") or []
        sub = sub if isinstance(sub, list) else [sub]
        if sub and bodies:
            put_text(be, s, cat, lay, bodies[0], [dict(text=str(t), bullet=False) for t in sub], "Subtitle", ctx, rep,
                     bullets=False)
        elif sub:
            put_box_text(be, s, [content[0], content[1] + content[3] // 2, content[2], content[3] // 2],
                         [dict(text=str(t)) for t in sub], "Subtitle", ctx, rep, base=20, bullets=False)
    elif kind == "section":
        sub = sl.get("subtitle")
        if sub and bodies:
            put_text(be, s, cat, lay, bodies[0], [dict(text=str(sub), bullet=False)], "Subtitle", ctx, rep, bullets=False)
    elif kind == "bullets":
        if bodies:
            ph = dict(bodies[0])
            if note_box and ph["box"]:
                ph["box"] = [ph["box"][0], ph["box"][1], ph["box"][2], min(ph["box"][3], note_box[1] - ph["box"][1])]
            put_text(be, s, cat, lay, ph, bullets or [dict(text="", bullet=False)], "Body", ctx, rep)
        else:
            put_box_text(be, s, content, bullets, "Body", ctx, rep, base=20)
    elif kind == "two_column":
        cols = [sl.get("left") or {}, sl.get("right") or {}]
        two = [p for p in bodies if p["type"] in ("obj", "body")][:2]
        boxes = [p["box"] for p in two] if len(two) == 2 and all(p["box"] for p in two) else list(split_h(content, 0.5))
        if note_box:
            boxes = [[b[0], b[1], b[2], min(b[3], note_box[1] - b[1])] for b in boxes]
        for ci, col in enumerate(cols):
            name = ("Left" if ci == 0 else "Right")
            paras = []
            if col.get("heading"):
                paras.append(dict(text=col["heading"], bold=True, bullet=False, color="accent1"))
            paras += norm_bullets(col.get("bullets"))
            vis_key = next((k for k in ("figure", "chart", "table") if col.get(k)), None)
            if vis_key:
                vb = boxes[ci]
                if paras:
                    hb, vb = split_v(vb, 0.28 if len(paras) <= 2 else 0.4)
                    put_box_text(be, s, hb, paras, name + " Text", ctx, rep, base=18)
                place_visual(be, s, col, vis_key, vb, ctx, rep, tag + "_" + name.lower(), ci + 1)
            elif paras:
                if len(two) == 2:
                    ph = dict(two[ci])
                    ph["box"] = boxes[ci]
                    put_text(be, s, cat, lay, ph, paras, name + " Body", ctx, rep)
                else:
                    put_box_text(be, s, boxes[ci], paras, name + " Body", ctx, rep, base=20)
    elif kind in ("figure", "chart", "table"):
        if bullets:
            arrange = sl.get("arrange") or ("below" if kind == "table" else "right")
            if not sl.get("arrange") and kind == "figure":
                ar = visual_aspect(sl.get("figure") or {}, ctx)
                if ar and ar > 1.9:
                    arrange = "below"
            if arrange == "below":
                vb, tb = split_v(content, float(sl.get("visual_share", 0.72)))
            else:
                vb, tb = split_h(content, float(sl.get("visual_share", 0.6)))
            place_visual(be, s, sl, kind, vb, ctx, rep, tag)
            put_box_text(be, s, tb, bullets, "Body", ctx, rep, base=18 if arrange == "right" else 18)
        else:
            place_visual(be, s, sl, kind, content, ctx, rep, tag)
    elif kind == "figures":
        figs = sl.get("figures") or []
        area = content
        if bullets:
            area, tb = split_v(content, float(sl.get("visual_share", 0.75)))
            put_box_text(be, s, tb, bullets, "Body", ctx, rep, base=18)
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
    if note_box:
        be.textbox(s, note_box, [dict(text=sl.get("source_note") or (ctx.lab["source"] + "; ".join(refs)), size=10, color="7F7F7F", bullet=False,
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
    be.notes(s, notes)
    rep["notes_chars"] = len(notes)
    rep["sources"] = refs
    be.finish(s)
    return rep


def make_ctx(cat, spec, outdir):
    """layout context shared by deck_build and deck_edit add_slide"""
    meta = spec.get("meta", {})
    th = cat["masters"][0]["theme"] if cat["masters"] else {}
    ctx = Ctx()
    ctx.master = 0
    ctx.content = K.content_area(cat)
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
    ctx.fig_dir = os.path.join(outdir, "figures")
    ctx.chart_dir = os.path.join(outdir, "charts")
    os.makedirs(ctx.fig_dir, exist_ok=True)
    os.makedirs(ctx.chart_dir, exist_ok=True)
    return ctx


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
    ctx = make_ctx(cat, spec, outdir)
    lang = ctx.lang
    def do_build(backend):
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
