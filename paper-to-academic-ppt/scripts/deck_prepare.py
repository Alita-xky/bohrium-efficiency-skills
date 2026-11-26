#!/usr/bin/env python3
"""deck_prepare.py - one call at the start of a case: read case.json/task.md, classify the inputs, extract text with
locators, map the paper, inspect the template and inventory a starting deck, check tools and fonts.

Usage:
  python3 deck_prepare.py --input /input --work /app/work [--task task.md] [--gallery]

Writes /app/work/prepare.json and prints a compact summary:
  constraints guessed from case/task (slide count, minutes, audience, language) - verify them in task.md;
  output_contract fields; main paper PDF -> paper_map.json (+ figs/fig_candidates.png);
  template (.potx/.pptx) -> template_report.json; starting deck -> deck_inventory.json + start_render/;
  tools (python-pptx, soffice, pdftoppm, ...) and fonts. Office documents (.ppt/.doc/.docx/.odt) are converted
  with LibreOffice so they can be read. Nothing is summarised or invented.
"""
import argparse, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pptkit as K  # noqa: E402

PY = sys.executable


# default evidence rows (kind::regex::label::n): problem, idea, method, main results, cost, limits, claims - verbatim
AUTO_FACTS = [
    "problem::challeng|bottleneck|suffer|difficult|limited by|remains (an )?open|costly|expensive|问题|挑战|瓶颈|困难",
    "method::we propose|we introduce|we present|in this (paper|work)|our (method|approach|model|framework)|本文提出|我们提出",
    "method::consists? of|composed of|architecture|pipeline|framework|stage|module|模块|框架|流程",
    "result::outperform|state[- ]of[- ]the[- ]art|surpass|achiev|improv|优于|提升|提高|达到",
    "result::outperform|state[- ]of[- ]the[- ]art|surpass|achiev|improv|优于|提升|提高|达到::::2",
    "result::\\d+(\\.\\d+)?\\s?(%|×|x faster|times)|(accuracy|BLEU|F1|AUC|error|RMSE|mAP|准确率|误差)\\D{0,30}\\d",
    "result::\\d+(\\.\\d+)?\\s?(%|×|x faster|times)|(accuracy|BLEU|F1|AUC|error|RMSE|mAP|准确率|误差)\\D{0,30}\\d::::2",
    "data::dataset|benchmark|corpus|samples|participants|trained on|数据集|样本",
    "cost::GPUs?\\b|GPU hours|\\d+ (hours|days)|FLOPs|training (cost|time)|computational cost|runtime|latency|计算量|耗时|成本",
    "limit::limitation|future work|fail|cannot|does not|restricted|only (applies|works)|局限|不足|未来工作",
    "claim::we (show|demonstrate|find)|our contributions?|in (summary|conclusion)|conclude|结论|总之",
]


def sh(args, timeout=300):
    try:
        p = subprocess.run([PY] + args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", repr(e)


def walk_keys(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield path + "/" + str(k), k, v
            yield from walk_keys(v, path + "/" + str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from walk_keys(v, path + "[%d]" % i)


def guess_constraints(case, task_text):
    c = {}
    pats = {"slides": r"(slide|page|页|幻灯)", "minutes": r"(minute|duration|time_limit|talk_time|时长|分钟)",
            "audience": r"(audience|听众|受众)", "language": r"(language|lang|语言)", "purpose": r"(purpose|goal|occasion|场合|目的|目标)",
            "template": r"(template|模板|master)", "deck": r"(deck|starting|source_ppt|ppt_file|初稿|原稿)"}
    for path, k, v in walk_keys(case):
        if "output_contract" in path:
            continue
        if isinstance(v, (str, int, float)) and not isinstance(v, bool):
            for name, rx in pats.items():
                if re.search(rx, str(k), re.I) and name not in c:
                    c[name] = dict(value=v, from_key=path)
    t = task_text or ""
    m = re.search(r"(\d+)\s*(?:-|to|~|–|至)?\s*(\d+)?\s*(?:slides|pages|页|张)", t, re.I)
    if m and "slides" not in c:
        c["slides"] = dict(value=m.group(0), from_key="task.md text")
    m = re.search(r"(\d+)\s*(?:min(?:ute)?s?|分钟)", t, re.I)
    if m and "minutes" not in c:
        c["minutes"] = dict(value=m.group(1), from_key="task.md text")
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="/input")
    ap.add_argument("--work", default="/app/work")
    ap.add_argument("--task", default=None)
    ap.add_argument("--gallery", action="store_true", help="render one slide per template layout (about 10 s)")
    ap.add_argument("--outdir", default="/app/output")
    ap.add_argument("--no-draft", action="store_true", help="do not start the background safety draft")
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    t0p = os.path.join(a.work, ".t0")
    if not os.path.exists(t0p):
        open(t0p, "w").write(str(time.time()))
    task = a.task
    for cand in ([a.task] if a.task else []) + [os.path.join(a.input, "task.md"), "task.md", "/app/task.md",
                                                  os.path.join(os.path.dirname(a.input), "task.md")]:
        if cand and os.path.exists(cand):
            task = cand
            break
    task_text = open(task, encoding="utf-8", errors="ignore").read() if task and os.path.exists(task) else ""
    case = {}
    cp = os.path.join(a.input, "case.json")
    if os.path.exists(cp):
        try:
            case = json.load(open(cp, encoding="utf-8"))
        except Exception as e:
            case = {"_error": repr(e)}
    files = []
    for root, _, names in os.walk(a.input):
        for n in sorted(names):
            files.append(os.path.join(root, n))
    conv_dir = os.path.join(a.work, "converted")
    pdfs, templates, decks, tables, images, others, converted = [], [], [], [], [], [], []
    for p in files:
        ext = os.path.splitext(p)[1].lower()
        base = os.path.basename(p).lower()
        if ext == ".pdf":
            pdfs.append(p)
        elif ext in (".potx", ".pot", ".otp"):
            templates.append(p)
        elif ext in (".pptx", ".ppt", ".pps", ".ppsx", ".odp"):
            src = p
            if ext in (".ppt", ".pps", ".odp"):
                q, log = K.soffice_convert(p, conv_dir, "pptx")
                converted.append(dict(src=p, dst=q, log=None if q else log))
                if not q:
                    others.append(p)
                    continue
                src = q
            try:
                n_sl = len(K.slide_list(K.Package(src)))
            except Exception:
                n_sl = -1
            if re.search(r"template|模板|master|母版|theme", base) or n_sl == 0:
                templates.append(src)
            else:
                decks.append(src)
        elif ext in (".csv", ".tsv", ".xlsx", ".xls"):
            tables.append(p)
        elif ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg", ".gif", ".bmp", ".emf"):
            images.append(p)
        elif ext in (".doc", ".docx", ".odt", ".rtf"):
            q, log = K.soffice_convert(p, conv_dir, "pdf")
            converted.append(dict(src=p, dst=q, log=None if q else log))
            if q:
                pdfs.append(q)
        elif base not in ("case.json",):
            others.append(p)
    rc, out, err = sh([os.path.join(HERE, "extract_materials.py"), "--input", a.input, "--work", a.work] +
                      (["--task", task] if task else []))
    extract_log = (out + err)[-1500:]
    if converted and any(c["dst"] for c in converted):
        rc2, out2, err2 = sh([os.path.join(HERE, "extract_materials.py"), "--input", conv_dir, "--work", a.work, "--render-pages", "0"])
        extract_log += "\n[converted] " + (out2 + err2)[-600:]
    paper = max(pdfs, key=os.path.getsize) if pdfs else None
    if isinstance(case, dict):
        for path, k, v in walk_keys(case):
            if isinstance(v, str) and v.lower().endswith(".pdf") and re.search(r"paper|论文|source|main", str(k) + v, re.I):
                cand = v if os.path.isabs(v) else os.path.join(a.input, v)
                if os.path.exists(cand):
                    paper = cand
                    break
    res = dict(task=task, case_id=None, output_contract=None, constraints=guess_constraints(case, task_text),
               paper=paper, pdfs=pdfs, templates=templates, decks=decks, tables=tables, images=images, others=others,
               converted=converted, materials_dir=os.path.join(a.work, "materials"))
    for path, k, v in walk_keys(case):
        if k in ("case_id", "id") and res["case_id"] is None and isinstance(v, (str, int)):
            res["case_id"] = v
        if k == "output_contract" and res["output_contract"] is None:
            res["output_contract"] = v
    if paper:
        rc, out, err = sh([os.path.join(HERE, "paper_map.py"), "--pdf", paper, "--materials", os.path.join(a.work, "materials"),
                           "--out", os.path.join(a.work, "paper_map.json"), "--figdir", os.path.join(a.work, "figs")])
        res["paper_map"] = os.path.join(a.work, "paper_map.json") if rc == 0 else None
        res["paper_map_summary"] = out[-2500:] if rc == 0 else (out + err)[-800:]
        fp = os.path.join(a.work, "facts.json")
        if rc == 0 and not os.path.exists(fp):      # evidence table built automatically: the agent cites F-ids at once
            rc3, out3, err3 = sh([os.path.join(HERE, "paper_map.py"), "fact"] + AUTO_FACTS +
                                 ["--out", fp, "--materials", os.path.join(a.work, "materials"),
                                  "--map", os.path.join(a.work, "paper_map.json"), "--width", "170", "--skip-used"])
            rows = [l for l in out3.splitlines() if re.match(r"F\d+ ", l)]
            res["facts_summary"] = ("\n".join(rows) + "\n%s: %d rows. Cite these ids in spec.json; a needed claim missing -> "
                                    "paper_map.py fact \"kind::regex::label\"; a wrong row -> --drop Fn" % (fp, len(rows))) \
                if rows else None
    if templates:
        args = [os.path.join(HERE, "deck_template.py"), "inspect", "--template", templates[0], "--out",
                os.path.join(a.work, "template_report.json")]
        if a.gallery:
            args += ["--gallery", os.path.join(a.work, "template_gallery")]
        rc, out, err = sh(args)
        res["template_report"] = os.path.join(a.work, "template_report.json") if rc == 0 else None
        res["template_summary"] = (out if rc == 0 else out + err)[-2500:]
    if decks:
        rc, out, err = sh([os.path.join(HERE, "deck_edit.py"), "inventory", "--pptx", decks[0], "--out",
                           os.path.join(a.work, "deck_inventory.json")])
        res["deck_inventory"] = os.path.join(a.work, "deck_inventory.json") if rc == 0 else None
        res["deck_summary"] = (out if rc == 0 else out + err)[-3000:]
        rc, out, err = sh([os.path.join(HERE, "deck_render.py"), "--pptx", decks[0], "--outdir",
                           os.path.join(a.work, "start_render"), "--name", "start"], timeout=400)
        res["start_render"] = out.strip().splitlines()[0] if out.strip() else err[-300:]
    tools = {}
    for mod in ("pptx", "matplotlib", "numpy", "PIL", "fitz", "openpyxl", "lxml"):
        try:
            m = __import__(mod)
            tools[mod] = getattr(m, "__version__", "yes")
        except Exception:
            tools[mod] = None
    for t in ("pdftotext", "pdftoppm", "pdffonts", "fc-list", "tesseract"):
        tools[t] = bool(shutil.which(t))
    tools["soffice"] = K.find_soffice()
    tools["python"] = sys.version.split()[0]
    res["tools"] = tools
    res["fonts"] = K.pick_fonts()
    res["build_backend"] = "python-pptx" if tools.get("pptx") else "ooxml-stdlib (python-pptx missing)"
    with open(os.path.join(a.work, "prepare.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print("case_id: %s | task: %s" % (res["case_id"], task))
    oc = res["output_contract"]
    print("output_contract fields: %s" % (list(oc.keys()) if isinstance(oc, dict) else oc))
    print("constraints (guessed, verify in task.md): %s" % json.dumps({k: v["value"] for k, v in res["constraints"].items()}, ensure_ascii=False))
    print("paper: %s | pdfs: %d | templates: %s | decks: %s | tables: %s | images: %d | other: %s" % (
        paper, len(pdfs), templates, decks, [os.path.basename(t) for t in tables], len(images), [os.path.basename(o) for o in others]))
    if converted:
        print("converted: %s" % json.dumps(converted, ensure_ascii=False))
    print("tools: %s" % json.dumps(tools))
    print("fonts: %s | build backend: %s" % (json.dumps({k: res['fonts'][k] for k in ('latin', 'ea')}, ensure_ascii=False), res["build_backend"]))
    for k in ("paper_map_summary", "facts_summary", "template_summary", "deck_summary"):
        if res.get(k):
            print("---- " + k)
            print(res[k].strip())
    if res.get("start_render"):
        print("---- starting deck render: " + res["start_render"])
    if not paper and not decks:
        print("WARN no paper PDF and no deck found: read materials/materials_index.json")
    try:
        editor = "source-preserving-editor" in open(os.path.join(HERE, "..", "SKILL.md"), encoding="utf-8").read(400)
    except Exception:
        editor = False
    editor = editor or bool(decks and not paper and not templates)
    if res.get("paper_map") and not editor and not a.no_draft and not os.path.exists(os.path.join(a.work, ".final_started")):
        try:
            p = subprocess.Popen([sys.executable, os.path.join(HERE, "deck_draft.py"), "--work", a.work, "--input", a.input,
                                  "--outdir", a.outdir], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            open(os.path.join(a.work, "draft.pid"), "w").write(str(p.pid))
            print("SAFETY DRAFT: building in the background -> %s in about a minute (deck, PDF, previews, result.json, "
                  "status partial; log %s/draft.log). It is only a fallback: your deck_make run replaces it." % (a.outdir, a.work))
        except Exception as e:
            print("WARN safety draft not started: %r" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
