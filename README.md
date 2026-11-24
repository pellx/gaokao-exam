# 北京高考试卷库

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
- 来源网页不是试卷附件，单独保存在 `data/original_downloads/source_pages/`。

## 清单与维护

`data/original_downloads/files.csv` 记录原卷和来源网页的实际路径；题目 JSON、任务清单中的原卷引用也应随文件移动同步更新。后续运行 `scripts/download_originals.py` 会沿用本目录和文件命名规则。原卷重组脚本 `scripts/reorganize_exam_files.py` 和已处理结果重组脚本 `scripts/reorganize_parsed_outputs.py` 均默认只预览，传入 `--apply` 才会执行移动；对应映射保存在各自的数据清单中。后续 2026 年任务生成器也会将结果写入新的 `parsed/` 目录结构。
