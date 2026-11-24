# 试卷题目 JSON 规范 v1

## 1. 目标与适用范围

本格式用于把一份 PDF 或 Word 试卷转换为**可检索、可追溯、可按最小题目单元使用**的 JSON 和图片。格式标识为 `exam-questions/v1`，机器校验规则见 [question-json-v1.schema.json](question-json-v1.schema.json)。

设计原则：

- 原卷只提取，不擅自解题、改写、翻译、补充知识点或推测难度。
- 长文章、共用题干、共用选项和图片只保存一次，通过引用复用。
- 保留原题号、大小问层级、原始答案和解析，不以最大连续题号猜测题目数量。
- 每道最小题目都能通过引用解析得到完整上下文；默认不另外保存展开后的重复文本。
- 原始文件不修改，结构化结果写入单独的 `parsed/` 目录。

## 2. 推荐目录与路径

```text
parsed/2026/零模/东城/北京二中/语文/
├── paper.json
└── assets/
    ├── a001.png
    └── a002.jpg
```

每份试卷一个 `paper.json`，图片等资源放在同目录的 `assets/` 中。JSON 使用 UTF-8、无 BOM；路径统一使用 `/`，不使用 Windows 绝对路径、URL 或 Base64 图片。

- `assets[*].path` 必须相对于 `paper.json`，且位于其同级 `assets/` 目录，例如 `assets/a001.png`。
- `source.file`、`source.answer_files[*].file` 也相对于 `paper.json`，可用 `../` 指向 `raw/` 中的原文件。
- 路径由程序根据实际位置计算，不能手工猜测。
- 同一试卷内部去重，不建立跨试卷的隐式文本依赖，以保证单份试卷目录可以独立移动。

## 3. 完整示例

下面仅演示结构，不是实际试卷提取结果。

```json
{
  "format": "exam-questions/v1",
  "paper": {
    "id": "demo|英语|阅读|",
    "title": "英语阅读与表达结构示例",
    "year": 2026,
    "kind": "示例",
    "subject": "英语",
    "district": "东城",
    "school": "示例学校"
  },
  "source": {
    "file": "example.pdf",
    "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
    "page_count": 2,
    "answer_files": [
      {
        "file": "example-answers.pdf",
        "sha256": "1111111111111111111111111111111111111111111111111111111111111111"
      }
    ]
  },
  "extraction": {
    "status": "draft",
    "tool": "结构示例，非真实提取"
  },
  "assets": {
    "a001": {
      "path": "assets/a001.png",
      "kind": "image",
      "page": 1
    }
  },
  "materials": {
    "m001": {
      "kind": "passage",
      "title": "Reading A",
      "content": [
        "This is the first paragraph of the shared article. It is stored only once.",
        "This is the second paragraph. Every related question refers to m001.",
        {
          "asset": "a001",
          "caption": "Figure 1"
        }
      ],
      "pages": [1]
    }
  },
  "questions": [
    {
      "id": "q021",
      "label": "21",
      "type": "single_choice",
      "materials": ["m001"],
      "stem": "What is the main idea of the passage?",
      "options": [
        {"key": "A", "content": "Option A"},
        {"key": "B", "content": "Option B"},
        {"key": "C", "content": "Option C"},
        {"key": "D", "content": "Option D"}
      ],
      "answer": {
        "source": "question_document",
        "choices": ["B"]
      },
      "analysis": "The original explanation is stored here, without repeating the answer label.",
      "score": 2,
      "pages": [1]
    },
    {
      "id": "q022",
      "label": "第二节",
      "type": "composite",
      "materials": ["m001"],
      "stem": "Answer the following questions according to the passage.",
      "parts": [
        {
          "id": "p1",
          "label": "（1）",
          "type": "short_answer",
          "stem": "What happened first?",
          "answer": {
            "source": "answer_document",
            "content": "The original answer."
          },
          "score": 2
        },
        {
          "id": "p2",
          "label": "（2）",
          "type": "short_answer",
          "stem": "Why did it happen?",
          "answer": {
            "source": "answer_document",
            "content": "The original reason."
          },
          "score": 3
        }
      ],
      "pages": [2]
    }
  ]
}
```

## 4. 顶层字段

| 字段 | 必需 | 用途 |
|---|---|---|
| `format` | 是 | 固定为 `exam-questions/v1`。 |
| `paper` | 是 | 试卷级元数据，只保存一次。`paper.id` 在本项目中使用现有 `entry_key`。 |
| `source` | 是 | 原卷路径、SHA-256、页数；可另列答案文件。 |
| `extraction` | 是 | 提取状态、工具及全局备注。 |
| `assets` | 否 | 图片、音频或附件资源表，键为资源 ID。 |
| `materials` | 否 | 长文章、共享材料、共用说明等，键为材料 ID。 |
| `option_sets` | 否 | 多题共用的选项组，键为选项组 ID。 |
| `questions` | 是 | 按原卷顺序排列的顶层题目数组。 |

`paper` 至少包含 `id`、`title`、`year`、`kind`、`subject`，可增加 `district`、`school`、`variant`、`academic_year` 和原卷明确印出的 `exam_date`。不要把索引中的题数统计、来源网页、下载状态重复写入每道题。

`extraction.status` 的含义：

- `draft`：机器初稿，尚未逐页复核。
- `needs_review`：存在无法辨认、映射不确定或文件异常，必须有相应 `notes`。
- `verified`：已逐题核对文本、层级、答案和配图；仅通过 Schema 校验不能标为 `verified`。

## 5. 内容表示：减少每个文本块的外壳

`content` 类型用于题干、材料、选项、答案、解析和评分细则：

- 只有一个普通文本块时，直接使用字符串。
- 多个段落、图片或表格混排时，使用数组。
- 数组中的字符串分别是一个段落，不必为每段再写 `{ "type": "text" }`。
- 文本使用原文字符、有限的 Markdown 行内格式和 LaTeX 公式。行内公式用 `$...$`，独立公式用 `$$...$$`。
- 填空下划线、选项符号、上下标、单位和题目要求必须保留。不得把答案回填到题干。
- 不把整段文字改写成 Markdown 图片链接；图片始终通过资源 ID 引用。

图片块：

```json
{"asset": "a001", "caption": "图1", "inline": false}
```

`caption` 和 `inline` 可省略。表格使用结构对象，单元格仍使用 `content`，可包含公式或图片：

```json
{
  "table": {
    "caption": "实验数据",
    "header": ["次数", "$U/\\mathrm{V}$", "$I/\\mathrm{A}$"],
    "rows": [["1", "1.5", "0.20"], ["2", "2.0", "0.28"]]
  }
}
```

## 6. 长文章、共用题干与小题

### 6.1 多道独立题共享文章

语文现代文阅读、英语阅读理解等，完整文章放在 `materials.m001.content`，每道题只写：

```json
"materials": ["m001"]
```

非连续性文本有多则材料时，可以按原顺序引用多个 ID：`["m001", "m002"]`。`materials` 可以是 `passage`、`instructions`、`data`、`situation`、`option_description`、`listening_transcript` 或 `other`。

### 6.2 一道大题包含多个小问

使用 `parts`，父节点保存共用题干、材料和分值，子节点只保存自己的内容：

```text
q016（父题：共同条件、材料、总题干）
├── p1（第（1）问：自己的题干、答案、解析）
└── p2（第（2）问：自己的题干、答案、解析）
```

有 `parts` 的容器使用 `type: "composite"`。`parts` 可以递归，以支持“（1）中的①、②”。独立编号的空、独立计分的小问或独立答案的小问，原则上拆到叶子节点；不要为了扁平化而复制父题干。

### 6.3 引用继承与最小题目单元

- 子节点继承所有祖先的 `materials` 和 `option_set`，自己的 `materials` 是追加引用，不是替换。
- 父级 `stem` 是子题的共同上下文；读取最小单元时按“根到叶”的顺序提供父级题干和叶子题干。
- 子节点自己的 `options` 优先于继承的 `option_set`；同一节点不能同时有 `options` 和 `option_set`。
- 节点路径为顶层 ID 与各层 part ID 的组合，例如 `paper.id#q016/p2`。ID 只需在同级节点中唯一，数组顺序表达原卷顺序。
- 对独立题目，完整单元为“试卷元数据＋引用材料＋本题内容”。对子题，还要加入祖先链上的共同题干和引用。

**引用展开是使用时的内存操作。** 默认不要再生成一份把所有文章复制进每题的 JSON，否则会失去去重的意义。

## 7. 选项与共享选项

普通选项：

```json
"options": [
  {"key": "A", "content": "选项内容"},
  {"key": "B", "content": ["第二段", {"asset": "a002"}]}
]
```

七选五、匹配题等多题共用同一组选项时，只在顶层保存一次：

```json
"option_sets": {
  "o001": [
    {"key": "A", "content": "Shared option A"},
    {"key": "B", "content": "Shared option B"}
  ]
}
```

相关题目写 `"option_set": "o001"`。不要将共用选项同时复制到每道题。

## 8. 题型、答案与解析

`type` 使用以下标准值：

- 选择：`single_choice`、`multiple_choice`；不定项归入后者。
- 判断、填空：`true_false`、`fill_blank`。
- 主观题：`short_answer`、`calculation`、`proof`、`translation`、`essay`、`drawing`。
- 匹配、排序：`matching`、`ordering`。
- 含小问的容器：`composite`；其他题型：`other`，可用 `type_original` 保留原题型名称。

完形填空、语法填空通常拆成 `fill_blank` 叶子；听力选择仍按答案形式记为 `single_choice`，其听力文本或音频通过材料和资源关联。实验探究等可按实际作答形式使用 `short_answer` 或 `composite`。

`answer` 必须标明来源：

| 来源 | 含义 |
|---|---|
| `question_document` | 当前试卷文件明确给出的答案。 |
| `answer_document` | `source.answer_files` 中对应答案文件给出的答案。 |
| `inferred` | 另行推理解答得到；默认转换任务禁止使用。 |

答案按内容选择字段：

- 选择：`choices: ["A"]` 或 `choices: ["A", "C"]`。
- 填空、计算、作文等：`content`。若同一道未编号填空题含多个空且不适合拆分，`content` 数组按空位顺序记录各空答案；有原小问编号或独立分值的空仍拆入 `parts`。
- 匹配：`pairs: [{"from": "1", "to": "A"}]`。
- 原卷明确给出分步评分时：`rubric: [{"content": "……", "score": 2}]`。

原解析放在 `analysis`，不要把“答案：B”等同一答案再次复制到解析开头，但不得删去实质推理过程。原卷没有答案或解析时，直接省略相应字段，不使用 `null`、空字符串或 Agent 自编答案。`score` 只记录原卷明确标出的分数；父题总分和子题分值可以分别保留。

## 9. 图片资源

每个资源在 `assets` 中登记一次：

```json
"a003": {
  "path": "assets/a003.png",
  "kind": "image",
  "page": 6,
  "bbox": [72, 180, 520, 410],
  "sha256": "实际文件的小写SHA-256"
}
```

- `page` 为从 1 开始的 PDF 页码；`bbox` 可选，顺序为 `[x0, y0, x1, y1]`，使用 PDF 点、页面左上角为原点。
- 线条图、电路图、函数图优先保存 PNG；照片可用 JPEG。优先保留源图清晰度，不进行无意义放大。
- 配图应包含完整坐标轴、图例、图中文字和必要标注，裁掉页眉、页脚及无关题目。
- 不得用整页截图代替文字识别。扫描卷可以先渲染页面用于 OCR 和定位，但最终仍应提取文字并裁剪具体配图。
- 同一图片被材料、题干、选项或解析多次使用时，只存一个资源 ID。
- 解析中独有的图也应保留，并在解析内容中的正确位置引用。

## 10. 缺省、质量记录与扩展

- 可选字段没有值就省略；不要写 `null`、空数组、空对象或无意义默认值。
- 无法确认的字符、跨页题干归属、答案对应关系等，在具体对象上设置 `needs_review: true` 和 `notes`，不能编造一个看似合理的值。
- 自定义扩展放入 `extensions`，核心字段不得另起同义字段。Schema 对核心对象禁止未定义字段。
- 路径使用小写扩展名；资源 ID 推荐 `a001`，材料 ID 推荐 `m001`，共享选项组推荐 `o001`，题 ID 推荐 `q001` 和 `p1`。
- 默认保留缩进以便复核；传输或存储时可另行压缩、Gzip，不必通过缩短字段名或省略必要文本来节省空间。

## 11. 交付前必须通过的检查

JSON Schema 负责结构校验，以下语义规则还需程序或 Agent 另行检查：

1. `format`、试卷元数据、源文件 SHA-256 正确，JSON 可解析且通过 Schema。
2. 同级题 ID 唯一，所有 `materials`、`option_set`、`asset` 引用都有对应定义。
3. 所有资源路径都是相对路径，文件实际存在且可读；图片已打开检查，路径以 `paper.json` 为基准解析。
4. 每道题都能沿祖先链解析出完整上下文，没有孤立的文章、无法对应的答案或丢失的小问。
5. 逐页核对题目覆盖范围，题号不连续、跨页题、材料与配图归属均按原卷处理。
6. 长文章全文只存一次；没有为了展开单题而重复存储，也没有为省空间删去正文。
7. 未将 Agent 推理解答冒充原答案，未把 OCR 猜测写成确定文本。
8. 仅在完成上述内容和图片复核后，才把 `extraction.status` 设为 `verified`。
