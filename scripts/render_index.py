from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
papers = json.loads((DATA / "papers.json").read_text(encoding="utf-8"))

counts = Counter((p["year"], p["kind"]) for p in papers)
status = Counter(p["count_status"] for p in papers)
KINDS = ["零模", "一模", "二模", "三模"]
summary = []
for year in range(2024, 2027):
    row = {"year": year}
    for kind in KINDS:
        row[kind] = counts[year, kind]
    row["合计"] = sum(row[k] for k in KINDS)
    summary.append(row)

with (DATA / "year_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["year"] + KINDS + ["合计"])
    writer.writeheader()
    writer.writerows(summary)

payload = json.dumps(papers, ensure_ascii=False).replace("</", "<\\/")
summary_json = json.dumps(summary, ensure_ascii=False)
html = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>北京高考模拟卷索引（2024—2026）</title>
<style>
:root{font-family:system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#172033;background:#f5f7fb}body{margin:0;padding:24px;max-width:1600px;margin:auto}h1{font-size:26px;margin:0 0 8px}.muted{color:#5b6475;line-height:1.65}.panel{background:white;border:1px solid #e2e7f0;border-radius:12px;padding:16px;margin:16px 0}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}input,select{box-sizing:border-box;width:100%;padding:9px;border:1px solid #cbd3e1;border-radius:7px;background:white}table{border-collapse:collapse;width:100%;font-size:14px;background:white}th,td{padding:9px;border-bottom:1px solid #e7ebf2;text-align:left;vertical-align:top}th{position:sticky;top:0;background:#edf2fa;z-index:1}tr:hover{background:#f8faff}.num{text-align:center;white-space:nowrap}.badge{display:inline-block;padding:2px 7px;border-radius:10px;background:#edf0f5;white-space:nowrap}.ok{background:#def4e7}.auto{background:#fff1c9}.pending{background:#fee6e4}.sources{max-width:330px}.sources a{display:inline-block;margin:0 5px 3px 0}.scroll{max-height:72vh;overflow:auto;border:1px solid #e2e7f0;border-radius:10px}.summary td,.summary th{text-align:center}.warning{border-left:4px solid #d48a13}.right{margin-left:auto}.toolbar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}code{background:#edf0f5;padding:2px 5px;border-radius:4px}@media(max-width:800px){body{padding:12px}.hide-mobile{display:none}.sources{max-width:220px}}
</style>
</head>
<body>
<h1>北京高考模拟卷索引（2024—2026）</h1>
<div class="muted">仅统计2024、2025、2026年零模、一模、二模、三模及校级模拟，不含正式高考真题和其余年份。题数为最大连续编号题数；点击“来源”查看出处。</div>
<div class="panel warning"><strong>覆盖边界：</strong>北京没有公开、完整、官方的“全部模拟卷”名录。本索引是已核查公开目录和可确认单卷的去重集合，不等于所有学校实际考试的全集；零模和校级卷尤其可能缺漏。付费或需登录的卷目会标明，题数无法可靠识别时保留为空。</div>
<div class="panel"><div id="summary"></div></div>
<div class="panel filters">
<input id="q" type="search" placeholder="搜索卷名、区/学校、URL">
<select id="year"><option value="">全部年份</option></select>
<select id="kind"><option value="">全部类型</option></select>
<select id="subject"><option value="">全部科目</option></select>
<select id="status"><option value="">全部题数状态</option></select>
</div>
<div class="panel toolbar"><strong id="shown"></strong><span class="muted">同一卷的正卷、答案和镜像页只计一次；自动识别结果需抽检。</span><a class="right" href="data/papers.csv">下载 CSV</a><a href="data/papers.json">下载 JSON</a><a href="data/source_catalogs.csv">题库目录</a><a href="data/gaps.csv">缺口清单</a></div>
<div class="scroll"><table><thead><tr><th>年份</th><th>类型</th><th>区/学校</th><th>科目</th><th>卷名</th><th class="num">题数</th><th class="num">部分数</th><th>状态</th><th>来源</th></tr></thead><tbody id="rows"></tbody></table></div>
<script>
const papers=__DATA__;
const summary=__SUMMARY__;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const host=u=>{try{return new URL(u).hostname.replace(/^www\./,'')}catch{return u}};
function fill(id,values){const el=document.getElementById(id);[...new Set(values)].sort().forEach(v=>el.insertAdjacentHTML('beforeend',`<option>${esc(v)}</option>`))}
fill('year',papers.map(x=>x.year));fill('kind',papers.map(x=>x.kind));fill('subject',papers.map(x=>x.subject));fill('status',papers.map(x=>x.count_status));
document.getElementById('summary').innerHTML='<table class="summary"><tr><th>年份</th><th>零模</th><th>一模</th><th>二模</th><th>三模</th><th>合计</th></tr>'+summary.map(x=>`<tr><td>${x.year}</td><td>${x.零模}</td><td>${x.一模}</td><td>${x.二模}</td><td>${x.三模}</td><td><strong>${x.合计}</strong></td></tr>`).join('')+'</table>';
function badge(s){const c=s==='已核实'?'ok':s.includes('自动')?'auto':s.includes('待')||s.includes('失败')?'pending':'';return `<span class="badge ${c}">${esc(s)}</span>`}
function sources(p){const urls=[...new Set(p.source_urls||[])];return `<details class="sources"><summary>${urls.length} 条出处</summary>${urls.map((u,i)=>`<a href="${esc(u)}" target="_blank" rel="noopener">${i+1}. ${esc(host(u))}</a>`).join('')}</details>`}
function row(p){return `<tr><td>${p.year}</td><td>${esc(p.kind)}</td><td>${esc(p.region_or_school)}</td><td>${esc(p.subject)}</td><td><a href="${esc(p.primary_url)}" target="_blank" rel="noopener">${esc(p.title)}</a>${p.verification_note?`<div class="muted">${esc(p.verification_note)}</div>`:''}</td><td class="num"><strong>${esc(p.question_count)||'—'}</strong></td><td class="num">${esc(p.section_count)||'—'}</td><td>${badge(p.count_status)}</td><td>${sources(p)}</td></tr>`}
function render(){const q=document.getElementById('q').value.trim().toLowerCase();const year=document.getElementById('year').value,kind=document.getElementById('kind').value,subject=document.getElementById('subject').value,status=document.getElementById('status').value;const out=papers.filter(p=>(!year||p.year==year)&&(!kind||p.kind===kind)&&(!subject||p.subject===subject)&&(!status||p.count_status===status)&&(!q||JSON.stringify(p).toLowerCase().includes(q)));document.getElementById('rows').innerHTML=out.map(row).join('');document.getElementById('shown').textContent=`显示 ${out.length} / ${papers.length} 卷`}
document.querySelectorAll('input,select').forEach(x=>x.addEventListener('input',render));render();
</script>
</body></html>"""
html = html.replace("__DATA__", payload).replace("__SUMMARY__", summary_json)
(ROOT / "index.html").write_text(html, encoding="utf-8")

lines = [
    "# 北京高考模拟卷索引（2024—2026）",
    "",
    f"当前共 {len(papers)} 个去重卷目。直接使用 `index.html` 搜索筛选；机器可读文件为 `data/papers.csv` 和 `data/papers.json`。",
    "",
    "## 统计口径",
    "",
    "- `question_count`：最大连续编号题数；`section_count`：卷面大部分数。二者不可混用。",
    "- 英语只统计笔试编号题，不含听说机考；未编号作文不额外加 1。",
    "- 同一卷的试题、答案、镜像页合并到 `source_urls`，不重复计卷。",
    "- 仅统计2024、2025、2026年模拟卷；正式高考真题、其余年份、月考、期中期末均不计入。",
    "- `自动识别待抽检`：从 PDF 文本层读取；`待核`：扫描件、无附件、需登录或无法可靠识别。",
    "",
    "## 覆盖边界",
    "",
    "北京没有公开、完整、官方的“全部模拟卷”名录。本索引是已核查公开目录和可确认单卷的去重集合，不代表所有学校实际考试的全集；零模、三模、校级考前卷仍可能缺漏。未找到题数的条目保留空值。",
    "",
    "## 年度数量",
    "",
    "| 年份 | 零模 | 一模 | 二模 | 三模 | 合计 |",
    "|---|---:|---:|---:|---:|---:|",
]
for row in summary:
    lines.append(f"| {row['year']} | {row['零模']} | {row['一模']} | {row['二模']} | {row['三模']} | {row['合计']} |")
lines += ["", "## 题数状态", ""]
for name, count in sorted(status.items()):
    lines.append(f"- {name}: {count}")
lines += ["", "题库入口见 `data/source_catalogs.csv`；覆盖缺口及未纳入候选见 `data/gaps.csv`；复跑脚本为 `scripts/build_index.py`、`scripts/render_index.py` 和 `scripts/validate_index.py`。", ""]
(ROOT / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
print(f"rendered {len(papers)} papers")
