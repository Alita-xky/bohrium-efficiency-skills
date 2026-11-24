# Slide rules (academic decks)

These rules are what deck_check / deck_render enforce or warn about. They exist because the common failure of
AI-made academic slides is not "no slides" but slides that cannot enter a real talk: pasted paragraphs, wrong
figure numbers, numbers that are rounded or invented, schematic figures shown as results, overflowing text,
broken templates and notes that repeat the slide.

## 1. One slide = one claim (assertion–evidence)
- The title is a full-sentence claim the audience should remember: "GateLite cuts WER by 3.5 points at 10 h",
  not "Results". Topic labels are allowed only for title, outline, section, Q&A and backup slides.
- The body is the evidence for that claim: a figure, a table, a chart or 2–5 short bullets.
- Keep titles short (about 12 English words or 25 Chinese characters, at most two lines): all content-slide titles
  share ONE size (the template/theme size; one step smaller for the whole deck only if a title needs 3 lines).

## 2. Density
- At most 5 bullets (6 tolerated), at most 2 levels, each a phrase of ≤ 12 words (≤ 25 Chinese characters).
- Never paste a paragraph. Rewrite to a phrase, keep the paper's terms, symbols, numbers and units unchanged.
- Type scale (deck-wide, written to `type_scale.json`): body text on a fixed 3-step ladder (default theme
  28/24/20 pt; with a template its body size and two steps, e.g. 32/28/24) — a slide that needs smaller text takes
  the next step, sub-bullets one step below; display 72/54/44 (big numbers), labels 16, captions 12, source notes
  10, tables 24/20/16/14/12/10. Smaller sizes only to avoid overflow, and `deck_check scale` warns on them.
  7–12 short bullets flow into two columns instead of a split; if a table needs < 10 pt, split it or show the crop.
- One idea per slide: if fitting needs < 16 pt, split the slide instead.

## 3. Numbers, units, names
- Every number on a slide must be in the materials (paper text, tables, CSV). Copy it with its unit and precision:
  27.9% stays 27.9%, not "28%" or "0.279". Computed numbers go into `derived` with the formula.
- Use the paper's method / dataset / metric names; define an abbreviation once.
- Say whose result it is: baselines are other people's results; "ours" only for the paper's own method.

## 4. Figures and tables
- Show the paper's own figure (cropped from the rendered PDF page) with its original number: "Fig. 3 (p.6)".
  The figure number on the slide must equal the paper's caption number — never renumber.
- Mark the figure kind: `data` (plots, measured images), `schematic` (method diagrams, illustrations),
  `photo`, `table_image`. A schematic is never evidence for a numeric result.
- Re-draw a chart only from data files given in the inputs (CSV/XLSX) and label it "redrawn from data";
  never re-draw from numbers read off a figure.
- Tables: copy cells exactly; highlight the row/column the claim is about; keep units in the header.
- Low resolution (< 96 px per inch on the slide) is reported; prefer a higher-dpi crop.
- Formulas: show the paper's display equation as its crop (`paper_map.json` → `equations`, ref "Eq. N"); the text
  layer of a PDF garbles math (`kxi − xj k2` for ‖x_i − x_j‖²), so never copy a formula from `materials/*.txt`. Short
  inline symbols may be typed with `^`/`_` markup (`ε_HOMO`, `O(n^2 · d)`, `2.3 · 10^19`).
- Scientific notation: write `2.3 · 10^19` (rendered as a superscript); deck_check compares it as the single value
  2.3e19 with the PDF's flattened `2.3 · 1019`, so a wrong exponent fails.

## 5. Story order
Paper talks: title → motivation/problem → (gap/idea) → method (1–3 slides) → setup → results (2–4 slides,
strongest first or in the paper's logical order) → limitations/discussion → conclusion/take-home → (Q&A,
backup). The main line follows the paper's contributions, not its section order: results that support no
contribution go to backup.

## 6. Speaker notes
2–4 sentences per slide: why the slide matters, what to point at, the transition to the next slide, and the
caveat if asked. Do not repeat the bullets (deck_check warns when > 80 % of the note words are on the slide).
The builder appends key message / transition / time / evidence lines when the spec gives them.

## 7. Visual consistency
- Use the template's placeholders: titles in the title placeholder, text in body placeholders — never text boxes
  imitating them. Keep the template's fonts, colours, footers and slide numbers.
- One emphasis colour (theme accent 2 via `**…**`); colour-blind-safe theme accents for charts.
- Every content slide gets a small source note ("Source: Fig. 3 (p.6); Sec. 4.2") at the bottom; footer and
  slide number come from the layout's placeholders.

## 8. Layout engine (automatic; the spec stays the same)
Default theme (no template): 1 bullet → statement panel; 2–4 short bullets → a row of cards; a results /
conclusion bullet with one number with a unit or % (27.9%, 3.5×, 28.4 BLEU) → big-number card (copied from the
bullet, never computed; setup and motivation numbers stay plain); outline/agenda role → numbered agenda;
section role → divider with its number; text + figure → two columns, the figure ≥ 55 % of the width and the full
height (wide figures go full width above the text); white figure margins are cropped (display crop, never
stretched); charts: bars from 0, value labels, the key series ("ours" / the method in the title) highlighted.
With a supplied template its fonts, colours, master and layouts win: body text stays in the layout's body
placeholder (moved next to a figure when needed), sizes follow the type scale (inherited where they match),
7–12 bullets flow into its two-content layout; cards/quote only where asked and only on a layout without a body
placeholder, in its theme colours; a takeaway becomes the last bold paragraph of the body placeholder.
Optional slide fields (use when they help, never required):
- `"layout": "cards"` — each top-level bullet becomes a card ("Head: text" → bold head + text).
- `"layout": "bignumber"` — cards with the key number of each bullet enlarged (`{"text", "number": "27.9%"}` picks it).
- `"layout": "two_column"` — `left` / `right` columns (text, figure, chart or table).
- `"role": "section"` — section divider (title + optional subtitle); `"role": "outline"` — numbered agenda.
- `"layout": "quote"` — `"quote"` (verbatim sentence) + `"quote_src"` ("Sec. 4 (p.3)") as a large pull quote.
- `"takeaway": "…"` — one-sentence accent bar at the bottom (also a `Takeaway: …` bullet, or `key_message` on
  results/analysis/conclusion slides); its numbers are checked like bullets; `"takeaway": false` turns it off.
deck_render warns when content fills < 50 % of the area between title and footer, body text < 14 pt, or a
native bar chart's axis does not start at 0; `deck_check scale` warns on sizes off `type_scale.json`.

## 9. Language
Write slides in the task's language. For Chinese decks keep English technical terms, symbols and numbers exactly
as in the paper; the builder tags runs zh-CN and the template's East-Asian font is used (render report shows
which font was actually embedded in the PDF).
