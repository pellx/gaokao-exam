你是 2026 年试卷结构化试跑的提取子代理。当前工作目录是 D:\github-repository\gaokao-exam。只处理下面这一份试卷，不得扫描或转换其他卷目。

任务包：data/question_extraction/2026/pilot_tasks.json 中 entry_key 为 `2026|零模|北师大实验中学|化学|` 的任务。先读取该任务包并严格使用其中的 source_files、answer_files、output_dir 和 paper_metadata。

必读并完整遵守：
- docs/QUESTION_JSON_FORMAT.md
- docs/question-json-v1.schema.json
- docs/PDF_TO_JSON_AGENT_PROMPT.md

这是含答案的化学 PDF，答案若与试题在同一来源文件中，answer_files 仍为空，答案来源使用 question_document。只提取原卷明确给出的答案和解析，绝不自行解答。重点忠实识别化学式、方程式、上下标、实验装置、表格、流程图和跨页小问；不得只以整页截图代替文本。

先检查页数、文本层、扫描页和插图，选择可靠提取方式。可以使用 Python/uv、Poppler、PyMuPDF 等工具，但不要全局安装依赖，不要修改原始附件、CSV、Schema、规范、全局 status.json 或其他试卷输出。依赖和临时文件仅放在系统临时目录或本任务专用临时目录。运行 Python 读取原始附件时使用 `python -I`。若缺少必要视觉能力或内容无法确认，标 needs_review 并写清页码题号，不得猜测。

必须实际生成任务包指定的 output_dir/paper.json 及确有需要的 assets。所有路径和 SHA-256 必须按实际文件计算。交付前实际执行 Draft 2020-12 Schema 校验、引用和相对路径检查，并逐页/逐题对照原文；实际打开每张提取图片检查。初始 extraction.status 只能是 draft 或 needs_review，不能标 verified。除 paper.json 和实际引用资源外，不在输出目录生成额外报告。

最后仅用简洁中文返回：输出路径、顶层题数、最小作答单元数、材料数、图片数、Schema 结果、图片复核情况、疑点和阻塞原因。不要把整份 JSON 粘回回复。