# Side-by-side cases (with / without a Skill)

Ten small cases we wrote from public arXiv papers to compare an agent **without** a Skill and the same agent
**with** one of the nine Skills in this repository, under the platform's budget wording (64 requests, 900 s).
They are our own test material, not the organiser's hidden cases. Each folder holds `task.md` (Chinese, like
the platform's tasks), `case.json` with an `output_contract`, and any non-paper inputs (CSV, SVG, PPTX/POTX
drafts we made). Download the paper PDF into the folder before running:

| folder | Skill | paper (put the PDF next to task.md) |
|---|---|---|
| `talk_transformer` | academic-talk-organization | arXiv:1706.03762 → `attention_is_all_you_need.pdf` |
| `paper2ppt_egnn` | paper-to-academic-ppt | arXiv:2102.09844 → `egnn_icml2021.pdf` |
| `template_transformer` | ppt-template-adaptation | arXiv:1706.03762 → `attention_is_all_you_need.pdf` |
| `pptedit_egnn` | ppt-generation-editing | arXiv:2102.09844 → `egnn_icml2021.pdf` |
| `chart_transformer_table2` | research-data-chart | arXiv:1706.03762 → `1706.03762.pdf` |
| `mechanism_dc` | bio-mechanism-pathway | arXiv:1607.07244 → `1607.07244.pdf` |
| `graphical_abstract_dc` | graphical-abstract | arXiv:1607.07244 → `1607.07244.pdf` |
| `schematic_dc_design` | research-schematic | arXiv:1607.07244 → `1607.07244.pdf` |
| `setup_route_dc` | apparatus-technical-route | arXiv:1607.07244 → `1607.07244.pdf` |
| `figure_edit_dc` | any figure Skill (edit mode) | arXiv:1607.07244 → `1607.07244.pdf` |

Run one arm:

```bash
./run_side_by_side.sh talk_transformer none            /tmp/run_A
./run_side_by_side.sh talk_transformer ../../academic-talk-organization /tmp/run_B
```

Then compare `/tmp/run_A/output` and `/tmp/run_B/output` (renders, result.json, the run's own reports).
Our observations (one run per arm, Claude Haiku 5.5 as the agent, 2026-11-25) are in each Skill's
`references/measured_gain.md`, section "Side-by-side runs with a real agent". They are qualitative: one run per
arm, a stand-in agent, judged by eye — repeat the runs to measure variance.
