"""Build and audit the standalone 2022—2026 Beijing gaokao recitation JSON."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/gaokao_beijing_recitation/work"
OUTPUT = ROOT / "data/gaokao_beijing_recitation.json"
EXPECTED_YEARS = [2022, 2023, 2024, 2025, 2026]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    temporary.replace(path)


def main():
    inventory = load(WORK / "inventory.json")
    sources = inventory["papers"]
    if [paper["year"] for paper in sources] != EXPECTED_YEARS:
        raise RuntimeError("年份覆盖不符合 2022—2026 五年范围")
    review_status = load(WORK / "review_status.json")
    papers = []
    source_file_details = []
    for source in sources:
        paper_id = source["paper_id"]
        result = load(ROOT / source["result_file"])
        review = load(WORK / "reviews" / f"{paper_id}.json")
        review_task = review_status.get("tasks", {}).get(paper_id, {})
        if result.get("paper_id") != paper_id or review.get("paper_id") != paper_id:
            raise RuntimeError(f"{paper_id}: paper_id 不一致")
        if result.get("status") != "ok":
            raise RuntimeError(f"{paper_id}: 提取状态不是 ok")
        if review.get("verdict") != "accept" or review_task.get("state") != "accept":
            raise RuntimeError(f"{paper_id}: 独立复核尚未通过")
        if review.get("issues"):
            raise RuntimeError(f"{paper_id}: accept 报告仍含未解决问题")
        if len(result["questions"]) != 1:
            raise RuntimeError(f"{paper_id}: 应恰好有一道默写大题")
        items = result["questions"][0]["items"]
        answers = [answer for item in items for answer in item["answers"]]
        alternates = [answer for item in items for group in item.get("alternate_answers", []) for answer in group["answers"]]
        if len(items) != 4 or len(answers) != 8:
            raise RuntimeError(f"{paper_id}: 五年试卷均应为 4 小问、8 个基本答案")
        if any(item["answer_status"] != "provided" for item in items):
            raise RuntimeError(f"{paper_id}: 存在未提供或不确定答案")
        if any(not answer.get("text") for answer in answers + alternates):
            raise RuntimeError(f"{paper_id}: 存在空答案")
        for relative in source["source_files"]:
            path = ROOT / relative
            data = path.read_bytes()
            source_file_details.append({
                "paper_id": paper_id,
                "path": relative,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
        papers.append({
            key: source[key]
            for key in (
                "paper_id", "title", "year", "kind", "district", "school", "variant",
                "source_files", "source_urls", "duplicate_files",
            )
        } | {
            "status": result["status"],
            "questions": result["questions"],
            "notes": result["notes"],
            "review": review,
        })
    questions = [question for paper in papers for question in paper["questions"]]
    items = [item for question in questions for item in question["items"]]
    answers = [answer for item in items for answer in item["answers"]]
    alternate_answers = [
        answer
        for item in items
        for group in item.get("alternate_answers", [])
        for answer in group["answers"]
    ]
    statistics = {
        "paper_count": len(papers),
        "papers_with_recitation": sum(bool(paper["questions"]) for paper in papers),
        "question_count": len(questions),
        "item_count": len(items),
        "blank_count": sum(len(re.findall(r"_+", item["context"])) for item in items),
        "answer_slot_count": len(answers),
        "answer_count": len(answers),
        "alternate_answer_count": len(alternate_answers),
        "missing_answer_items": sum(item["answer_status"] == "missing" for item in items),
        "uncertain_answer_items": sum(item["answer_status"] == "uncertain" for item in items),
    }
    expected = {
        "paper_count": 5,
        "papers_with_recitation": 5,
        "question_count": 5,
        "item_count": 20,
        "blank_count": 40,
        "answer_slot_count": 40,
        "answer_count": 40,
        "alternate_answer_count": 4,
        "missing_answer_items": 0,
        "uncertain_answer_items": 0,
    }
    if statistics != expected:
        raise RuntimeError(f"统计审计失败：{statistics}，预期 {expected}")
    output = {
        "format": "chinese-ancient-recitation/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "2022—2026 年北京高考语文真题中的古诗文、名篇名句默写题及公开转载参考答案；不包含诗歌鉴赏、文言文阅读、翻译、《红楼梦》或一般名著阅读。",
        "scope": {
            "years": EXPECTED_YEARS,
            "regions": ["北京"],
            "paper_type": "普通高等学校招生全国统一考试（北京卷）语文真题",
            "source_root": "data/gaokao_beijing_recitation/work",
            "official_gaokao_papers_in_source": True,
            "source_provenance": inventory["source_note"],
            "mixed_with_mock_exams": False,
            "page_number_note": "question_pages、answer_pages 使用来源文件内印刷页码；纯网页来源无固定页码，以空数组表示，不虚构页码。",
        },
        "extraction": {
            "model": "opencode-go/deepseek-v4.1-flash#high",
            "method": "逐年独立子代理提取；图片页直接识图，PDF 同时保留分页文本；答案只来自所收集参考答案，不由模型补写；2025、2026 年另用公开文字来源交叉核对。",
            "independent_review": {
                "completed": True,
                "accepted_paper_count": sum(
                    task.get("state") == "accept" for task in review_status["tasks"].values()
                ),
                "status_counts": review_status.get("status_counts", {}),
                "per_paper_reports": "每份 paper.review 保留独立复核范围、结论、问题和说明。",
            },
        },
        "coverage": {
            "expected_years": EXPECTED_YEARS,
            "covered_years": [paper["year"] for paper in papers],
            "unique_paper_count": len(papers),
            "processed_paper_count": len(papers),
            "needs_review_papers": [paper["paper_id"] for paper in papers if paper["status"] != "ok"],
            "not_found_papers": [paper["paper_id"] for paper in papers if not paper["questions"]],
            "source_file_count": len(source_file_details),
            "source_files": source_file_details,
        },
        "statistics": statistics,
        "papers": papers,
    }
    write_json(OUTPUT, output)
    audit = {
        "status": "pass",
        "years_exact": True,
        "one_question_per_paper": True,
        "four_items_and_eight_basic_answers_per_paper": True,
        "all_answers_provided": True,
        "all_source_files_exist_and_hashed": True,
        "all_independent_reviews_accepted_without_open_issues": True,
        "statistics": statistics,
    }
    write_json(WORK / "audit.json", audit)
    print(json.dumps(statistics, ensure_ascii=False))
    print(f"输出：{OUTPUT.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
