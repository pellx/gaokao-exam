你是化学卷返修后的独立复核子代理，必须与提取和返修会话不同。当前工作目录是 D:\github-repository\gaokao-exam。只复核 entry_key `2026|零模|北师大实验中学|化学|`，不得处理其他试卷。

读取 data/question_extraction/2026/pilot_tasks.json 中本卷任务包、原始 PDF、当前 output_dir/paper.json、全部 assets、docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md，以及上一轮 data/question_extraction/2026/review_chemistry_result.txt。

你是只读复核者：不得修改任何提取结果、原始附件、CSV、规范、Schema 或全局 status.json。可在系统临时目录渲染页面；Python 读取原始附件使用 `python -I`，不要全局安装依赖。

先逐项验证上一轮 reject 报告的所有 8 类内容问题和全部裁剪问题是否真正修复，包括 Q9/Q10 设问、Q15(2) 原文及祖先链、Q19(3) 父题干、章节说明和小标题、Q18(4) 的 `=`、Q7 选项、Q19 表格内容、Q16(2)① 可检索答案，以及所有被点名的图片。然后独立逐页对照全部 13 页、19 个顶层题和 48 个叶子，不要只检查旧问题。

实际打开返修后的每张图片，检查完整性、清晰度、方向、文字、归属和相邻内容；独立运行 Draft 2020-12 Schema 校验及引用、路径、SHA-256、同级 ID 检查。可运行 `python -I scripts/validate_exam_paper.py <paper.json路径> --schema docs/question-json-v1.schema.json`，但不能只凭 Schema 验收。

返回明确结论 `accept` 或 `reject`。reject 时列出具体页码、题号、字段、资源 ID 和原因；accept 时说明全部页面和图片的实际核对范围。最后用简洁中文返回统计和结论，不要修改提取状态，不要粘回整份 JSON。