"""Independent second-pass review of every recitation extraction."""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = Path(os.environ.get("RECITATION_WORK", ROOT / "data/chinese_recitation/work"))
if not WORK.is_absolute():
    WORK = ROOT / WORK
REVIEWS = WORK / "reviews"
PROMPTS = WORK / "review_prompts"
LOGS = WORK / "review_logs"
STATUS = WORK / "review_status.json"
MODEL = "opencode-go/deepseek-v4.1-flash#high"
OPENCODE = shutil.which("opencode")
LOCK = threading.Lock()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def prompt_for(paper):
    output = (REVIEWS / f"{paper['paper_id']}.json").relative_to(ROOT).as_posix()
    return f"""你是独立的语文古诗文默写提取复核子代理。工作目录是 {ROOT}。
只复核 {paper['paper_id']}，不得修改提取结果、raw、scripts 或其他试卷文件，禁止联网。
首轮结果：{paper['result_file']}
原卷与文本层：{json.dumps(paper['documents'], ensure_ascii=False, indent=2)}

请完整对照首轮结果与原卷，检查：
1. 是否漏掉任何直接默写、理解性情境默写或明确要求填写古诗文原句的小题；是否误收诗歌鉴赏、翻译、现代文或名著阅读。
2. 总题号、总题干、小问标记、完整语境、横线数量和位置、question_pages 是否忠实。不得把原卷缺失的横线自行补上。
3. 每个答案是否确由原卷给出，顺序是否对应横线，文字是否照录；不能用通行诗文替换原卷异文或排印错误。无答案不得自行作答。
4. works、answer_pages、answer_status 和 notes 是否准确。原卷自身错误但已清楚注明，不属于提取错误，可 accept。
5. documents 的 low_text_pages 涉及默写题或答案时，必须用 PyMuPDF 渲染并通过 Read 实际看图；不能仅看 OCR。普通文本层若清晰可主要依据文本，但关键异文应看原页。not_found 结果必须检查全卷是否确实无默写题。

只写复核报告 {output}，不得改首轮 JSON。报告为合法 UTF-8 JSON：
{{
  "paper_id": "{paper['paper_id']}",
  "verdict": "accept | reject",
  "checked_question_pages": [1],
  "checked_answer_pages": [10],
  "issues": [
    {{"path": "questions[0].items[1].answers[0].text", "current": "当前内容", "source": "原卷实际内容", "correction": "明确修复建议", "reason": "问题说明"}}
  ],
  "notes": ["实际复核范围与仍需人工注意的原卷问题"]
}}
只要存在漏题、漏空、漏答案、文字不符、答案顺序错误或自行补写，就必须 reject；不要仅因原卷本身有排印错误而 reject。
最后仅回复 verdict、问题数和报告路径。
"""


def load_report(paper):
    path = REVIEWS / f"{paper['paper_id']}.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("paper_id") != paper["paper_id"] or report.get("verdict") not in {"accept", "reject"}:
        raise ValueError("复核报告 paper_id 或 verdict 无效")
    if not isinstance(report.get("issues"), list) or not isinstance(report.get("notes"), list):
        raise ValueError("复核报告 issues/notes 无效")
    if report["verdict"] == "reject" and not report["issues"]:
        raise ValueError("reject 报告没有具体问题")
    return report


def update(paper_id, state, **values):
    with LOCK:
        status = json.loads(STATUS.read_text(encoding="utf-8")) if STATUS.is_file() else {"tasks": {}}
        status["tasks"].setdefault(paper_id, {}).update(state=state, **values)
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        counts = {}
        for task in status["tasks"].values():
            counts[task["state"]] = counts.get(task["state"], 0) + 1
        status["status_counts"] = counts
        write_json(STATUS, status)


def process(paper):
    paper_id = paper["paper_id"]
    REVIEWS.mkdir(parents=True, exist_ok=True)
    PROMPTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    reason = None
    for attempt in range(1, 3):
        try:
            prompt = prompt_for(paper)
            if reason:
                prompt += f"\n上次执行失败：{reason}\n请重新复核并实际写入合法报告。\n"
            prompt_path = PROMPTS / f"{paper_id}.attempt{attempt}.md"
            prompt_path.write_text(prompt, encoding="utf-8")
            log_path = LOGS / f"{paper_id}.attempt{attempt}.jsonl"
            update(paper_id, "running", attempts=attempt)
            command = [OPENCODE, "run", "--standalone", "--model", MODEL, "--format", "json",
                       "--title", f"{paper_id} 默写独立复核", "--file", prompt_path.relative_to(ROOT).as_posix(),
                       "严格执行附加文件中的独立复核任务，禁止联网，不得修改首轮结果。"]
            with log_path.open("w", encoding="utf-8") as log:
                completed = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                           timeout=2400, check=False)
            if completed.returncode:
                raise RuntimeError(f"OpenCode 退出码 {completed.returncode}")
            report = load_report(paper)
            update(paper_id, report["verdict"], attempts=attempt, issue_count=len(report["issues"]))
            print(f"{paper_id} {report['verdict']} issues={len(report['issues'])}", flush=True)
            return
        except Exception as error:
            reason = str(error)
            update(paper_id, "retrying" if attempt == 1 else "failed", attempts=attempt, error=reason)
            print(f"{paper_id} review attempt {attempt} failed: {reason}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--max-parallel", type=int, default=12)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    papers = json.loads((WORK / "inventory.json").read_text(encoding="utf-8"))["papers"]
    if args.only:
        selected = set(args.only.split(","))
        papers = [paper for paper in papers if paper["paper_id"] in selected]
    pending = []
    for paper in papers:
        if not args.force:
            try:
                report = load_report(paper)
                update(paper["paper_id"], report["verdict"], resumed=True, issue_count=len(report["issues"]))
                continue
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        pending.append(paper)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_parallel) as executor:
        futures = [executor.submit(process, paper) for paper in pending]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    print(f"独立复核完成：提交 {len(pending)} 份，详见 {STATUS.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
