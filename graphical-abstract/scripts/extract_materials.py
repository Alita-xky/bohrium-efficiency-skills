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


BIB_RE = re.compile(r"^\s*\[\d+\]|^\s*\d+\.\s+[A-Z][a-z]+,|\bIn Proceedings\b|\barXiv preprint\b|\bpp\.\s*\d|\bet al\.,? \(?\d{4}", re.I)
FOOT_RE = re.compile(r"@|equal contribution|work (was )?(performed|done) while|corresponding author|received:|accepted:", re.I)


# default evidence rows (kind::regex::label::n) for figure tasks: relations, values, steps, setup, results, claims
AUTO_FACTS = [
    "relation::activat|inhibit|promot|induc|suppress|binds?\\b|convert|phosphorylat|transport|upregulat|downregulat|leads? to|results? in|激活|抑制|促进|诱导|结合|转化",
    "relation::activat|inhibit|promot|induc|suppress|binds?\\b|convert|phosphorylat|transport|upregulat|downregulat|leads? to|results? in|激活|抑制|促进|诱导|结合|转化",
    "relation::activat|inhibit|promot|induc|suppress|binds?\\b|convert|phosphorylat|transport|upregulat|downregulat|leads? to|results? in|激活|抑制|促进|诱导|结合|转化",
    "value::\\d+(\\.\\d+)?\\s?(%|mg|ng|µg|μg|g/L|mM|µM|μM|nM|°C|K\\b|h\\b|min\\b|kg|cm|mm|nm|MPa|kPa|mV|V\\b|mA|W\\b|Hz)",
    "value::\\d+(\\.\\d+)?\\s?(%|mg|ng|µg|μg|g/L|mM|µM|μM|nM|°C|K\\b|h\\b|min\\b|kg|cm|mm|nm|MPa|kPa|mV|V\\b|mA|W\\b|Hz)",
    "step::first(ly)?,|then\\b|finally|subsequently|step \\d|stage|procedure|workflow|首先|然后|最后|步骤|流程",
    "setup::apparatus|device|instrument|chamber|sensor|pump|laser|electrode|reactor|detector|装置|仪器|传感器|反应器",
    "result::increas|decreas|higher|lower|significant|improv|reduc|enhanc|增加|降低|显著|提高",
    "result::increas|decreas|higher|lower|significant|improv|reduc|enhanc|增加|降低|显著|提高",
    "claim::we (show|propose|demonstrate|find|report)|in (summary|conclusion)|conclude|本文|结论|综上",
]


ENT_RE = re.compile(r"(?<![\w-])(?:NF-κB|NF-kB|Ca2\+|Ca²⁺|cAMP|cGMP|ROS|NO\b|"
                    r"[A-Z][A-Za-z]{0,6}[-]?[0-9α-ωΑ-Ωκβγ][A-Za-z0-9α-ωκβγ/-]{0,10}|[A-Z]{2,}[a-z]?[0-9]{0,3}[A-Za-z0-9/-]{0,6})(?![\w-])")
ENT_STOP = {"Fig", "FIG", "Fig.", "Table", "Eq", "WT", "DNA", "RNA", "mRNA", "PCR", "qPCR", "ELISA", "SD", "SEM", "ANOVA", "USA", "UK", "CI",
            "OD", "PBS", "DMSO", "ID", "HR", "OR", "II", "III", "IV", "S1", "S2", "S3", "S4", "S5", "A1", "B1", "PLOS", "PNAS"}
VERB_RE = re.compile(r"\b(activat\w*|inhibit\w*|induc\w*|suppress\w*|promot\w*|up-?regulat\w*|down-?regulat\w*|"
                     r"stimulat\w*|enhanc\w*|reduc\w*|bind\w*|bound|phosphorylat\w*|convert\w*|increas\w*|decreas\w*|"
                     r"trigger\w*|abolish\w*|attenuat\w*|lead(?:s)? to|led to|result(?:s|ed)? in|激活|抑制|促进|诱导|结合)", re.I)
PERTURB = re.compile(r"(block\w*|knock\w*|delet\w*|neutraliz\w*|antagoni\w*|inhibit\w* of|loss of|absence of|silenc\w*|"
                     r"deficien\w*|anti-)\W+(?:\w+\W+){0,4}$", re.I)
CLAUSE = re.compile(r"[,;:()]|\b(while|whereas|but|although|which|that)\b", re.I)
NEG_SIGN = re.compile(r"inhibit|suppress|block|down-?regulat|reduc|decreas|abolish|attenuat|antagoni|抑制", re.I)
NOT_RE = re.compile(r"\b(not|no|neither|nor|without|fail\w*)\b", re.I)


def edge_candidates(mdir, fp, limit=14):
    """Mechanism figures: candidate arrows 'A -> B (activates|inhibits|binds|converts|modifies)' read from sentences that
    name two entities around a relation verb (passive 'B is inhibited by A' reversed, negated sentences skipped). Each
    becomes a facts.json row (verbatim quote + locator) and an arrow in edges.json, ready to paste into the spec."""
    files = sorted(f for f in glob.glob(os.path.join(mdir, "*.txt")) if not f.endswith(".layout.txt"))
    try:
        box = json.load(open(fp, encoding="utf-8")) if os.path.exists(fp) else {"facts": []}
    except ValueError:
        return []
    rows = box["facts"] if isinstance(box, dict) else box
    nxt = max([int(m.group(1)) for r in rows for m in [re.match(r"F(\d+)$", str(r.get("id", "")))] if m] or [0]) + 1
    seen, edges, ents = set(), [], {}

    def eid(name):
        k = re.sub(r"[^a-z0-9]+", "_", name.lower().replace("α", "a").replace("β", "b").replace("γ", "g").replace("κ", "k")).strip("_")
        ents.setdefault(k, name)
        return k
    for f in files:
        for line in open(f, encoding="utf-8", errors="ignore"):
            m = re.match(r"\[(p\d+:\d+)\] (.*)", line.rstrip("\n"))
            if not m or BIB_RE.search(m.group(2)):
                continue
            for snt in re.split(r"(?<=[.;!?。；！？])\s+(?=[A-Z(\[0-9一-鿿])", m.group(2)):
                if len(edges) >= limit or NOT_RE.search(snt):
                    continue
                es = [(x.start(), x.end(), x.group(0)) for x in ENT_RE.finditer(snt)
                      if x.group(0) not in ENT_STOP and not re.match(r"(Fig|Table|Eq|Supp)", x.group(0), re.I)]
                for v in VERB_RE.finditer(snt):
                    before = [e for e in es if e[1] <= v.start() and len(snt[e[1]:v.start()].split()) <= 6
                              and not CLAUSE.search(snt[e[1]:v.start()])]
                    after = [e for e in es if e[0] >= v.end() and len(snt[v.end():e[0]].split()) <= 6
                             and not CLAUSE.search(snt[v.end():e[0]])]
                    if not before or not after or len(edges) >= limit:
                        continue
                    ea, eb = before[-1], after[0]
                    a, b = ea[2], eb[2]
                    mid = snt[v.end():eb[0]]
                    if PERTURB.search(snt[:eb[0]][-40:]) and not PERTURB.search(snt[:ea[0]][-40:]):
                        continue                                   # 'X increases blocking of B': a different experiment
                    if mid.strip().lower().startswith("by"):       # passive: B is inhibited by A
                        a, b = b, a
                    if a == b:
                        continue
                    vb = v.group(0).lower()
                    if re.search(r"ions?$", vb):                    # nouns (activation, induction): no direction
                        continue
                    neg = bool(NEG_SIGN.search(vb)) or (not re.search(r"increas|enhanc|activat|up", vb) and
                                                        bool(re.search(r"decreas|reduc|lower|less|loss|abolish", mid, re.I)))
                    if PERTURB.search(snt[:ea[0]][-40:]):        # 'blocking A decreased B' => A activates B
                        neg = not neg
                    kind = ("binds" if re.match(r"bind|bound|结合", vb) else "converts" if vb.startswith("convert") else
                            "modifies" if vb.startswith("phosphorylat") else "inhibits" if neg else "activates")
                    key = (a, b, kind)
                    if key in seen:
                        continue
                    seen.add(key)
                    w = snt.split()
                    if len(w) > 30:
                        at = len(snt[:v.start()].split())
                        lo = max(0, min(at - 10, len(w) - 30))
                        w = w[lo:lo + 30]
                    fid = "F%d" % nxt
                    nxt += 1
                    label = "%s → %s (%s)" % (a, b, kind)
                    rows.append(dict(id=fid, kind="relation", text=label, source=dict(file=os.path.basename(f)[:-4],
                                                                                         loc=m.group(1), quote=" ".join(w))))
                    edges.append(dict(id="r%d" % (len(edges) + 1), **{"from": eid(a), "to": eid(b)}, kind=kind,
                                      label=vb, source=fid))
    if edges:
        json.dump(rows if isinstance(box, list) else dict(box, facts=rows), open(fp, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        json.dump(dict(entities=[dict(id=k, label=v) for k, v in ents.items()], arrows=edges),
                  open(os.path.join(os.path.dirname(fp), "edges.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return [(r["id"], r["text"], r["source"]["loc"], r["source"]["quote"]) for r in rows if r.get("text") in
            {"%s → %s (%s)" % (ents[e["from"]], ents[e["to"]], e["kind"]) for e in edges}]


def add_facts(mdir, specs, out, drop="", words=30, width=150, skip_used=False):
    """Append evidence rows to facts.json from search patterns: each matching sentence is copied verbatim
    (<= `words` words around the match) with its [pN:M] locator, so quotes are never retyped.
    SPEC = "kind::regex[::short label[::n-th match]]"; cite the printed ids in the spec as "source": "F3"."""
    import glob
    files = sorted(f for f in glob.glob(os.path.join(mdir, "*.txt")) if not f.endswith(".layout.txt"))
    sents = []
    for f in files:
        for line in open(f, encoding="utf-8", errors="ignore"):
            m = re.match(r"\[(p\d+:\d+)\] (.*)", line.rstrip("\n"))
            if m:
                for snt in re.split(r"(?<=[.;!?。；！？])\s+(?=[A-Z(\[0-9一-鿿])", m.group(2)):
                    if snt.strip():
                        sents.append((os.path.basename(f)[:-4], m.group(1), snt.strip()))
    rows, box = [], None
    if os.path.exists(out):
        try:
            box = json.load(open(out, encoding="utf-8"))
        except ValueError:
            print("facts file %s is not valid JSON: fix or remove it first" % out)
            return 1
        rows = box["facts"] if isinstance(box, dict) else box
    if drop:
        gone = {x.strip() for x in drop.split(",") if x.strip()}
        rows[:] = [r for r in rows if r.get("id") not in gone]
        print("dropped:", ", ".join(sorted(gone)))
    used = {int(m.group(1)) for r in rows for m in [re.match(r"F(\d+)$", str(r.get("id", "")))] if m}
    nxt = max(used or {0}) + 1
    have = {((r.get("source") or {}).get("loc"), (r.get("source") or {}).get("quote")): r.get("id") for r in rows}
    added = 0
    for spec in specs:
        f = spec.split("::")
        if len(f) < 2:
            print("!! %s: use kind::regex[::label[::n]]" % spec)
            continue
        kind, pat = f[0].strip() or "claim", f[1].strip()
        label = f[2].strip() if len(f) > 2 else ""
        nth = int(f[3]) if len(f) > 3 and f[3].strip().isdigit() else 1
        try:
            rx = re.compile(pat, re.I)
        except re.error as e:
            print("!! %s: bad regex (%s)" % (spec, e))
            continue
        def numeric(t):                                   # table dumps / number runs rank last
            ws = t.split()
            return sum(bool(re.fullmatch(r"[-+±]?\d+([.,]\d+)?%?", x)) for x in ws) > 0.35 * max(1, len(ws))
        body = sorted((x for x in sents if rx.search(x[2])),
                      key=lambda x: (bool(BIB_RE.search(x[2])), numeric(x[2]), bool(FOOT_RE.search(x[2])), len(x[2].split()) < 6))
        if skip_used:                                     # automatic table: never two rows from one sentence
            usedq = [((r.get("source") or {}).get("loc"), ((r.get("source") or {}).get("quote") or "")[:40]) for r in rows]
            body = [x for x in body if not any(l == x[1] and q and q in x[2] for l, q in usedq)]
            nth = 1
            if not body:
                continue
        if len(body) < nth:
            print("-- %s::%s  (no match%s) - try another wording or the .layout.txt (tables); a requested item that is "
                  "not in the materials gets a row with \"status\":\"not_in_materials\"" % (kind, pat, "" if not body else "; only %d" % len(body)))
            continue
        fn, loc, snt = body[nth - 1]
        w = snt.split()
        if len(w) > words:
            m = rx.search(snt)
            at = len(snt[:m.start()].split()) if m else 0
            lo = max(0, min(at - words // 3, len(w) - words))
            w = w[lo:lo + words]
        quote = " ".join(w)
        if (loc, quote) in have:
            print("== %s [%s] already %s" % (kind, loc, have[(loc, quote)]))
            continue
        fid = "F%d" % nxt
        nxt += 1
        rows.append(dict(id=fid, kind=kind, text=label or quote, source=dict(file=fn, loc=loc, quote=quote)))
        have[(loc, quote)] = fid
        added += 1
        print("%s %-9s %s [%s] :: %s" % (fid, kind, fn[:24], loc, quote[:width]))
        alts = [(j + 1, x) for j, x in enumerate(body) if j + 1 != nth][:2]
        for j, x in alts:
            print("      ::%d [%s] %s" % (j, x[1], x[2][:90]))
        if len(body) > len(alts) + 1:
            print("      (%d matches in all; to swap: --drop %s and re-run with ::n)" % (len(body), fid))
    json.dump(rows if isinstance(box, list) else dict(box or {}, facts=rows), open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("%s: %d rows (%d added). In spec.json write \"source\": \"F3\" - fig_make fills file/loc/quote from it." % (out, len(rows), added))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--task", default=None)
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--render-pages", type=int, default=6, help="max pages rendered (caption pages first)")
    ap.add_argument("--find", nargs="+", default=None,
                    help="search already extracted materials: print each sentence containing a term with its locator")
    ap.add_argument("--fact", nargs="+", default=None,
                    help='append verbatim rows to facts.json: "kind::regex[::short label[::n-th match]]"')
    ap.add_argument("--facts-out", default=None, help="default <work>/facts.json")
    ap.add_argument("--drop", default="", help="comma-separated fact ids to remove first (wrong matches)")
    a = ap.parse_args()
    mdir = os.path.join(a.work, "materials")
    if a.fact is not None or a.drop:
        return add_facts(mdir, a.fact or [], a.facts_out or os.path.join(a.work, "facts.json"), a.drop)
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
    fp = a.facts_out or os.path.join(a.work, "facts.json")
    if not os.path.exists(fp) and glob.glob(os.path.join(mdir, "*.txt")):
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            add_facts(mdir, AUTO_FACTS, fp, width=170, skip_used=True)
        rows = [l for l in buf.getvalue().splitlines() if re.match(r"F\d+ ", l)]
        if rows:
            print("---- evidence table (automatic, each quote verbatim with its locator): %s" % fp)
            print("\n".join(rows))
            print("Cite these ids in spec.json as \"source\": \"F3\". Missing item -> --fact \"kind::regex::label\"; "
                  "wrong row -> --drop F3. Tables: grep -n -A15 'Table N' %s/*.layout.txt" % mdir)
        try:
            mech = "mechanism" in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "SKILL.md"),
                                       encoding="utf-8").read(600).lower()
        except Exception:
            mech = False
        if mech:
            cand = edge_candidates(mdir, fp)
            if cand:
                print("---- candidate arrows (A → B, sign from the verb; verbatim quote in facts.json; arrows + entity ids "
                      "in %s/edges.json - paste the right ones into spec.json \"arrows\", drop the wrong ones)" % a.work)
                pairs = {}
                for fid, text, loc, q in cand:
                    pr = text.rsplit(" (", 1)[0]
                    pairs.setdefault(pr, set()).add(text.rsplit(" (", 1)[-1])
                for fid, text, loc, q in cand:
                    flag = " !conflicting sign: read both quotes" if len(pairs[text.rsplit(" (", 1)[0]]) > 1 else ""
                    print("%s %-34s [%s] :: %s%s" % (fid, text[:34], loc, q[:120], flag))
                print("These are machine-read candidates: keep an arrow only if its quote states that direction and sign "
                      "(about 1 in 3 needs fixing or dropping); a perturbation sentence 'blocking A decreased B' means A activates B.")
    else:
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
