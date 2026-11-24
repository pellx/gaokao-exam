"""Deterministically cross-check the final Beijing gaokao recitation answers and pages."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/gaokao_beijing_recitation/work"
FINAL = ROOT / "data/gaokao_beijing_recitation.json"

EXPECTED = {
    "G2022": {
        "number": "14", "question_pages": [5], "answer_pages": [],
        "answers": [["沧海月明珠有泪", "蓝田日暖玉生烟"], ["过春风十里", "尽荠麦青青"], ["己所不欲", "勿施于人"], ["道之所存", "师之所存也"]],
        "alternates": [[], [], [["我不欲人之加诸我也", "吾亦欲无加诸人"]], [["闻道有先后", "术业有专攻"]]],
    },
    "G2023": {
        "number": "15", "question_pages": [6], "answer_pages": [10],
        "answers": [["出师未捷身先死", "长使英雄泪满襟"], ["小楼一夜听春雨", "深巷明朝卖杏花"], ["风乎舞雩", "咏而归"], ["奉之弥繁", "侵之愈急"]],
        "alternates": [[], [], [], []],
    },
    "G2024": {
        "number": "14", "question_pages": [5, 6], "answer_pages": [11],
        "answers": [["花径不曾缘客扫", "蓬门今始为君开"], ["佳期如梦", "忍顾鹊桥归路"], ["仁者不忧", "勇者不惧"], ["好色而不淫", "怨诽而不乱"]],
        "alternates": [[], [], [], []],
    },
    "G2025": {
        "number": "14", "question_pages": [5, 6], "answer_pages": [12],
        "answers": [["质胜文则野", "文胜质则史"], ["驽马十驾", "余音袅袅"], ["周公吐哺", "天下归心"], ["素月分辉", "明河共影"]],
        "alternates": [[], [], [], []],
    },
    "G2026": {
        "number": "14", "question_pages": [], "answer_pages": [],
        "answers": [["思国之安者", "必积其德义"], ["战不善", "弊在赂秦"], ["户庭无尘杂", "虚室有余闲"], ["小楼一夜听春雨", "深巷明朝卖杏花"]],
        "alternates": [[], [], [], []],
    },
}


def main():
    data = json.loads(FINAL.read_text(encoding="utf-8"))
    errors = []
    checked_answers = 0
    checked_alternates = 0
    checked_printed_blanks = 0
    for paper in data["papers"]:
        paper_id = paper["paper_id"]
        expected = EXPECTED[paper_id]
        if len(paper["questions"]) != 1:
            errors.append(f"{paper_id}: 默写大题数不是 1")
            continue
        question = paper["questions"][0]
        for field in ("number", "question_pages"):
            if question[field] != expected[field]:
                errors.append(f"{paper_id}: {field}={question[field]!r}，应为 {expected[field]!r}")
        for index, item in enumerate(question["items"]):
            actual_answers = [answer["text"] for answer in item["answers"]]
            if actual_answers != expected["answers"][index]:
                errors.append(f"{paper_id} item {index + 1}: {actual_answers!r} != {expected['answers'][index]!r}")
            if item["answer_pages"] != expected["answer_pages"]:
                errors.append(f"{paper_id} item {index + 1}: answer_pages 不符")
            actual_alternates = [[answer["text"] for answer in group["answers"]] for group in item.get("alternate_answers", [])]
            if actual_alternates != expected["alternates"][index]:
                errors.append(f"{paper_id} item {index + 1}: 备选答案不符 {actual_alternates!r}")
            printed_blank_count = len(re.findall(r"_+", item["context"]))
            expected_blank_count = expected.get("printed_blanks", [2, 2, 2, 2])[index]
            if printed_blank_count != expected_blank_count:
                errors.append(f"{paper_id} item {index + 1}: 小问横线数为 {printed_blank_count}，应为 {expected_blank_count}")
            checked_printed_blanks += printed_blank_count
            checked_answers += len(actual_answers)
            checked_alternates += sum(map(len, actual_alternates))
    audit = {
        "status": "pass" if not errors else "fail",
        "checked_papers": len(data["papers"]),
        "checked_basic_answers": checked_answers,
        "checked_alternate_answers": checked_alternates,
        "checked_printed_blanks": checked_printed_blanks,
        "question_numbers": {paper["paper_id"]: paper["questions"][0]["number"] for paper in data["papers"]},
        "errors": errors,
    }
    path = WORK / "deterministic_audit.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
