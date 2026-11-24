from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
papers = json.loads((ROOT / "data" / "papers.json").read_text(encoding="utf-8"))
keys = [(p["year"], p["kind"], p["region_or_school"], p["subject"], p.get("variant", "")) for p in papers]
assert len(keys) == len(set(keys)), "存在重复卷目"
assert all(p["source_urls"] for p in papers), "存在无出处条目"
assert all(p["year"] in {2024, 2025, 2026} for p in papers), "年份越界"
assert all(p["kind"] in {"零模", "一模", "二模", "三模"} for p in papers), "包含非模拟卷"
assert all(p["subject"] in {"语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"} for p in papers), "科目异常"
for p in papers:
    title = p["title"].replace("思想政治", "政治")
    for subject in {"语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"} - {p["subject"]}:
        assert f"{subject}试题" not in title and f"{subject}试卷" not in title, f"科目与标题不符：{p['title']}"

ranges = {
    "语文": (18, 26), "数学": (18, 23), "英语": (38, 55),
    "物理": (17, 22), "化学": (16, 22), "生物": (18, 23),
    "政治": (18, 23), "历史": (18, 23), "地理": (18, 23),
}
anomalies = []
for p in papers:
    count = p.get("question_count")
    if count == "" or count is None:
        continue
    low, high = ranges[p["subject"]]
    if not low <= int(count) <= high:
        anomalies.append({"title": p["title"], "count": count, "basis": p["count_basis"]})

html = (ROOT / "index.html").read_text(encoding="utf-8")
script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
result = subprocess.run(["node", "--check", "-"], input=script, text=True, encoding="utf-8", capture_output=True)
assert result.returncode == 0, result.stderr

report = {
    "scope": "2024-2026年模拟卷（不含真题）",
    "papers": len(papers),
    "by_year_kind": {f"{y}-{k}": sum(p["year"] == y and p["kind"] == k for p in papers) for y in range(2024, 2027) for k in ["零模", "一模", "二模", "三模"]},
    "count_status": dict(Counter(p["count_status"] for p in papers)),
    "question_count_anomalies": anomalies,
    "unique_keys": True,
    "html_javascript_syntax": True,
}
(ROOT / "data" / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False, indent=2))
