"""Audit source coverage and cross-check existing verified 2026 extractions."""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/chinese_recitation/work"
FINAL = ROOT / "data/chinese_recitation.json"
RECITATION = re.compile(r"填写作品原句|名篇名句|默写|古诗文.*填空|填写.*诗文")


def normalize(value):
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def flatten_parts(parts):
    for part in parts or []:
        yield part
        yield from flatten_parts(part.get("parts"))


def existing_answers(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    answers = []
    question_count = 0
    for question in data.get("questions", []):
        stem = question.get("stem", "")
        if not isinstance(stem, str) or not RECITATION.search(stem):
            continue
        question_count += 1
        for part in flatten_parts(question.get("parts")):
            content = (part.get("answer") or {}).get("content")
            if isinstance(content, str):
                answers.append(content)
            elif isinstance(content, list):
                answers.extend(str(item) for item in content if isinstance(item, str))
    return question_count, Counter(normalize(answer) for answer in answers if normalize(answer))


def main():
    final = json.loads(FINAL.read_text(encoding="utf-8"))
    inventory = json.loads((WORK / "inventory.json").read_text(encoding="utf-8"))
    raw_files = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "raw").rglob("*.pdf")
        if "语文" in path.name
    }
    accounted = []
    for paper in final["papers"]:
        accounted.extend(paper["source_files"])
        accounted.extend(paper["duplicate_files"])
    accounted.extend(item["file"] for item in final["coverage"]["excluded_files"])
    coverage_errors = []
    if set(accounted) != raw_files:
        coverage_errors.append({
            "missing": sorted(raw_files - set(accounted)),
            "unexpected": sorted(set(accounted) - raw_files),
        })
    duplicates = sorted(path for path, count in Counter(accounted).items() if count != 1)
    if duplicates:
        coverage_errors.append({"accounted_more_than_once": duplicates})

    keyword_without_questions = []
    for source in inventory["papers"]:
        text = "\n".join(
            (ROOT / document["text_file"]).read_text(encoding="utf-8")
            for document in source["documents"]
        )
        paper = next(item for item in final["papers"] if item["paper_id"] == source["paper_id"])
        if RECITATION.search(text) and not paper["questions"]:
            keyword_without_questions.append(source["paper_id"])

    parsed_by_source = {}
    for path in (ROOT / "parsed").rglob("paper.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        source_file = data.get("source", {}).get("file")
        if source_file and data.get("paper", {}).get("subject") == "语文":
            parsed_by_source[str((path.parent / source_file).resolve())] = path

    cross_checks = []
    for paper in final["papers"]:
        parsed_path = next(
            (
                parsed_by_source[str((ROOT / source).resolve())]
                for source in paper["source_files"]
                if str((ROOT / source).resolve()) in parsed_by_source
            ),
            None,
        )
        if not parsed_path:
            continue
        old_question_count, old_answers = existing_answers(parsed_path)
        new_answers = Counter(
            normalize(answer["text"])
            for question in paper["questions"]
            for item in question["items"]
            for answer in item["answers"]
            if normalize(answer["text"])
        )
        cross_checks.append({
            "paper_id": paper["paper_id"],
            "parsed_file": parsed_path.relative_to(ROOT).as_posix(),
            "existing_question_count": old_question_count,
            "new_question_count": len(paper["questions"]),
            "missing_or_changed_in_new": list((old_answers - new_answers).elements()),
            "extra_or_changed_in_new": list((new_answers - old_answers).elements()),
        })

    report = {
        "raw_pdf_count": len(raw_files),
        "accounted_pdf_count": len(accounted),
        "coverage_errors": coverage_errors,
        "keyword_without_questions": keyword_without_questions,
        "cross_checks": cross_checks,
        "cross_checks_with_differences": [
            item for item in cross_checks
            if item["existing_question_count"] != item["new_question_count"]
            or item["missing_or_changed_in_new"]
            or item["extra_or_changed_in_new"]
        ],
    }
    path = WORK / "audit.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "coverage_errors": len(coverage_errors),
        "keyword_without_questions": keyword_without_questions,
        "cross_checked": len(cross_checks),
        "cross_checks_with_differences": len(report["cross_checks_with_differences"]),
    }, ensure_ascii=False))
    print(f"审计报告：{path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
