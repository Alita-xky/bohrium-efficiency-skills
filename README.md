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

License: MIT (see LICENSE).
