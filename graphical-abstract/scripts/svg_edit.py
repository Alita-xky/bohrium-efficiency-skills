#!/usr/bin/env python3
"""Source-preserving edits of an existing SVG + export to PNG/PDF with whatever converter exists.

Usage:
  python3 svg_edit.py inventory --svg in.svg [--out inventory.json] [--grep word]
  python3 svg_edit.py apply --svg in.svg --ops ops.json --out edited.svg [--log edit_log.json]
  python3 svg_edit.py export --svg edited.svg --png out.png --pdf out.pdf [--dpi 300]

inventory: one row per element with an id: tag, text (lines joined by ' / '), colours used inside
it (stroke/fill, also from child styles), approximate bbox [x0,y0,x1,y1] in SVG user units; plus
canvas size, default font. Use the bbox to place new text without overlapping anything.

ops.json is a list of operations, each addressed by element id:
  {"op":"set_text","id":"lbl1","text":"new label"}            ("\\n" = new line; block stays centred)
  {"op":"recolor","id":"arrow3","to":"#D55E00"}               (replaces the element's main colour in
                                                               all descendants; "from":"#0072b2" to pick one)
  {"op":"set_style","id":"box3","prop":"fill","value":"#0072B2"}  (on a group: applied to every
                                                               descendant that defines the property)
  {"op":"set_attr","id":"box3","attr":"opacity","value":"0.5"}
  {"op":"translate","id":"grp2","dx":10,"dy":-5}
  {"op":"delete","id":"old_note"}
  {"op":"add_text","id":"new_note","x":40,"y":60,"text":"...","font-size":9,
   "text-anchor":"start","parent":"layer:annotations"}       (font defaults to the figure's font)
Everything not addressed is written back unchanged (same ids, order, attributes). After writing, every
op is VERIFIED on the output file (verified true/false in the log) and all untouched ids are compared
with the input (unchanged_check) - copy both into result.json.
"""
import argparse, copy, json, os, re, shutil, subprocess, sys, tempfile, xml.etree.ElementTree as ET
from collections import Counter

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS, "xlink": "http://www.w3.org/1999/xlink",
      "inkscape": "http://www.inkscape.org/namespaces/inkscape",
      "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"}
for k, v in NS.items():
    ET.register_namespace("" if k == "svg" else k, v)
COLOR_PROPS = ("fill", "stroke")



def load_json_lenient(path):
    """Agent-written JSON: allow raw newlines/tabs inside strings and a trailing comma; on failure
    exit with the line/column and the offending line instead of a traceback."""
    txt = open(path, encoding="utf-8-sig").read()
    try:
        return json.loads(txt, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", txt), strict=False)
        except json.JSONDecodeError as e:
            line = txt.splitlines()[e.lineno - 1] if e.lineno - 1 < len(txt.splitlines()) else ""
            sys.exit(f"INVALID JSON in {path}: {e.msg} at line {e.lineno} col {e.colno}: {line[:160]!r}")

def local(tag):
    return tag.split("}", 1)[-1] if isinstance(tag, str) else ""


def text_of(el):
    lines = []
    for t in el.iter():
        if local(t.tag) == "text":
            s = "".join(t.itertext()).strip()
            if s:
                lines.append(s)
    if not lines and local(el.tag) in ("text", "tspan"):
        lines = ["".join(el.itertext()).strip()]
    return " / ".join(lines)


def style_dict(el):
    st = el.get("style") or ""
    d = {}
    for p in st.split(";"):
        if ":" in p:
            k, v = p.split(":", 1)
            d[k.strip()] = v.strip()
    return d


def write_style(el, d):
    el.set("style", "; ".join(f"{k}: {v}" for k, v in d.items()))


def get_prop(el, prop):
    d = style_dict(el)
    return d.get(prop, el.get(prop))


def colours(el):
    c = Counter()
    for n in el.iter():
        for p in COLOR_PROPS:
            v = get_prop(n, p)
            if v and v.lower() not in ("none", "transparent") and not v.startswith("url("):
                c[v.lower()] += 1
    return c


NUM = r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?"


def bbox_of(el):
    """Approximate bbox from path data, x/y attributes and translate() of descendants."""
    xs, ys = [], []
    for n in el.iter():
        tag = local(n.tag)
        tr = re.search(r"translate\((" + NUM + r")[ ,]+(" + NUM + r")\)", n.get("transform") or "")
        if tag == "path" and n.get("d"):
            nums = [float(v) for v in re.findall(NUM, n.get("d"))]
            xs += nums[0::2]
            ys += nums[1::2]
        elif tag in ("rect", "image", "use"):
            try:
                x, y = float(n.get("x", 0)), float(n.get("y", 0))
                xs += [x, x + float(n.get("width", 0))]
                ys += [y, y + float(n.get("height", 0))]
            except ValueError:
                pass
        elif tag == "text":
            fs = re.search(r"font-size:\s*(" + NUM + ")", n.get("style") or "")
            fs = float(fs.group(1)) if fs else float(re.findall(NUM, n.get("font-size") or "10")[0])
            if n.get("x") is not None:
                x, y = float(re.findall(NUM, n.get("x"))[0]), float(re.findall(NUM, n.get("y") or "0")[0])
            elif tr:
                x, y = float(tr.group(1)), float(tr.group(2))
            else:
                continue
            w = 0.55 * fs * len("".join(n.itertext()))
            anchor = style_dict(n).get("text-anchor", n.get("text-anchor", "start"))
            x0 = x - w / 2 if anchor == "middle" else (x - w if anchor == "end" else x)
            xs += [x0, x0 + w]
            ys += [y - fs, y + 0.3 * fs]
        elif tag in ("circle", "ellipse"):
            try:
                cx, cy = float(n.get("cx", 0)), float(n.get("cy", 0))
                rx = float(n.get("rx", n.get("r", 0)))
                ry = float(n.get("ry", n.get("r", 0)))
                xs += [cx - rx, cx + rx]
                ys += [cy - ry, cy + ry]
            except ValueError:
                pass
    if not xs or not ys:
        return None
    return [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]


def doc_font(root):
    fams, sizes = Counter(), []
    for t in root.iter():
        if local(t.tag) == "text":
            d = style_dict(t)
            f = d.get("font-family", t.get("font-family"))
            if f:
                fams[f] += 1
            s = d.get("font-size", t.get("font-size"))
            if s:
                try:
                    sizes.append(float(re.findall(NUM, s)[0]))
                except IndexError:
                    pass
    sizes.sort()
    return (fams.most_common(1)[0][0] if fams else "Arial, Helvetica, sans-serif",
            sizes[len(sizes) // 2] if sizes else 10.0)


def inventory(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    rows = []
    for el in root.iter():
        eid = el.get("id")
        if not eid or local(el.tag) in ("clipPath", "defs", "style", "metadata"):
            continue
        c = colours(el)
        rows.append(dict(id=eid, tag=local(el.tag), text=text_of(el)[:90],
                         colors=[k for k, _ in c.most_common(3)], bbox=bbox_of(el),
                         label=el.get("{%s}label" % NS["inkscape"])))
    font, size = doc_font(root)
    n_text = sum(1 for e in root.iter() if local(e.tag) == "text")
    n_glyph_paths = sum(1 for e in root.iter() if local(e.tag) in ("path", "use") and
                        (e.get("id") or "").startswith(("glyph", "DejaVu")))
    notes = []
    if n_text == 0:
        notes.append("no <text> elements: text was converted to paths; set_text is impossible - delete the "
                     "glyph group and add_text at its bbox, and report the limitation")
    return dict(width=root.get("width"), height=root.get("height"), viewBox=root.get("viewBox"),
                default_font=font, median_font_size=size, n_text=n_text, n_glyph_paths=n_glyph_paths,
                n_ids=len(rows), notes=notes, elements=rows)


def find_parent(root, child):
    for p in root.iter():
        for c in list(p):
            if c is child:
                return p
    return None


def _line_y(node):
    m = re.search(r"translate\((" + NUM + r")[ ,]+(" + NUM + r")\)", node.get("transform") or "")
    if m:
        return float(m.group(2))
    try:
        return float(re.findall(NUM, node.get("y") or "0")[0])
    except IndexError:
        return 0.0


def _set_line_y(node, y):
    tr = node.get("transform") or ""
    m = re.search(r"translate\((" + NUM + r")[ ,]+(" + NUM + r")\)", tr)
    if m:
        node.set("transform", tr.replace(m.group(0), f"translate({m.group(1)} {y:.3f})"))
    else:
        node.set("y", f"{y:.3f}")
        rot = re.search(r"rotate\((" + NUM + r")[ ,]+(" + NUM + r")[ ,]+(" + NUM + r")\)", tr)
        if rot:
            node.set("transform", tr.replace(rot.group(0), f"rotate({rot.group(1)} {rot.group(2)} {y:.3f})"))


def set_text(root, el, text):
    lines = str(text).split("\n")
    if local(el.tag) in ("text", "tspan"):
        tnodes, holder = [el], find_parent(root, el)
    else:
        tnodes = [c for c in el.iter() if local(c.tag) == "text"]
        holder = el
    if not tnodes:
        raise KeyError("element has no <text> descendant (text may be paths): delete it and use add_text")
    fs = re.search(r"font-size:\s*(" + NUM + ")", tnodes[0].get("style") or "") or \
        re.search("(" + NUM + ")", tnodes[0].get("font-size") or "12")
    lh = 1.2 * float(fs.group(1))
    ys = [_line_y(n) for n in tnodes]
    if len(ys) > 1:
        lh = (ys[-1] - ys[0]) / (len(ys) - 1) or lh
    centre = sum(ys) / len(ys)
    nodes = []
    for i, ln in enumerate(lines):
        if i < len(tnodes):
            node = tnodes[i]
        else:
            node = copy.deepcopy(tnodes[-1])
            par = find_parent(root, tnodes[-1])
            par = holder if par is None else par
            par.insert(list(par).index(nodes[-1]) + 1, node)
        for c in list(node):
            if local(c.tag) == "tspan":
                node.remove(c)
        node.text = ln
        nodes.append(node)
    for extra in tnodes[len(lines):]:
        par = find_parent(root, extra)
        if par is not None:
            par.remove(extra)
    if len(lines) != len(tnodes):  # keep the block vertically centred where it was
        start = centre - lh * (len(lines) - 1) / 2
        for i, node in enumerate(nodes):
            _set_line_y(node, start + i * lh)
    anchored = all(style_dict(n).get("text-anchor", n.get("text-anchor")) in ("middle", "end") for n in nodes)
    return "re-centred vertically" if len(lines) != len(tnodes) else "", anchored


def deep_set(el, prop, value, only_from=None):
    """Set prop on el and every descendant that defines it; returns list of (before) values changed."""
    before = []
    for n in el.iter():
        d = style_dict(n)
        cur = d.get(prop, n.get(prop))
        if n is not el and cur is None:
            continue
        if cur is not None and cur.lower() in ("none",) and prop in COLOR_PROPS:
            continue
        if only_from and (cur or "").lower() != only_from.lower():
            continue
        before.append(cur)
        if prop in d or (n.get(prop) is None and n.get("style") is not None):
            d[prop] = value
            write_style(n, d)
        else:
            n.set(prop, value)
    return before


def snapshot(root):
    snap = {}
    for el in root.iter():
        eid = el.get("id")
        if eid:
            snap[eid] = ET.tostring(el, encoding="unicode")
    return snap


def apply_ops(svg_path, ops, out_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    before_snap = snapshot(root)
    byid = {el.get("id"): el for el in root.iter() if el.get("id")}
    font, med = doc_font(root)
    log, touched = [], set()
    for op in ops:
        kind, eid = op.get("op"), op.get("id")
        el = byid.get(eid) if eid else None
        entry = dict(op=op, status="ok")
        try:
            if kind == "add_text":
                parent = byid.get(op.get("parent")) if op.get("parent") else None
                if parent is None:
                    parent = next((byid[k] for k in ("layer:annotations", "layer:canvas") if byid.get(k) is not None), root)
                if op["id"] in byid:
                    raise KeyError(f"id {op['id']} already exists")
                t = ET.SubElement(parent, "{%s}text" % SVG_NS)
                t.set("id", op["id"])
                t.set("x", str(op.get("x", 10)))
                t.set("y", str(op.get("y", 20)))
                st = {"font-size": f"{float(op.get('font-size', med)):g}px",
                      "font-family": op.get("font-family", font),
                      "fill": op.get("fill", "#111111"), "text-anchor": op.get("text-anchor", "start")}
                if op.get("font-style"):
                    st["font-style"] = op["font-style"]
                write_style(t, st)
                t.text = op["text"]
                byid[op["id"]] = t
                entry["after"] = op["text"]
                touched.add(op["id"])
                new_bb = bbox_of(t)
                hits = []
                for oid, other in byid.items():
                    if oid == op["id"] or oid.startswith(("layer:", "figure_", "patch_1", "axes_")) or \
                            (oid.startswith("panel:") and not oid.endswith(":title")) or local(other.tag) not in ("g", "text"):
                        continue
                    bb = bbox_of(other)
                    if bb and new_bb and bb[2] - bb[0] < 0.9 * float(re.findall(NUM, root.get("width") or "1e9")[0]) and \
                            min(bb[2], new_bb[2]) - max(bb[0], new_bb[0]) > 1 and min(bb[3], new_bb[3]) - max(bb[1], new_bb[1]) > 1:
                        hits.append(oid)
                if hits:
                    entry["warning"] = f"new text {op['id']} overlaps {hits[:5]} - pick x/y outside the inventory bboxes"
            elif el is None:
                raise KeyError(f"id {eid} not found (see inventory)")
            elif kind == "set_text":
                entry["before"] = text_of(el)
                note, anchored = set_text(root, el, op["text"])
                entry["after"] = op["text"]
                if note:
                    entry["note"] = note
                if not anchored:
                    entry["note"] = (entry.get("note", "") + "; start-anchored text: x kept, re-centre with translate "
                                     "if the length changed a lot").strip("; ")
                touched.add(eid)
            elif kind == "recolor":
                c = colours(el)
                src = (op.get("from") or (c.most_common(1)[0][0] if c else "")).lower()
                n = 0
                for prop in COLOR_PROPS:
                    n += len(deep_set(el, prop, op["to"], only_from=src))
                if n == 0:
                    raise ValueError(f"colour {src or '?'} not found inside {eid}; inventory colours: {list(c)}")
                entry.update(before=src, after=op["to"], nodes_changed=n)
                touched.update(x.get("id") for x in el.iter() if x.get("id"))  # the op addresses the whole group
            elif kind == "set_style":
                b = deep_set(el, op["prop"], str(op["value"]))
                entry.update(before=sorted({str(x) for x in b}), after=str(op["value"]), nodes_changed=len(b))
                touched.update(x.get("id") for x in el.iter() if x.get("id"))
            elif kind == "set_attr":
                if op["attr"] in COLOR_PROPS + ("stroke-width", "opacity", "font-size", "font-family") and local(el.tag) == "g":
                    b = deep_set(el, op["attr"], str(op["value"]))
                    entry["note"] = "applied to descendants (group children carry their own styles)"
                    touched.update(x.get("id") for x in el.iter() if x.get("id"))
                else:
                    b = [el.get(op["attr"])]
                    el.set(op["attr"], str(op["value"]))
                entry.update(before=sorted({str(x) for x in b}), after=str(op["value"]))
                touched.add(eid)
            elif kind == "translate":
                tr = el.get("transform") or ""
                entry["before"] = tr
                el.set("transform", (f"translate({op.get('dx', 0)},{op.get('dy', 0)}) " + tr).strip())
                entry["after"] = el.get("transform")
                touched.add(eid)
            elif kind == "delete":
                parent = find_parent(root, el)
                entry["before"] = dict(tag=local(el.tag), text=text_of(el)[:80])
                parent.remove(el)
                touched.add(eid)
                for sub in el.iter():
                    if sub.get("id"):
                        touched.add(sub.get("id"))
            else:
                raise ValueError(f"unknown op {kind}")
        except Exception as e:  # keep going, record failure
            entry["status"] = "error"
            entry["error"] = repr(e)
        log.append(entry)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    # verification on the written file
    root2 = ET.parse(out_path).getroot()
    by2 = {el.get("id"): el for el in root2.iter() if el.get("id")}
    for entry in log:
        op = entry["op"]
        if entry["status"] != "ok":
            entry["verified"] = False
            continue
        k, eid = op.get("op"), op.get("id")
        el = by2.get(eid)
        if k == "delete":
            entry["verified"] = el is None
        elif el is None:
            entry["verified"] = False
        elif k in ("set_text", "add_text"):
            entry["verified"] = text_of(el).replace(" / ", "\n") == str(op["text"]).strip() or \
                text_of(el) == str(op["text"]).replace("\n", " / ")
        elif k == "recolor":
            entry["verified"] = entry["before"] not in colours(el) and op["to"].lower() in colours(el)
        elif k in ("set_style", "set_attr"):
            prop = op.get("prop") or op.get("attr")
            vals = {get_prop(n, prop) for n in el.iter() if get_prop(n, prop) is not None}
            entry["verified"] = vals == {str(op["value"])}
        else:
            entry["verified"] = True
    after_snap = snapshot(root2)
    untouched = [i for i in before_snap if i not in touched]
    changed = []
    for i in untouched:
        # ancestors of touched elements change by construction; compare leaves only
        el = by2.get(i)
        if el is None:
            changed.append(i)
            continue
        if any(x.get("id") in touched for x in el.iter()):
            continue
        if before_snap[i] != after_snap.get(i):
            changed.append(i)
    unchanged = dict(n_ids_input=len(before_snap), n_touched=len(touched),
                     n_untouched_identical=len([i for i in untouched if i not in changed]),
                     unexpected_changes=changed[:20])
    return log, unchanged


def _pdf_to_png(pdf, png, dpi, tried):
    if shutil.which("pdftoppm"):
        base = os.path.splitext(png)[0] + "__tmp"
        tried.append(f"pdftoppm -r {dpi}")
        if subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile", pdf, base],
                          capture_output=True, timeout=120).returncode == 0 and os.path.exists(base + ".png"):
            shutil.move(base + ".png", png)
            return True
    try:
        import fitz  # PyMuPDF
        tried.append("pymupdf")
        doc = fitz.open(pdf)
        doc[0].get_pixmap(dpi=dpi).save(png)
        return os.path.exists(png)
    except Exception:
        return False


def png_pixels(path):
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        import struct
        return list(struct.unpack(">II", head[16:24])) if head[:8] == b"\x89PNG\r\n\x1a\n" else None
    except OSError:
        return None


def export(svg, png=None, pdf=None, dpi=300):
    """Try converters in order; report which one worked. Never fakes an export.
    Order: rsvg-convert, cairosvg, inkscape, chromium, LibreOffice (PDF, then PDF->PNG at dpi)."""
    done, tried = {}, []
    def run(cmd):
        tried.append(" ".join(cmd[:3]) + " ...")
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=180).returncode == 0
        except Exception:
            return False
    soffice_pdf = None
    for target, fmt in ((pdf, "pdf"), (png, "png")):
        if not target:
            continue
        ok, how = False, None
        if shutil.which("rsvg-convert"):
            ok = run(["rsvg-convert", "-f", fmt, "-d", str(dpi), "-p", str(dpi), "-o", target, svg]); how = "rsvg-convert"
        if not ok:
            try:
                import cairosvg  # noqa
                (cairosvg.svg2png if fmt == "png" else cairosvg.svg2pdf)(url=svg, write_to=target, dpi=dpi)
                tried.append("cairosvg"); ok = os.path.exists(target); how = "cairosvg"
            except Exception:
                pass
        if not ok and shutil.which("inkscape"):
            ok = run(["inkscape", svg, f"--export-type={fmt}", f"--export-filename={target}", f"--export-dpi={dpi}"])
            how = "inkscape"
        if not ok:
            for b in ("chromium", "chromium-browser", "google-chrome"):
                if shutil.which(b):
                    if fmt == "pdf":
                        ok = run([b, "--headless", "--disable-gpu", "--no-sandbox", f"--print-to-pdf={target}", svg])
                    else:
                        ok = run([b, "--headless", "--disable-gpu", "--no-sandbox", f"--screenshot={target}",
                                  "--window-size=1600,1000", svg])
                    if ok:
                        how = b
                        break
        if not ok and fmt == "png" and (soffice_pdf or (pdf and os.path.exists(pdf))):
            ok = _pdf_to_png(soffice_pdf or pdf, target, dpi, tried); how = "pdf->png"
        if not ok:
            for b in ("soffice", "libreoffice"):
                if shutil.which(b):
                    tmp = tempfile.mkdtemp()
                    conv = "pdf" if fmt == "pdf" else "pdf"
                    if run([b, "--headless", "--convert-to", conv, "--outdir", tmp, svg]):
                        produced = os.path.join(tmp, os.path.splitext(os.path.basename(svg))[0] + ".pdf")
                        if os.path.exists(produced):
                            if fmt == "pdf":
                                shutil.move(produced, target); soffice_pdf = target
                                ok, how = True, b
                            else:
                                ok = _pdf_to_png(produced, target, dpi, tried); how = b + " pdf->png"
                    if not ok and fmt == "png" and run([b, "--headless", "--convert-to", "png", "--outdir", tmp, svg]):
                        produced = os.path.join(tmp, os.path.splitext(os.path.basename(svg))[0] + ".png")
                        if os.path.exists(produced):
                            shutil.move(produced, target)
                            ok, how = True, b + " (96 dpi)"
                    break
        res = dict(path=target, ok=bool(ok and os.path.exists(target)), via=how,
                   bytes=os.path.getsize(target) if os.path.exists(target) else 0)
        if fmt == "png" and res["ok"]:
            res["pixels"] = png_pixels(target)
        done[fmt] = res
    return dict(results=done, tried=tried)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("inventory"); p1.add_argument("--svg", required=True); p1.add_argument("--out")
    p1.add_argument("--grep", default=None, help="print only rows whose id/text contains this")
    p2 = sub.add_parser("apply"); p2.add_argument("--svg", required=True); p2.add_argument("--ops", required=True)
    p2.add_argument("--out", required=True); p2.add_argument("--log")
    p3 = sub.add_parser("export"); p3.add_argument("--svg", required=True); p3.add_argument("--png")
    p3.add_argument("--pdf"); p3.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    if a.cmd == "inventory":
        inv = inventory(a.svg)
        if a.out:
            json.dump(inv, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({k: inv[k] for k in ("width", "height", "viewBox", "default_font", "median_font_size",
                                             "n_text", "n_ids", "notes")}, ensure_ascii=False))
        rows = [r for r in inv["elements"] if not a.grep or a.grep.lower() in (r["id"] + r["text"]).lower()]
        rows = [r for r in rows if not re.fullmatch(r"(patch|line2d|text|axes|matplotlib\.axis)_\d+", r["id"]) or r["text"]]
        for r in rows[:90]:
            print(json.dumps({k: v for k, v in r.items() if v not in (None, "", [])}, ensure_ascii=False))
        if len(rows) > 90:
            print(f"... {len(rows) - 90} more rows in {a.out or 'the --out file'} (use --grep)")
    elif a.cmd == "apply":
        log, unchanged = apply_ops(a.svg, load_json_lenient(a.ops), a.out)
        if a.log:
            json.dump(dict(source=a.svg, output=a.out, changes=log, unchanged_check=unchanged),
                      open(a.log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        errs = [x for x in log if x["status"] != "ok" or not x.get("verified")]
        warns = [x["warning"] for x in log if x.get("warning")]
        print(json.dumps(dict(applied=len(log) - len(errs), not_applied_or_unverified=errs, warnings=warns,
                              unchanged_check=unchanged), ensure_ascii=False))
        return 0 if not errs else 2
    else:
        r = export(a.svg, a.png, a.pdf, a.dpi)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if all(v["ok"] for v in r["results"].values()) else 2


if __name__ == "__main__":
    sys.exit(main())
