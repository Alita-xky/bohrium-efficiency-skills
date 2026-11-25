# 任务：根据 Transformer 论文 Table 2 绘制对比图表

材料：`1706.03762.pdf`（Attention Is All You Need）与组织方转录的 `table2_transformer.csv`。

1. 左图：各模型在 newstest2014 EN-DE 与 EN-FR 上的 BLEU 分组条形图（10 个模型，名称较长，需保证可读）。
2. 右图：各模型训练成本（FLOPs），纵轴用对数刻度，EN-DE/EN-FR 分组；原文未报告的值不得补造，需在图中或图注中说明。
3. 坐标轴标注单位/量纲，图例清楚，突出 Transformer 两行。
4. 交付可编辑 SVG 及 PDF/PNG（300 dpi），result.json 中列出每个数值的来源（CSV 行列与原文 Table 2）。
