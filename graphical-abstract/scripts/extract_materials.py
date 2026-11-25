#!/usr/bin/env python3
"""Inventory and extract every input material in one call, with citable locators.

Usage:
  python3 extract_materials.py --input /input --task task.md --work /app/work [--render-pages 4]

Writes /app/work/materials/:
  <name>.txt            reading-order text, ONE PARAGRAPH PER LINE, each line starts with its locator
                        [p3:12] (page 3, paragraph 12) -> `grep -n "keyword" <name>.txt` shows the locator
  <name>.layout.txt     column-preserving text (pdftotext -layout) with "=== page N ===" markers: use it
                        for TABLES (rows stay on one line); cite tables as "Table 2 (p6)"
  pages/<name>-<n>.png  renders of the pages that carry figure/table captions (to look at originals)
  materials_index.json  one row per input file: type, pages, columns, captions, extraction method, problems
Search afterwards (prints "<file> [p3:12] <sentence>", i.e. a quote with its locator):
  python3 extract_materials.py --work /app/work --find "3.3 ng" "inhibits" "Table 2"
Two-column PDFs: the paragraph file uses reading order (columns are not interleaved), so quotes copied
from it match the checker. Superscripts are flattened by PDF text extraction ("2.3 · 1019" = 2.3×10^19).
Nothing is summarised or guessed: it only copies what the files contain and records what failed.
"""
import argparse, csv, glob, json, os, re, shutil, subprocess, sys, time

TEXT_EXT = {".txt", ".md", ".json", ".yaml", ".yml", ".tex", ".bib", ".html", ".xml"}
TABLE_EXT = {".csv", ".tsv"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def run(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", repr(e)


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\u00ad": ""}
_CW = r"(?:FIG(?:URE)?|Fig(?:ure)?|TABLE|Table|Scheme|SCHEME)\.?\s*S?\d+[A-Za-z]?"
CAP_RE = re.compile(r"(?:^|(?<=[\s.]))(" + _CW + r"\s*[:|]\s+\S|^" + _CW + r"\s*\.\s+[A-Z]|^[图表]\s*S?\d+)")


def clean(s):
    for k, v in LIGATURES.items():
        s = s.replace(k, v)
    return s


def tag_paragraphs(pages):
    """One paragraph per line: '[p3:12] text'. Lines inside a paragraph are joined with a space;
    a line ending in '-' is joined without a space (keeps 'anti-inflammatory', 'con-centration')."""
    out = []
    for pi, page in enumerate(pages, 1):
        paras = [q.strip() for q in re.split(r"\n\s*\n", clean(page)) if q.strip()]
        for qi, q in enumerate(paras, 1):
            q = re.sub(r"-\n\s*", "-", q)
            q = re.sub(r"\s*\n\s*", " ", q)
            out.append(f"[p{pi}:{qi}] " + re.sub(r"[ \t]+", " ", q))
    return "\n".join(out)


def find_captions(tagged):
    caps = []
    for line in tagged.splitlines():
        m = re.match(r"\[(p\d+:\d+)\] (.*)", line)
        if not m:
            continue
        for c in CAP_RE.finditer(m.group(2)):
            caps.append(f"[{m.group(1)}] {m.group(2)[c.start(1):c.start(1) + 150]}")
    return caps


def strip_pages(pages):
    while pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def pdf_text(path):
    if shutil.which("pdftotext"):
        rc, out, err = run(["pdftotext", path, "-"])   # reading order: two-column papers stay readable
        if rc == 0 and out.strip():
            return strip_pages(out.split("\f")), "pdftotext"
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        return [pg.get_text() for pg in doc], "pymupdf"
    except Exception:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return [(pg.extract_text() or "") for pg in pdf.pages], "pdfplumber"
    except Exception:
        pass
    return [], "none"


def find_terms(mdir, terms, width=1):
    """Print every sentence containing a term, prefixed by its locator -> ready-made verbatim quotes."""
    import glob
    files = sorted(f for f in glob.glob(os.path.join(mdir, "*.txt")) if not f.endswith(".layout.txt"))
    for term in terms:
        print(f"## {term}")
        rx = re.compile(re.escape(term).replace("\\ ", r"\s+"), re.I)
        hits = 0
        for f in files:
            for line in open(f, encoding="utf-8", errors="ignore"):
                m = re.match(r"(\[p\d+:\d+\]) (.*)", line.rstrip("\n"))
                if not m:
                    continue
                sents = re.split(r"(?<=[.;!?])\s+(?=[A-Z(\[])", m.group(2))
                for i, snt in enumerate(sents):
                    if rx.search(snt):
                        q = " ".join(sents[max(0, i - width + 1):i + width])
                        print(f"{os.path.basename(f)[:-4]} {m.group(1)} {q[:400]}")
                        hits += 1
                        if hits >= 8:
                            break
                if hits >= 8:
                    break
        if not hits:
            print(f"NOT FOUND: {term!r} (try a shorter term, other spelling, or the .layout.txt for tables)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--task", default=None)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--render-pages", type=int, default=6, help="max pages rendered (caption pages first)")
    ap.add_argument("--find", nargs="+", default=None,
                    help="search already extracted materials: print each sentence containing a term with its locator")
    a = ap.parse_args()
    mdir = os.path.join(a.work, "materials")
    if a.find:
        find_terms(mdir, a.find)
        return 0
    os.makedirs(os.path.join(mdir, "pages"), exist_ok=True)
    files = []
    for root, _, names in os.walk(a.input):
        for n in sorted(names):
            files.append(os.path.join(root, n))
    task = a.task if (a.task and os.path.exists(a.task)) else next(
        (c for c in (os.path.join(a.input, "task.md"), "task.md", "/app/task.md", "/app/work/task.md",
                     "/input/task.md") if os.path.exists(c)), None)
    if task and os.path.abspath(task) not in {os.path.abspath(f) for f in files}:
        files.append(task)
    index = []
    for p in files:
        name = os.path.basename(p)
        ext = os.path.splitext(name)[1].lower()
        row = dict(file=p, bytes=os.path.getsize(p), type=ext.lstrip("."), problems=[])
        try:
            if ext == ".pdf":
                pages, method = pdf_text(p)
                row.update(pages=len(pages), method=method)
                if not pages or not "".join(pages).strip():
                    row["problems"].append("no text layer: use OCR (tesseract) on rendered pages")
                    if shutil.which("pdftoppm") and shutil.which("tesseract"):
                        rc, _, _ = run(["pdftoppm", "-r", "150", "-png", "-l", "8", p, os.path.join(mdir, "pages", "ocr")])
                        texts = []
                        for img in sorted(f for f in os.listdir(os.path.join(mdir, "pages")) if f.startswith("ocr")):
                            rc, out, _ = run(["tesseract", os.path.join(mdir, "pages", img), "-", "-l", "eng+chi_sim"])
                            texts.append(out)
                        pages, row["method"] = texts, "ocr"
                tagged = tag_paragraphs(pages)
                txt_path = os.path.join(mdir, name + ".txt")
                open(txt_path, "w", encoding="utf-8").write(tagged)
                row.update(text_file=txt_path, paragraphs=tagged.count("\n") + 1, words=len(tagged.split()))
                if shutil.which("pdftotext") and row.get("method") == "pdftotext":
                    rc, lay, _ = run(["pdftotext", "-layout", p, "-"])
                    if rc == 0:
                        lp = strip_pages(lay.split("\f"))
                        open(os.path.join(mdir, name + ".layout.txt"), "w", encoding="utf-8").write(
                            "".join(f"=== page {i} ===\n{clean(pg)}\n" for i, pg in enumerate(lp, 1)))
                        row["layout_file"] = os.path.join(mdir, name + ".layout.txt")
                caps = find_captions(tagged)
                row["captions"] = caps[:30]
                cap_pages = sorted({int(re.match(r"\[p(\d+)", c).group(1)) for c in caps})
                if shutil.which("pdftoppm") and a.render_pages > 0:
                    targets = (cap_pages or list(range(1, len(pages) + 1)))[:a.render_pages]
                    for pg in targets:
                        run(["pdftoppm", "-r", "90", "-png", "-f", str(pg), "-l", str(pg), p,
                             os.path.join(mdir, "pages", os.path.splitext(name)[0])])
                    row["rendered_pages"] = targets
            elif ext in TABLE_EXT:
                delim = "\t" if ext == ".tsv" else ","
                with open(p, newline="", encoding="utf-8-sig") as f:
                    rows = list(csv.reader(f, delimiter=delim))
                row.update(columns=rows[0] if rows else [], n_rows=max(0, len(rows) - 1), head=rows[1:6])
                empties = sum(1 for r in rows[1:] for c in r if c.strip() in ("", "NA", "NaN", "nan", "-"))
                if empties:
                    row["problems"].append(f"{empties} empty/NA cells")
            elif ext in (".xlsx", ".xls"):
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(p, read_only=True, data_only=True)
                    sheets = {}
                    for ws in wb.worksheets:
                        vals = [[("" if c is None else str(c)) for c in r] for r in ws.iter_rows(max_row=6, values_only=True)]
                        sheets[ws.title] = vals
                        out_csv = os.path.join(mdir, f"{os.path.splitext(name)[0]}__{ws.title}.csv")
                        with open(out_csv, "w", newline="", encoding="utf-8") as f:
                            w = csv.writer(f)
                            for r in ws.iter_rows(values_only=True):
                                w.writerow(["" if c is None else c for c in r])
                    row.update(sheets=sheets, method="openpyxl (each sheet exported to CSV)")
                except Exception as e:
                    row["problems"].append(f"xlsx read failed: {e!r}")
            elif ext in TEXT_EXT:
                txt = open(p, encoding="utf-8", errors="ignore").read()
                open(os.path.join(mdir, name + ".txt"), "w", encoding="utf-8").write(tag_paragraphs([txt]))
                row["chars"] = len(txt)
            elif ext == ".svg":
                here = os.path.dirname(os.path.abspath(__file__))
                rc, out, err = run([sys.executable, os.path.join(here, "svg_edit.py"), "inventory", "--svg", p,
                                    "--out", os.path.join(mdir, name + ".inventory.json")])
                row["method"] = "svg_edit inventory"
                row["inventory_head"] = out.splitlines()[:8]
            elif ext in IMG_EXT:
                try:
                    import matplotlib.image as mpimg
                    im = mpimg.imread(p)
                    row["pixels"] = list(im.shape[:2][::-1])
                except Exception as e:
                    row["problems"].append(f"image read failed: {e!r}")
            elif ext in (".pptx", ".docx"):
                row["problems"].append("office file: convert with soffice --headless --convert-to pdf, then rerun")
        except Exception as e:
            row["problems"].append(repr(e))
        index.append(row)
    tools = {t: bool(shutil.which(t)) for t in ("pdftotext", "pdftoppm", "tesseract", "rsvg-convert", "inkscape",
                                                 "soffice", "chromium", "fc-list", "pdflatex", "xelatex")}
    out = dict(n_files=len(index), files=index, tools=tools, materials_dir=mdir)
    json.dump(out, open(os.path.join(mdir, "materials_index.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(dict(n_files=len(index), tools=tools), ensure_ascii=False))
    for r in index:
        print(json.dumps({k: r[k] for k in r if k in ("file", "type", "pages", "words", "text_file", "layout_file",
                                                      "columns", "n_rows", "method", "problems")},
                         ensure_ascii=False))
        for c in r.get("captions", [])[:25]:
            print("   CAPTION", c)
    print("NEXT: grep -n -i '<keyword>' %s/*.txt  (each hit line starts with its locator [pX:Y]);"
          " tables: grep -n -A15 'Table N' %s/*.layout.txt" % (mdir, mdir))
    start_draft(a)
    return 0


def start_draft(a):
    """Clock for the [budget] lines + a background safety draft (fig_draft.py) so a deliverable exists early."""
    if os.environ.get("FIG_DRAFT"):
        return
    t0p = os.path.join(a.work, ".t0")
    if not os.path.exists(t0p):
        open(t0p, "w").write(str(time.time()))
    if os.path.exists(os.path.join(a.work, ".final_started")) or not os.path.exists(os.path.join(a.input, "case.json")):
        return
    try:
        outdir = os.environ.get("FIG_OUTDIR", "/app/output")
        try:
            skill = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md"), encoding="utf-8").read(400)
        except Exception:
            skill = ""
        chart = "chart" in skill and bool(glob.glob(os.path.join(a.input, "**", "*.csv"), recursive=True))
        p = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fig_draft.py"),
                              "--work", a.work, "--input", a.input, "--outdir", outdir],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        open(os.path.join(a.work, "draft.pid"), "w").write(str(p.pid))
        print("SAFETY DRAFT: %s/result.json (status partial%s) is written in the background within a minute as a "
              "fallback; your ga_make.py run replaces it." % (outdir, ", with a chart bound to the CSV" if chart else ""))
    except Exception as e:
        print("WARN safety draft not started: %r" % e)


if __name__ == "__main__":
    sys.exit(main())
