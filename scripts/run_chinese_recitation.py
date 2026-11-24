"""Run isolated OpenCode agents for Chinese recitation extraction."""

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
INVENTORY = WORK / "inventory.json"
STATUS = WORK / "status.json"
LOGS = WORK / "logs"
MODEL = "opencode-go/deepseek-v4.1-flash#high"
OPENCODE = shutil.which("opencode")
LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def load_result(paper):
    path = ROOT / paper["result_file"]
    if not path.is_file():
        raise ValueError("结果文件不存在")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON 无效：{error}") from error
    if result.get("paper_id") != paper["paper_id"]:
        raise ValueError("paper_id 不匹配")
    if result.get("status") not in {"ok", "needs_review", "not_found"}:
        raise ValueError("status 无效")
    if not isinstance(result.get("questions"), list):
        raise ValueError("questions 不是数组")
    if not isinstance(result.get("notes"), list):
        raise ValueError("notes 不是数组")
    for question in result["questions"]:
        if not question.get("number") or not question.get("instruction"):
            raise ValueError("默写大题缺少 number 或 instruction")
        if not isinstance(question.get("items"), list) or not question["items"]:
            raise ValueError("默写大题缺少小问")
        for item in question["items"]:
            if not item.get("context"):
                raise ValueError("小问缺少 context")
            if item.get("answer_status") not in {"provided", "missing", "uncertain"}:
                raise ValueError("小问 answer_status 无效")
            if not isinstance(item.get("answers"), list):
                raise ValueError("小问 answers 不是数组")
            if item["answer_status"] == "provided" and not item["answers"]:
                raise ValueError("provided 小问没有答案")
            if item["answer_status"] == "missing" and item["answers"]:
                raise ValueError("missing 小问却含答案")
            for answer in item["answers"]:
                if not answer.get("text"):
                    raise ValueError("答案缺少 text")
    return result


def stats(result):
    questions = result["questions"]
    items = [item for question in questions for item in question["items"]]
    return {
        "question_count": len(questions),
        "item_count": len(items),
        "answer_count": sum(len(item["answers"]) for item in items),
        "missing_answer_items": sum(item["answer_status"] == "missing" for item in items),
        "uncertain_answer_items": sum(item["answer_status"] == "uncertain" for item in items),
    }


def update_status(paper_id, **updates):
    with LOCK:
        if STATUS.is_file():
            status = json.loads(STATUS.read_text(encoding="utf-8"))
        else:
            status = {"model": MODEL, "tasks": {}}
        task = status["tasks"].setdefault(paper_id, {})
        task.update(updates)
        status["updated_at"] = now()
        counts = {}
        for item in status["tasks"].values():
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        status["status_counts"] = counts
        write_json(STATUS, status)


def run_agent(paper, attempt, repair_reason=None):
    prompt_path = ROOT / paper["prompt_file"]
    prompt = prompt_path.read_text(encoding="utf-8")
    if repair_reason:
        prompt += f"\n\n上一次执行未通过检查：{repair_reason}\n请重新完整核对本卷并修复输出，不要只解释问题。\n"
    retry_prompt = WORK / "prompts" / f"{paper['paper_id']}.attempt{attempt}.md"
    retry_prompt.write_text(prompt, encoding="utf-8")
    log_path = LOGS / f"{paper['paper_id']}.attempt{attempt}.jsonl"
    command = [
        OPENCODE,
        "run",
        "--standalone",
        "--model",
        MODEL,
        "--format",
        "json",
        "--title",
        f"{paper['paper_id']} 语文默写提取",
        "--file",
        retry_prompt.relative_to(ROOT).as_posix(),
        "严格执行附加文件中的任务。禁止联网，必须实际写入指定 JSON 文件。",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=2400,
            check=False,
        )
    if completed.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        raise RuntimeError(f"OpenCode 退出码 {completed.returncode}：{tail}")
    return load_result(paper)


def process(paper):
    paper_id = paper["paper_id"]
    update_status(paper_id, state="running", started_at=now(), title=paper["title"])
    reason = None
    for attempt in range(1, 3):
        try:
            result = run_agent(paper, attempt, reason)
            result_stats = stats(result)
            update_status(
                paper_id,
                state=result["status"],
                attempts=attempt,
                completed_at=now(),
                **result_stats,
            )
            print(f"{paper_id} {paper['title']}: {result['status']}, {result_stats}", flush=True)
            return
        except Exception as error:
            reason = str(error)
            update_status(paper_id, state="retrying" if attempt == 1 else "failed", attempts=attempt, error=reason)
            print(f"{paper_id} attempt {attempt} failed: {reason[-500:]}", flush=True)
    update_status(paper_id, state="failed", completed_at=now())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="只运行一个 paper_id，用于试跑")
    parser.add_argument("--max-parallel", type=int, default=12)
    parser.add_argument("--force", action="store_true", help="忽略已有合格结果并重跑")
    args = parser.parse_args()
    if not OPENCODE:
        raise RuntimeError("找不到 opencode")
    LOGS.mkdir(parents=True, exist_ok=True)
    papers = json.loads(INVENTORY.read_text(encoding="utf-8"))["papers"]
    if args.only:
        papers = [paper for paper in papers if paper["paper_id"] == args.only]
        if not papers:
            raise RuntimeError(f"未知 paper_id: {args.only}")
    pending = []
    for paper in papers:
        if not args.force:
            try:
                result = load_result(paper)
                result_stats = stats(result)
                update_status(paper["paper_id"], state=result["status"], resumed=True, **result_stats)
                print(f"{paper['paper_id']} 已有合格结果，跳过", flush=True)
                continue
            except (ValueError, OSError, json.JSONDecodeError):
                pass
        pending.append(paper)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_parallel) as executor:
        futures = [executor.submit(process, paper) for paper in pending]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    print(f"完成：提交 {len(pending)} 个任务，详见 {STATUS.relative_to(ROOT)}", flush=True)


if __name__ == "__main__":
    main()
