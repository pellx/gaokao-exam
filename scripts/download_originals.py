from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
_LAYOUT_SPEC = importlib.util.spec_from_file_location("paper_layout", Path(__file__).with_name("paper_layout.py"))
if _LAYOUT_SPEC is None or _LAYOUT_SPEC.loader is None:
    raise RuntimeError("Cannot load scripts/paper_layout.py")
paper_layout = importlib.util.module_from_spec(_LAYOUT_SPEC)
_LAYOUT_SPEC.loader.exec_module(paper_layout)
CACHE = ROOT / "data" / "cache"
RAW = ROOT / "raw"
RECORDS = ROOT / "data" / "original_downloads"
YEARS = (2026, 2025, 2024)
KINDS = ("零模", "一模", "二模", "三模")
DISTRICTS = ("东城", "西城", "朝阳", "海淀", "丰台", "石景山", "通州", "顺义", "昌平", "大兴", "房山", "门头沟", "怀柔", "平谷", "密云", "延庆", "燕山")
SUBJECTS = ("语文", "数学", "英语", "物理", "化学", "生物", "政治", "历史", "地理")
DOCUMENT_EXTENSIONS = ("pdf", "doc", "docx", "txt", "zip", "rar", "7z")
SCHOOL_DISTRICTS = {
    "北京二中": "东城", "北京五中": "东城", "东直门中学": "东城", "广渠门中学": "东城",
    "汇文中学": "东城", "一六六中学": "东城", "北京一六六中": "东城", "一七一中学": "东城",
    "景山学校": "东城", "北京五十五中": "东城",
    "北京四中": "西城", "北京八中": "西城", "北师大实验中学": "西城", "北师大附中": "西城",
    "北师大二附中": "西城", "一六一中": "西城", "北京十三中": "西城", "十三中": "西城",
    "北京十五中": "西城", "北京十四中": "西城", "三十五中": "西城", "铁路二中": "西城", "育才学校": "西城",
    "人大附中": "海淀", "清华附中": "海淀", "北大附中": "海淀", "一零一中学": "海淀",
    "首师大附中": "海淀", "十一学校": "海淀", "八一学校": "海淀", "中关村中学": "海淀",
    "交大附中": "海淀", "理工附中": "海淀", "理工大附中": "海淀", "育英学校": "海淀", "五十七中": "海淀",
    "八十中": "朝阳", "陈经纶中学": "朝阳", "日坛中学": "朝阳", "工大附中": "朝阳",
    "和平街一中": "朝阳", "三里屯一中": "朝阳", "清华附中朝阳学校": "朝阳", "北京中学": "朝阳",
    "北京人朝分校": "朝阳",
    "北京十二中": "丰台", "丰台二中": "丰台", "北京十八中": "丰台",
    "北京九中": "石景山", "京源学校": "石景山",
    "潞河中学": "通州", "景山学校通州分校": "通州",
    "顺义一中": "顺义", "牛栏山一中": "顺义", "北京四中顺义分校": "顺义", "十一学校顺义学校": "顺义",
    "昌平一中": "昌平", "昌平二中": "昌平",
    "大兴一中": "大兴", "北京精华学校": "大兴",
    "良乡中学": "房山", "怀柔一中": "怀柔", "平谷一中": "平谷",
    "密云一中": "密云", "密云二中": "密云", "延庆一中": "延庆", "北师大燕化附中": "燕山",
}
ENTRY_FIELDS = [
    "sequence", "entry_key", "year", "kind", "district", "region_or_school", "subject", "variant",
    "title", "status", "original_files", "source_pages", "failed_files", "error", "primary_url",
]
FILE_FIELDS = [
    "sequence", "entry_key", "year", "kind", "district", "region_or_school", "subject", "variant",
    "title", "file_type", "url", "local_path", "status", "bytes", "sha256", "error",
]


def safe_name(value: str, limit: int = 100) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "未命名")[:limit].rstrip(" .")


def district_of(entry: dict) -> str:
    region = entry["region_or_school"]
    if region in DISTRICTS:
        return region
    for district in DISTRICTS:
        if district in region or district in entry["title"]:
            return district
    return SCHOOL_DISTRICTS.get(region, "校级及其他")


def entry_key(entry: dict) -> str:
    return "|".join(str(entry[key]) for key in ("year", "kind", "region_or_school", "subject")) + "|" + entry.get("variant", "")


def sort_key(entry: dict) -> tuple:
    district = district_of(entry)
    district_index = DISTRICTS.index(district) if district in DISTRICTS else len(DISTRICTS)
    subject_index = SUBJECTS.index(entry["subject"]) if entry["subject"] in SUBJECTS else len(SUBJECTS)
    return (-entry["year"], KINDS.index(entry["kind"]), district_index, entry["region_or_school"], subject_index, entry.get("variant", ""), entry["title"])


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def cache_path(url: str, extension: str) -> Path:
    return CACHE / (hashlib.sha256(url.encode()).hexdigest()[:24] + "." + extension)


def valid_document(path: Path, extension: str) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        start = handle.read(8)
    if extension == "pdf":
        return start.startswith(b"%PDF-")
    if extension in {"docx", "zip"}:
        return start.startswith(b"PK")
    if extension == "rar":
        return start.startswith(b"Rar!")
    if extension == "7z":
        return start.startswith(b"7z\xbc\xaf")
    return True


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def session() -> requests.Session:
    result = requests.Session()
    result.trust_env = False
    result.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36"})
    return result


def request_page(url: str, client: requests.Session) -> bytes:
    cached = cache_path(url, "html")
    if cached.exists() and cached.stat().st_size:
        return cached.read_bytes()
    error = None
    for attempt in range(3):
        try:
            response = client.get(url, timeout=(15, 45))
            response.raise_for_status()
            if not response.content:
                raise ValueError("空响应")
            CACHE.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(response.content)
            time.sleep(0.12)
            return response.content
        except Exception as exc:
            error = exc
            if attempt < 2:
                time.sleep(1 + attempt)
    raise ValueError(str(error))


def document_links(entry: dict, client: requests.Session) -> tuple[list[str], list[tuple[str, bytes]], list[str]]:
    links = []
    pages = []
    errors = []
    pattern = re.compile(r"https?://[^\"'\\<>\s]+?\.(?:pdf|docx?|txt|zip|rar|7z)(?:\?[^\"'\\<>\s]*)?", re.I)
    attribute = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.I)
    for source in entry.get("source_urls", []):
        extension = Path(unquote(urlparse(source).path)).suffix.lower().lstrip(".")
        if extension in DOCUMENT_EXTENSIONS:
            if source not in links:
                links.append(source)
            continue
        sources = [source]
        if urlparse(source).netloc.removeprefix("www.") == "gaokzx.com" and source.endswith(".html"):
            sources.append(source + "/_payload.json")
        for page_url in sources:
            try:
                body = request_page(page_url, client)
            except Exception as exc:
                errors.append(f"{page_url}: {exc}")
                continue
            if page_url == source:
                pages.append((source, body))
            text = body.decode("utf-8", errors="replace")
            found = pattern.findall(html.unescape(text))
            found += [urljoin(source, value) for value in attribute.findall(text)]
            for link in found:
                link = html.unescape(link).rstrip("),;")
                extension = Path(unquote(urlparse(link).path)).suffix.lower().lstrip(".")
                if extension in DOCUMENT_EXTENSIONS and link not in links:
                    links.append(link)
    return links, pages, errors


def download(url: str, target: Path, client: requests.Session) -> tuple[str, int, str, str]:
    extension = target.suffix.lower().lstrip(".")
    cached = cache_path(url, extension)
    if valid_document(cached, extension):
        target.parent.mkdir(parents=True, exist_ok=True)
        if not valid_document(target, extension):
            shutil.copyfile(cached, target)
        return "已保存（复用缓存）", target.stat().st_size, sha256(target), ""
    if valid_document(target, extension):
        return "已保存（本地已存在）", target.stat().st_size, sha256(target), ""
    error = None
    for attempt in range(3):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(target.suffix + ".part")
            with client.get(url, stream=True, timeout=(15, 90)) as response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for block in response.iter_content(1024 * 256):
                        if block:
                            handle.write(block)
            if not valid_document(temporary, extension):
                temporary.unlink(missing_ok=True)
                raise ValueError("响应不是有效的原始文档")
            temporary.replace(target)
            CACHE.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(target, cached)
            time.sleep(0.15)
            return "已下载", target.stat().st_size, sha256(target), ""
        except Exception as exc:
            error = exc
            target.with_suffix(target.suffix + ".part").unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 + attempt)
    return "下载失败", 0, "", str(error)


def save_records(entry_rows: dict, file_rows: dict, current: str) -> None:
    ordered_entries = sorted(entry_rows.values(), key=lambda row: int(row["sequence"]))
    ordered_files = sorted(file_rows.values(), key=lambda row: (int(row["sequence"]), row["file_type"], row["url"]))
    write_csv(RECORDS / "entries.csv", ENTRY_FIELDS, ordered_entries)
    write_csv(RECORDS / "files.csv", FILE_FIELDS, ordered_files)
    summary = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_or_last_entry": current,
        "entry_status": dict(Counter(row["status"] for row in entry_rows.values())),
        "file_status": dict(Counter(row["status"] for row in file_rows.values())),
        "saved_original_files": sum(row["file_type"] == "原始附件" and row["status"] != "下载失败" for row in file_rows.values()),
        "saved_original_bytes": sum(int(row["bytes"]) for row in file_rows.values() if row["file_type"] == "原始附件" and row["status"] != "下载失败"),
        "raw_directory": "raw",
    }
    temporary = RECORDS / "summary.json.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(RECORDS / "summary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="按年份、模考批次、区县顺序下载原始试卷附件")
    parser.add_argument("--year", type=int, choices=YEARS)
    parser.add_argument("--kind", choices=KINDS)
    parser.add_argument("--region")
    parser.add_argument("--max-entries", type=int)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    papers = json.loads((ROOT / "data" / "papers.json").read_text(encoding="utf-8"))
    papers = [entry for entry in papers if entry["year"] in YEARS and entry["kind"] in KINDS]
    supplemental = {}
    for row in read_csv(RECORDS / "supplemental_sources.csv"):
        supplemental.setdefault(row["entry_key"], []).append(row["url"])
    if args.year:
        papers = [entry for entry in papers if entry["year"] == args.year]
    if args.kind:
        papers = [entry for entry in papers if entry["kind"] == args.kind]
    if args.region:
        papers = [entry for entry in papers if entry["region_or_school"] == args.region or district_of(entry) == args.region]
    papers.sort(key=sort_key)
    if args.max_entries:
        papers = papers[: args.max_entries]
    all_papers = json.loads((ROOT / "data" / "papers.json").read_text(encoding="utf-8"))
    all_papers = sorted((entry for entry in all_papers if entry["year"] in YEARS and entry["kind"] in KINDS), key=sort_key)
    paper_base_counts = Counter(
        (entry["year"], entry["kind"], district_of(entry), entry["subject"])
        for entry in all_papers
    )
    paper_identity_counts = Counter()
    for candidate in all_papers:
        district = district_of(candidate)
        base_key = (candidate["year"], candidate["kind"], district, candidate["subject"])
        if paper_base_counts[base_key] > 1:
            layout_candidate = {**candidate, "district": district}
            identity = (*base_key, *paper_layout.disambiguator_tokens(layout_candidate))
            paper_identity_counts[identity] += 1
    sequences = {entry_key(entry): index for index, entry in enumerate(all_papers, 1)}
    entry_rows = {row["entry_key"]: row for row in read_csv(RECORDS / "entries.csv")}
    file_rows = {(row["entry_key"], row["file_type"], row["url"]): row for row in read_csv(RECORDS / "files.csv")}
    client = session()
    for index, entry in enumerate(papers, 1):
        key = entry_key(entry)
        sequence = sequences[key]
        previous = entry_rows.get(key)
        if previous:
            previous["sequence"] = sequence
            for row in file_rows.values():
                if row["entry_key"] == key:
                    row["sequence"] = sequence
        if previous and previous["status"] == "已获得原始附件" and previous["district"] == district_of(entry):
            related = [row for row in file_rows.values() if row["entry_key"] == key and row["file_type"] == "原始附件"]
            if related and all((ROOT / row["local_path"]).exists() for row in related):
                print(f"[{index}/{len(papers)}] 跳过已完成：{entry['title']}", flush=True)
                continue
        for row_key in [row_key for row_key in file_rows if row_key[0] == key]:
            del file_rows[row_key]
        district = district_of(entry)
        layout_entry = {**entry, "district": district, "sequence": sequence}
        base_key = (entry["year"], entry["kind"], district, entry["subject"])
        needs_disambiguation = paper_base_counts[base_key] > 1
        identity_tokens = paper_layout.disambiguator_tokens(layout_entry) if needs_disambiguation else []
        identity_key = (*base_key, *identity_tokens)
        needs_copy_suffix = paper_identity_counts[identity_key] > 1
        base = {
            "sequence": sequence, "entry_key": key, "year": entry["year"], "kind": entry["kind"],
            "district": district, "region_or_school": entry["region_or_school"], "subject": entry["subject"],
            "variant": entry.get("variant", ""), "title": entry["title"],
        }
        errors = []
        try:
            source_urls = entry.get("source_urls", []) + supplemental.get(key, [])
            links, pages, discovery_errors = document_links({**entry, "source_urls": source_urls}, client)
            errors.extend(discovery_errors)
        except Exception as exc:
            links, pages = [], []
            errors.append(str(exc))
        saved = 0
        failed = 0
        extension_totals = Counter(Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".") for url in links)
        extension_seen = Counter()
        for attachment_index, url in enumerate(links, 1):
            extension = Path(unquote(urlparse(url).path)).suffix.lower().lstrip(".")
            extension_seen[extension] += 1
            copy_index = None
            if extension_totals[extension] > 1 or needs_copy_suffix:
                copy_index = sequence * 1000 + extension_seen[extension] if needs_copy_suffix else extension_seen[extension]
            target = ROOT / paper_layout.paper_relative_path(
                layout_entry, extension, disambiguate=needs_disambiguation, copy_index=copy_index
            )
            status, size, digest, error = download(url, target, client)
            if status == "下载失败":
                failed += 1
            else:
                saved += 1
            if error:
                errors.append(f"{url}: {error}")
            row = {**base, "file_type": "原始附件", "url": url, "local_path": str(target.relative_to(ROOT)), "status": status, "bytes": size, "sha256": digest, "error": error}
            file_rows[(key, "原始附件", url)] = row
        saved_pages = 0
        if saved == 0:
            for page_index, (url, body) in enumerate(pages, 1):
                target = ROOT / paper_layout.source_page_relative_path(layout_entry, page_index)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body)
                row = {**base, "file_type": "来源网页", "url": url, "local_path": str(target.relative_to(ROOT)), "status": "仅保存网页", "bytes": len(body), "sha256": sha256(target), "error": ""}
                file_rows[(key, "来源网页", url)] = row
                saved_pages += 1
        if saved and not failed:
            status = "已获得原始附件"
        elif saved:
            status = "部分获得"
        elif saved_pages:
            status = "仅保存来源网页"
        else:
            status = "未获得"
        entry_rows[key] = {
            **base, "status": status, "original_files": saved, "source_pages": saved_pages,
            "failed_files": failed, "error": "\n".join(errors), "primary_url": entry["primary_url"],
        }
        save_records(entry_rows, file_rows, key)
        print(f"[{index}/{len(papers)}] {status}：{entry['year']} {entry['kind']} {district} {entry['region_or_school']} {entry['subject']}（附件 {saved}，失败 {failed}）", flush=True)
    save_records(entry_rows, file_rows, entry_rows.get(entry_key(papers[-1]), {}).get("entry_key", "") if papers else "")
    print(json.dumps(dict(Counter(entry_rows[key]["status"] for key in map(entry_key, papers))), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
