你是独立复核子代理，与提取代理不是同一会话。当前工作目录是 D:\github-repository\gaokao-exam。只复核 entry_key `2026|零模|北师大实验中学|数学|`，不得处理其他试卷。

从 data/question_extraction/2026/pilot_tasks.json 读取该任务包。同时获得并实际读取：原始 PDF、output_dir/paper.json、assets 目录、docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md。

你是只读复核者：不得修改 paper.json、assets、原始附件、CSV、规范、Schema 或全局 status.json。可在系统临时目录渲染页面，运行 Python 读取原始附件时使用 `python -I`，不要全局安装依赖。

独立执行，不得依赖提取总结：
1. 逐页逐题对照 PDF 的题目覆盖、原始题号、大小问层级、选项、公式、跨页内容；检查叶子小问祖先链上下文和共享资源复用。
2. 原卷无答案，检查 answer 和 analysis 是否确实省略，不得出现 Agent 自编答案、解析、分值或难度。
3. 逐张实际打开图片，检查清晰度、裁剪、方向、公式与图中文字、归属；不能只检查文件存在。公式应准确转成 LaTeX，不能只靠截图。
4. 独立运行 Draft 2020-12 Schema 校验，并检查引用、相对路径、SHA-256、同级 ID。可运行 `python -I scripts/validate_exam_paper.py <paper.json路径> --schema docs/question-json-v1.schema.json`，但还要自行核对内容。
5. 返回明确结论 `accept` 或 `reject`。若 reject，列出具体 PDF 页码、题号、字段和原因；若 accept，说明实际对照和图片检查范围。不要只凭 Schema 通过而 accept。

最后用简洁中文返回结论、统计数据、Schema 结果、图片复核结果及具体问题。不要修改提取状态，不要粘回整份 JSON。