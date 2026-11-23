#!/usr/bin/env python3
"""deck_edit.py - source-preserving edits of an existing .pptx, plus inventory, version snapshots and slide diffs.

  inventory --pptx deck.pptx [--out inv.json]          slides, layouts, shape ids/names/kinds/boxes/text, notes
  apply     --pptx in.pptx --ops ops.json --out out.pptx --log edit_log.json [--engine auto|pptx|xml]
  snapshot  --pptx deck.pptx --dir /app/output/versions --label v1 --note "why"   (copy + versions.json manifest)
  diff      --a v0.pptx --b v1.pptx --out slide_diff.json [--png-a dirA --png-b dirB]
  round     --pptx in.pptx --ops ops.json --outdir /app/output [--materials ...] [--note "why"]
            one call per revision: snapshot v0 (first time) -> apply -> render -> diff vs previous version (text +
            pixels) -> deck_check --pptx (numbers/evidence) -> template audit vs v0 -> snapshot vN

ops.json = list of operations, applied in order; slides are 1-based positions in the CURRENT deck (or
"slide_id": the stable sldId shown by inventory). Shapes: "shape": name | id | "title" | "body".
  replace_text {slide?|"all", shape?, find, replace}     run-level, formatting of the runs is kept
  set_text     {slide, shape, text | paragraphs:[str|{text,level}]}  paragraph/run formatting reused per level
  set_notes    {slide, text}
  set_font     {slide|"all", shape?, size?, bold?, italic?, color?(hex|accent1..), font?, ea_font?}
  delete_shape {slide, shape}        move_shape {slide, shape, box:[x,y,w,h] inches}
  replace_image{slide, shape, image, fit:"contain"|"box"}
  delete_slide {slide}   move_slide {slide, to}   reorder {order:[old positions in new order]}
  duplicate_slide {slide, to?}
  add_slide    {position?, slide:{deck_build slide spec}}   (uses the deck's own layouts)
  apply_theme  {fonts:{major,minor,ea}, colors:{accent1:..}} | {from_template: path}
Engine (apply): python-pptx (if importable) runs replace_text/set_text/set_notes/set_font; every other op and the
fallback use the stdlib XML engine, which copies untouched package parts byte-for-byte (round defaults to it;
--engine pptx/auto switches). edit_log.json records each op with before/after values and changed package parts.
"""
import argparse, copy, difflib, json, os, re, shutil, sys, tempfile, time, zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

A, P, R = K.A, K.P, K.R
PPTX_OPS = {"replace_text", "set_text", "set_notes", "set_font"}
RPR_ORDER = ["ln", "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill", "effectLst", "effectDag",
             "highlight", "uLnTx", "uLn", "uFillTx", "uFill", "latin", "ea", "cs", "sym", "hlinkClick",
             "hlinkMouseOver", "rtl", "extLst"]
FILL_TAGS = {"noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"}


class OpError(Exception):
    pass


def local(tag):
    return tag.split("}")[-1]


# ----------------------------------------------------------------------------- generic element helpers (ET or lxml)
def shape_elems(tree):
    for el in tree.iter():
        if local(el.tag) in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp"):
            yield el


def cnv(el):
    for ch in el:
        if local(ch.tag).startswith("nv"):
            for c in ch:
                if local(c.tag) == "cNvPr":
                    return c
    return None


def ph_of(el):
    for ch in el.iter():
        if local(ch.tag) == "ph":
            return ch
    return None


def find_shape(tree, ref):
    shapes = [e for e in shape_elems(tree) if cnv(e) is not None]
    if ref is None:
        return None
    r = str(ref)
    if r.isdigit():
        for e in shapes:
            if cnv(e).get("id") == r:
                return e
    if r.lower() == "title":
        for e in shapes:
            ph = ph_of(e)
            if ph is not None and ph.get("type") in ("title", "ctrTitle"):
                return e
    if r.lower() in ("body", "content", "subtitle"):
        for e in shapes:
            ph = ph_of(e)
            if ph is not None and ph.get("type", "obj") in ("obj", "body", "subTitle"):
                return e
    for e in shapes:
        if cnv(e).get("name") == r:
            return e
    for e in shapes:
        if (cnv(e).get("name") or "").lower() == r.lower():
            return e
    return None


def text_of(el):
    out = []
    for p in el.iter(A + "p"):
        s = ""
        for ch in p:
            t = local(ch.tag)
            if t in ("r", "fld"):
                x = ch.find(A + "t")
                s += (x.text or "") if x is not None else ""
            elif t == "br":
                s += "\n"
        out.append(s)
    return "\n".join(out)


def replace_in_para(p, find, repl):
    runs = [r for r in p if local(r.tag) == "r" and r.find(A + "t") is not None]
    ts = [r.find(A + "t") for r in runs]
    full = "".join(t.text or "" for t in ts)
    occ = [m.start() for m in re.finditer(re.escape(find), full)]
    if not occ:
        return 0
    bounds, off = [], 0
    for t in ts:
        L = len(t.text or "")
        bounds.append((off, off + L))
        off += L
    drop = set()
    for s0 in reversed(occ):
        e0 = s0 + len(find)
        i = next(k for k, b in enumerate(bounds) if b[0] <= s0 < b[1])
        j = next(k for k, b in enumerate(bounds) if b[0] < e0 <= b[1])
        ti, tj = ts[i], ts[j]
        if i == j:
            ti.text = ti.text[:s0 - bounds[i][0]] + repl + ti.text[e0 - bounds[i][0]:]
        else:
            ti.text = ti.text[:s0 - bounds[i][0]] + repl
            tj.text = tj.text[e0 - bounds[j][0]:]
            for k in range(i + 1, j):
                drop.add(k)
            if not tj.text:
                drop.add(j)
    for k in sorted(drop, reverse=True):
        p.remove(runs[k])
    return len(occ)


def set_paragraphs(el, paras, mk):
    """replace text paragraphs keeping per-level pPr/rPr formatting of the old text"""
    tx = None
    for ch in el.iter():
        if local(ch.tag) == "txBody":
            tx = ch
            break
    if tx is None:
        raise OpError("shape has no text body")
    old = tx.findall(A + "p")
    fmt = {}
    for p in old:
        ppr = p.find(A + "pPr")
        lvl = int(ppr.get("lvl", 0)) if ppr is not None else 0
        r = p.find(A + "r")
        rpr = r.find(A + "rPr") if r is not None else None
        end = p.find(A + "endParaRPr")
        fmt.setdefault(lvl, (ppr, rpr, end))
    first = fmt.get(0) or (next(iter(fmt.values())) if fmt else (None, None, None))
    for p in old:
        tx.remove(p)
    for q in paras:
        lvl = int(q.get("level", 0))
        ppr, rpr, end = fmt.get(lvl, first)
        p = mk("<a:p/>")
        if ppr is not None:
            np_ = copy.deepcopy(ppr)
            if lvl:
                np_.set("lvl", str(lvl))
            elif "lvl" in np_.attrib:
                del np_.attrib["lvl"]
            p.append(np_)
        elif lvl:
            p.append(mk('<a:pPr lvl="%d"/>' % lvl))
        for t, em, base in K.inline_runs(q["text"], scripts=True):
            r = mk("<a:r><a:t></a:t></a:r>")
            if rpr is not None:
                nr = copy.deepcopy(rpr)
                if em:
                    nr.set("b", "1")
                if base:
                    nr.set("baseline", str(base))
                r.insert(0, nr)
            elif em or base:
                r.insert(0, mk('<a:rPr lang="en-US"%s%s/>' % (' b="1"' if em else "", ' baseline="%d"' % base if base else "")))
            r.find(A + "t").text = t
            rp = r.find(A + "rPr")
            if rp is not None and K.has_cjk(t) and not str(rp.get("lang", "")).startswith(("zh", "ja", "ko")):
                rp.set("lang", "zh-CN")
                rp.set("altLang", "en-US")
            p.append(r)
        if end is not None:
            p.append(copy.deepcopy(end))
        tx.append(p)


def rpr_apply(rpr, mk, size=None, bold=None, italic=None, color=None, font=None, ea_font=None):
    before = dict(sz=rpr.get("sz"), b=rpr.get("b"), i=rpr.get("i"))
    if size:
        rpr.set("sz", str(int(round(float(size) * 100))))
    if bold is not None:
        rpr.set("b", "1" if bold else "0")
    if italic is not None:
        rpr.set("i", "1" if italic else "0")
    if color:
        if not K.color_xml(color):
            raise OpError("colour %r is neither a 6-digit hex nor a theme colour name" % color)
        for ch in list(rpr):
            if local(ch.tag) in FILL_TAGS:
                rpr.remove(ch)
        rpr.append(mk(K.color_xml(color)))
    for tag, val in (("latin", font), ("ea", ea_font)):
        if val:
            for ch in list(rpr):
                if local(ch.tag) == tag:
                    rpr.remove(ch)
            rpr.append(mk('<a:%s typeface="%s"/>' % (tag, K.xa(val))))
    kids = list(rpr)
    for ch in kids:
        rpr.remove(ch)
    for ch in sorted(kids, key=lambda c: RPR_ORDER.index(local(c.tag)) if local(c.tag) in RPR_ORDER else 99):
        rpr.append(ch)
    return before


def font_targets(el, mk):
    out = []
    for r in el.iter():
        if local(r.tag) in ("r", "fld"):
            rpr = r.find(A + "rPr")
            if rpr is None:
                rpr = mk('<a:rPr lang="en-US"/>')
                r.insert(0, rpr)
            out.append(rpr)
        elif local(r.tag) == "endParaRPr":
            out.append(r)
    return out


# ----------------------------------------------------------------------------- XML engine
class XmlEngine:
    name = "xml-stdlib"

    def __init__(self, path):
        self.pkg = K.Package(path)
        self.mk = K.frag

    def slides(self):
        return K.slide_list(self.pkg)

    def slide(self, op):
        sl = self.slides()
        if op.get("slide_id") is not None:
            for s in sl:
                if s["sld_id"] == int(op["slide_id"]):
                    return s
            raise OpError("no slide with sld_id %s" % op["slide_id"])
        i = op.get("slide")
        if not isinstance(i, int) or not 1 <= i <= len(sl):
            raise OpError("slide %r out of range 1..%d" % (i, len(sl)))
        return sl[i - 1]

    def targets(self, op):
        """[(slide_part, shape_el_or_None)] for slide/'all' and optional shape"""
        sl = self.slides() if op.get("slide") in (None, "all") and op.get("slide_id") is None else [self.slide(op)]
        out = []
        for s in sl:
            root = self.pkg.xml(s["part"])
            tree = root.find(P + "cSld/" + P + "spTree")
            if op.get("shape") is not None:
                e = find_shape(tree, op["shape"])
                if e is None:
                    if len(sl) == 1:
                        raise OpError("shape %r not found on slide %d (see inventory)" % (op["shape"], s["index"]))
                    continue
                out.append((s, e))
            else:
                out += [(s, e) for e in shape_elems(tree) if local(e.tag) in ("sp", "graphicFrame")]
        return out

    def op_replace_text(self, op):
        changes = []
        for s, e in self.targets(op):
            before = text_of(e)
            n = sum(replace_in_para(p, op["find"], op.get("replace", "")) for p in e.iter(A + "p"))
            if n:
                changes.append(dict(slide=s["index"], shape=cnv(e).get("name"), n=n, before=before, after=text_of(e)))
                self.pkg.touch(s["part"])
        if not changes:
            raise OpError("text %r not found" % op["find"])
        return changes

    def op_set_text(self, op):
        if op.get("shape") is None or (op.get("slide") in (None, "all") and op.get("slide_id") is None):
            raise OpError("set_text needs slide (or slide_id) and shape")
        s, e = self.targets(op)[0]
        before = text_of(e)
        paras = op.get("paragraphs") or [dict(text=t) for t in str(op.get("text", "")).split("\n")]
        paras = [dict(text=p, level=0) if isinstance(p, str) else dict(text=p.get("text", ""), level=p.get("level", 0)) for p in paras]
        set_paragraphs(e, paras, self.mk)
        self.pkg.touch(s["part"])
        return [dict(slide=s["index"], shape=cnv(e).get("name"), before=before, after=text_of(e))]

    def op_set_notes(self, op):
        s = self.slide(op)
        b, a_ = K.set_notes_text(self.pkg, s["part"], op.get("text", ""))
        return [dict(slide=s["index"], before=b, after=a_)]

    def op_set_font(self, op):
        res = []
        for s, e in self.targets(op):
            befores = [rpr_apply(r, self.mk, op.get("size"), op.get("bold"), op.get("italic"), op.get("color"),
                                 op.get("font"), op.get("ea_font")) for r in font_targets(e, self.mk)]
            if befores:
                res.append(dict(slide=s["index"], shape=cnv(e).get("name"), runs=len(befores), before=befores[:3],
                                after=dict(size=op.get("size"), bold=op.get("bold"), color=op.get("color"), font=op.get("font"))))
                self.pkg.touch(s["part"])
        return res

    def op_delete_shape(self, op):
        s = self.slide(op)
        root = self.pkg.xml(s["part"])
        tree = root.find(P + "cSld/" + P + "spTree")
        e = find_shape(tree, op["shape"])
        if e is None:
            raise OpError("shape %r not found" % op["shape"])
        parent = {c: p for p in tree.iter() for c in p}[e]
        txt = text_of(e)
        parent.remove(e)
        self.pkg.touch(s["part"])
        return [dict(slide=s["index"], shape=op["shape"], before=txt, after=None)]

    def op_move_shape(self, op):
        s = self.slide(op)
        tree = self.pkg.xml(s["part"]).find(P + "cSld/" + P + "spTree")
        e = find_shape(tree, op["shape"])
        if e is None:
            raise OpError("shape %r not found" % op["shape"])
        box = [K.emu(v) for v in op["box"]]
        before = K._box(e)
        sppr = e.find(P + "spPr")
        if local(e.tag) == "graphicFrame":
            x = e.find(P + "xfrm")
            if x is None:
                x = self.mk("<p:xfrm/>")
                e.insert(1, x)
        else:
            if sppr is None:
                raise OpError("shape has no spPr")
            x = sppr.find(A + "xfrm")
            if x is None:
                x = self.mk("<a:xfrm/>")
                sppr.insert(0, x)
        for ch in list(x):
            x.remove(ch)
        x.append(self.mk('<a:off x="%d" y="%d"/>' % (box[0], box[1])))
        x.append(self.mk('<a:ext cx="%d" cy="%d"/>' % (box[2], box[3])))
        self.pkg.touch(s["part"])
        return [dict(slide=s["index"], shape=op["shape"], before=[K.to_in(v) for v in before] if before else "inherited",
                     after=op["box"])]

    def op_replace_image(self, op):
        s = self.slide(op)
        tree = self.pkg.xml(s["part"]).find(P + "cSld/" + P + "spTree")
        e = find_shape(tree, op["shape"])
        if e is None or local(e.tag) != "pic":
            raise OpError("picture %r not found" % op.get("shape"))
        blip = e.find(P + "blipFill/" + A + "blip")
        rid = blip.get(R + "embed")
        rels = self.pkg.rels(s["part"])
        ext = os.path.splitext(op["image"])[1].lower().lstrip(".")
        mname = self.pkg.unique_name("ppt/media/image%d." + ext)
        with open(op["image"], "rb") as f:
            self.pkg.write(mname, f.read())
        self.pkg.ensure_default(ext, K.IMG_CT.get(ext, "image/" + ext))
        old = None
        for r in rels:
            if r["Id"] == rid:
                old = r["Target"]
                r["Target"] = self.pkg.relpath(s["part"], mname)
        self.pkg.write_rels(s["part"], rels)
        before = K._box(e)
        if op.get("fit", "contain") == "contain" and before:
            px = K.image_size(op["image"])
            if px:
                sc = min(before[2] / float(px[0]), before[3] / float(px[1]))
                w, h = int(px[0] * sc), int(px[1] * sc)
                x = e.find(P + "spPr/" + A + "xfrm")
                x.find(A + "off").set("x", str(before[0] + (before[2] - w) // 2))
                x.find(A + "off").set("y", str(before[1] + (before[3] - h) // 2))
                x.find(A + "ext").set("cx", str(w))
                x.find(A + "ext").set("cy", str(h))
        self.pkg.touch(s["part"])
        K.drop_orphan_media(self.pkg)
        return [dict(slide=s["index"], shape=op["shape"], before=old, after=mname)]

    def _order(self):
        return [s["sld_id"] for s in self.slides()]

    def op_delete_slide(self, op):
        s = self.slide(op)
        before = self._order()
        te = find_shape(self.pkg.xml(s["part"]).find(P + "cSld/" + P + "spTree"), "title")
        title = text_of(te) if te is not None else ""
        K.remove_slide(self.pkg, s["part"])
        return [dict(slide=s["index"], title=title, before=before, after=self._order())]

    def op_move_slide(self, op):
        s = self.slide(op)
        before = self._order()
        root, lst = K.sld_id_list(self.pkg)
        el = [x for x in lst if int(x.get("id")) == s["sld_id"]][0]
        lst.remove(el)
        to = max(1, min(int(op["to"]), len(lst) + 1))
        lst.insert(to - 1, el)
        self.pkg.touch("ppt/presentation.xml")
        return [dict(before=before, after=self._order())]

    def op_reorder(self, op):
        before = self._order()
        root, lst = K.sld_id_list(self.pkg)
        els = list(lst)
        order = [int(i) for i in op["order"]]
        if sorted(order) != list(range(1, len(els) + 1)):
            raise OpError("order must be a permutation of 1..%d" % len(els))
        for x in els:
            lst.remove(x)
        for i in order:
            lst.append(els[i - 1])
        self.pkg.touch("ppt/presentation.xml")
        return [dict(before=before, after=self._order())]

    def op_duplicate_slide(self, op):
        s = self.slide(op)
        pkg = self.pkg
        xml = pkg.read(s["part"])
        rels = [r for r in pkg.rels(s["part"]) if r["Type"] != K.RT_NOTES]
        new = pkg.unique_name("ppt/slides/slide%d.xml")
        pkg.write(new, xml)
        pkg.set_override(new, K.CT_SLIDE)
        pkg.write_rels(new, [dict(r) for r in rels])
        notes = pkg.related(s["part"], K.RT_NOTES)
        if notes:
            ntext = text_of(pkg.xml(notes[0]))
            K.set_notes_text(pkg, new, ntext)
        rid = pkg.add_rel("ppt/presentation.xml", K.RT_SLIDE, new)
        root, lst = K.sld_id_list(pkg)
        nid = max(int(x.get("id")) for x in lst) + 1
        el = ET.Element(P + "sldId", {"id": str(nid), R + "id": rid})
        to = int(op.get("to", s["index"] + 1))
        lst.insert(max(0, min(to - 1, len(lst))), el)
        pkg.touch("ppt/presentation.xml")
        return [dict(slide=s["index"], new_sld_id=nid, position=to, after=self._order())]

    def op_add_slide(self, op):
        import deck_build as DB
        spec_slide = op["slide"]
        be = DB.XmlBackend(None, keep_slides=True, pkg=self.pkg)
        outdir = op.get("assets_dir") or tempfile.mkdtemp(prefix="deck_edit_add_")
        ctx = DB.make_ctx(be.cat, dict(meta=op.get("meta", {}), footer=op.get("footer") or {}, slides=[spec_slide]), outdir)
        n_before = len(self.slides())
        rep = DB.build_slide(be, be.cat, spec_slide, n_before + 1, ctx, {})
        if op.get("position"):
            self.op_move_slide(dict(slide=n_before + 1, to=int(op["position"])))
        return [dict(position=op.get("position") or n_before + 1, layout=rep["layout"], warnings=rep["warnings"], after=self._order())]

    def op_apply_theme(self, op):
        fonts, colors = dict(op.get("fonts") or {}), dict(op.get("colors") or {})
        if op.get("from_template"):
            tp = K.Package(op["from_template"])
            tc = K.catalog(tp)
            th = tc["masters"][0]["theme"] if tc["masters"] else {}
            tf = th.get("fonts", {})
            fonts.setdefault("major", tf.get("major_latin"))
            fonts.setdefault("minor", tf.get("minor_latin"))
            fonts.setdefault("major_ea", tf.get("major_ea"))
            fonts.setdefault("minor_ea", tf.get("minor_ea"))
            for k, v in (th.get("colors") or {}).items():
                colors.setdefault(k, v)
        if fonts.get("ea"):
            fonts.setdefault("major_ea", fonts["ea"])
            fonts.setdefault("minor_ea", fonts["ea"])
        res = []
        cat = K.catalog(self.pkg)
        for m in cat["masters"]:
            tpart = m["theme"].get("part")
            if not tpart:
                continue
            root = self.pkg.xml(tpart)
            before = K.theme_info(self.pkg, tpart)
            fs = root.find(A + "themeElements/" + A + "fontScheme")
            for which, key_l, key_e in (("majorFont", "major", "major_ea"), ("minorFont", "minor", "minor_ea")):
                f = fs.find(A + which) if fs is not None else None
                if f is None:
                    continue
                for tag, key in (("latin", key_l), ("ea", key_e)):
                    if fonts.get(key):
                        e = f.find(A + tag)
                        if e is None:
                            e = ET.SubElement(f, A + tag)
                        e.set("typeface", fonts[key])
            cs = root.find(A + "themeElements/" + A + "clrScheme")
            if cs is not None:
                for c in cs:
                    name = local(c.tag)
                    if colors.get(name) and re.fullmatch(r"[0-9A-Fa-f]{6}", str(colors[name])):
                        for sub in list(c):
                            c.remove(sub)
                        ET.SubElement(c, A + "srgbClr", {"val": colors[name].upper()})
            self.pkg.touch(tpart)
            after = K.theme_info(self.pkg, tpart)
            res.append(dict(theme=tpart, before=dict(fonts=before["fonts"], colors=before["colors"]),
                            after=dict(fonts=after["fonts"], colors=after["colors"])))
        return res

    def save(self, path):
        self.pkg.save(path)


# ----------------------------------------------------------------------------- python-pptx engine (text ops)
class PptxEngine:
    name = "python-pptx"

    def __init__(self, path):
        import pptx
        from pptx import Presentation
        from pptx.oxml import parse_xml
        self.version = pptx.__version__
        self.prs = Presentation(path)
        self.mk = lambda x: parse_xml('<w %s>%s</w>' % (K.NSDECL, x))[0]

    def _slides(self, op):
        sl = list(self.prs.slides)
        if op.get("slide_id") is not None:
            r = [s for s in sl if s.slide_id == int(op["slide_id"])]
            if not r:
                raise OpError("no slide with sld_id %s" % op["slide_id"])
            return [(sl.index(r[0]) + 1, r[0])]
        if op.get("slide") in (None, "all"):
            return list(enumerate(sl, 1))
        i = op["slide"]
        if not isinstance(i, int) or not 1 <= i <= len(sl):
            raise OpError("slide %r out of range 1..%d" % (i, len(sl)))
        return [(i, sl[i - 1])]

    def targets(self, op):
        out = []
        many = op.get("slide") in (None, "all") and op.get("slide_id") is None
        for i, s in self._slides(op):
            tree = s.shapes._spTree
            if op.get("shape") is not None:
                e = find_shape(tree, op["shape"])
                if e is None:
                    if not many:
                        raise OpError("shape %r not found on slide %d (see inventory)" % (op["shape"], i))
                    continue
                out.append((i, s, e))
            else:
                out += [(i, s, e) for e in shape_elems(tree) if local(e.tag) in ("sp", "graphicFrame")]
        return out

    def op_replace_text(self, op):
        changes = []
        for i, s, e in self.targets(op):
            before = text_of(e)
            n = sum(replace_in_para(p, op["find"], op.get("replace", "")) for p in e.iter(A + "p"))
            if n:
                changes.append(dict(slide=i, shape=cnv(e).get("name"), n=n, before=before, after=text_of(e)))
        if not changes:
            raise OpError("text %r not found" % op["find"])
        return changes

    def op_set_text(self, op):
        t = self.targets(op)
        if not t or op.get("shape") is None:
            raise OpError("shape required")
        i, s, e = t[0]
        before = text_of(e)
        paras = op.get("paragraphs") or [dict(text=x) for x in str(op.get("text", "")).split("\n")]
        paras = [dict(text=p, level=0) if isinstance(p, str) else dict(text=p.get("text", ""), level=p.get("level", 0)) for p in paras]
        set_paragraphs(e, paras, self.mk)
        return [dict(slide=i, shape=cnv(e).get("name"), before=before, after=text_of(e))]

    def op_set_notes(self, op):
        (i, s), = self._slides(op)[:1]
        before = s.notes_slide.notes_text_frame.text if s.has_notes_slide else ""
        s.notes_slide.notes_text_frame.text = op.get("text", "")
        return [dict(slide=i, before=before, after=op.get("text", ""))]

    def op_set_font(self, op):
        res = []
        for i, s, e in self.targets(op):
            befores = [rpr_apply(r, self.mk, op.get("size"), op.get("bold"), op.get("italic"), op.get("color"),
                                 op.get("font"), op.get("ea_font")) for r in font_targets(e, self.mk)]
            if befores:
                res.append(dict(slide=i, shape=cnv(e).get("name"), runs=len(befores), before=befores[:3],
                                after=dict(size=op.get("size"), bold=op.get("bold"), color=op.get("color"), font=op.get("font"))))
        return res

    def save(self, path):
        self.prs.save(path)


def part_hashes(path):
    import hashlib
    with zipfile.ZipFile(path) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}


def cmd_apply(a):
    ops = json.load(open(a.ops, encoding="utf-8"))
    ops = ops.get("ops", ops) if isinstance(ops, dict) else ops
    bases = [os.path.dirname(os.path.abspath(a.ops)), os.path.dirname(os.path.abspath(a.pptx)), "/input", os.getcwd()]
    for op in ops:  # relative image/csv/pdf paths: ops.json folder, the deck's folder, /input, cwd
        targets = [op] if op.get("op") == "replace_image" else ([d for _, d in K.spec_visuals(op["slide"])] if op.get("op") == "add_slide" else [])
        for d in targets:
            for k in ("image", "csv", "pdf"):
                v = d.get(k)
                if isinstance(v, str) and v and not os.path.isabs(v):
                    for b in bases:
                        if os.path.exists(os.path.join(b, v)):
                            d[k] = os.path.join(b, v)
                            break
    want = a.engine
    if want == "auto":
        want = "xml" if os.environ.get("DECK_BACKEND", "").lower() == "xml" else "pptx"
    pptx_ok = True
    if want == "pptx":
        try:
            import pptx  # noqa: F401
        except ImportError:
            pptx_ok = False
    work = tempfile.mkdtemp(prefix="deck_edit_")
    cur = os.path.join(work, "cur.pptx")
    shutil.copyfile(a.pptx, cur)
    log = []
    eng, eng_kind = None, None
    for i, op in enumerate(ops, 1):
        kind = "pptx" if (want == "pptx" and pptx_ok and op.get("op") in PPTX_OPS) else "xml"
        if eng is not None and kind != eng_kind:
            eng.save(cur)
            eng = None
        if eng is None:
            try:
                eng = PptxEngine(cur) if kind == "pptx" else XmlEngine(cur)
            except Exception as e:  # python-pptx cannot open the file: stdlib engine takes over
                if kind != "pptx":
                    raise
                pptx_ok, kind = False, "xml"
                log.append(dict(i=0, op="engine", status="note", error="python-pptx failed to open (%r); using xml-stdlib" % (e,)))
                eng = XmlEngine(cur)
            eng_kind = kind
        fn = getattr(eng, "op_" + str(op.get("op")), None)
        entry = dict(i=i, op=op.get("op"), args={k: v for k, v in op.items() if k != "op"}, engine=eng.name,
                     reason=op.get("reason"))
        if fn is None:
            entry.update(status="error", error="unknown op (or not supported by this engine)")
        else:
            try:
                entry.update(status="ok", changes=fn(op))
            except (OpError, KeyError, ValueError, StopIteration) as e:
                entry.update(status="error", error=str(e) or repr(e))
            except Exception as e:  # unexpected (e.g. library version differences): logged, deck still written
                entry.update(status="error", error="%s: %r" % (type(e).__name__, e))
        log.append(entry)
    if eng is not None:
        eng.save(cur)
    shutil.copyfile(cur, a.out)
    hin, hout = part_hashes(a.pptx), part_hashes(a.out)
    changed = sorted(n for n in hout if n in hin and hin[n] != hout[n])
    added = sorted(n for n in hout if n not in hin)
    removed = sorted(n for n in hin if n not in hout)
    res = dict(input=os.path.abspath(a.pptx), input_sha256=K.sha256(a.pptx), output=os.path.abspath(a.out),
               output_sha256=K.sha256(a.out), engine_requested=a.engine, python_pptx_available=pptx_ok,
               time=time.strftime("%Y-%m-%d %H:%M:%S"), n_ops=len(ops), n_ok=sum(1 for e in log if e["status"] == "ok"),
               n_errors=sum(1 for e in log if e["status"] == "error"), ops=log,
               parts=dict(changed=changed, added=added, removed=removed,
                          unchanged=len([n for n in hout if n in hin and hin[n] == hout[n]])))
    if a.log:
        prev = []
        if os.path.exists(a.log) and a.append_log:
            try:
                prev = json.load(open(a.log, encoding="utf-8"))
                prev = prev if isinstance(prev, list) else [prev]
            except Exception:
                prev = []
        with open(a.log, "w", encoding="utf-8") as f:
            json.dump(prev + [res] if a.append_log else res, f, ensure_ascii=False, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(dict(applied=res["n_ok"], errors=[(e["i"], e["op"], e["error"]) for e in log if e["status"] == "error"],
                          parts_changed=len(changed), parts_added=len(added), parts_removed=len(removed),
                          parts_unchanged=res["parts"]["unchanged"], out=a.out), ensure_ascii=False))
    return 0


def cmd_inventory(a):
    inv = K.inventory(K.Package(a.pptx))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(inv, f, ensure_ascii=False, indent=1)
    print("slides: %d  size: %.2fx%.2f in  layouts: %s" % (inv["n_slides"], K.to_in(inv["slide_w"]), K.to_in(inv["slide_h"]),
                                                       ", ".join("%d:%s" % (l["index"], l["name"]) for l in inv["layouts"])))
    th = inv.get("theme") or {}
    print("theme fonts: %s" % json.dumps(th.get("fonts"), ensure_ascii=False))
    for s in inv["slides"]:
        print("slide %d [sld_id %d, layout '%s'] notes=%d chars" % (s["index"], s["sld_id"], s["layout"], len(s["notes"])))
        for sh in s["shapes"]:
            kind = sh["kind"] + ("(%s)" % sh["ph_type"] if sh.get("ph_type") else "")
            t = (sh.get("text") or "").replace("\n", " / ")
            print("   #%s '%s' %s %s%s" % (sh["id"], sh["name"], kind, ("%.0fpt " % sh["min_font_pt"]) if sh.get("min_font_pt") else "",
                                         repr(t[:70]) if t else ""))
    return 0


def cmd_snapshot(a):
    os.makedirs(a.dir, exist_ok=True)
    man_p = os.path.join(a.dir, "versions.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else []
    dst = os.path.join(a.dir, "%s.pptx" % a.label)
    shutil.copyfile(a.pptx, dst)
    parent = man[-1]["label"] if man else None
    entry = dict(label=a.label, file=dst, sha256=K.sha256(dst), parent=a.parent or parent, note=a.note,
                 source=os.path.abspath(a.pptx), n_slides=len(K.slide_list(K.Package(dst))), time=time.strftime("%Y-%m-%d %H:%M:%S"),
                 rollback="cp %s <deck.pptx>" % dst)
    man = [m for m in man if m["label"] != a.label] + [entry]
    with open(man_p, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(json.dumps(entry, ensure_ascii=False))
    return 0


def slide_map(inv):
    return {s["sld_id"]: s for s in inv["slides"]}


def cmd_diff(a):
    A_, B_ = K.inventory(K.Package(a.a)), K.inventory(K.Package(a.b))
    ma, mb = slide_map(A_), slide_map(B_)
    out = dict(a=os.path.abspath(a.a), b=os.path.abspath(a.b), slides_a=A_["n_slides"], slides_b=B_["n_slides"],
               removed=[dict(sld_id=k, index=v["index"], title=v["title"]) for k, v in ma.items() if k not in mb],
               added=[dict(sld_id=k, index=v["index"], title=v["title"]) for k, v in mb.items() if k not in ma],
               order_a=[s["sld_id"] for s in A_["slides"]], order_b=[s["sld_id"] for s in B_["slides"]], slides=[])
    pix = None
    if a.png_a and a.png_b:
        try:
            import numpy as np
            from PIL import Image
            pix = (np, Image)
        except Exception:
            pix = None
    for sid, sb in mb.items():
        if sid not in ma:
            continue
        sa = ma[sid]
        d = dict(sld_id=sid, index_a=sa["index"], index_b=sb["index"], moved=sa["index"] != sb["index"],
                 layout_changed=sa["layout"] != sb["layout"], shapes=[])
        sha = {sh["id"]: sh for sh in sa["shapes"]}
        shb = {sh["id"]: sh for sh in sb["shapes"]}
        for i_, sh in shb.items():
            if i_ not in sha:
                d["shapes"].append(dict(id=i_, name=sh["name"], change="added", after=sh.get("text", "")[:300]))
            else:
                o = sha[i_]
                ch = []
                if (o.get("text") or "") != (sh.get("text") or ""):
                    ch.append("text")
                if o.get("box") != sh.get("box"):
                    ch.append("position")
                if o.get("min_font_pt") != sh.get("min_font_pt"):
                    ch.append("font size")
                fa = [(r.get("latin"), r.get("color"), r.get("b")) for p in o.get("paras", []) for r in p["runs"]]
                fb = [(r.get("latin"), r.get("color"), r.get("b")) for p in sh.get("paras", []) for r in p["runs"]]
                if fa != fb and "text" not in ch:
                    ch.append("font/colour")
                if o.get("image_part") != sh.get("image_part"):
                    ch.append("image")
                if ch:
                    e = dict(id=i_, name=sh["name"], change=ch)
                    if "text" in ch:
                        e["diff"] = list(difflib.unified_diff((o.get("text") or "").split("\n"), (sh.get("text") or "").split("\n"), lineterm="", n=0))[2:]
                    d["shapes"].append(e)
        for i_, sh in sha.items():
            if i_ not in shb:
                d["shapes"].append(dict(id=i_, name=sh["name"], change="removed", before=sh.get("text", "")[:300]))
        if sa["notes"] != sb["notes"]:
            d["notes"] = list(difflib.unified_diff(sa["notes"].split("\n"), sb["notes"].split("\n"), lineterm="", n=0))[2:]
        if pix:
            np, Image = pix
            pa = os.path.join(a.png_a, "slide-%02d.png" % sa["index"])
            pb = os.path.join(a.png_b, "slide-%02d.png" % sb["index"])
            if os.path.exists(pa) and os.path.exists(pb):
                ia = np.asarray(Image.open(pa).convert("L"), dtype=float)
                ib = np.asarray(Image.open(pb).convert("L"), dtype=float)
                if ia.shape == ib.shape:
                    d["pixels_changed"] = round(float((abs(ia - ib) > 30).mean()), 4)
        d["changed"] = bool(d["shapes"] or d.get("notes") or d["moved"] or d["layout_changed"] or d.get("pixels_changed"))
        out["slides"].append(d)
    out["n_changed"] = sum(1 for d in out["slides"] if d["changed"])
    out["n_unchanged"] = sum(1 for d in out["slides"] if not d["changed"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    L = ["# Slide diff", "", "A: %s (%d slides)  B: %s (%d slides)" % (a.a, A_["n_slides"], a.b, B_["n_slides"]), ""]
    for r in out["removed"]:
        L.append("- removed slide (was %d): %s" % (r["index"], r["title"]))
    for r in out["added"]:
        L.append("- added slide %d: %s" % (r["index"], r["title"]))
    for d in out["slides"]:
        if not d["changed"]:
            continue
        L.append("## slide %d -> %d%s" % (d["index_a"], d["index_b"], " (moved)" if d["moved"] else ""))
        for sh in d["shapes"]:
            L.append("- %s: %s" % (sh["name"], sh["change"]))
            for x in sh.get("diff", []):
                L.append("    " + x)
        if d.get("notes"):
            L.append("- notes: " + " ".join(d["notes"])[:300])
        if d.get("pixels_changed") is not None:
            L.append("- pixels changed: %.1f%%" % (100 * d["pixels_changed"]))
    with open(a.out[:-5] + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(json.dumps(dict(changed=out["n_changed"], unchanged=out["n_unchanged"], added=len(out["added"]),
                          removed=len(out["removed"]), out=a.out), ensure_ascii=False))
    return 0


def _run(args, timeout=600):
    import subprocess
    p = subprocess.run([sys.executable] + args, capture_output=True, text=True, timeout=timeout)
    out = p.stdout + p.stderr
    js = {}
    for line in out.splitlines():
        if line.strip().startswith("{"):
            try:
                js = json.loads(line)
                break
            except ValueError:
                pass
    return js, out


def cmd_round(a):
    """one edit round: (snapshot v0) -> apply -> render -> diff vs previous version -> evidence check -> template audit -> snapshot"""
    here = os.path.dirname(os.path.abspath(__file__))
    vdir = os.path.join(a.outdir, "versions")
    os.makedirs(vdir, exist_ok=True)
    man_p = os.path.join(vdir, "versions.json")
    man = json.load(open(man_p, encoding="utf-8")) if os.path.exists(man_p) else []
    summary = {}
    if not man:
        cmd_snapshot(argparse.Namespace(pptx=a.pptx, dir=vdir, label="v0", note="starting deck (input)", parent=None))
        _run([os.path.join(here, "deck_render.py"), "--pptx", os.path.join(vdir, "v0.pptx"), "--outdir",
              os.path.join(vdir, "v0_render"), "--name", "v0"])
        man = json.load(open(man_p, encoding="utf-8"))
    prev = man[-1]
    label = a.label or "v%d" % len(man)
    out = os.path.join(a.outdir, a.name + ".pptx")
    src = a.pptx
    if os.path.abspath(src) == os.path.abspath(out):
        tmp = os.path.join(vdir, "_round_input.pptx")
        shutil.copyfile(src, tmp)
        src = tmp
    cmd_apply(argparse.Namespace(pptx=src, ops=a.ops, out=out, log=os.path.join(a.outdir, "edit_log.json"), append_log=True,
                                 engine=a.engine))
    summary["render"], _ = _run([os.path.join(here, "deck_render.py"), "--pptx", out, "--outdir", a.outdir, "--name", a.name])
    prev_png = os.path.join(vdir, "%s_render" % prev["label"], "previews")
    summary["diff"], _ = _run([os.path.join(here, "deck_edit.py"), "diff", "--a", prev["file"], "--b", out, "--out",
                               os.path.join(a.outdir, "slide_diff.json"), "--png-a", prev_png, "--png-b", os.path.join(a.outdir, "previews")])
    if a.materials:
        chk = [os.path.join(here, "deck_check.py"), "--pptx", out, "--mode", "edit", "--materials"] + a.materials + \
              ["--out", os.path.join(a.outdir, "check_report.json"), "--map", os.path.join(a.outdir, "evidence_map.json")]
        summary["check"], chk_out = _run(chk)
    ref = a.template_ref or os.path.join(vdir, "v0.pptx")
    summary["template_audit"], aud_out = _run([os.path.join(here, "deck_template.py"), "audit", "--pptx", out, "--template", ref,
                                               "--out", os.path.join(a.outdir, "compliance.json")])
    summary["template_audit"] = classify_audit(a, here, vdir, ref, summary["template_audit"])
    cmd_snapshot(argparse.Namespace(pptx=out, dir=vdir, label=label, note=a.note or "edit round %s" % label, parent=prev["label"]))
    if os.path.isdir(os.path.join(a.outdir, "previews")):
        dst = os.path.join(vdir, "%s_render" % label, "previews")
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(os.path.join(a.outdir, "previews"), dst)
    if os.path.exists(os.path.join(vdir, "_round_input.pptx")):
        os.remove(os.path.join(vdir, "_round_input.pptx"))
    log = json.load(open(os.path.join(a.outdir, "edit_log.json"), encoding="utf-8"))
    last = log[-1] if isinstance(log, list) else log
    res = dict(label=label, parent=prev["label"], deck=out, ops_ok=last["n_ok"], ops_errors=[(e["i"], e["op"], e.get("error")) for e in last["ops"] if e["status"] == "error"],
               parts_changed=len(last["parts"]["changed"]), parts_unchanged=last["parts"]["unchanged"],
               slides_changed=summary["diff"].get("changed"), slides_unchanged=summary["diff"].get("unchanged"),
               render=summary["render"].get("status"), render_fails=summary["render"].get("fails"),
               check=(summary.get("check") or {}).get("status"), template=(summary["template_audit"] or {}).get("status"),
               contact_sheet=summary["render"].get("contact_sheet"), rollback="cp %s %s" % (prev["file"], out))
    print(json.dumps(res, ensure_ascii=False))
    if a.materials:  # style warnings about slides the instructions did not touch are not shown (see check_report.json)
        nw = 0
        for line in chk_out.splitlines():
            t = line.strip()
            if t.startswith("FAIL") or (t.startswith("WARN") and " number " in t):
                print("  check " + t)
            elif t.startswith("WARN"):
                nw += 1
        if nw:
            print("  check: %d style warnings (notes/density/claim titles) in check_report.json - change only slides the "
                  "instructions address" % nw)
    try:  # render failures that v0 already had are not regressions of this round
        r0 = json.load(open(os.path.join(vdir, "v0_render", "render_report.json"), encoding="utf-8"))
        r1 = json.load(open(os.path.join(a.outdir, "render_report.json"), encoding="utf-8"))
        old = {re.sub(r"\d+", "#", x["msg"]) + str(x.get("shape")) for x in r0.get("issues", []) if x["level"] == "fail"}
        new = [x for x in r1.get("issues", []) if x["level"] == "fail" and re.sub(r"\d+", "#", x["msg"]) + str(x.get("shape")) not in old]
        print("  render: %d new failure(s) this round%s" % (len(new), "".join("\n    FAIL slide %s %s: %s" % (
            x.get("slide"), x.get("shape") or "", x["msg"]) for x in new)))
        n_old = sum(1 for x in r1.get("issues", []) if x["level"] == "fail") - len(new)
        res.update(new_render_fails=[dict(slide=x.get("slide"), shape=x.get("shape"), msg=x["msg"]) for x in new],
                   pre_existing_render_fails=n_old)
    except Exception:
        pass
    with open(os.path.join(a.outdir, "round_summary.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    comp = json.load(open(os.path.join(a.outdir, "compliance.json"), encoding="utf-8"))
    for x in comp.get("issues", []):
        if x["level"] == "fail":
            print("  template FAIL slide %s %s: %s" % (x.get("slide"), x.get("shape") or "", x["msg"]))
    if comp.get("n_intended") or comp.get("n_pre_existing"):
        print("  template: %d issue(s) requested by the instructions, %d already in v0 (kept as warn, not regressions)"
              % (comp.get("n_intended", 0), comp.get("n_pre_existing", 0)))
    return 0


def classify_audit(a, here, vdir, ref, summary):
    """edit rounds: an audit finding is a regression only if it is new. Findings already present in v0 are
    'pre-existing'; theme/font findings caused by apply_theme / set_font ops of this task are 'intended'."""
    cp = os.path.join(a.outdir, "compliance.json")
    base_p = os.path.join(vdir, "compliance_v0.json")
    try:
        if not os.path.exists(base_p):
            _run([os.path.join(here, "deck_template.py"), "audit", "--pptx", os.path.join(vdir, "v0.pptx"), "--template", ref,
                  "--out", base_p])
        comp = json.load(open(cp, encoding="utf-8"))
        base = json.load(open(base_p, encoding="utf-8")) if os.path.exists(base_p) else {"issues": []}
        ops = json.load(open(a.ops, encoding="utf-8"))
        ops = ops.get("ops", ops) if isinstance(ops, dict) else ops
        theme_op = any(o.get("op") == "apply_theme" for o in ops)
        font_op = any(o.get("op") == "set_font" and (o.get("font") or o.get("ea_font")) for o in ops)
        size_op = any(o.get("op") == "set_font" and o.get("size") for o in ops)
        seen = {(x.get("rule"), re.sub(r"slide \d+", "", x["msg"])) for x in base.get("issues", [])}
        n_int = n_pre = 0
        for x in comp.get("issues", []):
            if x["level"] != "fail":
                continue
            key = (x.get("rule"), re.sub(r"slide \d+", "", x["msg"]))
            if (x.get("rule") == "theme" and theme_op) or (x.get("rule") == "hard_font" and (font_op or size_op)):
                x["level"], x["class"] = "info", "intended (requested font/theme change)"
                n_int += 1
            elif key in seen:
                x["level"], x["class"] = "warn", "pre-existing in v0"
                n_pre += 1
            else:
                x["class"] = "regression"
        fails = [x for x in comp.get("issues", []) if x["level"] == "fail"]
        comp.update(status="pass" if not fails else "needs_fix", n_fail=len(fails), n_intended=n_int, n_pre_existing=n_pre,
                    note="edit mode: fails are only new findings (regressions); intended and pre-existing findings are kept, reclassified")
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(comp, f, ensure_ascii=False, indent=1)
        summary = dict(summary or {}, status=comp["status"], fails=len(fails))
    except Exception as e:  # keep the raw audit
        summary = dict(summary or {}, classify_error=repr(e))
    return summary


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("round")
    p.add_argument("--pptx", required=True, help="deck to edit (the input deck for the first round, then /app/output/deck.pptx)")
    p.add_argument("--ops", required=True)
    p.add_argument("--outdir", default="/app/output")
    p.add_argument("--name", default="deck")
    p.add_argument("--label", default=None)
    p.add_argument("--note", default="")
    p.add_argument("--materials", nargs="+", default=None)
    p.add_argument("--template-ref", default=None, help="template/deck whose master the result must keep (default: v0)")
    p.add_argument("--engine", default="xml", choices=["auto", "pptx", "xml"],
                   help="xml (default for rounds): untouched parts stay byte-identical; auto/pptx: python-pptx for text ops")
    p = sub.add_parser("inventory")
    p.add_argument("--pptx", required=True)
    p.add_argument("--out", default=None)
    p = sub.add_parser("apply")
    p.add_argument("--pptx", required=True)
    p.add_argument("--ops", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--log", default=None)
    p.add_argument("--append-log", action="store_true", help="append this run to an existing log list")
    p.add_argument("--engine", default="auto", choices=["auto", "pptx", "xml"])
    p = sub.add_parser("snapshot")
    p.add_argument("--pptx", required=True)
    p.add_argument("--dir", required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--parent", default=None)
    p = sub.add_parser("diff")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--png-a", default=None)
    p.add_argument("--png-b", default=None)
    a = ap.parse_args()
    return {"inventory": cmd_inventory, "apply": cmd_apply, "snapshot": cmd_snapshot, "diff": cmd_diff, "round": cmd_round}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
