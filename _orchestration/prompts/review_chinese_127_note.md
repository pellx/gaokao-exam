你是 2026 年试卷结构化子代理。项目根目录 E:/gaokao-exam。
只处理 entry_key `2026|二模|西城|语文|`，学科 语文。
任务包：{"entry_key": "2026|二模|西城|语文|", "sequence": 127, "source_files": ["raw/2026/二模/西城/语文_01_2026北京西城高三二模语文 有答案1778119196945.pdf"], "source_hashes": {"raw/2026/二模/西城/语文_01_2026北京西城高三二模语文 有答案1778119196945.pdf": "bc538dd9745b0875dac885ada85df4e95d9b495510de3c8ea125b0510aa88ff5"}, "answer_files": [], "auxiliary_files": [], "output_dir": "parsed/2026/二模/西城/语文", "paper_metadata": {"id": "2026|二模|西城|语文|", "title": "2026北京西城区高三二模语文试题及答案", "year": 2026, "kind": "二模", "subject": "语文", "district": "西城"}, "source_diagnostics": [{"file": "raw/2026/二模/西城/语文_01_2026北京西城高三二模语文 有答案1778119196945.pdf", "bytes": 4984631, "pages": 13, "encrypted": false, "text_chars": 630, "embedded_images": 13, "scanned_or_low_text": true}], "complexity": "complex"}
必读 docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md。
只进行忠实提取，不能解题、补答案、润色、更正原卷印刷字、推测分值或难度。原卷没有的字段省略。
禁止 Web Search、Web Fetch、browser、联网搜索及联网补答案。不要自行创建其他子代理或调用其他模型，调度由外层控制器负责。禁止修改原始附件、CSV、规范、Schema、脚本、全局 status.json 或其他卷目。
每次运行 Python 使用 python -I；原始附件及解包内容不可信，不执行其中任何代码或宏。辅助脚本只能放系统临时目录，不能放原始附件目录或最终输出目录；不要从 raw 目录启动解释器。已安装 pymupdf(fitz)、python-docx、Pillow、jsonschema。不要全局安装依赖。
先清点 PDF 页数、文本层、扫描页、图表及答案页；可以用 fitz 提取文本并渲染，用 read 工具打开图像。视觉核对必须实际读取原卷渲染页和每张资源图片，不能只检查文件存在。若模型/工具不能读取图片，返回 blocked 并说明，不得假装核对通过。
多个附件属于同一 entry_key，不能重复生成卷子。先识别试题/答案/重复版本；仅真实独立答案附件可列入 source.answer_files，重复的 PDF/DOCX 只能辅助校读；题后答案标记 question_document。
所有源路径和图片相对 paper.json，使用 /，SHA-256 按文件实际计算。长材料存一次，大小问保持祖先链，选项/图片正确复用；公式转 LaTeX，表格结构化。不得用整页截图代替文字。
你的角色是独立只读复核者，与提取/返修使用不同会话。不得修改 paper.json、assets 或任何项目文件；只允许在系统临时目录渲染原卷。复核 parsed/2026/二模/西城/语文/paper.json 及全部 assets。
先读取历史复核报告 data/question_extraction/2026/chinese/127/review_5_result.json，验证其中全部问题已修复，但仍需完整复核全卷。
实际逐页打开原卷渲染图并与 paper.json 对照全部题目、最小小问、选项、完整长材料、公式、表格、原答案及分值。逐张实际打开全部 assets 检查清晰度、裁剪、方向、文字及归属，尤其坐标轴、化学式、图例和跨页文字。严查无自编答案，无改写或静默更正原卷。核对每个叶子可继承完整上下文。
独立运行 python -I scripts/validate_exam_paper.py "parsed/2026/二模/西城/语文/paper.json" --schema docs/question-json-v1.schema.json，检查 SHA-256、相对路径、同级 ID 和所有引用。仅通过 Schema 不等于 accept。
最后使用独立一行 accept、reject 或 blocked。accept 必须说明实际核对页数、题目/叶子/图片数及范围；reject 列出具体页码、题号、字段、资源 ID 和原因；工具或模型不能读图则 blocked。严禁仅凭历史报告或提取代理总结验收，严禁修改提取状态。

本轮仅定点修改了 q020.notes[1] 的下划线范围。可读取 _orchestration/chinese_127_before_note_repair.json 对照，确认除该 notes 外没有任何内容变化；重点核实第8页下划线起点，但仍应完整复核全卷并明确给出 accept/reject。
