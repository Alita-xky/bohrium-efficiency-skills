# Bohrium efficiency-track skills

Agent Skills written for the Bohrium AI product weekly contest (efficiency track, round 3).
Each folder is a self-contained Skill (`SKILL.md` + scripts + templates + docs + a synthetic example).

| Folder | Challenge | What the Skill does |
|---|---|---|
| [`graphical-abstract/`](graphical-abstract/) | Graphical Abstract（图形摘要） | Source-bound graphical abstracts: evidence table with page/paragraph locators → JSON figure spec → audited, layered editable SVG + PDF/PNG, element-to-source map, contract-complete result.json; source-preserving SVG edits. |
| [`research-schematic/`](research-schematic/) | 科研示意图生成与编辑 | Relationship schematics: entities + quoted relations, category legend, source-preserving SVG edits. |
| [`research-data-chart/`](research-data-chart/) | 科研数据图表生成与编辑 | Charts bound to the case's own data (CSV/xlsx/table copies), units, n and error type checked, grouped bars/log axes. |
| [`bio-mechanism-pathway/`](bio-mechanism-pathway/) | 生物机制图与通路图 | Compartmentalised mechanism/pathway figures; every activation/inhibition/conversion arrow backed by a verbatim quote. |
| [`apparatus-technical-route/`](apparatus-technical-route/) | 实验装置与技术路线图 | Apparatus diagrams and ordered technical routes; conditions only when reported, order check. |
| [`paper-to-academic-ppt/`](paper-to-academic-ppt/) | 论文转学术 PPT | Paper → evidence-mapped deck: assertion titles, per-slide sources, figures cropped from the paper, charts/tables from its data, speaker notes, rendered previews. |
| [`academic-talk-organization/`](academic-talk-organization/) | 学术汇报内容组织 | Talk storyline with time budget, key messages, transitions and Q&A prep, plus the evidence-mapped deck. |
| [`ppt-template-adaptation/`](ppt-template-adaptation/) | 学术 PPT 模板与格式适配 | Maps content into a provided template's real layouts/placeholders, fixes format violations, compliance audit. |
| [`ppt-generation-editing/`](ppt-generation-editing/) | 学术 PPT 生成与编辑 | Source-preserving PPTX edits (text, slides, fonts, theme) with before/after log; untouched parts byte-identical. |

License: MIT (see LICENSE).

## Side-by-side cases
`benchmarks/side_by_side/` holds ten cases (task + contract, papers by arXiv link) and a one-command script to run an agent with and without any of these Skills; our observations are in each Skill's `references/measured_gain.md`.
