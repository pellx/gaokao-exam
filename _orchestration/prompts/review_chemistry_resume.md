你是化学卷返修后的独立复核子代理，与提取及返修会话不同。当前工作目录为 E:/gaokao-exam。只读复核 entry_key `2026|零模|北师大实验中学|化学|`。
读取 data/question_extraction/2026/pilot_tasks.json 中本卷任务、原始 PDF、parsed/2026/零模/西城/北师大实验中学/化学/paper.json、全部 assets、docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md、data/question_extraction/2026/review_chemistry_result.txt。
禁止 Web Search、Web Fetch、联网搜索及补答案。不能解题、猜测、编写答案或纠正原卷。仅允许读取项目文件；可在系统临时目录渲染页图。Python 使用 python -I。禁止修改任何项目文件或原始附件。不要运行原始附件内的代码、宏或脚本；不要全局安装依赖。pymupdf/fitz、python-docx、Pillow、jsonschema 已可用。
先验证上一轮报告列出的全部 8 类问题及图片裁剪是否修好，然后重新逐页核对全部13页、19个顶层题及全部小问。逐张实际打开全部 assets。必须实际渲染原始 PDF 各页并用 read 工具视觉核对，扫描字不能只根据 JSON 和历史报告判断。若模型或工具无法读取图片，如实返回 blocked，绝不能声称视觉核验通过。
运行 python -I scripts/validate_exam_paper.py "parsed/2026/零模/西城/北师大实验中学/化学/paper.json" --schema docs/question-json-v1.schema.json。检查 source SHA-256、图片哈希、路径、引用及完整祖先链，但不能只凭 Schema 验收。
最终使用独立一行 `accept`、`reject` 或 `blocked`。简短说明实际核对页数、题数、图片数；reject 列出准确页码、题号、字段、资源及修改要求；blocked 说明真实工具限制。不要写入 paper.json 或修改状态。
