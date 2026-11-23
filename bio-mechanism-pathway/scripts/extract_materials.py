#!/usr/bin/env python3
"""Inventory and extract every input material in one call, with citable locators.

Usage:
  python3 extract_materials.py --input /input --task task.md --work /app/work [--render-pages 4]

Writes /app/work/materials/:
  <name>.txt            text per file, paragraphs tagged  [p3:12]  (page 3, paragraph 12)
  pages/<name>-<n>.png  low-res renders of the first PDF pages (to look at original figures)
  materials_index.json  one row per input file: type, pages, columns, sizes, extraction method, problems
Nothing is summarised or guessed: it only copies what the files contain and records what failed.
"""
import argparse, csv, json, os, re, shutil, subprocess, sys

TEXT_EXT = {".txt", ".md", ".json", ".yaml", ".yml", ".tex", ".bib", ".html", ".xml"}
TABLE_EXT = {".csv", ".tsv"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


def run(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", repr(e)


def tag_paragraphs(pages):
    out = []
    for pi, page in enumerate(pages, 1):
        paras = [q.strip() for q in re.split(r"\n\s*\n", page) if q.strip()]
        for qi, q in enumerate(paras, 1):
            out.append(f"[p{pi}:{qi}] " + re.sub(r"[ \t]+", " ", q))
    return "\n\n".join(out)


def pdf_text(path):
    if shutil.which("pdftotext"):
        rc, out, err = run(["pdftotext", "-layout", path, "-"])
        if rc == 0 and out.strip():
            return out.split("\f"), "pdftotext"
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--task", default=None)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--render-pages", type=int, default=4)
    a = ap.parse_args()
    mdir = os.path.join(a.work, "materials")
    os.makedirs(os.path.join(mdir, "pages"), exist_ok=True)
    files = []
    for root, _, names in os.walk(a.input):
        for n in sorted(names):
            files.append(os.path.join(root, n))
    if a.task and os.path.exists(a.task):
        files.append(a.task)
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
                open(os.path.join(mdir, name + ".txt"), "w", encoding="utf-8").write(tag_paragraphs(pages))
                if shutil.which("pdftoppm") and a.render_pages > 0:
                    run(["pdftoppm", "-r", "60", "-png", "-l", str(a.render_pages), p,
                         os.path.join(mdir, "pages", os.path.splitext(name)[0])])
                caps = re.findall(r"((?:Fig(?:ure)?\.?|图)\s*\d+[A-Za-z]?[.:：][^\n]{0,160})", "\n".join(pages))
                row["figure_captions"] = caps[:20]
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
        print(json.dumps({k: r[k] for k in r if k in ("file", "type", "pages", "columns", "n_rows", "method", "problems")},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
