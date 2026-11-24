"""Run the 2026 Chinese-paper extraction batch through isolated OpenCode sessions."""

import concurrent.futures
import csv
import json
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data/question_extraction/2026"
BATCH_DIR = STATE_DIR / "chinese"
STATUS_PATH = STATE_DIR / "status.json"
SCHEMA = ROOT / "docs/question-json-v1.schema.json"
VALIDATOR = ROOT / "scripts/validate_exam_paper.py"
MODEL = "opencode-go/deepseek-v4.1-flash#high"
MAX_PARALLEL = 4
MAX_RETRY = 5
OPENCODE = shutil.which("opencode")
STATUS_LOCK = threading.Lock()

TARGETED_REPAIR_4 = {
    "2026|二模|西城|语文|": """
本轮只允许定点修复第 3 页材料一：将 materials.m003.content[1] 中的“其母曰：‘亦使之知，若何？’”恢复为原卷“其母曰：‘亦使知之，若何？’”（保留原标点和原有点位格式）。同步修正 extraction.notes 中关于两处语序一致的错误说明，明确材料一作“亦使知之”，第 7 题 C 项作“亦使之知”。第 7 题 C 项本身必须保持“亦使**之**知”，不得改动。除上述字段及必要 notes 外不得改动其他内容。
""",
    "2026|二模|房山|语文|": """
本轮只允许定点修复第 3 页 materials.m005.content[6] 的注释【2】：按原卷将“唐尧、虞舜”恢复为“唐尧、赏舜”，不得把原卷排印字静默改成“虞舜”。在该材料或全局 notes 中补充“原卷作‘赏舜’，参考答案第 9 题解析作‘虞舜’”。除该字段和必要 notes 外不得改动其他内容。
""",
    "2026|三模|北京精华学校|语文|三模": """
本轮只允许定点修复第 12 页第 15 题（2）答案示例第一处画线：在 q015.parts[p2].answer.content[0] 中，将 <u> 的起点移到“恪守亲族之间的礼数与分寸，合乎家族礼法与人情体面”的“恪”字前；前面的“为顾全尤氏的脸面与主客情分，依规处置冒犯主子的下人，”必须位于 <u> 之外。其余文字、第二处画线和 notes 不变。除该字段外不得改动其他内容。
""",
}

TARGETED_REPAIR_5 = {
    "2026|二模|西城|语文|": """
本轮以 review_4 的高倍放大结果为证据，只定点修复第 7 题 C 项第一句及关联备注：将 questions[q007].options[2].content[0] 恢复为原卷 `亦使知**之**`，即字符顺序为“亦—使—知—之”，加粗只在末字“之”。同步修正 extraction.notes[3]，明确材料一和第 7 题 C 项第一句均作“亦使知之”，二者语序相同，着重号在“之”下。materials.m003 当前“亦使知之”已正确，不得改动；C 项第二句及其他字段不得改动。
""",
    "2026|二模|房山|语文|": """
本轮只定点修复 review_4 列出的三处：
1. materials.m010.content[3] 中作品名按第 6 页原卷恢复为“（我们的田野》”，左侧保留原卷括号“（”，右侧保留书名号“》”，不得补成《我们的田野》；在 m010 或必要 notes 中注明原卷排印如此。
2. questions[q010].answer.rubric[0].content 中“(方法、内容各0.5分)”使用原卷半角括号，不得改成全角。
3. questions[q014].answer.rubric[0].content 中“(包含对所绘景象的描述、寄寓的情感)”使用原卷半角括号，不得改成全角。
除上述三个字段及必要 notes 外不得改动其他内容；此前已修复的“赏舜”等保持不动。
""",
}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def relative(path):
    return path.relative_to(ROOT).as_posix()


def output_dir(entry, source_files):
    return ROOT / "parsed" / entry["subject"] / str(entry["year"]) / entry["kind"] / Path(source_files[0]).name


def build_tasks():
    entries = read_csv(ROOT / "data/original_downloads/entries.csv")
    files = read_csv(ROOT / "data/original_downloads/files.csv")
    diagnostics = {
        item["entry_key"]: item
        for item in json.loads(
            (STATE_DIR / "chinese_pdf_diagnostics.json").read_text(encoding="utf-8")
        )
    }
    tasks = []
    for entry in entries:
        if not (
            entry["year"] == "2026"
            and entry["status"] == "已获得原始附件"
            and entry["subject"] == "语文"
        ):
            continue
        sources = []
        for record in files:
            if not (
                record["entry_key"] == entry["entry_key"]
                and record["year"] == "2026"
                and record["file_type"] == "原始附件"
                and record["status"] != "下载失败"
            ):
                continue
            path = ROOT / record["local_path"]
            if path.is_file() and path.stat().st_size > 0:
                sources.append(relative(path))
        if not sources:
            raise RuntimeError(f"No usable sources: {entry['entry_key']}")
        out = output_dir(entry, sources)
        metadata = {
            "id": entry["entry_key"],
            "title": entry["title"],
            "year": 2026,
            "kind": entry["kind"],
            "subject": "语文",
            "district": entry["district"],
        }
        if entry["region_or_school"] != entry["district"]:
            metadata["school"] = entry["region_or_school"]
        if entry["variant"]:
            metadata["variant"] = entry["variant"]
        diagnostic = diagnostics[entry["entry_key"]]
        tasks.append(
            {
                "entry_key": entry["entry_key"],
                "sequence": int(entry["sequence"]),
                "source_files": sources,
                "answer_files": [],
                "auxiliary_files": [],
                "output_dir": relative(out),
                "paper_metadata": metadata,
                "complexity": "complex"
                if diagnostic.get("text_chars") is not None
                and diagnostic["text_chars"] < 1000
                else "normal",
                "pages": diagnostic.get("pages"),
                "text_chars": diagnostic.get("text_chars"),
            }
        )
    tasks.sort(key=lambda item: item["sequence"])
    return tasks


def task_status(task):
    return {
        "entry_key": task["entry_key"],
        "sequence": task["sequence"],
        "output_dir": task["output_dir"],
        "complexity": task["complexity"],
        "pages": task["pages"],
        "status": "pending",
        "retry_count": 0,
        "extraction_sessions": [],
        "review_sessions": [],
        "history": [],
    }


def update_status(mutator):
    with STATUS_LOCK:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        mutator(status)
        status["chinese_batch"]["updated_at"] = now()
        counts = {}
        for item in status["chinese_batch"]["tasks"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        status["chinese_batch"]["status_counts"] = counts
        write_json(STATUS_PATH, status)


def save_event_result(log_path, result_path):
    events = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    texts = [
        event["part"]["text"]
        for event in events
        if event.get("type") == "text" and event.get("part", {}).get("text")
    ]
    errors = [
        event.get("error", {})
        for event in events
        if event.get("type") == "error"
    ]
    session_ids = [event["sessionID"] for event in events if event.get("sessionID")]
    result = {
        "session_id": session_ids[0] if session_ids else None,
        "text": texts[-1] if texts else "",
        "errors": errors,
        "event_count": len(events),
    }
    write_json(result_path, result)
    return result


def run_opencode(prompt_path, log_path, result_path, title):
    if not OPENCODE:
        raise RuntimeError("opencode executable not found")
    command = [
        OPENCODE,
        "run",
        "--standalone",
        "--model",
        MODEL,
        "--format",
        "json",
        "--title",
        title,
        "--file",
        relative(prompt_path),
        "严格执行附加文件中的任务。禁止 Web Search 和 Web Fetch，不需要联网。",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=7200,
            check=False,
        )
    result = save_event_result(log_path, result_path)
    if completed.returncode != 0:
        raise RuntimeError(
            f"OpenCode exit {completed.returncode}: {result['errors'] or 'see log'}"
        )
    if not result["text"]:
        raise RuntimeError("OpenCode returned no final text")
    return result


def prompt_header(task, role):
    return f"""你是 2026 年语文试卷结构化批次的{role}。当前工作目录是 {ROOT}。
只处理 entry_key `{task['entry_key']}`，不得扫描、转换或修改其他试卷。

任务包：{relative(STATE_DIR / 'chinese/tasks.json')} 中对应 entry_key 的任务。
输出目录：{task['output_dir']}
原卷：{json.dumps(task['source_files'], ensure_ascii=False)}
答案附件：[]。每卷只有一个原始附件；若试题和答案同在一个文件，source.answer_files 省略，answer.source 使用 question_document。不得把 DOCX/PDF 的重复副本冒充单独答案附件。
试卷元数据：{json.dumps(task['paper_metadata'], ensure_ascii=False)}

必读并完整遵守 docs/QUESTION_JSON_FORMAT.md、docs/question-json-v1.schema.json、docs/PDF_TO_JSON_AGENT_PROMPT.md。
禁止 Web Search、Web Fetch 和联网查找答案。只忠实提取，不解题，不编造答案、解析、分值或难度。
不得修改原始附件、CSV、规范、Schema、脚本、全局 status.json 或其他输出。不要全局安装依赖；可用 uv 在临时环境使用 pymupdf、pypdf、python-docx、Pillow、jsonschema。运行 Python 读取原始附件时使用 python -I。
"""


def extraction_prompt(task, repair=None):
    text = prompt_header(task, "提取子代理" if repair is None else "返修子代理")
    text += """
先检查原卷页数、文本层、扫描页、插图和答案页。逐页逐题提取所有题目、长阅读材料、选项、答案及原卷明确分值；保留原始题号、小问层级、原文和跨页关系。长文章只存一次，叶子小问必须能通过祖先链取得完整上下文。图片裁剪为具体配图，保留图中文字与标注，不能用整页截图代替文本。
"""
    if repair:
        text += f"""
这是第 {repair['attempt']} 次返修。先读取上一轮独立复核结果 {repair['report']}，逐项修复全部 reject 问题，并重新逐页逐图检查整卷，不得只改摘要，也不得通过删除题目、材料、图片或答案来通过校验。
"""
    if repair and repair["attempt"] == 4:
        text += TARGETED_REPAIR_4[task["entry_key"]]
    if repair and repair["attempt"] == 5:
        text += TARGETED_REPAIR_5[task["entry_key"]]
    text += f"""
必须实际生成 {task['output_dir']}/paper.json 和确有需要的 assets。所有路径与 SHA-256 按实际文件计算。实际执行 Draft 2020-12 Schema、引用、相对路径检查，并逐页对照原卷、实际打开每张图片。初始 extraction.status 只能为 draft 或 needs_review，不能标 verified。输出目录只保留 paper.json 和实际引用资源。
可运行 python -I scripts/validate_exam_paper.py "{task['output_dir']}/paper.json" --schema docs/question-json-v1.schema.json。
最后简洁返回输出路径、顶层题数、最小作答单元数、材料数、图片数、Schema 结果、图片复核、疑点及阻塞原因，不要粘回整份 JSON。
"""
    return text


def review_prompt(task, attempt, previous_report=None):
    text = prompt_header(task, "独立复核子代理")
    text += f"""
你与提取/返修代理不是同一会话。只读复核，不得修改 paper.json、assets 或其他项目文件；可在系统临时目录渲染页面。这是第 {attempt + 1} 次独立复核。
"""
    if previous_report:
        text += f"先读取上一轮复核结果 {previous_report}，逐项验证所有问题已修复，然后仍要完整复核整卷。\n"
    text += f"""
实际读取原卷、{task['output_dir']}/paper.json 和 assets。逐页逐题对照题目覆盖、原始题号、大小问、选项、材料完整性、答案对应和分值；检查长文只存一次、祖先链完整、共享选项和图片正确复用。检查无 Agent 自编答案、解析、分值或难度。
逐张实际打开图片，检查清晰度、裁剪、方向、图中文字与归属，不能只检查文件存在。独立运行 Draft 2020-12 Schema、引用、路径、SHA-256 和同级 ID 检查。可运行 python -I scripts/validate_exam_paper.py "{task['output_dir']}/paper.json" --schema docs/question-json-v1.schema.json，但不能只凭 Schema 验收。
最后必须明确返回 accept 或 reject。reject 时列出具体页码、题号、字段、资源 ID 和原因；accept 时说明实际核对范围。不要修改提取状态，不要粘回整份 JSON。
"""
    return text


def validate(task, report_path):
    with report_path.open("w", encoding="utf-8") as output:
        completed = subprocess.run(
            [
                shutil.which("python") or "python",
                "-I",
                relative(VALIDATOR),
                f"{task['output_dir']}/paper.json",
                "--schema",
                relative(SCHEMA),
            ],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=False,
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if completed.returncode != 0 or not report["valid"]:
        raise RuntimeError(f"Validation failed: {report.get('errors', 'see report')}")
    return report


def parse_verdict(text):
    lowered = text.lower()
    conclusions = re.findall(
        r"(?:最终结论|最终意见|复核结论|结论)[^\n]*?\b(accept|reject)\b",
        lowered,
    )
    if conclusions:
        return conclusions[-1]
    standalone = re.findall(r"^\s*\*{0,2}`?(accept|reject)`?\*{0,2}\s*$", lowered, re.M)
    return standalone[-1] if standalone else "unknown"


def process(task):
    key = task["entry_key"]
    with STATUS_LOCK:
        current_status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        saved_task = current_status["chinese_batch"]["tasks"][
            current_status["chinese_batch"]["index"][key]
        ]
        if saved_task["status"] == "blocked":
            return
    stem = f"{task['sequence']:03d}"
    task_dir = BATCH_DIR / stem
    task_dir.mkdir(parents=True, exist_ok=True)
    paper_path = ROOT / task["output_dir"] / "paper.json"

    if paper_path.is_file():
        try:
            data = json.loads(paper_path.read_text(encoding="utf-8"))
            if data["extraction"]["status"] == "verified":
                report = validate(task, task_dir / "validate_existing.json")
                update_status(
                    lambda status: status["chinese_batch"]["tasks"][
                        status["chinese_batch"]["index"][key]
                    ].update(
                        status="verified",
                        extraction_status="verified",
                        review_result="resume_skip",
                        stats=report,
                    )
                )
                return
        except Exception as error:
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(status="pending", resume_error=str(error))
            )

    update_status(
        lambda status: status["chinese_batch"]["tasks"][
            status["chinese_batch"]["index"][key]
        ].update(status="assigned", started_at=now())
    )
    resume_stage = None
    with STATUS_LOCK:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        saved = status["chinese_batch"]["tasks"][
            status["chinese_batch"]["index"][key]
        ]
        resume_stage = saved.get("paused_from")
        previous_report = saved.get("review_report")
    start_attempt = saved.get("resume_attempt", saved.get("retry_count", 0))

    for attempt in range(start_attempt, MAX_RETRY + 1):
        try:
            if not (resume_stage == "in_review" and paper_path.is_file()):
                prompt_path = task_dir / (
                    f"extract_{attempt}.md" if attempt == 0 else f"repair_{attempt}.md"
                )
                prompt_path.write_text(
                    extraction_prompt(
                        task,
                        None
                        if attempt == 0
                        else {"attempt": attempt, "report": previous_report},
                    ),
                    encoding="utf-8",
                )
                extraction = run_opencode(
                    prompt_path,
                    task_dir / f"extract_{attempt}.jsonl",
                    task_dir / f"extract_{attempt}_result.json",
                    f"2026 语文 {stem} 提取" if attempt == 0 else f"2026 语文 {stem} 返修 {attempt}",
                )
                update_status(
                    lambda status: status["chinese_batch"]["tasks"][
                        status["chinese_batch"]["index"][key]
                    ]["extraction_sessions"].append(extraction["session_id"])
                )
            report = validate(task, task_dir / f"validate_{attempt}.json")
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(
                    status="extracted",
                    retry_count=attempt,
                    extraction_status=json.loads(
                        paper_path.read_text(encoding="utf-8")
                    )["extraction"]["status"],
                    stats=report,
                    validation=relative(task_dir / f"validate_{attempt}.json"),
                )
            )

            resume_stage = None
            review_prompt_path = task_dir / f"review_{attempt}.md"
            review_prompt_path.write_text(
                review_prompt(task, attempt, previous_report), encoding="utf-8"
            )
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(status="in_review")
            )
            review = run_opencode(
                review_prompt_path,
                task_dir / f"review_{attempt}.jsonl",
                task_dir / f"review_{attempt}_result.json",
                f"2026 语文 {stem} 独立复核 {attempt + 1}",
            )
            verdict = parse_verdict(review["text"])
            is_reject = verdict == "reject"
            is_accept = verdict == "accept"
            previous_report = relative(task_dir / f"review_{attempt}_result.json")
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(
                    review_result=verdict,
                    review_report=previous_report,
                )
            )
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ]["review_sessions"].append(review["session_id"])
            )
            if is_reject or not is_accept:
                if attempt >= MAX_RETRY:
                    raise RuntimeError("Review did not accept after maximum retries")
                update_status(
                    lambda status: status["chinese_batch"]["tasks"][
                        status["chinese_batch"]["index"][key]
                    ].update(status="needs_review", retry_count=attempt + 1)
                )
                continue

            data = json.loads(paper_path.read_text(encoding="utf-8"))
            data["extraction"]["status"] = "verified"
            write_json(paper_path, data)
            report = validate(task, task_dir / "validate_verified.json")
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(
                    status="verified",
                    extraction_status="verified",
                    stats=report,
                    completed_at=now(),
                )
            )
            return
        except Exception as error:
            update_status(
                lambda status: status["chinese_batch"]["tasks"][
                    status["chinese_batch"]["index"][key]
                ].update(
                    status="blocked",
                    blocked_reason=str(error),
                    updated_at=now(),
                )
            )
            return


def main():
    tasks = build_tasks()
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    write_json(BATCH_DIR / "tasks.json", tasks)

    def initialize(status):
        existing = {
            item["entry_key"]: item
            for item in status.get("chinese_batch", {}).get("tasks", [])
        }
        batch_tasks = []
        for task in tasks:
            item = existing.get(task["entry_key"], task_status(task))
            if (
                item["status"] == "blocked"
                and item.get("blocked_reason") == "Review did not accept after maximum retries"
                and item.get("retry_count", 0) < MAX_RETRY
            ):
                item["resume_attempt"] = item.get("retry_count", 0) + 1
                item["paused_from"] = "needs_review"
                item["status"] = "pending"
                item.pop("blocked_reason", None)
                item.setdefault("history", []).append(
                    "additional targeted repair authorized by user"
                )
            if item["status"] == "blocked" and "Failed to execute statement" in item.get("blocked_reason", ""):
                if item.get("retry_count", 0) > 0:
                    item["paused_from"] = "needs_review"
                elif item.get("stats"):
                    item["paused_from"] = "in_review"
                else:
                    item["paused_from"] = "assigned"
                item["status"] = "pending"
                item.pop("blocked_reason", None)
                item.setdefault("history", []).append(
                    "retry OpenCode execution failure at reduced parallelism"
                )
            if item["status"] in {"assigned", "extracted", "in_review", "needs_review", "paused"}:
                previous = item["status"]
                if previous != "paused":
                    item["paused_from"] = previous
                item["status"] = "pending"
                item.setdefault("history", []).append(
                    f"reset {previous} after orchestrator restart"
                )
            batch_tasks.append(item)
        status["chinese_batch"] = {
            "model": MODEL,
            "max_parallel": MAX_PARALLEL,
            "max_retry": MAX_RETRY,
            "paused": False,
            "total": len(tasks),
            "started_at": status.get("chinese_batch", {}).get("started_at", now()),
            "updated_at": now(),
            "index": {task["entry_key"]: index for index, task in enumerate(tasks)},
            "tasks": batch_tasks,
        }

    update_status(initialize)
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        futures = [executor.submit(process, task) for task in tasks]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    print("Chinese batch ended; inspect status.json for verified and blocked counts.")


if __name__ == "__main__":
    main()
