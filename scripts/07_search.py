"""命令行全文检索工具。

该脚本面向快速验证：它直接读取政策台账和处理后文本，按关键词、主题、省份、
权威等级等字段打分，输出 CSV 检索结果。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"

REGIONAL_PROVINCES = {
    "南方区域": ["广东", "广西", "云南", "贵州", "海南"],
    "华中区域": ["湖北", "湖南", "河南", "江西", "重庆", "四川", "西藏"],
    "华北区域": ["北京", "天津", "河北", "山西", "内蒙古"],
    "东北区域": ["辽宁", "吉林", "黑龙江", "内蒙古"],
    "西北区域": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
    "华东区域": ["上海", "江苏", "浙江", "安徽", "福建", "山东"],
}

KNOWN_QUERY_TERMS = [
    "全国",
    "四川",
    "山东",
    "浙江",
    "山西",
    "广东",
    "南方区域",
    "西北区域",
    "华中区域",
    "华东区域",
    "电力市场",
    "交易规则",
    "实施细则",
    "市场规则",
    "规则体系",
    "高质量发展",
    "电网",
    "新型电力系统",
    "源网荷储",
    "电力保供",
    "电网规划",
    "输配电价",
    "新能源消纳",
    "新能源上网电价",
    "辅助服务",
    "现货",
    "中长期",
    "绿电",
    "信息披露",
    "山东电力市场规则",
    "广东电力市场规则",
    "四川辅助服务",
    "南方区域新能源",
    "跨电网经营区",
]

RESULT_FIELDS = [
    "检索时间",
    "查询词",
    "排序",
    "得分",
    "资料编号",
    "文件标题",
    "发布部门",
    "采集来源机构",
    "发布日期",
    "文号",
    "适用地区",
    "来源类型",
    "市场主题",
    "权威等级",
    "有效状态",
    "原文链接",
    "命中说明",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize(value: str) -> str:
    value = (value or "").lower()
    return re.sub(r"[\s《》〈〉“”\"'()（）\[\]【】,，。.;；:：、/\\_-]+", "", value)


def split_terms(value: str) -> list[str]:
    parts = re.split(r"[;；,，、\s]+", value or "")
    terms: list[str] = []
    seen: set[str] = set()
    for part in [*parts, *(term for term in KNOWN_QUERY_TERMS if normalize(term) in normalize(value))]:
        term = part.strip()
        key = normalize(term)
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def load_processed_text(row: dict[str, str], include_text: bool) -> str:
    if not include_text:
        return ""
    for raw_path in row.get("本地文件路径", "").split(";"):
        raw_path = raw_path.strip()
        if not raw_path.lower().endswith(".txt"):
            continue
        path = ROOT / raw_path
        if not path.exists():
            continue
        try:
            return path.read_text(encoding="utf-8", errors="ignore")[:120000]
        except OSError:
            return ""
    return ""


def combined_text(row: dict[str, str], include_text: bool) -> dict[str, str]:
    meta_fields = {
        "标题": row.get("文件标题", ""),
        "部门": row.get("发布部门", ""),
        "来源机构": row.get("采集来源机构", ""),
        "文号": row.get("文号", ""),
        "地区": row.get("适用地区", ""),
        "来源": row.get("来源类型", ""),
        "主题": row.get("市场主题", ""),
        "关键词": row.get("关键词", ""),
        "摘要": row.get("摘要", ""),
        "备注": row.get("备注", ""),
        "正文": load_processed_text(row, include_text),
    }
    return meta_fields


def authority_boost(row: dict[str, str]) -> float:
    """给官方、高权威、现行有效资料提供基础分，避免搜索结果被低质命中挤占。"""
    score = 0.0
    authority = row.get("权威等级", "")
    source_type = row.get("来源类型", "")
    if "A" in authority:
        score += 5.0
    elif "B" in authority:
        score += 3.0
    elif "C" in authority:
        score += 1.0
    if "官方" in source_type:
        score += 2.0
    elif "监管" in source_type:
        score += 2.0
    elif "交易规则" in source_type:
        score += 1.5
    if "现行有效" in row.get("有效状态", ""):
        score += 2.0
    if "征求" in row.get("有效状态", ""):
        score -= 4.0
    elif "废止" in row.get("有效状态", ""):
        score -= 8.0
    return score


def core_title_phrases(title: str) -> list[str]:
    phrases = re.findall(r"《([^》]{4,80})》", title or "")
    if not phrases and title:
        cleaned = re.sub(r"^关于(印发|修订|发布|审定|贯彻落实)", "", title)
        cleaned = re.sub(r"的通知.*$|的复函.*$|意见的通知.*$", "", cleaned)
        phrases.append(cleaned)
    return phrases


def time_boost(row: dict[str, str]) -> float:
    """把发布日期转成轻量新鲜度加分；旧政策仍可因强命中排到前面。"""
    raw_date = row.get("发布日期", "")
    match = re.match(r"(20\d{2})-(\d{2})-(\d{2})", raw_date)
    if not match:
        return 0.0
    try:
        publish_date = dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return 0.0
    days = (dt.date.today() - publish_date).days
    if days <= 15:
        return 4.0
    if days <= 30:
        return 3.0
    if days <= 90:
        return 2.0
    if days <= 365:
        return 1.0
    return 0.0


def score_row(row: dict[str, str], query_terms: list[str], include_text: bool) -> tuple[float, list[str]]:
    """按字段权重计算单条政策得分，并返回命中字段用于人工解释排序。"""
    fields = combined_text(row, include_text)
    normalized_fields = {name: normalize(value) for name, value in fields.items()}
    query_text = normalize(" ".join(query_terms))
    score = authority_boost(row) + time_boost(row)
    hits: list[str] = []
    if "解读" not in query_text and ("解读" in row.get("文件标题", "") or "解读" in row.get("来源类型", "")):
        score -= 24.0

    weights = {
        "标题": 12.0,
        "关键词": 8.0,
        "主题": 7.0,
        "地区": 6.0,
        "部门": 4.0,
        "文号": 4.0,
        "备注": 4.0,
        "摘要": 3.0,
        "正文": 1.2,
        "来源": 1.0,
    }

    for phrase in core_title_phrases(row.get("文件标题", "")):
        phrase_key = normalize(phrase)
        if len(phrase_key) >= 6 and phrase_key in query_text:
            score += 22.0
            hits.append(f"{phrase}:标题")

    for term in query_terms:
        term_key = normalize(term)
        if not term_key:
            continue
        matched_fields = []
        for field_name, text in normalized_fields.items():
            if term_key in text:
                score += weights.get(field_name, 1.0)
                matched_fields.append(field_name)
        if matched_fields:
            hits.append(f"{term}:{'/'.join(matched_fields)}")

    if not hits:
        score = 0.0
    return score, hits


def field_contains(row: dict[str, str], field: str, expected: str) -> bool:
    if not expected:
        return True
    if field == "适用地区":
        return any(region_matches(row.get(field, ""), term) for term in split_terms(expected))
    field_text = normalize(row.get(field, ""))
    return any(normalize(term) in field_text for term in split_terms(expected))


def region_matches(doc_region: str, term: str) -> bool:
    doc_norm = normalize(doc_region)
    term_norm = normalize(term)
    if term_norm and doc_norm and (term_norm in doc_norm or doc_norm in term_norm):
        return True
    doc_regions = split_terms(doc_region)
    for region, provinces in REGIONAL_PROVINCES.items():
        if term in provinces and any(region == item for item in doc_regions):
            return True
        if term == region and any(province in doc_regions for province in provinces):
            return True
    return False


def search(
    query: str,
    region: str,
    topic: str,
    source_type: str,
    top_k: int,
    include_text: bool,
) -> list[dict[str, str]]:
    docs = read_csv(LEDGER_CSV)
    query_terms = split_terms(query)
    scored: list[tuple[float, list[str], dict[str, str]]] = []

    for row in docs:
        if not field_contains(row, "适用地区", region):
            continue
        if not field_contains(row, "市场主题", topic):
            continue
        if not field_contains(row, "来源类型", source_type):
            continue

        score, hits = score_row(row, query_terms, include_text)
        if score <= 0:
            continue
        scored.append((score, hits, row))

    scored.sort(key=lambda item: (item[0], item[2].get("发布日期", "")), reverse=True)
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    results: list[dict[str, str]] = []
    for rank, (score, hits, row) in enumerate(scored[:top_k], start=1):
        results.append(
            {
                "检索时间": now,
                "查询词": query,
                "排序": str(rank),
                "得分": f"{score:.2f}",
                "资料编号": row.get("资料编号", ""),
                "文件标题": row.get("文件标题", ""),
                "发布部门": row.get("发布部门", ""),
                "采集来源机构": row.get("采集来源机构", ""),
                "发布日期": row.get("发布日期", ""),
                "文号": row.get("文号", ""),
                "适用地区": row.get("适用地区", ""),
                "来源类型": row.get("来源类型", ""),
                "市场主题": row.get("市场主题", ""),
                "权威等级": row.get("权威等级", ""),
                "有效状态": row.get("有效状态", ""),
                "原文链接": row.get("原文链接", ""),
                "命中说明": ";".join(hits),
            }
        )
    return results


def self_check() -> list[str]:
    errors: list[str] = []
    if not LEDGER_CSV.exists():
        return [f"缺少台账：{LEDGER_CSV}"]
    rows = read_csv(LEDGER_CSV)
    if not rows:
        return ["政策资料台账为空"]
    required = ["资料编号", "文件标题", "适用地区", "来源类型", "市场主题", "本地文件路径", "原文链接"]
    missing = [field for field in required if field not in rows[0]]
    if missing:
        errors.append(f"政策资料台账缺少字段：{'、'.join(missing)}")

    probe = search("辅助服务 市场规则", region="全国", topic="", source_type="", top_k=3, include_text=False)
    if not probe:
        errors.append("内置检索自检失败：未找到辅助服务相关资料")
    elif not any("辅助服务" in row["文件标题"] or "辅助服务" in row["市场主题"] for row in probe):
        errors.append("内置检索自检失败：Top3未命中辅助服务相关资料")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the local power policy knowledge base.")
    parser.add_argument("query", nargs="*", help="Search query terms.")
    parser.add_argument("--region", default="", help="Filter by applicable region.")
    parser.add_argument("--topic", default="", help="Filter by market topic.")
    parser.add_argument("--source-type", default="", help="Filter by source type.")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results to return.")
    parser.add_argument("--no-text", action="store_true", help="Only search metadata fields.")
    parser.add_argument("--output", default="", help="Optional output csv path.")
    parser.add_argument("--self-check-only", action="store_true", help="Run self checks and exit.")
    args = parser.parse_args()

    errors = self_check()
    if errors:
        print("全文检索脚本自检失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    if args.self_check_only:
        print("全文检索脚本自检通过")
        return 0

    query = " ".join(args.query).strip()
    if not query:
        print("请提供查询词，例如：python .\\scripts\\07_search.py 山东 现货 结算")
        return 1

    rows = search(
        query=query,
        region=args.region,
        topic=args.topic,
        source_type=args.source_type,
        top_k=args.top_k,
        include_text=not args.no_text,
    )

    if not rows:
        print("未找到匹配结果")
        return 0

    for row in rows:
        print(f"{row['排序']}. [{row['得分']}] {row['文件标题']}")
        source_org = row.get("采集来源机构") or row.get("发布部门", "")
        doc_no = f" | {row['文号']}" if row.get("文号") else ""
        print(f"   {row['适用地区']} | {row['来源类型']} | {source_org} | {row['发布日期']}{doc_no} | {row['原文链接']}")

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output) if args.output else OUTPUT_DIR / f"全文检索结果_{timestamp}.csv"
    write_csv(output_path, rows, RESULT_FIELDS)
    print(f"结果已写入：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
