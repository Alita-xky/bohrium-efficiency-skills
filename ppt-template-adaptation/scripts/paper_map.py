#!/usr/bin/env python3
"""paper_map.py - a compact, citable map of a paper PDF: title, section headings, figure/table captions with
page + crop box, where each figure/table is discussed, and key sentences (claims, numbers, limitations).

Usage:
  python3 paper_map.py --pdf /input/paper.pdf --materials /app/work/materials --out /app/work/paper_map.json \
         [--figdir /app/work/figs] [--dpi 200]
  python3 paper_map.py crop --pdf /input/paper.pdf --page 5 --bbox 0.08,0.12,0.92,0.48 --out /app/work/figs/fig3.png

Locators use the same paragraph tags as extract_materials.py ([p3:12] = page 3, paragraph 12).
Figure regions come from text-free gaps above a figure caption (below a table caption), found with
`pdftotext -bbox-layout`, then whitespace-trimmed. They are CANDIDATES: look at figs/fig_candidates.png once and
fix a box with the crop sub-command when it is wrong. Nothing is guessed beyond what the PDF contains.
"""
import argparse, json, os, re, shutil, subprocess, sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

CAP_RE = re.compile(r"^\s*(Figure|Fig\.?|FIGURE|FIG\.?|Table|TABLE|Tab\.?|图|表)\s*(\d+|[IVX]+)([a-z]?)\s*[:.：|\-–—]\s*(.*)$")
SEC_WORDS = r"(Abstract|Introduction|Related Work|Background|Preliminaries|Method|Methods|Methodology|Approach|Model|" \
            r"Experiments?|Experimental Setup|Setup|Results|Evaluation|Analysis|Ablations?|Discussion|Limitations?|" \
            r"Conclusions?|Conclusion and Future Work|Future Work|References|Acknowledg(?:e)?ments?|Appendix)"
HEAD_RE = [re.compile(r"^\s*((?:\d+(?:\.\d+)*)\.?)\s+([A-Z][A-Za-z0-9 ,:&/()\-–]{2,80})$"),
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
    rc, out, _ = K.run(["pdftotext", "-layout", pdf, "-"], timeout=120)
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
                words = [w.text or "" for w in ln.iter("word")]
                lines.append(dict(box=[float(ln.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                                  text=" ".join(words), nwords=len(words)))
            P["blocks"].append(dict(box=[float(b.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")], lines=lines))
        pages.append(P)
    return pages


def is_body_block(b, colw):
    ls = b["lines"]
    if len(ls) < 2:
        t = ls[0]["text"] if ls else ""
        return any(r.match(t) for r in HEAD_RE) or (ls and ls[0]["nwords"] >= 9 and (ls[0]["box"][2] - ls[0]["box"][0]) > 0.6 * colw)
    body = [l for l in ls[:-1] if l["nwords"] >= 7 and (l["box"][2] - l["box"][0]) >= 0.55 * colw]
    return len(body) >= max(1, (len(ls) - 1) * 0.6)


def find_region(P, label_words, kind):
    """locate caption line, return (caption_box, region_box) in PDF points or (None, None)"""
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
    cl = b["lines"][cap[1]:]
    cbox = [min(l["box"][0] for l in cl), min(l["box"][1] for l in cl), max(l["box"][2] for l in cl), max(l["box"][3] for l in cl)]
    xs0 = [bb["box"][0] for bb in P["blocks"]] or [0.07 * W]
    xs1 = [bb["box"][2] for bb in P["blocks"]] or [0.93 * W]
    left, right = min(xs0), max(xs1)
    mid = (left + right) / 2
    if cbox[2] - cbox[0] < 0.55 * (right - left):
        if (cbox[0] + cbox[2]) / 2 < mid:
            col = [left, mid - 4]
        else:
            col = [mid + 4, right]
    else:
        col = [left, right]
    colw = col[1] - col[0]
    top_margin = min(bb["box"][1] for bb in P["blocks"]) if P["blocks"] else 0.06 * H
    bot_margin = max(bb["box"][3] for bb in P["blocks"]) if P["blocks"] else 0.94 * H

    def overlaps(bb):
        return bb["box"][0] < col[1] and bb["box"][2] > col[0]
    if kind == "figure":
        y1 = cbox[1] - 2
        y0 = top_margin
        for bb in P["blocks"]:
            if bb is b or not overlaps(bb):
                continue
            if bb["box"][3] <= cbox[1] + 1 and is_body_block(bb, colw):
                y0 = max(y0, bb["box"][3] + 2)
        if b["lines"][0] is not b["lines"][cap[1]]:  # caption starts mid-block: lines above in block are text
            y0 = max(y0, b["lines"][cap[1] - 1]["box"][3] + 2)
    else:
        y0 = cbox[3] + 2
        y1 = bot_margin
        for bb in P["blocks"]:
            if bb is b or not overlaps(bb):
                continue
            if bb["box"][1] >= cbox[3] - 1 and is_body_block(bb, colw):
                y1 = min(y1, bb["box"][1] - 2)
    if y1 - y0 < 12:
        return cbox, None
    return cbox, [col[0] - 2, y0, col[1] + 2, y1]


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
    pages = sorted({it["page"] for it in items if it.get("bbox")})[:8]
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
        fig, axes = plt.subplots(1, n, figsize=(4 * n, 5.4), dpi=80)
        axes = [axes] if n == 1 else list(axes)
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
    for tag, text in paras:
        buf = []
        for line in text.split("\n"):
            s = line.strip()
            hit = None
            if not CAP_RE.match(s):
                for rx in HEAD_RE:
                    m = rx.match(s)
                    if m and (len(s) <= 90 or not rx.pattern.endswith("$")):
                        head = (s if rx.pattern.endswith("$") else m.group(0)).strip().rstrip(".")
                        if re.match(r"^\d+(\.\d+)*$", head) or len(head) < 3:
                            continue
                        hit = head
                        break
            if hit:
                if buf:
                    segments.append((tag, cur["role"], "\n".join(buf)))
                    buf = []
                new = dict(heading=hit[:80], role=role_for(hit), tag=tag, page=int(tag.split(":")[0][1:]))
                if not (sections and sections[-1]["tag"] == tag and sections[-1]["role"] == new["role"]):
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
            if region:
                frac = [region[0] / P["w"], region[1] / P["h"], region[2] / P["w"], region[3] / P["h"]]
                frac = [round(max(0.0, min(1.0, v)), 4) for v in frac]
                prev = os.path.join(a.figdir, "_trim_p%d.png" % c["page"])
                if not os.path.exists(prev):
                    K.run(["pdftoppm", "-f", str(c["page"]), "-l", str(c["page"]), "-r", "60", "-png", "-singlefile",
                           a.pdf, prev[:-4]])
                if os.path.exists(prev):
                    t = trim_white(prev, frac)
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
        items.append(c)
    for f in os.listdir(a.figdir):
        if f.startswith("_trim_p"):
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
    title = paras[0][1].split("\n")[0].strip()
    out = dict(pdf=a.pdf, text_source=src, n_pages=n_pages, title_guess=title, sections=sections,
               figures_tables=items, overview_png=overview, key_sentences=keys,
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
        print("  %s p%d [%s] %s | crop=%s %s" % (c["ref"], c["page"], c["caption_loc"], c["caption"][:90],
                                                 os.path.basename(c.get("crop_png", "")) or "-", c.get("region_note", "")))
    print("key sentences: %d (see %s)  overview: %s" % (len(keys), a.out, overview))
    return 0


def main():
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
