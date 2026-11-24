#!/usr/bin/env python3
"""pptkit.py - stdlib-only PresentationML (PPTX) toolkit shared by the deck_* scripts.

Nothing here needs python-pptx. It provides:
  * Package           read/modify/write a .pptx/.potx zip, namespace-preserving XML round trips
  * make_default_template()  a clean academic 16:9 master + 6 layouts + theme, generated in code
  * catalog()         masters, layouts, placeholders (with inherited boxes), theme fonts/colours, text styles
  * inventory()       every slide -> shapes (id, name, kind, box, paragraphs, effective font sizes), notes
  * XML snippet builders for paragraphs, text boxes, pictures, tables, notes, footers
  * text measurement (wrap simulation, CJK aware) used for overflow estimates and font fitting
  * small utilities: image_size(), find_soffice(), fc_match(), sha256(), crop_pdf_region()
Units: EMU (914400 per inch, 12700 per point) unless a name says _in or _pt.
"""
import copy, hashlib, io, json, math, os, posixpath, re, shutil, struct, subprocess, tempfile, zipfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as _xesc

EMU_IN = 914400
EMU_PT = 12700
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
EXTRA_NS = {
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p14": "http://schemas.microsoft.com/office/powerpoint/2010/main",
    "p15": "http://schemas.microsoft.com/office/powerpoint/2012/main",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "a16": "http://schemas.microsoft.com/office/drawing/2014/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "v": "urn:schemas-microsoft-com:vml",
}
for _k, _v in list(NS.items()) + list(EXTRA_NS.items()):
    ET.register_namespace(_k, _v)
A = "{%s}" % NS["a"]
P = "{%s}" % NS["p"]
R = "{%s}" % NS["r"]
NSDECL = 'xmlns:a="%s" xmlns:r="%s" xmlns:p="%s"' % (NS["a"], NS["r"], NS["p"])

RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
RT_SLIDE, RT_LAYOUT, RT_MASTER, RT_THEME = RT + "slide", RT + "slideLayout", RT + "slideMaster", RT + "theme"
RT_NOTES, RT_NOTESMASTER, RT_IMAGE = RT + "notesSlide", RT + "notesMaster", RT + "image"
CT = "application/vnd.openxmlformats-officedocument."
CT_PRES = CT + "presentationml.presentation.main+xml"
CT_TEMPLATE = CT + "presentationml.template.main+xml"
CT_SLIDESHOW = CT + "presentationml.slideshow.main+xml"
CT_SLIDE = CT + "presentationml.slide+xml"
CT_LAYOUT = CT + "presentationml.slideLayout+xml"
CT_MASTER = CT + "presentationml.slideMaster+xml"
CT_NOTES = CT + "presentationml.notesSlide+xml"
CT_NOTESMASTER = CT + "presentationml.notesMaster+xml"
CT_THEME = CT + "theme+xml"
IMG_CT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
          "bmp": "image/bmp", "tif": "image/tiff", "tiff": "image/tiff", "emf": "image/x-emf",
          "wmf": "image/x-wmf", "svg": "image/svg+xml"}
SLIDE_W, SLIDE_H = 12192000, 6858000   # 13.333 x 7.5 in (16:9)


# ----------------------------------------------------------------------------- small utils
def xt(s):
    """escape text for XML element content; drop control characters"""
    return _xesc(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s)))


def xa(s):
    return _xesc(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s)), {'"': "&quot;"})


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def emu(inches):
    return int(round(float(inches) * EMU_IN))


def to_in(v):
    return round(v / EMU_IN, 3)


def run(cmd, timeout=180, env=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:  # missing binary, timeout
        return 127, "", repr(e)


def has_cjk(s):
    return any(is_wide(ch) for ch in str(s))


def is_wide(ch):
    o = ord(ch)
    return (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF
            or 0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60 or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x2FFFF)


def image_size(path):
    """(width_px, height_px) for PNG/JPEG/GIF/BMP without third-party libraries; None if unknown"""
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"BM":
                w, h = struct.unpack("<ii", head[18:26])
                return w, abs(h)
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    b = f.read(1)
                    while b and b != b"\xff":
                        b = f.read(1)
                    while b == b"\xff":
                        b = f.read(1)
                    if not b:
                        return None
                    marker = b[0]
                    if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        f.read(3)
                        h, w = struct.unpack(">HH", f.read(4))
                        return w, h
                    seg = struct.unpack(">H", f.read(2))[0]
                    f.seek(seg - 2, 1)
    except Exception:
        return None
    return None


def find_soffice():
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in ("/Applications/LibreOffice.app/Contents/MacOS/soffice", "/usr/lib/libreoffice/program/soffice",
              "/opt/libreoffice/program/soffice", "/usr/local/bin/soffice", "/snap/bin/libreoffice"):
        if os.path.exists(p):
            return p
    import glob as _g
    c = sorted(_g.glob("/opt/libreoffice*/program/soffice"))
    return c[-1] if c else None


def font_files(families, want_cjk=False, limit=14):
    """font files for the given families (fc-list); plus CJK fonts when needed. Used to hand fonts to LibreOffice
    builds that do not see system fonts (e.g. macOS): they are linked into the private profile's user/fonts."""
    files = []
    if not shutil.which("fc-list"):
        return files
    for fam in families:
        if not fam or fam.startswith("+"):
            continue
        rc, out, _ = run(["fc-list", fam, "file", "style"], timeout=20)
        styled = []
        for line in out.splitlines():
            f, _, st = line.partition(":")
            st = st.split("=")[-1].split(",")[0].strip().lower()
            rank = {"regular": 0, "bold": 1, "italic": 2, "bold italic": 3, "w3": 0, "w6": 1, "medium": 4, "light": 5}.get(st, 9)
            styled.append((rank, f.strip()))
        for rank, f in sorted(styled)[:4]:
            if f and f not in files and rank < 9:
                files.append(f)
        if not any(r < 9 for r, _ in styled):
            for _, f in sorted(styled)[:1]:
                if f and f not in files:
                    files.append(f)
    if want_cjk:
        rc, out, _ = run(["fc-list", ":lang=zh", "file", "family"], timeout=20)
        cands = sorted({l.split(":")[0].strip() for l in out.splitlines() if l.strip()},
                       key=lambda f: (0 if re.search(r"(?i)noto.*cjk|sourcehan|wqy|wenquan|hiragino|pingfang|yahei|heiti|simhei|droid", f) else 1, f))
        for f in cands[:3]:
            if f not in files:
                files.append(f)
    return files[:limit]


def soffice_convert(src, outdir, fmt="pdf", timeout=240, fonts=None):
    """convert with a private LibreOffice profile (avoids lock/first-start problems). Returns (path|None, log).
    fonts: font files linked into the profile's user/fonts so the renderer can use them."""
    so = find_soffice()
    if not so:
        return None, "soffice/libreoffice not found"
    os.makedirs(outdir, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="lo_prof_")
    if fonts:
        fd = os.path.join(prof, "user", "fonts")
        os.makedirs(fd, exist_ok=True)
        for i, f in enumerate(fonts):
            try:
                os.symlink(f, os.path.join(fd, "%02d_%s" % (i, os.path.basename(f))))
            except OSError:
                try:
                    shutil.copyfile(f, os.path.join(fd, "%02d_%s" % (i, os.path.basename(f))))
                except OSError:
                    pass
    cmd = [so, "-env:UserInstallation=file://" + prof, "--headless", "--norestore", "--nologo",
           "--convert-to", fmt, "--outdir", outdir, src]
    rc, out, err = run(cmd, timeout=timeout)
    shutil.rmtree(prof, ignore_errors=True)
    target = os.path.join(outdir, os.path.splitext(os.path.basename(src))[0] + "." + fmt.split(":")[0])
    if os.path.exists(target):
        return target, (out + err).strip()[-400:]
    return None, ("rc=%s " % rc + (out + err).strip())[-600:]


_FC_CACHE = {}


def fc_match(family):
    """resolve a font family with fontconfig; returns (resolved_family or None, tool_available)"""
    if family in _FC_CACHE:
        return _FC_CACHE[family]
    if not shutil.which("fc-match"):
        _FC_CACHE[family] = (None, False)
        return _FC_CACHE[family]
    rc, out, _ = run(["fc-match", "-f", "%{family}", family], timeout=20)
    res = (out.split(",")[0].strip() if rc == 0 and out.strip() else None, True)
    _FC_CACHE[family] = res
    return res


METRIC_TWINS = {"arial": "liberation sans", "helvetica": "liberation sans", "times new roman": "liberation serif",
                "courier new": "liberation mono", "calibri": "carlito", "cambria": "caladea",
                "arial narrow": "liberation sans narrow"}


def font_status(family):
    """'available' | 'metric_compatible:<x>' | 'substituted:<x>' | 'unknown' (no fontconfig)"""
    if not family or family.startswith("+"):
        return "theme-ref"
    got, ok = fc_match(family)
    if not ok:
        return "unknown"
    if got is None:
        return "substituted:?"
    if got.lower() == family.lower():
        return "available"
    if METRIC_TWINS.get(family.lower()) == got.lower():
        return "metric_compatible:" + got
    return "substituted:" + got


LATIN_PREF = ["Arial", "Liberation Sans", "Helvetica", "Carlito", "DejaVu Sans", "Noto Sans"]
EA_PREF = ["Noto Sans CJK SC", "Source Han Sans SC", "Source Han Sans CN", "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
           "PingFang SC", "Microsoft YaHei", "SimHei", "Heiti SC", "Droid Sans Fallback", "AR PL UMing CN"]


def pick_fonts():
    """choose theme fonts that exist here (or have metric twins); returns dict(latin, ea, notes)"""
    notes = []
    latin = "Arial"
    st = font_status("Arial")
    if st.startswith("substituted"):
        for f in LATIN_PREF[1:]:
            if font_status(f) == "available":
                latin = f
                break
        notes.append("Arial unavailable (%s); theme latin font = %s" % (st, latin))
    ea = None
    if shutil.which("fc-list"):
        rc, out, _ = run(["fc-list", ":lang=zh", "family"], timeout=20)
        fams = set(x.strip() for line in out.splitlines() for x in line.split(","))
        for f in EA_PREF:
            if f in fams:
                ea = f
                break
        if not ea and fams:
            ea = sorted(fams)[0]
    if not ea:
        ea = "Microsoft YaHei"
        notes.append("no CJK font detected by fc-list; theme ea font set to Microsoft YaHei (may be substituted)")
    return dict(latin=latin, ea=ea, notes=notes)


def crop_pdf_region(pdf, page, bbox, out_png, dpi=200):
    """render page (1-based) of a PDF and crop bbox=(x0,y0,x1,y1) fractions of the page (None = full page)."""
    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    base = out_png[:-4] if out_png.lower().endswith(".png") else out_png
    if shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-png", "-singlefile"]
        if bbox:
            rc, out, _ = run(["pdfinfo", "-f", str(page), "-l", str(page), pdf], timeout=30) if shutil.which("pdfinfo") else (1, "", "")
            m = re.search(r"Page\s+%d size:\s+([\d.]+) x ([\d.]+)" % page, out) or re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", out)
            if m:
                wpt, hpt = float(m.group(1)), float(m.group(2))
                W, H = wpt / 72 * dpi, hpt / 72 * dpi
                x0, y0, x1, y1 = bbox
                cmd += ["-x", str(int(x0 * W)), "-y", str(int(y0 * H)), "-W", str(max(1, int((x1 - x0) * W))),
                        "-H", str(max(1, int((y1 - y0) * H)))]
        rc, out, err = run(cmd + [pdf, base], timeout=120)
        if rc == 0 and os.path.exists(base + ".png"):
            if bbox and "-x" not in cmd:
                _crop_png_inplace(base + ".png", bbox)
            return base + ".png", "pdftoppm"
    try:
        import fitz
        doc = fitz.open(pdf)
        pg = doc[page - 1]
        r = pg.rect
        clip = fitz.Rect(r.x0 + bbox[0] * r.width, r.y0 + bbox[1] * r.height, r.x0 + bbox[2] * r.width,
                         r.y0 + bbox[3] * r.height) if bbox else None
        pix = pg.get_pixmap(dpi=dpi, clip=clip)
        pix.save(base + ".png")
        return base + ".png", "pymupdf"
    except Exception as e:
        return None, "no PDF renderer (pdftoppm/PyMuPDF): %r" % (e,)


def _crop_png_inplace(path, bbox):
    try:
        from PIL import Image
        im = Image.open(path)
        W, H = im.size
        im.crop((int(bbox[0] * W), int(bbox[1] * H), int(bbox[2] * W), int(bbox[3] * H))).save(path)
    except Exception:
        pass


# ----------------------------------------------------------------------------- text measurement
def char_em(ch):
    if is_wide(ch):
        return 1.0
    if ch == " ":
        return 0.28
    if ch in "il.,:;|!'`()[]{}/\\-":
        return 0.33
    if ch in "mwMW@%":
        return 0.85
    if ch.isupper():
        return 0.68
    if ch.isdigit():
        return 0.56
    return 0.53


def text_em(s):
    return sum(char_em(c) for c in str(s))


def wrap_count(text, width_pt, size_pt):
    """greedy word-wrap simulation; returns number of lines"""
    if width_pt <= size_pt:
        return 999
    cap = width_pt / size_pt  # line capacity in em
    lines = 0
    for seg in str(text).split("\n"):
        toks = re.findall(r"[^\s\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff00-\uffef]+\s*|[\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\uff00-\uffef]|\s+", seg)
        cur, n = 0.0, 1
        for t in toks:
            w = text_em(t)
            wt = text_em(t.rstrip())
            if cur + wt > cap and cur > 0:
                n += 1
                cur = 0.0
            while wt > cap:  # very long token is broken
                n += 1
                wt -= cap
                w -= cap
            cur += w
        lines += n
    return max(1, lines)


def text_height_pt(paras, width_pt, line_factor=None, indent_pt_per_level=27.0, bullet_pt=0.0, insets_pt=(7.2, 7.2)):
    """paras: list of dict(text, size, level). Returns estimated height in pt for a text body."""
    h = insets_pt[1]
    for i, p in enumerate(paras):
        size = float(p.get("size") or 18)
        lf = line_factor or (1.32 if has_cjk(p.get("text", "")) else 1.2)
        w = width_pt - insets_pt[0] - p.get("level", 0) * indent_pt_per_level - bullet_pt
        n = wrap_count(p.get("text", ""), w, size)
        h += n * size * lf + ((float(p["spc"]) if p.get("spc") is not None else size * 0.35) if i else 0)
    return h


# ----------------------------------------------------------------------------- XML round trips
def parse_xml(data):
    decls = []
    try:
        for ev, item in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
            if item not in decls:
                decls.append(item)
    except ET.ParseError:
        pass
    return ET.fromstring(data), decls


def dump_xml(root, decls=()):
    for pfx, uri in decls:
        if pfx and not re.match(r"ns\d+$", pfx) and pfx != "xml":
            try:
                ET.register_namespace(pfx, uri)
            except ValueError:
                pass
    s = ET.tostring(root, encoding="unicode")
    m = re.match(r"<([^\s>/]+)([^>]*)>", s)
    if m:
        head = m.group(0)
        add = ""
        for pfx, uri in decls:
            key = ("xmlns:%s=" % pfx) if pfx else "xmlns="
            if key not in head:
                add += ' %s"%s"' % (key, uri)
        if add:
            cut = 1 + len(m.group(1))
            s = s[:cut] + add + s[cut:]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + s).encode("utf-8")


class Package:
    """A PPTX/POTX zip held in memory. Parts are bytes; xml() gives a cached element tree (saved on save())."""

    def __init__(self, path=None):
        self.parts = {}
        self._xml = {}
        self._dirty = set()
        self.path = path
        if path:
            with zipfile.ZipFile(path) as z:
                for n in z.namelist():
                    if not n.endswith("/"):
                        self.parts[n] = z.read(n)

    def has(self, name):
        return name in self.parts or name in self._xml

    def read(self, name):
        if name in self._xml:
            root, decls = self._xml[name]
            return dump_xml(root, decls)
        return self.parts[name]

    def write(self, name, data):
        self._xml.pop(name, None)
        self._dirty.discard(name)
        self.parts[name] = data.encode("utf-8") if isinstance(data, str) else data

    def xml(self, name):
        if name not in self._xml:
            self._xml[name] = parse_xml(self.parts[name])
        return self._xml[name][0]

    def touch(self, name):
        """mark a parsed part as modified (serialised now and again at save); parts only read stay byte-identical"""
        root, decls = self._xml[name]
        self.parts[name] = dump_xml(root, decls)
        self._dirty.add(name)

    def delete(self, name):
        self.parts.pop(name, None)
        self._xml.pop(name, None)
        self._dirty.discard(name)

    def flush(self):
        for name in list(self._dirty):
            if name in self._xml:
                self.touch(name)
        self._dirty.clear()

    def save(self, path):
        self.flush()
        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            order = ["[Content_Types].xml"] + [n for n in self.parts if n != "[Content_Types].xml"]
            for n in order:
                if n in self.parts:
                    z.writestr(n, self.parts[n])
        os.replace(tmp, path)
        return path

    # --- relationships
    @staticmethod
    def rels_name(part):
        d, b = posixpath.split(part)
        return posixpath.join(d, "_rels", b + ".rels")

    def rels(self, part):
        rn = self.rels_name(part)
        if rn not in self.parts:
            return []
        root = ET.fromstring(self.parts[rn])
        out = []
        for el in root:
            out.append(dict(Id=el.get("Id"), Type=el.get("Type"), Target=el.get("Target"),
                            TargetMode=el.get("TargetMode")))
        return out

    def write_rels(self, part, rels):
        s = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        for r in rels:
            mode = ' TargetMode="%s"' % r["TargetMode"] if r.get("TargetMode") else ""
            s.append('<Relationship Id="%s" Type="%s" Target="%s"%s/>' % (xa(r["Id"]), xa(r["Type"]), xa(r["Target"]), mode))
        s.append("</Relationships>")
        self.write(self.rels_name(part), "".join(s))

    @staticmethod
    def resolve(part, target):
        if target.startswith("/"):
            return target[1:]
        return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))

    @staticmethod
    def relpath(from_part, to_part):
        return posixpath.relpath(to_part, posixpath.dirname(from_part))

    def related(self, part, rtype):
        return [self.resolve(part, r["Target"]) for r in self.rels(part) if r["Type"] == rtype and r.get("TargetMode") != "External"]

    def add_rel(self, part, rtype, target_part, rels=None):
        rels = self.rels(part) if rels is None else rels
        used = {r["Id"] for r in rels}
        i = 1
        while "rId%d" % i in used:
            i += 1
        rid = "rId%d" % i
        rels.append(dict(Id=rid, Type=rtype, Target=self.relpath(part, target_part), TargetMode=None))
        self.write_rels(part, rels)
        return rid

    # --- content types
    def content_types(self):
        root = ET.fromstring(self.parts["[Content_Types].xml"])
        d, o = {}, {}
        for el in root:
            tag = el.tag.split("}")[-1]
            if tag == "Default":
                d[el.get("Extension").lower()] = el.get("ContentType")
            elif tag == "Override":
                o[el.get("PartName")] = el.get("ContentType")
        return d, o

    def write_content_types(self, d, o):
        s = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
        for k, v in d.items():
            s.append('<Default Extension="%s" ContentType="%s"/>' % (xa(k), xa(v)))
        for k, v in o.items():
            s.append('<Override PartName="%s" ContentType="%s"/>' % (xa(k), xa(v)))
        s.append("</Types>")
        self.write("[Content_Types].xml", "".join(s))

    def set_override(self, part, ctype):
        d, o = self.content_types()
        if ctype is None:
            o.pop("/" + part, None)
        else:
            o["/" + part] = ctype
        self.write_content_types(d, o)

    def ensure_default(self, ext, ctype):
        d, o = self.content_types()
        if ext.lower() not in d:
            d[ext.lower()] = ctype
            self.write_content_types(d, o)

    def unique_name(self, pattern, start=1):
        """pattern like 'ppt/slides/slide%d.xml'"""
        i = start
        while self.has(pattern % i):
            i += 1
        return pattern % i


def normalize_template(src, dst):
    """copy a .potx/.pptx/.ppsx to dst (.pptx) with the main content type set to presentation."""
    pkg = Package(src)
    d, o = pkg.content_types()
    changed = False
    for k, v in list(o.items()):
        if v in (CT_TEMPLATE, CT_SLIDESHOW):
            o[k] = CT_PRES
            changed = True
    if changed:
        pkg.write_content_types(d, o)
    pkg.save(dst)
    return changed


# ----------------------------------------------------------------------------- reading structure
def _box(el):
    x = el.find(P + "spPr/" + A + "xfrm")
    if x is None:
        x = el.find(P + "xfrm")
    if x is None:
        x = el.find(P + "grpSpPr/" + A + "xfrm")
    if x is None:
        return None
    off, ext = x.find(A + "off"), x.find(A + "ext")
    if off is None or ext is None:
        return None
    return [int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)), int(ext.get("cy", 0))]


def _ph(el):
    for path in (P + "nvSpPr/" + P + "nvPr/" + P + "ph", P + "nvPicPr/" + P + "nvPr/" + P + "ph",
                 P + "nvGraphicFramePr/" + P + "nvPr/" + P + "ph"):
        ph = el.find(path)
        if ph is not None:
            return ph
    return None


def _cnvpr(el):
    for path in (P + "nvSpPr/" + P + "cNvPr", P + "nvPicPr/" + P + "cNvPr", P + "nvGraphicFramePr/" + P + "cNvPr",
                 P + "nvGrpSpPr/" + P + "cNvPr", P + "nvCxnSpPr/" + P + "cNvPr"):
        c = el.find(path)
        if c is not None:
            return c
    return None


def ph_type(ph):
    return ph.get("type", "obj")


def _lst_sizes(lst):
    out = {}
    if lst is None:
        return out
    for lvl in range(1, 10):
        e = lst.find(A + "lvl%dpPr" % lvl)
        if e is not None:
            d = e.find(A + "defRPr")
            if d is not None and d.get("sz"):
                out[lvl] = int(d.get("sz")) / 100.0
    return out


def theme_info(pkg, theme_part):
    root = pkg.xml(theme_part)
    info = dict(part=theme_part, name=root.get("name"), fonts={}, colors={})
    fs = root.find(A + "themeElements/" + A + "fontScheme")
    if fs is not None:
        for which in ("majorFont", "minorFont"):
            f = fs.find(A + which)
            if f is not None:
                for tag in ("latin", "ea", "cs"):
                    e = f.find(A + tag)
                    info["fonts"]["%s_%s" % (which[:5], tag)] = e.get("typeface") if e is not None else None
    cs = root.find(A + "themeElements/" + A + "clrScheme")
    if cs is not None:
        info["color_scheme"] = cs.get("name")
        for c in cs:
            name = c.tag.split("}")[-1]
            v = None
            for sub in c:
                v = sub.get("val") if sub.tag.endswith("srgbClr") else sub.get("lastClr") or sub.get("val")
            info["colors"][name] = v
    return info


PH_MAP_TO_MASTER = {"obj": "body", "body": "body", "subTitle": "body", "ctrTitle": "title", "title": "title",
                    "dt": "dt", "ftr": "ftr", "sldNum": "sldNum", "pic": "body", "tbl": "body", "chart": "body",
                    "clipArt": "body", "dgm": "body", "media": "body", "sldImg": "sldImg"}


def _placeholders(root):
    out = []
    tree = root.find(P + "cSld/" + P + "spTree")
    if tree is None:
        return out
    for el in tree:
        ph = _ph(el)
        if ph is None:
            continue
        c = _cnvpr(el)
        tx = el.find(P + "txBody")
        out.append(dict(type=ph_type(ph), idx=int(ph.get("idx", 0)), attrs=dict(ph.attrib),
                        name=c.get("name") if c is not None else "", box=_box(el),
                        lst_sizes=_lst_sizes(tx.find(A + "lstStyle")) if tx is not None else {},
                        el=el))
    return out


def classify_layout(lay):
    t = lay.get("ltype") or ""
    phs = [p for p in lay["placeholders"] if p["type"] not in ("dt", "ftr", "sldNum")]
    has_title = any(p["type"] in ("title", "ctrTitle") for p in phs)
    bodies = [p for p in phs if p["type"] in ("obj", "body", "subTitle", "pic", "tbl", "chart")]
    name = (lay.get("name") or "").lower()
    kinds = set()
    if t == "title" or any(p["type"] == "ctrTitle" for p in phs) or "title slide" in name or "标题幻灯片" in name:
        kinds.add("title")
    if t == "secHead" or "section" in name or "节标题" in name:
        kinds.add("section")
    vertical = t in ("vertTx", "vertTitleAndTx", "vertTitleAndTxOverChart") or "vertical" in name
    textual = [b for b in bodies if b["type"] in ("obj", "body")]
    if not vertical and (t in ("twoObj", "twoTxTwoObj", "twoColTx") or (has_title and len(textual) == 2 and t not in ("picTx",))):
        kinds.add("two")
    if not vertical and (t in ("obj", "tx") or (has_title and len(bodies) == 1 and "title" not in kinds and "section" not in kinds)):
        kinds.add("content")
    if vertical:
        kinds.add("vertical")
    if t == "titleOnly" or (has_title and not bodies):
        kinds.add("title_only")
    if t == "blank" or not phs:
        kinds.add("blank")
    return sorted(kinds)


def catalog(pkg):
    """structure of the presentation: size, masters, layouts (with inherited placeholder boxes), theme, styles"""
    pres = "ppt/presentation.xml"
    root = pkg.xml(pres)
    sz = root.find(P + "sldSz")
    cat = dict(slide_w=int(sz.get("cx")) if sz is not None else SLIDE_W,
               slide_h=int(sz.get("cy")) if sz is not None else SLIDE_H, masters=[], layouts=[])
    prels = {r["Id"]: r for r in pkg.rels(pres)}
    mids = root.find(P + "sldMasterIdLst")
    master_parts = []
    if mids is not None:
        for m in mids:
            rid = m.get(R + "id")
            if rid in prels:
                master_parts.append(pkg.resolve(pres, prels[rid]["Target"]))
    for mi, mp in enumerate(master_parts):
        mroot = pkg.xml(mp)
        mphs = _placeholders(mroot)
        mrels = {r["Id"]: r for r in pkg.rels(mp)}
        theme = pkg.related(mp, RT_THEME)
        tx = mroot.find(P + "txStyles")
        styles = {}
        if tx is not None:
            for k, tag in (("title", "titleStyle"), ("body", "bodyStyle"), ("other", "otherStyle")):
                styles[k] = _lst_sizes(tx.find(P + tag))
        minfo = dict(index=mi, part=mp, theme=theme_info(pkg, theme[0]) if theme else {}, styles=styles,
                     placeholders=[{k: v for k, v in p.items() if k != "el"} for p in mphs], layouts=[])
        lids = mroot.find(P + "sldLayoutIdLst")
        lparts = []
        if lids is not None:
            for l in lids:
                rid = l.get(R + "id")
                if rid in mrels:
                    lparts.append(pkg.resolve(mp, mrels[rid]["Target"]))
        for li, lp in enumerate(lparts):
            lroot = pkg.xml(lp)
            csld = lroot.find(P + "cSld")
            lay = dict(part=lp, master=mi, index=li, gindex=len(cat["layouts"]),
                       name=csld.get("name") if csld is not None else "", ltype=lroot.get("type"), placeholders=[])
            for p in _placeholders(lroot):
                q = {k: v for k, v in p.items() if k != "el"}
                if q["box"] is not None and (q["box"][2] <= 0 or q["box"][3] <= 0):
                    q["degenerate_box"] = list(q["box"])  # zero width/height in the layout: a template defect
                    q["box"], q["fixed_box"] = None, True
                if q["box"] is None:
                    mt = PH_MAP_TO_MASTER.get(q["type"], "body")
                    for mpp in mphs:
                        if mpp["type"] == mt and mpp["box"] and mpp["box"][2] > 0 and mpp["box"][3] > 0:
                            q["box"] = mpp["box"]
                            q["inherited"] = True
                            break
                if q.get("fixed_box") and q["box"] is None:
                    q["box"] = [int(0.05 * cat["slide_w"]), int(0.15 * cat["slide_h"]), int(0.9 * cat["slide_w"]), int(0.7 * cat["slide_h"])]
                lay["placeholders"].append(q)
            lay["kinds"] = classify_layout(lay)
            minfo["layouts"].append(lay["gindex"])
            cat["layouts"].append(lay)
        cat["masters"].append(minfo)
    nm = root.find(P + "notesMasterIdLst")
    cat["notes_master"] = None
    if nm is not None and len(nm):
        rid = nm[0].get(R + "id")
        if rid in prels:
            cat["notes_master"] = pkg.resolve(pres, prels[rid]["Target"])
    cat["slides"] = slide_list(pkg)
    return cat


def slide_list(pkg):
    pres = "ppt/presentation.xml"
    root = pkg.xml(pres)
    prels = {r["Id"]: r for r in pkg.rels(pres)}
    out = []
    lst = root.find(P + "sldIdLst")
    if lst is None:
        return out
    for i, s in enumerate(lst, 1):
        rid = s.get(R + "id")
        if rid in prels:
            out.append(dict(index=i, sld_id=int(s.get("id")), rid=rid, part=pkg.resolve(pres, prels[rid]["Target"])))
    return out


def pick_layout(cat, want, master=0):
    """want in title|content|two|section|title_only|blank -> (layout dict, how)."""
    chains = {"title": ["title", "section", "content", "title_only"],
              "content": ["content", "two", "title_only"],
              "two": ["two", "content", "title_only"],
              "section": ["section", "title", "title_only", "content"],
              "title_only": ["title_only", "content", "blank"],
              "blank": ["blank", "title_only", "content"]}
    lays = [l for l in cat["layouts"] if l["master"] == master] or cat["layouts"]
    for k in chains.get(want, [want]):
        for l in lays:
            if k in l["kinds"]:
                return l, ("exact" if k == want else "fallback:%s->%s" % (want, k))
    return (lays[0] if lays else None), "fallback:first-layout"


def find_layout(cat, ref):
    """layout by name (case-insensitive), global index (int) or part name"""
    if ref is None:
        return None
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        i = int(ref)
        return cat["layouts"][i] if 0 <= i < len(cat["layouts"]) else None
    for l in cat["layouts"]:
        if (l["name"] or "").lower() == str(ref).lower() or l["part"] == ref:
            return l
    for l in cat["layouts"]:
        if str(ref).lower() in (l["name"] or "").lower():
            return l
    return None


def content_area(cat, master=0):
    """the template's text body box ('type area') from the content layout, else margins"""
    lay, _ = pick_layout(cat, "content", master)
    if lay:
        for p in lay["placeholders"]:
            if p["type"] in ("obj", "body") and p["box"]:
                return list(p["box"])
    W, H = cat["slide_w"], cat["slide_h"]
    return [int(W * 0.04), int(H * 0.2), int(W * 0.92), int(H * 0.7)]


# ----------------------------------------------------------------------------- inventory of an existing deck
def _paras(txbody):
    out = []
    if txbody is None:
        return out
    for p in txbody.findall(A + "p"):
        ppr = p.find(A + "pPr")
        lvl = int(ppr.get("lvl", 0)) if ppr is not None else 0
        runs, text = [], ""
        for ch in p:
            tag = ch.tag.split("}")[-1]
            if tag in ("r", "fld"):
                t = ch.find(A + "t")
                rpr = ch.find(A + "rPr")
                s = t.text if (t is not None and t.text) else ""
                run_ = dict(text=s, field=(tag == "fld"))
                if rpr is not None:
                    if rpr.get("sz"):
                        run_["sz"] = int(rpr.get("sz")) / 100.0
                    if rpr.get("b") is not None:
                        run_["b"] = rpr.get("b") in ("1", "true")
                    lat, ea = rpr.find(A + "latin"), rpr.find(A + "ea")
                    if lat is not None:
                        run_["latin"] = lat.get("typeface")
                    if ea is not None:
                        run_["ea"] = ea.get("typeface")
                    sf = rpr.find(A + "solidFill")
                    if sf is not None and len(sf):
                        c = sf[0]
                        run_["color"] = c.get("val") if c.tag.endswith("srgbClr") else "scheme:" + str(c.get("val"))
                runs.append(run_)
                text += s
            elif tag == "br":
                text += "\n"
        out.append(dict(level=lvl, text=text, runs=runs))
    return out


def _walk_shapes(tree, off=(0, 0), scale=(1.0, 1.0), chOff=(0, 0)):
    for el in tree:
        tag = el.tag.split("}")[-1]
        if tag in ("nvGrpSpPr", "grpSpPr", "extLst"):
            continue
        if tag == "AlternateContent":
            choice = el.find("{%s}Fallback" % EXTRA_NS["mc"])
            if choice is None:
                choice = el.find("{%s}Choice" % EXTRA_NS["mc"])
            if choice is not None:
                for sub in _walk_shapes(choice, off, scale, chOff):
                    yield sub
            continue
        b = _box(el)
        if b:
            b = [int(off[0] + (b[0] - chOff[0]) * scale[0]), int(off[1] + (b[1] - chOff[1]) * scale[1]),
                 int(b[2] * scale[0]), int(b[3] * scale[1])]
        yield el, tag, b
        if tag == "grpSp":
            x = el.find(P + "grpSpPr/" + A + "xfrm")
            if x is not None and x.find(A + "chExt") is not None and b:
                ce, co = x.find(A + "chExt"), x.find(A + "chOff")
                sx = b[2] / max(1, int(ce.get("cx", 1)))
                sy = b[3] / max(1, int(ce.get("cy", 1)))
                for sub in _walk_shapes(el, (b[0], b[1]), (sx, sy), (int(co.get("x", 0)), int(co.get("y", 0)))):
                    yield sub


def inventory(pkg, cat=None):
    cat = cat or catalog(pkg)
    lay_by_part = {l["part"]: l for l in cat["layouts"]}
    slides = []
    for s in cat["slides"]:
        root = pkg.xml(s["part"])
        lp = (pkg.related(s["part"], RT_LAYOUT) or [None])[0]
        lay = lay_by_part.get(lp)
        master = cat["masters"][lay["master"]] if lay else (cat["masters"][0] if cat["masters"] else {})
        styles = master.get("styles", {})
        shapes = []
        tree = root.find(P + "cSld/" + P + "spTree")
        for el, tag, box in (_walk_shapes(tree) if tree is not None else []):
            c = _cnvpr(el)
            ph = _ph(el)
            kind = {"sp": "shape", "pic": "picture", "graphicFrame": "graphic", "grpSp": "group",
                    "cxnSp": "connector"}.get(tag, tag)
            if tag == "sp" and el.find(P + "nvSpPr/" + P + "cNvSpPr") is not None and \
                    el.find(P + "nvSpPr/" + P + "cNvSpPr").get("txBox") == "1":
                kind = "textbox"
            if tag == "graphicFrame":
                gd = el.find(A + "graphic/" + A + "graphicData")
                uri = gd.get("uri", "") if gd is not None else ""
                kind = "table" if uri.endswith("/table") else ("chart" if "chart" in uri else "graphic")
            sh = dict(id=int(c.get("id", 0)) if c is not None else 0, name=c.get("name", "") if c is not None else "",
                      kind=kind, box=box, box_inherited=False)
            if c is not None and c.get("descr"):
                sh["descr"] = c.get("descr")
            lvl_sizes = {}
            if ph is not None:
                sh["kind"] = "placeholder"
                sh["ph_type"], sh["ph_idx"] = ph_type(ph), int(ph.get("idx", 0))
                lph = None
                if lay:
                    for q in lay["placeholders"]:
                        if (q["idx"] == sh["ph_idx"] and sh["ph_idx"] != 0) or (sh["ph_idx"] == 0 and PH_MAP_TO_MASTER.get(q["type"]) == PH_MAP_TO_MASTER.get(sh["ph_type"])):
                            lph = q
                            break
                if box is None and lph:
                    sh["box"], sh["box_inherited"] = lph["box"], True
                if sh["box"] is None:
                    mt = PH_MAP_TO_MASTER.get(sh["ph_type"], "body")
                    for q in master.get("placeholders", []):
                        if q["type"] == mt and q["box"]:
                            sh["box"], sh["box_inherited"] = q["box"], True
                            break
                base = styles.get("title" if PH_MAP_TO_MASTER.get(sh["ph_type"]) == "title" else
                                  ("body" if PH_MAP_TO_MASTER.get(sh["ph_type"]) == "body" else "other"), {})
                lvl_sizes = dict(base)
                for q in master.get("placeholders", []):
                    if q["type"] == PH_MAP_TO_MASTER.get(sh["ph_type"], "body") or q["type"] == sh["ph_type"]:
                        lvl_sizes.update(q.get("lst_sizes") or {})
                        break
                if lph:
                    lvl_sizes.update(lph.get("lst_sizes", {}))
                if sh["ph_type"] in ("dt", "ftr", "sldNum"):
                    sh["aux"] = True
            else:
                lvl_sizes = {1: 18.0}
            tx = el.find(P + "txBody")
            if tag == "graphicFrame" and kind == "table":
                cells = []
                for tr in el.iter(A + "tr"):
                    row = []
                    for tc in tr.findall(A + "tc"):
                        row.append("\n".join(p["text"] for p in _paras(tc.find(A + "txBody"))))
                    cells.append(row)
                sh["table"] = cells
                sh["text"] = "\n".join(" | ".join(r) for r in cells)
                sizes = [int(r.get("sz")) / 100.0 for r in el.iter(A + "rPr") if r.get("sz")]
                sh["min_font_pt"] = min(sizes) if sizes else None
            if tx is not None:
                own = _lst_sizes(tx.find(A + "lstStyle"))
                lvl_sizes.update(own)
                paras = _paras(tx)
                for p in paras:
                    szs = [r["sz"] for r in p["runs"] if r.get("sz")]
                    p["size"] = min(szs) if szs else lvl_sizes.get(p["level"] + 1, lvl_sizes.get(1, 18.0))
                bp = tx.find(A + "bodyPr")
                sh["autofit"] = None
                sh["anchor"] = bp.get("anchor") if bp is not None else None
                if bp is not None:
                    for k in ("normAutofit", "spAutoFit", "noAutofit"):
                        if bp.find(A + k) is not None:
                            sh["autofit"] = k
                sh["paras"] = paras
                sh["text"] = "\n".join(p["text"] for p in paras)
                sizes = [p["size"] for p in paras if p["text"].strip()]
                sh["min_font_pt"] = min(sizes) if sizes else None
            if sh["kind"] == "chart":
                for x in el.iter():
                    rid = x.get(R + "id")
                    if x.tag.endswith("}chart") and rid:
                        rr = {r["Id"]: r for r in pkg.rels(s["part"])}
                        if rid in rr:
                            sh["chart_part"] = pkg.resolve(s["part"], rr[rid]["Target"])
                        break
            if tag == "pic":
                blip = el.find(P + "blipFill/" + A + "blip")
                if blip is not None:
                    rid = blip.get(R + "embed")
                    rr = {r["Id"]: r for r in pkg.rels(s["part"])}
                    if rid in rr:
                        sh["image_part"] = pkg.resolve(s["part"], rr[rid]["Target"])
                src = el.find(P + "blipFill/" + A + "srcRect")
                if src is not None:
                    sh["src_rect"] = {k: int(v) for k, v in src.attrib.items()}
            shapes.append(sh)
        notes_part = (pkg.related(s["part"], RT_NOTES) or [None])[0]
        notes = ""
        if notes_part and pkg.has(notes_part):
            nroot = pkg.xml(notes_part)
            for el in nroot.iter(P + "sp"):
                ph = _ph(el)
                if ph is not None and ph_type(ph) == "body":
                    notes = "\n".join(p["text"] for p in _paras(el.find(P + "txBody")))
        title = ""
        for sh in shapes:
            if sh.get("ph_type") in ("title", "ctrTitle") and sh.get("text"):
                title = sh["text"]
                break
        slides.append(dict(index=s["index"], sld_id=s["sld_id"], part=s["part"], layout=lay["name"] if lay else None,
                           layout_part=lp, title=title, shapes=shapes, notes=notes, notes_part=notes_part))
    return dict(slide_w=cat["slide_w"], slide_h=cat["slide_h"], n_slides=len(slides), slides=slides,
                theme=cat["masters"][0]["theme"] if cat["masters"] else {},
                layouts=[dict(index=l["gindex"], name=l["name"], kinds=l["kinds"]) for l in cat["layouts"]])


def estimate_overflow(sh):
    """(needed_pt, available_pt) for a text shape using its effective sizes, or None"""
    if not sh.get("paras") or not sh.get("box"):
        return None
    paras = [dict(text=p["text"], size=p["size"], level=p["level"]) for p in sh["paras"] if p["text"].strip()]
    if not paras:
        return None
    w_pt = sh["box"][2] / EMU_PT
    h_pt = sh["box"][3] / EMU_PT
    bullet = 0 if sh.get("ph_type") in ("title", "ctrTitle", "subTitle", "ftr", "sldNum", "dt") or sh.get("kind") == "textbox" else 22
    need = text_height_pt(paras, w_pt, bullet_pt=bullet)
    return need, h_pt


# ----------------------------------------------------------------------------- XML snippet builders
SCHEME = {"accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "tx1", "tx2", "bg1", "bg2", "dk1", "dk2",
          "lt1", "lt2", "hlink", "folHlink"}


def color_xml(c):
    if not c:
        return ""
    c = str(c).lstrip("#")
    if c in SCHEME:
        return '<a:solidFill><a:schemeClr val="%s"/></a:solidFill>' % c
    if re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        return '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % c.upper()
    return ""


MATH_MARKUP = os.environ.get("DECK_MATH_MARKUP", "1") != "0"
_SCRIPT_RE = re.compile(r"(?<=[0-9A-Za-zα-ωΑ-Ωϵ∆Δ)\]}'’*])([\^_])(\{[^{}]{1,16}\}|[-−+]?[0-9A-Za-zα-ωΑ-Ω]{1,6})")


def script_runs(text):
    """x^2, x^{l+1}, 10^-4 -> superscript; h_i, x_{ij}, ε_HOMO -> subscript. Returns [(text, baseline)] with baseline
    30000 (super), -25000 (sub) or 0. \\^ and \\_ stay literal; DECK_MATH_MARKUP=0 switches it off.
    Text that looks like a file name or path (fig1_p1.png, a/b_c) is left unchanged."""
    t = str(text)
    if not MATH_MARKUP or ("^" not in t and "_" not in t) or re.search(r"[/\\]\w+_|\w+_\w+\.\w{2,4}\b", t):
        return [(t.replace("\\^", "^").replace("\\_", "_"), 0)]
    out, pos = [], 0
    for m in _SCRIPT_RE.finditer(t):
        if m.start() > 0 and t[m.start() - 1] == "\\":
            continue
        if m.group(1) == "_" and re.search(r"[A-Za-z]{3,}$", t[:m.start()]) and re.match(r"[a-z0-9]{2,}", m.group(2)):
            continue  # snake_case identifier (CSV column "full_finetune_wer"), not a subscript
        if m.start() > pos:
            out.append((t[pos:m.start()], 0))
        body = m.group(2)
        body = body[1:-1] if body.startswith("{") else body
        out.append((body, 30000 if m.group(1) == "^" else -25000))
        pos = m.end()
    if pos < len(t):
        out.append((t[pos:], 0))
    return [(x.replace("\\^", "^").replace("\\_", "_"), b) for x, b in out if x]


def inline_runs(text, scripts=False):
    """'**x**' marks emphasis (bold + accent2). Returns [(text, emphasised)], or with scripts=True
    [(text, emphasised, baseline)] where ^/_ markup became super/subscript runs (see script_runs)."""
    parts = re.split(r"(\*\*[^*]+\*\*)", str(text))
    out = []
    for p in parts:
        if not p:
            continue
        em = p.startswith("**") and p.endswith("**") and len(p) > 4
        t = p[2:-2] if em else p
        if scripts:
            out += [(x, em, b) for x, b in script_runs(t)]
        else:
            out.append((t, em))
    return out


def rpr_xml(text, size=None, bold=None, italic=None, color=None, tag="a:rPr", baseline=0):
    lang = 'lang="zh-CN" altLang="en-US"' if has_cjk(text) else 'lang="en-US"'
    attrs = [lang]
    if size:
        attrs.append('sz="%d"' % int(round(float(size) * 100)))
    if baseline:
        attrs.append('baseline="%d"' % int(baseline))
    if bold is not None:
        attrs.append('b="%d"' % (1 if bold else 0))
    if italic:
        attrs.append('i="1"')
    attrs.append('dirty="0"')
    inner = color_xml(color)
    return "<%s %s>%s</%s>" % (tag, " ".join(attrs), inner, tag) if inner else "<%s %s/>" % (tag, " ".join(attrs))


def para_xml(p, mode="ph", emph_color="accent2"):
    """p: dict(text, level, size, bold, color, italic, align, bullet(bool|None), field)
    mode 'ph': bullets come from the placeholder style (bullet False -> buNone)
    mode 'tb': text box; bullet True draws an explicit bullet"""
    lvl = int(p.get("level", 0) or 0)
    ppr_attr, ppr_in = [], ""
    if lvl:
        ppr_attr.append('lvl="%d"' % lvl)
    if p.get("align"):
        ppr_attr.append('algn="%s"' % {"left": "l", "center": "ctr", "right": "r", "justify": "just"}.get(p["align"], p["align"]))
    bullet = p.get("bullet")
    lead = ""  # optional line spacing (pct) / space before (pt) chosen by the layout engine
    if p.get("lnspc"):
        lead += '<a:lnSpc><a:spcPct val="%d"/></a:lnSpc>' % int(float(p["lnspc"]) * 1000)
    if p.get("spc") is not None:
        lead += '<a:spcBef><a:spcPts val="%d"/></a:spcBef>' % int(round(float(p["spc"]) * 100))
    if mode == "tb" and bullet:
        mar = 228600 + lvl * 342900
        ppr_attr += ['marL="%d"' % mar, 'indent="-228600"']
        ppr_in = (lead or '<a:spcBef><a:spcPts val="600"/></a:spcBef>') + '<a:buClr><a:schemeClr val="accent1"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="%s"/>' % ("•" if lvl == 0 else "–")
    elif bullet is False:
        ppr_attr += ['marL="0"', 'indent="0"']
        ppr_in = lead + "<a:buNone/>"
    elif mode == "tb":
        ppr_in = (lead or '<a:spcBef><a:spcPts val="300"/></a:spcBef>') + '<a:buNone/>'
    else:
        ppr_in = lead
    ppr = ("<a:pPr %s>%s</a:pPr>" % (" ".join(ppr_attr), ppr_in)) if (ppr_attr or ppr_in) else ""
    runs = []
    if p.get("field") == "slidenum":
        runs.append('<a:fld id="{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}" type="slidenum">%s<a:t>%s</a:t></a:fld>'
                    % (rpr_xml("1", p.get("size"), color=p.get("color")), xt(p.get("text") or "‹#›")))
    else:
        for seg_i, line in enumerate(str(p.get("text", "")).split("\n")):
            if seg_i:
                runs.append("<a:br>%s</a:br>" % rpr_xml(line, p.get("size")))
            for t, em, base in inline_runs(line, scripts=True):
                runs.append("<a:r>%s<a:t>%s</a:t></a:r>" % (
                    rpr_xml(t, p.get("size"), True if (em or p.get("bold")) else None, p.get("italic"),
                            emph_color if em and not p.get("color") else p.get("color"), baseline=base), xt(t)))
    end = rpr_xml(p.get("text", ""), p.get("size"), tag="a:endParaRPr")
    return "<a:p>%s%s%s</a:p>" % (ppr, "".join(runs), end)


def txbody_xml(paras, mode="ph", anchor=None, autofit="none", insets=True, wrap=True, tag="p:txBody"):
    bp = []
    if wrap:
        bp.append('wrap="square"')
    if insets and mode == "tb":
        bp += ['lIns="91440"', 'tIns="45720"', 'rIns="91440"', 'bIns="45720"']
    if anchor:
        bp.append('anchor="%s"' % anchor)
    fit = {"none": "<a:noAutofit/>", "shape": "<a:spAutoFit/>", "norm": "<a:normAutofit/>", None: ""}[autofit]
    body = "".join(para_xml(p, mode) for p in paras) or '<a:p><a:endParaRPr lang="en-US" dirty="0"/></a:p>'
    if mode == "ph" and autofit is None:
        bpx = '<a:bodyPr anchor="%s"/>' % anchor if anchor else "<a:bodyPr/>"
    else:
        bpx = "<a:bodyPr %s>%s</a:bodyPr>" % (" ".join(bp), fit)
    return "<%s>%s<a:lstStyle/>%s</%s>" % (tag, bpx, body, tag)


def xfrm_xml(box, tag="a:xfrm"):
    x, y, cx, cy = [int(v) for v in box]
    return '<%s><a:off x="%d" y="%d"/><a:ext cx="%d" cy="%d"/></%s>' % (tag, x, y, cx, cy, tag)


def set_xfrm(el, box):
    """give a placeholder shape an explicit position (used when the layout's own box is degenerate)"""
    sp = el.find(P + "spPr")
    if sp is None:
        sp = el.makeelement(P + "spPr", {})
        el.insert(2 if len(el) > 2 else len(el), sp)
    for x in sp.findall(A + "xfrm"):
        sp.remove(x)
    x = sp.makeelement(A + "xfrm", {})
    x.append(x.makeelement(A + "off", {"x": str(int(box[0])), "y": str(int(box[1]))}))
    x.append(x.makeelement(A + "ext", {"cx": str(int(box[2])), "cy": str(int(box[3]))}))
    sp.insert(0, x)


def ph_sp_xml(sid, name, ph_attrs, paras, box=None, anchor=None):
    attrs = " ".join('%s="%s"' % (k, xa(v)) for k, v in ph_attrs.items())
    sppr = "<p:spPr>%s</p:spPr>" % xfrm_xml(box) if box else "<p:spPr/>"
    tb = txbody_xml(paras, "ph", anchor=anchor, autofit=None)
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph %s/></p:nvPr></p:nvSpPr>%s%s</p:sp>' % (sid, xa(name), attrs, sppr, tb))


def textbox_xml(sid, name, box, paras, anchor="t", fill=None, line=None, autofit="none"):
    geo = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    fillx = color_xml(fill) if fill else "<a:noFill/>"
    linex = '<a:ln w="9525">%s</a:ln>' % color_xml(line) if line else ""
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            '<p:spPr>%s%s%s%s</p:spPr>%s</p:sp>' % (sid, xa(name), xfrm_xml(box), geo, fillx, linex,
                                                    txbody_xml(paras, "tb", anchor=anchor, autofit=autofit)))


def rect_xml(sid, name, box, fill="accent1"):
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>%s'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s<a:ln><a:noFill/></a:ln></p:spPr></p:sp>'
            % (sid, xa(name), xfrm_xml(box), color_xml(fill)))


def deco_xml(sid, name, box, fill="accent1", prst="rect", line=None, line_w=9525, radius=None):
    """decorative shape without text (card background, accent bar, number badge base)"""
    av = '<a:avLst><a:gd name="adj" fmla="val %d"/></a:avLst>' % int(radius) if radius is not None else "<a:avLst/>"
    fillx = color_xml(fill) if fill else "<a:noFill/>"
    linex = ('<a:ln w="%d">%s</a:ln>' % (line_w, color_xml(line))) if line else "<a:ln><a:noFill/></a:ln>"
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr>%s'
            '<a:prstGeom prst="%s">%s</a:prstGeom>%s%s</p:spPr></p:sp>' % (sid, xa(name), xfrm_xml(box), prst, av, fillx, linex))


# ----------------------------------------------------------------------------- design helpers shared by build and check
TAKEAWAY_ROLES = {"results", "result", "analysis", "evaluation", "conclusion", "conclusions", "takeaway", "summary",
                  "discussion", "evidence"}
TAKEAWAY_PREFIX = re.compile(r"^\s*(?:take[- ]?aways?|take[- ]home(?: message)?|bottom line|key message|so what|"
                             r"结论|要点|启示|一句话总结|核心结论|核心信息)\s*[:：]\s*", re.I)


def _sim(a, b):
    from difflib import SequenceMatcher
    a, b = re.sub(r"\W+", "", str(a).lower()), re.sub(r"\W+", "", str(b).lower())
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def takeaway_text(sl):
    """the sentence shown in the slide's bottom takeaway bar, or None. Sources (first wins): slide.takeaway
    (string; false = never); a bullet that starts with 'Takeaway:'/'结论：'... (taken out of the list); on key slides
    (results/analysis/conclusion/discussion roles) the talk field key_message unless it repeats the title."""
    tk = sl.get("takeaway")
    if tk is False or sl.get("role") in ("title", "section") or sl.get("layout") in ("title", "section", "quote"):
        return None
    if isinstance(tk, str) and tk.strip():
        return tk.strip()
    for b in sl.get("bullets") or []:
        t = b.get("text", "") if isinstance(b, dict) else str(b)
        if TAKEAWAY_PREFIX.match(t) and not (isinstance(b, dict) and b.get("level")):
            return t.strip()
    km = sl.get("key_message")
    if isinstance(km, str) and km.strip() and str(sl.get("role", "")).lower() in TAKEAWAY_ROLES and \
            len(km) <= 160 and _sim(km, sl.get("title", "")) < 0.6:
        return km.strip()
    return None


_NUM_TOKEN = re.compile(r"(?<![\w.^_/])[-+−]?\d+(?:[.,]\d+)*(?:\s?(?:%|×|倍|x(?![A-Za-z])|pp\b|pts?\b|points?\b|"
                        r"dB\b|ms\b|s\b|h\b|hours?\b|days?\b|小时|天|GB\b|MB\b|[KMB](?![A-Za-z])|BLEU\b|FPS\b))?")
_REF_BEFORE = re.compile(r"(?:Fig(?:ure)?s?\.?|Tables?|Tab\.|Sec(?:tion)?s?\.?|§|Eqs?\.?|Equations?|Appendix|Alg\.?|p\.|pp\.|"
                         r"Ref\.?|图|表|第|公式|[A-Za-z]\(|[A-Za-z]-|\[)\s*$", re.I)


def number_tokens(text):
    """[(token, score)] for the numbers of a phrase: 3 = with unit/%, 2 = decimal, 1 = integer >= 10, 0 = small
    integer. References (Fig. 3, Table 2, SE(3), top-2, [12]) and years are skipped."""
    out = []
    t = str(text)
    for m in _NUM_TOKEN.finditer(t):
        tok = m.group(0).strip()
        if _REF_BEFORE.search(t[max(0, m.start() - 12):m.start()]):
            continue
        core = re.match(r"[-+−]?\d+(?:[.,]\d+)*", tok).group(0)
        if re.fullmatch(r"(19|20)\d\d", core):
            continue
        if tok != core:
            sc = 3
        elif "." in core:
            sc = 2
        elif len(core.lstrip("-+−")) >= 2:
            sc = 1
        else:
            sc = 0
        out.append((tok, sc))
    return out


CALLOUT_ROLES = {"results", "result", "analysis", "evaluation", "evidence", "conclusion", "conclusions", "takeaway",
                 "summary", "discussion"}
_RESULT_UNIT = re.compile(r"(%|×|倍|x|pp|pts?|points?|BLEU|dB|FPS)$")


def key_number(text, role=None):
    """the single result number of a phrase, copied verbatim with its unit (or None): exactly one number with
    % / x / points / BLEU ..., at most two numbers in all, not part of a range ('1 to 50 h'), and - when role is
    given - only on result / analysis / conclusion slides (never setup or motivation numbers)."""
    if role is not None and str(role).lower() not in CALLOUT_ROLES:
        return None
    t = str(text)
    if re.search(r"\d\s*(?:to|–|—|-|~|～|至|到)\s*\d", t):
        return None
    toks = number_tokens(t)
    sal = [tok for tok, sc in toks if sc >= 2 and _RESULT_UNIT.search(tok)]
    if len(sal) == 1 and len(toks) <= 2 and len(sal[0]) <= 9:
        return sal[0]
    return None


def src_rect_xml(crop):
    """crop = (l, t, r, b) fractions of the image cut away (display crop, the image bytes stay unchanged)"""
    if not crop or not any(crop):
        return ""
    return '<a:srcRect l="%d" t="%d" r="%d" b="%d"/>' % tuple(int(round(v * 100000)) for v in crop)


def pic_xml(sid, name, rid, box, descr="", crop=None):
    return ('<p:pic><p:nvPicPr><p:cNvPr id="%d" name="%s" descr="%s"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/>'
            '</p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="%s"/>%s<a:stretch><a:fillRect/></a:stretch>'
            '</p:blipFill><p:spPr>%s<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
            % (sid, xa(name), xa(descr), rid, src_rect_xml(crop), xfrm_xml(box)))


def table_xml(sid, name, box, rows, col_w, row_h, size_pt, header=True, highlight=(), aligns=None):
    """rows: list of list of str. col_w/row_h in EMU. highlight: list of [r,c] (0-based, r includes header)"""
    hl = {tuple(x) for x in (highlight or [])}
    grid = "".join('<a:gridCol w="%d"/>' % int(w) for w in col_w)
    border = ('<a:ln%s w="6350"><a:solidFill><a:srgbClr val="BFBFBF"/></a:solidFill></a:ln%s>')
    trs = []
    for ri, row in enumerate(rows):
        tcs = []
        for ci in range(len(col_w)):
            val = row[ci] if ci < len(row) else ""
            is_h = header and ri == 0
            em = (ri, ci) in hl
            al = "ctr" if (aligns is None and ci > 0) else ((aligns or ["l"] * len(col_w))[ci] if aligns else "l")
            ps = []
            for line in str(val).split("\n"):
                rs = "".join('<a:r>%s<a:t>%s</a:t></a:r>' % (
                    rpr_xml(t, size_pt, bold=True if (is_h or em) else None,
                            color="FFFFFF" if is_h else ("accent2" if em else None), baseline=base), xt(t))
                    for t, base in (script_runs(line) or [(line, 0)]))
                ps.append('<a:p><a:pPr algn="%s"/>%s</a:p>' % (al, rs))
            fill = '<a:solidFill><a:schemeClr val="accent1"/></a:solidFill>' if is_h else (
                '<a:solidFill><a:srgbClr val="F2F4F7"/></a:solidFill>' if ri % 2 == 0 else '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill>')
            borders = "".join(border % (s, s) for s in ("L", "R", "T", "B"))
            tcs.append('<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>%s</a:txBody><a:tcPr marL="68580" marR="68580" '
                       'marT="34290" marB="34290" anchor="ctr">%s%s</a:tcPr></a:tc>' % ("".join(ps), borders, fill))
        trs.append('<a:tr h="%d">%s</a:tr>' % (int(row_h[ri]), "".join(tcs)))
    return ('<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="%d" name="%s"/><p:cNvGraphicFramePr>'
            '<a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>%s'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>'
            '<a:tblPr firstRow="%d" bandRow="1"/><a:tblGrid>%s</a:tblGrid>%s</a:tbl></a:graphicData></a:graphic>'
            '</p:graphicFrame>' % (sid, xa(name), xfrm_xml(box, "p:xfrm"), 1 if header else 0, grid, "".join(trs)))


SPTREE_HEAD = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>'
               '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def slide_xml(shapes_xml, name=None):
    nm = ' name="%s"' % xa(name) if name else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sld %s><p:cSld%s><p:spTree>%s%s</p:spTree>'
            '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (NSDECL, nm, SPTREE_HEAD, "".join(shapes_xml)))


def notes_slide_xml(text):
    paras = [dict(text=t) for t in str(text).split("\n")] or [dict(text="")]
    body = "".join(para_xml(p, "ph") for p in paras)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notes %s><p:cSld><p:spTree>%s'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" '
            'noRot="1" noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>%s'
            '</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
            % (NSDECL, SPTREE_HEAD, body))


CLRMAP = ('bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
          'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"')


def notes_master_xml():
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:notesMaster %s><p:cSld><p:bg><p:bgRef idx="1001">'
            '<a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>%s'
            '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Slide Image Placeholder 1"/><p:cNvSpPr><a:spLocks noGrp="1" noRot="1" '
            'noChangeAspect="1"/></p:cNvSpPr><p:nvPr><p:ph type="sldImg" idx="2"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm>'
            '<a:off x="381000" y="685800"/><a:ext cx="6096000" cy="3429000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
            '</a:prstGeom><a:noFill/><a:ln w="12700"><a:solidFill><a:prstClr val="black"/></a:solidFill></a:ln></p:spPr></p:sp>'
            '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
            '<p:nvPr><p:ph type="body" sz="quarter" idx="3"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="685800" '
            'y="4400550"/><a:ext cx="5486400" cy="3600450"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '</p:spPr><p:txBody><a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0"/>'
            '<a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMap %s/>'
            '<p:notesStyle><a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" '
            'hangingPunct="1"><a:defRPr sz="1200" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
            '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr>'
            '</p:notesStyle></p:notesMaster>' % (NSDECL, SPTREE_HEAD, CLRMAP))


DEFAULT_COLORS = dict(dk1="1A1A1A", lt1="FFFFFF", dk2="1F2A44", lt2="EEF1F5", accent1="0072B2", accent2="D55E00",
                      accent3="009E73", accent4="E69F00", accent5="56B4E9", accent6="CC79A7", hlink="0563C1",
                      folHlink="954F72")


def theme_xml(fonts, colors=None, name="Academic Clean"):
    c = dict(DEFAULT_COLORS)
    c.update(colors or {})
    order = ["dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink", "folHlink"]
    cs = "".join('<a:%s><a:srgbClr val="%s"/></a:%s>' % (k, c[k], k) for k in order)
    lat, ea = xa(fonts.get("latin", "Arial")), xa(fonts.get("ea", ""))
    font = '<a:latin typeface="%s"/><a:ea typeface="%s"/><a:cs typeface=""/>' % (lat, ea)
    ph = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    ln = '<a:ln w="%d" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln>'
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:theme xmlns:a="%s" name="%s"><a:themeElements>'
            '<a:clrScheme name="%s">%s</a:clrScheme><a:fontScheme name="%s"><a:majorFont>%s</a:majorFont><a:minorFont>%s'
            '</a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst>%s%s%s</a:fillStyleLst><a:lnStyleLst>'
            '%s%s%s</a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/>'
            '</a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst>%s%s%s'
            '</a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'
            % (NS["a"], xa(name), xa(name), cs, xa(name), font, font, ph, ph, ph, ln % 9525, ln % 19050, ln % 28575, ph, ph, ph))


def _lvl(n, mar, ind, sz, char, spc, color="tx1"):
    bu = '<a:buClr><a:schemeClr val="accent1"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="%s"/>' % char if char else "<a:buNone/>"
    return ('<a:lvl%dpPr marL="%d" indent="%d" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
            '<a:lnSpc><a:spcPct val="100000"/></a:lnSpc><a:spcBef><a:spcPts val="%d"/></a:spcBef>%s<a:defRPr sz="%d" kern="1200">'
            '<a:solidFill><a:schemeClr val="%s"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/>'
            '<a:cs typeface="+mn-cs"/></a:defRPr></a:lvl%dpPr>' % (n, mar, ind, spc, bu, sz, color, n))


def _ph_sp(sid, name, ph, box=None, lst="", body="", anchor=None, fit="<a:noAutofit/>"):
    sppr = "<p:spPr>%s<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr>" % xfrm_xml(box) if box else "<p:spPr/>"
    anc = ' anchor="%s"' % anchor if anchor else ""
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr>%s</p:nvPr>'
            '</p:nvSpPr>%s<p:txBody><a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0"%s>%s'
            '</a:bodyPr><a:lstStyle>%s</a:lstStyle>%s</p:txBody></p:sp>'
            % (sid, name, ph, sppr, anc, fit, lst, body or '<a:p><a:endParaRPr lang="en-US"/></a:p>'))


def make_default_template(path, fonts=None, colors=None, size=(SLIDE_W, SLIDE_H), footer_text=""):
    """Write a clean academic 16:9 template (.pptx, no slides): master + 6 layouts + theme + notes master."""
    fonts = fonts or {"latin": "Arial", "ea": "Microsoft YaHei"}
    W, H = size
    I = EMU_IN
    mx = int(0.55 * I)
    cw = W - 2 * mx
    title_box = [mx, int(0.22 * I), cw, int(1.1 * I)]
    body_box = [mx, int(1.52 * I), cw, H - int(1.52 * I) - int(0.8 * I)]
    ftr_y = H - int(0.55 * I)
    gray = '<a:solidFill><a:srgbClr val="7F7F7F"/></a:solidFill>'
    small = '<a:lvl1pPr><a:defRPr sz="1100">%s</a:defRPr></a:lvl1pPr>' % gray
    m_sp = [
        _ph_sp(2, "Title Placeholder 1", '<p:ph type="title"/>', title_box, anchor="b",
               body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Click to edit title</a:t></a:r></a:p>'),
        _ph_sp(3, "Text Placeholder 2", '<p:ph type="body" idx="1"/>', body_box,
               body='<a:p><a:pPr lvl="0"/><a:r><a:rPr lang="en-US"/><a:t>First level</a:t></a:r></a:p>'
                    '<a:p><a:pPr lvl="1"/><a:r><a:rPr lang="en-US"/><a:t>Second level</a:t></a:r></a:p>'),
        _ph_sp(4, "Date Placeholder 3", '<p:ph type="dt" sz="half" idx="2"/>', [mx, ftr_y, int(2.2 * I), int(0.35 * I)],
               lst=small, anchor="ctr"),
        _ph_sp(5, "Footer Placeholder 4", '<p:ph type="ftr" sz="quarter" idx="3"/>',
               [mx, ftr_y, int(8.5 * I), int(0.35 * I)], lst=small, anchor="ctr"),
        _ph_sp(6, "Slide Number Placeholder 5", '<p:ph type="sldNum" sz="quarter" idx="4"/>',
               [W - mx - int(1.2 * I), ftr_y, int(1.2 * I), int(0.35 * I)],
               lst='<a:lvl1pPr algn="r"><a:defRPr sz="1100">%s</a:defRPr></a:lvl1pPr>' % gray, anchor="ctr",
               body='<a:p><a:fld id="{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}" type="slidenum"><a:rPr lang="en-US"/>'
                    '<a:t>‹#›</a:t></a:fld><a:endParaRPr lang="en-US"/></a:p>'),
        rect_xml(7, "Title Rule", [mx, int(1.36 * I), cw, int(0.012 * I)], "D5DBE3"),
        rect_xml(8, "Title Rule Accent", [mx, int(1.343 * I), int(1.1 * I), int(0.045 * I)], "accent1"),
        rect_xml(9, "Footer Rule", [mx, ftr_y - int(0.05 * I), cw, int(0.01 * I)], "D5DBE3"),
    ]
    body_style = (_lvl(1, 285750, -285750, 2200, "•", 900) + _lvl(2, 685800, -228600, 1900, "–", 400) +
                  _lvl(3, 1028700, -228600, 1700, "•", 300) +
                  "".join(_lvl(n, 1028700 + (n - 3) * 342900, -228600, 1600, "•", 300) for n in range(4, 10)))
    title_style = ('<a:lvl1pPr algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
                   '<a:lnSpc><a:spcPct val="90000"/></a:lnSpc><a:spcBef><a:spcPct val="0"/></a:spcBef><a:buNone/>'
                   '<a:defRPr sz="2800" b="1" kern="1200"><a:solidFill><a:schemeClr val="tx2"/></a:solidFill>'
                   '<a:latin typeface="+mj-lt"/><a:ea typeface="+mj-ea"/><a:cs typeface="+mj-cs"/></a:defRPr></a:lvl1pPr>')
    other_style = ('<a:defPPr><a:defRPr lang="en-US"/></a:defPPr><a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" '
                   'eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="1800" kern="1200"><a:solidFill>'
                   '<a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/>'
                   '<a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr>')
    layouts = []
    ftr_phs = ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Date Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
               '<p:nvPr><p:ph type="dt" sz="half" idx="10"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>'
               '<a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>'
               '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Footer Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr>'
               '<p:nvPr><p:ph type="ftr" sz="quarter" idx="11"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/>'
               '<a:lstStyle/><a:p><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>'
               '<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Slide Number Placeholder"/><p:cNvSpPr><a:spLocks noGrp="1"/>'
               '</p:cNvSpPr><p:nvPr><p:ph type="sldNum" sz="quarter" idx="12"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody>'
               '<a:bodyPr/><a:lstStyle/><a:p><a:fld id="{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}" type="slidenum">'
               '<a:rPr lang="en-US"/><a:t>‹#›</a:t></a:fld><a:endParaRPr lang="en-US"/></a:p></p:txBody></p:sp>')

    def lay(ltype, name, sps):
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldLayout %s type="%s" preserve="1">'
                '<p:cSld name="%s"><p:spTree>%s%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>'
                '</p:sldLayout>' % (NSDECL, ltype, xa(name), SPTREE_HEAD, "".join(sps)))

    def tph(sid=2):
        return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="Title"/><p:cNvSpPr><a:spLocks noGrp="1"/></p:cNvSpPr><p:nvPr>'
                '<p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p>'
                '<a:r><a:rPr lang="en-US"/><a:t>Click to edit title</a:t></a:r></a:p></p:txBody></p:sp>' % sid)

    def bph(sid, idx, box=None, lst="", name="Content Placeholder"):
        return _ph_sp(sid, name, '<p:ph idx="%d"/>' % idx, box, lst=lst, fit="",
                      body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Click to add text</a:t></a:r></a:p>').replace(
            '<a:bodyPr vert="horz" lIns="91440" tIns="45720" rIns="91440" bIns="45720" rtlCol="0"></a:bodyPr>', "<a:bodyPr/>")

    sub_lst = ('<a:lvl1pPr marL="0" indent="0"><a:buNone/><a:defRPr sz="2000"><a:solidFill><a:srgbClr val="404040"/>'
               '</a:solidFill></a:defRPr></a:lvl1pPr>')
    band_h = int(0.6 * H)
    white_lst = ('<a:lvl1pPr algn="l"><a:lnSpc><a:spcPct val="95000"/></a:lnSpc><a:defRPr sz="4000" b="1"><a:solidFill>'
                 '<a:schemeClr val="bg1"/></a:solidFill></a:defRPr></a:lvl1pPr>')
    layouts.append(lay("title", "Title Slide", [
        rect_xml(20, "Title Band", [0, 0, W, band_h], "tx2"),
        rect_xml(21, "Title Band Accent", [0, band_h, W, int(0.07 * I)], "accent1"),
        rect_xml(22, "Title Mark", [int(0.8 * I), int(0.75 * I), int(0.9 * I), int(0.07 * I)], "accent1"),
        _ph_sp(2, "Title 1", '<p:ph type="ctrTitle"/>', [int(0.8 * I), int(1.0 * I), W - int(1.6 * I), band_h - int(1.35 * I)],
               lst=white_lst, anchor="b",
               body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Presentation title</a:t></a:r></a:p>'),
        _ph_sp(3, "Subtitle 2", '<p:ph type="subTitle" idx="1"/>', [int(0.8 * I), band_h + int(0.4 * I), W - int(1.6 * I),
                                                                   H - band_h - int(1.0 * I)],
               lst=sub_lst, body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Authors · venue · date</a:t></a:r></a:p>'),
        ftr_phs % (4, 5, 6)]))
    layouts.append(lay("obj", "Title and Content", [tph(), bph(3, 1), ftr_phs % (4, 5, 6)]))
    layouts.append(lay("secHead", "Section Header", [
        rect_xml(20, "Section Background", [0, 0, W, H], "lt2"),
        rect_xml(21, "Section Bar", [0, 0, int(0.3 * I), H], "accent1"),
        _ph_sp(2, "Title 1", '<p:ph type="title"/>', [int(1.2 * I), int(2.55 * I), W - int(2.4 * I), int(1.6 * I)],
               lst='<a:lvl1pPr><a:defRPr sz="4000"/></a:lvl1pPr>', anchor="b",
               body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Section title</a:t></a:r></a:p>'),
        _ph_sp(3, "Text 2", '<p:ph type="body" idx="1"/>', [int(1.2 * I), int(4.35 * I), W - int(2.4 * I), int(1.3 * I)],
               lst=sub_lst, body='<a:p><a:r><a:rPr lang="en-US"/><a:t>Section subtitle</a:t></a:r></a:p>'),
        ftr_phs % (4, 5, 6)]))
    half = (cw - int(0.3 * I)) // 2
    layouts.append(lay("twoObj", "Two Content", [
        tph(), bph(3, 1, [mx, body_box[1], half, body_box[3]], name="Content Placeholder Left"),
        bph(4, 2, [mx + half + int(0.3 * I), body_box[1], half, body_box[3]], name="Content Placeholder Right"),
        ftr_phs % (5, 6, 7)]))
    layouts.append(lay("titleOnly", "Title Only", [tph(), ftr_phs % (3, 4, 5)]))
    layouts.append(lay("blank", "Blank", [ftr_phs % (2, 3, 4)]))

    master = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:sldMaster %s><p:cSld><p:bg><p:bgRef idx="1001">'
              '<a:schemeClr val="bg1"/></p:bgRef></p:bg><p:spTree>%s%s</p:spTree></p:cSld><p:clrMap %s/><p:sldLayoutIdLst>%s'
              '</p:sldLayoutIdLst><p:txStyles><p:titleStyle>%s</p:titleStyle><p:bodyStyle>%s</p:bodyStyle><p:otherStyle>%s'
              '</p:otherStyle></p:txStyles></p:sldMaster>'
              % (NSDECL, SPTREE_HEAD, "".join(m_sp), CLRMAP,
                 "".join('<p:sldLayoutId id="%d" r:id="rId%d"/>' % (2147483649 + i, i + 1) for i in range(len(layouts))),
                 title_style, body_style, other_style))
    pres = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentation %s saveSubsetFonts="1">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:notesMasterIdLst>'
            '<p:notesMasterId r:id="rId2"/></p:notesMasterIdLst><p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" '
            'cy="9144000"/><p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr><a:lvl1pPr marL="0" algn="l" '
            'defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1"><a:defRPr sz="1800" kern="1200">'
            '<a:solidFill><a:schemeClr val="tx1"/></a:solidFill><a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/>'
            '<a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr></p:defaultTextStyle></p:presentation>' % (NSDECL, W, H))
    pkg = Package()
    pkg.write("ppt/presentation.xml", pres)
    pkg.write("ppt/slideMasters/slideMaster1.xml", master)
    pkg.write("ppt/theme/theme1.xml", theme_xml(fonts, colors))
    pkg.write("ppt/theme/theme2.xml", theme_xml(fonts, colors, "Notes Theme"))
    pkg.write("ppt/notesMasters/notesMaster1.xml", notes_master_xml())
    pkg.write("ppt/presProps.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:presentationPr %s/>' % NSDECL)
    pkg.write("ppt/viewProps.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<p:viewPr %s><p:normalViewPr>'
              '<p:restoredLeft sz="15620"/><p:restoredTop sz="94660"/></p:normalViewPr><p:gridSpacing cx="76200" cy="76200"/>'
              '</p:viewPr>' % NSDECL)
    pkg.write("ppt/tableStyles.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<a:tblStyleLst xmlns:a="%s" '
              'def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>' % NS["a"])
    pkg.write("docProps/app.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Properties xmlns="http://schemas.'
              'openxmlformats.org/officeDocument/2006/extended-properties"><Application>pptkit</Application></Properties>')
    pkg.write("docProps/core.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<cp:coreProperties xmlns:cp="'
              'http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
              '<dc:title>Academic Clean template</dc:title><dc:creator>pptkit</dc:creator></cp:coreProperties>')
    for i, lx in enumerate(layouts, 1):
        pkg.write("ppt/slideLayouts/slideLayout%d.xml" % i, lx)
        pkg.write_rels("ppt/slideLayouts/slideLayout%d.xml" % i,
                       [dict(Id="rId1", Type=RT_MASTER, Target="../slideMasters/slideMaster1.xml")])
    pkg.write_rels("ppt/slideMasters/slideMaster1.xml",
                   [dict(Id="rId%d" % i, Type=RT_LAYOUT, Target="../slideLayouts/slideLayout%d.xml" % i) for i in range(1, len(layouts) + 1)]
                   + [dict(Id="rId%d" % (len(layouts) + 1), Type=RT_THEME, Target="../theme/theme1.xml")])
    pkg.write_rels("ppt/notesMasters/notesMaster1.xml", [dict(Id="rId1", Type=RT_THEME, Target="../theme/theme2.xml")])
    pkg.write_rels("ppt/presentation.xml", [
        dict(Id="rId1", Type=RT_MASTER, Target="slideMasters/slideMaster1.xml"),
        dict(Id="rId2", Type=RT_NOTESMASTER, Target="notesMasters/notesMaster1.xml"),
        dict(Id="rId3", Type=RT + "presProps", Target="presProps.xml"),
        dict(Id="rId4", Type=RT + "viewProps", Target="viewProps.xml"),
        dict(Id="rId5", Type=RT_THEME, Target="theme/theme1.xml"),
        dict(Id="rId6", Type=RT + "tableStyles", Target="tableStyles.xml")])
    pkg.write("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.'
              'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
              'officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/><Relationship Id="rId2" '
              'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
              'Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/'
              '2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>')
    d = {"rels": "application/vnd.openxmlformats-package.relationships+xml", "xml": "application/xml",
         "png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg"}
    o = {"/ppt/presentation.xml": CT_PRES, "/ppt/slideMasters/slideMaster1.xml": CT_MASTER,
         "/ppt/theme/theme1.xml": CT_THEME, "/ppt/theme/theme2.xml": CT_THEME,
         "/ppt/notesMasters/notesMaster1.xml": CT_NOTESMASTER,
         "/ppt/presProps.xml": CT + "presentationml.presProps+xml", "/ppt/viewProps.xml": CT + "presentationml.viewProps+xml",
         "/ppt/tableStyles.xml": CT + "presentationml.tableStyles+xml",
         "/docProps/core.xml": "application/vnd.openxmlformats-package.core-properties+xml",
         "/docProps/app.xml": CT + "extended-properties+xml"}
    for i in range(1, len(layouts) + 1):
        o["/ppt/slideLayouts/slideLayout%d.xml" % i] = CT_LAYOUT
    pkg.write_content_types(d, o)
    pkg.save(path)
    return path


# ----------------------------------------------------------------------------- package surgery used by builders/editors
def ensure_notes_master(pkg):
    pres = "ppt/presentation.xml"
    nm = [r for r in pkg.rels(pres) if r["Type"] == RT_NOTESMASTER]
    if nm:
        return pkg.resolve(pres, nm[0]["Target"])
    part = pkg.unique_name("ppt/notesMasters/notesMaster%d.xml")
    theme = pkg.unique_name("ppt/theme/theme%d.xml")
    # reuse the first master's theme fonts/colours for the notes theme
    pkg.write(theme, theme_xml({"latin": "Arial", "ea": ""}, None, "Notes Theme"))
    pkg.set_override(theme, CT_THEME)
    pkg.write(part, notes_master_xml())
    pkg.write_rels(part, [dict(Id="rId1", Type=RT_THEME, Target=pkg.relpath(part, theme))])
    pkg.set_override(part, CT_NOTESMASTER)
    rid = pkg.add_rel(pres, RT_NOTESMASTER, part)
    root = pkg.xml(pres)
    el = ET.Element(P + "notesMasterIdLst")
    ET.SubElement(el, P + "notesMasterId", {R + "id": rid})
    pos = list(root).index(root.find(P + "sldMasterIdLst")) + 1 if root.find(P + "sldMasterIdLst") is not None else 0
    root.insert(pos, el)
    pkg.touch(pres)
    return part


def sld_id_list(pkg):
    root = pkg.xml("ppt/presentation.xml")
    lst = root.find(P + "sldIdLst")
    if lst is None:
        lst = ET.Element(P + "sldIdLst")
        pos = 0
        for i, ch in enumerate(list(root)):
            if ch.tag in (P + "sldMasterIdLst", P + "notesMasterIdLst", P + "handoutMasterIdLst"):
                pos = i + 1
        root.insert(pos, lst)
    return root, lst


def add_slide_part(pkg, xml, layout_part, media=(), notes=None, position=None):
    """add a slide part. media: list of (rid, src_path). Returns slide part name."""
    part = pkg.unique_name("ppt/slides/slide%d.xml")
    rels = [dict(Id="rId1", Type=RT_LAYOUT, Target=pkg.relpath(part, layout_part))]
    for rid, src in media:
        ext = os.path.splitext(src)[1].lower().lstrip(".") or "png"
        mname = pkg.unique_name("ppt/media/image%d." + ext)
        with open(src, "rb") as f:
            pkg.write(mname, f.read())
        pkg.ensure_default(ext, IMG_CT.get(ext, "image/" + ext))
        rels.append(dict(Id=rid, Type=RT_IMAGE, Target=pkg.relpath(part, mname)))
    pkg.write(part, xml)
    pkg.set_override(part, CT_SLIDE)
    if notes is not None and str(notes).strip():
        nm = ensure_notes_master(pkg)
        npart = pkg.unique_name("ppt/notesSlides/notesSlide%d.xml")
        pkg.write(npart, notes_slide_xml(notes))
        pkg.write_rels(npart, [dict(Id="rId1", Type=RT_NOTESMASTER, Target=pkg.relpath(npart, nm)),
                               dict(Id="rId2", Type=RT_SLIDE, Target=pkg.relpath(npart, part))])
        pkg.set_override(npart, CT_NOTES)
        used = {r["Id"] for r in rels}
        k = len(rels) + 1
        while "rId%d" % k in used:
            k += 1
        rels.append(dict(Id="rId%d" % k, Type=RT_NOTES, Target=pkg.relpath(part, npart)))
    pkg.write_rels(part, rels)
    rid = pkg.add_rel("ppt/presentation.xml", RT_SLIDE, part)
    root, lst = sld_id_list(pkg)
    ids = [int(s.get("id")) for s in lst] or [255]
    el = ET.Element(P + "sldId", {"id": str(max(max(ids), 255) + 1), R + "id": rid})
    if position is None or position >= len(lst):
        lst.append(el)
    else:
        lst.insert(max(0, position), el)
    pkg.touch("ppt/presentation.xml")
    return part


def remove_slide(pkg, part):
    """drop a slide (and its notes slide) from the package; orphan media are removed."""
    pres = "ppt/presentation.xml"
    prels = pkg.rels(pres)
    rid = None
    for r in prels:
        if r["Type"] == RT_SLIDE and pkg.resolve(pres, r["Target"]) == part:
            rid = r["Id"]
    root, lst = sld_id_list(pkg)
    for s in list(lst):
        if s.get(R + "id") == rid:
            lst.remove(s)
    pkg.touch(pres)
    pkg.write_rels(pres, [r for r in prels if r["Id"] != rid])
    for n in pkg.related(part, RT_NOTES):
        pkg.delete(n)
        pkg.delete(pkg.rels_name(n))
        pkg.set_override(n, None)
    pkg.delete(part)
    pkg.delete(pkg.rels_name(part))
    pkg.set_override(part, None)
    drop_orphan_media(pkg)


def drop_orphan_media(pkg):
    used = set()
    for n in list(pkg.parts):
        if n.endswith(".rels"):
            src = n.replace("_rels/", "")[:-5]
            for r in pkg.rels(src):
                if r.get("TargetMode") != "External":
                    used.add(pkg.resolve(src, r["Target"]))
    for n in list(pkg.parts):
        if n.startswith("ppt/media/") and n not in used:
            pkg.delete(n)


def set_notes_text(pkg, slide_part, text):
    """create or replace the notes text of a slide; returns (before, after)"""
    nparts = pkg.related(slide_part, RT_NOTES)
    if not nparts:
        nm = ensure_notes_master(pkg)
        npart = pkg.unique_name("ppt/notesSlides/notesSlide%d.xml")
        pkg.write(npart, notes_slide_xml(text))
        pkg.write_rels(npart, [dict(Id="rId1", Type=RT_NOTESMASTER, Target=pkg.relpath(npart, nm)),
                               dict(Id="rId2", Type=RT_SLIDE, Target=pkg.relpath(npart, slide_part))])
        pkg.set_override(npart, CT_NOTES)
        pkg.add_rel(slide_part, RT_NOTES, npart)
        return "", text
    npart = nparts[0]
    root = pkg.xml(npart)
    before = ""
    for el in root.iter(P + "sp"):
        ph = _ph(el)
        if ph is not None and ph_type(ph) == "body":
            tx = el.find(P + "txBody")
            before = "\n".join(p["text"] for p in _paras(tx))
            for p in tx.findall(A + "p"):
                tx.remove(p)
            for t in str(text).split("\n"):
                tx.append(ET.fromstring('<a:p xmlns:a="%s">%s</a:p>' % (NS["a"], para_xml(dict(text=t), "ph")[5:-6])))
            pkg.touch(npart)
            return before, text
    return before, None


def frag(xml):
    """parse an XML fragment that uses a:/p:/r: prefixes"""
    return ET.fromstring('<w %s>%s</w>' % (NSDECL, xml))[0]


def max_shape_id(tree):
    ids = [int(c.get("id")) for c in tree.iter() if c.tag.endswith("}cNvPr") and str(c.get("id", "")).isdigit()]
    return max(ids) if ids else 1


# ----------------------------------------------------------------------------- spec helpers shared by build/check
def spec_visuals(sl):
    """yield (key, dict) for every visual element of a slide spec (figure/chart/table, figures[], left/right columns)"""
    for key in ("figure", "chart", "table"):
        if isinstance(sl.get(key), dict):
            yield key, sl[key]
    for f in sl.get("figures") or []:
        if isinstance(f, dict):
            yield ("chart" if f.get("y") else ("table" if (f.get("rows") or (f.get("csv") and not f.get("y"))) else "figure")), f
    for col in ("left", "right"):
        c = sl.get(col) or {}
        for key in ("figure", "chart", "table"):
            if isinstance(c.get(key), dict):
                yield key, c[key]


def resolve_spec_paths(slides, base):
    """relative image/pdf/csv paths in a spec are resolved against the spec file's folder"""
    for sl in slides:
        for _, d in spec_visuals(sl):
            for k in ("image", "pdf", "csv"):
                v = d.get(k)
                if isinstance(v, str) and v and not os.path.isabs(v):
                    cand = os.path.normpath(os.path.join(base, v))
                    if os.path.exists(cand) or not os.path.exists(v):
                        d[k] = cand
    return slides


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "template":
        f = pick_fonts()
        make_default_template(sys.argv[2], {"latin": f["latin"], "ea": f["ea"]})
        print(json.dumps(dict(template=sys.argv[2], fonts=f), ensure_ascii=False))
    elif len(sys.argv) > 2 and sys.argv[1] == "catalog":
        c = catalog(Package(sys.argv[2]))
        for l in c["layouts"]:
            print(l["gindex"], l["name"], l["ltype"], l["kinds"], [(p["type"], p["idx"]) for p in l["placeholders"]])
    else:
        print("usage: pptkit.py template out.pptx | catalog deck.pptx")


# ---------------------------------------------------------------- stdlib image sheets (no PIL / matplotlib needed)
_FONT5x7 = {  # 5x7 bitmap glyphs, rows top->bottom, 5 bits each (labels are drawn upper-case)
    "0": "0E 11 13 15 19 11 0E", "1": "04 0C 04 04 04 04 0E", "2": "0E 11 01 02 04 08 1F", "3": "1F 02 04 02 01 11 0E",
    "4": "02 06 0A 12 1F 02 02", "5": "1F 10 1E 01 01 11 0E", "6": "06 08 10 1E 11 11 0E", "7": "1F 01 02 04 08 08 08",
    "8": "0E 11 11 0E 11 11 0E", "9": "0E 11 11 0F 01 02 0C", "A": "0E 11 11 1F 11 11 11", "B": "1E 11 11 1E 11 11 1E",
    "C": "0E 11 10 10 10 11 0E", "D": "1E 11 11 11 11 11 1E", "E": "1F 10 10 1E 10 10 1F", "F": "1F 10 10 1E 10 10 10",
    "G": "0E 11 10 17 11 11 0F", "H": "11 11 11 1F 11 11 11", "I": "0E 04 04 04 04 04 0E", "J": "07 02 02 02 02 12 0C",
    "K": "11 12 14 18 14 12 11", "L": "10 10 10 10 10 10 1F", "M": "11 1B 15 15 11 11 11", "N": "11 11 19 15 13 11 11",
    "O": "0E 11 11 11 11 11 0E", "P": "1E 11 11 1E 10 10 10", "Q": "0E 11 11 11 15 12 0D", "R": "1E 11 11 1E 14 12 11",
    "S": "0F 10 10 0E 01 01 1E", "T": "1F 04 04 04 04 04 04", "U": "11 11 11 11 11 11 0E", "V": "11 11 11 11 11 0A 04",
    "W": "11 11 11 15 15 15 0A", "X": "11 11 0A 04 0A 11 11", "Y": "11 11 0A 04 04 04 04", "Z": "1F 01 02 04 08 10 1F",
    ".": "00 00 00 00 00 0C 0C", "-": "00 00 00 1F 00 00 00", " ": "00 00 00 00 00 00 00", ":": "00 0C 0C 00 0C 0C 00",
}


def read_ppm(path):
    """P6 (RGB) or P5 (gray) netpbm file as (w, h, bytearray RGB)"""
    data = open(path, "rb").read()
    parts, i = [], 0
    while len(parts) < 4:
        while data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b"#":
            i = data.index(b"\n", i) + 1
            continue
        j = i
        while not data[j:j + 1].isspace():
            j += 1
        parts.append(data[i:j])
        i = j
    i += 1
    w, h = int(parts[1]), int(parts[2])
    px = bytearray(data[i:i + w * h * (3 if parts[0] == b"P6" else 1)])
    if parts[0] == b"P5":
        px = bytearray(b for g in px for b in (g, g, g))
    return w, h, px


def write_png(path, w, h, rgb):
    import zlib
    raw = b"".join(b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
                chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    return path


class Canvas:
    def __init__(self, w, h, bg=(255, 255, 255)):
        self.w, self.h = w, h
        self.px = bytearray(bytes(bg) * (w * h))

    def paste(self, x, y, w, h, rgb):
        for r in range(min(h, self.h - y)):
            o = ((y + r) * self.w + x) * 3
            n = min(w, self.w - x) * 3
            self.px[o:o + n] = rgb[r * w * 3:r * w * 3 + n]

    def rect(self, x0, y0, x1, y1, col=(220, 30, 30), lw=2, fill=False):
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        x1, y1 = min(self.w - 1, int(x1)), min(self.h - 1, int(y1))
        c = bytes(col)
        for y in range(y0, y1 + 1):
            if fill or y - y0 < lw or y1 - y < lw:
                o = (y * self.w + x0) * 3
                self.px[o:o + (x1 - x0 + 1) * 3] = c * (x1 - x0 + 1)
            else:
                for x in list(range(x0, min(x0 + lw, x1 + 1))) + list(range(max(x1 - lw + 1, x0), x1 + 1)):
                    o = (y * self.w + x) * 3
                    self.px[o:o + 3] = c

    def text(self, x, y, s, col=(0, 0, 0), scale=2, bg=None):
        s = str(s).upper()
        if bg:
            self.rect(x - 2, y - 2, x + len(s) * 6 * scale + 1, y + 7 * scale + 1, bg, fill=True)
        for k, ch in enumerate(s):
            rows = _FONT5x7.get(ch, _FONT5x7[" "]).split()
            for ry, hx in enumerate(rows):
                bits = int(hx, 16)
                for rx in range(5):
                    if bits & (1 << (4 - rx)):
                        self.rect(x + (k * 6 + rx) * scale, y + ry * scale, x + (k * 6 + rx) * scale + scale - 1,
                                  y + ry * scale + scale - 1, col, fill=True)

    def save(self, path):
        return write_png(path, self.w, self.h, self.px)


def pdf_page_ppm(pdf, page, out_base, width=None, dpi=None):
    """render one PDF page as PPM via pdftoppm; returns (w, h, rgb) or None"""
    if not shutil.which("pdftoppm"):
        return None
    cmd = ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile"]
    cmd += (["-scale-to-x", str(width), "-scale-to-y", "-1"] if width else ["-r", str(dpi or 50)])
    rc, _, _ = run(cmd + [pdf, out_base], timeout=120)
    p = out_base + ".ppm"
    if rc != 0 or not os.path.exists(p):
        return None
    try:
        return read_ppm(p)
    finally:
        os.remove(p)


def sheet_from_pdf(pdf, pages, out_png, cols=4, width=400, labels=None, boxes=None):
    """stdlib contact sheet: PDF pages as thumbnails in a grid, optional labels and red boxes
    (boxes: {page: [(x0,y0,x1,y1 fractions, label)]}). Returns out_png or None."""
    tiles = []
    tmp = tempfile.mkdtemp(prefix="sheet_")
    try:
        for i, pg in enumerate(pages):
            t = pdf_page_ppm(pdf, pg, os.path.join(tmp, "t%d" % i), width=width)
            if t:
                tiles.append((pg, (labels or {}).get(pg, "page %d" % pg), t))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not tiles:
        return None
    tw = max(t[2][0] for t in tiles)
    th = max(t[2][1] for t in tiles)
    cols = max(1, min(cols, len(tiles)))
    rows = (len(tiles) + cols - 1) // cols
    cv = Canvas(cols * (tw + 10) + 10, rows * (th + 30) + 10)
    for k, (pg, lab, (w, h, rgb)) in enumerate(tiles):
        r, c = divmod(k, cols)
        x, y = 10 + c * (tw + 10), 10 + r * (th + 30)
        cv.text(x, y + 2, lab, scale=2)
        cv.paste(x, y + 20, w, h, rgb)
        cv.rect(x, y + 20, x + w - 1, y + 20 + h - 1, (160, 160, 160), lw=1)
        for (x0, y0, x1, y1, bl) in (boxes or {}).get(pg, []):
            cv.rect(x + x0 * w, y + 20 + y0 * h, x + x1 * w, y + 20 + y1 * h, (220, 30, 30), lw=2)
            cv.text(int(x + x0 * w) + 2, int(y + 20 + y0 * h) + 2, bl, col=(255, 255, 255), scale=1, bg=(220, 30, 30))
    return cv.save(out_png)


def chart_table(pkg, chart_part):
    """categories x series values of a native chart (cached values in the chart XML) -> rows for a CSV"""
    C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
    root = ET.fromstring(pkg.read(chart_part))
    series = []
    for ser in root.iter(C + "ser"):
        tx = ser.find(C + "tx")
        name = "".join(v.text or "" for v in tx.iter(C + "v")) if tx is not None else ""
        def pts(tag):
            el = ser.find(C + tag)
            if el is None:
                return {}
            return {int(pt.get("idx", 0)): (pt.findtext(C + "v") or "") for pt in el.iter(C + "pt")}
        series.append((name or "series %d" % (len(series) + 1), pts("cat") or pts("xVal"), pts("val") or pts("yVal")))
    if not series:
        return []
    cats = series[0][1]
    n = max([len(cats)] + [len(v) for _, _, v in series])
    rows = [["category"] + [nm for nm, _, _ in series]]
    for i in range(n):
        rows.append([cats.get(i, str(i + 1))] + [v.get(i, "") for _, _, v in series])
    kind = next((el.tag.split("}")[1] for el in root.iter() if el.tag.endswith("Chart") and el.tag != C + "chart"), "chart")
    return rows, kind
