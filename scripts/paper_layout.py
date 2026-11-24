"""Shared naming rules for the raw exam-paper library."""
from __future__ import annotations

from pathlib import Path

SUBJECT_ENGLISH = {
    "语文": "chinese",
    "数学": "math",
    "英语": "english",
    "物理": "physics",
    "化学": "chemistry",
    "生物": "biology",
    "政治": "politics",
    "历史": "history",
    "地理": "geography",
}

PAPER_TYPE_ENGLISH = {
    "零模": "mock0",
    "一模": "mock1",
    "二模": "mock2",
    "三模": "mock3",
    "真题": "gaokao",
    "高考": "gaokao",
}

KIND_DIRECTORY = {
    "零模": "零模",
    "一模": "一模",
    "二模": "二模",
    "三模": "三模",
    "真题": "真题",
    "高考": "真题",
}

DISTRICT_PINYIN = {
    "东城": "dongcheng",
    "西城": "xicheng",
    "朝阳": "chaoyang",
    "海淀": "haidian",
    "丰台": "fengtai",
    "石景山": "shijingshan",
    "通州": "tongzhou",
    "顺义": "shunyi",
    "昌平": "changping",
    "大兴": "daxing",
    "房山": "fangshan",
    "门头沟": "mentougou",
    "怀柔": "huairou",
    "平谷": "pinggu",
    "密云": "miyun",
    "延庆": "yanqing",
    "燕山": "yanshan",
}

# Used only when a colliding filename needs a human-readable school suffix.
ENTITY_PINYIN = {
    "一六一中": "yiliuyizhong",
    "一零一中学": "yilingyizhongxue",
    "三十五中": "sanshiwuzhong",
    "东直门中学": "dongzhimenzhongxue",
    "中关村中学": "zhongguancunzhongxue",
    "人大附中": "rendafuzhong",
    "八一学校": "bayixuexiao",
    "北京一六六中": "beijingyiliuliuzhong",
    "北京二中": "beijingerzhong",
    "北京五中": "beijingwuzhong",
    "北京五十五中": "beijingwushiwuzhong",
    "北京八中": "beijingbazhong",
    "北京九中": "beijingjiuzhong",
    "北京中学": "beijingzhongxue",
    "北京人朝分校": "beijingrenchaofenxiao",
    "北京四中顺义分校": "beijingsizhongshunyifenxiao",
    "八十中": "bashizhong",
    "北师大燕化附中": "beishidayanhuafuzhong",
    "清华附中朝阳学校": "qinghuafuzhongchaoyangxuexiao",
    "潞河中学": "luhezhongxue",
    "陈经纶中学": "chenjinlunzhongxue",
    "丰台二中": "fengtaierzhong",
    "怀柔一中": "huairouyizhong",
    "北京十四中": "beijingshisizhong",
    "北京四中": "beijingsizhong",
    "北京精华学校": "beijingjinghuaxuexiao",
    "北大附中": "beidafuzhong",
    "北师大二附中": "beishidaerfuzhong",
    "北师大实验中学": "beishidashiyanzhongxue",
    "北师大附中": "beishidafuzhong",
    "十一学校": "shiyixuexiao",
    "十一学校顺义学校": "shiyixuexiaoshunyixuexiao",
    "十三中": "shisanzhong",
    "大兴一中": "daxingyizhong",
    "汇文中学": "huiwenzhongxue",
    "海淀进修实验学校": "haidianjinxiushiyanxuexiao",
    "清华附中": "qinghuafuzhong",
    "牛栏山一中": "niulanshanyizhong",
    "理工大附中": "ligongdafuzhong",
    "育才学校": "yucaixuexiao",
    "西城外国语": "xichengwaiguoyu",
    "顺义一中": "shunyiyizhong",
    "首师大附中": "shoushidafuzhong",
}

VARIANT_PINYIN = {
    "三模": "sanmo",
    "保温": "baowen",
    "暖身": "nuanshen",
    "查漏补缺": "chaloubuque",
    "校级模拟": "xiaojimoni",
    "考前提醒": "kaoqiantixing",
    "考前热身": "kaoqianreshen",
    "仿真模拟": "fangzhenmoni",
    "校模": "xiaomo",
    "考前自测": "kaoqianzice",
    "自主检测": "zizhujiance",
    "质量调研": "zhiliangdiaoyan",
    "适应性测试": "shiyingxingceshi",
}


def subject_token(subject: str) -> str:
    try:
        return SUBJECT_ENGLISH[subject]
    except KeyError as exc:
        raise ValueError(f"No English filename token for subject: {subject}") from exc


def kind_token(kind: str) -> str:
    try:
        return PAPER_TYPE_ENGLISH[kind]
    except KeyError as exc:
        raise ValueError(f"No English filename token for paper type: {kind}") from exc


def district_token(district: str) -> str:
    try:
        return DISTRICT_PINYIN[district]
    except KeyError as exc:
        raise ValueError(f"No district pinyin mapping for: {district}") from exc


def _entity_token(name: str) -> str:
    if name in DISTRICT_PINYIN:
        return DISTRICT_PINYIN[name]
    try:
        return ENTITY_PINYIN[name]
    except KeyError as exc:
        raise ValueError(f"Add a pinyin mapping before naming this school: {name}") from exc


def disambiguator_tokens(entry: dict) -> list[str]:
    district = entry["district"]
    school = entry.get("region_or_school") or entry.get("school") or district
    tokens = []
    if school != district:
        tokens.append("school-" + _entity_token(school))
    variant = (entry.get("variant") or "").strip()
    kind = entry.get("kind", "")
    if variant and variant != kind:
        try:
            variant_token = VARIANT_PINYIN[variant]
        except KeyError as exc:
            raise ValueError(f"Add a pinyin mapping before naming this variant: {variant}") from exc
        tokens.append("variant-" + variant_token.replace(" ", ""))
    return tokens


def paper_relative_path(
    entry: dict,
    extension: str,
    *,
    disambiguate: bool = False,
    copy_index: int | None = None,
) -> Path:
    subject = entry["subject"]
    year = str(entry["year"])
    kind = entry["kind"]
    directory = KIND_DIRECTORY.get(kind)
    if directory is None:
        raise ValueError(f"No directory mapping for paper type: {kind}")
    district = entry["district"]
    stem = "-".join((subject_token(subject), year, kind_token(kind), district_token(district)))
    if disambiguate:
        suffixes = disambiguator_tokens(entry)
        if suffixes:
            stem += "-" + "-".join(suffixes)
        elif copy_index is None:
            stem += "-copy"
    if copy_index is not None:
        stem += f"-copy-{copy_index:02d}"
    extension = extension.lower().lstrip(".")
    return Path("raw") / subject / year / directory / f"{stem}.{extension}"


def source_page_relative_path(entry: dict, page_index: int) -> Path:
    subject = entry["subject"]
    year = str(entry["year"])
    kind = entry["kind"]
    district = district_token(entry["district"])
    sequence = str(entry.get("sequence", "0000")).zfill(4)
    stem = "-".join((subject_token(subject), year, kind_token(kind), district, sequence, f"source-{page_index:02d}"))
    return (
        Path("data")
        / "original_downloads"
        / "source_pages"
        / year
        / KIND_DIRECTORY[kind]
        / district
        / f"{stem}.html"
    )
