#!/usr/bin/env python3
"""Render a source-bound figure spec (JSON) to editable SVG + PDF + PNG with layout checks.

Usage:
  python3 ga_render.py --spec spec.json --outdir /app/output [--name graphical_abstract]

Coordinates in the spec use the top-left origin and fractions of the parent box
(figure for top-level elements, panel for elements inside a panel). Every drawn
element keeps its spec `id` as the SVG group id, so the SVG stays layered and editable.
Outputs: <name>.svg, <name>.pdf, <name>.png, render_report.json, layer_map.json.
Only matplotlib/numpy (+ stdlib) are required.
"""
import argparse, csv, hashlib, json, math, os, re, sys, textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Ellipse, FancyArrowPatch, Rectangle

matplotlib.rcParams["svg.fonttype"] = "none"      # keep text as <text> (editable)
matplotlib.rcParams["svg.hashsalt"] = "ga-skill"   # deterministic ids
matplotlib.rcParams["pdf.fonttype"] = 42           # embed TrueType (editable in Illustrator)
matplotlib.rcParams["ps.fonttype"] = 42

PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
CJK_RE = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]")
ARROW_KINDS = {
    "activates": dict(arrowstyle="-|>", linestyle="-"),
    "promotes": dict(arrowstyle="-|>", linestyle="-"),
    "inhibits": dict(arrowstyle="-[, widthB=0.55, lengthB=0.0", linestyle="-"),
    "flow": dict(arrowstyle="-|>, head_width=0.45, head_length=0.9", linestyle="-"),
    "produces": dict(arrowstyle="-|>", linestyle="--"),
    "converts": dict(arrowstyle="-|>", linestyle="--"),
    "binds": dict(arrowstyle="-", linestyle=":"),
    "associates": dict(arrowstyle="-", linestyle=":"),
    "next": dict(arrowstyle="-|>", linestyle="-"),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pick_fonts(spec, all_text):
    """Return (family list, notes). Adds a CJK-capable font when CJK text is present."""
    notes = []
    names = {f.name for f in font_manager.fontManager.ttflist}
    want = spec.get("canvas", {}).get("font_family", "auto")
    fams = []
    if want and want != "auto":
        if want in names:
            fams.append(want)
        else:
            notes.append(f"requested font '{want}' not installed; using fallback")
    for cand in ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"]:
        if cand in names and cand not in fams:
            fams.append(cand)
            break
    if CJK_RE.search(all_text):
        cjk = [n for n in sorted(names) if re.search(r"CJK|Hei|Song|Kai|WenQuanYi|Noto Sans SC|Source Han|PingFang|Microsoft YaHei|SimSun|Droid Sans Fallback", n)]
        if cjk:
            fams.insert(0, cjk[0])
            notes.append(f"CJK text detected; using font '{cjk[0]}'")
        else:
            notes.append("CJK text detected but no CJK font found: glyphs may render as boxes (check PNG)")
    if not fams:
        fams = ["DejaVu Sans"]
    return fams, notes


class Box:
    def __init__(self, x0, y0, w, h):
        self.x0, self.y0, self.w, self.h = x0, y0, w, h  # figure fraction, top-left origin

    def sub(self, x, y, w, h):
        return Box(self.x0 + x * self.w, self.y0 + y * self.h, w * self.w, h * self.h)

    def mpl_xy(self):  # bottom-left in figure fraction (matplotlib)
        return self.x0, 1.0 - self.y0 - self.h

    def center(self):
        return self.x0 + self.w / 2, 1.0 - (self.y0 + self.h / 2)


def load_series(el, spec_dir):
    """Load chart data from CSV or inline values. Returns dict with x, y, err, group lists."""
    d = el.get("data", {})
    if "csv" in d:
        path = d["csv"] if os.path.isabs(d["csv"]) else os.path.join(spec_dir, d["csv"])
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        def col(name, num=True):
            if not name:
                return None
            out = []
            for r in rows:
                v = r.get(name, "")
                if num:
                    try:
                        out.append(float(v))
                    except ValueError:
                        out.append(float("nan"))
                else:
                    out.append(v)
            return out
        xs_num = d.get("x_numeric", False)
        return dict(x=col(d.get("x"), num=xs_num), y=col(d.get("y")), err=col(d.get("err")),
                    group=col(d.get("group"), num=False), origin=f"csv:{path}")
    vals = d.get("values", {})
    return dict(x=vals.get("x"), y=vals.get("y"), err=vals.get("err"), group=vals.get("group"), origin="inline")


def draw(spec, spec_dir, font_scale=1.0):
    cv = spec.get("canvas", {})
    W, H = float(cv.get("width_in", 13.33)), float(cv.get("height_in", 7.5))
    base = float(cv.get("base_font_pt", 11)) * font_scale
    fig = plt.figure(figsize=(W, H), facecolor=cv.get("background", "#FFFFFF"))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.set_gid("layer:canvas")
    registry = {}   # id -> dict(kind, artist(s), box)
    texts = []      # (id, text artist, container Box or None)
    root = Box(0, 0, 1, 1)

    def put_text(eid, s, x, y, size, ha="center", va="center", weight="normal", color="#111111", wrap_w=None, style="normal"):
        if wrap_w:
            # rough wrap by character budget; refined later by overflow check
            chars = max(6, int(wrap_w * W * 72 / (size * 0.55)))
            if CJK_RE.search(s):
                chars = max(4, int(wrap_w * W * 72 / (size * 1.0)))
                lines = [s[i:i + chars] for i in range(0, len(s), chars)] if "\n" not in s else s.split("\n")
                s = "\n".join(lines)
            else:
                s = "\n".join(textwrap.fill(p, chars) for p in s.split("\n"))
        t = ax.text(x, y, s, ha=ha, va=va, fontsize=size, fontweight=weight, color=color,
                    fontstyle=style, transform=ax.transAxes, zorder=6)
        t.set_gid(eid)
        return t

    title = spec.get("title")
    if title:
        t = put_text("title", title["text"], 0.5, 1 - float(title.get("y", 0.04)), base * 1.6,
                     weight="bold", wrap_w=0.95)
        registry["title"] = dict(kind="text", artist=t)
        texts.append(("title", t, None))

    def draw_elements(elements, parent):
        for el in elements:
            eid = el["id"]
            typ = el.get("type", "entity")
            if typ == "entity":
                b = parent.sub(el["x"], el["y"], el["w"], el["h"])
                x, y = b.mpl_xy()
                shape = el.get("shape", "round")
                fill = el.get("fill", "#EAF2FB")
                edge = el.get("edge", "#2B4C7E")
                if shape == "ellipse":
                    p = Ellipse(b.center(), b.w, b.h, facecolor=fill, edgecolor=edge, lw=1.4, zorder=3)
                else:
                    style = "round,pad=0.004,rounding_size=0.015" if shape == "round" else "square,pad=0.002"
                    p = FancyBboxPatch((x, y), b.w, b.h, boxstyle=style, facecolor=fill, edgecolor=edge,
                                       lw=1.4, zorder=3)
                ax.add_patch(p)
                p.set_gid(eid + ":shape")
                t = put_text(eid, el.get("label", ""), *b.center(), el.get("size", base),
                             weight=el.get("weight", "normal"), color=el.get("text_color", "#111111"),
                             wrap_w=b.w * 0.9)
                registry[eid] = dict(kind="entity", artist=p, text=t, box=b)
                texts.append((eid, t, b))
            elif typ == "text":
                b = parent.sub(el["x"], el["y"], el.get("w", 0.3), el.get("h", 0.05))
                ha = el.get("ha", "left")
                xx = b.x0 if ha == "left" else (b.x0 + b.w / 2 if ha == "center" else b.x0 + b.w)
                t = put_text(eid, el["text"], xx, 1 - (b.y0 + b.h / 2), el.get("size", base * 0.9), ha=ha,
                             weight=el.get("weight", "normal"), color=el.get("color", "#111111"),
                             wrap_w=b.w, style=el.get("style", "normal"))
                registry[eid] = dict(kind="text", artist=t, box=b)
                texts.append((eid, t, b))
            elif typ == "chart":
                b = parent.sub(el["x"], el["y"], el["w"], el["h"])
                x, y = b.mpl_xy()
                cax = fig.add_axes([x, y, b.w, b.h])
                cax.set_gid(eid)
                s = load_series(el, spec_dir)
                kind = el.get("kind", "bar")
                fs = el.get("size", base * 0.8)
                colors = el.get("colors") or PALETTE  # pass explicit colors; defaults avoid clashing with category legends
                if s.get("group") and kind != "bar":
                    groups = list(dict.fromkeys(s["group"]))
                    for gi, g in enumerate(groups):
                        idx = [i for i, gg in enumerate(s["group"]) if gg == g]
                        xs = [s["x"][i] for i in idx]; ys = [s["y"][i] for i in idx]
                        if kind == "line":
                            cax.plot(xs, ys, marker="o", ms=3, color=colors[gi % len(colors)], label=g, lw=1.4)
                        else:
                            cax.scatter(xs, ys, s=12, color=colors[gi % len(colors)], label=g)
                    cax.legend(fontsize=fs * 0.85, frameon=False)
                elif kind == "bar":
                    xs = s["x"] if s["x"] is not None else list(range(len(s["y"])))
                    pos = list(range(len(s["y"])))
                    bar_colors = [colors[i % len(colors)] for i in range(len(pos))] if el.get("color_by_bar", False) else (el.get("color") or "#6B7785")
                    cax.bar(pos, s["y"], yerr=s.get("err"), color=bar_colors, capsize=3, edgecolor="#333333", lw=0.6)
                    cax.set_xticks(pos)
                    cax.set_xticklabels([str(v) for v in xs], fontsize=fs, rotation=el.get("xtick_rotation", 0))
                elif kind == "line":
                    cax.plot(s["x"], s["y"], marker="o", ms=3, color=colors[0], lw=1.4)
                else:
                    cax.scatter(s["x"], s["y"], s=12, color=colors[0])
                cax.set_xlabel(el.get("xlabel", ""), fontsize=fs)
                cax.set_ylabel(el.get("ylabel", ""), fontsize=fs)
                if el.get("title"):
                    cax.set_title(el["title"], fontsize=fs * 1.05)
                cax.tick_params(labelsize=fs * 0.9)
                for sp in ("top", "right"):
                    cax.spines[sp].set_visible(False)
                registry[eid] = dict(kind="chart", artist=cax, box=b, data_origin=s["origin"],
                                     n_points=len(s["y"] or []))
            elif typ == "legend":
                b = parent.sub(el["x"], el["y"], el.get("w", 0.2), el.get("h", 0.2))
                entries = el.get("entries", [])
                n = max(1, len(entries))
                fs = el.get("size", base * 0.8)
                arts = []
                for i, e in enumerate(entries):
                    cy = 1 - (b.y0 + b.h * (i + 0.5) / n)
                    hx = b.x0 + 0.012
                    m = e.get("marker", "square")
                    col = e.get("color", "#333333")
                    if m.startswith("arrow"):
                        kind = m.split("_", 1)[1] if "_" in m else "activates"
                        st = ARROW_KINDS.get(kind, ARROW_KINDS["activates"])
                        a = FancyArrowPatch((hx - 0.01, cy), (hx + 0.012, cy), arrowstyle=st["arrowstyle"],
                                            linestyle=st["linestyle"], mutation_scale=10, color=col, lw=1.3,
                                            transform=ax.transAxes, zorder=6)
                        ax.add_patch(a)
                    elif m == "line":
                        a, = ax.plot([hx - 0.01, hx + 0.01], [cy, cy], color=col, lw=2, transform=ax.transAxes, zorder=6)
                    else:
                        a = Rectangle((hx - 0.008, cy - 0.008), 0.016, 0.016 * W / H, facecolor=col,
                                      edgecolor="#333333", lw=0.5, transform=ax.transAxes, zorder=6)
                        ax.add_patch(a)
                    a.set_gid(f"{eid}:handle:{i}")
                    t = ax.text(hx + 0.02, cy, e["label"], fontsize=fs, va="center", ha="left",
                                transform=ax.transAxes, zorder=6)
                    t.set_gid(f"{eid}:label:{i}")
                    arts.append(t)
                    texts.append((f"{eid}:label:{i}", t, None))
                registry[eid] = dict(kind="legend", artist=arts, box=b, n_entries=len(entries))
            elif typ == "scalebar":
                b = parent.sub(el["x"], el["y"], el.get("length", 0.1), 0.02)
                cy = 1 - b.y0
                ln, = ax.plot([b.x0, b.x0 + b.w], [cy, cy], color=el.get("color", "#111111"), lw=2.5,
                              transform=ax.transAxes, zorder=6)
                ln.set_gid(eid + ":bar")
                t = put_text(eid, el.get("label", ""), b.x0 + b.w / 2, cy - 0.022, el.get("size", base * 0.8))
                registry[eid] = dict(kind="scalebar", artist=ln, text=t, box=b)
                texts.append((eid, t, None))
            elif typ == "image":
                b = parent.sub(el["x"], el["y"], el["w"], el["h"])
                x, y = b.mpl_xy()
                path = el["path"] if os.path.isabs(el["path"]) else os.path.join(spec_dir, el["path"])
                iax = fig.add_axes([x, y, b.w, b.h]); iax.axis("off"); iax.set_gid(eid)
                iax.imshow(plt.imread(path))
                registry[eid] = dict(kind="image", artist=iax, box=b)
            elif typ == "group":
                b = parent.sub(el["x"], el["y"], el["w"], el["h"])
                draw_elements(el.get("elements", []), b)
        return

    for pnl in spec.get("panels", []):
        b = root.sub(pnl["x"], pnl["y"], pnl["w"], pnl["h"])
        x, y = b.mpl_xy()
        frame = FancyBboxPatch((x, y), b.w, b.h, boxstyle="round,pad=0.003,rounding_size=0.012",
                               facecolor=pnl.get("fill", "#FAFAFA"), edgecolor=pnl.get("edge", "#9AA5B1"),
                               lw=1.0, zorder=1)
        ax.add_patch(frame)
        frame.set_gid(f"panel:{pnl['id']}")
        registry[f"panel:{pnl['id']}"] = dict(kind="panel", artist=frame, box=b, role=pnl.get("role"))
        if pnl.get("title"):
            t = put_text(f"panel:{pnl['id']}:title", pnl["title"], b.x0 + b.w / 2, 1 - (b.y0 + 0.035 * b.h),
                         pnl.get("title_size", base * 1.1), weight="bold", wrap_w=b.w * 0.95)
            texts.append((f"panel:{pnl['id']}:title", t, b))
        draw_elements(pnl.get("elements", []), b)
    draw_elements(spec.get("elements", []), root)

    # arrows last (need entity patches for clipping)
    all_arrows = list(spec.get("arrows", []))
    for pnl in spec.get("panels", []):
        all_arrows += pnl.get("arrows", [])
    for ar in all_arrows:
        a, bb = registry.get(ar["from"]), registry.get(ar["to"])
        if not a or not bb or "box" not in a or "box" not in bb:
            registry[ar["id"]] = dict(kind="arrow", error=f"endpoint missing: {ar['from']} -> {ar['to']}")
            continue
        st = ARROW_KINDS.get(ar.get("kind", "activates"), ARROW_KINDS["activates"])
        col = ar.get("color", "#333333")
        pA, pB = a["box"].center(), bb["box"].center()
        if a["kind"] == "panel" or bb["kind"] == "panel":
            # connect facing edges of panels (or panel <-> entity) instead of centres
            def edge(box, toward):
                cx, cy = box.center(); tx, ty = toward
                if abs(tx - cx) >= abs(ty - cy):
                    return (box.x0 + box.w + 0.004, cy) if tx > cx else (box.x0 - 0.004, cy)
                return (cx, 1 - box.y0 + 0.004) if ty > cy else (cx, 1 - box.y0 - box.h - 0.004)
            if a["kind"] == "panel":
                pA = edge(a["box"], bb["box"].center())
            if bb["kind"] == "panel":
                pB = edge(bb["box"], a["box"].center())
        fa = FancyArrowPatch(pA, pB, arrowstyle=st["arrowstyle"],
                             linestyle=st["linestyle"], mutation_scale=ar.get("scale", 14), color=col,
                             lw=ar.get("lw", 2.4 if ar.get("kind") == "flow" else 1.6), shrinkA=2, shrinkB=2,
                             patchA=a.get("artist") if a["kind"] == "entity" else None,
                             patchB=bb.get("artist") if bb["kind"] == "entity" else None,
                             connectionstyle=ar.get("connection", "arc3,rad=0.0"), zorder=4)
        ax.add_patch(fa)
        fa.set_gid(ar["id"])
        entry = dict(kind="arrow", artist=fa, frm=ar["from"], to=ar["to"], arrow_kind=ar.get("kind", "activates"))
        if ar.get("label"):
            (x1, y1), (x2, y2) = pA, pB
            t = ax.text((x1 + x2) / 2, (y1 + y2) / 2 + 0.012, ar["label"], fontsize=ar.get("size", base * 0.75),
                        ha="center", va="bottom", color=col, transform=ax.transAxes, zorder=7,
                        bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
            t.set_gid(ar["id"] + ":label")
            entry["text"] = t
            texts.append((ar["id"] + ":label", t, None))
        registry[ar["id"]] = entry

    footnote = spec.get("footnote")
    if footnote:
        t = put_text("footnote", footnote, 0.01, 0.015, base * 0.65, ha="left", va="bottom",
                     color="#555555", wrap_w=0.98)
        texts.append(("footnote", t, None))
    return fig, ax, registry, texts


def bbox_frac(artist, fig, renderer):
    bb = artist.get_window_extent(renderer=renderer)
    W, H = fig.get_size_inches() * fig.dpi
    return (bb.x0 / W, 1 - bb.y1 / H, bb.x1 / W, 1 - bb.y0 / H)  # top-left origin


def check_layout(fig, registry, texts, min_pt):
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    issues = []
    for eid, t, cont in texts:
        x0, y0, x1, y1 = bbox_frac(t, fig, r)
        if x0 < -0.001 or y0 < -0.001 or x1 > 1.001 or y1 > 1.001:
            issues.append(dict(level="fail", check="out_of_canvas", id=eid, bbox=[round(v, 4) for v in (x0, y0, x1, y1)]))
        if cont is not None:
            pad = 0.002
            if x0 < cont.x0 - pad or x1 > cont.x0 + cont.w + pad or y0 < cont.y0 - pad or y1 > cont.y0 + cont.h + pad:
                issues.append(dict(level="fail", check="text_overflow", id=eid,
                                   text_bbox=[round(v, 4) for v in (x0, y0, x1, y1)],
                                   container=[round(v, 4) for v in (cont.x0, cont.y0, cont.x0 + cont.w, cont.y0 + cont.h)]))
        if t.get_fontsize() < min_pt:
            issues.append(dict(level="warn", check="font_too_small", id=eid, size=round(t.get_fontsize(), 1)))
    ents = [(k, v["box"]) for k, v in registry.items() if v.get("kind") == "entity"]
    for i in range(len(ents)):
        for j in range(i + 1, len(ents)):
            a, b = ents[i][1], ents[j][1]
            ox = min(a.x0 + a.w, b.x0 + b.w) - max(a.x0, b.x0)
            oy = min(a.y0 + a.h, b.y0 + b.h) - max(a.y0, b.y0)
            if ox > 0.002 and oy > 0.002:
                issues.append(dict(level="warn", check="entity_overlap", ids=[ents[i][0], ents[j][0]],
                                   overlap_frac=round(ox * oy / min(a.w * a.h, b.w * b.h), 3)))
    for k, v in registry.items():
        if v.get("kind") == "arrow" and v.get("error"):
            issues.append(dict(level="fail", check="arrow_endpoint", id=k, detail=v["error"]))
    return issues


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    import struct
    return struct.unpack(">II", head[16:24])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--name", default="figure")
    ap.add_argument("--min-font-pt", type=float, default=6.5)
    args = ap.parse_args()
    with open(args.spec, encoding="utf-8") as f:
        spec = json.load(f)
    spec_dir = os.path.dirname(os.path.abspath(args.spec))
    os.makedirs(args.outdir, exist_ok=True)
    all_text = json.dumps(spec, ensure_ascii=False)
    fams, font_notes = pick_fonts(spec, all_text)
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["font.sans-serif"] = fams + matplotlib.rcParams["font.sans-serif"]

    # auto-shrink loop: reduce global font scale until no overflow (max 4 tries)
    scale, attempts = 1.0, []
    for _ in range(4):
        fig, ax, registry, texts = draw(spec, spec_dir, font_scale=scale)
        issues = check_layout(fig, registry, texts, args.min_font_pt)
        n_over = sum(1 for i in issues if i["check"] in ("text_overflow", "out_of_canvas"))
        attempts.append(dict(font_scale=round(scale, 3), overflow_or_out=n_over))
        if n_over == 0 or scale <= 0.7:
            break
        plt.close(fig)
        scale *= 0.9

    dpi = int(spec.get("canvas", {}).get("dpi", 300))
    out = {}
    for ext in ("svg", "pdf", "png"):
        p = os.path.join(args.outdir, f"{args.name}.{ext}")
        try:
            fig.savefig(p, dpi=dpi if ext == "png" else None, facecolor=fig.get_facecolor())
            out[ext] = dict(path=p, bytes=os.path.getsize(p), sha256=sha256(p))
            if ext == "png":
                out[ext]["pixels"] = png_size(p)
        except Exception as e:  # report, keep going
            out[ext] = dict(path=p, error=repr(e))
            issues.append(dict(level="fail", check="export", format=ext, detail=repr(e)))
    plt.close(fig)

    # SVG sanity: ids present and text kept as text
    svg_ids_missing = []
    if "path" in out.get("svg", {}) and "error" not in out["svg"]:
        svg = open(out["svg"]["path"], encoding="utf-8").read()
        for k, v in registry.items():
            gid = k if v.get("kind") != "panel" else k
            if v.get("kind") in ("entity", "arrow", "chart", "text", "panel") and f'id="{gid}' not in svg:
                svg_ids_missing.append(k)
        out["svg"]["text_elements"] = svg.count("<text")
    if svg_ids_missing:
        issues.append(dict(level="warn", check="svg_layer_ids", missing=svg_ids_missing))

    layer_map = {}
    def walk(elements, panel=None):
        for el in elements:
            layer_map[el["id"]] = dict(type=el.get("type", "entity"), panel=panel,
                                       label=el.get("label") or el.get("text") or el.get("title"),
                                       source=el.get("source"))
            if el.get("type") == "group":
                walk(el.get("elements", []), panel)
    for pnl in spec.get("panels", []):
        layer_map[f"panel:{pnl['id']}"] = dict(type="panel", role=pnl.get("role"), title=pnl.get("title"),
                                               source=pnl.get("source"))
        walk(pnl.get("elements", []), pnl["id"])
        for ar in pnl.get("arrows", []):
            layer_map[ar["id"]] = dict(type="arrow", kind=ar.get("kind"), frm=ar["from"], to=ar["to"],
                                       label=ar.get("label"), source=ar.get("source"))
    walk(spec.get("elements", []))
    for ar in spec.get("arrows", []):
        layer_map[ar["id"]] = dict(type="arrow", kind=ar.get("kind"), frm=ar["from"], to=ar["to"],
                                   label=ar.get("label"), source=ar.get("source"))
    for k, v in registry.items():
        if v.get("kind") == "chart" and k in layer_map:
            layer_map[k]["data_origin"] = v["data_origin"]; layer_map[k]["n_points"] = v["n_points"]
    with open(os.path.join(args.outdir, "layer_map.json"), "w", encoding="utf-8") as f:
        json.dump(layer_map, f, ensure_ascii=False, indent=1)

    fails = [i for i in issues if i["level"] == "fail"]
    report = dict(status="ok" if not fails else "needs_fix", outputs=out, fonts=fams[:3],
                  font_notes=font_notes, shrink_attempts=attempts, issues=issues,
                  n_elements=len(layer_map), canvas=spec.get("canvas", {}))
    with open(os.path.join(args.outdir, "render_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=report["status"], fails=len(fails),
                          warns=len(issues) - len(fails), outputs={k: v.get("path") for k, v in out.items()},
                          font_notes=font_notes), ensure_ascii=False))
    for i in issues[:12]:
        print("ISSUE", json.dumps(i, ensure_ascii=False))
    return 0 if not fails else 2


if __name__ == "__main__":
    sys.exit(main())
