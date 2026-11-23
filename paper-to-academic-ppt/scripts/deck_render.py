#!/usr/bin/env python3
"""deck_render.py - render a .pptx to PDF + one PNG per slide (+ contact sheet) and run layout/font checks.

Usage:
  python3 deck_render.py --pptx /app/output/deck.pptx --outdir /app/output [--name deck] [--dpi 110]
         [--min-body-pt 12] [--expect-slides 12] [--no-png]

Rendering: LibreOffice (`soffice --headless --convert-to pdf`, private profile) -> PDF; `pdftoppm` (or PyMuPDF)
-> previews/slide-NN.png; previews/contact_sheet.png shows all slides in one image (look at it once).
Checks (render_report.json, level fail|warn):
  slide count pptx vs PDF, empty slides, estimated text overflow per text shape (wrap simulation at the
  effective font size, template inheritance resolved), body text < min pt (captions/footers >= 9-10 pt),
  shapes outside the slide, overlapping content shapes, stretched pictures, missing speaker notes,
  fonts: theme + hard-coded fonts resolved with fc-match (available / metric-compatible / substituted),
  fonts actually embedded in the PDF (pdffonts), slide titles findable in the PDF text layer.
Missing tools are reported as such (status render_failed / previews_missing); nothing is faked.
"""
import argparse, json, os, re, shutil, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

AUX_PREFIX = ("Caption", "Source Note", "Footer", "Slide Number", "Date")


def norm(s):
    return re.sub(r"\s+", "", str(s)).lower()


def contact_sheet(pngs, out, cols=4, thumb_w=420):
    if not pngs:
        return None
    try:
        from PIL import Image, ImageDraw
        ims = [Image.open(p).convert("RGB") for p in pngs]
        w, h = ims[0].size
        th = int(thumb_w * h / w)
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (thumb_w + 10) + 10, rows * (th + 28) + 10), "white")
        d = ImageDraw.Draw(sheet)
        for i, im in enumerate(ims):
            r, c = divmod(i, cols)
            x, y = 10 + c * (thumb_w + 10), 10 + r * (th + 28)
            sheet.paste(im.resize((thumb_w, th)), (x, y + 18))
            d.rectangle([x, y + 18, x + thumb_w - 1, y + 18 + th - 1], outline=(160, 160, 160))
            d.text((x, y + 2), "slide %d" % (i + 1), fill=(0, 0, 0))
        sheet.save(out)
        return out
    except Exception:
        pass
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.image as mpimg
        rows = (len(pngs) + cols - 1) // cols
        fig, axes = plt.subplots(rows, cols, figsize=(cols * 4, rows * 2.5), dpi=100)
        axes = axes.flatten() if hasattr(axes, "flatten") else [axes]
        for i, ax in enumerate(axes):
            ax.axis("off")
            if i < len(pngs):
                ax.imshow(mpimg.imread(pngs[i]))
                ax.set_title("slide %d" % (i + 1), fontsize=9)
        fig.tight_layout()
        fig.savefig(out)
        plt.close(fig)
        return out
    except Exception:
        return None


def pdf_to_pngs(pdf, outdir, dpi):
    os.makedirs(outdir, exist_ok=True)
    for f in os.listdir(outdir):
        if f.startswith("slide-") and f.endswith(".png"):
            os.remove(os.path.join(outdir, f))
    if shutil.which("pdftoppm"):
        rc, out, err = K.run(["pdftoppm", "-r", str(dpi), "-png", pdf, os.path.join(outdir, "slide")], timeout=300)
        files = sorted(f for f in os.listdir(outdir) if f.startswith("slide-") and f.endswith(".png"))
        res = []
        for f in files:
            n = int(re.findall(r"(\d+)", f)[-1])
            dst = os.path.join(outdir, "slide-%02d.png" % n)
            if os.path.join(outdir, f) != dst:
                os.replace(os.path.join(outdir, f), dst)
            res.append(dst)
        if res:
            return sorted(res), "pdftoppm"
    try:
        import fitz
        doc = fitz.open(pdf)
        res = []
        for i, pg in enumerate(doc, 1):
            p = os.path.join(outdir, "slide-%02d.png" % i)
            pg.get_pixmap(dpi=dpi).save(p)
            res.append(p)
        return res, "pymupdf"
    except Exception as e:
        return [], "no rasteriser (pdftoppm/PyMuPDF): %r" % (e,)


def pdf_fonts(pdf):
    if not shutil.which("pdffonts"):
        return None
    rc, out, _ = K.run(["pdffonts", pdf], timeout=60)
    fonts = []
    for line in out.splitlines()[2:]:
        parts = line.split()
        if len(parts) >= 6:
            fonts.append(dict(name=parts[0].split("+")[-1], embedded=parts[-5] == "yes", subset=parts[-4] == "yes"))
    return fonts


CJK_NAME = re.compile(r"(?i)cjk|hei|song|kai|ming|gothic|pingfang|yahei|wenquan|wqy|hiragino|sourcehan|notosans(sc|tc|jp|kr)|"
                      r"notoserif(sc|tc)|droidsansfallback|arialunicode|simsun|fangsong|stxihei|heiti|lisong|mincho|batang|gulim|malgun")


def cjk_font_embedded(embedded):
    names = {e["name"].replace(" ", "").lower() for e in embedded}
    if shutil.which("fc-list"):
        rc, out, _ = K.run(["fc-list", ":lang=zh", "postscriptname"], timeout=20)
        ps = {l.split("=")[-1].strip().replace(" ", "").lower() for l in out.splitlines() if l.strip()}
        if names & ps:
            return True
    return any(CJK_NAME.search(n) for n in names)


def pdf_page_texts(pdf):
    if shutil.which("pdftotext"):
        rc, out, _ = K.run(["pdftotext", "-layout", pdf, "-"], timeout=120)
        if rc == 0:
            return out.split("\f")
    try:
        import fitz
        return [pg.get_text() for pg in fitz.open(pdf)]
    except Exception:
        return None


def check_deck(pptx, min_body, issues):
    pkg = K.Package(pptx)
    cat = K.catalog(pkg)
    inv = K.inventory(pkg, cat)
    W, H = inv["slide_w"], inv["slide_h"]
    tol = int(0.05 * K.EMU_IN)
    per = []
    used_fonts = {}
    th = inv.get("theme", {}).get("fonts", {})
    for k, v in th.items():
        if v and not k.endswith("_cs"):
            used_fonts.setdefault(v, set()).add("theme:" + k)

    def add(level, slide, msg, shape=None):
        issues.append(dict(level=level, slide=slide, shape=shape, msg=msg))
    for s in inv["slides"]:
        i = s["index"]
        texts = [sh for sh in s["shapes"] if sh.get("text", "").strip()]
        pics = [sh for sh in s["shapes"] if sh["kind"] == "picture"]
        tables = [sh for sh in s["shapes"] if sh["kind"] in ("table", "chart", "graphic")]
        if not texts and not pics and not tables:
            add("fail", i, "empty slide (no text, picture or table)")
        if not s["notes"].strip():
            add("warn", i, "no speaker notes")
        mins = []
        for sh in s["shapes"]:
            aux = sh.get("aux") or sh["name"].startswith(AUX_PREFIX)
            b = sh.get("box")
            if b and (b[0] < -tol or b[1] < -tol or b[0] + b[2] > W + tol or b[1] + b[3] > H + tol):
                add("fail", i, "shape extends outside the slide", sh["name"])
            for p in sh.get("paras", []):
                for r in p["runs"]:
                    for k in ("latin", "ea"):
                        if r.get(k) and not r[k].startswith("+"):
                            used_fonts.setdefault(r[k], set()).add("run")
            mf = sh.get("min_font_pt")
            if mf and sh.get("text", "").strip():
                mins.append(mf)
                floor = 9 if sh.get("ph_type") in ("ftr", "sldNum", "dt") else (10 if (aux or sh["kind"] == "table") else min_body)
                if mf < floor:
                    add("fail" if not aux else "warn", i, "font %.1f pt below %s pt minimum" % (mf, floor), sh["name"])
                elif sh["kind"] == "table" and mf < min_body:
                    add("warn", i, "table font %.1f pt (< %s pt body size)" % (mf, min_body), sh["name"])
            ov = K.estimate_overflow(sh) if sh["kind"] != "table" else None
            if ov:
                need, avail = ov
                if sh.get("autofit") == "normAutofit" and need > avail:
                    add("warn", i, "text relies on shrink-on-overflow (needs %.0f of %.0f pt): renderer may shrink it" % (need, avail), sh["name"])
                elif need > avail * 1.08:
                    add("fail", i, "estimated text overflow: needs %.0f pt, box %.0f pt" % (need, avail), sh["name"])
                elif need > avail:
                    add("warn", i, "text close to box limit (%.0f of %.0f pt)" % (need, avail), sh["name"])
            if sh["kind"] == "picture" and sh.get("image_part") and b:
                try:
                    data = pkg.read(sh["image_part"])
                    import tempfile
                    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(sh["image_part"])[1], delete=False) as tf:
                        tf.write(data)
                    px = K.image_size(tf.name)
                    os.unlink(tf.name)
                    if px:
                        cr = sh.get("src_rect", {})
                        fw = 1 - (cr.get("l", 0) + cr.get("r", 0)) / 100000.0
                        fh = 1 - (cr.get("t", 0) + cr.get("b", 0)) / 100000.0
                        img_ar = (px[0] * fw) / max(1e-6, px[1] * fh)
                        box_ar = b[2] / max(1, b[3])
                        if abs(box_ar / img_ar - 1) > 0.02:
                            add("fail", i, "picture stretched: box aspect %.2f vs image %.2f" % (box_ar, img_ar), sh["name"])
                except Exception:
                    pass
        content = [sh for sh in s["shapes"] if sh.get("box") and not (sh.get("aux") or sh["name"].startswith(AUX_PREFIX))
                   and (sh.get("text", "").strip() or sh["kind"] in ("picture", "table", "chart"))]
        for a_i in range(len(content)):
            for b_i in range(a_i + 1, len(content)):
                A_, B_ = content[a_i]["box"], content[b_i]["box"]
                ix = max(0, min(A_[0] + A_[2], B_[0] + B_[2]) - max(A_[0], B_[0]))
                iy = max(0, min(A_[1] + A_[3], B_[1] + B_[3]) - max(A_[1], B_[1]))
                small = min(A_[2] * A_[3], B_[2] * B_[3]) or 1
                if ix * iy > 0.15 * small:
                    add("warn", i, "shapes overlap (%s / %s)" % (content[a_i]["name"], content[b_i]["name"]))
        per.append(dict(slide=i, layout=s["layout"], title=s["title"][:120], n_text_shapes=len(texts), n_pictures=len(pics),
                        n_tables=len(tables), notes_chars=len(s["notes"]), min_font_pt=min(mins) if mins else None))
    fonts = []
    for f, where in sorted(used_fonts.items()):
        st = K.font_status(f)
        fonts.append(dict(font=f, used_as=sorted(where), status=st))
    return inv, per, fonts


def resolve_fonts(fonts, embedded, issues, deck_has_cjk):
    """compare requested fonts with what the PDF really embeds"""
    emb = {re.sub(r"mt$", "", re.split(r"[-,]", e["name"])[0].replace(" ", "").lower()) for e in (embedded or [])}
    for f in fonts:
        key = f["font"].replace(" ", "").lower()
        twin = K.METRIC_TWINS.get(f["font"].lower(), "").replace(" ", "")
        if embedded is None:
            f["in_pdf"] = None
        elif key in emb:
            f["in_pdf"] = "embedded"
        elif twin and twin in emb:
            f["in_pdf"] = "metric-compatible substitute " + twin
            issues.append(dict(level="warn", slide=None, shape=None, msg="font '%s' rendered with metric-compatible %s "
                               "(same widths, layout preserved)" % (f["font"], twin)))
        else:
            is_ea = any("_ea" in u for u in f["used_as"])
            f["in_pdf"] = "not embedded"
            if not is_ea or deck_has_cjk:
                issues.append(dict(level="warn", slide=None, shape=None, msg="font '%s' not found in the PDF (renderer "
                                   "substituted it; embedded: %s): line breaks may differ" % (f["font"], ", ".join(sorted(emb))[:200])))
        if f["status"].startswith("substituted"):
            issues.append(dict(level="warn", slide=None, shape=None, msg="font '%s' not installed (fc-match -> %s)" % (f["font"], f["status"])))
    return fonts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--name", default=None)
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--min-body-pt", type=float, default=12)
    ap.add_argument("--expect-slides", type=int, default=None)
    ap.add_argument("--no-png", action="store_true")
    ap.add_argument("--no-font-link", action="store_true", help="do not hand font files to LibreOffice's private profile")
    ap.add_argument("--report", default=None)
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    name = a.name or os.path.splitext(os.path.basename(a.pptx))[0]
    issues = []
    inv, per, fonts = check_deck(a.pptx, a.min_body_pt, issues)
    n = inv["n_slides"]
    if a.expect_slides and n != a.expect_slides:
        issues.append(dict(level="fail", slide=None, shape=None, msg="deck has %d slides, task expects %d" % (n, a.expect_slides)))
    tools = dict(soffice=K.find_soffice(), pdftoppm=shutil.which("pdftoppm"), pdftotext=shutil.which("pdftotext"),
                 pdffonts=shutil.which("pdffonts"), fc_match=shutil.which("fc-match"))
    if tools["soffice"]:
        rc, out, err = K.run([tools["soffice"], "--version"], timeout=60)
        tools["soffice_version"] = (out or err).strip()[:80]
    tmpdir = os.path.join(a.outdir, "_render_tmp")
    has_cjk = any(K.has_cjk(sh.get("text", "")) for s in inv["slides"] for sh in s["shapes"])
    ffiles = K.font_files([f["font"] for f in fonts], want_cjk=has_cjk) if not a.no_font_link else []
    pdf, log = K.soffice_convert(os.path.abspath(a.pptx), tmpdir, "pdf", fonts=ffiles)
    pdf_path, pngs, raster, pdf_pages, embedded = None, [], None, None, None
    tools["fonts_linked_into_renderer"] = ffiles
    if pdf:
        pdf_path = os.path.join(a.outdir, name + ".pdf")
        os.replace(pdf, pdf_path)
        texts = pdf_page_texts(pdf_path)
        if texts is not None:
            pages = [t for t in texts]
            if pages and not pages[-1].strip():
                pages = pages[:-1]
            pdf_pages = len(pages)
            for s in inv["slides"]:
                t = s["title"]
                if t and s["index"] <= len(pages) and norm(t)[:20] not in norm(pages[s["index"] - 1]):
                    issues.append(dict(level="warn", slide=s["index"], shape="Title",
                                       msg="title text not found in PDF text layer (font/glyph problem or text cut off?)"))
        if shutil.which("pdfinfo"):
            rc, out, _ = K.run(["pdfinfo", pdf_path], timeout=30)
            m = re.search(r"Pages:\s+(\d+)", out)
            if m:
                pdf_pages = int(m.group(1))
        if pdf_pages is not None and pdf_pages != n:
            issues.append(dict(level="fail", slide=None, shape=None, msg="PDF has %s pages but deck has %d slides" % (pdf_pages, n)))
        embedded = pdf_fonts(pdf_path)
        resolve_fonts(fonts, embedded, issues, has_cjk)
        if has_cjk and embedded is not None and not cjk_font_embedded(embedded):
            issues.append(dict(level="fail", slide=None, shape=None, msg="Chinese/CJK text was rendered without any CJK font "
                               "(glyphs missing in PDF/PNG). Install or provide a CJK font (fc-list :lang=zh) and re-render; "
                               "the .pptx itself is unaffected"))
        if not a.no_png:
            pngs, raster = pdf_to_pngs(pdf_path, os.path.join(a.outdir, "previews"), a.dpi)
            if not pngs:
                issues.append(dict(level="warn", slide=None, shape=None, msg="PDF made but no PNG previews: " + str(raster)))
    else:
        issues.append(dict(level="fail", slide=None, shape=None, msg="PDF export failed: " + log))
    shutil.rmtree(tmpdir, ignore_errors=True)
    sheet = contact_sheet(pngs, os.path.join(a.outdir, "previews", "contact_sheet.png")) if pngs else None
    fails = [x for x in issues if x["level"] == "fail"]
    status = "render_failed" if not pdf_path else ("needs_fix" if fails else "ok")
    font_notes = [x["msg"] for x in issues if "font '" in x["msg"]]
    rep = dict(status=status, pptx=os.path.abspath(a.pptx), pptx_sha256=K.sha256(a.pptx), n_slides=n,
               pdf=pdf_path, pdf_pages=pdf_pages, pdf_sha256=K.sha256(pdf_path) if pdf_path else None,
               previews=pngs, contact_sheet=sheet, rasteriser=raster, dpi=a.dpi, tools=tools, convert_log=log[-300:],
               slide_size_in=[K.to_in(inv["slide_w"]), K.to_in(inv["slide_h"])], fonts=fonts, pdf_fonts=embedded,
               font_notes=font_notes, theme=inv.get("theme"), layouts=inv.get("layouts"), per_slide=per, issues=issues,
               n_fail=len(fails), n_warn=len(issues) - len(fails))
    rp = a.report or os.path.join(a.outdir, "render_report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=status, slides=n, pdf_pages=pdf_pages, pngs=len(pngs), contact_sheet=sheet,
                          fails=len(fails), warns=len(issues) - len(fails), report=rp), ensure_ascii=False))
    for x in issues[:30]:
        print("  %s slide %s %s: %s" % (x["level"].upper(), x["slide"], x.get("shape") or "", x["msg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
