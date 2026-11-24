"""Prepare the three-paper 2026 OpenCode pilot without modifying source data."""

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY_CSV = ROOT / "data/original_downloads/entries.csv"
FILE_CSV = ROOT / "data/original_downloads/files.csv"
STATE_DIR = ROOT / "data/question_extraction/2026"
MODEL = "opencode-go/deepseek-v4.1-flash"
EFFORT = "high"
PILOT_KEYS = [
    "2026|零模|北京二中|语文|",
    "2026|零模|北师大实验中学|数学|",
    "2026|零模|北师大实验中学|化学|",
]


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json_atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main():
    entries = read_csv(ENTRY_CSV)
    files = read_csv(FILE_CSV)
    eligible = [
        entry
        for entry in entries
        if entry["year"] == "2026" and entry["status"] == "已获得原始附件"
    ]
    by_key = {entry["entry_key"]: entry for entry in eligible}
    tasks = []

    for entry_key in PILOT_KEYS:
        entry = by_key[entry_key]
        source_files = []
        for record in files:
            if not (
                record["entry_key"] == entry_key
                and record["year"] == "2026"
                and record["file_type"] == "原始附件"
                and record["status"] != "下载失败"
            ):
                continue
            path = ROOT / record["local_path"]
            if path.is_file() and path.stat().st_size > 0:
                source_files.append(path.relative_to(ROOT).as_posix())
        if not source_files:
            raise RuntimeError(f"No usable source file for {entry_key}")

        output_dir = (
            ROOT
            / "parsed"
            / entry["subject"]
            / str(entry["year"])
            / entry["kind"]
            / Path(source_files[0]).name
        )

        metadata = {
            "id": entry_key,
            "title": entry["title"],
            "year": 2026,
            "kind": entry["kind"],
            "subject": entry["subject"],
            "district": entry["district"],
        }
        if entry["region_or_school"] != entry["district"]:
            metadata["school"] = entry["region_or_school"]
        if entry["variant"]:
            metadata["variant"] = entry["variant"]

        tasks.append(
            {
                "entry_key": entry_key,
                "sequence": int(entry["sequence"]),
                "source_files": source_files,
                "answer_files": [],
                "auxiliary_files": [],
                "output_dir": output_dir.relative_to(ROOT).as_posix(),
                "paper_metadata": metadata,
                "complexity": "normal" if entry["subject"] == "语文" else "complex",
                "selection_reason": {
                    "语文": "长阅读材料与 DOCX 内答案",
                    "数学": "公式、插图和无答案来源",
                    "化学": "复杂公式、表格、配图与版面",
                }[entry["subject"]],
            }
        )

    status = {
        "year": 2026,
        "mode": "pilot",
        "max_papers": 3,
        "max_parallel": 3,
        "max_retry": 2,
        "model": MODEL,
        "effort": EFFORT,
        "eligible_entry_count": len(eligible),
        "skipped_no_original": [
            entry["entry_key"]
            for entry in entries
            if entry["year"] == "2026" and entry["status"] != "已获得原始附件"
        ],
        "resume_skips": [
            {
                "entry_key": "2026|零模|八十中|物理|",
                "reason": "existing paper.json is verified; source SHA-256 and Schema checks passed",
            }
        ],
        "tasks": [
            {
                "entry_key": task["entry_key"],
                "output_dir": task["output_dir"],
                "status": "pending",
                "retry_count": 0,
                "extraction_session": None,
                "review_sessions": [],
            }
            for task in tasks
        ],
    }
    write_json_atomic(STATE_DIR / "pilot_tasks.json", tasks)
    write_json_atomic(STATE_DIR / "status.json", status)
    print(f"Prepared {len(tasks)} pilot tasks; eligible entries: {len(eligible)}")


if __name__ == "__main__":
    main()
