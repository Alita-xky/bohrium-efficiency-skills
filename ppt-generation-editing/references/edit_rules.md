# Editing academic decks: source-preserving, versioned, evidence-checked

An edit task is not a rebuild. Reviewers compare the revised deck with the original: slides and shapes that
were not addressed must stay identical (ids, positions, formatting, notes, master), every change must be
explainable (which instruction, which source), and a bad round must be reversible.

## 1. Rules
1. Never regenerate an edit task from scratch. Use `deck_edit.py` ops on the existing file; untouched package
   parts stay byte-identical with the XML engine (`--engine xml`), and the log lists changed / unchanged parts.
2. Address precisely: slide position at the time of the op (or the stable `slide_id` from inventory) + shape
   name / id / `title` / `body`. Put text edits before structural ops (move/delete/duplicate) or use `slide_id`.
3. Every op carries `reason` (the instruction it implements) and, for content, `src` (paper locator / fact id).
   Numbers come from the materials, exactly as written there (deck_check --pptx verifies all numbers on all slides).
4. Keep the template: no run-level font names or RGB colours unless asked; use `apply_theme` for deck-wide
   font/colour changes. The round audits the result against v0's master/theme (`compliance.json`).
5. One round = one coherent set of instructions → render → diff → check → snapshot. Look at the contact sheet;
   if the round broke something, roll back (`cp versions/vN-1.pptx deck.pptx`) and redo the ops.

## 2. Instruction → ops cookbook
| instruction | ops |
|---|---|
| fix a wrong number / typo | `replace_text {slide, shape, find, replace}` (run formatting kept) |
| compress a slide to N bullets | `set_text {slide, shape:"body", paragraphs:[…N phrases…]}` (levels/formatting reused); move the dropped detail into `set_notes` |
| expand a slide | `set_text` with more paragraphs; if > 6 bullets → `duplicate_slide` + `set_text` on both halves |
| change audience (reviewer / supervisor / peers) | rewrite titles as claims for that audience (`set_text title`), adjust notes; keep numbers |
| translate a slide / deck | `set_text` per shape with the translation; keep numbers, symbols, method names; CJK runs are tagged zh-CN; check the CJK font in render_report |
| insert a paper figure | `add_slide {slide:{title, figure:{pdf,page,bbox,ref,caption,kind}}}` or `replace_image {slide, shape, image}` (aspect kept) |
| add a backup / data slide | `add_slide {position, slide:{role:"backup", table:{csv}} }` |
| reorder / remove | `move_slide {slide,to}`, `reorder {order}`, `delete_slide {slide}` |
| split a slide | `duplicate_slide {slide}` then `set_text` on each copy |
| emphasise | `set_font {slide, shape, bold:true, color:"accent2"}` (theme colour) |
| restyle deck-wide | `apply_theme {fonts:{major,minor,ea}, colors:{accent1:…}}` or `{from_template: path}` |
| speaker notes | `set_notes {slide, text}` (2–4 sentences, not the bullets) |
| move/resize a shape | `move_shape {slide, shape, box:[x,y,w,h] inches}` |

## 3. Generate-then-edit
If no deck is given, generate v0 like paper2ppt (facts.json + spec.json → `deck_make.py`), snapshot it, then
apply the requested revisions as rounds so the change history is still recorded.

## 4. Records produced per round (`deck_edit.py round`)
| file | content |
|---|---|
| `versions/vN.pptx`, `versions/versions.json` | every version with sha256, parent, note, rollback command |
| `edit_log.json` | list of rounds; per op: engine, status, reason, before/after text/fonts/order, parts changed |
| `slide_diff.json/.md` | previous version vs new: added/removed/moved slides, per-shape text diff, notes diff, % pixels changed |
| `check_report.json`, `evidence_map.*` | numbers on every slide found in the materials; slide text auto-matched to paper paragraphs |
| `compliance.json` | template/master unchanged vs v0 (theme, layouts, fonts, colours, titles, numbering) |
| `deck.pdf`, `previews/` | real renders of the current version |
A slide that the instructions did not address but shows up as changed in slide_diff is a regression: fix it.
