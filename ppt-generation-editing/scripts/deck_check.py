#!/usr/bin/env python3
"""deck_check.py - audit a deck spec against the source materials and write the page-level evidence map.

Usage:
  python3 deck_check.py --spec /app/work/spec.json [--spec part2.json] --facts /app/work/facts.json \
      --materials '/app/work/materials/*.txt' /input/*.csv [--paper-map /app/work/paper_map.json] \
      --mode paper|talk|template|edit [--min-slides 8] [--max-slides 15] [--duration-min 15] [--plan talk_plan.json] \
      --out /app/output/check_report.json --map /app/output/evidence_map.json
  python3 deck_check.py scale --pptx /app/output/deck.pptx [--type-scale /app/output/type_scale.json]

FAIL (must fix before delivery)                       WARN (fix if cheap, else report)
- fact quote not found verbatim in the materials       - fact locator points to another paragraph (suggests one)
- slide without any source / unknown fact id           - title is a topic label, not a claim
- number on a slide that is in no material             - > 6 bullets, long bullets, notes repeat the slide
- Fig./Table number that has no caption in the paper   - figure page differs from its caption page
- chart CSV/column missing, TODO text left             - numeric claim backed only by a schematic figure
                                                        - bar chart axis not from 0; `scale` mode: text sizes
                                                          of the built deck off its type_scale.json ladder
- content slide without speaker notes                  - limitations slide missing (paper/talk)
- slide count outside limits; required story parts     - order not motivation -> method -> results -> conclusion
  missing (paper/talk); talk time budget off by >10%
Writes evidence_map.json + evidence_map.md: every slide item -> fact -> file/locator/verbatim quote + check result.
"""
import argparse, csv, glob, json, os, re, sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

NUM_RE = re.compile(r"(?<![A-Za-z_\d.])[-+−]?\d+(?:,\d{3})*(?:\.\d+)?(?:\s?[×x]\s?10\^?[-−]?\d+|e[-−]?\d+)?")
REF_RE = re.compile(r"(Fig(?:ure)?s?\.?|Tables?|Tab\.|Sec(?:tion)?s?\.?|§|Eqs?\.?|Equations?|Appendix|Alg(?:orithm)?s?\.?|"
                    r"Steps?|Stages?|Parts?|pp?\.|pages?|Ref\.?|slides?|图|表|第|公式)\s*\(?\d+(?:[.\-–]\d+)*[a-z]?\)?", re.I)
CAPREF_RE = re.compile(r"(Fig(?:ure)?s?\.?|Tables?|Tab\.|图|表)\s*(\d+)", re.I)
RANK = {"title": 0, "outline": 1, "agenda": 1, "overview": 1, "motivation": 2, "background": 2, "problem": 2, "context": 2,
        "related": 2, "question": 2, "goal": 2, "method": 3, "approach": 3, "setup": 3, "design": 3, "data": 3,
        "results": 4, "result": 4, "experiment": 4, "evaluation": 4, "analysis": 4, "ablation": 4, "evidence": 4,
        "discussion": 5, "limitations": 6, "limitation": 6, "future": 7, "conclusion": 7, "takeaway": 7,
        "qa": 8, "questions": 8, "thanks": 8, "backup": 9, "appendix": 9, "references": 9}
NO_SOURCE_NEEDED = {"title", "section", "outline", "agenda", "qa", "questions", "thanks", "references"}
GENERIC = {"introduction", "intro", "background", "motivation", "method", "methods", "methodology", "approach", "results",
           "result", "experiments", "experiment", "evaluation", "discussion", "conclusion", "conclusions", "summary",
           "outline", "agenda", "overview", "limitations", "future work", "related work", "q&a", "questions", "thanks",
           "thank you", "引言", "背景", "研究背景", "方法", "实验", "结果", "实验结果", "讨论", "结论", "总结", "目录", "致谢", "展望"}
META_FILES = ("case.json", "task.md")
TODO_RE = re.compile(r"\b(TODO|TBD|FIXME|lorem ipsum|XXX)\b|\?\?\?|<placeholder>", re.I)


def norm_text(s):
    s = str(s)
    for a, b in (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"), ("ﬃ", "ffi"), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'),
                 ("–", "-"), ("—", "-"), ("−", "-"), (" ", " "), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def variants(s):
    s = norm_text(s)
    return {s, re.sub(r"(\w)- (\w)", r"\1\2", s), re.sub(r"(\w)- (\w)", r"\1-\2", s)}


def to_float(tok):
    t = tok.replace("−", "-").replace(",", "").replace(" ", "")
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)(?:[×x]10\^?([-]?\d+)|e([-]?\d+))?$", t)
    if not m:
        return None
    v = float(m.group(1))
    e = m.group(2) or m.group(3)
    return round(v * (10 ** int(e)) if e else v, 6)


SCI_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?[·×x*]\s?10\s?(?:\^\{?|\*\*)([-−]?\d{1,3})\}?")
SCI_MAT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?[·×]\s?10\^?([-−]?\d{1,3})(?![\d.])")  # PDF text: "2.3 · 1019"


def sci_value(m):
    try:
        return float("%se%d" % (m.group(1), int(m.group(2).replace("−", "-"))))
    except (ValueError, OverflowError):
        return None


def numbers_in(text):
    """numbers on a slide; '2.3 · 10^19' / '2.3×10^{19}' count as ONE value (2.3e19), not 2.3, 10 and 19"""
    out = []
    def _sci(m):
        v = sci_value(m)
        if v is not None:
            out.append((m.group(0).strip(), v))
        return " "
    t = SCI_RE.sub(_sci, str(text))
    t = REF_RE.sub(" ", t)
    t = re.sub(r"(^|\s)\(?\d{1,2}\)\s", " ", t)  # list markers (1) 2)
    t = re.sub(r"\*\*", "", t)
    for m in NUM_RE.finditer(t):
        v = to_float(m.group(0))
        if v is not None:
            out.append((m.group(0).strip(), v))
    return out


def load_materials(paths):
    docs = []  # (file, tag, text)
    for pat in paths:
        for p in sorted(glob.glob(pat)) or ([pat] if os.path.exists(pat) else []):
            name = os.path.basename(p)
            try:
                if p.lower().endswith((".csv", ".tsv")):
                    with open(p, newline="", encoding="utf-8-sig") as f:
                        rows = list(csv.reader(f, delimiter="\t" if p.lower().endswith(".tsv") else ","))
                    for i, r in enumerate(rows):
                        docs.append((name, "row%d" % i, ",".join(r)))
                else:
                    txt = open(p, encoding="utf-8", errors="ignore").read()
                    parts = re.split(r"(?m)^\[(p\d+:\d+)\] ", txt)
                    if len(parts) > 2:
                        for i in range(1, len(parts) - 1, 2):
                            docs.append((name, parts[i], parts[i + 1]))
                    else:
                        for i, para in enumerate(re.split(r"\n\s*\n", txt), 1):
                            docs.append((name, "para%d" % i, para))
            except OSError:
                continue
    return docs


def slide_text_items(sl):
    """[(what, text, src_ids)] of everything visible on the slide"""
    items = []
    base_src = list(sl.get("sources") or [])
    if sl.get("title"):
        items.append(("title", sl["title"], base_src))
    sub = sl.get("subtitle")
    for t in (sub if isinstance(sub, list) else ([sub] if sub else [])):
        items.append(("subtitle", str(t), base_src))
    for i, b in enumerate(sl.get("bullets") or [], 1):
        if isinstance(b, dict):
            items.append(("bullet %d" % i, b.get("text", ""), b.get("src") or base_src))
        else:
            items.append(("bullet %d" % i, str(b[0] if isinstance(b, (list, tuple)) else b), base_src))
    tk = K.takeaway_text(sl)
    if tk and not any(tk == t for _, t, _ in items):
        items.append(("takeaway", tk, base_src))
    for key in ("quote", "quote_src"):
        if isinstance(sl.get(key), str) and sl[key].strip():
            items.append((key, sl[key], base_src))
    for col in ("left", "right"):
        c = sl.get(col) or {}
        if c.get("heading"):
            items.append((col + " heading", c["heading"], base_src))
        for i, b in enumerate(c.get("bullets") or [], 1):
            t = b.get("text", "") if isinstance(b, dict) else str(b)
            items.append(("%s bullet %d" % (col, i), t, (b.get("src") if isinstance(b, dict) else None) or base_src))
    for key, v in K.spec_visuals(sl):
        src = v.get("src") or base_src
        label = "%s %s" % (key, v.get("ref") or "")
        if v.get("caption") or v.get("ref"):
            items.append((label.strip() + " caption", ((v.get("ref") or "") + " " + (v.get("caption") or "")).strip(), src))
        if key == "table":
            rows = v.get("rows")
            if not rows and v.get("csv") and os.path.exists(v["csv"]):
                with open(v["csv"], newline="", encoding="utf-8-sig") as f:
                    rows = list(csv.reader(f))
            for r in rows or []:
                items.append((label.strip() + " cells", " | ".join(str(c) for c in r), src))
        if key == "chart":
            items.append((label.strip() + " data", "CSV %s: x=%s y=%s" % (os.path.basename(str(v.get("csv"))), v.get("x"), v.get("y")), src))
    return items


def deck_to_spec(pptx):
    """existing deck -> pseudo spec (title, text paragraphs, first table, notes) for auditing"""
    inv = K.inventory(K.Package(pptx))
    slides = []
    for s in inv["slides"]:
        sp = dict(id="slide%d" % s["index"], role="", title=s["title"], bullets=[], notes=s["notes"], sources=[])
        for sh in s["shapes"]:
            if sh.get("ph_type") in ("title", "ctrTitle", "ftr", "sldNum", "dt") or sh["name"].startswith(("Slide Number", "Source Note", "Footer")):
                continue
            if sh["kind"] == "table" and sh.get("table") and not sp.get("table"):
                sp["table"] = dict(rows=sh["table"], ref=None)
            for p in sh.get("paras", []):
                if p["text"].strip():
                    sp["bullets"].append(dict(text=p["text"], level=p["level"]))
        slides.append(sp)
    return dict(meta=dict(title=inv["slides"][0]["title"] if inv["slides"] else "", source_deck=os.path.abspath(pptx)), slides=slides)


def _toks(s):
    s = str(s).lower()
    t = set(w for w in re.findall(r"[a-z][a-z0-9\-]{2,}|\d+(?:\.\d+)?", s))
    cjk = re.sub(r"[^\u4e00-\u9fff]", "", s)
    t |= {cjk[i:i + 2] for i in range(len(cjk) - 1)}
    return t


def auto_sources(spec, docs, only_missing=False):
    """attach the best-matching material paragraph (token overlap >= 0.5) to each text item of a pseudo spec"""
    dt = [(f, t, x, _toks(x)) for f, t, x in docs if not f.startswith(META_FILES)]
    for sl in spec["slides"]:
        if only_missing and (sl.get("sources") or any(isinstance(b, dict) and b.get("src") for b in sl.get("bullets") or [])):
            continue
        tb = dict(text=sl.get("title", ""))
        sl["bullets"] = [b if isinstance(b, dict) else dict(text=str(b), level=0) for b in sl.get("bullets") or []]
        for b in [tb] + sl["bullets"]:
            tk = _toks(b["text"])
            if len(tk) < 3:
                continue
            best = max(dt, key=lambda d: len(tk & d[3]) / float(len(tk)), default=None)
            if best is None:
                continue
            sim = len(tk & best[3]) / float(len(tk))
            if sim >= 0.5:
                sents = re.split(r"(?<=[.!?。；;])\s+", best[2])
                q = max(sents, key=lambda z: len(tk & _toks(z)))
                b["src"] = [dict(file=best[0], loc=best[1], quote=norm_text(q)[:200], similarity=round(sim, 2), auto=True)]
        if tb.get("src"):
            sl["sources"] = tb["src"]
    return spec


def scale_issues(pptx, scale_path=None):
    """every explicitly written text size of the built deck vs its type_scale.json (title size, body ladder,
    display / label / caption / note / table sizes); sizes inherited from the template's placeholders are the
    template's own and pass. Returns (issues, scale) - issues = off-scale sizes."""
    scale_path = scale_path or os.path.join(os.path.dirname(os.path.abspath(pptx)), "type_scale.json")
    if not os.path.exists(scale_path):
        return [], None
    sc = json.load(open(scale_path, encoding="utf-8"))
    allowed = set()
    for k in ("display", "table", "caption"):
        allowed.update(sc.get(k) or [])
    allowed.update((sc.get("body") or {}).get("ladder") or [])
    allowed.update(v for v in (sc.get("label"), sc.get("source_note"), (sc.get("title") or {}).get("size_pt")) if v)
    tables = set(sc.get("table") or [])
    issues = []
    inv = K.inventory(K.Package(pptx))
    for sl in inv["slides"]:
        for sh in sl["shapes"]:
            if sh["kind"] == "table":
                mf = sh.get("min_font_pt")
                if mf and not any(abs(mf - v) < 0.6 for v in tables | allowed):
                    issues.append(dict(slide=sl["index"], shape=sh["name"], size=mf))
                continue
            for p in sh.get("paras", []):
                bad = [r["sz"] for r in p.get("runs", []) if r.get("sz") and (r.get("text") or "").strip()
                       and not any(abs(r["sz"] - v) < 0.6 for v in allowed)]
                if bad:
                    issues.append(dict(slide=sl["index"], shape=sh["name"], size=bad[0]))
                    break
    return issues, sc


def scale_main(argv):
    ap = argparse.ArgumentParser(prog="deck_check.py scale")
    ap.add_argument("--pptx", required=True)
    ap.add_argument("--type-scale", default=None)
    a = ap.parse_args(argv)
    issues, sc = scale_issues(a.pptx, a.type_scale)
    if sc is None:
        print(json.dumps(dict(status="no_type_scale", pptx=a.pptx)))
        return 0
    print(json.dumps(dict(status="pass" if not issues else "warn", title_pt=(sc.get("title") or {}).get("size_pt"),
                          body_ladder=(sc.get("body") or {}).get("ladder"), off_scale=len(issues)), ensure_ascii=False))
    for x in issues[:20]:
        print("WARN type_scale slide %d %s: %.1f pt is not on the deck's type scale (see type_scale.json)"
              % (x["slide"], x["shape"], x["size"]))
    return 0


# ----------------------------------------------------------------------------- talk helpers
CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
STOP = set("the a an of to in on for and or with is are was were be by that this it as at from how what why does do did can "
           "which who will would should could than then their there its our your not no but if into over under about".split())


def speak_seconds(text):
    """speaking time of a notes script: ~4 Chinese characters/s (240/min), ~2.3 English words/s (140 wpm)"""
    t = str(text or "")
    cjk = len(CJK_RE.findall(t))
    words = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9.%\-]*", CJK_RE.sub(" ", t)))
    return cjk / 4.0 + words / 2.3


def topic_tokens(text):
    t = str(text or "").lower()
    toks = {w for w in re.findall(r"[a-z][a-z0-9\-]{2,}", t) if w not in STOP}
    cjk = "".join(CJK_RE.findall(t))
    toks |= {cjk[i:i + 2] for i in range(len(cjk) - 1)}
    return toks


def covers(doubt, text):
    d = topic_tokens(doubt)
    return len(d & topic_tokens(text)) / len(d) if d else 0.0


def doubt_coverage(plan, slides):
    """audience.likely_doubts -> the Q&A items / slides that answer them (explicit qa[].doubt index or text, else word overlap)"""
    aud = plan.get("audience") or {}
    doubts = aud.get("likely_doubts") if isinstance(aud, dict) else None
    doubts = [str(d) for d in (doubts or []) if str(d).strip() and "TODO" not in str(d)]
    qa = [q for q in (plan.get("qa") or []) if isinstance(q, dict)]
    rows = []
    tags = [q.get("doubt") for q in qa if str(q.get("doubt", "")).strip().isdigit()]
    one_based = bool(tags) and min(int(t) for t in tags) >= 1 and max(int(t) for t in tags) == len(doubts)  # 1..n given
    for k, d in enumerate(doubts):
        hits = []
        for j, q in enumerate(qa, 1):
            tag = q.get("doubt")
            ap_ = q.get("answer_points")
            txt = " ".join([str(q.get("question", ""))] + (ap_ if isinstance(ap_, list) else [str(ap_ or "")]))
            idx = int(tag) - (1 if one_based else 0) if str(tag if tag is not None else "").strip().isdigit() else None
            if idx == k or (isinstance(tag, str) and idx is None and (tag.strip() == d or covers(d, tag) >= 0.6)):
                hits.append(dict(where="Q&A %d" % j, how="tagged", backup=q.get("backup_slide")))
            elif covers(d, txt) >= 0.4:
                hits.append(dict(where="Q&A %d" % j, how="overlap %.2f" % covers(d, txt), backup=q.get("backup_slide")))
        for i, sl in enumerate(slides, 1):
            txt = " ".join(str(sl.get(x, "")) for x in ("title", "key_message")) + " " + " ".join(map(str, sl.get("if_asked") or []))
            c = covers(d, txt)
            if c >= 0.5:
                hits.append(dict(where="slide %d%s" % (i, " (backup)" if str(sl.get("role", "")).lower() in ("backup", "appendix") else ""),
                                 how="overlap %.2f" % c))
        rows.append(dict(doubt=d, answered_by=hits))
    return rows


def selection_ledger(pm, slides, plan):
    """every figure/table of the paper -> main slide(s) / backup slide(s) / recorded cut / not presented"""
    def keyof(m):
        return ("table" if m.group(1).lower().startswith(("tab", "表")) else "figure", m.group(2))
    used = {}
    for i, sl in enumerate(slides, 1):
        bk = str(sl.get("role", "")).lower() in ("backup", "appendix")
        refs = set()
        for _, v in K.spec_visuals(sl):
            for m in CAPREF_RE.finditer(str(v.get("ref", ""))):
                refs.add((keyof(m), "shown"))
        for _, t, _ in slide_text_items(sl):
            for m in CAPREF_RE.finditer(t):
                refs.add((keyof(m), "cited"))
        for k, how in refs:
            used.setdefault(k, []).append(dict(slide=i, id=sl.get("id"), backup=bk, how=how))
    cut_of = {}
    for c in plan.get("cuts") or []:
        txt = (" ".join(str(c.get(x, "")) for x in ("ref", "content")) if isinstance(c, dict) else str(c))
        for m in CAPREF_RE.finditer(txt):
            cut_of.setdefault(keyof(m), c.get("reason") if isinstance(c, dict) else txt)
    rows = []
    for c in (pm or {}).get("figures_tables", []):
        num = re.sub(r"\D", "", str(c.get("label", "")).split()[-1] if c.get("label") else "")
        if not num:
            continue
        k = ("table" if c.get("kind") == "table" else "figure", num)
        u = used.get(k, [])
        main = sorted({x["slide"] for x in u if not x["backup"]})
        bk = sorted({x["slide"] for x in u if x["backup"]})
        if main:
            disp = "main slide %s" % ", ".join(map(str, main)) + ((" + backup %s" % ", ".join(map(str, bk))) if bk else "")
        elif bk:
            disp = "backup slide %s" % ", ".join(map(str, bk))
        elif k in cut_of:
            disp = "cut: %s" % cut_of[k]
        else:
            disp = "not presented (no reason recorded)"
        rows.append(dict(ref=("Table " if k[0] == "table" else "Figure ") + num, page=c.get("page"),
                         caption=str(c.get("caption") or c.get("label") or "")[:120], shown_on=main, backup_on=bk,
                         disposition=disp, ok=not disp.startswith("not presented")))
    return rows


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "scale":
        return scale_main(sys.argv[2:])
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", action="append", default=[])
    ap.add_argument("--pptx", default=None, help="audit an existing deck instead of a spec (sources are auto-matched)")
    ap.add_argument("--facts", default=None)
    ap.add_argument("--materials", nargs="+", default=["/app/work/materials/*.txt"])
    ap.add_argument("--paper-map", default=None)
    ap.add_argument("--mode", default="paper", choices=["paper", "talk", "template", "edit"])
    ap.add_argument("--min-slides", type=int, default=None)
    ap.add_argument("--max-slides", type=int, default=None)
    ap.add_argument("--duration-min", type=float, default=None)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--out", default="/app/output/check_report.json")
    ap.add_argument("--map", default=None)
    a = ap.parse_args()

    spec = deck_to_spec(a.pptx) if a.pptx else None
    if not a.spec and not a.pptx:
        ap.error("give --spec or --pptx")
    for p in a.spec:
        d = json.load(open(p, encoding="utf-8"))
        d = {"slides": d} if isinstance(d, list) else d
        K.resolve_spec_paths(d.get("slides", []), os.path.dirname(os.path.abspath(p)))
        if spec is None:
            spec = d
            spec.setdefault("slides", [])
        else:
            spec["slides"].extend(d.get("slides", []))
            for k, v in d.items():
                spec.setdefault(k, v)
    meta = spec.get("meta", {})
    facts = {}
    if a.facts and os.path.exists(a.facts):
        fd = json.load(open(a.facts, encoding="utf-8"))
        rows = fd.get("facts", fd) if isinstance(fd, dict) else fd
        rows = [dict(v, id=k) for k, v in rows.items()] if isinstance(rows, dict) else rows
        facts = {r["id"]: r for r in rows if isinstance(r, dict) and r.get("id")}
    docs = load_materials(a.materials)
    normdocs = [(f, t, norm_text(x)) for f, t, x in docs]
    if a.pptx or a.mode in ("template", "edit"):
        auto_sources(spec, docs, only_missing=not a.pptx)
    all_nums = {}
    for f, t, x in docs:
        for m in NUM_RE.finditer(x):
            v = to_float(m.group(0))
            if v is not None:
                all_nums.setdefault(v, []).append("%s:%s" % (f, t))
                all_nums.setdefault(abs(v), []).append("%s:%s" % (f, t))
        for m in list(SCI_MAT_RE.finditer(x)) + list(SCI_RE.finditer(x)):  # scientific notation, superscript flattened
            v = sci_value(m)
            if v is not None:
                all_nums.setdefault(v, []).append("%s:%s" % (f, t))
    captions = {}
    pm = None
    if a.paper_map and os.path.exists(a.paper_map):
        pm = json.load(open(a.paper_map, encoding="utf-8"))
        for c in pm.get("figures_tables", []):
            captions[("table" if c["kind"] == "table" else "figure", re.sub(r"\D", "", c["label"].split()[-1]))] = c
    else:
        for f, t, x in docs:
            for line in x.split("\n"):
                m = re.match(r"^\s*(Figure|Fig\.?|Table|Tab\.?|图|表)\s*(\d+)\s*[:.：|]", line, re.I)
                if m:
                    kind = "table" if m.group(1).lower().startswith(("tab", "表")) else "figure"
                    pg = re.match(r"p(\d+)", t)
                    captions.setdefault((kind, m.group(2)), dict(label=line[:60], page=int(pg.group(1)) if pg else None, caption_loc=t))
    issues = []

    def add(level, check, msg, slide=None, item=None):
        issues.append(dict(level=level, check=check, slide=slide, item=item, msg=msg))

    # ---- facts
    fact_status = {}
    for fid, f in facts.items():
        src = f.get("source", {}) or {}
        q = src.get("quote", "")
        st = dict(quote_found=None, loc_ok=None, found_at=[])
        if f.get("status") == "not_in_materials":
            st["not_in_materials"] = True
        elif not q:
            add("warn", "fact_quote", "fact %s has no verbatim quote" % fid)
        else:
            vs = variants(q)
            hits = ["%s:%s" % (fn, tg) for fn, tg, x in normdocs if any(v in x or v in re.sub(r"(\w)- (\w)", r"\1\2", x) for v in vs)]
            if not hits:  # quote may span two paragraphs: search the concatenated text of the file
                joined = {}
                for fn, tg, x in normdocs:
                    joined.setdefault(fn, []).append(x)
                for fn, xs in joined.items():
                    blob = " ".join(xs)
                    if any(v in blob or v in re.sub(r"(\w)- (\w)", r"\1\2", blob) for v in vs):
                        hits.append("%s:(spans paragraphs)" % fn)
            st["found_at"] = hits[:5]
            st["quote_found"] = bool(hits)
            if not hits:
                best = 0.0
                for fn, tg, x in normdocs:
                    if len(x) > 20:
                        r = SequenceMatcher(None, norm_text(q)[:200], x[:4000]).find_longest_match(0, min(200, len(norm_text(q))), 0, min(4000, len(x)))
                        best = max(best, r.size / max(1, min(200, len(norm_text(q)))))
                add("fail", "fact_quote", "fact %s quote not found verbatim in materials (longest match %.0f%%): copy it "
                    "exactly from materials/*.txt" % (fid, best * 100))
            else:
                loc = str(src.get("loc", ""))
                st["loc_ok"] = any(h.endswith(":" + loc) for h in hits) or not loc or "spans" in hits[0]
                if not st["loc_ok"]:
                    add("warn", "fact_loc", "fact %s locator %s does not hold the quote; found at %s" % (fid, loc, ", ".join(hits[:3])))
        fact_status[fid] = st

    # ---- slides
    slides = spec.get("slides", [])
    n = len(slides)
    mn = a.min_slides or meta.get("min_slides")
    mx = a.max_slides or meta.get("max_slides")
    if mn and n < int(mn):
        add("fail", "slide_count", "deck has %d slides, minimum is %s" % (n, mn))
    if mx and n > int(mx):
        add("fail", "slide_count", "deck has %d slides, maximum is %s" % (n, mx))
    emap = []
    titles = {}
    for i, sl in enumerate(slides, 1):
        role = str(sl.get("role", "")).lower()
        sid = sl.get("id", "S%d" % i)
        items = slide_text_items(sl)
        all_src = set()
        for _, _, s_ in items:
            for x in s_ or []:
                all_src.add(x if isinstance(x, str) else json.dumps(x, sort_keys=True))
        if not all_src and role not in NO_SOURCE_NEEDED:
            if a.pptx or a.mode in ("template", "edit"):
                add("warn", "sources", "no material paragraph matches this slide's text (unsourced or rephrased content?)", i)
            else:
                add("fail", "sources", "slide has no sources (cite fact ids)", i)
        for x in all_src:
            if not x.startswith("{") and facts and x not in facts:
                add("fail", "sources", "unknown fact id %s" % x, i)
        text_blob = "\n".join(t for _, t, _ in items)
        if TODO_RE.search(text_blob + " " + str(sl.get("notes", ""))):
            add("fail", "todo", "template/TODO text left on slide", i)
        # numbers
        num_rows = []
        derived = {to_float(str(d.get("value"))) for d in (sl.get("derived") or []) if isinstance(d, dict)}
        for what, t, _ in items:
            if what.endswith(" data"):
                continue
            for tok, v in numbers_in(t):
                where = sorted(set(all_nums.get(v) or all_nums.get(abs(v)) or []), key=lambda w: w.startswith(META_FILES))
                ok = bool(where) or v in derived
                if where and all(w.startswith(META_FILES) for w in where) and v not in derived:
                    add("warn", "number", "number %s (%s) appears only in case.json/task.md, not in the paper/data" % (tok, what), i, what)
                num_rows.append(dict(value=tok, item=what, found_at=(where or [])[:3], derived=v in derived and not where))
                if not ok and a.mode == "template":  # carried text must not be changed: report, do not "fix"
                    add("warn", "number", "number %s (%s) from the source deck is not in the paper/data: keep it, list it "
                        "in uncertainty as unverified (or as a conflict if the paper gives another value)" % (tok, what), i, what)
                elif not ok:
                    add("fail", "number", "number %s (%s) is not in any material; use the paper's value or declare it in "
                        "slide.derived with formula" % (tok, what), i, what)
        # figure / table references
        for what, t, _ in items:
            for m in CAPREF_RE.finditer(t):
                kind = "table" if m.group(1).lower().startswith(("tab", "表")) else "figure"
                if captions and (kind, m.group(2)) not in captions:
                    add("fail", "fig_ref", "%s %s is cited but the paper has no such caption" % (m.group(1), m.group(2)), i, what)
        for key, v in K.spec_visuals(sl):
            if key == "chart":
                if not v.get("csv") or not os.path.exists(v["csv"]):
                    add("fail", "chart", "chart CSV missing: %s" % v.get("csv"), i)
                else:
                    with open(v["csv"], newline="", encoding="utf-8-sig") as f:
                        cols = next(csv.reader(f), [])
                    for c in [v.get("x")] + (v.get("y") if isinstance(v.get("y"), list) else [v.get("y")]):
                        if c and c not in cols:
                            add("fail", "chart", "column %s not in %s" % (c, os.path.basename(v["csv"])), i)
                if v.get("kind", "bar") in ("bar", "barh") and (v.get("logy") or (v.get("ylim") and v["ylim"][0] not in (0, None))):
                    add("warn", "bar_axis", "bar chart axis does not start at 0 (%s): the builder draws bars from 0; use a line "
                        "chart if the range matters" % ("log scale" if v.get("logy") else "ylim %s" % v["ylim"][0]), i)
            if key == "figure":
                if str(v.get("chart_type", "")).startswith("bar") and not v.get("data_csv"):
                    add("warn", "bar_axis", "bar chart carried as an image without its data: its axis may not start at 0", i)
                if v.get("image") and not os.path.exists(v["image"]):
                    add("fail", "figure", "image not found: %s" % v["image"], i)
                if v.get("pdf") and not os.path.exists(v["pdf"]):
                    add("fail", "figure", "pdf not found: %s" % v["pdf"], i)
                m = CAPREF_RE.search(str(v.get("ref", "")))
                if m and captions:
                    kind = "table" if m.group(1).lower().startswith(("tab", "表")) else "figure"
                    c = captions.get((kind, m.group(2)))
                    if c and v.get("page") and c.get("page") and int(v["page"]) != int(c["page"]):
                        add("warn", "fig_page", "%s shown from page %s but its caption is on page %s" % (v["ref"], v["page"], c["page"]), i)
                if not v.get("ref"):
                    add("warn", "fig_ref", "figure without ref (Fig./Table number of the paper or 'new')", i)
                if v.get("kind") == "schematic" and any(r["item"].startswith(("title", "bullet")) for r in num_rows) and \
                        not any(k in ("chart", "table") for k, _ in K.spec_visuals(sl)) and role in ("results", "result", "evaluation"):
                    add("warn", "schematic_as_data", "numeric result shown next to a schematic only: cite the data figure/table", i)
        # titles, density, notes
        title = str(sl.get("title", "")).strip()
        if title:
            titles.setdefault(title.lower(), []).append(i)
        if role not in NO_SOURCE_NEEDED and role not in ("section", "backup", "appendix"):
            words = len(title.split()) if not K.has_cjk(title) else len(re.sub(r"\s", "", title)) / 2.5
            if title.lower().strip(" :.") in GENERIC or words < 4:
                add("warn", "claim_title", "title '%s' is a topic label; write the slide's claim as the title" % title[:40], i)
        bl = sl.get("bullets") or []
        if a.mode in ("paper", "talk") and role in ("motivation", "problem", "background", "method", "approach", "results",
                                                     "result", "analysis", "evaluation", "discussion", "limitations",
                                                     "conclusion") and not K.spec_visuals(sl):
            chars = sum(len(re.sub(r"\s", "", t)) * (2 if K.has_cjk(t) else 1) for w_, t, _ in items if w_.startswith(("bullet", "left", "right")))
            if len(bl) < 3 or chars < 90:
                add("warn", "thin_slide", "only %d short point(s) and no figure/table: add the paper's concrete evidence "
                    "(numbers with units, conditions, Sec./Table locators) or merge this slide" % len(bl), i)
        if len(bl) > 9:
            add("fail", "density", "%d bullets (max 9; aim <= 6): split the slide" % len(bl), i)
        elif len(bl) > 6:
            add("warn", "density", "%d bullets (aim <= 6)" % len(bl), i)
        for what, t, _ in items:
            if what.startswith(("bullet", "left bullet", "right bullet")):
                lim = 45 if K.has_cjk(t) else 110
                if len(t) > lim:
                    add("warn", "density", "long bullet (%d chars > %d): shorten to a phrase" % (len(t), lim), i, what)
        notes = str(sl.get("notes", "")).strip()
        if role not in ("title", "section") or a.mode == "talk":
            if not notes:
                add("fail" if role not in ("section", "backup", "appendix", "references") and a.mode not in ("template", "edit") else "warn",
                    "notes", "no speaker notes" + (" (source deck had none; add only if the task asks)" if a.mode == "template" else ""), i)
            elif len(notes) < 30:
                add("warn", "notes", "very short notes (%d chars)" % len(notes), i)
            else:
                tok_n = set(re.findall(r"\w+", notes.lower()))
                tok_s = set(re.findall(r"\w+", text_blob.lower()))
                if tok_n and len(tok_n & tok_s) / len(tok_n) > 0.8:
                    add("warn", "notes_repeat", "notes mostly repeat the slide text: say what is NOT on the slide "
                        "(why it matters, transition, caveat)", i)
        # evidence map rows
        rows = []
        for what, t, src in items:
            srcs = []
            for x in src or []:
                if isinstance(x, str):
                    f = facts.get(x, {})
                    s_ = f.get("source", {}) or {}
                    st = fact_status.get(x, {})
                    srcs.append(dict(fact=x, file=s_.get("file"), loc=s_.get("loc"), ref=s_.get("ref"), quote=s_.get("quote"),
                                     quote_found=st.get("quote_found"), loc_ok=st.get("loc_ok"),
                                     not_in_materials=st.get("not_in_materials", False)))
                elif isinstance(x, dict):
                    srcs.append(dict(x, fact=None, quote_found=x.get("auto") or x.get("quote_found"), loc_ok=True))
            rows.append(dict(item=what, text=t, sources=srcs))
        slide_issues = [x for x in issues if x["slide"] == i]
        emap.append(dict(slide=i, id=sid, role=role, title=title, items=rows, numbers=num_rows,
                         visuals=[dict(type=k, ref=v.get("ref"), page=v.get("page"), kind=v.get("kind"),
                                       file=v.get("image") or v.get("pdf") or v.get("csv")) for k, v in K.spec_visuals(sl)],
                         status="ok" if not any(x["level"] == "fail" for x in slide_issues) else "needs_fix",
                         issues=[x["msg"] for x in slide_issues]))
    for t, idx in titles.items():
        if len(idx) > 1:
            add("warn", "duplicate_title", "same title on slides %s" % idx)
    # ---- story
    if a.mode in ("paper", "talk"):
        roles = [str(s.get("role", "")).lower() for s in slides]
        need = {"motivation/problem/background": ("motivation", "problem", "background", "context", "question", "goal"),
                "method": ("method", "approach", "setup", "design"), "results": ("results", "result", "experiment", "evaluation", "evidence", "analysis"),
                "conclusion": ("conclusion", "takeaway")}
        for k, alts in need.items():
            if not any(r in alts for r in roles):
                add("fail", "story", "no %s slide (set slide.role)" % k)
        if not any(r in ("limitations", "limitation", "discussion") for r in roles):
            add("warn", "story", "no limitations/discussion slide: state the paper's limits")
        last = -1
        for i, r in enumerate(roles, 1):
            if r in ("section", "summary", "") or r not in RANK:
                continue
            if RANK[r] < last and RANK[r] < 8:
                add("warn", "story_order", "slide %d role '%s' comes after a later-stage slide" % (i, r), i)
            last = max(last, RANK[r])
    # ---- talk plan
    plan = json.load(open(a.plan, encoding="utf-8")) if a.plan and os.path.exists(a.plan) else (spec.get("plan") or {})
    timeline = []
    speech_fit, speech_total, doubt_rows, ledger = [], None, [], []
    dur = a.duration_min or plan.get("duration_min") or meta.get("duration_min")
    if a.mode == "talk":
        for k in ("goal", "audience"):
            if not (plan.get(k) or meta.get(k)):
                add("fail", "talk_plan", "talk plan has no '%s'" % k)
        main_slides = [s for s in slides if str(s.get("role", "")).lower() not in ("backup", "appendix")]
        tot = sum(float(s.get("time_s", 0) or 0) for s in main_slides)
        if dur:
            if not tot:
                add("fail", "time_budget", "no slide has time_s; budget %s min" % dur)
            elif abs(tot - float(dur) * 60) > 0.1 * float(dur) * 60:
                add("fail", "time_budget", "slide times sum to %.1f min, talk is %s min (allowed +-10%%)" % (tot / 60, dur))
        for i, s in enumerate(slides, 1):
            r = str(s.get("role", "")).lower()
            if r in ("backup", "appendix"):
                continue
            if not s.get("time_s"):
                add("warn", "time_budget", "slide has no time_s", i)
            if r not in ("title", "section", "qa", "thanks") and not s.get("key_message"):
                add("warn", "key_message", "no key_message", i)
        with_tr = sum(1 for s in main_slides[:-1] if s.get("transition"))
        if len(main_slides) > 2 and with_tr < 0.7 * (len(main_slides) - 1):
            add("warn", "transitions", "only %d of %d slides have a transition sentence" % (with_tr, len(main_slides) - 1))
        qa = plan.get("qa") or meta.get("qa") or []
        ids = {s.get("id") for s in slides}
        if len(qa) < 3:
            add("fail", "qa", "Q&A preparation has %d questions (need >= 3 with answer points, evidence, backup slide)" % len(qa))
        for q in qa:
            for f in q.get("evidence", []):
                if facts and f not in facts:
                    add("fail", "qa", "Q&A evidence %s is not a fact id" % f)
            b = q.get("backup_slide")
            if b and b not in ids:
                add("fail", "qa", "Q&A backup slide %s does not exist" % b)
        for where, text in [("plan section %s" % sec.get("name"), str(sec.get("key_message", ""))) for sec in plan.get("sections", [])] + \
                [("Q&A '%s'" % str(q.get("question"))[:40], " ".join(q.get("answer_points", []) if isinstance(q.get("answer_points"), list)
                                                                   else [str(q.get("answer_points", ""))])) for q in qa]:
            for tok, v in numbers_in(text):
                if not (all_nums.get(v) or all_nums.get(abs(v))):
                    add("fail", "number", "number %s in %s is not in any material" % (tok, where))
        if not (plan.get("cuts") or meta.get("cuts")):
            add("warn", "cuts", "no record of what was left out and why (plan.cuts)")
        # spoken script vs slide time (the classic 'slide count and talk length do not match' failure)
        tot_speak = 0.0
        for i, s in enumerate(slides, 1):
            if str(s.get("role", "")).lower() in ("backup", "appendix"):
                continue
            ts, sp = float(s.get("time_s", 0) or 0), speak_seconds(s.get("notes"))
            tot_speak += sp
            speech_fit.append(dict(slide=i, id=s.get("id"), time_s=ts, speak_s=round(sp)))
            if ts and sp > 1.5 * ts and sp - ts > 15:
                add("warn", "speech_fit", "notes take ~%ds to say (%.0f%% of the %ds slot): trim the script or move detail to "
                    "if_asked/backup" % (sp, 100 * sp / ts, ts), i)
            elif ts >= 45 and sp < 0.3 * ts:
                add("warn", "speech_fit", "notes cover only ~%ds of the %ds slot: write what you will actually say" % (sp, ts), i)
        if dur and tot_speak:
            speech_total = dict(script_min=round(tot_speak / 60, 1), slot_min=float(dur))
            if tot_speak > 1.3 * float(dur) * 60:
                add("warn", "speech_fit", "the whole script takes ~%.1f min to read aloud, the slot is %s min: cut notes, not slides"
                    % (tot_speak / 60, dur))
        # every likely doubt of the audience gets an answer (Q&A item or slide)
        doubt_rows = doubt_coverage(dict(plan, audience=plan.get("audience") or meta.get("audience") or {},
                                         qa=plan.get("qa") or meta.get("qa") or []), slides)
        for r in doubt_rows:
            if not r["answered_by"]:
                add("warn", "doubts", "audience doubt not answered by any Q&A item or slide: '%s' (add a qa item with "
                    "\"doubt\": its index)" % r["doubt"][:70])
        # where every figure/table of the paper went
        ledger = selection_ledger(pm, slides, dict(plan, cuts=plan.get("cuts") or meta.get("cuts") or []))
        missing = [r["ref"] for r in ledger if not r["ok"]]
        if missing:
            add("warn", "ledger", "paper %s not presented and not in plan.cuts: show it, move it to backup, or record why "
                "it is cut" % ", ".join(missing[:8]))
        t = 0.0
        for i, s in enumerate(slides, 1):
            ts = float(s.get("time_s", 0) or 0)
            timeline.append(dict(slide=i, id=s.get("id"), role=s.get("role"), start="%d:%02d" % (t // 60, t % 60), seconds=ts,
                                 title=s.get("title", ""), key_message=s.get("key_message"), transition=s.get("transition")))
            if str(s.get("role", "")).lower() not in ("backup", "appendix"):
                t += ts
    fails = [x for x in issues if x["level"] == "fail"]
    rep = dict(status="needs_fix" if fails else "pass", mode=a.mode, n_slides=n, n_facts=len(facts),
               facts_verified=sum(1 for s in fact_status.values() if s.get("quote_found")),
               facts_not_in_materials=[k for k, s in fact_status.items() if s.get("not_in_materials")],
               numbers_checked=sum(len(e["numbers"]) for e in emap), n_fail=len(fails), n_warn=len(issues) - len(fails),
               issues=issues, timeline=timeline or None)
    if a.mode == "talk":
        rep.update(speech_fit=dict(total=speech_total, slides=speech_fit, rate="4 CJK chars/s, 2.3 English words/s"),
                   doubt_coverage=doubt_rows, selection_ledger=ledger)
        with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "selection_ledger.json"), "w", encoding="utf-8") as f:
            json.dump(dict(figures_tables=ledger, cuts=plan.get("cuts") or meta.get("cuts") or [],
                           doubt_coverage=doubt_rows), f, ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    mp = a.map or os.path.join(os.path.dirname(os.path.abspath(a.out)), "evidence_map.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(dict(deck=meta.get("title"), n_slides=n, facts_file=a.facts, slides=emap,
                       facts={k: dict(v, check=fact_status.get(k)) for k, v in facts.items()}), f, ensure_ascii=False, indent=1)
    L = ["# Page-level evidence map", "", "Every slide item -> fact -> source file, locator ([pN:M] = page N paragraph M of the "
         "extracted text) and the verbatim quote. `ok` = quote found verbatim at that locator.", ""]
    for e in emap:
        L.append("## Slide %d (%s, %s): %s" % (e["slide"], e["id"], e["role"] or "-", e["title"]))
        L.append("")
        L.append("| item | slide text | source | quote (verbatim) | check |")
        L.append("|---|---|---|---|---|")
        for r in e["items"]:
            if not r["sources"]:
                L.append("| %s | %s | - | - | no source |" % (r["item"], r["text"].replace("|", "/")[:90]))
            for s_ in r["sources"]:
                chk = ("auto-matched sim=%.2f" % s_["similarity"]) if s_.get("auto") else "ok" if s_.get("quote_found") and s_.get("loc_ok") is not False else (
                    "loc?" if s_.get("quote_found") else ("NOT IN MATERIALS" if s_.get("not_in_materials") else "CHECK"))
                L.append("| %s | %s | %s %s %s | %s | %s |" % (r["item"], r["text"].replace("|", "/")[:90], s_.get("ref") or "",
                                                              s_.get("file") or "", s_.get("loc") or "",
                                                              str(s_.get("quote") or "").replace("|", "/")[:160], chk))
        if e["numbers"]:
            L.append("")
            L.append("numbers: " + "; ".join("%s -> %s" % (x["value"], (x["found_at"] or ["derived" if x["derived"] else "NOT FOUND"])[0])
                                             for x in e["numbers"]))
        if e["issues"]:
            L.append("")
            L.append("issues: " + " | ".join(e["issues"]))
        L.append("")
    if timeline:
        L += ["## Talk timeline", "", "| start | s | slide | key message | transition |", "|---|---|---|---|---|"]
        for t in timeline:
            L.append("| %s | %d | %s %s | %s | %s |" % (t["start"], t["seconds"], t["slide"], t["title"][:50], t["key_message"] or "",
                                                     t["transition"] or ""))
    with open(mp[:-5] + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    if a.mode == "talk" and (plan or meta.get("audience")):
        T = ["# Talk plan", "", "- goal: %s" % (plan.get("goal") or meta.get("goal")), "- audience: %s" % json.dumps(
            plan.get("audience") or meta.get("audience"), ensure_ascii=False), "- duration: %s min, %d slides (%d backup)" % (
            dur, n, sum(1 for s in slides if str(s.get("role", "")).lower() in ("backup", "appendix"))), ""]
        if plan.get("sections"):
            T += ["## Sections and time budget", "", "| section | minutes | slides | key message | evidence strength |", "|---|---|---|---|---|"]
            for sec in plan["sections"]:
                T.append("| %s | %s | %s | %s | %s |" % (sec.get("name"), sec.get("minutes"), ", ".join(sec.get("slides", [])),
                                                       sec.get("key_message", ""), sec.get("evidence_strength", "")))
            T.append("")
        if timeline:
            T += ["## Slide timeline", "", "| start | sec | slide | title | key message | transition |", "|---|---|---|---|---|---|"]
            for t in timeline:
                T.append("| %s | %d | %s | %s | %s | %s |" % (t["start"], t["seconds"], t["slide"], t["title"][:60], t["key_message"] or "",
                                                         t["transition"] or ""))
            T.append("")
        qa = plan.get("qa") or meta.get("qa") or []
        if qa:
            T += ["## Q&A preparation", "", "| question | answer points | evidence | backup slide |", "|---|---|---|---|"]
            for q in qa:
                T.append("| %s | %s | %s | %s |" % (q.get("question"), "; ".join(q.get("answer_points", [])) if isinstance(
                    q.get("answer_points"), list) else q.get("answer_points"), ", ".join(q.get("evidence", [])), q.get("backup_slide") or "-"))
            T.append("")
        if doubt_rows:
            T += ["## Audience doubts -> where they are answered", "", "| likely doubt | answered by | backup slide |", "|---|---|---|"]
            for r in doubt_rows:
                T.append("| %s | %s | %s |" % (r["doubt"], "; ".join("%s (%s)" % (h["where"], h["how"]) for h in r["answered_by"]) or
                                               "**not answered**", ", ".join(sorted({str(h["backup"]) for h in r["answered_by"] if h.get("backup")})) or "-"))
            T.append("")
        if speech_fit:
            T += ["## Script length vs slide time", "", "Speaking rate: ~4 Chinese characters/s or ~2.3 English words/s (240 chars, "
                  "140 words per minute)." + ((" Whole script ~%.1f min for a %s-min slot." % (speech_total["script_min"], dur))
                                              if speech_total else ""), "", "| slide | slot s | script s | fit |", "|---|---|---|---|"]
            for r in speech_fit:
                fit = "-" if not r["time_s"] else ("long" if r["speak_s"] > 1.5 * r["time_s"] and r["speak_s"] - r["time_s"] > 15
                                                   else "thin" if r["time_s"] >= 45 and r["speak_s"] < 0.3 * r["time_s"] else "ok")
                T.append("| %s %s | %d | %d | %s |" % (r["slide"], r["id"] or "", r["time_s"], r["speak_s"], fit))
            T.append("")
        if ledger:
            T += ["## Selection ledger: every figure/table of the paper", "", "| paper item | page | where it went |", "|---|---|---|"]
            for r in ledger:
                T.append("| %s | %s | %s |" % (r["ref"], r["page"] or "-", r["disposition"]))
            T.append("")
        cuts = plan.get("cuts") or meta.get("cuts") or []
        if cuts:
            T += ["## What was left out and why", ""] + ["- %s: %s" % (c.get("content"), c.get("reason")) if isinstance(c, dict) else "- %s" % c for c in cuts]
        with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "talk_plan.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(T) + "\n")
    print(json.dumps(dict(status=rep["status"], fails=len(fails), warns=rep["n_warn"], facts_verified="%d/%d" % (
        rep["facts_verified"], len(facts)), numbers_checked=rep["numbers_checked"], evidence_map=mp), ensure_ascii=False))
    for x in issues[:40]:
        print("  %s %s slide %s: %s" % (x["level"].upper(), x["check"], x["slide"], x["msg"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
