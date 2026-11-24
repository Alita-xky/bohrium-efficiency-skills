#!/usr/bin/env python3
"""deck_design.py - the layout engine behind deck_build: a deck-wide type scale + content-driven slide designs.

Type scale (written to type_scale.json by deck_build): ONE title size for all content slides; body text from a
fixed ladder of 3 sizes (default theme 28/24/20 pt; with a template its body size and two 4-pt steps, e.g.
32/28/24) - a slide that needs smaller text takes the next step, sub-bullets one step below their parent;
display sizes for big numbers (72/54/44), labels 16, captions 12, source notes 10, tables 24/20/16/14/12/10.
Spacing: default theme 0.6 x size before a top-level bullet, 0.25 x size before a sub-bullet; with a template
its own paragraph spacing is inherited. Sizes outside the scale are used only to avoid overflow and are warned.

Default theme (no --template): 1 bullet -> statement panel; 2-4 short bullets -> cards (numbered, or a big number
when a results/conclusion bullet holds one number with a unit or %, copied verbatim); outline -> numbered agenda;
7+ bullets -> two columns; takeaway -> accent bar. With a supplied template body text stays in the layout's body
placeholder (explicit cards / bignumber / quote only on layouts without one, in theme fonts and accent colours).
Nothing here invents text: every string drawn comes from the spec (numbers are substrings of their bullet).
"""
import re

import pptkit as K

EMU_IN, EMU_PT = K.EMU_IN, K.EMU_PT
IN = EMU_IN
AGENDA_ROLES = {"outline", "agenda", "overview", "contents", "toc", "roadmap"}
NO_AUTO_ROLES = {"title", "section", "references", "qa", "questions", "thanks"}
CARD_FILL = "F3F5F8"
NEUTRAL_TEXT = "595959"
DISPLAY = [72, 54, 44]
LABEL_PT = 16
CAPTION_PT = 12
NOTE_PT = 10
TABLE_LADDER = [24, 20, 16, 14, 12, 10]
SPACING = {"level0": 0.6, "sub": 0.25}


def make_scale(designed, body_base, title_pt=None, min_body=12):
    """the deck's type scale (body ladder of 3 sizes)"""
    if designed:
        body = [28, 24, 20]
    else:
        b = int(round(body_base or 24))
        st = 4 if b >= 24 else 2
        body = [b, max(min_body, b - st), max(min_body, b - 2 * st)]
    body = sorted(set(body), reverse=True)
    return dict(title_pt=title_pt, body=body, display=DISPLAY, label=LABEL_PT, caption=CAPTION_PT, note=NOTE_PT,
                table=TABLE_LADDER, spacing=dict(SPACING) if designed else "inherited from the template body style",
                designed=bool(designed))


def sub_size(ctx, size):
    """one ladder step below size (the last step stays)"""
    L = ctx.scale["body"]
    for v in L:
        if v < size:
            return v
    return L[-1]


def lf(text):
    return 1.32 if K.has_cjk(text) else 1.2


def lines(text, w_pt, size):
    return K.wrap_count(text, w_pt, size)


def tint(hex6, keep=0.12):
    """hex colour mixed with white (keep = share of the colour)"""
    try:
        c = [int(hex6[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, TypeError):
        return "EEF2F7"
    return "".join("%02X" % int(255 - (255 - v) * keep) for v in c)


def accent_hex(ctx, k="accent1"):
    return (ctx.colors or {}).get(k) or K.DEFAULT_COLORS.get(k, "0072B2")


def card_fill(ctx):
    """default theme: neutral panel; template: its light lt2 theme colour when it is light enough"""
    if ctx.designed:
        return CARD_FILL
    lt2 = (ctx.colors or {}).get("lt2") or ""
    try:
        r, g, b = (int(lt2[i:i + 2], 16) for i in (0, 2, 4))
        if 0.299 * r + 0.587 * g + 0.114 * b > 215:
            return "lt2"
    except (ValueError, TypeError):
        pass
    return "F2F2F2"


def groups_of(bullets):
    """level-0 bullets with their sub-bullets"""
    gs = []
    for b in bullets:
        if b.get("level", 0) == 0 or not gs:
            gs.append(dict(text=b["text"], subs=[], src=b))
        else:
            gs[-1]["subs"].append(b["text"])
    return gs


def split_head(text):
    """'Head: rest' -> (head, rest) when the head is a short label (no digit right before the colon)"""
    m = re.match(r"^\s*([^:：]{2,40}?)\s*([:：])\s*(\S.*)$", str(text))
    if not m or re.search(r"\d$", m.group(1)) or "://" in text:
        return None, text
    head = m.group(1)
    if K.text_em(head) > 16 or len(m.group(3)) < 4 or "**" in head:
        return None, text
    return head, m.group(3)


# ----------------------------------------------------------------------------- decision
def choose(sl, bullets, ctx, takeaway=None):
    """design for a plain bullets slide: agenda | statement | cards | list | list2 | None (template: placeholder)"""
    role = str(sl.get("role", "")).lower()
    gs = groups_of(bullets)
    n = len(gs)
    if not n:
        return None
    if not ctx.designed:
        if n >= 7 and ctx.has_two and not any(b.get("level") for b in bullets):
            return "list2"
        return None
    if role in AGENDA_ROLES or re.match(r"^\s*(outline|agenda|overview|roadmap|contents|目录|提纲|大纲|报告提纲|汇报提纲)\s*$",
                                        str(sl.get("title", "")), re.I):
        return "agenda" if n <= 12 else None
    if role in NO_AUTO_ROLES:
        return "list2" if n >= 7 else "list"
    head_em = [K.text_em(g["text"]) + sum(K.text_em(t) for t in g["subs"]) for g in gs]
    if n == 1 and head_em[0] <= 110:
        return "statement"
    if 2 <= n <= 4 and max(head_em) <= (75 if n == 4 else 95) and sum(len(g["subs"]) for g in gs) <= 4:
        return "cards"
    if n >= 7 or (n >= 6 and max(head_em) <= 40):
        return "list2"
    return "list"


# ----------------------------------------------------------------------------- primitives
def deco(be, s, box, name, fill, prst="rect", line=None, radius=None, back=False):
    be.raw(s, lambda sid: K.deco_xml(sid, name, [int(v) for v in box], fill, prst, line, radius=radius), back=back)


def text(be, s, box, paras, name, anchor="t"):
    be.textbox(s, [int(v) for v in box], paras, name, anchor=anchor)


def est(paras, w_pt):
    """height in pt of text-box paragraphs with explicit size/spc (same model as pptkit.text_height_pt)"""
    h = 7.2
    for i, p in enumerate(paras):
        sz = float(p.get("size") or 18)
        ind = 18.0 if p.get("bullet") else 0.0
        h += lines(p.get("text", ""), w_pt - 14.4 - ind, sz) * sz * (p.get("lnspc", 0) / 100.0 or lf(p.get("text", "")))
        if i:
            h += float(p.get("spc") if p.get("spc") is not None else sz * 0.35)
    return h


# ----------------------------------------------------------------------------- cards / big numbers / statement
def card_paras(g, size, num, num_size, badge, ctx):
    ps = []
    small = sub_size(ctx, size)
    if num:
        ps.append(dict(text=num, size=num_size, bold=True, color="accent1", bullet=False, lnspc=100))
    elif badge:
        ps.append(dict(text=badge, size=ctx.scale["label"], bold=True, color="accent1", bullet=False))
    head, body = split_head(g["text"])
    t = g["text"].strip().rstrip(" .。")
    if num and not head and t.endswith(num) and re.search(r"[:：=]\s*$", t[:-len(num)]):
        head, body = re.sub(r"\s*[:：=]\s*$", "", t[:-len(num)]), num
    if head and num and body.strip(" .。") == num:  # 'Average WER at 10 h: 27.9%' -> big 27.9% + label only
        ps.append(dict(text=head, size=size, bold=True, color="tx2", bullet=False, spc=round(size * 0.4)))
    elif head:
        ps.append(dict(text=head, size=size, bold=True, color="tx2", bullet=False, spc=round(size * 0.4) if ps else None))
        ps.append(dict(text=body, size=small, bullet=False, spc=round(size * 0.25)))
    else:
        ps.append(dict(text=g["text"], size=size, bullet=False, color="tx2" if not num else None,
                       spc=round(size * 0.4) if ps else None))
    for t in g["subs"]:
        ps.append(dict(text=t, size=small, color=NEUTRAL_TEXT, bullet=True, spc=round(size * 0.25)))
    return ps


def draw_cards(be, s, gs, area, ctx, rep, numbers=True, force_numbers=False, role=None):
    """2-4 cards in a row (accent bar + number badge or big number + text); 1 group = statement panel"""
    n = len(gs)
    if n == 1:
        return draw_statement(be, s, gs[0], area, ctx, rep, numbers, role)
    gap = int(0.25 * IN)
    pad = int(0.22 * IN)
    cw = (area[2] - gap * (n - 1)) // n
    inner_w = (cw - 2 * pad) / EMU_PT
    nums = []
    for g in gs:
        num = g["src"].get("number") if isinstance(g.get("src"), dict) else None
        if num and num not in g["text"]:
            rep["warnings"].append("card number '%s' is not in its bullet text: not shown" % num)
            num = None
        nums.append(num or (K.key_number(g["text"], role) if numbers else None))
    if force_numbers and not any(nums):
        nums = [K.key_number(g["text"]) for g in gs]  # explicit bignumber layout: any role, still unit or %
    badges = ["%02d" % (i + 1) if not any(nums) else None for i in range(n)]
    if not any(nums) and force_numbers:
        rep["warnings"].append("bignumber layout: no bullet holds a single number with a unit or %; drawn as cards")
    L = ctx.scale["body"] if n <= 3 else ctx.scale["body"][1:] or ctx.scale["body"]
    nsz = next((d for d in ctx.scale["display"][(1 if n >= 3 else 0):]
                if all(K.text_em(nm) * d <= inner_w * 0.95 for nm in nums if nm)), ctx.scale["display"][-1])
    avail_h = area[3] / EMU_PT
    for size in L:
        hs = [est(card_paras(g, size, nm, nsz, badges[i], ctx), inner_w) + 0.07 * 72 + 2 * pad / EMU_PT
              for i, (g, nm) in enumerate(zip(gs, nums))]
        if max(hs) <= avail_h:
            break
    if max(hs) > avail_h:
        rep["warnings"].append("FAIL text overflow in cards (needs %.0f pt of %.0f pt at %d pt): shorten the bullets"
                               % (max(hs), avail_h, size))
    need = max(hs) * EMU_PT
    ch = int(min(area[3], max(need + 0.3 * IN, 0.72 * area[3])))
    y = area[1] + int((area[3] - ch) * 0.4)
    fill = card_fill(ctx)
    for i, (g, nm) in enumerate(zip(gs, nums)):
        x = area[0] + i * (cw + gap)
        deco(be, s, [x, y, cw, ch], "Card %d" % (i + 1), fill)
        deco(be, s, [x, y, cw, int(0.07 * IN)], "Card %d Accent" % (i + 1), "accent1")
        text(be, s, [x + pad - int(0.1 * IN), y + int(0.07 * IN) + pad - int(0.05 * IN), cw - 2 * pad + int(0.2 * IN),
                     ch - int(0.07 * IN) - 2 * pad + int(0.1 * IN)],
             card_paras(g, size, nm, nsz, badges[i], ctx), "Card %d Text" % (i + 1), anchor="ctr")
    rep["shapes"].append("Cards x%d" % n)
    rep["design"] = dict(layout="cards", n=n, text_pt=size, numbers=[x for x in nums if x], number_pt=nsz if any(nums) else None)
    rep["body_pt"] = size


def draw_statement(be, s, g, area, ctx, rep, numbers=True, role=None):
    """one bullet: a panel with a left accent bar; a key number becomes a big callout on the left"""
    num = g["src"].get("number") if isinstance(g.get("src"), dict) else None
    num = num if (num and num in g["text"]) else (K.key_number(g["text"], role) if numbers else None)
    bar = int(0.1 * IN)
    pad = int(0.35 * IN)
    avail_h = area[3] / EMU_PT
    if num:
        nw = int(area[2] * 0.36)
        tw = area[2] - nw - bar
    else:
        nw, tw = 0, area[2] - bar
    inner_w = (tw - 2 * pad) / EMU_PT
    for size in ctx.scale["body"]:
        ps = []
        head, body = split_head(g["text"])
        small = sub_size(ctx, size)
        if head:
            ps += [dict(text=head, size=size, bold=True, color="tx2", bullet=False),
                   dict(text=body, size=small, bullet=False, spc=round(size * 0.4))]
        else:
            ps += [dict(text=g["text"], size=size, bold=not num, color="tx2", bullet=False)]
        ps += [dict(text=t, size=small, color=NEUTRAL_TEXT, bullet=True, spc=round(size * 0.25)) for t in g["subs"]]
        need = est(ps, inner_w)
        if need <= avail_h * 0.9:
            break
    ph = int(min(area[3], max(need * EMU_PT + 2 * pad, 0.7 * area[3])))
    y = area[1] + int((area[3] - ph) * 0.35)
    deco(be, s, [area[0], y, area[2], ph], "Statement Panel", card_fill(ctx))
    deco(be, s, [area[0], y, bar, ph], "Statement Accent", "accent1")
    if num:
        nsz = next((d for d in ctx.scale["display"] if K.text_em(num) * d <= (nw - 2 * pad) / EMU_PT * 0.95),
                   ctx.scale["display"][-1])
        text(be, s, [area[0] + bar + pad // 2, y, nw - pad // 2, ph],
             [dict(text=num, size=nsz, bold=True, color="accent1", bullet=False, align="center", lnspc=100)], "Big Number",
             anchor="ctr")
        deco(be, s, [area[0] + bar + nw, y + int(ph * 0.2), int(0.02 * IN), int(ph * 0.6)], "Statement Divider", "D5DBE3")
    text(be, s, [area[0] + bar + nw + pad - int(0.1 * IN), y + pad // 2, tw - 2 * pad + int(0.2 * IN), ph - pad], ps,
         "Statement Text", anchor="ctr")
    if need > avail_h:
        rep["warnings"].append("FAIL text overflow in statement (needs %.0f pt of %.0f pt)" % (need, avail_h))
    rep["shapes"].append("Statement")
    rep["design"] = dict(layout="statement", text_pt=size, number=num)
    rep["body_pt"] = size


# ----------------------------------------------------------------------------- agenda, quote, section number, takeaway
def draw_agenda(be, s, gs, area, ctx, rep):
    """numbered agenda: one light row panel per item with an accent number badge"""
    n = len(gs)
    cols = 1 if n <= 6 else 2
    per = (n + cols - 1) // cols
    gap_c = int(0.4 * IN)
    cw = (area[2] - gap_c * (cols - 1)) // cols
    rh = min(int(1.15 * IN), area[3] // max(per, 1))
    gap_r = int(min(0.16 * IN, rh * 0.15))
    top = area[1] + max(0, (area[3] - rh * per) // 2)
    badge = int(min(0.72 * IN, (rh - gap_r) * 0.8))
    L = ctx.scale["body"]
    start = L[0] if per <= 4 else (L[1] if per <= 5 else L[-1])
    used = set()
    for i, g in enumerate(gs):
        c, r = divmod(i, per)
        x = area[0] + c * (cw + gap_c)
        y = top + r * rh
        ph = rh - gap_r
        deco(be, s, [x, y, cw, ph], "Agenda Row %d" % (i + 1), card_fill(ctx))
        bx, by = x + int(0.18 * IN), y + (ph - badge) // 2
        deco(be, s, [bx, by, badge, badge], "Agenda Badge %d" % (i + 1), "accent1")
        text(be, s, [bx, by, badge, badge], [dict(text="%d" % (i + 1), size=L[-1], bold=True, color="bg1", bullet=False,
                                                  align="center")], "Agenda Number %d" % (i + 1), anchor="ctr")
        tx = bx + badge + int(0.3 * IN)
        tw = x + cw - tx - int(0.15 * IN)
        sz = start
        for v in [v for v in L if v <= start]:
            sz = v
            if lines(g["text"], tw / EMU_PT - 14.4, v) * v * lf(g["text"]) <= ph / EMU_PT - 6:
                break
        used.add(sz)
        ps = [dict(text=g["text"], size=sz, bullet=False, color="tx2")]
        if g["subs"] and ph / EMU_PT > sz * 2.6:
            ps.append(dict(text=" · ".join(g["subs"]), size=L[-1], color=NEUTRAL_TEXT, bullet=False, spc=2))
        text(be, s, [tx, y, tw, ph], ps, "Agenda Item %d" % (i + 1), anchor="ctr")
    rep["shapes"].append("Agenda x%d" % n)
    rep["design"] = dict(layout="agenda", n=n, text_pt=sorted(used, reverse=True))
    rep["body_pt"] = max(used)


def draw_quote(be, s, quote, attribution, area, ctx, rep):
    pad = int(0.5 * IN)
    w = area[2] - 2 * pad
    L = ctx.scale["body"]
    for size in L:
        ps = [dict(text=quote, size=size, italic=True, color="tx2", bullet=False)]
        if attribution:
            ps.append(dict(text="— " + attribution, size=L[-1], color=NEUTRAL_TEXT, bullet=False, align="right",
                           spc=round(size * 0.8)))
        need = est(ps, w / EMU_PT - 0.9 * 72)
        if need <= area[3] / EMU_PT * 0.8:
            break
    ph = int(min(area[3], max(need * EMU_PT + 2 * pad, 0.66 * area[3])))
    y = area[1] + (area[3] - ph) // 2
    deco(be, s, [area[0], y, area[2], ph], "Quote Panel", card_fill(ctx))
    deco(be, s, [area[0], y, int(0.1 * IN), ph], "Quote Accent", "accent1")
    text(be, s, [area[0] + int(0.2 * IN), y + int(0.05 * IN), int(1.17 * IN), int(1.2 * IN)],
         [dict(text="“", size=ctx.scale["display"][1], bold=True, color="accent1", bullet=False, lnspc=100)], "Quote Mark")
    text(be, s, [area[0] + pad + int(0.9 * IN), y + pad - int(0.1 * IN), w - int(0.9 * IN), ph - 2 * pad + int(0.2 * IN)], ps,
         "Quote Text", anchor="ctr")
    if need > area[3] / EMU_PT:
        rep["warnings"].append("FAIL text overflow in quote (needs %.0f pt)" % need)
    rep["shapes"].append("Quote")
    rep["design"] = dict(layout="quote", text_pt=size)
    rep["body_pt"] = size


def draw_section_number(be, s, no, title_box, ctx, rep):
    if not title_box:
        return
    box = [title_box[0], max(int(0.6 * IN), title_box[1] - int(1.25 * IN)), int(3.0 * IN), int(1.2 * IN)]
    text(be, s, box, [dict(text="%02d" % no, size=ctx.scale["display"][1], bold=True, color="accent1", bullet=False,
                           lnspc=100)], "Section Number", anchor="b")
    rep["shapes"].append("Section Number")


def takeaway_height(text_, w, ctx):
    size = ctx.scale["body"][-1]
    h = lines(text_, w / EMU_PT - 0.55 * 72 - 14.4, size) * size * lf(text_) + 0.34 * 72
    return int(max(0.7 * 72, h) * EMU_PT), size


def draw_takeaway(be, s, text_, box, size, ctx, rep):
    acc = accent_hex(ctx)
    deco(be, s, box, "Takeaway Bar", tint(acc, 0.13))
    deco(be, s, [box[0], box[1], int(0.09 * IN), box[3]], "Takeaway Accent", "accent1")
    text(be, s, [box[0] + int(0.3 * IN), box[1], box[2] - int(0.4 * IN), box[3]],
         [dict(text=text_, size=size, bold=True, color="tx2", bullet=False)], "Takeaway", anchor="ctr")
    rep["shapes"].append("Takeaway")
    rep["takeaway"] = text_


# ----------------------------------------------------------------------------- body lists on the ladder
def list_paras(bullets, ctx, size, inherit=False):
    """paragraphs at ladder size `size` (sub-levels one step below). inherit=True (template at its own body size):
    no explicit sizes or spacing - the placeholder's inherited style is used."""
    out = []
    for i, b in enumerate(bullets):
        lv = b.get("level", 0)
        q = dict(b)
        if inherit:
            q.pop("size", None)
            q.pop("spc", None)
        else:
            q["size"] = size if lv == 0 else sub_size(ctx, size)
            if ctx.scale.get("designed"):
                q["spc"] = 0 if i == 0 else round(q["size"] * (SPACING["level0"] if lv == 0 else SPACING["sub"]))
        out.append(q)
    return out


def ladder_list(bullets, box, ctx, bullet_pt=22.0, start=0, base=None, min_pt=12):
    """largest ladder step (from `start`) at which the list fits the box. Template at its own body size keeps
    the inherited sizes. Off-ladder sizes (2-pt steps below the ladder, >= min_pt) only to avoid overflow.
    Returns dict(paras, fits, need, avail, size, off_ladder)."""
    w_pt, h_pt = box[2] / EMU_PT, box[3] / EMU_PT
    L = ctx.scale["body"]
    steps = L[min(start, len(L) - 1):]
    last = None
    for size in steps:
        inherit = (not ctx.designed) and base and abs(size - base) < 0.5
        ps = list_paras(bullets, ctx, size, inherit)
        est_ps = [dict(p, size=p.get("size") or (size if not p.get("level") else (ctx.inherited_sub or sub_size(ctx, size))))
                  for p in ps]
        need = K.text_height_pt(est_ps, w_pt, bullet_pt=bullet_pt)
        last = (ps, need, size)
        if need <= h_pt:
            return dict(paras=ps, fits=True, need=need, avail=h_pt, size=size, off_ladder=False)
    size = L[-1]
    while size - 2 >= min_pt:
        size -= 2
        ps = [dict(b, size=size if not b.get("level") else max(min_pt, size - 2), spc=None) for b in bullets]
        need = K.text_height_pt(ps, w_pt, bullet_pt=bullet_pt)
        last = (ps, need, size)
        if need <= h_pt:
            return dict(paras=ps, fits=True, need=need, avail=h_pt, size=size, off_ladder=True)
    ps, need, size = last
    return dict(paras=ps, fits=False, need=need, avail=h_pt, size=size, off_ladder=size not in L)
