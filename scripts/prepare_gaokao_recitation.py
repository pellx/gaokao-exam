"""Prepare five Beijing gaokao recitation extraction tasks from verified public sources."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/gaokao_beijing_recitation/work"
MODEL = "opencode-go/deepseek-v4.1-flash#high"


def doc(role, path, pages, url):
    return {"role": role, "path": path, "pages": pages, "source_url": url}


PAPERS = [
    {
        "paper_id": "G2022",
        "title": "2022年普通高等学校招生全国统一考试（北京卷）语文",
        "year": 2022,
        "source_files": [
            "data/gaokao_beijing_recitation/work/documents/2022/questions.docx",
            "data/gaokao_beijing_recitation/work/images/2022/page_05.png",
            "data/gaokao_beijing_recitation/work/pages/2022/source.html",
        ],
        "source_urls": [
            {"role": "question_page_images", "url": "https://gaokao.eol.cn/shiti/yw/202303/t20230314_2324023.shtml"},
            {"role": "question_docx", "url": "https://gaokao.eol.cn/shiti/yw/202303/W020230314407715432528.docx"},
            {"role": "question_and_answer_text", "url": "https://www.e-chinaedu.cn/html/kaoshi/2023/gaokao_0512/102743.shtml"},
        ],
        "documents": [
            doc("question_image", "data/gaokao_beijing_recitation/work/images/2022/page_05.png", [5], "https://gaokao.eol.cn/shiti/yw/202303/t20230314_2324023.shtml"),
            doc("answer_text", "data/gaokao_beijing_recitation/work/pages/2022/article.txt", [], "https://www.e-chinaedu.cn/html/kaoshi/2023/gaokao_0512/102743.shtml"),
            doc("supplementary_question_text", "data/gaokao_beijing_recitation/work/documents/2022/questions.txt", [], "https://gaokao.eol.cn/shiti/yw/202303/W020230314407715432528.docx"),
        ],
    },
    {
        "paper_id": "G2023",
        "title": "2023年普通高等学校招生全国统一考试（北京卷）语文",
        "year": 2023,
        "source_files": [
            "data/gaokao_beijing_recitation/work/images/2023/yw06.png",
            "data/gaokao_beijing_recitation/work/images/2023/yw10.png",
            "data/gaokao_beijing_recitation/work/pages/2023/source.html",
        ],
        "source_urls": [
            {"role": "question_and_answer_images", "url": "https://gaokao.eol.cn/shiti/yw/202309/t20230907_2459971.shtml"},
        ],
        "documents": [
            doc("question_image", "data/gaokao_beijing_recitation/work/images/2023/yw06.png", [6], "https://gaokao.eol.cn/shiti/yw/202309/t20230907_2459971.shtml"),
            doc("answer_image", "data/gaokao_beijing_recitation/work/images/2023/yw10.png", [10], "https://gaokao.eol.cn/shiti/yw/202309/t20230907_2459971.shtml"),
        ],
    },
    {
        "paper_id": "G2024",
        "title": "2024年普通高等学校招生全国统一考试（北京卷）语文",
        "year": 2024,
        "source_files": [
            "data/gaokao_beijing_recitation/work/images/2024/yw05.png",
            "data/gaokao_beijing_recitation/work/images/2024/yw06.png",
            "data/gaokao_beijing_recitation/work/images/2024/yw11.png",
            "data/gaokao_beijing_recitation/work/pages/2024/source.html",
        ],
        "source_urls": [
            {"role": "question_and_answer_images", "url": "https://gaokao.eol.cn/shiti/yw/202407/t20240705_2621911.shtml"},
        ],
        "documents": [
            doc("question_image", "data/gaokao_beijing_recitation/work/images/2024/yw05.png", [5], "https://gaokao.eol.cn/shiti/yw/202407/t20240705_2621911.shtml"),
            doc("question_image_continuation", "data/gaokao_beijing_recitation/work/images/2024/yw06.png", [6], "https://gaokao.eol.cn/shiti/yw/202407/t20240705_2621911.shtml"),
            doc("answer_image", "data/gaokao_beijing_recitation/work/images/2024/yw11.png", [11], "https://gaokao.eol.cn/shiti/yw/202407/t20240705_2621911.shtml"),
        ],
    },
    {
        "paper_id": "G2025",
        "title": "2025年普通高等学校招生全国统一考试（北京卷）语文",
        "year": 2025,
        "source_files": [
            "data/gaokao_beijing_recitation/work/pdfs/2025/questions.pdf",
            "data/gaokao_beijing_recitation/work/pdfs/2025/answers.pdf",
            "data/gaokao_beijing_recitation/work/pages/2025_sohu/source.html",
        ],
        "source_urls": [
            {"role": "question_pdf", "url": "https://www.jhgk.cn/upload/file/20250708/1751941949359096440.pdf"},
            {"role": "answer_pdf", "url": "https://www.jhgk.cn/upload/file/20250708/1751941961501043401.pdf"},
            {"role": "cross_check_text", "url": "https://www.sohu.com/a/907809759_120886047"},
        ],
        "documents": [
            doc("question_image", "data/gaokao_beijing_recitation/work/images/2025/questions_page_05.png", [5], "https://www.jhgk.cn/upload/file/20250708/1751941949359096440.pdf"),
            doc("question_image_continuation", "data/gaokao_beijing_recitation/work/images/2025/questions_page_06.png", [6], "https://www.jhgk.cn/upload/file/20250708/1751941949359096440.pdf"),
            doc("answer_image", "data/gaokao_beijing_recitation/work/images/2025/answers_page_12.png", [12], "https://www.jhgk.cn/upload/file/20250708/1751941961501043401.pdf"),
            doc("question_text", "data/gaokao_beijing_recitation/work/texts/2025/questions.json", [5, 6], "https://www.jhgk.cn/upload/file/20250708/1751941949359096440.pdf"),
            doc("answer_text", "data/gaokao_beijing_recitation/work/texts/2025/answers.json", [12], "https://www.jhgk.cn/upload/file/20250708/1751941961501043401.pdf"),
            doc("cross_check_text", "data/gaokao_beijing_recitation/work/pages/2025_sohu/article.txt", [], "https://www.sohu.com/a/907809759_120886047"),
        ],
    },
    {
        "paper_id": "G2026",
        "title": "2026年普通高等学校招生全国统一考试（北京卷）语文",
        "year": 2026,
        "source_files": [
            "data/gaokao_beijing_recitation/work/pages/2026_sohu/source.html",
            "data/gaokao_beijing_recitation/work/pages/2026_sohu_analysis/source.html",
        ],
        "source_urls": [
            {"role": "question_and_answer_text", "url": "https://m.sohu.com/a/1062842127_120886047"},
            {"role": "cross_check_text", "url": "https://m.sohu.com/a/1070279492_125993/"},
        ],
        "documents": [
            doc("question_and_answer_text", "data/gaokao_beijing_recitation/work/pages/2026_sohu/article.txt", [], "https://m.sohu.com/a/1062842127_120886047"),
            doc("cross_check_text", "data/gaokao_beijing_recitation/work/pages/2026_sohu_analysis/article.txt", [], "https://m.sohu.com/a/1070279492_125993/"),
        ],
    },
]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prompt_for(paper):
    output = f"data/gaokao_beijing_recitation/work/results/{paper['paper_id']}.json"
    return f"""你是语文古诗文默写真题提取子代理。工作目录是 {ROOT}。
只处理 {paper['paper_id']}（{paper['year']} 年北京高考语文卷），禁止联网，不得修改来源资料、脚本或其他年份文件。

来源文档（角色、相对路径、原页码、公开 URL）：
{json.dumps(paper['documents'], ensure_ascii=False, indent=2)}

任务：
1. 只提取直接默写、理解性情境默写以及明确要求填写古诗文原句的大题。不要收诗歌鉴赏、文言文阅读翻译、《红楼梦》或一般名著阅读。
2. 必须实际 Read 所有 question_image、question_image_continuation、answer_image；不得只凭记忆或文本层作答。网页文字来源须读取 article.txt。cross_check_text 用于交叉核对，不得把解析内容混入原题。
3. 题号、总题干、小问标记、语境、横线位置、标点、篇名、作者均按所给试题来源照录。跨页小问合并，不得漏掉续页；不得自行补引号、横线、标点或“通行版本”文字。
4. 答案只能照录所给参考答案来源，不能自行解题。将 8 个基本空按顺序放入各 item 的 answers。若答案页明确列出“或”的备选答案，基本 answers 仍只放首选答案，并在 alternate_answers 中逐组保留备选答案。
5. 网页没有固定页码时 question_pages、answer_pages 用 []，不要虚构页码。PDF/图片使用其文件内印刷页码，不是阅读器另计的页码。
6. works 记录该小问涉及的作品；同一小问涉及两篇作品时记录两项。作品名不加书名号，作者无法从题干或基本文献常识明确确定时为 null。答案对象也填 work_title、author。
7. 公开转载来源并不等于官方发布。若题目与答案文字存在转载差异、异文、缺字或版面疑点，写入 notes，不要静默修正。

只写合法 UTF-8 JSON 到 {output}，结构如下：
{{
  "paper_id": "{paper['paper_id']}",
  "status": "ok | needs_review",
  "questions": [
    {{
      "number": "实际大题号（仅数字）",
      "instruction": "总题干原文",
      "question_pages": [5],
      "items": [
        {{
          "label": "（1）",
          "context": "含 __________ 的完整小问原文",
          "works": [{{"title": "篇名", "author": "作者或 null"}}],
          "answers": [{{"text": "首选答案原句", "work_title": "篇名", "author": "作者或 null"}}],
          "alternate_answers": [
            {{"label": "或", "answers": [{{"text": "备选答案原句", "work_title": "篇名", "author": "作者或 null"}}]}}
          ],
          "answer_status": "provided | missing | uncertain",
          "answer_pages": [10]
        }}
      ]
    }}
  ],
  "notes": ["来源、页码、跨页、异文或转载差异说明；无则空数组"]
}}
没有备选答案时 alternate_answers 必须为 []。答案完整且顺序明确时 status 用 ok；只有真实无法确定时才用 needs_review。
完成后仅回复题号、小问数、基本答案数和输出路径。
"""


def main():
    for directory in ("prompts", "results", "reviews", "logs"):
        (WORK / directory).mkdir(parents=True, exist_ok=True)
    for paper in PAPERS:
        paper.update({
            "kind": "高考真题",
            "district": "北京",
            "school": None,
            "variant": "北京卷",
            "result_file": f"data/gaokao_beijing_recitation/work/results/{paper['paper_id']}.json",
            "prompt_file": f"data/gaokao_beijing_recitation/work/prompts/{paper['paper_id']}.md",
            "duplicate_files": [],
        })
        for path in paper["source_files"] + [item["path"] for item in paper["documents"]]:
            if not (ROOT / path).is_file():
                raise FileNotFoundError(path)
        (ROOT / paper["prompt_file"]).write_text(prompt_for(paper), encoding="utf-8")
    inventory = {
        "format": "gaokao-beijing-recitation-inventory/v1",
        "years": [2022, 2023, 2024, 2025, 2026],
        "paper_count": len(PAPERS),
        "source_note": "公开转载的试题与参考答案资料；不将第三方网页标注的‘官方版’视为官方来源证明。",
        "papers": PAPERS,
    }
    write_json(WORK / "inventory.json", inventory)
    print(f"prepared {len(PAPERS)} papers: {WORK / 'inventory.json'}")


if __name__ == "__main__":
    main()
