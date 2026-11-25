#!/usr/bin/env python3
"""paper_map.py - a compact, citable map of a paper PDF: title, section headings, figure/table captions with
page + crop box, where each figure/table is discussed, and key sentences (claims, numbers, limitations).

Usage:
  python3 paper_map.py --pdf /input/paper.pdf --materials /app/work/materials --out /app/work/paper_map.json \
         [--figdir /app/work/figs] [--dpi 200]
  python3 paper_map.py crop --pdf /input/paper.pdf --page 5 --bbox 0.08,0.12,0.92,0.48 --out /app/work/figs/fig3.png
  python3 paper_map.py find "outperform" "Table 2" "limitation" [--materials /app/work/materials] [--max 8]
      -> matching sentences with their [pN:M] locator (copy them verbatim into facts.json)

Also detected: display equations numbered "(N)" (equations[] with crops eqA_B_pP.png: math garbled in the text
layer is shown as an image), and for tables the cells read from the text layer (table_rows, super/subscripts as
^/_ which the builder renders as real super/subscripts).

Locators use the same paragraph tags as extract_materials.py ([p3:12] = page 3, paragraph 12).
Figure regions come from text-free gaps above a figure caption (below a table caption), found with
`pdftotext -bbox-layout`, then whitespace-trimmed. They are CANDIDATES: look at figs/fig_candidates.png once and
fix a box with the crop sub-command when it is wrong. Nothing is guessed beyond what the PDF contains.
"""
import argparse, json, os, re, shutil, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

CAP_RE = re.compile(r"^\s*(Figure|Fig\.?|FIGURE|FIG\.?|Table|TABLE|Tab\.?|图|表)\s*(\d+|[IVX]+)([a-z]?)\s*[:.：|\-–—]\s*(.*)$")
SEC_WORDS = r"(Abstract|Introduction|Related Work|Background|Preliminaries|Method|Methods|Methodology|Approach|Model|" \
            r"Experiments?|Experimental Setup|Setup|Results|Evaluation|Analysis|Ablations?|Discussion|Limitations?|" \
            r"Conclusions?|Conclusion and Future Work|Future Work|References|Acknowledg(?:e)?ments?|Appendix)"
HEAD_RE = [re.compile(r"^\s*((?:\d+(?:\.\d+)*)\.?)\s+([A-Z][A-Za-z0-9 ,:;&/()'’\-–—]{2,80})$"),
           re.compile(r"^\s*([IVX]+\.)\s+([A-Z][A-Za-z0-9 ,:&/()\-–]{2,80})$"),
           re.compile(r"^\s*()" + SEC_WORDS + r"\s*[.:]?\s*$", re.I),
           re.compile(r"^\s*()" + SEC_WORDS + r"\.\s"),
           re.compile(r"^\s*((?:\d+(?:\.\d+)*)?\.?)\s*(摘要|引言|绪论|相关工作|背景|方法|模型|实验|实验设置|结果|讨论|分析|局限性?|结论|总结|参考文献)\s*$"),
           re.compile(r"^\s*([一二三四五六七八九十]+、)\s*(\S.{0,40})$")]
CUES = ["we propose", "we present", "we introduce", "our contribution", "contributions are", "we show", "outperform",
        "improv", "reduc", "increas", "achiev", "state-of-the-art", "significant", "limitation", "we find", "results show",
        "in summary", "in conclusion", "however", "fails", "cannot", "本文", "我们提出", "结果表明", "显著", "提高", "降低", "局限"]
ROLE_OF = [("abstract", "summary"), ("introduction", "motivation"), ("background", "motivation"), ("related", "context"),
           ("method", "method"), ("approach", "method"), ("model", "method"), ("preliminar", "method"),
           ("experiment", "results"), ("setup", "results"), ("result", "results"), ("evaluation", "results"),
           ("analysis", "results"), ("ablation", "results"), ("discussion", "discussion"), ("limitation", "limitations"),
           ("conclusion", "conclusion"), ("future", "conclusion"), ("reference", "references"), ("acknowledg", "other"),
           ("appendix", "appendix"), ("摘要", "summary"), ("引言", "motivation"), ("绪论", "motivation"),
           ("方法", "method"), ("模型", "method"), ("实验", "results"), ("结果", "results"), ("讨论", "discussion"),
           ("局限", "limitations"), ("结论", "conclusion"), ("总结", "conclusion"), ("参考", "references")]


def tag_paragraphs(pages):
    out = []
    for pi, page in enumerate(pages, 1):
        paras = [q.strip() for q in re.split(r"\n\s*\n", page) if q.strip()]
        for qi, q in enumerate(paras, 1):
            out.append(("p%d:%d" % (pi, qi), re.sub(r"[ \t]+", " ", q)))
    return out


def load_tagged(pdf, materials):
    cand = os.path.join(materials or "", os.path.basename(pdf) + ".txt")
    if materials and os.path.exists(cand):
        txt = open(cand, encoding="utf-8").read()
        parts = re.split(r"(?m)^\[(p\d+:\d+)\] ", txt)
        return [(parts[i], parts[i + 1].strip()) for i in range(1, len(parts) - 1, 2)], cand
    rc, out, _ = K.run(["pdftotext", pdf, "-"], timeout=120)
    if rc == 0 and out.strip():
        return tag_paragraphs(out.split("\f")), "pdftotext"
    try:
        import fitz
        return tag_paragraphs([pg.get_text() for pg in fitz.open(pdf)]), "pymupdf"
    except Exception:
        return [], "none"


def role_for(heading):
    h = heading.lower()
    for k, r in ROLE_OF:
        if k in h:
            return r
    return "other"


def bbox_pages(pdf):
    """[{w,h,blocks:[{box,lines:[{box,text,nwords}]}]}] from pdftotext -bbox-layout"""
    if not shutil.which("pdftotext"):
        return []
    rc, out, _ = K.run(["pdftotext", "-bbox-layout", pdf, "-"], timeout=180)
    if rc != 0:
        return []
    out = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", out)  # math fonts emit control chars -> invalid XML
    out = re.sub(r"<!DOCTYPE[^>]*>", "", out)
    out = re.sub(r'\sxmlns="[^"]+"', "", out, count=1)
    try:
        root = ET.fromstring(out)
    except ET.ParseError:
        return []
    pages = []
    for pg in root.iter("page"):
        P = dict(w=float(pg.get("width")), h=float(pg.get("height")), blocks=[])
        for b in pg.iter("block"):
            lines = []
            for ln in b.iter("line"):
                wl = [(w.text or "", [float(w.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")]) for w in ln.iter("word")]
                lines.append(dict(box=[float(ln.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                                  text=" ".join(t for t, _ in wl), nwords=len(wl), words=wl))
            P["blocks"].append(dict(box=[float(b.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")], lines=lines))
        pages.append(P)
    # running heads / page numbers: short lines in the top or bottom 8 % repeated on >= 2 pages, or bare numbers
    from collections import Counter
    cnt = Counter()
    for P in pages:
        seen = set()
        for b in P["blocks"]:
            for l in b["lines"]:
                if l["box"][3] < 0.08 * P["h"] or l["box"][1] > 0.92 * P["h"]:
                    key = re.sub(r"\d+", "#", l["text"].strip())
                    if key not in seen:
                        cnt[key] += 1
                        seen.add(key)
    for P in pages:
        top, bot = 0.0, P["h"]
        keep = []
        for b in P["blocks"]:
            txt = " ".join(l["text"] for l in b["lines"]).strip()
            key = re.sub(r"\d+", "#", txt)
            edge_top, edge_bot = b["box"][3] < 0.08 * P["h"], b["box"][1] > 0.92 * P["h"]
            if (edge_top or edge_bot) and (cnt[key] >= 2 or re.fullmatch(r"[\d\s\-–]+", txt) or len(b["lines"]) == 1 and cnt[key] >= 2):
                if edge_top:
                    top = max(top, b["box"][3])
                else:
                    bot = min(bot, b["box"][1])
                continue
            keep.append(b)
        P["blocks"], P["head_y"], P["foot_y"] = keep, top, bot
    # text area of the document (pages whose figure has no text above it still get a sensible top margin)
    tops = sorted(min(b["box"][1] for b in P["blocks"]) for P in pages if P["blocks"])
    bots = sorted(max(b["box"][3] for b in P["blocks"]) for P in pages if P["blocks"])
    for P in pages:
        P["text_top"] = tops[len(tops) // 4] if tops else 0.06 * P["h"]
        P["text_bot"] = bots[len(bots) * 3 // 4] if bots else 0.94 * P["h"]
    return pages


def body_width(P):
    """typical width of a running-text line on this page (one column of a two-column paper)"""
    ws = sorted(l["box"][2] - l["box"][0] for b in P["blocks"] for l in b["lines"] if l["nwords"] >= 7)
    return ws[len(ws) * 3 // 4] if ws else 0.4 * P["w"]


NUMTOK = re.compile(r"^[-+±−]?(\d+(\.\d*)?|\.\d+)%?[*†‡]?$|^[-–—]$")


def is_body_block(b, colw):
    """running text (paragraph) rather than a figure label, table row or axis text; colw = body_width(page)"""
    ls = b["lines"]
    if not ls:
        return False
    def prose(l):
        nnum = sum(1 for t, _ in l.get("words", []) if NUMTOK.match(t))
        return l["nwords"] >= 7 and (l["box"][2] - l["box"][0]) >= 0.8 * colw and nnum <= 0.3 * l["nwords"]
    if len(ls) < 2:
        t = ls[0]["text"]
        # numbered headings stop a region; a bare word like "Model"/"Method" is usually a table header cell
        return bool(any(HEAD_RE[i].match(t) for i in (0, 1, 4, 5)) or prose(ls[0]))
    body = [l for l in ls[:-1] if prose(l)]
    return len(body) >= max(1, (len(ls) - 1) * 0.6)


def split_headings(P):
    """y-tops of section headings whose number is a separate text block ("3.5" | "Positional Encoding")"""
    ys = []
    one = [b for b in P["blocks"] if len(b["lines"]) == 1]
    for b in one:
        if not re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,3}\.?", b["lines"][0]["text"].strip()):
            continue
        for o in P["blocks"]:
            t = o["lines"][0]["text"].strip() if o["lines"] else ""
            if o is not b and abs(o["box"][1] - b["box"][1]) < 2 and 0 <= o["box"][0] - b["box"][2] < 30 \
                    and t[:1].isupper() and not NUMTOK.match(t.split()[0]):
                ys.append(b["box"][1])
                break
    return ys


def words_in(P, box):
    out = []
    for b in P["blocks"]:
        for l in b["lines"]:
            for t, wb in l.get("words", []):
                cx, cy = (wb[0] + wb[2]) / 2, (wb[1] + wb[3]) / 2
                if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]:
                    out.append((t, wb))
    return out


def table_rows(P, box):
    """cells of a table region: words grouped into rows by baseline, split into cells at wide gaps;
    superscripts become ^x (1.0 · 10^20), subscripts _x. A candidate: check against the crop."""
    ws = sorted(words_in(P, box), key=lambda w: (w[1][3], w[1][0]))
    if not ws:
        return []
    hs = sorted(w[1][3] - w[1][1] for w in ws)
    h = hs[len(hs) // 2] or 8.0
    # attach super/subscripts to the word on their left
    merged = []
    for t, b in sorted(ws, key=lambda w: (w[1][0])):
        merged.append([t, list(b)])
    merged.sort(key=lambda w: w[1][0])
    base = []
    for w in merged:
        t, b = w
        small = (b[3] - b[1]) < 0.8 * h
        host = None
        if small:
            for o in base:
                ob = o[1]
                if abs(b[0] - ob[2]) < 1.5 and (ob[3] - ob[1]) >= 0.8 * h and ob[1] - 2 <= (b[1] + b[3]) / 2 <= ob[3] + 2:
                    host = o
                    break
        if host is not None:
            hb = host[1]
            mark = "^" if (b[1] + b[3]) / 2 < (hb[1] + hb[3]) / 2 - 0.5 else "_"
            host[0] += mark + t
            hb[2] = max(hb[2], b[2])
        else:
            base.append(w)
    base.sort(key=lambda w: ((w[1][1] + w[1][3]) / 2, w[1][0]))
    rows, cur, cy = [], [], None
    for t, b in base:
        y = (b[1] + b[3]) / 2
        if cy is None or abs(y - cy) <= 0.45 * h:
            cur.append((t, b))
            cy = y if cy is None else (cy + y) / 2
        else:
            rows.append(cur)
            cur, cy = [(t, b)], y
    if cur:
        rows.append(cur)
    grid = []
    for r in rows:
        r.sort(key=lambda w: w[1][0])
        cells, cell, last, x0 = [], [], None, None
        for t, b in r:
            if last is not None and b[0] - last > 0.9 * h:
                cells.append((" ".join(cell), x0, last))
                cell, x0 = [], None
            cell.append(t)
            x0 = b[0] if x0 is None else x0
            last = b[2]
        cells.append((" ".join(cell), x0, last))
        grid.append(cells)
    # align cells to the columns of the widest row (empty cells -> ""), so rows can be pasted into a slide table
    anchor = max(grid, key=len)
    if len(anchor) < 2:
        return [[c[0] for c in r] for r in grid]
    cx = [(c[1] + c[2]) / 2 for c in anchor]
    out = []
    for r in grid:
        if len(r) == len(anchor) or len(r) == 1:
            out.append([c[0] for c in r])
            continue
        row = [""] * len(anchor)
        for t, a0, a1 in r:
            k = min(range(len(cx)), key=lambda i: abs(cx[i] - (a0 + a1) / 2) if i else abs(anchor[0][1] - a0))
            row[k] = (row[k] + " " + t).strip()
        out.append(row)
    while out and HEAD_RE[0].match(" ".join(out[-1]).strip()):  # a section heading right below the table
        out.pop()
    return out


def find_region(P, label_words, kind):
    """locate caption line, return (caption_box, region_box) in PDF points or (None, None).
    Figures: region above the caption. Tables: above AND below are tried (captions sit above the table in most
    journals, below it in ICML/NeurIPS-style papers); the side with more numeric/short tokens wins."""
    W, H = P["w"], P["h"]
    cap = None
    for bi, b in enumerate(P["blocks"]):
        for li, ln in enumerate(b["lines"]):
            if re.match(r"^\s*" + re.escape(label_words) + r"\s*[:.：|\-–—]", ln["text"]) or \
                    re.match(r"^\s*" + re.escape(label_words.replace(" ", "")) + r"\s*[:.：|\-–—]", ln["text"]):
                cap = (bi, li)
                break
        if cap:
            break
    if not cap:
        return None, None
    b = P["blocks"][cap[0]]
    cl = [b["lines"][cap[1]]]
    for ln in b["lines"][cap[1] + 1:]:  # caption = following lines with paragraph spacing; table rows / labels stop it
        prev = cl[-1]
        lh = prev["box"][3] - prev["box"][1]
        ws = ln.get("words") or []
        gap = max([ws[k + 1][1][0] - ws[k][1][2] for k in range(len(ws) - 1)] or [0])
        first = cl[0]["box"]
        off_left = abs(ln["box"][0] - first[0]) > 3 * lh
        off_centre = abs((ln["box"][0] + ln["box"][2]) / 2 - (first[0] + first[2]) / 2) > 3 * lh
        if ln["box"][1] - prev["box"][3] > 0.6 * lh or CAP_RE.match(ln["text"]) or gap > 1.5 * lh or \
                (off_left and off_centre):  # a table cell / axis label, not the next caption line
            break
        cl.append(ln)
    cbox = [min(l["box"][0] for l in cl), min(l["box"][1] for l in cl), max(l["box"][2] for l in cl), max(l["box"][3] for l in cl)]
    xs0 = [bb["box"][0] for bb in P["blocks"]] or [0.07 * W]
    xs1 = [bb["box"][2] for bb in P["blocks"]] or [0.93 * W]
    left, right = min(xs0), max(xs1)
    mid = (left + right) / 2
    if body_width(P) > 0.55 * W:  # single-column page: a short centred caption still spans the full width
        col = [left, right]
    elif cbox[2] - cbox[0] < 0.55 * (right - left):
        if (cbox[0] + cbox[2]) / 2 < mid:
            col = [left, mid - 4]
        else:
            col = [mid + 4, right]
    else:
        col = [left, right]
    colw = body_width(P)
    top_margin = max(P.get("head_y", 0) + 2, min([bb["box"][1] for bb in P["blocks"]] + [P.get("text_top", 0.06 * H)]))
    bot_margin = min(P.get("foot_y", H) - 2, max([bb["box"][3] for bb in P["blocks"]] + [P.get("text_bot", 0.94 * H)]))

    def overlaps(bb):
        return bb["box"][0] < col[1] and bb["box"][2] > col[0]

    def above():
        y1 = cbox[1] - 2
        y0 = top_margin
        for bb in P["blocks"]:
            if bb is b or not overlaps(bb):
                continue
            if bb["box"][3] <= cbox[1] + 1 and is_body_block(bb, colw):
                y0 = max(y0, bb["box"][3] + 2)
        for ln in b["lines"][:cap[1]]:  # caption starts mid-block: prose lines above it in the block are text
            if ln["nwords"] >= 7 and (ln["box"][2] - ln["box"][0]) >= 0.8 * colw:
                y0 = max(y0, ln["box"][3] + 2)
        return [col[0] - 2, y0, col[1] + 2, y1] if y1 - y0 >= 12 else None

    def below():
        y0 = cbox[3] + 2
        y1 = bot_margin
        for bb in P["blocks"]:
            if bb is b or not overlaps(bb):
                continue
            if bb["box"][1] >= cbox[3] - 1 and is_body_block(bb, colw):
                y1 = min(y1, bb["box"][1] - 2)
        for hy in split_headings(P):
            if hy > y0:
                y1 = min(y1, hy - 2)
        for ln in b["lines"][cap[1] + len(cl):]:  # prose lines after the table inside the caption's block
            if ln["box"][1] > y0 and ln["nwords"] >= 7 and (ln["box"][2] - ln["box"][0]) >= 0.8 * colw and \
                    sum(1 for t, _ in ln.get("words", []) if NUMTOK.match(t)) <= 0.3 * ln["nwords"]:
                y1 = min(y1, ln["box"][1] - 2)
                break
        return [col[0] - 2, y0, col[1] + 2, y1] if y1 - y0 >= 12 else None

    if kind == "figure":
        r = above()
        return cbox, r
    cands = [r for r in (below(), above()) if r]
    if not cands:
        return cbox, None

    def score(r):
        ws = words_in(P, r)
        if not ws:
            return 0.0
        num = sum(1 for t, _ in ws if NUMTOK.match(t))
        return num + 0.2 * len(ws)
    best = max(cands, key=score)
    return cbox, best


def find_equations(P, page_no):
    """display equations = lines ending with an equation number "(N)" near a column's right edge.
    Consecutive numbered lines (gap < 3 line heights) are grouped, e.g. Eq. 3-6 of a layer definition."""
    W = P["w"]
    hits = []
    for b in P["blocks"]:
        for l in b["lines"]:
            ws = l.get("words") or []
            if not ws:
                continue
            m = re.fullmatch(r"\((\d{1,3})\)", ws[-1][0])
            if not m or l["nwords"] > 25:
                continue
            x1 = ws[-1][1][2]
            right_col = x1 > 0.76 * W or (0.40 * W < x1 < 0.52 * W)
            if not right_col:
                continue
            hits.append(dict(num=int(m.group(1)), box=list(l["box"]), x_end=x1, x_num=ws[-1][1][0]))
    hits.sort(key=lambda h: (h["x_end"] > 0.6 * W, h["box"][1]))
    groups = []
    for h in hits:
        g = groups[-1] if groups else None
        lh = h["box"][3] - h["box"][1]
        if g and (g["x_end"] > 0.6 * W) == (h["x_end"] > 0.6 * W) and h["box"][1] - g["box"][3] < 3.2 * lh \
                and h["num"] == g["nums"][-1] + 1:
            g["nums"].append(h["num"])
            g["box"] = [min(g["box"][0], h["box"][0]), g["box"][1], max(g["box"][2], h["box"][2]), h["box"][3]]
            g["x_num"] = min(g["x_num"], h["x_num"])
        else:
            groups.append(dict(nums=[h["num"]], box=list(h["box"]), x_end=h["x_end"], x_num=h["x_num"]))
    out = []
    for g in groups:
        lh = 10.0
        two_col_page = body_width(P) < 0.55 * W
        x0 = 0.5 * W if (two_col_page and g["x_end"] > 0.6 * W and g["box"][0] > 0.45 * W) else 0.05 * W
        box = [x0, g["box"][1] - 1.2 * lh, g["x_num"] - 2, g["box"][3] + 1.2 * lh]  # number "(N)" left out: ref says it
        out.append(dict(ref="Eq. %s" % ("%d-%d" % (g["nums"][0], g["nums"][-1]) if len(g["nums"]) > 1 else g["nums"][0]),
                        nums=g["nums"], page=page_no,
                        bbox=[round(max(0, box[0] / W), 4), round(max(0, box[1] / P["h"]), 4),
                              round(min(1, box[2] / W), 4), round(min(1, box[3] / P["h"]), 4)]))
    return out


_PAGE_CACHE = {}
_INK = bytes(0 if i < 235 else 255 for i in range(256))


def trim_page(pdf, page, frac_box, dpi=72):
    """tighten a fractional box to the ink inside it (pure stdlib: pdftoppm PPM + bytes.translate).
    Returns the tightened box, None when the region is blank, or the input box when no renderer exists."""
    key = (pdf, page)
    if key not in _PAGE_CACHE:
        tmp = tempfile.mkdtemp(prefix="trim_")
        try:
            _PAGE_CACHE[key] = K.pdf_page_ppm(pdf, page, os.path.join(tmp, "p"), dpi=dpi)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    img = _PAGE_CACHE[key]
    if not img:
        return frac_box
    W, H, px = img
    x0, y0 = max(0, int(frac_box[0] * W)), max(0, int(frac_box[1] * H))
    x1, y1 = min(W, int(frac_box[2] * W)), min(H, int(frac_box[3] * H))
    if x1 <= x0 or y1 <= y0:
        return None
    rows, lo, hi = [], None, None
    for y in range(y0, y1):
        r = bytes(px[(y * W + x0) * 3:(y * W + x1) * 3]).translate(_INK)
        a = len(r) - len(r.lstrip(b"\xff"))
        if a < len(r):
            b = len(r.rstrip(b"\xff"))
            rows.append(y)
            lo = a // 3 if lo is None else min(lo, a // 3)
            hi = (b - 1) // 3 if hi is None else max(hi, (b - 1) // 3)
    if not rows:
        return None
    pad = 3
    return [max(0, (x0 + lo - pad) / W), max(0, (rows[0] - pad) / H), min(1, (x0 + hi + 1 + pad) / W), min(1, (rows[-1] + 1 + pad) / H)]


def trim_white(png, frac_box):
    """tighten a fractional box by removing white margins (needs PIL or matplotlib+numpy)"""
    try:
        import numpy as np
        try:
            from PIL import Image
            im = np.asarray(Image.open(png).convert("L"), dtype=float)
        except Exception:
            import matplotlib.image as mpimg
            a = mpimg.imread(png)
            im = (a[..., :3].mean(axis=2) * 255) if a.ndim == 3 else a * 255
        H, W = im.shape
        x0, y0, x1, y1 = [int(frac_box[0] * W), int(frac_box[1] * H), int(frac_box[2] * W), int(frac_box[3] * H)]
        sub = im[y0:y1, x0:x1] < 235
        if not sub.any():
            return None
        rows, cols = np.where(sub.any(axis=1))[0], np.where(sub.any(axis=0))[0]
        pad = 4
        return [max(0, (x0 + cols[0] - pad) / W), max(0, (y0 + rows[0] - pad) / H),
                min(1, (x0 + cols[-1] + pad) / W), min(1, (y0 + rows[-1] + pad) / H)]
    except Exception:
        return frac_box


def draw_candidates(pdf, items, out_png, n_pages):
    """one overview image: pages that hold candidates, boxes drawn and labelled"""
    pages = sorted({it["page"] for it in items if it.get("bbox")})[:12]
    if not pages or not shutil.which("pdftoppm"):
        return None
    tmp = os.path.join(os.path.dirname(out_png), "_ov")
    os.makedirs(tmp, exist_ok=True)
    ims = []
    for p in pages:
        base = os.path.join(tmp, "pg%d" % p)
        K.run(["pdftoppm", "-f", str(p), "-l", str(p), "-r", "50", "-png", "-singlefile", pdf, base])
        if os.path.exists(base + ".png"):
            ims.append((p, base + ".png"))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.image as mpimg
        from matplotlib.patches import Rectangle
        n = len(ims)
        ncol = min(4, n)
        nrow = (n + ncol - 1) // ncol
        fig, axes = plt.subplots(nrow, ncol, figsize=(4 * ncol, 5.2 * nrow), dpi=80, squeeze=False)
        axes = [ax for row in axes for ax in row]
        for ax in axes[n:]:
            ax.axis("off")
        for ax, (p, f) in zip(axes, ims):
            im = mpimg.imread(f)
            H, W = im.shape[:2]
            ax.imshow(im)
            ax.set_title("page %d" % p, fontsize=11)
            ax.axis("off")
            for it in items:
                if it["page"] == p and it.get("bbox"):
                    x0, y0, x1, y1 = it["bbox"]
                    ax.add_patch(Rectangle((x0 * W, y0 * H), (x1 - x0) * W, (y1 - y0) * H, fill=False, ec="red", lw=2))
                    ax.text(x0 * W, y0 * H, it["label"], color="white", fontsize=9, va="bottom",
                            bbox=dict(fc="red", ec="none", pad=1))
        fig.tight_layout()
        fig.savefig(out_png)
        plt.close(fig)
        shutil.rmtree(tmp, ignore_errors=True)
        return out_png
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
    try:  # stdlib fallback (no matplotlib): PPM thumbnails + red boxes, PNG written with zlib
        boxes = {}
        for it in items:
            if it.get("bbox"):
                lab = it["ref"].replace("Fig. ", "FIG ").replace("Table ", "TAB ").replace("Eq. ", "EQ ")
                boxes.setdefault(it["page"], []).append(tuple(it["bbox"]) + (lab,))
        return K.sheet_from_pdf(pdf, pages, out_png, cols=4, width=420, boxes=boxes)
    except Exception:
        return None


def sentences(text):
    t = re.sub(r"-\n(\w)", r"\1", text)
    t = re.sub(r"\s*\n\s*", " ", t)
    return [s.strip() for s in re.split(r"(?<=[.!?。！？])\s+(?=[A-Z0-9“\"(一-鿿])", t) if len(s.strip()) > 25]


def main_map(a):
    paras, src = load_tagged(a.pdf, a.materials)
    if not paras:
        print(json.dumps(dict(error="no text extracted from %s (scanned PDF?): run extract_materials (OCR) first" % a.pdf)))
        return 1
    n_pages = max(int(t.split(":")[0][1:]) for t, _ in paras)
    # sections (headings may sit inside a tagged paragraph: split into segments with their own role)
    sections, cur = [], dict(heading="(front matter)", role="front", tag=paras[0][0])
    segments = []  # (tag, role, text)
    flat = [ln.strip() for _, text in paras for ln in text.split("\n") if ln.strip()]
    numbered = sum(1 for i, ln in enumerate(flat) if HEAD_RE[0].match(ln) or (
        re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,3}\.?", ln) and i + 1 < len(flat) and HEAD_RE[0].match(ln + " " + flat[i + 1])))
    last_num = [None]

    def plausible(head, ri):
        """reject list items / wrapped words / table headers that look like headings"""
        if ri in (0, 1, 4) and len(head.split()) > 9:
            return False
        if ri == 0:
            m = re.match(r"^(\d+(?:\.\d+)*)", head)
            num = tuple(int(x) for x in m.group(1).split("."))
            if num[0] > 20 or len(num) > 4:
                return False
            prev = last_num[0]
            if prev is not None and (num <= prev or num[0] > prev[0] + 1):
                return False
            last_num[0] = num
            return True
        if ri in (2, 3):
            if not head[:1].isupper():
                return False
            if numbered >= 3 and not re.match(r"(?i)(abstract|references|acknowledg|appendix)", head):
                return False
        return True
    pending = None  # a bare section number ("3.1") printed apart from its title (separate text block)
    top_roles = {}
    for tag, text in paras:
        buf = []
        for line in text.split("\n"):
            s = line.strip()
            hit = None
            if re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,3}\.?", s):
                pending = s
                continue
            if pending:
                s, line = pending + " " + s, pending + " " + line
                pending = None
            if not CAP_RE.match(s):
                for ri, rx in enumerate(HEAD_RE):
                    m = rx.match(s)
                    if m and (len(s) <= 90 or not rx.pattern.endswith("$")):
                        head = (s if rx.pattern.endswith("$") else m.group(0)).strip().rstrip(".")
                        if re.match(r"^\d+(\.\d+)*$", head) or len(head) < 3 or not plausible(head, ri):
                            continue
                        hit = head
                        break
            if hit:
                if buf:
                    segments.append((tag, cur["role"], "\n".join(buf)))
                    buf = []
                role = role_for(hit)
                mnum = re.match(r"^(\d+)((?:\.\d+)*)", hit)
                if mnum and mnum.group(2) and top_roles.get(mnum.group(1)) not in (None, "other"):
                    role = top_roles[mnum.group(1)]  # subsections inherit the section's role
                elif mnum and not mnum.group(2):
                    if role == "other" and any(x["role"] in ("motivation", "context") for x in sections) and \
                            not any(x["role"] in ("method", "results") for x in sections):
                        role = "method"  # first unnamed main section after the introduction = the method
                    top_roles[mnum.group(1)] = role
                new = dict(heading=hit[:80], role=role, tag=tag, page=int(tag.split(":")[0][1:]))
                if not (sections and sections[-1]["tag"] == tag and sections[-1]["heading"] == new["heading"]):
                    sections.append(new)
                cur = new
                if not rx.pattern.endswith("$"):
                    buf.append(s[len(m.group(0)):] if len(s) > len(m.group(0)) else "")
                continue
            buf.append(line)
        if buf:
            segments.append((tag, cur["role"], "\n".join(buf)))
    # captions + mentions
    caps, mentions = {}, {}
    for tag, text in paras:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            m = CAP_RE.match(line)
            if m:
                kind = "table" if m.group(1).lower().startswith(("tab", "表")) else "figure"
                num = m.group(2) + m.group(3)
                key = ("Table " if kind == "table" else "Figure ") + num
                if key not in caps:
                    body = " ".join([m.group(4)] + [l.strip() for l in lines[i + 1:i + 4]]).strip()
                    caps[key] = dict(label=key, ref=("Table " if kind == "table" else "Fig. ") + num, kind=kind,
                                     page=int(tag.split(":")[0][1:]), caption_loc=tag, caption=body[:400],
                                     label_in_pdf=(m.group(1) + " " + num).strip() if not m.group(1) in ("图", "表") else m.group(1) + num)
        for m in re.finditer(r"(Fig(?:ure)?s?\.?|Tables?|Tab\.|图|表)\s*(\d+)", text):
            kind = "table" if m.group(1).lower().startswith(("tab", "表")) else "figure"
            key = ("Table " if kind == "table" else "Figure ") + m.group(2)
            mentions.setdefault(key, [])
            if tag not in mentions[key]:
                mentions[key].append(tag)
    # regions
    bp = bbox_pages(a.pdf)
    os.makedirs(a.figdir, exist_ok=True)
    items = []
    for key, c in sorted(caps.items(), key=lambda kv: (kv[1]["page"], kv[0])):
        c["discussed_at"] = [t for t in mentions.get(key, []) if t != c["caption_loc"]][:6]
        if bp and c["page"] <= len(bp):
            P = bp[c["page"] - 1]
            label_words = c["label_in_pdf"]
            cbox, region = find_region(P, label_words, c["kind"])
            if region is None and label_words.startswith("Fig ") is False:
                for alt in (label_words.replace("Figure", "Fig."), label_words.replace("Fig.", "Figure")):
                    cbox, region = find_region(P, alt, c["kind"])
                    if region:
                        break
            if region and c["kind"] == "table":
                rows = table_rows(P, region)
                if rows:
                    c["table_rows"] = rows[:40]
                    c["table_note"] = ("cells read from the PDF text layer (rows by baseline, cells by gaps, ^/_ = "
                                       "super/subscript); check against the crop before copying into a slide")
            if region:
                frac = [region[0] / P["w"], region[1] / P["h"], region[2] / P["w"], region[3] / P["h"]]
                frac = [round(max(0.0, min(1.0, v)), 4) for v in frac]
                if True:
                    t = trim_page(a.pdf, c["page"], frac)
                    if t is None:
                        frac = None
                        c["region_note"] = "region between text blocks is empty (figure may be elsewhere on the page)"
                    else:
                        frac = [round(v, 4) for v in t]
                if frac:
                    c["bbox"] = frac
                    out = os.path.join(a.figdir, "%s%s_p%d.png" % ("tab" if c["kind"] == "table" else "fig",
                                                                  re.sub(r"\W", "", key.split()[-1]), c["page"]))
                    p, how = K.crop_pdf_region(a.pdf, c["page"], frac, out, a.dpi)
                    if p:
                        c["crop_png"] = p
                        c["crop_px"] = list(K.image_size(p) or [])
            else:
                c["region_note"] = "caption found but no image region detected; crop manually (crop sub-command)"
        elif not bp:
            c["region_note"] = "pdftotext -bbox-layout unavailable: no automatic crop; use the crop sub-command"
        items.append(c)
    for f in os.listdir(a.figdir):
        if f.startswith("_trim_p"):
            os.remove(os.path.join(a.figdir, f))
    equations = []
    for pi, P in enumerate(bp, 1):
        if pi > 12:
            break
        for e in find_equations(P, pi):
            outp = os.path.join(a.figdir, "eq%s_p%d.png" % ("_".join(str(n) for n in e["nums"][:1] + e["nums"][-1:]), pi))
            t = trim_page(a.pdf, pi, e["bbox"])
            if t:
                e["bbox"] = [round(v, 4) for v in t]
            pth, how = K.crop_pdf_region(a.pdf, pi, e["bbox"], outp, 250)
            if pth:
                e["crop_png"] = pth
            equations.append(e)
    for f in os.listdir(a.figdir):
        if f.startswith("_eq_p"):
            os.remove(os.path.join(a.figdir, f))
    overview = draw_candidates(a.pdf, items, os.path.join(a.figdir, "fig_candidates.png"), n_pages)
    # key sentences
    keys = []
    for tag, role, text in segments:
        if role in ("references", "appendix"):
            continue
        for s in sentences(text):
            letters = sum(ch.isalpha() for ch in s)
            if CAP_RE.match(s) or len(s.split()) < 6 and not K.has_cjk(s) or not re.search(r"[.!?。！？;；]$", s) \
                    or letters < 0.6 * len(s.replace(" ", "")):
                continue
            low = s.lower()
            score = sum(1 for c in CUES if c in low) + (1 if re.search(r"\d", s) else 0) + \
                (2 if role in ("summary", "conclusion", "limitations") else 0) + (1 if role == "results" else 0)
            if score >= 2:
                keys.append(dict(loc=tag, role=role, score=score, text=s[:300]))
    if len(keys) > a.max_key:  # keep the strongest sentences, in document order
        cut = sorted(keys, key=lambda k: -k["score"])[a.max_key - 1]["score"]
        strong = [k for k in keys if k["score"] > cut]
        keys = [k for k in keys if k["score"] > cut or (k["score"] == cut and len(strong) < a.max_key and not strong.append(k))]
    title = guess_title(a.pdf, paras)
    out = dict(pdf=a.pdf, text_source=src, n_pages=n_pages, title_guess=title, sections=sections,
               figures_tables=items, equations=equations, overview_png=overview, key_sentences=keys,
               notes=["bbox = fractions of the page (x0,y0,x1,y1); crops are candidates - check fig_candidates.png",
                      "quote text from materials/*.txt with the [pN:M] tag; never retype numbers from memory"])
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("title_guess: %s | pages: %d | text: %s" % (title[:100], n_pages, src))
    print("sections:")
    for s in sections:
        print("  [%s] %s  (%s)" % (s["tag"], s["heading"], s["role"]))
    print("figures/tables:")
    for c in items:
        print("  %s p%d [%s] %s | crop=%s %s%s" % (c["ref"], c["page"], c["caption_loc"], c["caption"][:90],
                                                   os.path.basename(c.get("crop_png", "")) or "-", c.get("region_note", ""),
                                                   (" | table_rows: %dx%d" % (len(c["table_rows"]), max(len(r) for r in c["table_rows"])))
                                                   if c.get("table_rows") else ""))
    if equations:
        print("equations (display, numbered; text layer garbles math -> show these crops as images):")
        print("  " + "; ".join("%s p%d %s" % (e["ref"], e["page"], os.path.basename(e.get("crop_png", "")) or "-")
                               for e in equations[:30]))
    print("key sentences: %d (see %s)  overview: %s" % (len(keys), a.out, overview))
    return 0


def main_find(a):
    """print sentences matching each regex with their [pN:M] locator, ready to paste as facts.json quotes"""
    files = sorted(f for f in os.listdir(a.materials) if f.endswith(".txt") and f not in ("task.md.txt", "case.json.txt"))
    for pat in a.patterns:
        rx = re.compile(pat, re.I)
        n = 0
        print("== %s" % pat)
        for fn in files:
            txt = open(os.path.join(a.materials, fn), encoding="utf-8").read()
            parts = re.split(r"(?m)^\[(p\d+:\d+)\] ", txt)
            for i in range(1, len(parts) - 1, 2):
                for sent in sentences(parts[i + 1]) or [re.sub(r"\s+", " ", parts[i + 1])]:
                    if rx.search(sent) and n < a.max:
                        print("[%s] %s :: %s" % (parts[i], fn[:-4], sent[:a.width]))
                        n += 1
        if not n:
            print("   (no match)")
    return 0


TITLE_BAD = re.compile(r"permission|licen[cs]e|copyright|arxiv|preprint|attribution|proceedings|conference on|journal of|"
                       r"vol\.|doi|https?:|©|under review|workshop|accepted|published", re.I)


def guess_title(pdf, paras):
    """Paper title: the largest-font lines of page 1 (license/arXiv stamps skipped), else PDF metadata, else the
    first short paragraph that is not boilerplate."""
    try:
        import fitz
        doc = fitz.open(pdf)
        spans = []
        for b in doc[0].get_text("dict").get("blocks", []):
            for l in b.get("lines", []):
                if l.get("dir") and abs(l["dir"][1]) > 0.5:      # rotated side stamps
                    continue
                for sp in l.get("spans", []):
                    t = sp["text"].strip()
                    if t and not TITLE_BAD.search(t):
                        spans.append((sp["size"], sp["bbox"][1], sp["bbox"][0], t))
        if spans:
            big = max(x[0] for x in spans)
            cand = " ".join(x[3] for x in sorted((x for x in spans if x[0] >= big - 0.6), key=lambda x: (round(x[1]), x[2])))
            cand = re.sub(r"\s+", " ", cand).strip()
            if 6 <= len(cand) <= 250 and len(cand.split()) <= 30:
                return cand
        md = ((doc.metadata or {}).get("title") or "").strip()
        if 6 <= len(md) <= 250 and not TITLE_BAD.search(md) and not re.search(r"\.(pdf|docx?|tex)$|^untitled|microsoft", md, re.I):
            return md
    except Exception:
        pass
    for _, p in paras[:10]:
        t = p.split("\n")[0].strip()
        if t and not TITLE_BAD.search(t) and len(t.split()) <= 25:
            return t
    return paras[0][1].split("\n")[0].strip()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "find":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("patterns", nargs="+", help="regular expressions (case-insensitive)")
        ap.add_argument("--materials", default="/app/work/materials")
        ap.add_argument("--max", type=int, default=8, help="matches per pattern")
        ap.add_argument("--width", type=int, default=320)
        return main_find(ap.parse_args())
    if len(sys.argv) > 1 and sys.argv[1] == "crop":
        ap = argparse.ArgumentParser()
        ap.add_argument("cmd")
        ap.add_argument("--pdf", required=True)
        ap.add_argument("--page", type=int, required=True)
        ap.add_argument("--bbox", default=None, help="x0,y0,x1,y1 fractions of the page; omit for the full page")
        ap.add_argument("--out", required=True)
        ap.add_argument("--dpi", type=int, default=200)
        a = ap.parse_args()
        bbox = [float(v) for v in a.bbox.split(",")] if a.bbox else None
        p, how = K.crop_pdf_region(a.pdf, a.page, bbox, a.out, a.dpi)
        print(json.dumps(dict(out=p, via=how, pixels=K.image_size(p) if p else None)))
        return 0 if p else 1
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--materials", default="/app/work/materials")
    ap.add_argument("--out", default="/app/work/paper_map.json")
    ap.add_argument("--figdir", default="/app/work/figs")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--max-key", type=int, default=40)
    return main_map(ap.parse_args())


if __name__ == "__main__":
    sys.exit(main())
