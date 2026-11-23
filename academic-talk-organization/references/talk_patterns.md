# Organising an academic talk: goal, audience, order, key messages, Q&A

The same study is told differently to a lab meeting (method and progress), a thesis committee (novelty and
boundaries), a review panel (conclusions and strength of evidence) or a conference room (one memorable result).
Audience, time, slide count, template and Q&A pressure decide **what goes on a slide, in what order, and what
single sentence each slide leaves behind**. Write these decisions down in `talk_plan.json`; deck_check verifies
the time budget, Q&A references and cuts, and writes `talk_plan.md`.

## 1. Goal → checkable target
Write the goal as an outcome you could test after the talk:
- "The committee accepts that the routing regulariser is new and necessary (ablation, Table 3)."
- "Collaborators understand the two risks for the next milestone (latency, spontaneous speech)."
Derive from it: slide count, time split, and how strong the conclusion wording may be.

## 2. Occasion patterns (default splits; the task's numbers always win)
| occasion | goal | order | time split (main part) | emphasis |
|---|---|---|---|---|
| lab / group meeting | method + progress + next steps | question → what was done → results so far → problems → plan | 15 % context, 35 % method, 35 % results, 15 % issues/plan | details, open problems, asks for help |
| thesis defence / proposal | novelty + rigour + boundaries | problem → gap → contributions → method → evidence per contribution → limits → summary | 15 / 15 / 25 / 35 / 10 | contribution ↔ evidence mapping, limitations stated first-hand |
| project / grant review | conclusions + strength of evidence | answer first (key result) → why it matters → evidence → risks → plan | 20 / 15 / 40 / 15 / 10 | evidence strength labels, risks, deliverables |
| conference talk (10–15 min) | one memorable result | hook → problem → idea → method sketch → 2–3 results → take-home | 15 / 15 / 25 / 35 / 10 | one figure per claim, minimal formulas |
| lightning / 3-5 min | interest | problem → idea → one result → take-home | 25 / 25 / 35 / 15 | 3–5 slides |
Pace: about one main slide per minute (complex result slides 1.5–2 min, title 15–30 s, take-home 45–60 s).
Backup slides are outside the time budget.

## 3. Audience adaptation
| audience | background share | terminology | evidence depth |
|---|---|---|---|
| same sub-field experts | 5–10 % | paper's terms without definitions | full tables, ablations, error bars |
| neighbouring field | 15–20 % | define each key term once (in notes or a one-line bullet) | main figure + one table |
| non-specialists / management | 25–30 % | plain words, one example | headline numbers with comparison point |
List the audience's likely doubts in `audience.likely_doubts` — each one gets a Q&A entry.

## 4. Narrative order and cuts
- Keep the problem → method → results → conclusion spine, but order inside it by the goal, not by the paper's
  table of contents (a review panel may get the answer first).
- Every slide has one `key_message` (≤ 20 words) and a `transition` sentence to the next slide.
- Record cuts: what from the paper is left out or moved to backup, and why (`cuts`). This makes the selection
  reviewable ("ablation on dataset C moved to backup S14: not needed for the defence goal").

## 5. Evidence strength (wording must match)
| label | meaning | wording |
|---|---|---|
| strong | direct result in a table/figure of the paper, with numbers | "X reduces Y by 3.5 points (Table 1)" |
| moderate | single setting, indirect measurement, or no variance reported | "in the 10 h setting, X lowers Y" |
| weak | stated by the authors without data in the paper, or speculation | "the authors suggest …" (notes or backup only) |
Never upgrade the wording beyond the label; note the label in the section plan.

## 6. Q&A preparation (≥ 3 entries; deck_check fails fewer)
Cover these question types, each with 2–3 answer points, fact ids and a backup slide where possible:
1. **mechanism** — why does it work? (method section, ablation)
2. **data** — how much / which data, how measured? (setup, tables)
3. **controls / baselines** — compared to what, fair? (baseline rows, settings)
4. **limits / generality** — where does it fail? (limitations, largest-data setting)
5. **cost** — time, memory, latency, parameters (if the paper reports it)
Answer pattern: one-sentence answer → the number with its source → "backup slide S14 shows …".
If the paper does not answer a likely question, say so in the plan ("not reported in the paper").

## 7. Speaker notes for a talk
Per slide: the key message in spoken form, what to point at, the transition, the timing, and an
"if asked" line. The builder appends `Key message / Transition / Time / If asked / Evidence` lines to the notes
from the spec fields, so the notes double as a script and as a Q&A crib.
