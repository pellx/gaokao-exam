"""Merge per-paper recitation extractions into one validated JSON file."""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/chinese_recitation/work"
OUTPUT = ROOT / "data/chinese_recitation.json"


def main():
    inventory = json.loads((WORK / "inventory.json").read_text(encoding="utf-8"))
    papers = []
    missing = []
    for source in inventory["papers"]:
        result_path = ROOT / source["result_file"]
        if not result_path.is_file():
            missing.append(source["paper_id"])
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("paper_id") != source["paper_id"]:
            raise ValueError(f"paper_id mismatch: {result_path}")
        papers.append(
            {
                key: source[key]
                for key in (
                    "paper_id",
                    "title",
                    "year",
                    "kind",
                    "district",
                    "school",
                    "variant",
                    "source_files",
                    "duplicate_files",
                )
            }
            | {
                "status": result["status"],
                "questions": result["questions"],
                "notes": result["notes"],
            }
        )
    if missing:
        raise RuntimeError(f"仍有 {len(missing)} 份试卷没有结果：{', '.join(missing)}")

    review_path = WORK / "review_status.json"
    review_status = json.loads(review_path.read_text(encoding="utf-8")) if review_path.is_file() else None
    if review_status:
        review_pending = [
            paper["paper_id"]
            for paper in papers
            if review_status.get("tasks", {}).get(paper["paper_id"], {}).get("state") != "accept"
        ]
        if review_pending:
            raise RuntimeError(f"独立复核尚未全部通过：{', '.join(review_pending)}")

    questions = [question for paper in papers for question in paper["questions"]]
    items = [item for question in questions for item in question["items"]]
    output = {
        "format": "chinese-ancient-recitation/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "raw 目录现有高三语文模拟卷中的古诗文、名篇名句默写题及原卷答案；不包含诗歌鉴赏、文言文阅读、翻译或一般名著阅读。",
        "scope": {
            "years": [2024, 2025, 2026],
            "source_root": "raw",
            "official_gaokao_papers_in_source": False,
            "note": "raw 索引仅包含 2024—2026 年模拟卷，不含正式高考真题。完全相同的 PDF 只提取一次，重复路径保留在 duplicate_files。",
        },
        "extraction": {
            "model": "opencode-go/deepseek-v4.1-flash#high",
            "method": "逐卷独立子代理提取；PDF 文本层为主，低文本页渲染后由子代理直接识图；答案只来自原卷，不由模型补写。",
            "independent_review": {
                "completed": bool(review_status),
                "accepted_paper_count": sum(
                    task.get("state") == "accept"
                    for task in (review_status or {}).get("tasks", {}).values()
                ),
                "status_counts": (review_status or {}).get("status_counts", {}),
            },
        },
        "coverage": {
            "raw_chinese_pdf_count": inventory["pdf_count"],
            "unique_paper_count": len(papers),
            "unique_pdf_count": inventory["unique_pdf_count"],
            "excluded_files": inventory["excluded_files"],
            "duplicate_files": inventory["duplicate_files"],
            "processed_paper_count": len(papers),
            "needs_review_papers": [paper["paper_id"] for paper in papers if paper["status"] == "needs_review"],
            "not_found_papers": [paper["paper_id"] for paper in papers if paper["status"] == "not_found"],
        },
        "statistics": {
            "paper_count": len(papers),
            "papers_with_recitation": sum(bool(paper["questions"]) for paper in papers),
            "question_count": len(questions),
            "item_count": len(items),
            "answer_count": sum(len(item["answers"]) for item in items),
            "missing_answer_items": sum(item["answer_status"] == "missing" for item in items),
            "uncertain_answer_items": sum(item["answer_status"] == "uncertain" for item in items),
        },
        "papers": papers,
    }
    temporary = OUTPUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    temporary.replace(OUTPUT)
    print(json.dumps(output["statistics"], ensure_ascii=False))
    print(f"输出：{OUTPUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
