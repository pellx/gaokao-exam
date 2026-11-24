你是原提取代理，现对 entry_key `2026|零模|北师大实验中学|化学|` 进行第 1 次返修。当前工作目录是 D:\github-repository\gaokao-exam。只处理该卷，只能修改任务包指定的 output_dir 内 paper.json 和 assets，不得修改其他试卷、原始附件、CSV、规范、Schema 或全局 status.json。

先重新读取：
- data/question_extraction/2026/pilot_tasks.json 中本卷任务包
- docs/QUESTION_JSON_FORMAT.md
- docs/question-json-v1.schema.json
- docs/PDF_TO_JSON_AGENT_PROMPT.md
- data/question_extraction/2026/review_chemistry_result.txt（独立复核的完整 reject 报告）
- 原始 PDF、当前 paper.json 和全部 assets

必须逐项修复复核报告中的 8 类问题，不得只修复摘要：
1. 补回 p3 Q9、Q10 的关键设问。
2. 按 p6 原卷恢复 Q15(2) 父题及①②的原文和祖先层级，删除改写/自编文字。
3. 补回 p10 Q19(3) 父节点原题干。
4. 恢复 Q16 小标题、Q18“I. 硫酸化”、Q12 完整题干及第一/第二部分说明；共享说明按规范存 materials 并引用，不要无意义重复。
5. 将 Q18(4) 答案中的符号恢复为原卷的 `=`，不得把 ⇌ 当作原文；保留原卷电荷不平衡的 needs_review 说明，但不得再声称错误符号是按原样录入。
6. 按原卷恢复 Q7 的 A–D 选项及表格关系，使 single_choice 答案可直接对应选项。
7. 忠实恢复 Q19 表格的合并语义、操作文字和试管图引用；Schema 没有合并单元格字段时，可在不丢失内容的前提下展开各行，或在 table.caption/相关题干中明确原表合并关系，不得用“—”代替原有内容。
8. 将 Q16(2)① 的离子方程式转写为可检索文本/LaTeX，图片只作辅助。

同时逐张重新裁剪并打开检查报告列出的图片，尤其 a001、a002、a013、a012、a014、a015、a016、a017、a019、a020、a026、a021–a024、a031–a033。完整保留坐标轴、标题、图例、化学结构顶点、装置和标注，去掉相邻题干及残字；更新受影响资源的 SHA-256 和必要 bbox。不得通过删除图片、题目、材料或答案来让校验通过。

运行 Python 读取原始附件时使用 `python -I`，不要全局安装依赖。临时文件仅放系统临时目录。修复后重新逐页对照全部 19 题和 48 个叶子，并实际打开全部 33 张或修复后实际数量的图片；独立复核前 extraction.status 只能为 draft 或 needs_review，不得标 verified。重新执行 Draft 2020-12 Schema、引用、相对路径和 SHA-256 校验。

最后简洁返回：修复项、输出路径、顶层题数、叶子数、材料数、图片数、Schema 结果、逐图检查情况、仍需人工处理的具体页码题号。不要粘回整份 JSON。