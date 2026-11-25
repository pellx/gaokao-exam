# 北京高考试卷库

根目录只保留 `raw/`（原始附件）、`parsed/`（处理结果）、`pipeline/`（当前方法）、`backup/`（历史档案）和 `Index.json`（总文件索引）。生成或校验索引：

```powershell
python -I pipeline/build_index.py
```

```powershell
python -I pipeline/build_index.py --check
```

## 默认识别方案：local-hybrid-v1

新提取和返修默认使用 **`local-hybrid-v1`**：重复的 OCR、版面检测和裁剪由本地 Python 在模型会话外完成，Agent 只负责语义切题、跨页关系、答案归属和 JSON 组装。数据契约仍为 `exam-questions/v1`，独立只读复核、Schema/路径/SHA-256 校验及实际读图证据要求保持不变。

```text
原卷哈希校验
  └─ PDF：210 dpi 原页渲染
       ├─ 可靠文本层：直接读取（有效字母数字 ≥ 80，异常字形比例 ≤ 2%）
       ├─ 其他页面：PP-OCRv5 server detection + recognition（轻量 ONNX）
       ├─ 配图：文本层页优先合适内嵌图框，其余使用 DocLayout-YOLO
       ├─ 表格：DocLayout-YOLO 只提供候选表框，单元格仍须读取和复核
       └─ 从原页裁剪候选图，记录页码、PDF 坐标、q_hint 和 SHA-256
            └─ 精简 agent_bundle.json → Agent 组装 paper.json
                 └─ 校验 → 独立会话逐页/逐图复核 → 必要返修 → verified
  └─ DOCX 主文件：agent_only，不伪称走过本地 OCR
```

`q_hint` 只是自动题号提示，不是最终配图归属；旋转页停用该提示。候选图也可能包含广告、二维码或装饰，Agent 必须打开原页和候选图核对后决定是否引用。本地 OCR 或模型校验失败会记录 `local/prepare.log` 并停止该卷，**不会静默退回高 token 的整卷 Agent OCR**。

### 实测依据

[小样本 Benchmark](../backup/benchmark/BENCHMARK.md) 覆盖 4 份试卷的 12 页：文本层优先、其余 PP-OCRv5 的正文字符正确率为 **98.10%**，扫描页为 **97.05%**，12 页合计 **39.25 秒**。Xiaomi-OCR-0 和 DocLayout-YOLO + PaddleOCR-VL-1.6 分别为 **94.43% / 759.01 秒**、**94.43% / 738.14 秒**，因此不作为默认整页识别器。内嵌图与 DocLayout-YOLO 的配图组合在样本中为 **9/9 找图并挂对题号**。

这些是抽样页结果，不是 284 卷端到端验收成绩。复杂公式、表格单元格、跨页题、答案和配图归属仍必须对照原卷复核；不能因为表框或行列形状正确就视为表格内容正确。

### 安装与启动

在 Windows 项目根目录安装固定版本依赖：

```powershell
python -m pip install -r pipeline/requirements-ocr.txt
```

准备固定 revision 的模型数据；脚本只下载 ONNX/字典，不下载或执行模型仓库 Python。已有文件会逐项校验大小和哈希，无需重复下载：

```powershell
python -I pipeline/fetch_ocr_models.py
```

也可从已验证的本地模型目录复制：

```powershell
python -I pipeline/fetch_ocr_models.py --copy-from <已验证模型目录>
```

模型位于 `pipeline/models/question_ocr/`，包括 PP-OCRv5 检测、识别、字典和 DocLayout-YOLO。文字 OCR 与版面检测在本机运行；语义提取和独立复核仍使用 [CONTROLLER.md](CONTROLLER.md) 中固定的 OpenCode 配置。

启动默认控制器：

```powershell
pipeline/native/run_2026_all.exe --parallel 3
```

默认并发为 **3**，每卷本地 OCR 为 **4 线程**，避免在 12 逻辑 CPU 上过度超订。当前清单共 284 卷：**282 个 PDF 主文件走 `pdf_local`，2 个 DOCX 主文件走 `agent_only`**。不要仅为了测试新管线直接覆盖已验收结果或启动整批处理。

### 运行产物、缓存与安全边界

每卷的 `pipeline/runs/<运行时间>/<序号>/local/` 包含：

- `agent_bundle.json`：给 Agent 的精简文本、页图路径、候选资源和复核提示；不包含逐行框及内部主副本路径。
- `source_bundle.json`：完整审计数据，包括路由、逐行结果、原卷/页图/候选哈希和统计。
- `pages/`：210 dpi 原页 PNG；`candidates/`：候选裁剪主副本；`prepare.log`：本地阶段日志。
- `prune_report.json`：提取后资源清理结果。

最终 `paper.json` 只引用输出目录 `assets/` 中确有需要的资源。来源哈希和管线版本匹配时可复用 OCR 缓存，并重新校验页图与候选主副本。已有同名但内容不同的最终资源不会被覆盖或误用；候选会改用带内容哈希的路径。

自动清理只删除**本阶段创建、未被 `paper.json` 引用且内容未改变**的候选资源；已有资源和 Agent 修改过的资源保留。已有 verified 结果不会因代码更新自动重写，已有草稿仍优先进入复核。提取 Agent 不能自标 verified，独立复核也不能修改结果。原始附件始终只读。

控制器参数、断点续跑和故障策略见 [CONTROLLER.md](CONTROLLER.md)，原生调度器见 [native/README.md](native/README.md)。

## 试卷目录规则

原始试卷统一放在 `raw/` 下，按“学科 → 年份 → 考试类型 → 试卷文件”整理：

```text
raw/
└── <学科>/
    └── <年份>/
        ├── 零模/
        ├── 一模/
        ├── 二模/
        ├── 三模/
        └── 真题/
```

例如：`raw/数学/2026/一模/math-2026-mock1-dongcheng.pdf`。

已处理的结果采用相同的“学科 → 年份 → 考试类型”顺序，并以标准化原卷完整文件名（含原扩展名）作为结果包目录名；目录内保留 `paper.json` 和关联的 `assets/`：

```text
parsed/
└── 数学/2026/一模/math-2026-mock1-dongcheng.pdf/
    ├── paper.json
    └── assets/
```

尚未生成 `paper.json` 的不完整处理结果，也会按对应原卷目录保留已有的 `assets/`，不视为已完成结果。

文件名使用小写 ASCII 英文字母、数字和连字符，格式为：

```text
[subject]-[year]-[paper-type]-[district].<extension>
```

- `subject` 使用英文科目名：语文 `chinese`、数学 `math`、英语 `english`、物理 `physics`、化学 `chemistry`、生物 `biology`、政治 `politics`、历史 `history`、地理 `geography`。
- `year` 使用四位年份。
- `paper-type` 使用英文类型标记：零模 `mock0`、一模 `mock1`、二模 `mock2`、三模 `mock3`、高考真题 `gaokao`。
- `district` 使用区县拼音全小写：东城 `dongcheng`、西城 `xicheng`、朝阳 `chaoyang`、海淀 `haidian`、丰台 `fengtai`、石景山 `shijingshan`、通州 `tongzhou`、顺义 `shunyi`、昌平 `changping`、大兴 `daxing`、房山 `fangshan`、门头沟 `mentougou`、怀柔 `huairou`、平谷 `pinggu`、密云 `miyun`、延庆 `yanqing`、燕山 `yanshan`。
- 保留原文件后缀，并统一为小写。

### 重名处理

基础格式相同但实际试卷不同的文件，在文件名末尾追加学校和/或变体的拼音标记，例如：

```text
chinese-2026-mock2-haidian-variant-chaloubuque.pdf
math-2026-mock3-xicheng-school-beishidaerfuzhong.pdf
```

若学校和变体标记仍不能区分同名文件，再追加 `copy-01`、`copy-02` 等序号。非重名文件不追加这些标记。该规则用于保留同一区县、学科和批次下的多份学校卷、补充卷及不同附件，不覆盖或丢弃原文件。

### 特殊类别

- 现有资料中包含零模，因此保留 `零模/` 目录，并在文件名中标为 `mock0`。
- `真题/` 目录用于高考真题，文件名类型标记为 `gaokao`。当前原始附件清单以 2024—2026 年模拟卷为主；没有真题文件时该目录可以暂不存在。
- 来源网页不是试卷附件，历史快照归档在 `backup/data/original_downloads/source_pages/`。

## 总索引 Index.json

`Index.json` 是整个仓库唯一的索引，由 `pipeline/build_index.py` 从磁盘现状生成，主要字段：

- `catalog`：827 条去重卷目名录，含年份、批次、区县/学校、科目、标题、题数、核查状态与来源 URL；原始数据在 `pipeline/manifests/papers_catalog.json`。
- `source_pages`：81 条来源网页记录，含页面文件名、来源 URL、大小与 SHA-256；原始数据在 `pipeline/manifests/source_pages.json`。网页快照本体在本地归档 `backup/data/original_downloads/source_pages/`，不入库。
- `papers`：已处理的 190 份结果，含 `paper.json` 路径、SHA-256、抽取状态（verified 110、needs_review 13、draft 67）和队列状态（verified 88、blocked 3、paused 10、pending 183）。
- `unprocessed_raw_files`：尚未处理的 829 份原卷。
- `files`：`raw/` 与 `parsed/` 下全部 4606 个文件的路径、大小和哈希。
- `errors`：索引时发现的问题，当前为 0 条。

## 清单与维护

原始附件按 `.gitignore` 只留在本地，不入库；不同工作副本的 `raw/` 内容可能不同，`Index.json` 的 `raw_files` / `unprocessed_raw_files` 以生成本索引时的副本为准。

`pipeline/manifests/files.csv` 记录当前工作副本的原卷实际路径；其中 81 条来源网页记录的 `local_path` 定位到根目录 `Index.json`，详情见其 `source_pages` 字段。题目 JSON、任务清单中的原卷引用应随文件移动同步更新。旧下载、目录重组和网页索引脚本仅作为历史档案保存在 `backup/scripts/`，不属于当前处理入口。后续 2026 年任务生成器会将结果写入 `parsed/` 目录结构。

控制器参数、断点续跑和故障策略见 [pipeline/README.md](pipeline/README.md)；原生调度器见 [pipeline/native/README.md](pipeline/native/README.md)。
