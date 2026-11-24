from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import re
import subprocess
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "cache"
YEARS = (2024, 2025, 2026)
MOCK_KINDS = ("零模", "一模", "二模", "三模")
SUBJECTS = {"语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"}
DISTRICTS = ["东城", "西城", "朝阳", "海淀", "丰台", "石景山", "通州", "顺义", "昌平", "大兴", "房山", "门头沟", "怀柔", "平谷", "密云", "延庆", "燕山"]
CATALOGS = [
    ("一模", "https://www.gaokzx.com/gk/shitiku/153215.html"),
    ("一模", "https://www.gaokzx.com/gk/shitiku/136325.html"),
    ("一模", "https://www.gaokzx.com/c/202202/58924.html"),
    ("二模", "https://www.gaokzx.com/gk/shitiku/154590.html"),
    ("二模", "https://www.gaokzx.com/gk/shitiku/138765.html"),
    ("二模", "https://www.gaokzx.com/c/202204/60482.html"),
    ("三模", "https://www.gaokzx.com/gk/shitiku/155655.html"),
    ("三模", "https://www.gaokzx.com/gk/shitiku/140775.html"),
    ("三模", "https://www.gaokzx.com/gk/shitiku/122595.html"),
    ("一模", "https://www.zgkao.com/shitiku/79294.html"),
    ("二模", "https://www.zgkao.com/shitiku/81557.html"),
    ("零模", "https://www.gaokzx.com/gk/shitiku/120147.html"),
    ("零模", "https://www.zgkao.com/shitiku/80232.html"),
]
KEWAI_CATALOGS = [
    ("语文", "http://kewai100.cn/sys-nd/1194.html"),
    ("数学", "http://kewai100.cn/sys-nd/1195.html"),
    ("英语", "http://kewai100.cn/sys-nd/1193.html"),
    ("物理", "http://kewai100.cn/sys-nd/1196.html"),
    ("化学", "http://kewai100.cn/sys-nd/1197.html"),
    ("生物", "http://kewai100.cn/sys-nd/1198.html"),
    ("历史", "http://kewai100.cn/sys-nd/1199.html"),
    ("地理", "http://kewai100.cn/sys-nd/1200.html"),
    ("政治", "http://kewai100.cn/sys-nd/1201.html"),
]
EXTRA_MOCK_PAPERS = [
    (2024, "三模", "人大附中", "数学", "三模", "2024年北京人大附中高三三模数学试题及答案", "https://www.gaokzx.com/gk/shitiku/122729.html"),
    (2024, "三模", "北师大二附中", "化学", "保温", "2024北京北师大二附中高三保温卷化学试题及答案", "https://www.gaokzx.com/gk/shitiku/123020.html"),
    (2024, "三模", "人大附中", "数学", "考前提醒", "2024年北京人大附中高三考前提醒数学试题及答案", "https://www.gaokzx.com/gk/shitiku/123059.html"),
    (2024, "三模", "顺义一中", "化学", "三模", "2024年北京顺义一中高三三模化学试题及答案", "https://www.gaokzx.com/gk/shitiku/122872.html"),
    (2024, "二模", "人大附中", "物理", "校级模拟", "2024届北京市人大附中高三下学期二模模拟物理试题", "https://www.21cnjy.com/H/6/34850/20356503.shtml"),
    (2025, "零模", "人大附中", "化学", "", "2025年北京人大附中高三零模化学试题及答案", "https://www.gaokzx.com/gk/shitiku/137120.html"),
    (2025, "零模", "北师大实验中学", "化学", "", "2025年北京北师大实验中学高三零模化学试题及答案", "https://www.gaokzx.com/gk/shitiku/137121.html"),
    (2025, "零模", "北师大实验中学", "物理", "", "2025北京北师大实验中学高三零模物理试题及答案解析", "http://www.wulixuexi.com/gaosan/kaixuekao/2465.html"),
    (2025, "零模", "北京四中", "化学", "", "北京四中2024—2025学年第二学期高三年级零模化学试卷", "https://zy.21cnjy.com/22655529"),
    (2025, "零模", "北京四中", "物理", "", "2025北京四中高三零模物理试题及答案解析", "http://www.wulixuexi.com/gaosan/yimo/2568.html"),
    (2025, "零模", "北京四中", "英语", "", "2025年北京西城四中高三英语零模考试", "http://mp.weixin.qq.com/s?__biz=MjM5MDY5NDQyMw==&mid=2653097154&idx=1&sn=ba605b23969a086fd6c25786b4a27a2b"),
    (2025, "零模", "八一学校", "数学", "", "北京市八一学校2025届高三下学期3月零模数学试题", "https://www.zxxk.com/soft/51211452.html"),
    (2025, "零模", "北京四中顺义分校", "物理", "", "2025年北京四中顺义区分校高考物理零模试卷", "https://m.51jiaoxi.com/doc-16860199.html"),
    (2026, "零模", "北师大实验中学", "数学", "", "2026北师大实验中学高三零模数学试题", "https://www.gaokzx.com/gk/shitiku/153783.html"),
    (2026, "零模", "北师大实验中学", "化学", "", "2026北师大实验中学高三零模化学试题及答案", "https://www.gaokzx.com/gk/shitiku/153825.html"),
    (2026, "零模", "八十中", "物理", "", "2026北京八十中高三零模物理试题及答案解析", "https://www.wulixuexi.com/gaosan/yimo/2840.html"),
    (2026, "一模", "通州", "物理", "", "2026北京通州高三一模物理试题及答案解析", "https://www.wulixuexi.com/mobile/index/show/catid/2/id/2959.html"),
    (2024, "二模", "通州", "历史", "", "2024届北京市通州区高三下学期4月模拟考试（二模）历史试题", "https://www.51jiaoxi.com/doc-15715483.html"),
    (2024, "二模", "通州", "政治", "", "2024届北京市通州区高三二模政治试卷", "https://www.51jiaoxi.com/doc-15687157.html"),
    (2024, "三模", "北京精华学校", "物理", "三模", "2024北京大兴精华学校高三三模物理试题及答案解析", "https://www.wulixuexi.com/mobile/index/show/catid/6/id/2541.html"),
    (2025, "零模", "怀柔一中", "政治", "", "怀柔一中2024—2025学年第二学期高三零模练习政治试题", "http://mp.weixin.qq.com/s?__biz=MzI2NjQ5NTQ2Nw==&mid=2247586681&idx=4&sn=4f71ae072ba9d8e1fe301191e8ad9161"),
    (2025, "一模", "大兴一中", "物理", "", "2025届北京市大兴一中高三一模物理试题", "https://www.51jiaoxi.com/doc-17311097.html"),
    (2025, "三模", "北京精华学校", "物理", "三模", "2025北京大兴精华学校高三三模物理试题及答案解析", "http://www.wulixuexi.com/gaosan/sanmo/2707.html"),
    (2026, "一模", "大兴", "数学", "", "2026届北京市大兴区高三第一次模拟考试数学试卷", "https://www.51jiaoxi.com/doc-18302847.html"),
    (2026, "一模", "大兴", "物理", "", "2026届北京市大兴区高考物理一模试卷", "https://www.51jiaoxi.com/doc-17757547.html"),
    (2026, "一模", "怀柔", "物理", "", "2026届北京市怀柔区高三3月份第一次模拟考试物理试卷", "https://www.51jiaoxi.com/doc-17757716.html"),
    (2026, "二模", "通州", "数学", "", "2026届北京市通州区高三第二次模拟考试数学试卷", "https://www.51jiaoxi.com/doc-18302741.html"),
    (2026, "二模", "通州", "物理", "", "2026届北京市通州区高三第二次模拟考试物理试卷", "https://www.51jiaoxi.com/doc-17762982.html"),
    (2024, "零模", "朝阳", "语文", "", "2024年北京市朝阳区高三语文零模试题及答案（2024.3）", "https://www.taodocs.com/p-1044555628.html"),
    (2024, "一模", "平谷", "数学", "", "2024年北京市平谷区高三年级一模数学试卷及参考答案", "https://www.51jiaoxi.com/doc-15665847.html"),
    (2025, "零模", "北京八中", "语文", "", "北京八中2025届高三语文第二学期零模试题（2025.3.24）", "http://mp.weixin.qq.com/s?__biz=Mzk0NTIwNDk1Ng==&mid=2247504738&idx=1&sn=7c46b7f9650c206ce404792009dd6e12"),
    (2026, "一模", "延庆", "历史", "", "2026北京延庆高三一模历史试题及答案", "https://www.gaokzx.com/gk/shitiku/153479.html"),
]
EXTRA_MOCK_PAPERS += [
    (2024, "二模", "顺义", subject, "", f"北京市顺义区2024届高三第二次质量监测{subject}试卷", f"https://www.51jiaoxi.com/doc-{doc_id}.html")
    for subject, doc_id in {
        "语文": 16147415, "数学": 15653946, "英语": 15704865, "物理": 15720850,
        "化学": 16156189, "生物": 15684965, "历史": 16920084, "地理": 15917155,
    }.items()
]
EXTRA_MOCK_PAPERS += [
    (2024, "二模", "房山", subject, "", f"2024北京房山高三二模{subject}试题及答案", "https://app.gaokaozhitongche.com/newsexam/h/7DRA7jyg")
    for subject in ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"]
]
EXTRA_MOCK_PAPERS += [
    (2024, "二模", "海淀", subject, "查漏补缺", f"2024北京高三海淀二模查漏补缺{subject}试题及答案", f"https://www.gaokzx.com/gk/shitiku/{page_id}.html")
    for subject, page_id in {
        "数学": 122679, "物理": 122683, "化学": 122686, "生物": 122607,
        "地理": 122684, "政治": 122685, "历史": 122760, "英语": 122759,
    }.items()
]
EXTRA_MOCK_PAPERS += [
    (2024, "二模", "大兴", subject, "查漏补缺", f"2024北京大兴高三二模查漏补缺{subject}试题及答案", f"https://www.gaokzx.com/gk/shitiku/{page_id}.html")
    for subject, page_id in {
        "语文": 122687, "化学": 122761, "生物": 122711,
        "地理": 122713, "政治": 122709, "英语": 122708,
    }.items()
]
EXTRA_MOCK_PAPERS += [
    (2026, "二模", "海淀", subject, "查漏补缺", f"2026北京海淀区高三二模查漏补缺{subject}试题及答案", f"https://www.gaokzx.com/gk/shitiku/{page_id}.html")
    for subject, page_id in {
        "语文": 155554, "数学": 155656, "英语": 155657, "物理": 155658, "化学": 155659,
        "生物": 155663, "政治": 155660, "历史": 155661, "地理": 155662,
    }.items()
]
EXTRA_MOCK_PAPERS += [
    (2026, "零模", "北京二中", subject, "", f"2026北京第二中学高三下零模{subject}试题及答案", "https://app.gaokaozhitongche.com/newsexam/h/bD1e0ZQD")
    for subject in ["语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理"]
]


def clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


class Node:
    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []

    def text(self) -> str:
        return clean("".join(c if isinstance(c, str) else c.text() for c in self.children))

    def links(self) -> list[str]:
        found = [self.attrs["href"]] if self.tag == "a" and self.attrs.get("href") else []
        for child in self.children:
            if isinstance(child, Node):
                found.extend(child.links())
        return found


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables: list[Node] = []
        self.stack: list[Node] = []

    def handle_starttag(self, tag: str, attrs):
        if tag not in {"table", "tr", "td", "th", "a", "p", "span", "strong"}:
            return
        node = Node(tag, dict(attrs))
        if self.stack:
            self.stack[-1].children.append(node)
        if tag == "table":
            self.tables.append(node)
        self.stack.append(node)

    def handle_endtag(self, tag: str):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str):
        if self.stack:
            self.stack[-1].children.append(data)


def children(node: Node, tag: str) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node) and c.tag == tag]


def get(url: str, binary: bool = False):
    CACHE.mkdir(parents=True, exist_ok=True)
    suffix = ".pdf" if binary else ".html"
    target = CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + suffix)
    if target.exists() and target.stat().st_size:
        data = target.read_bytes()
        if not binary and data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
            target.write_bytes(data)
        return data
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
    response.raise_for_status()
    data = response.content
    if not data:
        result = subprocess.run(["curl", "-L", "--compressed", "--max-time", "45", "-A", "Mozilla/5.0", "-sS", url], capture_output=True)
        data = result.stdout
    if not data:
        raise ValueError(f"空响应：{url}")
    target.write_bytes(data)
    time.sleep(0.08)
    return data


def page(url: str) -> str:
    return get(url).decode("utf-8", errors="replace")


def page_title(url: str) -> str:
    try:
        source = page(url)
    except Exception:
        return ""
    match = re.search(r"<title>(.*?)</title>", source, re.I | re.S)
    return clean(match.group(1)).removesuffix("_北京高考在线") if match else ""


def table_rows(table: Node) -> list[list[Node]]:
    rows = []
    for tr in children(table, "tr"):
        rows.append([c for c in tr.children if isinstance(c, Node) and c.tag in {"td", "th"}])
    return rows


def exam_year(text: str, fallback: int) -> int | None:
    match = re.search(r"20(?:2[0-6])", text)
    return int(match.group()) if match else fallback


def parse_catalog(kind: str, url: str) -> list[dict]:
    parser = TableParser()
    parser.feed(page(url))
    entries = []
    for table in parser.tables:
        rows = table_rows(table)
        if not rows:
            continue
        header_index = next((i for i, row in enumerate(rows) if sum(bool(exam_year(c.text(), 0)) for c in row) >= 2), None)
        if header_index is None:
            continue
        header = [c.text() for c in rows[header_index]]
        year_columns = {i: exam_year(text, 0) for i, text in enumerate(header) if exam_year(text, 0)}
        region = "北京市" if kind == "真题" else ""
        for original_row in rows[header_index + 1:]:
            row = original_row
            cells = [c.text() for c in row]
            if not cells:
                continue
            is_subject = lambda text: text.split("（")[0] in SUBJECTS
            if is_subject(cells[0]) and len(row) == len(header) - 1:
                row = [Node("td", {})] + row
                cells = [c.text() for c in row]
            if kind != "真题" and cells[0] and not is_subject(cells[0]) and cells[0] not in {"区域", "科目", "地区", "考试科目"}:
                region = cells[0].removesuffix("区")
            subject_index = next((i for i, text in enumerate(cells) if is_subject(text)), None)
            if subject_index is None:
                continue
            subject = cells[subject_index].split("（")[0]
            for column, year in year_columns.items():
                if column >= len(row) or not (2020 <= year <= 2026):
                    continue
                for raw_link in row[column].links():
                    link = urljoin(url, raw_link)
                    if urlparse(link).netloc.removeprefix("www.") != urlparse(url).netloc.removeprefix("www."):
                        continue
                    entries.append({
                        "year": year,
                        "kind": kind,
                        "region_or_school": region,
                        "subject": subject,
                        "title": f"{year}北京{region}高三{kind}{subject}试题",
                        "question_count": "",
                        "count_status": "待核",
                        "count_basis": "",
                        "primary_url": link,
                        "source_urls": [link],
                        "catalog_urls": [url],
                        "access": "公开目录；附件以原页面为准",
                    })
    return entries


def parse_zero_catalog(url: str, year: int, kind: str = "零模") -> list[dict]:
    parser = TableParser()
    parser.feed(page(url))
    entries = []
    region, school = "", ""
    for table in parser.tables:
        for row in table_rows(table):
            cells = [c.text() for c in row]
            canonical = lambda text: "政治" if text.startswith("思想政治") else text.split("（")[0].rstrip("12")
            subject_index = next((i for i, text in enumerate(cells) if canonical(text) in SUBJECTS), None)
            if subject_index is None:
                continue
            raw_subject = cells[subject_index]
            subject = canonical(raw_subject)
            base = "思想政治" if raw_subject.startswith("思想政治") else subject
            variant = raw_subject.removeprefix(base).strip("（）")
            if subject_index >= 2:
                region, school = cells[0].removesuffix("区"), cells[1]
            elif subject_index == 1:
                school = cells[0]
            place = school or region or ("北京市" if kind == "适应性测试" else "")
            for cell in row[subject_index + 1:]:
                if not any(word in cell.text() for word in ["试题", "试卷", "答案", "查看"]):
                    continue
                for raw_link in cell.links():
                    link = urljoin(url, raw_link)
                    if urlparse(link).netloc.removeprefix("www.") != urlparse(url).netloc.removeprefix("www."):
                        continue
                    entries.append({
                        "year": year, "kind": kind, "region_or_school": place, "variant": variant,
                        "subject": subject, "title": f"{year}北京{place}高三{kind}{cells[subject_index]}试题",
                        "question_count": "", "count_status": "待核", "count_basis": "",
                        "primary_url": link, "source_urls": [link], "catalog_urls": [url],
                        "access": "公开目录；附件以原页面为准",
                    })
    return entries


def parse_kewai_catalog(subject: str, url: str) -> list[dict]:
    parser = TableParser()
    parser.feed(page(url))
    entries = []
    for table in parser.tables:
        for row in table_rows(table):
            if not row:
                continue
            title = row[0].text()
            if "2026年" not in title or "名校高三考前热身" in title:
                continue
            place = title.split("2026年", 1)[0].removesuffix("区")
            place = {
                "北师大附属实验中学": "北师大实验中学", "精华学校": "北京精华学校",
            }.get(place, place)
            if not place:
                continue
            entries.append({
                "year": 2026, "kind": "三模", "region_or_school": place, "variant": "",
                "subject": subject, "title": title, "question_count": "", "section_count": "",
                "count_status": "待核", "count_basis": "目录仅提供登录后下载，未读取原卷",
                "verification_note": "", "primary_url": url, "source_urls": [url], "catalog_urls": [url],
                "access": "目录公开；下载需免费注册", "preserve_title": True,
            })
    return entries


def pdf_links(url: str) -> list[str]:
    source = page(url)
    links = re.findall(r"(?:href|src)=[\"']([^\"']+)[\"']", source, re.I)
    sources = [source]
    if urlparse(url).netloc.removeprefix("www.") == "gaokzx.com" and url.endswith(".html"):
        try:
            sources.append(page(url + "/_payload.json"))
        except Exception:
            pass
    for text in sources:
        links += re.findall(r"https?://[^\"'\\<>]+?\.pdf", text, re.I)
    result = []
    for link in links:
        link = html.unescape(urljoin(url, link))
        if ".pdf" in urlparse(link).path.lower() and link not in result:
            result.append(link)
    return result


def choose_pdf(links: list[str], subject: str, year: int | None = None) -> str | None:
    def score(link: str):
        name = unquote(urlparse(link).path)
        value = 0
        if subject in name:
            value += 4
        if "试题" in name or "试卷" in name:
            value += 3
        if "答案" in name and "试题" not in name and "试卷" not in name:
            value -= 8
        if "Fup/document" in link:
            value += 1
        if any(word in name for word in ["初三", "中考", "高一", "高二"]):
            value -= 20
        if year:
            years = [int(x) for x in re.findall(r"20\d{2}", name)]
            if year in years:
                value += 4
            elif any(2020 <= x <= 2026 and x != year for x in years):
                value -= 12
        return value
    if not links:
        return None
    selected = max(links, key=score)
    return selected if score(selected) > -15 else None


def normalize_text(text: str) -> str:
    return unicodedata.normalize("NFKC", text).replace("．", ".").replace("。", ".")


def count_questions(pdf_url: str, subject: str) -> tuple[int | None, str]:
    data = get(pdf_url, binary=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", tmp_path, "-"],
            capture_output=True,
            timeout=45,
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    if result.returncode:
        return None, "PDF文本提取失败"
    text = normalize_text(result.stdout.decode("utf-8", errors="replace"))
    if len(text.strip()) < 100:
        return None, "扫描件或无文本层"
    ranges = {
        "语文": (15, 30), "数学": (15, 25), "英语": (35, 60),
        "物理": (14, 24), "化学": (14, 24), "生物": (15, 25),
        "政治": (15, 25), "历史": (15, 25), "地理": (15, 25),
    }
    low, high = ranges[subject]
    pattern = re.compile(r"(?im)^\s*(?:\(([1-9]\d?)\)|\(?([1-9]\d?)\)?\s*[.、])")
    matches = [(int(m.group(1) or m.group(2)), m.start()) for m in pattern.finditer(text)]
    method = "行首题号"
    if not any(low <= n <= high for n, _ in matches):
        pattern = re.compile(r"(?:\(([1-9]\d?)\)|\b([1-9]\d?)\s*[.、])")
        matches = [(int(m.group(1) or m.group(2)), m.start()) for m in pattern.finditer(text)]
        method = "全文宽松题号"
    candidates = [n for n, pos in matches if low <= n <= high and any(x == 1 and p < pos for x, p in matches)]
    if not candidates:
        return None, "未识别到可靠连续题号"
    count = max(candidates)
    present = {n for n, _ in matches}
    missing = [n for n in range(1, count + 1) if n not in present]
    if 1 not in present or count not in present or len(missing) > max(1, count // 6):
        return None, f"题号不连续：缺{missing}"
    note = f"；文本层缺号{missing}，需抽检" if missing else ""
    return count, f"原卷PDF文本层最大连续编号题数（自动识别，{method}）" + note


def prepare_entry(entry: dict) -> dict:
    if not entry.get("preserve_title"):
        title = page_title(entry["primary_url"])
        if title:
            entry["title"] = title
    title = entry["title"].replace("思想政治", "政治")
    title_subjects = {subject for subject in SUBJECTS if subject in title}
    if len(title_subjects) == 1:
        entry["subject"] = title_subjects.pop()
    districts = [district for district in DISTRICTS if district in title]
    if len(districts) == 1 and entry["region_or_school"] in DISTRICTS + ["", "未分区", "物理反馈", "查漏", "拓展"]:
        entry["region_or_school"] = districts[0]
    aliases = {
        "一零一中": "一零一中学", "一六一中学": "一六一中", "二中": "北京二中",
        "四中": "北京四中", "五中": "北京五中", "八中": "北京八中", "九中": "北京九中",
        "首师附中": "首师大附中", "北师大附属实验中学": "北师大实验中学",
        "北京八十中": "八十中", "精华学校": "北京精华学校",
    }
    entry["region_or_school"] = aliases.get(entry["region_or_school"], entry["region_or_school"])
    if entry["kind"] == "三模" and not entry.get("variant"):
        variants = [
            (["自主复习检测", "自主检测"], "自主检测"),
            (["阶段性质量检测", "质量调研"], "质量调研"),
            (["查漏补缺"], "查漏补缺"), (["考前提醒"], "考前提醒"),
            (["考前热身", "热身练"], "考前热身"), (["考前自测"], "考前自测"),
            (["保温"], "保温"), (["暖身"], "暖身"),
            (["适应训练", "适应性"], "适应性测试"), (["校模"], "校模"),
            (["仿真"], "仿真模拟"), (["三模", "模拟"], "三模"),
        ]
        entry["variant"] = next((name for words, name in variants if any(x in title for x in words)), "")
    return entry


def enrich(entry: dict, metadata_only: bool) -> dict:
    try:
        title = page_title(entry["primary_url"])
        if title and not entry.get("preserve_title"):
            entry["title"] = title
        if metadata_only:
            return entry
        links = []
        for detail_url in list(entry["source_urls"]):
            try:
                links.extend(x for x in pdf_links(detail_url) if x not in links)
            except Exception:
                continue
        entry["source_urls"].extend(x for x in links if x not in entry["source_urls"])
        selected = choose_pdf(links, entry["subject"], entry["year"])
        if selected:
            count, basis = count_questions(selected, entry["subject"])
            entry["question_count"] = count or ""
            entry["count_basis"] = selected + "；" + basis
            entry["count_status"] = "自动识别待抽检" if count else "待核"
        else:
            entry["count_basis"] = "详情页未发现直链PDF"
    except Exception as exc:
        entry["count_status"] = "读取失败"
        entry["count_basis"] = str(exc)
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-only", action="store_true")
    args = parser.parse_args()
    entries = []
    for kind, url in CATALOGS:
        year = exam_year(page_title(url), 2024)
        found = parse_zero_catalog(url, year) if kind == "零模" else parse_catalog(kind, url)
        print(kind, url, len(found), flush=True)
        for entry in found:
            if not entry["region_or_school"]:
                title = page_title(entry["primary_url"])
                entry["title"] = title or entry["title"]
                entry["region_or_school"] = next((x for x in DISTRICTS if x in title), "未分区")
        entries.extend(found)
    for subject, url in KEWAI_CATALOGS:
        found = parse_kewai_catalog(subject, url)
        print("三模", url, len(found), flush=True)
        entries.extend(found)
    for year, kind, region, subject, variant, title, url in EXTRA_MOCK_PAPERS:
        if "gaokaozhitongche" in url:
            access = "目录公开；完整内容需App"
        elif any(host in url for host in ["wulixuexi.com", "zxxk.com", "51jiaoxi.com", "book118.com", "21cnjy.com"]):
            access = "卷目或部分正文公开；下载以原页面为准"
        else:
            access = "公开页面；附件以原页面为准"
        catalog_url = "https://www.gaokzx.com/gk/shitiku/155108.html" if variant == "查漏补缺" and region == "海淀" else url
        entries.append({
            "year": year, "kind": kind, "region_or_school": region, "subject": subject,
            "variant": variant, "title": title, "question_count": "", "count_status": "待核",
            "count_basis": "", "primary_url": url, "source_urls": [url],
            "catalog_urls": [catalog_url], "access": access, "preserve_title": True,
        })
    entries = [entry for entry in entries if entry["year"] in YEARS and entry["kind"] in MOCK_KINDS]
    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(prepare_entry, entries))
    unique = {}
    detail_urls = {}
    catalog_pages = {url for _, url in CATALOGS} | {url for _, url in KEWAI_CATALOGS}
    for entry in entries:
        entry.setdefault("variant", "")
        key = (entry["year"], entry["kind"], entry["region_or_school"], entry["subject"], entry["variant"])
        url_key = (entry["year"], entry["kind"], entry["subject"], entry["primary_url"])
        target = unique.get(key)
        if target is None and entry["primary_url"] not in catalog_pages:
            target = detail_urls.get(url_key)
        if target is None:
            unique[key] = entry
            if entry["primary_url"] not in catalog_pages:
                detail_urls[url_key] = entry
            continue
        for field in ["source_urls", "catalog_urls"]:
            target[field].extend(x for x in entry[field] if x not in target[field])
    entries = list(unique.values())
    with ThreadPoolExecutor(max_workers=8) as pool:
        for i, entry in enumerate(pool.map(lambda x: enrich(x, args.metadata_only), entries), 1):
            entries[i - 1] = entry
            if i % 50 == 0:
                print(f"details {i}/{len(entries)}", flush=True)
    for entry in entries:
        entry.setdefault("section_count", "")
        entry.setdefault("verification_note", "")
    entries.sort(key=lambda x: (x["year"], MOCK_KINDS.index(x["kind"]), x["region_or_school"], x["subject"], x["variant"]))
    output = ROOT / "data"
    with (output / "papers.csv").open("w", encoding="utf-8-sig", newline="") as f:
        fields = ["year", "kind", "region_or_school", "subject", "variant", "title", "question_count", "section_count", "count_status", "count_basis", "verification_note", "primary_url", "source_urls", "catalog_urls", "access"]
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow({**entry, "source_urls": json.dumps(entry["source_urls"], ensure_ascii=False), "catalog_urls": json.dumps(entry["catalog_urls"], ensure_ascii=False)})
    (output / "papers.json").write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} entries")


if __name__ == "__main__":
    main()
