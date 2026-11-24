"""Extract article text and resource URLs from downloaded exam source pages."""

import json
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URLS = {
    "2022": "https://www.e-chinaedu.cn/html/kaoshi/2023/gaokao_0512/102743.shtml",
    "2023": "https://gaokao.eol.cn/shiti/yw/202309/t20230907_2459971.shtml",
    "2024": "https://gaokao.eol.cn/shiti/yw/202407/t20240705_2621911.shtml",
    "2025": "https://www.zizzs.com/gk/shitiku/204984_11.html",
    "2026": "https://sjds.net/797085.html",
    "2022_eol": "https://gaokao.eol.cn/shiti/yw/202303/t20230314_2324023.shtml",
    "2025_sohu": "https://www.sohu.com/a/907809759_120886047",
    "2026_sohu": "https://m.sohu.com/a/1062842127_120886047",
    "2026_sohu_analysis": "https://m.sohu.com/a/1070279492_125993/",
    "2026_jhgk": "https://www.jhgk.cn/trendDetails.htm?id=d7c00c3c-6b56-4ea5-86f6-1b627dfa708b",
}
SELECTORS = [".art-content", ".post_content", ".article-content", ".content", ".article", "#content", ".post_main"]


def main():
    root = Path(sys.argv[1])
    for year_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        source = year_dir / "source.html"
        if not source.is_file():
            continue
        raw = source.read_bytes()
        soup = BeautifulSoup(raw, "html.parser")
        article = next((soup.select_one(selector) for selector in SELECTORS if soup.select_one(selector)), None)
        if article is None:
            article = max(soup.find_all(["article", "section", "div"]), key=lambda node: len(node.get_text()), default=soup)
        text = article.get_text("\n", strip=True)
        (year_dir / "article.txt").write_text(text, encoding="utf-8")
        resources = []
        for node in article.find_all(["a", "img", "iframe", "embed"]):
            attribute = "href" if node.name == "a" else "src"
            value = node.get(attribute)
            if not value:
                continue
            resources.append({
                "tag": node.name,
                "url": urljoin(BASE_URLS.get(year_dir.name, ""), value),
                "text": node.get_text(" ", strip=True) if node.name == "a" else node.get("alt", ""),
                "class": node.get("class", []),
            })
        for node in soup.find_all("a", href=True):
            href = node["href"]
            if any(token in href.lower() for token in (".pdf", ".doc", "download", "down", "attachment")):
                resources.append({"tag": "a", "url": urljoin(BASE_URLS.get(year_dir.name, ""), href), "text": node.get_text(" ", strip=True), "class": node.get("class", [])})
        unique = list({item["url"] + "\0" + item["tag"]: item for item in resources}.values())
        (year_dir / "resources.json").write_text(json.dumps(unique, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(year_dir.name, "text_chars", len(text), "resources", len(unique), "article_class", article.get("class"))


if __name__ == "__main__":
    main()
