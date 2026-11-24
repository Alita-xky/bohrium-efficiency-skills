# Design rules for source-bound research figures

These rules turn "looks fine" into checkable decisions. Each one is enforced or reported by the
check script (`*_check.py`) or the render script (`*_render.py`), or must be stated in `result.json`.

## 1. Content comes from the materials
- Entity names, relation directions, numbers, units and sample sizes are copied from the materials
  (main text > figure legend > table > supplement). Record `file` + `loc` (`p3:12`, `Table 2 row 4`,
  `Fig. 2B legend`) and a short verbatim quote for relations and numbers.
- Do not round differently from the source; do not convert units unless the task asks (then put the
  formula in `derived`).
- A missing value is drawn as "not reported" and listed under uncertainty; never estimate it from a
  rendered plot unless the task explicitly asks for digitisation (then label it "digitised, ±error").

## 2. Visual grammar (one meaning per channel)
| Channel | Meaning | Rule |
|---|---|---|
| fill colour | category (problem/method/result/conclusion, cell type, material class) | ≤ 6 categories, legend required |
| arrow `activates` / `promotes` (→, solid) | positive effect stated in source | direction from quote |
| arrow `inhibits` (⊣) | negative effect / suppression | must be in legend |
| arrow `produces` / `converts` (dashed →) | material/chemical transformation | label reagent/condition if stated |
| arrow `binds` / `associates` (dotted) | physical binding / correlation without direction | never imply causality |
| arrow `flow` / `next` | workflow order between panels/steps | design only, no scientific claim |
| position | spatial compartments (outside/membrane/cytosol/nucleus; upstream→downstream) | keep source topology |
| bar length / point position | measured value | axis starts at 0 for bars; units in label |

Legend colours: the swatch of a category uses the same colour as that category's entity `edge` (or
`fill`); two categories never share a colour (checked: `legend_color_mismatch`, `legend_duplicate_color`).

Palette (Okabe–Ito, colour-blind safe): `#0072B2` blue, `#E69F00` orange, `#009E73` green,
`#D55E00` vermillion, `#CC79A7` purple, `#56B4E9` sky, `#F0E442` yellow, `#000000` black.
Charts default to neutral grey so that their bars never clash with the category colours.

## 3. Layouts
- **Graphical abstract** (default template): 4 panels left→right problem → method → result →
  conclusion, flow arrows between panels, one key chart or mechanism in the result panel, legend in the
  last panel, source footnote.
- **Mechanism / pathway**: compartments as large panels (`fill` very light), entities inside, arrows
  between entities; ligand at the top, nucleus/outcome at the bottom; legend for arrow kinds. When the
  source places things in a membrane or nucleus, draw those compartments as such: a panel with
  `"shape":"membrane"` (lipid-bilayer band; receptor/channel icons placed in it span the bilayer) above a
  cytoplasm panel, and `"shape":"nucleus"` (ellipse with double envelope) for nuclear entities (§6).
- **Apparatus / technical route**: left→right or top→bottom stages as entities with `next` arrows;
  label each stage with the instrument/condition from the methods section; scale bar or dimensions only
  if reported. Instruments the methods name are drawn as icons in their physical order (§6), joined by
  `flow` arrows labelled with what flows (light, buffer, signal, data).
- **Schematic**: groups (`type:"group"`) for repeated motifs; keep labels outside small shapes.
- **Data chart**: one `chart` element per panel, data from the CSV; error bars only if the column
  exists; state n and error type (SD/SEM/CI) in the axis label or footnote, copied from the source.
  > 6 categories or long names → `barh`; several series → grouped bars with the legend above the
  plot; not-reported cells → "n.r." markers + footnote; orders of magnitude → log axis named in the label.
- **Arrows**: signal → integrator → output top-to-bottom or left-to-right; regulators at the side.
  Opposite pairs become two arcs automatically; other arrows are routed around boxes. Remaining
  `arrow_through_entity` / `arrows_cross` issues mean the entities should be re-ordered.

## 4. Typography and canvas
- Base 11 pt; nothing below 6.5 pt (renderer shrinks globally up to 30 % before reporting overflow).
- Labels ≤ ~8 words; prefer the source's term over paraphrase; abbreviations as defined in the paper.
- Default canvas 13.33 × 7.5 in (16:9) at 300 dpi; journals often ask 5 × 5 cm–13 × 5 cm or
  1200 × 600 px — follow the task exactly and report final pixel size.
- CJK text needs a CJK font: the renderer picks one whose glyphs cover the text (checked per
  character) and reports `cjk_font_missing` otherwise. Mixed CJK/Latin labels wrap without splitting
  Latin words.
- Scientific notation in labels: `10^18`, `10^{-3}`, `Ca^{2+}`, `CO_{2}` → Unicode super/subscripts
  (stay editable text). Keep the source's units (`ng/ml`, not a converted unit) unless asked.

## 5. Deliverable checklist
1. Layered SVG: one Inkscape layer per panel (+ connectors, annotations); every element is a
   `<g id=...>` named after the spec id; text stays `<text>` (multi-line labels centre-anchored).
2. PDF (TrueType embedded) and PNG previews generated from the same drawing.
3. `layer_map.json`: id → type, panel, label, source, data origin.
4. `check_report.json` + `render_report.json`: provenance, numbers, legend, overflow, canvas, export.
5. Edit tasks: `edit_log.json` with before/after for every change; all other ids untouched.

## 6. Icons (built-in vector library)
Flat vector icons drawn with matplotlib paths only (no image files, no downloads): one accent colour per
icon (Okabe–Ito tints; inside an entity the entity's `edge` colour), neutral grey instrument bodies, the
same stroke weight everywhere. Each icon is one SVG group named after its id; its label is real `<text>`.
- **Standalone**: `{"id","type":"icon","name":"laser","x","y","w","h","label":"488 nm laser","color"?,"source"}`
  — art on top, label below (`label_pos` below|above|right|left). Arrows may start or end at an icon:
  they stop at the drawn icon + label. Give icons in one row the same `w`/`h` so labels line up.
- **Inside a box**: `"icon":"cell"` on an `entity` draws the icon left of the label (wide box) or above
  it (tall box; force with `icon_pos` left|top). Arrows still clip to the entity box.
- **Compartments**: panel `"shape":"membrane"` = horizontal lipid bilayer (receptor/channel icons
  inside it snap across the bilayer, extracellular part above; arrows arrive at the binding site and leave
  from the cytoplasmic domain), `"shape":"nucleus"` = ellipse with double envelope (name at the bottom),
  `"shape":"cell"` = rounded cell with a membrane outline.

**When to use an icon — only for an object the source names**, on the element that cites it (the
icon inherits the element's `source`). Never as decoration and never to suggest something the source
does not state: no microscope when the paper does not say how images were taken, no DNA icon on a
protein, no receptor drawn in the membrane unless the source puts it there, no plant/cell type the
paper does not study. An instrument of unstated type stays a labelled box with "not reported".
Concepts, variables, treatments and outcomes stay plain boxes. Icons never encode values and never
replace the label. Typical counts: mechanism 4–8, apparatus one per named instrument, schematic and
graphical abstract 2–4.

| group | icon names (aliases) |
|---|---|
| biology | `cell`, `nucleus`, `mitochondrion`, `receptor` (membrane_receptor), `channel` (ion_channel, transporter), `protein` (blob), `peptide`, `molecule` (small_molecule), `dna` (gene), `rna`, `virus`, `bacterium`, `antibody`, `plant` (seedling) |
| lab / apparatus | `flask`, `beaker`, `cuvette` (sample_cell, sample), `laser`, `lens`, `detector` (camera), `microscope`, `pump`, `tube` (pipe, tubing), `computer` (monitor), `database` (cylinder), `chip` (sensor) |
| generic | `document` (paper), `chart`, `gear` (process) — not scientific claims; `source:{"type":"design"}` is enough |

Checks: unknown name → FAIL `icon_unknown` (lists the valid names); a biology/lab icon whose element has
no material source → FAIL `icon_without_source`; a biology/lab icon whose object no material mentions
→ WARN `icon_not_named_in_materials` (remove it or choose the icon of the named object).
