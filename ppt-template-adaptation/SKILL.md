---
name: ppt-template-adapter
description: "Apply a given PowerPoint template (.pptx/.potx master + layouts) to an academic deck or to paper materials so that inheritance is real and checkable: content mapped into the template's placeholders, theme fonts/colours kept, an explainable font-size ladder instead of overflow, contain-fit figures with source numbering, slide numbers/footers on every slide, per-slide compliance audit before/after with rule-based fixes, content-preservation check, real PDF/PNG renders, evidence map and a contract-complete result.json. Use for template application, re-theming, format adaptation, batch formatting consistency or 'make this deck follow our template' tasks."
---

# Template adapter: real master/layout inheritance, audited slide by slide

Goal: a deck that **truly uses the given template** (every slide on a template layout, titles/text in its
placeholders, theme fonts and colours, footers and slide numbers from its placeholders), whose **content is
unchanged and traceable**, with **per-slide deviation records before and after**, delivered as `.pptx` + PDF/PNG.

Budget: 64 requests / 900 s / 16k tokens per reply. Target ≈ 14 requests; deliverable after step 4.

```bash
SKILL=$(dirname "$(find /skills /app -name SKILL.md -path '*weekly-skill*' 2>/dev/null | head -1)")
[ -f "$SKILL/scripts/deck_build.py" ] || SKILL=$(dirname "$(dirname "$(find / -name deck_build.py -path '*scripts*' 2>/dev/null | head -1)")")
mkdir -p /app/work /app/output
```

## 1. Prepare (1 call) — template anatomy + starting deck + paper map
```bash
cat task.md /input/task.md 2>/dev/null | head -150; python3 -c "import json;print(json.dumps(json.load(open('/input/case.json')),ensure_ascii=False,indent=1)[:5000])"
python3 $SKILL/scripts/deck_prepare.py --input /input --work /app/work --gallery
```
Prints the template's layouts and placeholders (type/idx), theme fonts/colours, text-size ladder, the
content→layout mapping and gaps; renders `work/template_gallery/template_gallery.png` (one slide per layout,
placeholders labelled) and, if a deck is given, its inventory (`deck_inventory.json`, shape ids/names/fonts) and
renders (`work/start_render/previews/`). Look at the gallery and the starting deck's contact sheet once.
A `.potx` is handled directly (converted to a presentation package internally).

## 2. Audit the starting deck (1 call, if a deck is given) — the "before" record
```bash
python3 $SKILL/scripts/deck_template.py audit --pptx /input/<deck>.pptx --template /input/<template> --out /app/output/compliance_before.json
```
Per slide: layout not from the template, theme fonts/colours/slide size differ, hard-coded fonts, off-palette
colours, title drawn as a text box or moved, missing slide number, empty placeholders, overflow, < 12 pt text,
stretched pictures, off-slide shapes. Rules and fixes: `references/template_rules.md`.

## 3. Choose the route and produce the deck (1–3 calls)
| route | when | do |
|---|---|---|
| **A rebuild** (default when a draft deck must move to the template; always if slide size/master differ) | draft deck + template | `deck_template.py to_spec --pptx <deck> --out /app/work/spec.json --previews /app/work/start_render/previews` → open spec.json, set `role` per slide, split slides that will overflow, optionally `layout_name`; keep all text exactly |
| **B generate** | paper/materials + template, no deck | write facts.json + spec.json as in `templates/spec_template.json` (claims, sources, notes) |
| **C repair in place** | deck already on the template's master; only formatting drifted | `deck_template.py fix --pptx <deck> --template <t> --out /app/output/deck.pptx --log /app/output/fix_log.json` |
to_spec copies text, levels, pictures (exported), tables, notes and footer text exactly; "Figure N: …" text
boxes become the visual's caption, "来源/Source: …" boxes its source note; shape names (Picture 2, Table 5) are
never used as paper references. Charts/SmartArt are carried as cropped images from the renders, a native chart's
data are also written to `deck_media/sNN_<chart>_data.csv` and listed in `not_carried_over` (editability lost:
say so in uncertainty). Text stays literal (`meta.math_markup: false`: "d_k" is not turned into a subscript).
After to_spec: set `role` per slide, split any slide with > 6 bullets or a title > 2 lines into two slides (copy
the text, add "（续）"/"(cont.)" to the second title, note the split in uncertainty).

## 4. Build + check + render + audit + preservation (1 call; routes A/B)
```bash
python3 $SKILL/scripts/deck_make.py --spec /app/work/spec.json [--facts /app/work/facts.json] --outdir /app/output \
  --template /input/<template> --mode template [--preserve-from /input/<deck>.pptx]
```
Builds on the template (placeholders inherit position/size/fonts; sizes shrink only along the logged ladder,
never below 12 pt), renders PDF + PNGs + contact sheet, writes `compliance.json` (after) and `preserve.json`
(every paragraph, number, picture and note of the source deck present). For route C run `deck_template.py audit`
and `deck_render.py` on the fixed deck. Fix FAILs (shorten/split, change layout), re-run at most twice, look
at `previews/contact_sheet.png` once: same title position on all slides, nothing cut, pictures not stretched.
In template mode a number of the draft that is not in the paper is a WARN (keep it; report it in uncertainty as
unverified, or as a conflict if the paper gives another value) — never "fix" the draft's content silently.

## 5. Records and result.json (1–2 calls)
```bash
python3 $SKILL/scripts/deck_fill.py --case /input/case.json --outdir /app/output --work /app/work --out /app/work/fill.json
python3 $SKILL/scripts/make_result.py --case /input/case.json --outdir /app/output --fill /app/work/fill.json
```
Contract fields about template/format/compliance/layout mapping get compliance before/after, rates, fix log,
layouts used, `slide_layout_map` (output slide → source slide → template layout) and preservation status; others get the deck, evidence map (auto-matched to the paper for carried content), render and
version records (template sha256, fonts requested vs embedded, tools). Add unmapped fields with `--set`.

## Exception handling
| Situation | Action |
|---|---|
| template lacks a layout kind | builder falls back (recorded); or pick another with `layout_name`; never edit the master |
| layout placeholder with zero size (template defect, flagged "ZERO-SIZE" by prepare) | builder gives that slide placeholder the master's box (inheritance kept, defect listed in uncertainty) |
| native chart in the draft | image of the original render + data CSV; state "chart no longer editable, data in …csv" |
| placeholder missing (no body / no title) | content placed in the template's text area as text boxes + warning; title box flagged |
| text overflows the placeholder | ladder shrink to ≥ 12 pt; if still FAIL split the slide (content preserved across slides) |
| template font not installed / CJK font missing | keep theme font names; render_report shows substitute; for Chinese set an installed `ea` font via `deck_edit.py` `apply_theme` and record it |
| slide size differs (16:9 ↔ 4:3) | route A; pictures re-fitted, never stretched |
| chart / SmartArt / group in the draft | carried as image crop from the render (`--previews`); note "no longer editable as chart" |
| picture file broken / missing | placeholder box "Figure not available" + FAIL listed in uncertainty |
| export fails | deliver pptx, status partial, report renderer error |

## Final checklist
1. `/app/output`: `deck.pptx`, `deck.pdf`, `previews/`, `compliance_before.json` (if a deck was given),
   `compliance.json`, `preserve.json` (A/C), `fix_log.json` (C), `evidence_map.*`, `render_report.json`,
   `build_report.json`, `version_record.json`, `result.json`.
2. compliance.json: no FAIL; every slide on a template layout; title in placeholder; slide numbers present.
3. preserve.json `pass` (or every missing item explained); `result.json` complete and honest.
