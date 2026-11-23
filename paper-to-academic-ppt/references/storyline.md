# From paper to storyline (paper2ppt)

A paper is continuous prose; a deck is a sequence of claims, each pinned to a figure, table, section or number.
The conversion is a re-structuring, not "cut paragraphs into lines".

## Step A — extract the main line (from paper_map.json + 3–5 targeted reads)
Fill these five slots, each with fact ids (verbatim quotes in facts.json):
1. **Problem** — what fails today and for whom (abstract sentence 1–2, intro paragraph 1).
2. **Gap / idea** — why existing approaches fail and the paper's key idea (intro, "we propose").
3. **Method** — the core mechanism in ≤ 3 steps; the overview figure (usually Fig. 1 or 2, kind `schematic`).
4. **Evidence** — the 2–4 results that support the stated contributions (main table, main figure, ablation).
5. **Limits + take-home** — limitations / discussion section, conclusion sentence with the headline number.
Separate **main claims** (in the abstract/contributions list) from **supporting details** (hyper-parameters,
extra datasets, secondary ablations) → details go to notes or backup slides.

## Step B — slide budget (fit the task's limit; default 10–15)
| paper type | title | motivation | method | setup | results | limits | conclusion | backup |
|---|---|---|---|---|---|---|---|---|
| experimental / method paper (12 slides) | 1 | 2 | 2–3 | 1 | 3–4 | 1 | 1 | 0–2 |
| theory paper (12) | 1 | 2 | 4 (setting, main theorem, proof idea, assumptions) | 0 | 2 (examples/bounds) | 1 | 1 | 0–2 |
| review / survey (12) | 1 | 1 | 1 (taxonomy) | 0 | 6 (one per theme, with the review's own table/figure) | 1 (open problems) | 1 | 0–1 |
| short talk (≤ 6) | 1 | 1 | 1 | 0 | 2 | 0 (in notes) | 1 | 0 |
Backup slides count toward the slide limit unless the task says otherwise; drop them first.

## Step C — page-level claims
For each slide write: role, claim title, evidence object (Fig./Table/data), 2–5 bullets, fact ids, notes.
- The claim must be literally supported by the quoted fact; do not strengthen it ("significantly",
  "state-of-the-art") unless the paper says so.
- Result slides: the number in the title must also be visible in the figure/table on the slide.
- Use the order of the paper's contributions; put the strongest evidence first within results.
- Keep the paper's figure numbering on slides; a figure you redraw from data is "Fig. N (redrawn from data)".

## Step D — what never goes on a slide
- Numbers you computed without declaring them in `derived`; numbers only seen in a figure image.
- Other papers' results presented as this paper's.
- Content only from the case labels (e.g., the domain tag) that the materials do not contain.
- Related-work lists (one sentence in notes is enough unless the task asks).

## Typical failure → fix
| failure | fix |
|---|---|
| deck follows section order and pastes abstract | rebuild from the five slots in Step A |
| "Fig. 4" on slide but the crop is Fig. 3 | take the bbox from paper_map (caption-anchored), check fig_candidates.png |
| method diagram used to "show" improvement | add the result table/figure; kind=schematic on the diagram |
| 9 bullets of 30 words | split into two slides or move details to notes |
| notes = bullets read aloud | notes say why, what to point at, transition |
