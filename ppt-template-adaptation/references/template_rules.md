# Template application and format adaptation rules

"Applied the template" means the **inheritance chain is real and checkable**: slide → layout → master → theme.
A deck where text boxes imitate the template's title, where fonts and colours are typed onto every run, or where
only the first slides were fixed, is not template-compliant even if it looks similar. deck_template.py audit
checks the items below per slide and writes `compliance.json` (per-slide deviation list + rate).

## 1. Template anatomy (read `template_report.json` / the layout gallery once)
- **Master**: theme fonts (major = headings, minor = body; latin + East-Asian `ea`), theme colours
  (dk1/lt1/dk2/lt2/accent1–6), text-size ladder (title, body levels 1–9), background, footer placeholders.
- **Layouts**: Title Slide, Title and Content, Two Content, Comparison, Section Header, Title Only, Blank, Picture
  with Caption …; each has placeholders (type + idx + box). Content type → layout:
  | content | layout kind | builder `layout` |
  |---|---|---|
  | deck title | title (ctrTitle + subTitle) | title |
  | claim + bullets | content (title + one body/obj) | bullets |
  | two lists / text vs figure | two (title + two obj) | two_column |
  | figure / chart / table | title_only (+ boxes inside the content area) | figure / chart / table |
  | section divider | section | section |
  Force a specific layout with `layout_name` (name or index from template_report.json).
- **Placeholders keep inheritance** only when the slide shape carries the same `<p:ph type idx>` and no own
  position or run-level fonts. The builder writes exactly that.

## 2. Missing layouts / placeholders
| situation | what the builder does (recorded in build_report.json) | what you can do |
|---|---|---|
| no Two Content layout | split the content area into two text boxes | accept, or use bullets + figure |
| no Title Only layout | use Title and Content, drop the empty body | — |
| layout without title placeholder | title drawn as a text box + warning | pick another layout with `layout_name` |
| no slide-number / footer placeholder | numbers omitted + warning | tell the user; do not draw fake footers |
| template has sample slides | removed (use `--keep-template-slides` to keep them) | — |
Never imitate a placeholder with a text box; never edit the master to make content fit.

## 3. Size ladder and overflow
- Placeholder text **inherits** the template sizes; the deck's type scale is written to `type_scale.json`.
  Titles: ONE size for every content slide — the template's title size, or one ladder step smaller (0.86, then
  0.74 ×) for the whole deck when some title would need 3 lines; cover and section titles keep their layout size;
  title boxes stay where the layout puts them. Body: a 3-step ladder = the template body size and two steps
  (e.g. 32/28/24); a slide that needs less takes the next step (sub-levels one step below); at the template size
  nothing is written (inheritance), spacing is the template's own. Below the ladder only to avoid overflow (≥ 12
  pt, warned by `deck_check scale`); captions/source notes 12/10 pt; tables 24…10 pt. 7–12 short bullets flow
  into the template's two-content layout at one common step. Each override is logged (`font_overrides` with `why`).
- Still overflowing at the floor → FAIL: shorten (keep wording from the source) or split the slide.
- Do not rely on "shrink text on overflow" (`normAutofit`): render_report warns when a template does.

## 4. Fonts and colours
- Use theme fonts only (`+mj-lt`, `+mn-lt`, `+mn-ea` references); run-level hard-coded fonts are violations
  (`fix --rules fonts` removes them so text inherits the theme).
- Chinese text needs the theme's East-Asian font; if the template defines none, set it with
  `deck_edit.py apply` op `apply_theme {"fonts":{"ea":"<installed CJK font>"}}` and report it.
- Colours: theme colours (schemeClr) only. Off-palette RGB on text is flagged; `fix --rules colors` maps it to the
  nearest theme colour (if close) or removes it. Grey shades are allowed.
- render_report.json lists each requested font, what fontconfig resolves it to, and what the PDF really embeds —
  a substitution (e.g. Calibri → Carlito, metric-compatible) is recorded, not hidden.

## 5. Figures and tables in a template
- Pictures are contain-fitted into the content area (never stretched; stretched pictures are FAIL and
  `fix --rules pictures` restores the aspect ratio inside the old box).
- Captions keep the source numbering ("Fig. 3 (p.6)"); charts use the template's accent colours automatically.
- Low resolution (< 96 px/inch on the slide) is reported; re-crop at higher dpi.

## 6. Batch consistency (whole deck, not the first 3 slides)
Same layout for the same content kind; titles in the title placeholder at the layout position on every slide
(moved titles are flagged, `fix --rules title_pos` resets them); slide numbers and footer on all content slides
(`fix --rules slide_numbers`); no empty placeholders (`fix --rules empty_ph`); same size ladder throughout.

## 7. Adaptation routes
| route | when | commands |
|---|---|---|
| A rebuild | a draft deck must move onto a new template (different master, size or layouts) | `to_spec` → edit spec (roles, `layout_name`, split overfull slides; keep text) → `deck_make --template` → `preserve` |
| B generate | only paper/materials + template | facts.json + spec.json → `deck_make --template` |
| C repair in place | deck already uses the template's master; formatting drifted | `audit` → `fix` → `audit` → render |
Always report compliance **before and after** and the content-preservation result for A/C.

## 8. Export consistency
The PDF is rendered from the same .pptx by LibreOffice; previews are made from that PDF. render_report compares
slide count vs PDF pages, finds each slide title in the PDF text layer, and lists embedded fonts. Fonts that are
not installed on the target machine will change line breaks — note it in uncertainty.
