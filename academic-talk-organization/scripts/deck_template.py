#!/usr/bin/env python3
"""deck_template.py - read a template (.pptx/.potx), adapt decks to it and report compliance.

  inspect  --template T [--out template_report.json] [--gallery DIR]
           layouts + placeholders (type/idx/box), theme fonts/colours, master text sizes, slide size;
           --gallery builds one slide per layout with labelled placeholders and renders a contact sheet
  audit    --pptx D --template T [--out compliance.json] [--min-body-pt 12]
           per-slide deviations: layout not from template, theme fonts/colours/slide size differ, hard-coded
           fonts, off-palette colours, title not in a title placeholder or moved, missing slide number,
           empty placeholders, + render checks (overflow, < min pt, off-slide, stretched pictures)
  fix      --pptx D --template T --out D2 --log fix_log.json [--rules theme,fonts,colors,title_pos,title_box,min_size,pictures,empty_ph,slide_numbers]
           rule-based, logged before/after; content text is never changed
  to_spec  --pptx D --out spec_from_deck.json [--media DIR] [--previews DIR_with_slide-NN.png]
           existing deck -> deck_build spec (exact text, levels, pictures, tables, notes); charts/SmartArt
           are cropped from the rendered previews when given, otherwise listed as not carried over
  preserve --a source.pptx --b adapted.pptx [--out preserve.json]
           content preservation: every text paragraph, number, picture (by hash) and note of A found in B
Rebuild route (true master/layout inheritance): to_spec -> deck_build --template T -> audit -> render -> preserve.
"""
import argparse, collections, hashlib, json, os, re, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pptkit as K  # noqa: E402

A, P, R = K.A, K.P, K.R


def local(t):
    return t.split("}")[-1]


def open_template(path, work):
    dst = os.path.join(work, "template_norm.pptx")
    K.normalize_template(path, dst)
    return dst


def hex_rgb(h):
    h = str(h).lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if re.fullmatch(r"[0-9A-Fa-f]{6}", h) else None


def palette(theme):
    out = {}
    for k, v in (theme.get("colors") or {}).items():
        rgb = hex_rgb(v) if v else None
        if rgb:
            out[k] = rgb
    return out


def nearest(rgb, pal):
    best, bd = None, 1e9
    for k, v in pal.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, v)) ** 0.5
        if d < bd:
            best, bd = k, d
    return best, bd


def cmd_inspect(a):
    work = tempfile.mkdtemp(prefix="tpl_")
    t = open_template(a.template, work)
    pkg = K.Package(t)
    cat = K.catalog(pkg)
    rep = dict(template=os.path.abspath(a.template), sha256=K.sha256(a.template),
               slide_size_in=[K.to_in(cat["slide_w"]), K.to_in(cat["slide_h"])], n_existing_slides=len(cat["slides"]),
               masters=[], layouts=[])
    for m in cat["masters"]:
        rep["masters"].append(dict(index=m["index"], theme_fonts=m["theme"].get("fonts"), theme_colors=m["theme"].get("colors"),
                                   text_sizes_pt=m["styles"], placeholders=[dict(type=p["type"], idx=p["idx"],
                                   box_in=[K.to_in(v) for v in p["box"]] if p["box"] else None) for p in m["placeholders"]]))
    for l in cat["layouts"]:
        rep["layouts"].append(dict(index=l["gindex"], name=l["name"], type=l["ltype"], kinds=l["kinds"], master=l["master"],
                                   placeholders=[dict(type=p["type"], idx=p["idx"], name=p["name"],
                                                      box_in=[K.to_in(v) for v in p["box"]] if p["box"] else None,
                                                      inherited=p.get("inherited", False), sizes_pt=p.get("lst_sizes") or None)
                                                 for p in l["placeholders"]]))
    need = {"title": "title", "content": "content", "two": "two_column", "section": "section", "title_only": "figure/table/chart"}
    rep["layout_mapping"] = {}
    for k, use in need.items():
        lay, how = K.pick_layout(cat, k)
        rep["layout_mapping"][use] = dict(layout=lay["name"] if lay else None, how=how)
    rep["content_area_in"] = [K.to_in(v) for v in K.content_area(cat)]
    rep["has_footer_ph"] = any(p["type"] == "ftr" for l in cat["layouts"] for p in l["placeholders"])
    rep["has_slide_number_ph"] = any(p["type"] == "sldNum" for l in cat["layouts"] for p in l["placeholders"])
    rep["fonts_status"] = {k: K.font_status(v) for k, v in (cat["masters"][0]["theme"].get("fonts") or {}).items() if v} if cat["masters"] else {}
    missing = [use for use, v in rep["layout_mapping"].items() if not v["how"] or v["how"] != "exact"]
    rep["gaps"] = ["no exact layout for %s (%s)" % (u, rep["layout_mapping"][u]["how"]) for u in missing]
    if a.gallery:
        import deck_build as DB
        import deck_render as DR
        os.makedirs(a.gallery, exist_ok=True)
        be = DB.XmlBackend(t, keep_slides=False)
        for l in be.cat["layouts"]:
            s = be.new_slide(l)
            for p in l["placeholders"]:
                lab = "%s idx=%d  [layout %d '%s']" % (p["type"], p["idx"], l["gindex"], l["name"]) if p["type"] in ("title", "ctrTitle") \
                    else "%s placeholder idx=%d" % (p["type"], p["idx"])
                if p["type"] == "sldNum":
                    be.ph_text(s, p, [dict(text="#", field="slidenum", bullet=False)], "Slide Number")
                else:
                    be.ph_text(s, p, [dict(text=lab, bullet=False)], "PH %s %d" % (p["type"], p["idx"]))
            be.notes(s, "layout %d: %s" % (l["gindex"], l["name"]))
            be.finish(s)
        gp = os.path.join(a.gallery, "template_gallery.pptx")
        be.save(gp)
        pdf, log = K.soffice_convert(gp, a.gallery, "pdf")
        if pdf:
            pngs, how = DR.pdf_to_pngs(pdf, os.path.join(a.gallery, "previews"), 60)
            rep["gallery"] = DR.contact_sheet(pngs, os.path.join(a.gallery, "template_gallery.png"), cols=3, thumb_w=360)
        else:
            rep["gallery"] = None
            rep["gallery_error"] = log
    out = a.out or "template_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    print("template: %s | %.2fx%.2f in | %d layouts | %d existing slides | footer ph: %s | slide-number ph: %s" % (
        os.path.basename(a.template), rep["slide_size_in"][0], rep["slide_size_in"][1], len(rep["layouts"]),
        rep["n_existing_slides"], rep["has_footer_ph"], rep["has_slide_number_ph"]))
    if rep["masters"]:
        print("theme fonts: %s" % json.dumps(rep["masters"][0]["theme_fonts"], ensure_ascii=False))
        print("theme colours: %s" % json.dumps(rep["masters"][0]["theme_colors"]))
        print("master text sizes (pt): %s" % json.dumps(rep["masters"][0]["text_sizes_pt"]))
    for l in rep["layouts"]:
        print("  layout %d '%s' (%s) %s: %s" % (l["index"], l["name"], l["type"], l["kinds"],
                                                ", ".join("%s/%d" % (p["type"], p["idx"]) for p in l["placeholders"])))
    print("mapping: " + json.dumps({k: v["layout"] for k, v in rep["layout_mapping"].items()}, ensure_ascii=False))
    if rep["gaps"]:
        print("gaps: " + "; ".join(rep["gaps"]))
    if rep.get("gallery"):
        print("gallery: " + rep["gallery"])
    return 0


def theme_diff(tt, dt):
    d = []
    for k, v in (tt.get("fonts") or {}).items():
        if v and (dt.get("fonts") or {}).get(k) != v:
            d.append("theme font %s: deck '%s' vs template '%s'" % (k, (dt.get("fonts") or {}).get(k), v))
    for k, v in (tt.get("colors") or {}).items():
        if v and str((dt.get("colors") or {}).get(k, "")).upper() != str(v).upper():
            d.append("theme colour %s: deck %s vs template %s" % (k, (dt.get("colors") or {}).get(k), v))
    return d


def cmd_audit(a):
    import deck_render as DR
    work = tempfile.mkdtemp(prefix="tpl_")
    t = open_template(a.template, work)
    tcat = K.catalog(K.Package(t))
    dpkg = K.Package(a.pptx)
    dcat = K.catalog(dpkg)
    issues = []
    inv, per, fonts = DR.check_deck(a.pptx, a.min_body_pt, issues)
    for x in issues:
        x["rule"] = "render"
    tth = tcat["masters"][0]["theme"] if tcat["masters"] else {}
    dth = dcat["masters"][0]["theme"] if dcat["masters"] else {}

    def add(level, rule, slide, msg, shape=None):
        issues.append(dict(level=level, rule=rule, slide=slide, shape=shape, msg=msg))
    for m in theme_diff(tth, dth):
        add("fail", "theme", None, m)
    if (tcat["slide_w"], tcat["slide_h"]) != (dcat["slide_w"], dcat["slide_h"]):
        add("fail", "slide_size", None, "slide size %.2fx%.2f in, template %.2fx%.2f in" % (
            K.to_in(dcat["slide_w"]), K.to_in(dcat["slide_h"]), K.to_in(tcat["slide_w"]), K.to_in(tcat["slide_h"])))
    tnames = {l["name"] for l in tcat["layouts"]}
    tfonts = {v for v in (tth.get("fonts") or {}).values() if v}
    pal = palette(tth)
    dlay = {l["part"]: l for l in dcat["layouts"]}
    for s in inv["slides"]:
        i = s["index"]
        lay = dlay.get(s["layout_part"])
        if s["layout"] not in tnames:
            add("fail", "layout", i, "layout '%s' is not a layout of the template" % s["layout"])
        lph = lay["placeholders"] if lay else []
        has_title_ph = any(p["type"] in ("title", "ctrTitle") for p in lph)
        sh_title = [sh for sh in s["shapes"] if sh.get("ph_type") in ("title", "ctrTitle")]
        if not sh_title:
            big_top = [sh for sh in s["shapes"] if sh["kind"] == "textbox" and sh.get("box") and sh["box"][1] < 0.22 * inv["slide_h"]
                       and (sh.get("min_font_pt") or 0) >= 22]
            if big_top:
                add("fail", "title_placeholder", i, "title drawn as a text box instead of a title placeholder%s" % (
                    "" if has_title_ph else " (use a layout that has one, e.g. Title Only)"), big_top[0]["name"])
            elif has_title_ph:
                add("warn", "title_placeholder", i, "slide has no title")
        for sh in sh_title:
            if not sh.get("box_inherited") and lay:
                lp = next((p for p in lph if p["type"] in ("title", "ctrTitle")), None)
                if lp and lp["box"] and sh["box"] and max(abs(x - y) for x, y in zip(lp["box"], sh["box"])) > 0.1 * K.EMU_IN:
                    add("warn", "title_position", i, "title placeholder moved/resized away from the layout position", sh["name"])
        if any(p["type"] == "sldNum" for p in lph) and "title" not in (lay or {}).get("kinds", []) and \
                not any(sh.get("ph_type") == "sldNum" for sh in s["shapes"]):
            add("warn", "slide_number", i, "layout has a slide-number placeholder but the slide shows no number")
        for sh in s["shapes"]:
            if sh["kind"] == "placeholder" and sh.get("ph_type") not in ("pic", "tbl", "chart", "media", "clipArt", "dgm") \
                    and not (sh.get("text") or "").strip() and sh.get("ph_type") not in ("sldNum", "dt", "ftr"):
                add("warn", "empty_placeholder", i, "empty placeholder (shows prompt text when editing)", sh["name"])
            hard = set()
            offpal = set()
            for p in sh.get("paras", []):
                for r in p["runs"]:
                    for k in ("latin", "ea"):
                        if r.get(k) and not r[k].startswith("+") and r[k] not in tfonts:
                            hard.add(r[k])
                    c = r.get("color")
                    if c and not str(c).startswith("scheme:"):
                        rgb = hex_rgb(c)
                        if rgb and not (abs(rgb[0] - rgb[1]) < 8 and abs(rgb[1] - rgb[2]) < 8):
                            nk, d = nearest(rgb, pal)
                            if d > 40:
                                offpal.add(c)
            for f in sorted(hard):
                add("fail", "hard_font", i, "hard-coded font '%s' (template fonts: %s)" % (f, ", ".join(sorted(tfonts))), sh["name"])
            for c in sorted(offpal):
                add("warn", "off_palette", i, "colour #%s is not in the template palette" % c, sh["name"])
    fails = [x for x in issues if x["level"] == "fail"]
    by_rule = collections.Counter(x.get("rule", "render") for x in issues)
    slides_clean = [s["index"] for s in inv["slides"] if not any(x["slide"] == s["index"] and x["level"] == "fail" for x in issues)]
    deck_level_fail = any(x["slide"] is None and x["level"] == "fail" for x in issues)
    rep = dict(status="needs_fix" if fails else "pass", deck=os.path.abspath(a.pptx), template=os.path.abspath(a.template),
               template_sha256=K.sha256(a.template), n_slides=inv["n_slides"], slides_without_fail=len(slides_clean),
               compliance_rate=round(len(slides_clean) / max(1, inv["n_slides"]), 3) if not deck_level_fail else 0.0,
               issues_by_rule=dict(by_rule), n_fail=len(fails), n_warn=len(issues) - len(fails),
               per_slide=[dict(slide=p["slide"], layout=p["layout"], issues=[x["msg"] for x in issues if x["slide"] == p["slide"]])
                          for p in per], deck_level=[x["msg"] for x in issues if x["slide"] is None], issues=issues)
    out = a.out or "compliance.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(dict(status=rep["status"], compliance_rate=rep["compliance_rate"], fails=len(fails), warns=rep["n_warn"],
                          by_rule=rep["issues_by_rule"], out=out), ensure_ascii=False))
    for x in issues[:30]:
        print("  %s %s slide %s %s: %s" % (x["level"].upper(), x.get("rule"), x["slide"], x.get("shape") or "", x["msg"]))
    return 0


def cmd_fix(a):
    import deck_edit as DE
    work = tempfile.mkdtemp(prefix="tpl_")
    t = open_template(a.template, work)
    tcat = K.catalog(K.Package(t))
    tth = tcat["masters"][0]["theme"] if tcat["masters"] else {}
    tfonts = {v for v in (tth.get("fonts") or {}).values() if v}
    pal = palette(tth)
    rules = set(a.rules.split(","))
    eng = DE.XmlEngine(a.pptx)
    pkg = eng.pkg
    log = []
    if "theme" in rules:
        log.append(dict(rule="theme", changes=eng.op_apply_theme({"from_template": t})))
    cat = K.catalog(pkg)
    lay_by = {l["part"]: l for l in cat["layouts"]}
    for s in cat["slides"]:
        root = pkg.xml(s["part"])
        tree = root.find(P + "cSld/" + P + "spTree")
        lp = (pkg.related(s["part"], K.RT_LAYOUT) or [None])[0]
        lay = lay_by.get(lp)
        changed = False
        parent = {c: p for p in tree.iter() for c in p}
        for e in list(DE.shape_elems(tree)):
            name = (DE.cnv(e).get("name") if DE.cnv(e) is not None else "")
            for rpr in [x for x in e.iter() if local(x.tag) in ("rPr", "endParaRPr", "defRPr")]:
                if "fonts" in rules:
                    for ch in list(rpr):
                        if local(ch.tag) in ("latin", "ea", "cs") and ch.get("typeface") and not ch.get("typeface").startswith("+") \
                                and ch.get("typeface") not in tfonts:
                            log.append(dict(rule="fonts", slide=s["index"], shape=name, before=ch.get("typeface"), after="theme font"))
                            rpr.remove(ch)
                            changed = True
                if "colors" in rules:
                    for sf in [c for c in rpr if local(c.tag) == "solidFill"]:
                        c = sf.find(A + "srgbClr")
                        if c is None:
                            continue
                        rgb = hex_rgb(c.get("val"))
                        if not rgb or (abs(rgb[0] - rgb[1]) < 8 and abs(rgb[1] - rgb[2]) < 8):
                            continue
                        nk, d = nearest(rgb, pal)
                        if d > 40:
                            sf.remove(c)
                            if nk and d < 120:
                                sf.append(K.frag('<a:schemeClr val="%s"/>' % nk))
                                after = "theme " + nk
                            else:
                                rpr.remove(sf)
                                after = "inherited"
                            log.append(dict(rule="colors", slide=s["index"], shape=name, before=c.get("val"), after=after))
                            changed = True
                if "min_size" in rules and rpr.get("sz") and int(rpr.get("sz")) < a.min_body_pt * 100 and local(rpr.tag) == "rPr":
                    ph = DE.ph_of(e)
                    if not (ph is not None and ph.get("type") in ("ftr", "sldNum", "dt")) and not name.startswith(("Caption", "Source Note")) \
                            and local(e.tag) != "graphicFrame":
                        log.append(dict(rule="min_size", slide=s["index"], shape=name, before=int(rpr.get("sz")) / 100, after=a.min_body_pt))
                        rpr.set("sz", str(int(a.min_body_pt * 100)))
                        changed = True
            ph = DE.ph_of(e)
            if "title_pos" in rules and ph is not None and ph.get("type") in ("title", "ctrTitle"):
                sppr = e.find(P + "spPr")
                x = sppr.find(A + "xfrm") if sppr is not None else None
                if x is not None and lay:
                    lpp = next((p for p in lay["placeholders"] if p["type"] in ("title", "ctrTitle")), None)
                    if lpp and lpp["box"] and max(abs(u - v) for u, v in zip(lpp["box"], K._box(e))) > 0.1 * K.EMU_IN:
                        log.append(dict(rule="title_pos", slide=s["index"], shape=name, before=[K.to_in(v) for v in K._box(e)],
                                        after="layout position"))
                        sppr.remove(x)
                        changed = True
            if "empty_ph" in rules and ph is not None and ph.get("type", "obj") not in ("pic", "tbl", "chart", "sldNum", "dt", "ftr") \
                    and local(e.tag) == "sp" and not DE.text_of(e).strip():
                log.append(dict(rule="empty_ph", slide=s["index"], shape=name, before="empty placeholder", after="removed"))
                parent[e].remove(e)
                changed = True
                continue
            if "pictures" in rules and local(e.tag) == "pic":
                b = K._box(e)
                blip = e.find(P + "blipFill/" + A + "blip")
                if b and blip is not None and e.find(P + "blipFill/" + A + "srcRect") is None:
                    rr = {r["Id"]: r for r in pkg.rels(s["part"])}
                    rid = blip.get(R + "embed")
                    if rid in rr:
                        mp = pkg.resolve(s["part"], rr[rid]["Target"])
                        tmp = os.path.join(work, "img" + os.path.splitext(mp)[1])
                        with open(tmp, "wb") as f:
                            f.write(pkg.read(mp))
                        px = K.image_size(tmp)
                        if px and abs((b[2] / max(1, b[3])) / (px[0] / float(px[1])) - 1) > 0.02:
                            sc = min(b[2] / float(px[0]), b[3] / float(px[1]))
                            w, h = int(px[0] * sc), int(px[1] * sc)
                            x = e.find(P + "spPr/" + A + "xfrm")
                            x.find(A + "off").set("x", str(b[0] + (b[2] - w) // 2))
                            x.find(A + "off").set("y", str(b[1] + (b[3] - h) // 2))
                            x.find(A + "ext").set("cx", str(w))
                            x.find(A + "ext").set("cy", str(h))
                            log.append(dict(rule="pictures", slide=s["index"], shape=name, before=[K.to_in(v) for v in b],
                                            after=[K.to_in(v) for v in (b[0] + (b[2] - w) // 2, b[1] + (b[3] - h) // 2, w, h)]))
                            changed = True
        if "title_box" in rules:
            has_title = any(DE.ph_of(e) is not None and DE.ph_of(e).get("type") in ("title", "ctrTitle") for e in DE.shape_elems(tree))
            if not has_title:
                inv_s = next((x for x in K.inventory(pkg, cat)["slides"] if x["part"] == s["part"]), None)
                tops = [sh for sh in (inv_s or {}).get("shapes", []) if sh["kind"] == "textbox" and sh.get("box")
                        and sh["box"][1] < 0.22 * cat["slide_h"] and (sh.get("min_font_pt") or 0) >= 22 and sh.get("text", "").strip()]
                if tops:
                    target = lay if lay and any(p["type"] in ("title", "ctrTitle") for p in lay["placeholders"]) else None
                    if target is None:
                        target, _ = K.pick_layout(cat, "title_only", lay["master"] if lay else 0)
                        if target and lp:
                            rels = pkg.rels(s["part"])
                            for r_ in rels:
                                if r_["Type"] == K.RT_LAYOUT:
                                    r_["Target"] = pkg.relpath(s["part"], target["part"])
                            pkg.write_rels(s["part"], rels)
                            log.append(dict(rule="title_box", slide=s["index"], before="layout '%s'" % (lay or {}).get("name"),
                                            after="layout '%s' (has a title placeholder)" % target["name"]))
                            lay = target
                    tph = next((p for p in (target or {}).get("placeholders", []) if p["type"] in ("title", "ctrTitle")), None)
                    tb_el = DE.find_shape(tree, tops[0]["id"])
                    if tph and tb_el is not None:
                        nid = K.max_shape_id(tree) + 1
                        tree.insert(2, K.frag(K.ph_sp_xml(nid, "Title", tph["attrs"], [dict(text=tops[0]["text"], bullet=False)])))
                        parent = {c: p for p in tree.iter() for c in p}
                        parent[tb_el].remove(tb_el)
                        log.append(dict(rule="title_box", slide=s["index"], shape=tops[0]["name"], before="title as text box",
                                        after="text moved into the layout's title placeholder"))
                        changed = True
        if "slide_numbers" in rules and lay and "title" not in lay["kinds"]:
            has = any(DE.ph_of(e) is not None and DE.ph_of(e).get("type") == "sldNum" for e in DE.shape_elems(tree))
            lp_ = next((p for p in lay["placeholders"] if p["type"] == "sldNum"), None)
            if lp_ and not has:
                nid = K.max_shape_id(tree) + 1
                tree.append(K.frag(K.ph_sp_xml(nid, "Slide Number", lp_["attrs"], [dict(text=str(s["index"]), field="slidenum", bullet=False)])))
                log.append(dict(rule="slide_numbers", slide=s["index"], before="none", after="slide-number placeholder"))
                changed = True
        if changed:
            pkg.touch(s["part"])
    pkg.save(a.out)
    with open(a.log, "w", encoding="utf-8") as f:
        json.dump(dict(input=os.path.abspath(a.pptx), input_sha256=K.sha256(a.pptx), output=os.path.abspath(a.out),
                       output_sha256=K.sha256(a.out), template=os.path.abspath(a.template), rules=sorted(rules),
                       n_changes=len(log), changes=log), f, ensure_ascii=False, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    cnt = collections.Counter(x["rule"] for x in log)
    print(json.dumps(dict(out=a.out, changes=dict(cnt), log=a.log), ensure_ascii=False))
    return 0


def cmd_to_spec(a):
    pkg = K.Package(a.pptx)
    cat = K.catalog(pkg)
    inv = K.inventory(pkg, cat)
    media = a.media or os.path.join(os.path.dirname(os.path.abspath(a.out)), "deck_media")
    os.makedirs(media, exist_ok=True)
    lay_by = {l["part"]: l for l in cat["layouts"]}
    slides, dropped, footers = [], [], []
    W, H = inv["slide_w"], inv["slide_h"]
    for s in inv["slides"]:
        lay = lay_by.get(s["layout_part"]) or {"kinds": []}
        sp = dict(id="S%d" % s["index"], origin="source deck slide %d (layout '%s')" % (s["index"], s["layout"]), sources=[])
        if "title" in lay["kinds"]:
            sp["role"] = "title"
        elif "section" in lay["kinds"]:
            sp["role"] = "section"
        bodies, extra, figs, tables, captions = [], [], [], [], []
        if not any(sh.get("ph_type") in ("title", "ctrTitle") for sh in s["shapes"]):
            tops = [sh for sh in s["shapes"] if sh["kind"] == "textbox" and sh.get("box") and sh["box"][1] < 0.22 * H
                    and (sh.get("min_font_pt") or 0) >= 22 and sh.get("text", "").strip()]
            if tops:
                sp["title"] = tops[0]["text"]
                sp["title_from"] = "text box '%s' (no title placeholder in source)" % tops[0]["name"]
        for sh in s["shapes"]:
            if sp.get("title_from") and sh["kind"] == "textbox" and sh.get("text") == sp["title"]:
                continue
            pt = sh.get("ph_type")
            if pt in ("title", "ctrTitle"):
                sp["title"] = sh.get("text", "")
            elif pt == "subTitle" or (pt in ("body",) and sp.get("role") in ("title", "section")):
                sp["subtitle"] = [p["text"] for p in sh.get("paras", []) if p["text"].strip()]
            elif pt in ("obj", "body"):
                bodies.append(sh)
            elif pt in ("ftr", "sldNum", "dt"):
                if pt == "ftr" and sh.get("text", "").strip():
                    footers.append(sh["text"].strip())
                continue
            elif sh["kind"] == "picture" and sh.get("image_part"):
                data = pkg.read(sh["image_part"])
                h = hashlib.sha256(data).hexdigest()[:10]
                fn = os.path.join(media, "s%02d_%s%s" % (s["index"], h, os.path.splitext(sh["image_part"])[1]))
                with open(fn, "wb") as f:
                    f.write(data)
                figs.append(dict(image=fn, ref=sh.get("descr") or sh["name"], caption="", kind="carried_over", sha256_10=h))
            elif sh["kind"] == "table":
                tables.append(dict(rows=sh["table"], ref=sh["name"]))
            elif sh["kind"] in ("chart", "graphic", "group") or (sh["kind"] == "shape" and not sh.get("text", "").strip()):
                if sh["kind"] == "shape":
                    continue
                crop = None
                if a.previews and sh.get("box"):
                    png = os.path.join(a.previews, "slide-%02d.png" % s["index"])
                    if os.path.exists(png):
                        try:
                            from PIL import Image
                            im = Image.open(png)
                            iw, ih = im.size
                            b = sh["box"]
                            crop = os.path.join(media, "s%02d_%s_raster.png" % (s["index"], re.sub(r"\W", "_", sh["name"])))
                            im.crop((int(b[0] / W * iw), int(b[1] / H * ih), int((b[0] + b[2]) / W * iw), int((b[1] + b[3]) / H * ih))).save(crop)
                        except Exception:
                            crop = None
                if crop:
                    figs.append(dict(image=crop, ref=sh["name"], caption="", kind="raster_of_%s" % sh["kind"]))
                else:
                    dropped.append(dict(slide=s["index"], shape=sh["name"], kind=sh["kind"],
                                        reason="native %s cannot be rebuilt; pass --previews to carry it as an image" % sh["kind"]))
            elif re.match(r"^Caption \d+", sh["name"]) and sh.get("text", "").strip():
                captions.append(sh["text"].strip())
            elif sh["name"].startswith("Source Note") and sh.get("text", "").strip():
                sp["source_note"] = sh["text"].strip()
            elif sh.get("text", "").strip():
                extra.append(sh)
        paras = []
        if len(bodies) >= 2:
            cols = sorted(bodies, key=lambda x: (x["box"] or [0])[0])
            sp["left"] = dict(bullets=[dict(text=p["text"], level=p["level"]) for p in cols[0]["paras"] if p["text"].strip()])
            sp["right"] = dict(bullets=[dict(text=p["text"], level=p["level"]) for p in cols[1]["paras"] if p["text"].strip()])
        else:
            for b in bodies:
                paras += [dict(text=p["text"], level=p["level"]) for p in b["paras"] if p["text"].strip()]
        for x in extra:
            paras += [dict(text=p["text"], level=p["level"], origin="text box '%s'" % x["name"]) for p in x["paras"] if p["text"].strip()]
        if paras:
            sp["bullets"] = paras
        if len(figs) == 1 and not tables:
            sp["figure"] = figs[0]
        elif figs:
            sp["figures"] = figs
        if tables:
            sp["table"] = tables[0]
            for t in tables[1:]:
                dropped.append(dict(slide=s["index"], shape=t["ref"], kind="table", reason="second table on one slide: split manually"))
        visuals = ([sp["figure"]] if sp.get("figure") else sp.get("figures", [])) + ([sp["table"]] if sp.get("table") else [])
        for v, cap in zip(visuals, captions):
            v["caption_text"] = cap
            m = re.match(r"^((?:Fig(?:ure)?\.?|Table|Tab\.|图|表)\s*\d+)", cap)
            v["ref"] = m.group(1) if m else v.get("ref")
        for cap in captions[len(visuals):]:
            sp.setdefault("bullets", []).append(dict(text=cap, level=0, origin="caption text box"))
        if s["notes"].strip():
            sp["notes"] = s["notes"]
        slides.append(sp)
    spec = dict(meta=dict(title=inv["slides"][0]["title"] if inv["slides"] else "", mode="template",
                          source_deck=os.path.abspath(a.pptx), source_sha256=K.sha256(a.pptx),
                          note="generated by deck_template.py to_spec: text copied exactly; set roles/layouts, then deck_build --template"),
                footer=dict(text=max(set(footers), key=footers.count) if footers else "", slide_numbers=True),
                slides=slides, not_carried_over=dropped)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(out=a.out, slides=len(slides), pictures=sum(1 for s in slides for _ in ([s["figure"]] if s.get("figure") else s.get("figures", []))),
                          tables=sum(1 for s in slides if s.get("table")), not_carried_over=len(dropped)), ensure_ascii=False))
    for d in dropped:
        print("  NOT CARRIED: slide %d %s (%s): %s" % (d["slide"], d["shape"], d["kind"], d["reason"]))
    return 0


def deck_content(path):
    pkg = K.Package(path)
    inv = K.inventory(pkg)
    paras, notes, nums, imgs = [], [], [], set()
    for s in inv["slides"]:
        for sh in s["shapes"]:
            if sh.get("ph_type") in ("sldNum", "dt") or sh["name"].startswith(("Slide Number", "Source Note")):
                continue
            for line in (sh.get("text") or "").split("\n"):
                t = re.sub(r"\s+", " ", line).strip()
                if t:
                    paras.append((s["index"], t))
            if sh.get("image_part"):
                imgs.add(hashlib.sha256(pkg.read(sh["image_part"])).hexdigest())
        if s["notes"].strip():
            notes.append((s["index"], re.sub(r"\s+", " ", s["notes"]).strip()))
    blob = " ".join(t for _, t in paras)
    for m in re.finditer(r"[-+−]?\d+(?:[.,]\d+)*%?", blob):
        nums.append(m.group(0))
    return paras, notes, nums, imgs, blob


def cmd_preserve(a):
    pa, na, numa, ia, _ = deck_content(a.a)
    pb, nb, numb, ib, blob_b = deck_content(a.b)
    nb_blob = " ".join(t for _, t in nb)
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    bb = norm(blob_b)
    miss_p = [dict(slide=i, text=t) for i, t in pa if norm(t) not in bb]
    miss_n = [dict(slide=i, text=t[:120]) for i, t in na if norm(t) not in norm(nb_blob) and norm(t) not in bb]
    cb = collections.Counter(numb)
    miss_num = []
    for k, v in collections.Counter(numa).items():
        if cb.get(k, 0) < v:
            miss_num.append(dict(number=k, in_a=v, in_b=cb.get(k, 0)))
    miss_img = len(ia - ib)
    rep = dict(a=os.path.abspath(a.a), b=os.path.abspath(a.b), paragraphs_a=len(pa), paragraphs_missing=miss_p,
               notes_a=len(na), notes_missing=miss_n, numbers_missing=miss_num, images_a=len(ia), images_missing=miss_img,
               text_preserved_rate=round(1 - len(miss_p) / max(1, len(pa)), 3),
               status="pass" if not (miss_p or miss_n or miss_num or miss_img) else "check")
    out = a.out or "preserve.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print(json.dumps(dict(status=rep["status"], text_preserved_rate=rep["text_preserved_rate"], paragraphs_missing=len(miss_p),
                          notes_missing=len(miss_n), numbers_missing=len(miss_num), images_missing=miss_img, out=out), ensure_ascii=False))
    for m in miss_p[:15]:
        print("  MISSING slide %d: %s" % (m["slide"], m["text"][:100]))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect")
    p.add_argument("--template", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--gallery", default=None)
    p = sub.add_parser("audit")
    p.add_argument("--pptx", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--min-body-pt", type=float, default=12)
    p = sub.add_parser("fix")
    p.add_argument("--pptx", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--log", required=True)
    p.add_argument("--rules", default="theme,fonts,colors,title_pos,title_box,min_size,pictures,empty_ph,slide_numbers")
    p.add_argument("--min-body-pt", type=float, default=12)
    p = sub.add_parser("to_spec")
    p.add_argument("--pptx", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--media", default=None)
    p.add_argument("--previews", default=None)
    p = sub.add_parser("preserve")
    p.add_argument("--a", required=True)
    p.add_argument("--b", required=True)
    p.add_argument("--out", default=None)
    a = ap.parse_args()
    return {"inspect": cmd_inspect, "audit": cmd_audit, "fix": cmd_fix, "to_spec": cmd_to_spec, "preserve": cmd_preserve}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
