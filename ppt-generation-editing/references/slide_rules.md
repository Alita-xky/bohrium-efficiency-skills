# Slide rules (academic decks)

These rules are what deck_check / deck_render enforce or warn about. They exist because the common failure of
AI-made academic slides is not "no slides" but slides that cannot enter a real talk: pasted paragraphs, wrong
figure numbers, numbers that are rounded or invented, schematic figures shown as results, overflowing text,
broken templates and notes that repeat the slide.

## 1. One slide = one claim (assertion–evidence)
- The title is a full-sentence claim the audience should remember: "GateLite cuts WER by 3.5 points at 10 h",
  not "Results". Topic labels are allowed only for title, outline, section, Q&A and backup slides.
- The body is the evidence for that claim: a figure, a table, a chart or 2–5 short bullets.
- Keep titles to two lines (about 15 English words or 30 Chinese characters).

## 2. Density
- At most 5 bullets (6 tolerated), at most 2 levels, each a phrase of ≤ 12 words (≤ 25 Chinese characters).
- Never paste a paragraph. Rewrite to a phrase, keep the paper's terms, symbols, numbers and units unchanged.
- Body text ≥ 18 pt in the default template (builder shrinks to fit, never below 12 pt); captions and source
  notes 10–12 pt; table cells ≥ 10 pt — if a table needs less, split it or show the paper's table as an image.
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
- Formulas that exist only as images: crop them as images; do not retype them unless the text layer has them.

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

## 8. Language
Write slides in the task's language. For Chinese decks keep English technical terms, symbols and numbers exactly
as in the paper; the builder tags runs zh-CN and the template's East-Asian font is used (render report shows
which font was actually embedded in the PDF).
