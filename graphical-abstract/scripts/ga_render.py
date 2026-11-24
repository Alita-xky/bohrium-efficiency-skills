#!/usr/bin/env python3
"""Render a source-bound figure spec (JSON) to editable SVG + PDF + PNG with layout checks.

Usage:
  python3 ga_render.py --spec spec.json --outdir /app/output [--name graphical_abstract]

Coordinates in the spec use the top-left origin and fractions of the parent box
(figure for top-level elements, panel for elements inside a panel). Every drawn
element keeps its spec `id` as the SVG group id; groups are collected into one Inkscape
layer per panel (+ connectors, annotations), so the SVG stays layered and editable.
Outputs: <name>.svg, <name>.pdf, <name>.png, render_report.json, layer_map.json.

Automatic fixes (reported in render_report.json): per-label font shrink on overflow (never below
--min-font-pt), word-aware wrapping of mixed CJK/Latin labels, opposite arrow pairs drawn as two
arcs, arrows without an explicit `connection` routed around other entities, arrow labels placed on
the curve away from boxes, crowded chart tick labels rotated.
Geometric checks for agents that cannot look at images: text_overflow, out_of_canvas, text_overlap,
arrow_through_entity, arrow_through_text, arrows_cross, label_overlap, chart_out_of_canvas,
tick_labels_overlap, legend_overlap, glyph_missing.
Text: `10^18`, `10^{-3}`, `Ca^{2+}`, `CO_{2}`, `EC_{50}` become Unicode super/subscripts
(still plain editable <text>). Only matplotlib/numpy (+ stdlib) are required.
"""
import argparse, csv, glob, hashlib, json, math, os, re, shutil, subprocess, sys, unicodedata
import logging, warnings
import xml.etree.ElementTree as ET

try:
    import matplotlib
except ImportError:   # scientific-python normally has it; try one quick install, then fail with a clear message
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--timeout", "20",
                    "matplotlib", "numpy"], capture_output=True, timeout=120)
    try:
        import matplotlib
    except ImportError:
        print(json.dumps(dict(status="failed", fails=1, warns=0, outputs={}), ensure_ascii=False))
        print("ISSUE " + json.dumps(dict(level="fail", check="matplotlib_missing",
              hint="matplotlib is not installed and pip could not install it: deliver spec.json + facts.json, "
                   "report the missing renderer in result.json (status failed/partial); for an edit task svg_edit.py "
                   "needs no matplotlib")))
        sys.exit(3)
matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
logging.getLogger("fontTools").setLevel(logging.ERROR)
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, Ellipse, FancyArrowPatch, Rectangle

matplotlib.rcParams["svg.fonttype"] = "none"      # keep text as <text> (editable)
matplotlib.rcParams["svg.hashsalt"] = "ga-skill"   # deterministic ids
matplotlib.rcParams["pdf.fonttype"] = 42           # embed TrueType (editable in Illustrator)
matplotlib.rcParams["ps.fonttype"] = 42

PALETTE = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#F0E442", "#000000"]
CJK_CLASS = "\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef"
CJK_RE = re.compile("[" + CJK_CLASS + "]")
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
ENT_KINDS = ("entity", "icon")   # boxes arrows clip to, route around and are checked against
SVG_NS = "http://www.w3.org/2000/svg"
INK_NS = "http://www.inkscape.org/namespaces/inkscape"



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

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def find_data_file(p, spec_dir):
    """Resolve a data path the way an agent means it: absolute, next to the spec, cwd, /input, /app/work,
    then the same file name anywhere under /input or /app/work. Returns the first existing path (or the
    spec-relative path when nothing exists, so the error names it)."""
    if not p:
        return p
    cands = [p] if os.path.isabs(p) else [os.path.join(spec_dir, p), os.path.abspath(p), os.path.join("/input", p),
                                           os.path.join("/app/work", p)]
    for c in cands:
        if os.path.exists(c):
            return c
    import glob as _glob
    base = os.path.basename(p)
    for root in ("/input", "/app/work", spec_dir):
        hits = sorted(_glob.glob(os.path.join(root, "**", base), recursive=True))
        if hits:
            return hits[0]
    return cands[0]


# ---------------------------------------------------------------- fonts
CJK_PREF = ["Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "Source Han Sans CN", "WenQuanYi Zen Hei",
            "WenQuanYi Micro Hei", "Sarasa Gothic SC", "PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",
            "Microsoft YaHei", "SimHei", "Noto Sans CJK JP", "Noto Serif CJK SC", "AR PL UMing CN", "AR PL UKai CN",
            "Droid Sans Fallback", "Arial Unicode MS"]


def _covers(fname, chars):
    try:
        from matplotlib.ft2font import FT2Font
        f = FT2Font(fname)
        return all(f.get_char_index(ord(c)) for c in chars)
    except Exception:
        return False


def _cjk_font_files(limit=400):
    """CJK-capable font files on disk: fc-list :lang=zh first, then common font folders (name-filtered)."""
    found = []
    if shutil.which("fc-list"):
        try:
            out = subprocess.run(["fc-list", ":lang=zh", "file"], capture_output=True, text=True, timeout=20).stdout
            found += [l.split(":")[0].strip() for l in out.splitlines() if l.strip()]
        except Exception:
            pass
    name_rx = re.compile(r"cjk|han|hei|song|kai|ming|wqy|wenquan|noto.*(sc|tc|jp|kr)|droid|pingfang|hiragino|yahei|"
                         r"simsun|fang|sarasa|unifont|arialuni|ukai|uming", re.I)
    for d in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
              os.path.expanduser("~/.local/share/fonts"), "/opt", "/System/Library/Fonts", "/Library/Fonts",
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fonts")):
        if os.path.isdir(d):
            for ext in ("ttf", "otf", "ttc", "TTF", "OTF", "TTC"):
                found += [p for p in glob.glob(os.path.join(d, "**", "*." + ext), recursive=True)
                          if name_rx.search(os.path.basename(p))][:limit]
    pref = re.compile(r"sans|hei|gothic|wqy|wenquan|sourcehan|source.han|pingfang|yahei|droid", re.I)
    return sorted(dict.fromkeys(found), key=lambda p: (not pref.search(os.path.basename(p)), p))[:limit]


def pick_fonts(spec, all_text):
    """Return (family list, notes, cjk_ok). Latin font first; a CJK font that really contains the
    glyphs used is appended (matplotlib >= 3.6 falls back per glyph; older versions get it first)."""
    notes = []
    entries = [f for f in font_manager.fontManager.ttflist if not f.name.startswith(".")]
    names = {f.name for f in entries}
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
    cjk_ok = True
    used = sorted(set(CJK_RE.findall(all_text)))
    if used:
        sample = used[:40]
        by_name = {}
        for f in entries:
            by_name.setdefault(f.name, []).append(f.fname)
        pick = None
        for n in CJK_PREF + sorted(by_name):
            if n in by_name and any(_covers(fn, sample) for fn in by_name[n][:3]):
                pick = n
                break
        if not pick:   # the font cache can predate installed fonts: look for CJK fonts on disk, register, retry
            for fp in _cjk_font_files():
                if _covers(fp, sample):
                    try:
                        font_manager.fontManager.addfont(fp)
                        pick = font_manager.FontProperties(fname=fp).get_name()
                        notes.append(f"CJK font registered from {fp} (it was not in matplotlib's font cache)")
                        break
                    except Exception:
                        continue
        if pick:
            new_fallback = tuple(int(x) for x in re.findall(r"\d+", matplotlib.__version__)[:2]) >= (3, 6)
            fams = fams + [pick] if new_fallback else [pick] + fams
            notes.append(f"CJK text detected; CJK font '{pick}' (covers all {len(used)} CJK characters used)")
        else:
            cjk_ok = False
            notes.append("CJK text detected but NO installed font contains these glyphs: PNG/PDF would show boxes. "
                         "Use English labels (keep Chinese in result.json) unless the task requires Chinese, and report it")
    if not fams:
        fams = ["DejaVu Sans"]
    return fams, notes, cjk_ok


# ---------------------------------------------------------------- text helpers
SUP = str.maketrans("0123456789+-\u2212=()n", "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079\u207a\u207b\u207b\u207c\u207d\u207e\u207f")
SUB = str.maketrans("0123456789+-=()aeoxhklmnpst",
                    "\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089\u208a\u208b\u208c\u208d\u208e"
                    "\u2090\u2091\u2092\u2093\u2095\u2096\u2097\u2098\u2099\u209a\u209b\u209c")


def pretty(s):
    """10^18 -> 10¹⁸, 10^{-3} -> 10⁻³, Ca^{2+} -> Ca²⁺, CO_{2} -> CO₂ (mathtext '$...$' left alone)."""
    if not isinstance(s, str) or "$" in s:
        return s
    s = re.sub(r"\^\{([0-9+\-\u2212=()n]+)\}", lambda m: m.group(1).translate(SUP), s)
    s = re.sub(r"\^([+\-\u2212]?[0-9]+)", lambda m: m.group(1).translate(SUP), s)
    s = re.sub(r"_\{([0-9+\-=()aeoxhklmnpst]+)\}", lambda m: m.group(1).translate(SUB), s)
    return s


def _em(c):
    if CJK_RE.match(c) or unicodedata.east_asian_width(c) in ("W", "F"):
        return 1.0
    if c == " ":
        return 0.28
    if c in "il.,:;|!'`()[]{}ftjrI\u00b7":
        return 0.3
    if c.isupper() or c in "mwMW@%&":
        return 0.7
    return 0.56


def text_em(s):
    return sum(_em(c) for c in s)


TOKEN_RE = re.compile("[ \\t]+|[" + CJK_CLASS + "]|[^ \\t" + CJK_CLASS + "]+")
NO_BREAK_BEFORE = set("，。、；：！？）》」』】,.;:!?)%")


def wrap_text(s, max_em):
    """Greedy wrap by estimated width: CJK characters may break anywhere, Latin words never split
    unless longer than a whole line; no line starts with closing punctuation."""
    out = []
    for para in s.split("\n"):
        line, w = "", 0.0
        for tok in TOKEN_RE.findall(para):
            if tok.isspace():
                if line:
                    line += " "
                    w += 0.28
                continue
            tw = text_em(tok)
            if line.strip() and w + tw > max_em and tok[0] not in NO_BREAK_BEFORE:
                out.append(line.rstrip())
                line, w = "", 0.0
            while not line and tw > max_em and len(tok) > 1:
                cut, cw = "", 0.0
                for ch in tok:
                    if cut and cw + _em(ch) > max_em:
                        break
                    cut += ch
                    cw += _em(ch)
                soft = max(cut.rfind("/"), cut.rfind("-"))   # prefer breaking after / or -
                if soft >= len(cut) * 0.3 and soft < len(cut) - 1:
                    cut = cut[:soft + 1]
                out.append(cut)
                tok = tok[len(cut):]
                tw = text_em(tok)
            line += tok
            w += tw
        out.append(line.rstrip())
    return "\n".join(out)


class Box:
    def __init__(self, x0, y0, w, h):
        self.x0, self.y0, self.w, self.h = x0, y0, w, h  # figure fraction, top-left origin

    def sub(self, x, y, w, h):
        return Box(self.x0 + x * self.w, self.y0 + y * self.h, w * self.w, h * self.h)

    def mpl_xy(self):  # bottom-left in figure fraction (matplotlib)
        return self.x0, 1.0 - self.y0 - self.h

    def center(self):
        return self.x0 + self.w / 2, 1.0 - (self.y0 + self.h / 2)

    def rect(self):  # (x0, y0, x1, y1) top-left origin
        return (self.x0, self.y0, self.x0 + self.w, self.y0 + self.h)


def rects_overlap(a, b, pad=0.0):
    return min(a[2], b[2]) - max(a[0], b[0]) > pad and min(a[3], b[3]) - max(a[1], b[1]) > pad


def point_in(p, r, shrink=0.0):
    return r[0] + shrink < p[0] < r[2] - shrink and r[1] + shrink < p[1] < r[3] - shrink


def seg_intersect(p1, p2, p3, p4):
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = ccw(p3, p4, p1), ccw(p3, p4, p2), ccw(p1, p2, p3), ccw(p1, p2, p4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        t = d1 / (d1 - d2) if d1 != d2 else 0.5
        return (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
    return None


def curve_points(pA, pB, rad, W, H, n=40):
    """Sample matplotlib's arc3 connection (quadratic Bezier in display space). Inputs/outputs are
    figure fractions with matplotlib's bottom-left origin."""
    x1, y1, x2, y2 = pA[0] * W, pA[1] * H, pB[0] * W, pB[1] * H
    cx, cy = (x1 + x2) / 2 + rad * (y2 - y1), (y1 + y2) / 2 - rad * (x2 - x1)
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t ** 2 * x2
        y = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t ** 2 * y2
        pts.append((x / W, y / H))
    return pts


def to_tl(pts):
    return [(x, 1 - y) for x, y in pts]


# ---------------------------------------------------------------- icon library (vector, matplotlib primitives only)
# Each icon is drawn in a unit square (0..1, y up) mapped onto a square on the canvas, so it keeps its aspect
# ratio. Flat style: one accent colour per icon (Okabe-Ito; light tint fill, mid tint, darker stroke of the same
# hue), neutral greys for instrument bodies, one stroke width that scales mildly with the icon size. The parts
# become one SVG group named after the element id (labels stay real <text>). Keep ICON_NAMES in ga_check.py in sync.
ICON_GROUPS = {
    "biology": ["cell", "nucleus", "mitochondrion", "receptor", "channel", "protein", "peptide", "molecule", "dna",
                "rna", "virus", "bacterium", "antibody", "plant"],
    "lab": ["flask", "beaker", "cuvette", "laser", "lens", "detector", "microscope", "pump", "tube", "computer",
            "database", "chip"],
    "generic": ["document", "chart", "gear"],
}
ICON_ALIASES = {"membrane_receptor": "receptor", "ion_channel": "channel", "transporter": "channel", "blob": "protein",
                "small_molecule": "molecule", "gene": "dna", "sample_cell": "cuvette", "sample": "cuvette",
                "camera": "detector", "pipe": "tube", "tubing": "tube", "monitor": "computer", "cylinder": "database",
                "sensor": "chip", "paper": "document", "process": "gear", "seedling": "plant"}
ICON_COLOR = dict(cell="#56B4E9", nucleus="#0072B2", mitochondrion="#E69F00", receptor="#009E73", channel="#009E73",
                  protein="#CC79A7", peptide="#D55E00", molecule="#D55E00", dna="#0072B2", rna="#CC79A7",
                  virus="#D55E00", bacterium="#009E73", antibody="#0072B2", plant="#009E73", flask="#009E73",
                  beaker="#56B4E9", cuvette="#56B4E9", laser="#D55E00", lens="#56B4E9", detector="#0072B2",
                  microscope="#0072B2", pump="#009E73", tube="#56B4E9", computer="#0072B2", database="#0072B2",
                  chip="#009E73", document="#0072B2", chart="#0072B2", gear="#6B7785")
MEMBRANE_ICONS = ("receptor", "channel")     # drawn across a lipid bilayer; snap into `shape:"membrane"` panels
N_FILL, N_MID, N_DARK, N_EDGE, GLASS = "#EEF1F4", "#C3CBD4", "#6B7785", "#3E4A57", "#F4F8FB"
MEM_FILL, MEM_HEAD, MEM_EDGE = "#FCEFD2", "#EBB858", "#B5832A"


def icon_canonical(name):
    n = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return ICON_ALIASES.get(n, n)


def _rgb(c):
    return matplotlib.colors.to_rgb(c)


def tint(c, t):
    return tuple(x + (1 - x) * t for x in _rgb(c))


def shade(c, s):
    return tuple(x * (1 - s) for x in _rgb(c))


def _sine(x0, x1, yc, amp, periods, phase=0.0, n=64):
    return [(x0 + (x1 - x0) * i / n, yc + amp * math.sin(2 * math.pi * periods * i / n + phase)) for i in range(n + 1)]


class IconPen:
    """Unit-square drawing pen: (0..1, y up) -> a square of side s_in inches centred at (cx, cy) (figure fractions,
    matplotlib bottom-left origin)."""

    def __init__(self, ax, gid, cx, cy, s_in, color, z=3.5):
        from matplotlib.transforms import Affine2D
        W, H = ax.figure.get_size_inches()
        sw, sh = s_in / W, s_in / H
        self.Affine2D = Affine2D
        self.T = Affine2D().scale(sw, sh).translate(cx - sw / 2, cy - sh / 2) + ax.transAxes
        self.ax, self.gid, self.z, self.parts = ax, gid, z, []
        self.ext = None   # drawn extent in unit coordinates (x0, y0, x1, y1)
        self.lw = max(0.6, min(1.3, 1.1 * s_in / 0.8))
        self.C, self.F, self.M, self.S = _rgb(color), tint(color, .80), tint(color, .42), shade(color, .42)

    def _kw(self, fc, ec, lw, ls="-", alpha=None):
        return dict(facecolor="none" if fc is None else fc, edgecolor="none" if ec is None else ec,
                    linewidth=0 if ec is None else self.lw * lw, linestyle=ls, joinstyle="round", capstyle="round",
                    alpha=alpha)

    def _add(self, p, pre=None):
        p.set_transform(pre + self.T if pre is not None else self.T)
        p.set_clip_on(False)
        p.set_zorder(self.z)
        p.set_gid(f"{self.gid}:p{len(self.parts)}")
        self.ax.add_patch(p)
        self.parts.append(p)
        try:
            path = p.get_path().transformed(p.get_patch_transform())
            e = (path.transformed(pre) if pre is not None else path).get_extents()
            self.ext = (e.x0, e.y0, e.x1, e.y1) if self.ext is None else (
                min(self.ext[0], e.x0), min(self.ext[1], e.y0), max(self.ext[2], e.x1), max(self.ext[3], e.y1))
        except Exception:
            pass
        return p

    def circle(self, x, y, r, fc=None, ec=None, lw=1.0, **k):
        return self._add(Ellipse((x, y), 2 * r, 2 * r, **self._kw(fc, ec, lw, **k)))

    def ellipse(self, x, y, w, h, angle=0.0, fc=None, ec=None, lw=1.0, **k):
        return self._add(Ellipse((x, y), w, h, angle=angle, **self._kw(fc, ec, lw, **k)))

    def rect(self, x, y, w, h, fc=None, ec=None, lw=1.0, r=0.0, angle=0.0, about=None, **k):
        style = f"round,pad=0,rounding_size={r}" if r else "square,pad=0"
        p = FancyBboxPatch((x, y), w, h, boxstyle=style, **self._kw(fc, ec, lw, **k))
        pre = self.Affine2D().rotate_deg_around(*(about or (x + w / 2, y + h / 2)), angle) if angle else None
        return self._add(p, pre)

    def poly(self, pts, fc=None, ec=None, lw=1.0, **k):
        from matplotlib.patches import Polygon
        return self._add(Polygon(pts, closed=True, **self._kw(fc, ec, lw, **k)))

    def path(self, verts, codes, fc=None, ec=None, lw=1.0, **k):
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path
        return self._add(PathPatch(Path(verts, codes), **self._kw(fc, ec, lw, **k)))

    def line(self, pts, ec, lw=1.0, cap="round", **k):
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path
        kw = self._kw(None, ec, lw, **k)
        kw["capstyle"] = cap
        return self._add(PathPatch(Path(pts), fill=False, **kw))

    def blob(self, cx, cy, radii, rx=1.0, ry=1.0, rot=0.0, fc=None, ec=None, lw=1.0, **k):
        """Smooth closed curve through points at equal angles (Catmull-Rom -> cubic Bezier)."""
        from matplotlib.path import Path
        n = len(radii)
        pts = [(cx + rx * r * math.cos(2 * math.pi * i / n + rot), cy + ry * r * math.sin(2 * math.pi * i / n + rot))
               for i, r in enumerate(radii)]
        verts, codes = [pts[0]], [Path.MOVETO]
        for i in range(n):
            p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
            verts += [(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6),
                      (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6), p2]
            codes += [Path.CURVE4] * 3
        return self.path(verts + [pts[0]], codes + [Path.CLOSEPOLY], fc=fc, ec=ec, lw=lw, **k)

    def leaf(self, base, tip, width, fc=None, ec=None, lw=1.0):
        from matplotlib.path import Path
        mx, my = (base[0] + tip[0]) / 2, (base[1] + tip[1]) / 2
        dx, dy = tip[0] - base[0], tip[1] - base[1]
        L = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / L * width, dx / L * width
        verts = [base, (mx + nx, my + ny), tip, (mx - nx, my - ny), base, base]
        codes = [Path.MOVETO, Path.CURVE3, Path.CURVE3, Path.CURVE3, Path.CURVE3, Path.CLOSEPOLY]
        return self.path(verts, codes, fc=fc, ec=ec, lw=lw)


def convex_hull(pts):
    """Andrew's monotone chain; returns the hull counter-clockwise without repeating the first point."""
    pts = sorted(set(pts))
    if len(pts) <= 2:
        return pts
    def half(seq):
        h = []
        for q in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (q[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(q)
        return h
    lower, upper = half(pts), half(reversed(pts))
    return lower[:-1] + upper[:-1]


def _membrane(p, y0=.36, y1=.60, skip=(), n=11):
    """Short lipid-bilayer segment: pale band + two rows of head groups."""
    p.rect(0.0, y0, 1.0, y1 - y0, fc=MEM_FILL)
    r = (y1 - y0) * 0.15
    for i in range(n):
        x = (i + 0.5) / n
        if any(a <= x <= b for a, b in skip):
            continue
        for y in (y1 - r, y0 + r):
            p.circle(x, y, r, fc=MEM_HEAD, ec=MEM_EDGE, lw=0.45)


def _ic_cell(p, o):
    radii = [1.0, .97, 1.02, .98, 1.0, 1.03, .97, 1.01, .99, 1.02]
    p.blob(.5, .5, radii, rx=.46, ry=.40, fc=p.F, ec=p.S, lw=1.5)
    p.blob(.5, .5, radii, rx=.42, ry=.36, ec=p.M, lw=.7)
    p.ellipse(.26, .62, .15, .075, angle=25, fc=p.M, ec=p.S, lw=.6)
    p.ellipse(.29, .35, .13, .065, angle=-20, fc=p.M, ec=p.S, lw=.6)
    for x, y, r in ((.46, .24, .028), (.80, .32, .026), (.44, .76, .024), (.17, .48, .022)):
        p.circle(x, y, r, fc=p.S)
    p.circle(.61, .53, .165, fc=p.M, ec=p.S)
    p.circle(.65, .57, .055, fc=p.S)


def _ic_nucleus(p, o):
    p.circle(.5, .5, .44, fc=p.F, ec=p.S, lw=1.4)
    p.circle(.5, .5, .385, ec=p.S, lw=.7, ls=(0, (2.4, 1.8)))
    for x0, x1, y, ph in ((.20, .50, .66, 0.0), (.18, .80, .47, 1.7), (.27, .73, .30, 3.1)):
        p.line(_sine(x0, x1, y, .035, (x1 - x0) / .16, ph), ec=p.M, lw=1.6)
    p.circle(.65, .64, .095, fc=p.M, ec=p.S, lw=.8)


def _ic_mitochondrion(p, o):
    ang = 18.0
    p.ellipse(.5, .5, .94, .54, angle=ang, fc=p.M, ec=p.S, lw=1.4)
    p.ellipse(.5, .5, .80, .40, angle=ang, fc=p.F)
    a, b, c, s = .40, .20, math.cos(math.radians(ang)), math.sin(math.radians(ang))
    pts = []
    for i in range(97):
        t = i / 96
        x = -a * .88 + 2 * a * .88 * t
        y = b * .80 * math.sqrt(max(0.0, 1 - (x / a) ** 2)) * math.sin(2 * math.pi * 3.5 * t)
        pts.append((.5 + x * c - y * s, .5 + x * s + y * c))
    p.line(pts, ec=p.S, lw=1.1)


def _ic_receptor(p, o):
    if o.get("membrane", True):
        _membrane(p, skip=[(.36, .64)])
    p.rect(.385, .30, .085, .36, fc=p.M, ec=p.S, r=.035)
    p.rect(.530, .30, .085, .36, fc=p.M, ec=p.S, r=.035)
    p.ellipse(.375, .79, .19, .32, angle=22, fc=p.C, ec=p.S)
    p.ellipse(.625, .79, .19, .32, angle=-22, fc=p.C, ec=p.S)
    p.rect(.33, .07, .34, .20, fc=p.F, ec=p.S, r=.07)


def _ic_channel(p, o):
    if o.get("membrane", True):
        _membrane(p, skip=[(.25, .75)])
    p.rect(.27, .24, .19, .52, fc=p.M, ec=p.S, r=.08)
    p.rect(.54, .24, .19, .52, fc=p.M, ec=p.S, r=.08)
    for y in (.88, .48, .10):
        p.circle(.5, y, .042, fc=p.C, ec=p.S, lw=.6)


def _ic_protein(p, o):
    p.blob(.5, .5, [.42, .34, .44, .37, .30, .43, .38, .32, .41], rot=.3, fc=p.M, ec=p.S, lw=1.3)
    p.blob(.37, .61, [.11, .08, .10, .09, .11], rot=.5, fc=p.F)


def _ic_peptide(p, o):
    pts = [(.12, .36), (.29, .62), (.46, .38), (.62, .64), (.78, .40), (.90, .64)]
    p.line(pts, ec=p.S, lw=1.6)
    for i, (x, y) in enumerate(pts):
        p.circle(x, y, .085, fc=p.C if i % 2 == 0 else p.M, ec=p.S, lw=.9)


def _ic_molecule(p, o):
    cx, cy, r = .42, .50, .25
    hexa = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in range(30, 390, 60)]
    p.line([hexa[0], (.80, .72)], ec=p.S, lw=1.4)
    p.line([hexa[3], (.12, .29)], ec=p.S, lw=1.4)
    p.poly(hexa, fc=p.F, ec=p.S, lw=1.4)
    p.circle(cx, cy, .14, ec=p.S, lw=.9)
    p.circle(.83, .74, .07, fc=p.C, ec=p.S, lw=.8)
    p.circle(.10, .27, .055, fc=p.M, ec=p.S, lw=.8)


def _ic_dna(p, o):
    x0, x1, amp, per = .06, .94, .30, 1.5
    pair_cols = [("#E69F00", "#009E73"), ("#56B4E9", "#CC79A7")]
    for k in range(12):
        t = (k + .5) / 12
        x = x0 + (x1 - x0) * t
        ya, yb = .5 + amp * math.sin(2 * math.pi * per * t), .5 - amp * math.sin(2 * math.pi * per * t)
        if abs(ya - yb) < .12:
            continue
        ca, cb = pair_cols[k % 2]
        p.line([(x, ya), (x, .5)], ec=ca, lw=1.6, cap="butt")
        p.line([(x, .5), (x, yb)], ec=cb, lw=1.6, cap="butt")
    p.line(_sine(x0, x1, .5, -amp, per), ec=p.S, lw=2.4)
    p.line(_sine(x0, x1, .5, amp, per), ec=p.C, lw=2.4)


def _ic_rna(p, o):
    cols = ["#E69F00", "#009E73", "#56B4E9", "#CC79A7"]
    for k in range(9):
        t = (k + .5) / 9
        x, y = .06 + .88 * t, .64 + .12 * math.sin(2 * math.pi * 1.25 * t)
        p.line([(x, y), (x, y - .24)], ec=cols[k % 4], lw=1.8)
    p.line(_sine(.06, .94, .64, .12, 1.25), ec=p.C, lw=2.6)


def _ic_virus(p, o):
    for k in range(12):
        a = 2 * math.pi * k / 12
        p.line([(.5 + .27 * math.cos(a), .5 + .27 * math.sin(a)), (.5 + .40 * math.cos(a), .5 + .40 * math.sin(a))],
               ec=p.S, lw=1.1)
        p.circle(.5 + .43 * math.cos(a), .5 + .43 * math.sin(a), .045, fc=p.C, ec=p.S, lw=.6)
    p.poly([(.5 + .31 * math.cos(math.radians(a)), .5 + .31 * math.sin(math.radians(a))) for a in range(0, 360, 60)],
           fc=p.M, ec=p.S, lw=1.4)
    p.line(_sine(.37, .63, .5, .06, 1.5), ec=p.S, lw=1.1)


def _ic_bacterium(p, o):
    p.line(_sine(.70, .98, .50, .05, 1.5), ec=p.S, lw=1.1)
    p.rect(.05, .32, .67, .36, fc=p.M, ec=p.S, lw=1.4, r=.18)
    p.rect(.09, .36, .59, .28, ec=p.F, lw=.8, r=.14)
    p.line(_sine(.22, .56, .50, .05, 2.5), ec=p.S, lw=1.0)
    for x, y in ((.17, .45), (.62, .58), (.60, .42), (.30, .57)):
        p.circle(x, y, .022, fc=p.S)


def _ic_antibody(p, o):
    hx, hy = .5, .46
    p.rect(hx - .065, .06, .13, hy - .03, fc=p.C, ec=p.S, r=.05)
    for sgn in (1, -1):
        ang = 38 * sgn
        lx = hx - .06 - .078 if sgn > 0 else hx + .06 + .013
        p.rect(lx, hy + .16, .065, .28, fc=p.M, ec=p.S, r=.03, angle=ang, about=(hx, hy))
        p.rect(hx - .06, hy - .02, .12, .44, fc=p.C, ec=p.S, r=.05, angle=ang, about=(hx, hy))


def _ic_plant(p, o):
    pot = "#E69F00"
    p.line([(.5, .32), (.5, .52), (.49, .70), (.5, .84)], ec=p.S, lw=1.6)
    p.leaf((.5, .50), (.17, .66), .11, fc=p.M, ec=p.S)
    p.leaf((.5, .64), (.84, .79), .11, fc=p.M, ec=p.S)
    p.leaf((.5, .83), (.38, .98), .06, fc=p.C, ec=p.S)
    p.poly([(.31, .29), (.69, .29), (.64, .04), (.36, .04)], fc=tint(pot, .45), ec=shade(pot, .45))
    p.rect(.27, .26, .46, .08, fc=tint(pot, .25), ec=shade(pot, .45), r=.02)


def _ic_flask(p, o):
    body = [(.40, .92), (.40, .62), (.13, .11), (.87, .11), (.60, .62), (.60, .92)]
    p.poly(body, fc=GLASS, ec=N_EDGE, lw=1.3)
    yl = .40
    xl, xr = .40 - (.62 - yl) * .27 / .51, .60 + (.62 - yl) * .27 / .51
    p.poly([(xl + .025, yl), (xr - .025, yl), (.84, .135), (.16, .135)], fc=p.M)
    p.circle(.44, .26, .03, fc=p.F)
    p.circle(.55, .31, .02, fc=p.F)
    p.rect(.36, .90, .28, .06, fc=N_MID, ec=N_EDGE, r=.015)


def _ic_beaker(p, o):
    p.poly([(.18, .90), (.22, .08), (.78, .08), (.82, .90)], fc=GLASS, ec=N_EDGE, lw=1.3)
    p.poly([(.205, .52), (.225, .105), (.775, .105), (.795, .52)], fc=p.M)
    p.line([(.12, .94), (.18, .90)], ec=N_EDGE, lw=1.3)
    for y in (.30, .45, .60, .75):
        p.line([(.27, y), (.37, y)], ec=N_EDGE, lw=.7)


def _ic_cuvette(p, o):
    p.poly([(.64, .10), (.76, .20), (.76, .94), (.64, .84)], fc=N_FILL, ec=N_EDGE)
    p.poly([(.64, .12), (.75, .21), (.75, .58), (.64, .50)], fc=p.M)
    p.poly([(.24, .84), (.36, .94), (.76, .94), (.64, .84)], fc=N_FILL, ec=N_EDGE)
    p.rect(.24, .10, .40, .74, fc=GLASS, ec=N_EDGE, lw=1.3)
    p.rect(.26, .12, .36, .38, fc=p.M)
    p.line([(.30, .78), (.30, .56)], ec="white", lw=1.4)


def _ic_laser(p, o):
    p.line([(.71, .5), (.99, .5)], ec=tint(o["color"], .55), lw=5.0, cap="butt")
    p.line([(.71, .5), (.99, .5)], ec=p.C, lw=2.0, cap="butt")
    p.rect(.03, .30, .62, .40, fc=N_DARK, ec=N_EDGE, lw=1.2, r=.05)
    for x in (.41, .47, .53, .59):
        p.line([(x, .38), (x, .62)], ec=N_MID, lw=1.0)
    p.poly([(.12, .40), (.31, .40), (.215, .585)], fc="#F0E442", ec="#222222", lw=.7)
    p.line([(.215, .53), (.215, .465)], ec="#222222", lw=.9)
    p.circle(.215, .43, .011, fc="#222222")
    p.rect(.65, .42, .07, .16, fc=N_MID, ec=N_EDGE)


def _ic_lens(p, o):
    from matplotlib.path import Path
    v = [(.5, .94), (.70, .80), (.70, .20), (.5, .06), (.30, .20), (.30, .80), (.5, .94), (.5, .94)]
    c = [Path.MOVETO] + [Path.CURVE4] * 6 + [Path.CLOSEPOLY]
    p.path(v, c, fc=tint(o["color"], .62), ec=p.S, lw=1.3, alpha=.92)
    p.line([(.415, .76), (.39, .58)], ec="white", lw=1.6)


def _ic_detector(p, o):
    p.rect(.62, .70, .20, .10, fc=N_MID, ec=N_EDGE, r=.02)
    p.rect(.30, .24, .62, .50, fc=N_MID, ec=N_EDGE, lw=1.2, r=.06)
    for x in (.50, .58, .66):
        p.line([(x, .33), (x, .52)], ec=N_DARK, lw=1.0)
    p.rect(.10, .34, .22, .30, fc=N_DARK, ec=N_EDGE)
    p.ellipse(.10, .49, .09, .32, fc=p.C, ec=p.S)
    p.circle(.82, .63, .035, fc=p.C)


def _ic_microscope(p, o):
    p.circle(.37, .23, .04, fc="#F0E442", ec=N_EDGE, lw=.7)
    p.poly([(.58, .14), (.70, .14), (.70, .62), (.61, .80), (.50, .86), (.45, .78), (.56, .71), (.58, .60)],
           fc=N_MID, ec=N_EDGE)
    p.rect(.14, .05, .62, .09, fc=N_DARK, ec=N_EDGE, r=.03)
    p.rect(.56, .33, .06, .07, fc=N_MID, ec=N_EDGE)
    p.rect(.18, .35, .46, .045, fc=N_DARK, ec=N_EDGE)
    p.rect(.25, .395, .24, .02, fc=p.M)
    p.poly([(.31, .52), (.43, .52), (.41, .445), (.33, .445)], fc=p.C, ec=p.S)
    p.rect(.29, .52, .16, .045, fc=N_DARK, ec=N_EDGE)
    p.rect(.31, .565, .12, .21, fc=N_FILL, ec=N_EDGE)
    p.rect(.31, .74, .095, .17, fc=N_MID, ec=N_EDGE, r=.015, angle=22, about=(.37, .74))
    p.circle(.645, .40, .045, fc=N_FILL, ec=N_EDGE)


def _ic_pump(p, o):
    p.rect(.08, .12, .84, .76, fc=N_FILL, ec=N_EDGE, lw=1.2, r=.07)
    p.rect(.13, .78, .20, .05, fc=p.M)
    arc = [(.5 + .30 * math.cos(math.radians(a)), .48 + .30 * math.sin(math.radians(a))) for a in range(200, -21, -10)]
    p.line([(.0, .38)] + arc + [(1.0, .38)], ec=p.C, lw=2.2)
    p.circle(.5, .48, .22, fc=p.F, ec=p.S, lw=1.1)
    for a in (90, 210, 330):
        p.circle(.5 + .135 * math.cos(math.radians(a)), .48 + .135 * math.sin(math.radians(a)), .06, fc=p.M, ec=p.S)
    p.circle(.5, .48, .035, fc=p.S)


def _ic_tube(p, o):
    p.ellipse(.12, .5, .10, .26, fc=p.M, ec=p.S)
    p.rect(.12, .37, .74, .26, fc=p.F)
    p.line([(.12, .63), (.86, .63)], ec=p.S)
    p.line([(.12, .37), (.86, .37)], ec=p.S)
    p.line([(.20, .575), (.80, .575)], ec="white", lw=1.4)
    for x in (.36, .52, .68):
        p.line([(x - .04, .555), (x + .03, .50), (x - .04, .445)], ec=p.C, lw=1.5)
    p.ellipse(.86, .5, .10, .26, fc=p.M, ec=p.S)


def _ic_computer(p, o):
    p.poly([(.44, .30), (.56, .30), (.60, .14), (.40, .14)], fc=N_MID, ec=N_EDGE)
    p.rect(.26, .08, .48, .07, fc=N_MID, ec=N_EDGE, r=.03)
    p.rect(.06, .28, .88, .60, fc=N_DARK, ec=N_EDGE, lw=1.2, r=.04)
    p.rect(.11, .34, .78, .48, fc=p.F)
    p.line([(.17, .42), (.30, .56), (.42, .49), (.56, .68), (.68, .60), (.82, .74)], ec=p.C, lw=1.5)


def _ic_database(p, o):
    p.ellipse(.5, .16, .68, .18, fc=p.M, ec=p.S)
    p.rect(.16, .16, .68, .64, fc=p.M)
    p.line([(.16, .16), (.16, .80)], ec=p.S)
    p.line([(.84, .16), (.84, .80)], ec=p.S)
    for yc in (.37, .58):
        p.line([(.5 + .34 * math.cos(math.radians(a)), yc + .09 * math.sin(math.radians(a))) for a in range(180, 361, 10)],
               ec=p.S, lw=.9)
    p.ellipse(.5, .80, .68, .18, fc=p.F, ec=p.S)


def _ic_chip(p, o):
    for i in range(4):
        t = .29 + i * .14
        for x, y, w, h in ((.06, t - .03, .14, .06), (.80, t - .03, .14, .06), (t - .03, .06, .06, .14),
                           (t - .03, .80, .06, .14)):
            p.rect(x, y, w, h, fc=N_MID, ec=N_EDGE, lw=.6)
    p.rect(.18, .18, .64, .64, fc=N_DARK, ec=N_EDGE, lw=1.2, r=.05)
    p.rect(.33, .33, .34, .34, fc=p.C, r=.03)
    p.rect(.41, .41, .18, .18, fc=p.F, r=.02)
    p.circle(.27, .73, .028, fc=N_FILL)


def _ic_document(p, o):
    p.poly([(.18, .05), (.82, .05), (.82, .72), (.62, .95), (.18, .95)], fc="white", ec=N_EDGE, lw=1.2)
    p.poly([(.62, .95), (.62, .72), (.82, .72)], fc=N_FILL, ec=N_EDGE)
    p.rect(.27, .78, .26, .06, fc=p.C)
    for y, x1 in ((.62, .72), (.51, .72), (.40, .72), (.29, .60), (.18, .66)):
        p.line([(.27, y), (x1, y)], ec=N_MID, lw=1.8)


def _ic_chart(p, o):
    for x, h, fc in ((.22, .30, p.F), (.43, .50, p.M), (.64, .72, p.C)):
        p.rect(x, .10, .14, h, fc=fc, ec=p.S, lw=.8)
    p.line([(.12, .92), (.12, .10), (.92, .10)], ec=N_EDGE, lw=1.3)


def _ic_gear(p, o):
    pts = []
    for k in range(8):
        a = 45 * k
        for r, d in ((.35, -14), (.46, -8), (.46, 8), (.35, 14)):
            pts.append((.5 + r * math.cos(math.radians(a + d)), .5 + r * math.sin(math.radians(a + d))))
    p.poly(pts, fc=p.M, ec=p.S, lw=1.2)
    p.circle(.5, .5, .23, ec=p.S, lw=.6)
    p.circle(.5, .5, .12, fc="white", ec=p.S)


ICONS = {n: globals()["_ic_" + n] for g in ICON_GROUPS.values() for n in g}


def icon_parts(ax, name, gid, cx, cy, s_in, color=None, z=3.5, **opts):
    """Draw icon `name` centred at (cx, cy) (figure fractions, bottom-left origin), side s_in inches.
    Returns the list of patches (gids gid:p0, gid:p1, ...)."""
    n = icon_canonical(name)
    col = color or ICON_COLOR.get(n, "#0072B2")
    pen = IconPen(ax, gid, cx, cy, s_in, col, z=z)
    ICONS[n](pen, dict(opts, color=col))
    return pen


# ---------------------------------------------------------------- data
def _num(v):
    try:
        return float(str(v).replace("\u2212", "-").replace(",", "").strip())
    except ValueError:
        return float("nan")


def load_series(el, spec_dir):
    """Return dict(cats, series=[{label, x, y, err, raw}], origin, numeric_x).
    data.csv + x + y (one column, or a LIST of columns = wide table, one series per column)
    [+ err (column or list) + group (long table, one series per group value)]; or data.values inline."""
    d = el.get("data", {})
    if "csv" in d:
        path = find_data_file(d["csv"], spec_dir)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        origin = f"csv:{path}"
        get = lambda name: [r.get(name, "") for r in rows]
        xs = get(d["x"]) if d.get("x") else [str(i) for i in range(len(rows))]
        ycols = d["y"] if isinstance(d.get("y"), list) else [d.get("y")]
        ecols = d.get("err") if isinstance(d.get("err"), list) else [d.get("err")] * len(ycols)
        labels = d.get("series_labels") or ycols
        series = []
        if d.get("group") and len(ycols) == 1:
            gv = get(d["group"])
            for g in dict.fromkeys(gv):
                idx = [i for i, v in enumerate(gv) if v == g]
                yraw = [get(ycols[0])[i] for i in idx]
                series.append(dict(label=str(g), x=[xs[i] for i in idx], y=[_num(v) for v in yraw], raw=yraw,
                                   err=[_num(get(ecols[0])[i]) for i in idx] if ecols[0] else None))
        else:
            for yc, ec, lb in zip(ycols, ecols, labels):
                yraw = get(yc)
                series.append(dict(label=str(lb), x=list(xs), y=[_num(v) for v in yraw], raw=yraw,
                                   err=[_num(v) for v in get(ec)] if ec else None))
    else:
        vals = d.get("values", {})
        origin = "inline"
        ys = vals.get("y") or []
        if ys and isinstance(ys[0], list):   # several series inline
            labels = vals.get("series_labels") or [f"series {i + 1}" for i in range(len(ys))]
            errs = vals.get("err") or [None] * len(ys)
            series = [dict(label=lb, x=list(vals.get("x") or range(len(y))), y=[_num(v) for v in y],
                           raw=[str(v) for v in y], err=e) for y, e, lb in zip(ys, errs, labels)]
        else:
            series = [dict(label="", x=list(vals.get("x") or range(len(ys))), y=[_num(v) for v in ys],
                           raw=[str(v) for v in ys], err=vals.get("err"))]
    numeric_x = bool(d.get("x_numeric"))
    if numeric_x:
        for s in series:
            s["x"] = [_num(v) for v in s["x"]]
    cats = list(dict.fromkeys(str(v) for s in series for v in s["x"]))
    return dict(cats=cats, series=series, origin=origin, numeric_x=numeric_x)


def draw_chart(fig, el, b, base, adj):
    x, y = b.mpl_xy()
    cax = fig.add_axes([x, y, b.w, b.h])
    cax.set_gid(el["id"])
    s = load_series(el, el["_spec_dir"])
    kind = el.get("kind", "bar")
    fs = el.get("size", base * 0.8) * adj.get("fscale", 1.0)
    colors = el.get("colors") or PALETTE
    series, cats = s["series"], s["cats"]
    multi = len(series) > 1
    highlight = set(map(str, el.get("highlight", [])))
    n_missing, missing_where = 0, []
    if kind in ("bar", "barh"):
        nb = len(series)
        width = 0.8 / max(1, nb)
        horiz = kind == "barh"
        for gi, sr in enumerate(series):
            lookup = {str(xv): i for i, xv in enumerate(sr["x"])}
            ys, es, raws = [], [], []
            for cat in cats:
                i = lookup.get(cat)
                ys.append(sr["y"][i] if i is not None else float("nan"))
                es.append(sr["err"][i] if (i is not None and sr.get("err")) else 0)
                raws.append(sr["raw"][i] if i is not None else "")
            pos = [ci - 0.4 + width * (gi + 0.5) if multi else ci for ci in range(len(cats))]
            if multi:
                col = colors[gi % len(colors)]
            elif el.get("color_by_bar"):
                col = [colors[i % len(colors)] for i in range(len(cats))]
            else:
                col = el.get("color") or "#6B7785"
            edge = ["#000000" if c in highlight else "#333333" for c in cats]
            lw = [1.6 if c in highlight else 0.5 for c in cats]
            kw = dict(color=col, edgecolor=edge, linewidth=lw, label=sr["label"] if multi else None)
            err = es if sr.get("err") else None
            if horiz:
                cax.barh(pos, ys, height=width * 0.95 if multi else 0.7, xerr=err, capsize=2, **kw)
            else:
                cax.bar(pos, ys, width=width * 0.95 if multi else 0.7, yerr=err, capsize=2, **kw)
            for p, v, r, c in zip(pos, ys, raws, cats):
                if math.isnan(v):
                    n_missing += 1
                    missing_where.append(f"{sr['label']}|{c}" if multi else c)
                    sr.setdefault("_missing_pos", []).append(p)
                elif el.get("value_labels"):
                    lab = r.strip() or f"{v:g}"
                    if horiz:
                        cax.annotate(pretty(lab), (v, p), xytext=(2, 0), textcoords="offset points", va="center",
                                     ha="left", fontsize=fs * 0.75)
                    else:
                        cax.annotate(pretty(lab), (p, v), xytext=(0, 2), textcoords="offset points", ha="center",
                                     va="bottom", fontsize=fs * 0.75, rotation=90 if multi and len(cats) > 6 else 0)
        ticks = list(range(len(cats)))
        rot = adj.get("rotation", el.get("xtick_rotation", 0))
        if horiz:
            cax.set_yticks(ticks)
            cax.set_yticklabels([pretty(c) for c in cats], fontsize=fs)
            cax.invert_yaxis()
            tl = cax.get_yticklabels()
        else:
            cax.set_xticks(ticks)
            if rot:
                cax.set_xticklabels([pretty(c) for c in cats], fontsize=fs, rotation=rot, ha="right",
                                    va="center" if rot >= 90 else "top", rotation_mode="anchor")
            else:
                cax.set_xticklabels([pretty(c) for c in cats], fontsize=fs)
            tl = cax.get_xticklabels()
        for t, c in zip(tl, cats):
            if c in highlight:
                t.set_fontweight("bold")
    else:
        for gi, sr in enumerate(series):
            xs = sr["x"] if s["numeric_x"] else list(range(len(sr["x"])))
            col = colors[gi % len(colors)]
            if kind == "line":
                cax.errorbar(xs, sr["y"], yerr=sr.get("err"), marker="o", ms=3, color=col, lw=1.4, capsize=2,
                             label=sr["label"] if multi else None)
            else:
                cax.scatter(xs, sr["y"], s=14, color=col, label=sr["label"] if multi else None)
            n_missing += sum(1 for v in sr["y"] if math.isnan(v))
        if not s["numeric_x"]:
            cax.set_xticks(list(range(len(cats))))
            cax.set_xticklabels([pretty(c) for c in cats], fontsize=fs, rotation=adj.get("rotation", el.get("xtick_rotation", 0)))
    if el.get("yscale"):
        cax.set_yscale(el["yscale"])
    if el.get("xscale"):
        cax.set_xscale(el["xscale"])
    if el.get("ylim"):
        cax.set_ylim(*el["ylim"])
    if el.get("xlim"):
        cax.set_xlim(*el["xlim"])
    # mark values that are not reported instead of leaving an ambiguous gap
    if kind in ("bar", "barh") and n_missing and el.get("missing_label", "n.r.") is not None:
        lab = el.get("missing_label", "n.r.")
        for sr in series:
            for p in sr.get("_missing_pos", []):
                if kind == "barh":
                    x0 = cax.get_xlim()[0]
                    cax.annotate(lab, (x0, p), xytext=(2, 0), textcoords="offset points", va="center", ha="left",
                                 fontsize=fs * 0.7, color="#777777", style="italic")
                else:
                    y0 = cax.get_ylim()[0]
                    cax.annotate(lab, (p, y0), xytext=(0, 2), textcoords="offset points", ha="center", va="bottom",
                                 fontsize=fs * 0.7, color="#777777", style="italic", rotation=90)
    cax.set_xlabel(pretty(el.get("xlabel", "")), fontsize=fs)
    cax.set_ylabel(pretty(el.get("ylabel", "")), fontsize=fs)
    if multi:
        loc = el.get("legend_loc", "above")
        lkw = dict(fontsize=fs * 0.85, frameon=False, title=el.get("legend_title"), title_fontsize=fs * 0.85)
        if loc == "above":
            cax.legend(loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=min(len(series), 4), borderaxespad=0.2, **lkw)
        else:
            cax.legend(loc=loc, **lkw)
    if el.get("title"):
        cax.set_title(pretty(el["title"]), fontsize=fs * 1.05, loc="left" if multi else "center")
    cax.tick_params(labelsize=fs * 0.9)
    for sp in ("top", "right"):
        cax.spines[sp].set_visible(False)
    return cax, dict(data_origin=s["origin"], n_points=sum(len(sr["y"]) for sr in series), n_series=len(series),
                     n_missing=n_missing, missing=missing_where[:30], horiz=(kind == "barh"))


# ---------------------------------------------------------------- drawing
def draw(spec, spec_dir, adj):
    """adj: per-id adjustments {id: {"fscale": f, "rotation": deg}} found by the fix loop."""
    cv = spec.get("canvas", {})
    W, H = float(cv.get("width_in", 13.33)), float(cv.get("height_in", 7.5))
    base = float(cv.get("base_font_pt", 11))
    fig = plt.figure(figsize=(W, H), facecolor=cv.get("background", "#FFFFFF"))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.set_gid("layer:canvas")
    registry = {}   # id -> dict(kind, artist(s), box)
    svg_groups = []  # icon parts to gather into one <g id=...> after saving
    texts = []      # (id, text artist, container Box or None)
    sizes = {}      # id -> requested font size (before shrink)
    notes = []
    root = Box(0, 0, 1, 1)

    def put_text(eid, s, x, y, size, ha="center", va="center", weight="normal", color="#111111", wrap_w=None,
                 style="normal"):
        sizes[eid] = size
        size = size * adj.get(eid, {}).get("fscale", 1.0)
        s = pretty(str(s))
        if wrap_w and "$" not in s:
            s = wrap_text(s, max(3.0, wrap_w * W * 72 / size))
        t = ax.text(x, y, s, ha=ha, va=va, fontsize=size, fontweight=weight, color=color,
                    fontstyle=style, transform=ax.transAxes, zorder=6)
        t.set_gid(eid)
        return t

    # content top edge -> title must stay above it
    tops = [p["y"] for p in spec.get("panels", [])] + [e.get("y", 1) for e in spec.get("elements", [])
                                                        if e.get("type") != "legend"]
    content_top = min(tops) if tops else 0.15
    title = spec.get("title")
    if title:
        ty = float(title.get("y", 0.04))
        top = max(0.012, ty - 0.025)
        t = put_text("title", title["text"], 0.5, 1 - top, base * 1.6, va="top", weight="bold", wrap_w=0.95)
        registry["title"] = dict(kind="text", artist=t)
        texts.append(("title", t, Box(0.0, 0.0, 1.0, max(content_top - 0.004, top + 0.03))))

    def est_lines(s, width_frac, size):
        lines = wrap_text(pretty(str(s)), max(3.0, width_frac * W * 72 / size)).split("\n")
        return lines, max(text_em(l) for l in lines) * size / 72, len(lines) * 1.22 * size / 72   # inches

    def draw_icon_element(el, parent, ctx):
        eid = el["id"]
        name = icon_canonical(el.get("name"))
        if name not in ICONS:
            registry[eid] = dict(kind="icon_error", error=f"unknown icon '{el.get('name')}'")
            return
        b = parent.sub(el["x"], el["y"], el["w"], el["h"])
        bw, bh = b.w * W, b.h * H
        lab = el.get("label") or ""
        lsize = el.get("size", base * 0.85)
        lsize_adj = lsize * adj.get(eid + ":label", {}).get("fscale", 1.0)
        band = (ctx or {}).get("band")
        snapped = bool(band) and name in MEMBRANE_ICONS and el.get("snap", True)
        lpos = el.get("label_pos") or ("right" if snapped else "below")
        if lpos not in ("below", "above", "right", "left", "none"):
            lpos = "below"
        if lpos == "none":
            lab = ""
        gap = 0.05
        lab_box, ha, va, lx, ly = None, "center", "top", 0.0, 0.0
        if snapped:     # transmembrane part aligned with the bilayer of the membrane panel
            s = (band[1] - band[0]) * H / 0.24
            cx = b.x0 + (s / 2 / W if lab and lpos == "right" else b.w / 2)
            cy = 1 - (band[0] + band[1]) / 2 + 0.02 * s / H
            if lab:
                lx, ly, ha, va = cx + (s / 2 + gap) / W, cy + 0.29 * s / H, "left", "center"
                lab_box = Box(lx, 1 - ly - 0.3 * s / H, max(0.02, b.x0 + b.w - lx), 0.6 * s / H)
        elif lab and lpos in ("below", "above"):
            _, _, lh = est_lines(lab, b.w, lsize_adj)
            s = max(0.12, min(bw, bh - lh - gap))
            top = max(0.0, (bh - s - gap - lh) / 2)
            cx = b.x0 + b.w / 2
            if lpos == "below":
                cy = 1 - (b.y0 + (top + s / 2) / H)
                lx, ly = cx, 1 - (b.y0 + (top + s + gap) / H)
                lab_box = Box(b.x0, b.y0 + (top + s + gap * 0.5) / H, b.w, (lh + gap) / H)
            else:
                cy = 1 - (b.y0 + (top + lh + gap + s / 2) / H)
                lx, ly, va = cx, 1 - (b.y0 + (top + lh) / H), "bottom"
                lab_box = Box(b.x0, b.y0 + top / H, b.w, (lh + gap * 0.5) / H)
        elif lab and lpos in ("right", "left"):
            s = max(0.12, min(bh, bw * 0.42))
            cy = 1 - (b.y0 + b.h / 2)
            if lpos == "right":
                cx = b.x0 + s / 2 / W
                lx, ly, ha, va = b.x0 + (s + gap) / W, cy, "left", "center"
                lab_box = Box(lx, b.y0, max(0.02, b.x0 + b.w - lx), b.h)
            else:
                cx = b.x0 + b.w - s / 2 / W
                lx, ly, ha, va = b.x0 + b.w - (s + gap) / W, cy, "right", "center"
                lab_box = Box(b.x0, b.y0, max(0.02, b.w - (s + gap) / W), b.h)
        else:
            s = max(0.12, min(bw, bh))
            cx, cy = b.center()
        col = el.get("color") or ICON_COLOR.get(name)
        pen = icon_parts(ax, name, eid, cx, cy, s, color=col, membrane=(not snapped) and el.get("membrane", True))
        t = None
        sw, sh = s / W, s / H
        ux = lambda u: cx + (u - 0.5) * sw
        uy = lambda u: cy + (u - 0.5) * sh
        e = pen.ext or (0.04, 0.04, 0.96, 0.96)
        pad = 0.02
        art = (ux(e[0] - pad), uy(e[1] - pad), ux(e[2] + pad), uy(e[3] + pad))   # drawn extent, bottom-left origin
        rects = [art]
        if lab:
            t = put_text(eid + ":label", lab, lx, ly, lsize, ha=ha, va=va, color=el.get("text_color", "#111111"),
                         wrap_w=lab_box.w, weight=el.get("weight", "normal"))
            texts.append((eid + ":label", t, lab_box))
            _, ew, eh = est_lines(lab, lab_box.w, lsize_adj)
            ew, eh = min(ew / W, lab_box.w), eh / H
            x0 = {"center": lx - ew / 2, "left": lx, "right": lx - ew}[ha]
            y0 = {"top": ly - eh, "bottom": ly, "center": ly - eh / 2}[va]
            rects.append((x0, y0, x0 + ew, y0 + eh))
        from matplotlib.patches import PathPatch
        from matplotlib.path import Path
        # arrows clip to the CONVEX hull of art + label (matplotlib bisects along the arrow, so the shape must be
        # convex for the arrow to stop at its outer edge instead of running through the label)
        hull = convex_hull([(r[i], r[j]) for r in rects for i in (0, 2) for j in (1, 3)])
        clip = PathPatch(Path(hull + [hull[0]], [Path.MOVETO] + [Path.LINETO] * (len(hull) - 1) + [Path.CLOSEPOLY]),
                         transform=ax.transAxes, facecolor="none", edgecolor="none")
        clip.set_visible(False)
        ax.add_patch(clip)
        ux0, uy0 = min(r[0] for r in rects), min(r[1] for r in rects)
        ux1, uy1 = max(r[2] for r in rects), max(r[3] for r in rects)
        anc = dict(anchor=(cx, (art[1] + art[3]) / 2))
        if name in MEMBRANE_ICONS:   # arrows arrive at the extracellular part, leave from the cytoplasmic part
            anc = dict(anchor=(cx, cy), anchor_in=(cx, uy(0.80)), anchor_out=(cx, uy(0.17)))
        registry[eid] = dict(kind="icon", artist=clip, text=t, box=Box(ux0, 1 - uy1, ux1 - ux0, uy1 - uy0),
                             name=name, snapped=snapped, icon_in=round(s, 3), **anc)
        svg_groups.append(dict(id=eid, rx=re.compile(re.escape(eid) + r":p\d+$"), extra=[eid + ":label"],
                               label=f"icon: {name}"))

    def draw_elements(elements, parent, ctx=None):
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
                    inner = 0.72
                else:
                    style = "round,pad=0.004,rounding_size=0.015" if shape == "round" else "square,pad=0.002"
                    p = FancyBboxPatch((x, y), b.w, b.h, boxstyle=style, facecolor=fill, edgecolor=edge,
                                       lw=1.4, zorder=3)
                    inner = 0.9
                ax.add_patch(p)
                p.set_gid(eid + ":shape")
                cont = b if shape != "ellipse" else Box(b.x0 + b.w * 0.08, b.y0 + b.h * 0.1, b.w * 0.84, b.h * 0.8)
                tx, ty, wrap = b.center()[0], b.center()[1], b.w * inner
                icon = icon_canonical(el.get("icon")) if el.get("icon") else None
                if icon and icon not in ICONS:
                    registry[eid + ":icon"] = dict(kind="icon_error", error=f"unknown icon '{el.get('icon')}'")
                elif icon:   # icon left of (wide box) or above (tall box) the label, inside the box
                    bw, bh = b.w * W, b.h * H
                    pos = el.get("icon_pos") or ("top" if bw < 1.3 * bh else "left")
                    pad = min(bw, bh) * 0.10
                    inset = bw * (0.13 if shape == "ellipse" else 0.0)
                    if pos == "top":
                        s = max(0.12, min(bh * (0.40 if shape == "ellipse" else 0.46), bw * 0.6))
                        off = pad * (1.6 if shape == "ellipse" else 0.8)
                        icx, icy = b.x0 + b.w / 2, 1 - (b.y0 + (off + s / 2) / H)
                        top = (off + s + pad * 0.3) / H
                        cont = Box(b.x0 + (pad * 0.5 + inset) / W, b.y0 + top, b.w - (pad + 2 * inset) / W,
                                   max(0.01, b.h - top - pad * 0.4 / H))
                    else:
                        s = max(0.12, min(bh * (0.56 if shape == "ellipse" else 0.72), bw * 0.34))
                        xl = inset + pad * 0.9
                        icx, icy = b.x0 + (xl + s / 2) / W, 1 - (b.y0 + b.h / 2)
                        left = (xl + s + pad * 0.6) / W
                        cont = Box(b.x0 + left, b.y0 + b.h * 0.08, max(0.01, b.w - left - (pad * 0.6 + inset) / W),
                                   b.h * 0.84)
                    tx, ty, wrap = cont.x0 + cont.w / 2, 1 - (cont.y0 + cont.h / 2), cont.w
                    icon_parts(ax, icon, eid + ":icon", icx, icy, s, color=el.get("icon_color") or edge,
                               membrane=el.get("membrane", True))
                    svg_groups.append(dict(id=eid + ":icon", rx=re.compile(re.escape(eid) + r":icon:p\d+$"), extra=[],
                                           label=f"icon: {icon}"))
                t = put_text(eid, el.get("label", ""), tx, ty, el.get("size", base),
                             weight=el.get("weight", "normal"), color=el.get("text_color", "#111111"),
                             wrap_w=wrap)
                registry[eid] = dict(kind="entity", artist=p, text=t, box=b, icon=icon)
                texts.append((eid, t, cont))
            elif typ == "icon":
                draw_icon_element(el, parent, ctx)
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
                el["_spec_dir"] = spec_dir
                cax, info = draw_chart(fig, el, b, base, adj.get(eid, {}))
                registry[eid] = dict(kind="chart", artist=cax, box=b, parent_box=parent, **info)
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
                        a = Rectangle((hx - 0.008, cy - 0.008), 0.016, 0.016 * W / H, facecolor=e.get("fill", col),
                                      edgecolor=e.get("edge", "#333333"), lw=1.0 if e.get("edge") else 0.5,
                                      transform=ax.transAxes, zorder=6)
                        ax.add_patch(a)
                    a.set_gid(f"{eid}:handle:{i}")
                    lid = f"{eid}:label:{i}"
                    sizes[lid] = fs
                    t = ax.text(hx + 0.02, cy, pretty(e["label"]), fontsize=fs * adj.get(lid, {}).get("fscale", 1.0),
                                va="center", ha="left", transform=ax.transAxes, zorder=6)
                    t.set_gid(lid)
                    arts.append(t)
                    row = Box(hx + 0.018, b.y0 + b.h * i / n, max(0.01, b.x0 + b.w - hx - 0.018), b.h / n)
                    texts.append((lid, t, row))
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
                draw_elements(el.get("elements", []), b, ctx)
        return

    for pnl in spec.get("panels", []):
        b = root.sub(pnl["x"], pnl["y"], pnl["w"], pnl["h"])
        x, y = b.mpl_xy()
        pid, pshape, ctx = pnl["id"], pnl.get("shape", "box"), {}
        if pshape == "nucleus":     # ellipse with a double (porous) envelope
            ec = pnl.get("edge", "#2F6EA5")
            frame = Ellipse(b.center(), b.w, b.h, facecolor=pnl.get("fill", "#EAF3FB"), edgecolor=ec, lw=1.6, zorder=1)
            env = Ellipse(b.center(), b.w - 0.12 / W, b.h - 0.12 / H, facecolor="none", edgecolor=ec, lw=0.8,
                          linestyle=(0, (3, 2.2)), zorder=1.01)
            ax.add_patch(env)
            env.set_gid(f"panel:{pid}:envelope")
        elif pshape == "cell":      # rounded cell with a thick membrane outline
            frame = FancyBboxPatch((x, y), b.w, b.h, boxstyle="round,pad=0,rounding_size=0.02",
                                   facecolor=pnl.get("fill", "#F7FAFD"), edgecolor=pnl.get("edge", MEM_HEAD),
                                   lw=5.0, zorder=1)
            for k, d in enumerate((1, -1)):
                dx, dy = d * 2.6 / 72 / W, d * 2.6 / 72 / H
                ln = FancyBboxPatch((x - dx, y - dy), b.w + 2 * dx, b.h + 2 * dy, boxstyle="round,pad=0,rounding_size=0.02",
                                    facecolor="none", edgecolor=MEM_EDGE, lw=0.7, zorder=1.01)
                ax.add_patch(ln)
                ln.set_gid(f"panel:{pid}:leaflet{k}")
        elif pshape == "membrane":  # horizontal lipid bilayer across the panel; receptor/channel icons snap into it
            from matplotlib.collections import PatchCollection, LineCollection
            from matplotlib.patches import Circle
            th = min(b.h * H, float(pnl.get("thickness_in", 0.30)))
            yc = b.y0 + b.h / 2
            y0, y1 = yc - th / 2 / H, yc + th / 2 / H
            ctx["band"] = (y0, y1)
            frame = Rectangle((b.x0, 1 - y1), b.w, th / H, facecolor=pnl.get("fill", MEM_FILL), edgecolor="none", zorder=1)
            r = th * 0.15
            n = max(2, int(b.w * W / (2.3 * r)))
            step = b.w * W / n
            heads, tails = [], []
            for i in range(n):
                xi = b.x0 * W + (i + 0.5) * step
                for yh, sg in (((1 - y0) * H - r, -1), ((1 - y1) * H + r, 1)):
                    heads.append(Circle((xi, yh), r))
                    for dx in (-0.4 * r, 0.4 * r):
                        tails.append([(xi + dx, yh + sg * r), (xi + dx * 0.8, yh + sg * (th / 2 - 1.15 * r))])
            tl = LineCollection(tails, colors=MEM_EDGE, linewidths=0.6, alpha=0.55, zorder=1.01)
            tl.set_transform(fig.dpi_scale_trans)
            ax.add_collection(tl, autolim=False)
            tl.set_gid(f"panel:{pid}:tails")
            hd = PatchCollection(heads, facecolor=MEM_HEAD, edgecolor=MEM_EDGE, linewidth=0.45, zorder=1.02)
            hd.set_transform(fig.dpi_scale_trans)
            ax.add_collection(hd, autolim=False)
            hd.set_gid(f"panel:{pid}:heads")
        else:
            frame = FancyBboxPatch((x, y), b.w, b.h, boxstyle="round,pad=0.003,rounding_size=0.012",
                                   facecolor=pnl.get("fill", "#FAFAFA"), edgecolor=pnl.get("edge", "#9AA5B1"),
                                   lw=1.0, zorder=1)
        ax.add_patch(frame)
        frame.set_gid(f"panel:{pid}")
        registry[f"panel:{pid}"] = dict(kind="panel", artist=frame, box=b, role=pnl.get("role"), shape=pshape)
        if pnl.get("title"):
            tid = f"panel:{pid}:title"
            if pshape == "membrane":   # small italic name above the bilayer, right-aligned
                t = put_text(tid, pnl["title"], b.x0 + b.w - 0.004, 1 - ctx["band"][0] + 0.008,
                             pnl.get("title_size", base * 0.85), ha="right", va="bottom", style="italic",
                             color=MEM_EDGE, wrap_w=b.w * 0.5)
                texts.append((tid, t, None))
            elif pshape == "nucleus":   # name at the bottom: arrows usually enter the nucleus from above
                t = put_text(tid, pnl["title"], b.x0 + b.w / 2, 1 - (b.y0 + b.h * 0.90), pnl.get("title_size", base),
                             va="bottom", weight="bold", wrap_w=b.w * 0.55)
                texts.append((tid, t, Box(b.x0 + b.w * 0.2, b.y0 + b.h * 0.72, b.w * 0.6, b.h * 0.22)))
            else:
                t = put_text(tid, pnl["title"], b.x0 + b.w / 2, 1 - (b.y0 + 0.012), pnl.get("title_size", base * 1.1),
                             va="top", weight="bold", wrap_w=b.w * 0.95)
                texts.append((tid, t, Box(b.x0, b.y0, b.w, b.h * 0.2)))
        draw_elements(pnl.get("elements", []), b, ctx)
    draw_elements(spec.get("elements", []), root)

    # ---- arrows last (need entity boxes for clipping and routing)
    all_arrows = list(spec.get("arrows", []))
    for pnl in spec.get("panels", []):
        all_arrows += pnl.get("arrows", [])
    obstacles = {k: v["box"].rect() for k, v in registry.items() if v.get("kind") in ENT_KINDS}
    soft = {k: v["box"].rect() for k, v in registry.items() if v.get("kind") in ("legend", "text", "chart") and v.get("box")}
    for pnl in spec.get("panels", []):
        if pnl.get("title") and pnl.get("shape", "box") in ("box", "cell"):
            pb = registry[f"panel:{pnl['id']}"]["box"]
            th = 1.5 * pnl.get("title_size", base * 1.1) / 72 / H
            soft[f"panel:{pnl['id']}:title"] = (pb.x0 + pb.w * 0.1, pb.y0, pb.x0 + pb.w * 0.9, pb.y0 + 0.012 + th)
    pairs = {(ar.get("from"), ar.get("to")) for ar in all_arrows}
    placed = []      # polylines (top-left) of arrows already drawn
    label_boxes = []

    def cost(poly_tl, frm, to):
        c = 0.0
        ends = [obstacles.get(frm), obstacles.get(to)]
        for k, r in obstacles.items():
            if k in (frm, to):
                continue
            if any(point_in(p, r, 0.002) for p in poly_tl[1:-1]):
                c += 1.0
        for k, r in soft.items():
            if any(point_in(p, r, 0.0) for p in poly_tl[1:-1]):
                c += 0.5
        for other in placed:
            for i in range(len(poly_tl) - 1):
                for j in range(len(other) - 1):
                    q = seg_intersect(poly_tl[i], poly_tl[i + 1], other[j], other[j + 1])
                    if q and not any(e and point_in(q, (e[0] - 0.02, e[1] - 0.02, e[2] + 0.02, e[3] + 0.02)) for e in ends):
                        c += 0.3
        return c

    for ar in all_arrows:
        a, bb = registry.get(ar["from"]), registry.get(ar["to"])
        if not a or not bb or "box" not in a or "box" not in bb:
            registry[ar["id"]] = dict(kind="arrow", error=f"endpoint missing: {ar['from']} -> {ar['to']}")
            continue
        st = ARROW_KINDS.get(ar.get("kind", "activates"), ARROW_KINDS["activates"])
        col = ar.get("color", "#333333")
        pA = a.get("anchor_out") or a.get("anchor") or a["box"].center()
        pB = bb.get("anchor_in") or bb.get("anchor") or bb["box"].center()
        if a["kind"] == "panel" or bb["kind"] == "panel":
            def edge(box, toward):
                cx, cy = box.center(); tx, ty = toward
                if abs(tx - cx) >= abs(ty - cy):
                    return (box.x0 + box.w + 0.004, cy) if tx > cx else (box.x0 - 0.004, cy)
                return (cx, 1 - box.y0 + 0.004) if ty > cy else (cx, 1 - box.y0 - box.h - 0.004)
            if a["kind"] == "panel":
                pA = edge(a["box"], bb["box"].center())
            if bb["kind"] == "panel":
                pB = edge(bb["box"], a["box"].center())
        auto = None
        if ar.get("connection"):
            conn = ar["connection"]
            m = re.search(r"rad\s*=\s*([-\d.]+)", conn)
            rad = float(m.group(1)) if m else 0.0
        elif (ar["to"], ar["from"]) in pairs and a["kind"] in ENT_KINDS and bb["kind"] in ENT_KINDS:
            rad = 0.25
            conn = f"arc3,rad={rad}"
            auto = "opposite pair -> two arcs"
        elif a["kind"] in ENT_KINDS and bb["kind"] in ENT_KINDS:
            best = None
            for r in (0.0, 0.22, -0.22, 0.4, -0.4):
                c = cost(to_tl(curve_points(pA, pB, r, W, H, n=16)), ar["from"], ar["to"]) + abs(r) * 0.2
                if best is None or c < best[0] - 1e-9:
                    best = (c, r)
            rad = best[1]
            conn = f"arc3,rad={rad}"
            if rad:
                auto = f"routed around other boxes (rad={rad})"
        else:
            rad, conn = 0.0, "arc3,rad=0.0"
        poly = to_tl(curve_points(pA, pB, rad, W, H, n=24))
        placed.append(to_tl(curve_points(pA, pB, rad, W, H, n=16)))
        fa = FancyArrowPatch(pA, pB, arrowstyle=st["arrowstyle"],
                             linestyle=st["linestyle"], mutation_scale=ar.get("scale", 14), color=col,
                             lw=ar.get("lw", 2.4 if ar.get("kind") == "flow" else 1.6), shrinkA=2, shrinkB=2,
                             patchA=a.get("artist") if a["kind"] in ENT_KINDS else None,
                             patchB=bb.get("artist") if bb["kind"] in ENT_KINDS else None,
                             connectionstyle=conn, zorder=4)
        ax.add_patch(fa)
        fa.set_gid(ar["id"])
        entry = dict(kind="arrow", artist=fa, frm=ar["from"], to=ar["to"], arrow_kind=ar.get("kind", "activates"),
                     poly=poly, auto=auto, connection=conn)
        if ar.get("label"):
            lab = pretty(ar["label"])
            lsize = ar.get("size", base * 0.75) * adj.get(ar["id"] + ":label", {}).get("fscale", 1.0)
            sizes[ar["id"] + ":label"] = ar.get("size", base * 0.75)
            lw_, lh_ = text_em(lab) * lsize / 72 / W, 1.3 * lsize / 72 / H
            pts = curve_points(pA, pB, rad, W, H, n=20)
            cands = [ar["label_pos"]] if "label_pos" in ar else [0.5, 0.4, 0.6, 0.3, 0.7, 0.25, 0.75]
            chosen = None
            for tpos in cands:
                px, py = pts[int(round(tpos * 20))]
                r = (px - lw_ / 2, 1 - py - lh_ / 2, px + lw_ / 2, 1 - py + lh_ / 2)
                bad = [k for k, o in obstacles.items() if rects_overlap(r, o)] + \
                      [k for k, o in soft.items() if rects_overlap(r, o)] + \
                      [1 for lb in label_boxes if rects_overlap(r, lb)]
                if chosen is None or not bad:
                    chosen = (px, py, r)
                if not bad:
                    break
            px, py, r = chosen
            label_boxes.append(r)
            t = ax.text(px, py, lab, fontsize=lsize, ha="center", va="center", color=col, transform=ax.transAxes,
                        zorder=7, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85))
            t.set_gid(ar["id"] + ":label")
            try:
                t.get_bbox_patch().set_gid(ar["id"] + ":label_bg")
            except Exception:
                pass
            entry["text"] = t
            texts.append((ar["id"] + ":label", t, None))
        registry[ar["id"]] = entry

    footnote = spec.get("footnote")
    if footnote:
        t = put_text("footnote", footnote, 0.01, 0.012, base * 0.7, ha="left", va="bottom",
                     color="#555555", wrap_w=0.98)
        texts.append(("footnote", t, None))
    registry["__svg_groups__"] = dict(kind="meta", groups=svg_groups)
    return fig, ax, registry, texts, sizes


def bbox_frac(artist, fig, renderer):
    bb = artist.get_window_extent(renderer=renderer)
    W, H = fig.get_size_inches() * fig.dpi
    return (bb.x0 / W, 1 - bb.y1 / H, bb.x1 / W, 1 - bb.y0 / H)  # top-left origin


def _bb_frac(bb, fig):
    W, H = fig.get_size_inches() * fig.dpi
    return (bb.x0 / W, 1 - bb.y1 / H, bb.x1 / W, 1 - bb.y0 / H)


def check_layout(fig, registry, texts, sizes, min_pt):
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    issues = []
    tb = {}
    for eid, t, cont in texts:
        if not t.get_text().strip():
            continue
        x0, y0, x1, y1 = bbox_frac(t, fig, r)
        tb[eid] = (x0, y0, x1, y1)
        if x0 < -0.001 or y0 < -0.001 or x1 > 1.001 or y1 > 1.001:
            issues.append(dict(level="fail", check="out_of_canvas", id=eid, bbox=[round(v, 4) for v in (x0, y0, x1, y1)]))
        if cont is not None:
            pad = 0.002
            if x0 < cont.x0 - pad or x1 > cont.x0 + cont.w + pad or y0 < cont.y0 - pad or y1 > cont.y0 + cont.h + pad:
                issues.append(dict(level="fail", check="text_overflow", id=eid,
                                   text_bbox=[round(v, 4) for v in (x0, y0, x1, y1)],
                                   container=[round(v, 4) for v in (cont.x0, cont.y0, cont.x0 + cont.w, cont.y0 + cont.h)]))
        if t.get_fontsize() < min_pt - 0.05:
            issues.append(dict(level="warn", check="font_too_small", id=eid, size=round(t.get_fontsize(), 1)))
    base_of = lambda k: k.split(":label")[0].split(":handle")[0]
    keys = list(tb)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            if base_of(keys[i]) == base_of(keys[j]) and ":label" not in keys[i] + keys[j]:
                continue
            if rects_overlap(tb[keys[i]], tb[keys[j]], 0.001):
                issues.append(dict(level="warn", check="text_overlap", ids=[keys[i], keys[j]],
                                   hint="move one element or shorten text"))
    ents = {k: v["box"].rect() for k, v in registry.items() if v.get("kind") in ENT_KINDS}
    for k, v in registry.items():
        if v.get("kind") == "icon_error":
            issues.append(dict(level="fail", check="icon_unknown", id=k, detail=v["error"],
                               valid=sorted(ICONS), hint="use one of the listed icon names (or an alias) or drop the icon"))
    ek = list(ents)
    for i in range(len(ek)):
        for j in range(i + 1, len(ek)):
            a, b = ents[ek[i]], ents[ek[j]]
            if rects_overlap(a, b, 0.002):
                issues.append(dict(level="warn", check="entity_overlap", ids=[ek[i], ek[j]]))
    arrows = {k: v for k, v in registry.items() if v.get("kind") == "arrow" and v.get("poly")}
    for k, v in arrows.items():
        inner = v["poly"][2:-2]
        hit = [e for e, rr in ents.items() if e not in (v["frm"], v["to"]) and any(point_in(p, rr, 0.002) for p in inner)]
        if hit:
            issues.append(dict(level="warn", check="arrow_through_entity", id=k, entities=hit,
                               hint="move the entities, or set connection 'arc3,rad=0.3' / '-0.3' on this arrow"))
        own = k + ":label"
        thit = [e for e, rr in tb.items() if e != own and base_of(e) not in (v["frm"], v["to"]) and
                not e.startswith("panel:") and e not in ents and any(point_in(p, rr, 0.0) for p in inner)]
        if thit:
            issues.append(dict(level="warn", check="arrow_through_text", id=k, texts=thit[:5]))
    ak = list(arrows)
    for i in range(len(ak)):
        for j in range(i + 1, len(ak)):
            A, B = arrows[ak[i]], arrows[ak[j]]
            shared = {A["frm"], A["to"]} & {B["frm"], B["to"]}
            ends = [ents.get(e) or (registry.get(e, {}).get("box").rect() if registry.get(e, {}).get("box") else None)
                    for e in (A["frm"], A["to"], B["frm"], B["to"])]
            hit = None
            for p in range(len(A["poly"]) - 1):
                for q in range(len(B["poly"]) - 1):
                    x = seg_intersect(A["poly"][p], A["poly"][p + 1], B["poly"][q], B["poly"][q + 1])
                    if x and not any(e and point_in(x, (e[0] - 0.015, e[1] - 0.015, e[2] + 0.015, e[3] + 0.015)) for e in ends):
                        hit = x
                        break
                if hit:
                    break
            if hit and not (shared and len(shared) == 2):
                issues.append(dict(level="warn", check="arrows_cross", ids=[ak[i], ak[j]],
                                   at=[round(hit[0], 3), round(hit[1], 3)],
                                   hint="reorder entities so arrows do not cross, or curve one arrow"))
    for k, v in arrows.items():
        lb = tb.get(k + ":label")
        if lb:
            over = [e for e, rr in ents.items() if rects_overlap(lb, rr, 0.002)]
            if over:
                issues.append(dict(level="warn", check="label_overlap", id=k + ":label", entities=over,
                                   hint="set label_pos (0..1 along the arrow) or shorten the label"))
    # charts: tight bbox inside canvas, crowded tick labels, collisions
    for k, v in registry.items():
        if v.get("kind") != "chart":
            continue
        cax = v["artist"]
        try:
            cb = _bb_frac(cax.get_tightbbox(r), fig)
        except Exception:
            continue
        v["tight"] = cb
        if cb[0] < -0.002 or cb[1] < -0.002 or cb[2] > 1.002 or cb[3] > 1.002:
            issues.append(dict(level="fail", check="chart_out_of_canvas", id=k, bbox=[round(x, 3) for x in cb],
                               hint="reduce chart w/h or move it; rotated tick labels need ~0.25 of height below the axes"))
        pb = v.get("parent_box")
        if pb is not None and pb.rect() != (0, 0, 1, 1):
            pr = pb.rect()
            if cb[0] < pr[0] - 0.004 or cb[1] < pr[1] - 0.004 or cb[2] > pr[2] + 0.004 or cb[3] > pr[3] + 0.004:
                issues.append(dict(level="warn", check="chart_outside_panel", id=k, bbox=[round(x, 3) for x in cb]))
        labs = [t for t in (cax.get_yticklabels() if v.get("horiz") else cax.get_xticklabels()) if t.get_text()]
        rot = labs[0].get_rotation() if labs else 0
        if rot % 180 == 0 or v.get("horiz"):
            lbs = [bbox_frac(t, fig, r) for t in labs]
            crowd = any(rects_overlap(lbs[i], lbs[i + 1], 0.0005) for i in range(len(lbs) - 1))
        else:   # rotated labels are parallel: crowded when their perpendicular spacing < text height
            xs = sorted(cax.transData.transform((t.get_position()[0], 0))[0] for t in labs)
            gap = min((b - a for a, b in zip(xs, xs[1:])), default=1e9)
            crowd = gap * abs(math.sin(math.radians(rot))) < 1.15 * labs[0].get_fontsize() * fig.dpi / 72
        if crowd:
            issues.append(dict(level="warn", check="tick_labels_overlap", id=k,
                               hint="auto-rotated; for many/long categories use kind 'barh'"))
        for e, rr in tb.items():
            if e in ("title",) or e.startswith("panel:"):
                continue
            if rects_overlap(cb, rr, 0.004) and not any(rects_overlap(rr, ents[x], 0) for x in ents):
                issues.append(dict(level="warn", check="chart_overlaps_text", id=k, text=e))
        if v.get("n_missing"):
            issues.append(dict(level="info", check="chart_missing_values", id=k, n=v["n_missing"],
                               where=v.get("missing"), note="drawn as 'n.r.' markers; say so in footnote/result.json"))
    for k, v in registry.items():
        if v.get("kind") == "legend":
            lr = v["box"].rect()
            hit = [e for e, rr in ents.items() if rects_overlap(lr, rr, 0.004)]
            hit += [a for a, av in arrows.items() if any(point_in(p, lr, 0.004) for p in av["poly"][2:-2])]
            if hit:
                issues.append(dict(level="warn", check="legend_overlap", id=k, overlaps=hit[:6]))
    for k, v in registry.items():
        if v.get("kind") == "arrow" and v.get("error"):
            issues.append(dict(level="fail", check="arrow_endpoint", id=k, detail=v["error"]))
    return issues


# ---------------------------------------------------------------- SVG post-processing
def postprocess_svg(path, spec, texts, W, icon_groups=()):
    """Centre-anchor multi-line centred labels (so later text edits stay centred), gather each icon's parts
    (+ its label) into one <g id=element id>, and gather the flat matplotlib groups into one Inkscape layer
    per panel + connectors + annotations."""
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    ET.register_namespace("inkscape", INK_NS)
    tree = ET.parse(path)
    root = tree.getroot()
    byid = {el.get("id"): el for el in root.iter() if el.get("id")}
    for eid, t, _ in texts:
        g = byid.get(eid)
        if g is None or t.get_ha() not in ("center", "right") or "\n" not in t.get_text():
            continue
        anchor = "middle" if t.get_ha() == "center" else "end"
        x_svg = t.get_position()[0] * W * 72
        for tx in [c for c in g.iter() if c.tag.endswith("text")]:
            m = re.match(r"translate\(([-\d.e]+)[ ,]+([-\d.e]+)\)", tx.get("transform") or "")
            if not m:
                continue
            tx.attrib.pop("transform")
            tx.set("x", f"{x_svg:.3f}")
            tx.set("y", m.group(2))
            st = tx.get("style") or ""
            if "text-anchor" not in st:
                st = st.replace("; fill", f"; text-anchor: {anchor}; fill") if "; fill" in st else st + f"; text-anchor: {anchor}"
            tx.set("style", st)
    # icon groups: parts (one <g id=x:pN><path/></g> each) are unwrapped into a single <g id=x> with the label
    canvas0 = byid.get("layer:canvas")
    if canvas0 is not None:
        for gspec in icon_groups:
            kids = list(canvas0)
            members = [c for c in kids if gspec["rx"].match(c.get("id") or "") or (c.get("id") in gspec["extra"])]
            if not members:
                continue
            wrap = ET.Element("{%s}g" % SVG_NS)
            wrap.set("id", gspec["id"])
            wrap.set("{%s}label" % INK_NS, gspec["label"])
            pos = kids.index(members[0])
            for m in members:
                canvas0.remove(m)
                if gspec["rx"].match(m.get("id") or ""):
                    for ch in list(m):
                        wrap.append(ch)
                else:
                    wrap.append(m)
            canvas0.insert(pos, wrap)
    # layer assignment
    owner = {}
    def walk(elements, pid):
        for el in elements:
            owner[el["id"]] = pid
            if el.get("type") == "group":
                walk(el.get("elements", []), pid)
    for p in spec.get("panels", []):
        owner["panel:" + p["id"]] = p["id"]
        walk(p.get("elements", []), p["id"])
        for a in p.get("arrows", []):
            owner[a["id"]] = p["id"]
    walk(spec.get("elements", []), None)
    for a in spec.get("arrows", []):
        f, t = owner.get(a["from"]), owner.get(a["to"])
        owner[a["id"]] = f if (f and f == t) else "__connectors"
    def layer_of(gid):
        if gid.startswith("panel:"):
            return gid.split(":")[1]
        base = gid.split(":")[0]
        if base in owner:
            return owner[base] or "__annotations"
        if base in ("title", "footnote"):
            return "__annotations"
        return None
    canvas = byid.get("layer:canvas")
    fig_g = byid.get("figure_1")
    if canvas is None or fig_g is None:
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return 0
    order = [p["id"] for p in spec.get("panels", [])] + ["__connectors", "__annotations"]
    layers = {}
    for lid in order:
        g = ET.Element("{%s}g" % SVG_NS)
        name = "connectors" if lid == "__connectors" else ("annotations" if lid == "__annotations" else lid)
        g.set("id", "layer:" + name if lid.startswith("__") else "layer:panel_" + lid)
        g.set("{%s}groupmode" % INK_NS, "layer")
        role = next((p.get("role") for p in spec.get("panels", []) if p["id"] == lid), None)
        g.set("{%s}label" % INK_NS, name + (f" ({role})" if role else ""))
        layers[lid] = g
    moved = 0
    first_idx = None
    for parent in (canvas, fig_g):
        for child in list(parent):
            gid = child.get("id") or ""
            lid = layer_of(gid) if gid else None
            if lid in layers:
                if parent is canvas and first_idx is None:
                    first_idx = list(canvas).index(child)
                parent.remove(child)
                layers[lid].append(child)
                moved += 1
    pos = first_idx if first_idx is not None else len(canvas)
    for lid in order:
        if len(layers[lid]):
            canvas.insert(pos, layers[lid])
            pos += 1
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return moved


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
    spec = load_json_lenient(args.spec)
    spec_dir = os.path.dirname(os.path.abspath(args.spec))
    os.makedirs(args.outdir, exist_ok=True)
    missing_data = [dict(level="fail", check="chart_csv_missing", id=el.get("id"), path=el["data"]["csv"],
                         hint="give the CSV path as written in /input (absolute), or copy the table to /app/work/<name>.csv")
                    for _, el in iter_all(spec) if el.get("type") == "chart" and isinstance(el.get("data"), dict)
                    and el["data"].get("csv") and not os.path.exists(find_data_file(el["data"]["csv"], spec_dir))]
    if missing_data:   # stop cleanly (no traceback): nothing can be drawn without the data
        print(json.dumps(dict(status="needs_fix", fails=len(missing_data), warns=0, outputs={}), ensure_ascii=False))
        for i in missing_data:
            print("ISSUE", json.dumps(i, ensure_ascii=False))
        return 2
    all_text = json.dumps(spec, ensure_ascii=False)
    fams, font_notes, cjk_ok = pick_fonts(spec, all_text)
    # explicit family list -> per-glyph fallback (Latin font, CJK font, DejaVu Sans for symbols such as ⊣ →)
    matplotlib.rcParams["font.family"] = fams + (["DejaVu Sans"] if "DejaVu Sans" not in fams else [])

    # fix loop: shrink only the labels that overflow (never below min font), rotate crowded ticks
    adj, attempts, missing_glyphs = {}, [], set()
    for attempt in range(7):
        with warnings.catch_warnings(record=True) as wlog:
            warnings.simplefilter("always")
            fig, ax, registry, texts, sizes = draw(spec, spec_dir, adj)
            issues = check_layout(fig, registry, texts, sizes, args.min_font_pt)
        for w in wlog:
            m = re.search(r"Glyph (\d+) .*missing", str(w.message))
            if m:
                missing_glyphs.add(chr(int(m.group(1))))
        over = sorted({i["id"] for i in issues if i["check"] in ("text_overflow", "out_of_canvas")})
        crowd = sorted({i["id"] for i in issues if i["check"] == "tick_labels_overlap"})
        attempts.append(dict(attempt=attempt, overflow=over[:12], crowded_ticks=crowd))
        changed = False
        for eid in over:
            cur = adj.get(eid, {}).get("fscale", 1.0)
            if sizes.get(eid, 10) * cur * 0.88 >= args.min_font_pt:
                adj.setdefault(eid, {})["fscale"] = cur * 0.88
                changed = True
        for cid in crowd:
            el = next((e for _, e in iter_all(spec) if e.get("id") == cid), {})
            cur = adj.get(cid, {}).get("rotation", el.get("xtick_rotation", 0))
            nxt = {0: 45, 30: 45, 45: 90}.get(cur)
            if nxt and el.get("kind", "bar") != "barh":
                adj.setdefault(cid, {})["rotation"] = nxt
                changed = True
            elif cur >= 90 or el.get("kind") == "barh":
                fs = adj.get(cid, {}).get("fscale", 1.0)
                if fs > 0.75:
                    adj.setdefault(cid, {})["fscale"] = fs * 0.88
                    changed = True
        if not changed:
            break
        plt.close(fig)

    if missing_glyphs:
        issues.append(dict(level="fail" if not cjk_ok else "warn", check="glyph_missing", chars=sorted(missing_glyphs)[:30],
                           hint="these characters render as boxes: replace them (e.g. 'alpha' for α, '->' for →) "
                                "or use characters the reported fonts contain"))
    elif not cjk_ok:
        issues.append(dict(level="fail", check="cjk_font_missing"))
    dpi = int(spec.get("canvas", {}).get("dpi", 300))
    out = {}
    for ext in ("svg", "pdf", "png"):
        p = os.path.join(args.outdir, f"{args.name}.{ext}")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                # no timestamps in the files: the same spec gives byte-identical SVG/PDF/PNG (re-run check)
                meta = {"svg": {"Date": None}, "pdf": {"CreationDate": None, "ModDate": None}}.get(ext)
                fig.savefig(p, dpi=dpi if ext == "png" else None, facecolor=fig.get_facecolor(), metadata=meta)
            out[ext] = dict(path=p, bytes=os.path.getsize(p), sha256=sha256(p))
            if ext == "png":
                out[ext]["pixels"] = png_size(p)
                out[ext]["dpi"] = dpi
        except Exception as e:  # report, keep going
            out[ext] = dict(path=p, error=repr(e))
            issues.append(dict(level="fail", check="export", format=ext, detail=repr(e)))
    W = float(spec.get("canvas", {}).get("width_in", 13.33))
    plt.close(fig)

    # SVG: centre anchors + panel layers, then sanity (ids present, text kept as text)
    svg_ids_missing = []
    if "path" in out.get("svg", {}) and "error" not in out["svg"]:
        try:
            out["svg"]["groups_layered"] = postprocess_svg(out["svg"]["path"], spec, texts, W,
                                                           registry.get("__svg_groups__", {}).get("groups", []))
        except Exception as e:
            issues.append(dict(level="warn", check="svg_postprocess", detail=repr(e)))
        svg = open(out["svg"]["path"], encoding="utf-8").read()
        for k, v in registry.items():
            if v.get("kind") in ("entity", "icon", "arrow", "chart", "text", "panel") and f'id="{k}' not in svg:
                svg_ids_missing.append(k)
        out["svg"]["text_elements"] = svg.count("<text")
        out["svg"]["sha256"] = sha256(out["svg"]["path"])
        out["svg"]["bytes"] = os.path.getsize(out["svg"]["path"])
    if svg_ids_missing:
        issues.append(dict(level="warn", check="svg_layer_ids", missing=svg_ids_missing))

    layer_map = {}
    def walk(elements, panel=None):
        for el in elements:
            layer_map[el["id"]] = dict(type=el.get("type", "entity"), panel=panel,
                                       label=el.get("label") or el.get("text") or el.get("title"),
                                       category=el.get("category"), source=el.get("source"))
            ic = el.get("name") if el.get("type") == "icon" else el.get("icon")
            if ic:
                layer_map[el["id"]]["icon"] = icon_canonical(ic)
                layer_map[el["id"]]["icon_svg_group"] = el["id"] if el.get("type") == "icon" else el["id"] + ":icon"
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
    auto = {}
    for k, v in registry.items():
        if v.get("kind") == "chart" and k in layer_map:
            for f in ("data_origin", "n_points", "n_series", "n_missing", "missing"):
                layer_map[k][f] = v.get(f)
        if v.get("kind") == "arrow" and v.get("auto"):
            auto[k] = v["auto"]
            if k in layer_map:
                layer_map[k]["auto_routing"] = v["auto"]
    with open(os.path.join(args.outdir, "layer_map.json"), "w", encoding="utf-8") as f:
        json.dump(layer_map, f, ensure_ascii=False, indent=1)

    fails = [i for i in issues if i["level"] == "fail"]
    warns = [i for i in issues if i["level"] == "warn"]
    shrunk = {k: round(v["fscale"], 2) for k, v in adj.items() if "fscale" in v}
    report = dict(status="ok" if not fails else "needs_fix", outputs=out, fonts=fams[:3],
                  font_notes=font_notes, fix_attempts=attempts, shrunk_labels=shrunk,
                  rotated_ticks={k: v["rotation"] for k, v in adj.items() if "rotation" in v},
                  auto_routing=auto, issues=issues, n_elements=len(layer_map), canvas=spec.get("canvas", {}))
    with open(os.path.join(args.outdir, "render_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=report["status"], fails=len(fails), warns=len(warns),
                          outputs={k: v.get("path") for k, v in out.items()},
                          png_pixels=out.get("png", {}).get("pixels"), font_notes=font_notes,
                          shrunk_labels=shrunk, auto_routing=auto), ensure_ascii=False))
    for i in [i for i in issues if i["level"] != "info"][:15] + [i for i in issues if i["level"] == "info"][:3]:
        print("ISSUE", json.dumps(i, ensure_ascii=False))
    return 0 if not fails else 2


def iter_all(spec):
    for el in spec.get("elements", []):
        yield None, el
        for sub in el.get("elements", []) if el.get("type") == "group" else []:
            yield None, sub
    for p in spec.get("panels", []):
        for el in p.get("elements", []):
            yield p["id"], el
            for sub in el.get("elements", []) if el.get("type") == "group" else []:
                yield p["id"], sub


if __name__ == "__main__":
    sys.exit(main())
