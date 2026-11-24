"""Prepare raw Chinese exam PDFs for ancient-poetry recitation extraction."""

import hashlib
import json
import re
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "raw"
WORK = ROOT / "data/chinese_recitation/work"
TEXTS = WORK / "texts"
PROMPTS = WORK / "prompts"
RESULTS = WORK / "results"
EXAM_ORDER = {"零模": 0, "一模": 1, "二模": 2, "三模": 3}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sort_key(path):
    relative = path.relative_to(RAW)
    parts = relative.parts
    year = int(parts[0]) if parts and parts[0].isdigit() else 0
    return year, EXAM_ORDER.get(parts[1] if len(parts) > 1 else "", 99), str(relative)


def paper_metadata(relative_parent, variant):
    parts = relative_parent.parts
    year = int(parts[0])
    kind = parts[1] if len(parts) > 1 else ""
    district = parts[2] if len(parts) > 2 else ""
    school = parts[3] if len(parts) > 3 else None
    label = school or district
    suffix = variant.removeprefix("语文")
    title = f"{year}北京{label}高三{kind}语文{suffix}"
    return {
        "year": year,
        "kind": kind,
        "district": district,
        "school": school,
        "variant": suffix or None,
        "title": title,
    }


def build_groups():
    candidates = []
    excluded = []
    for path in sorted(RAW.rglob("*.pdf"), key=sort_key):
        if "语文" not in path.name:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if "初三" in path.name:
            excluded.append({"file": relative, "reason": "文件名标明为初三试卷，非高三模拟卷"})
            continue
        candidates.append(path)

    hashes = {}
    groups = []
    by_key = {}
    duplicates = []
    for path in candidates:
        digest = sha256(path)
        relative = path.relative_to(ROOT).as_posix()
        if digest in hashes:
            canonical = hashes[digest]
            canonical["duplicate_files"].append(relative)
            duplicates.append({"file": relative, "duplicate_of": canonical["source_files"][0]})
            continue

        variant = path.name.split("_", 1)[0]
        parent = path.parent.relative_to(RAW)
        key = (str(parent), variant)
        if key not in by_key:
            paper_id = f"P{len(groups) + 1:03d}"
            group = {
                "paper_id": paper_id,
                **paper_metadata(parent, variant),
                "source_files": [],
                "duplicate_files": [],
                "documents": [],
            }
            groups.append(group)
            by_key[key] = group
        group = by_key[key]
        group["source_files"].append(relative)
        hashes[digest] = group
    return groups, excluded, duplicates


def extract_documents(groups):
    for group in groups:
        text_dir = TEXTS / group["paper_id"]
        text_dir.mkdir(parents=True, exist_ok=True)
        for index, source in enumerate(group["source_files"], 1):
            source_path = ROOT / source
            text_path = text_dir / f"{index:02d}.txt"
            page_texts = []
            low_text_pages = []
            with fitz.open(source_path) as document:
                page_count = len(document)
                for page_number, page in enumerate(document, 1):
                    text = page.get_text("text", sort=True).replace("\x00", "")
                    page_texts.append(f"===== PDF 第 {page_number} 页 =====\n{text.strip()}\n")
                    if len(re.sub(r"\s+", "", text)) < 40:
                        low_text_pages.append(page_number)
            text_path.write_text("\n".join(page_texts), encoding="utf-8")
            text_chars = len(re.sub(r"\s+", "", "".join(page_texts)))
            group["documents"].append(
                {
                    "source_file": source,
                    "text_file": text_path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(source_path),
                    "page_count": page_count,
                    "text_chars": text_chars,
                    "low_text_pages": low_text_pages,
                }
            )
        group["needs_visual_check"] = any(
            document["text_chars"] < 1500 or document["low_text_pages"]
            for document in group["documents"]
        )


def prompt_for(group):
    output = (RESULTS / f"{group['paper_id']}.json").relative_to(ROOT).as_posix()
    documents = json.dumps(group["documents"], ensure_ascii=False, indent=2)
    metadata = {
        key: group[key]
        for key in ("paper_id", "title", "year", "kind", "district", "school", "variant")
    }
    return f"""你是语文“古诗文名篇名句默写”提取子代理。工作目录是 {ROOT}。
只处理 {group['paper_id']}，不得读取或修改其他试卷的结果，不得修改 raw、scripts 或本任务以外的文件。
禁止 Web Search、Web Fetch 和其他联网行为。

试卷元数据：{json.dumps(metadata, ensure_ascii=False)}
PDF 与已提取文本（含明确的 PDF 页码分隔符）：
{documents}

目标：只提取试卷中的古诗文/名篇名句默写题，包括直接默写、理解性情境默写，以及明确要求填写古诗文原句的小题。
不要把文言文阅读、诗歌鉴赏、翻译、现代文填空、《红楼梦》或一般名著阅读题混入。

操作要求：
1. 完整阅读该卷所有文本，分别定位试题和参考答案，不能只看关键词所在的一页。注意同一试卷可能有试题册、教师版、答案册多个 PDF。
2. 本任务所用模型具备识图能力。若文本层缺失、乱码或关键内容不确定，必须用原 PDF 渲染图片并使用 Read 工具实际打开图片识图，不能只依赖 OCR 或文本层。documents 中 low_text_pages 列出的页面必须逐页检查，先定位默写题和对应答案，再高清核对相关页。可用 uv 在临时环境运行 PyMuPDF 渲染；运行读取 PDF 的 Python 时必须使用 python -I。不得凭常识补全看不清的题干或答案。
3. 忠实保留总题号、总题干、每个小问的完整语境和横线（横线统一写作 ______）、答案原句及 PDF 页码。答案数组按横线顺序排列；一句答案含逗号、句号时仍放在同一个 text 中，不拆散。
4. 篇名和作者只记录原卷明确给出或答案明确对应的信息；无法确定时填 null，禁止联网查或猜测。
5. 原卷没有答案时 answers 为 []，answer_status 为 missing；不得自行解题。答案不确定时在 notes 说明，并将 status 设为 needs_review。
6. 同一小问有多个空时，answers 中逐空记录；若答案把两空合并给出，保持原答案并注明。原卷明确允许其他合理答案时保留原说明。
7. 输出必须是合法 UTF-8 JSON，不得输出 Markdown 代码围栏。实际写入 {output}，结构如下：
{{
  "paper_id": "{group['paper_id']}",
  "status": "ok | needs_review | not_found",
  "questions": [
    {{
      "number": "原题号，字符串",
      "instruction": "默写大题的总题干",
      "question_pages": [1],
      "items": [
        {{
          "label": "（1）或①等原标记，无标记则为 null",
          "context": "该小问完整原文，含 ______",
          "works": [{{"title": "篇名或 null", "author": "作者或 null"}}],
          "answers": [{{"text": "该空标准答案原句", "work_title": "篇名或 null", "author": "作者或 null"}}],
          "answer_status": "provided | missing | uncertain",
          "answer_pages": [10]
        }}
      ]
    }}
  ],
  "notes": ["文本、答案或版本的疑点；无疑点则为空数组"]
}}
若完整检查后确实没有默写题，status 为 not_found，questions 为 []，notes 说明检查范围。
8. 写完后重新读取 JSON，检查题号、小问数、答案数和所有必填字段。最后仅回复输出路径、默写大题数、小问数、答案缺失数与疑点，不要粘回整份 JSON。
"""


def main():
    RESULTS.mkdir(parents=True, exist_ok=True)
    PROMPTS.mkdir(parents=True, exist_ok=True)
    groups, excluded, duplicates = build_groups()
    extract_documents(groups)
    for group in groups:
        prompt_path = PROMPTS / f"{group['paper_id']}.md"
        prompt_path.write_text(prompt_for(group), encoding="utf-8")
        group["prompt_file"] = prompt_path.relative_to(ROOT).as_posix()
        group["result_file"] = (RESULTS / f"{group['paper_id']}.json").relative_to(ROOT).as_posix()
    inventory = {
        "source_root": "raw",
        "scope": "文件名含“语文”的高三 PDF；排除文件名明确标注初三的试卷；按内容 SHA-256 去除完全重复文件",
        "pdf_count": sum(len(group["source_files"]) + len(group["duplicate_files"]) for group in groups) + len(excluded),
        "paper_count": len(groups),
        "unique_pdf_count": sum(len(group["source_files"]) for group in groups),
        "excluded_files": excluded,
        "duplicate_files": duplicates,
        "papers": groups,
    }
    write_json(WORK / "inventory.json", inventory)
    print(json.dumps({
        "papers": len(groups),
        "unique_pdfs": inventory["unique_pdf_count"],
        "excluded": len(excluded),
        "duplicates": len(duplicates),
        "needs_visual_check": sum(group["needs_visual_check"] for group in groups),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
