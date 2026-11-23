#!/usr/bin/env python3
"""Source-preserving edits of an existing SVG + export to PNG/PDF with whatever converter exists.

Usage:
  python3 svg_edit.py inventory --svg in.svg [--out inventory.json]
  python3 svg_edit.py apply --svg in.svg --ops ops.json --out edited.svg [--log edit_log.json]
  python3 svg_edit.py export --svg edited.svg --png out.png --pdf out.pdf [--dpi 300]

ops.json is a list of operations, each addressed by element id:
  {"op":"set_text","id":"lbl1","text":"new label"}
  {"op":"set_attr","id":"box3","attr":"fill","value":"#0072B2"}       (also stroke, font-size, opacity ...)
  {"op":"set_style","id":"box3","prop":"fill","value":"#0072B2"}      (edits the CSS style attribute)
  {"op":"translate","id":"grp2","dx":10,"dy":-5}
  {"op":"delete","id":"old_note"}
  {"op":"add_text","parent":"layer1","id":"new_note","x":40,"y":60,"text":"...","font-size":"12"}
Everything not addressed is written back unchanged (same ids, order, attributes). The log records
before/after values so every change is traceable.
"""
import argparse, copy, json, os, re, shutil, subprocess, sys, xml.etree.ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS, "xlink": "http://www.w3.org/1999/xlink",
      "inkscape": "http://www.inkscape.org/namespaces/inkscape",
      "sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"}
for k, v in NS.items():
    ET.register_namespace("" if k == "svg" else k, v)


def local(tag):
    return tag.split("}", 1)[-1]


def text_of(el):
    return "".join(el.itertext()).strip()


def inventory(svg_path):
    tree = ET.parse(svg_path)
    rows = []
    for el in tree.iter():
        eid = el.get("id")
        if not eid:
            continue
        rows.append(dict(id=eid, tag=local(el.tag), text=text_of(el)[:80] if local(el.tag) in ("text", "tspan", "g") else "",
                         fill=el.get("fill"), stroke=el.get("stroke"), style=(el.get("style") or "")[:80],
                         label=el.get("{%s}label" % NS["inkscape"])))
    root = tree.getroot()
    return dict(width=root.get("width"), height=root.get("height"), viewBox=root.get("viewBox"),
                n_ids=len(rows), elements=rows)


def find_parent(root, child):
    for p in root.iter():
        for c in list(p):
            if c is child:
                return p
    return None


def apply_ops(svg_path, ops, out_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    byid = {el.get("id"): el for el in root.iter() if el.get("id")}
    log = []
    for op in ops:
        kind, eid = op.get("op"), op.get("id")
        el = byid.get(eid) if eid else None
        entry = dict(op=op, status="ok")
        try:
            if kind == "add_text":
                parent = byid.get(op.get("parent")) if op.get("parent") else root
                if parent is None:
                    raise KeyError(f"parent {op.get('parent')} not found")
                t = ET.SubElement(parent, "{%s}text" % SVG_NS)
                for k in ("id", "x", "y", "font-size", "font-family", "fill", "text-anchor"):
                    if op.get(k) is not None:
                        t.set(k, str(op[k]))
                t.text = op["text"]
                byid[op["id"]] = t
                entry["after"] = op["text"]
            elif el is None:
                raise KeyError(f"id {eid} not found")
            elif kind == "set_text":
                entry["before"] = text_of(el)
                lines = str(op["text"]).split("\n")
                if local(el.tag) in ("text", "tspan"):
                    tnodes, holder = [el], find_parent(root, el)
                else:
                    tnodes = [c for c in el.iter() if local(c.tag) == "text"]
                    holder = el
                if not tnodes:
                    raise KeyError(f"id {eid} has no <text> descendant")
                # one <text> per line (matplotlib/Inkscape style); reuse, drop or clone line nodes
                for i, ln in enumerate(lines):
                    if i < len(tnodes):
                        node = tnodes[i]
                    else:
                        node = copy.deepcopy(tnodes[-1])
                        tr = node.get("transform") or ""
                        m = re.search(r"translate\(([-\d.]+)[ ,]+([-\d.]+)\)", tr)
                        fs = re.search(r"font-size:\s*([\d.]+)", node.get("style") or "") or re.search(r"([\d.]+)", node.get("font-size") or "12")
                        dy = 1.2 * float(fs.group(1)) * (i - len(tnodes) + 1)
                        if m:
                            node.set("transform", tr.replace(m.group(0), f"translate({m.group(1)} {float(m.group(2)) + dy:.3f})"))
                        else:
                            node.set("y", str(float(node.get("y") or 0) + dy))
                        par = find_parent(root, tnodes[-1])
                        par = holder if par is None else par
                        par.insert(list(par).index(tnodes[-1]) + (i - len(tnodes) + 1), node)
                    for c in list(node):
                        if local(c.tag) == "tspan":
                            node.remove(c)
                    node.text = ln
                for extra in tnodes[len(lines):]:
                    par = find_parent(root, extra)
                    if par is not None:
                        par.remove(extra)
                entry["after"] = op["text"]
                entry["note"] = "line positions kept from the original; re-centre manually if alignment matters"
            elif kind == "set_attr":
                entry["before"] = el.get(op["attr"])
                el.set(op["attr"], str(op["value"]))
                entry["after"] = str(op["value"])
            elif kind == "set_style":
                style = el.get("style") or ""
                props = dict(p.split(":", 1) for p in style.split(";") if ":" in p)
                props = {k.strip(): v.strip() for k, v in props.items()}
                entry["before"] = props.get(op["prop"])
                props[op["prop"]] = str(op["value"])
                el.set("style", ";".join(f"{k}:{v}" for k, v in props.items()))
                entry["after"] = str(op["value"])
            elif kind == "translate":
                tr = el.get("transform") or ""
                entry["before"] = tr
                el.set("transform", (tr + f" translate({op.get('dx', 0)},{op.get('dy', 0)})").strip())
                entry["after"] = el.get("transform")
            elif kind == "delete":
                parent = find_parent(root, el)
                entry["before"] = dict(tag=local(el.tag), text=text_of(el)[:80])
                parent.remove(el)
            else:
                raise ValueError(f"unknown op {kind}")
        except Exception as e:  # keep going, record failure
            entry["status"] = "error"
            entry["error"] = repr(e)
        log.append(entry)
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    return log


def export(svg, png=None, pdf=None, dpi=300):
    """Try converters in order; report which one worked. Never fakes an export."""
    done, tried = {}, []
    def run(cmd):
        tried.append(" ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=120).returncode == 0
    for target, fmt in ((png, "png"), (pdf, "pdf")):
        if not target:
            continue
        ok = False
        if shutil.which("rsvg-convert"):
            ok = run(["rsvg-convert", "-f", fmt, "-d", str(dpi), "-p", str(dpi), "-o", target, svg])
        if not ok:
            try:
                import cairosvg  # noqa
                (cairosvg.svg2png if fmt == "png" else cairosvg.svg2pdf)(url=svg, write_to=target, dpi=dpi)
                tried.append("cairosvg"); ok = os.path.exists(target)
            except Exception:
                pass
        if not ok and shutil.which("inkscape"):
            ok = run(["inkscape", svg, f"--export-type={fmt}", f"--export-filename={target}", f"--export-dpi={dpi}"])
        if not ok:
            for b in ("chromium", "chromium-browser", "google-chrome"):
                if shutil.which(b):
                    if fmt == "pdf":
                        ok = run([b, "--headless", "--disable-gpu", "--no-sandbox", f"--print-to-pdf={target}", svg])
                    else:
                        ok = run([b, "--headless", "--disable-gpu", "--no-sandbox", f"--screenshot={target}",
                                  "--window-size=1600,1000", svg])
                    if ok:
                        break
        if not ok:
            for b in ("soffice", "libreoffice"):
                if shutil.which(b):
                    outdir = os.path.dirname(os.path.abspath(target))
                    if run([b, "--headless", "--convert-to", fmt, "--outdir", outdir, svg]):
                        produced = os.path.join(outdir, os.path.splitext(os.path.basename(svg))[0] + "." + fmt)
                        if os.path.exists(produced) and produced != target:
                            shutil.move(produced, target)
                        ok = os.path.exists(target)
                        break
        done[fmt] = dict(path=target, ok=bool(ok and os.path.exists(target)),
                         bytes=os.path.getsize(target) if os.path.exists(target) else 0)
    return dict(results=done, tried=tried)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("inventory"); p1.add_argument("--svg", required=True); p1.add_argument("--out")
    p2 = sub.add_parser("apply"); p2.add_argument("--svg", required=True); p2.add_argument("--ops", required=True)
    p2.add_argument("--out", required=True); p2.add_argument("--log")
    p3 = sub.add_parser("export"); p3.add_argument("--svg", required=True); p3.add_argument("--png")
    p3.add_argument("--pdf"); p3.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    if a.cmd == "inventory":
        inv = inventory(a.svg)
        if a.out:
            json.dump(inv, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps(dict(n_ids=inv["n_ids"], width=inv["width"], height=inv["height"]), ensure_ascii=False))
        for r in inv["elements"][:60]:
            print(json.dumps(r, ensure_ascii=False))
    elif a.cmd == "apply":
        log = apply_ops(a.svg, json.load(open(a.ops, encoding="utf-8")), a.out)
        if a.log:
            json.dump(dict(source=a.svg, output=a.out, changes=log), open(a.log, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        errs = [x for x in log if x["status"] != "ok"]
        print(json.dumps(dict(applied=len(log) - len(errs), errors=errs), ensure_ascii=False))
        return 0 if not errs else 2
    else:
        r = export(a.svg, a.png, a.pdf, a.dpi)
        print(json.dumps(r, ensure_ascii=False))
        return 0 if all(v["ok"] for v in r["results"].values()) else 2


if __name__ == "__main__":
    sys.exit(main())
