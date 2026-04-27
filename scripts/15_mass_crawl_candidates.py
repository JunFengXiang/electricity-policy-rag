from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
SOURCE_CSV = META_DIR / "来源清单.csv"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
SEED_CSV = META_DIR / "待采集链接.csv"
COLLECTOR_PATH = Path(__file__).with_name("02_collect_official_candidates.py")

OUTPUT_FIELDS = [
    "抓取批次",
    "候选编号",
    "来源编号",
    "来源层级",
    "地区",
    "来源栏目",
    "发布部门",
    "文件标题",
    "发布日期",
    "命中关键词",
    "文档类型",
    "质量分",
    "入库状态",
    "建议动作",
    "原文链接",
    "来源列表页",
]

BROAD_KEYWORDS = [
    "电力市场",
    "电力交易",
    "交易规则",
    "规则体系",
    "实施细则",
    "运营规则",
    "市场监管",
    "市场化交易",
    "年度交易",
    "月度交易",
    "中长期",
    "现货",
    "辅助服务",
    "调峰",
    "调频",
    "备用",
    "省内",
    "省间",
    "跨省",
    "跨区",
    "电能量",
    "容量电价",
    "机制电价",
    "上网电价",
    "计量结算",
    "结算",
    "绿电",
    "绿证",
    "源网荷储",
    "新型储能",
    "新能源上网",
    "注册",
]

NEGATIVE_TITLE_TERMS = [
    "节能报告",
    "审查意见",
    "许可",
    "许可证",
    "行政处罚",
    "名单",
    "招聘",
    "招标",
    "会议",
    "活动",
    "事故",
    "调查报告",
    "营商环境",
    "获得电力",
    "新能源汽车",
    "外债",
    "天然气",
    "专项资金",
    "供水",
    "科技奖励",
    "科技型企业",
    "城镇供水",
    "数据清单",
    "结算数据清单",
]

HIGH_VALUE_TERMS = [
    "规则",
    "细则",
    "办法",
    "通知",
    "实施方案",
    "市场",
    "交易",
    "现货",
    "中长期",
    "辅助服务",
    "省间",
    "跨省",
    "容量电价",
    "绿电",
    "结算",
]

POWER_RELEVANCE_TERMS = [
    "电力",
    "电价",
    "发电",
    "电网",
    "电能量",
    "新能源",
    "储能",
    "绿电",
    "绿证",
    "辅助服务",
    "调峰",
    "调频",
    "容量",
    "需求响应",
    "源网荷储",
    "售电",
    "购电",
    "用电",
]


def load_collector():
    spec = importlib.util.spec_from_file_location("official_candidate_collector", COLLECTOR_PATH)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载采集器：{COLLECTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def canonical_url(value: str) -> str:
    return (value or "").strip().rstrip("/")


def candidate_id(url: str) -> str:
    digest = hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:12].upper()
    return f"CAND-{digest}"


def document_type(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx"}:
        return suffix.removeprefix(".").upper()
    return "HTML"


def parse_date(value: str) -> dt.date | None:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def quality_score(row: dict[str, str]) -> int:
    title = row.get("文件标题", "")
    source_level = row.get("来源层级", "")
    source_name = row.get("来源栏目", "")
    score = 20

    if "国家级" in source_level:
        score += 28
    elif "区域监管" in source_level:
        score += 26
    elif "省级" in source_level or "直辖市" in source_level:
        score += 22
    elif "交易" in source_level or "交易中心" in source_name:
        score += 22
    elif "电网" in source_level or "电网" in source_name:
        score += 18
    else:
        score += 12

    score += min(25, sum(4 for term in HIGH_VALUE_TERMS if term in title))
    if "征求意见" in title or "公开征求" in title:
        score -= 8
    if any(term in title for term in NEGATIVE_TITLE_TERMS):
        score -= 35

    publish_date = parse_date(row.get("发布日期", ""))
    if publish_date:
        age_days = (dt.date.today() - publish_date).days
        if age_days <= 365:
            score += 8
        elif age_days <= 365 * 3:
            score += 4
        elif age_days > 365 * 8:
            score -= 4

    return max(0, min(score, 100))


def is_power_relevant(row: dict[str, str]) -> bool:
    title = row.get("文件标题", "")
    source_name = row.get("来源栏目", "")
    source_level = row.get("来源层级", "")
    text = f"{title} {source_name} {source_level}"
    if any(term in title for term in NEGATIVE_TITLE_TERMS):
        return False
    if any(term in text for term in POWER_RELEVANCE_TERMS):
        return True
    return "交易中心" in source_name or "能源" in source_name or "监管局" in source_name


def status_and_action(url: str, ledger_urls: set[str], seed_urls: set[str], score: int) -> tuple[str, str]:
    key = canonical_url(url)
    if key in ledger_urls:
        return "已入库", "无需重复入库"
    if key in seed_urls:
        return "已在待采集", "等待正式抓取"
    if score >= 55:
        return "新增候选", "建议回填待采集"
    return "低分候选", "暂不回填"


def enrich_rows(rows: list[dict[str, str]], min_score: int, include_existing: bool) -> list[dict[str, str]]:
    ledger_urls = {canonical_url(row.get("原文链接", "")) for row in read_csv(LEDGER_CSV) if row.get("原文链接")}
    seed_urls = {canonical_url(row.get("url", "")) for row in read_csv(SEED_CSV) if row.get("url")}
    batch = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    output: list[dict[str, str]] = []
    for row in rows:
        if not is_power_relevant(row):
            continue
        score = quality_score(row)
        status, action = status_and_action(row.get("原文链接", ""), ledger_urls, seed_urls, score)
        if score < min_score:
            continue
        if not include_existing and status == "已入库":
            continue
        output.append(
            {
                "抓取批次": batch,
                "候选编号": candidate_id(row.get("原文链接", "")),
                "来源编号": row.get("来源编号", ""),
                "来源层级": row.get("来源层级", ""),
                "地区": row.get("地区", ""),
                "来源栏目": row.get("来源栏目", ""),
                "发布部门": row.get("发布部门", ""),
                "文件标题": row.get("文件标题", ""),
                "发布日期": row.get("发布日期", ""),
                "命中关键词": row.get("命中关键词", ""),
                "文档类型": document_type(row.get("原文链接", "")),
                "质量分": str(score),
                "入库状态": status,
                "建议动作": action,
                "原文链接": row.get("原文链接", ""),
                "来源列表页": row.get("来源列表页", ""),
            }
        )

    status_rank = {"新增候选": 0, "已在待采集": 1, "低分候选": 2, "已入库": 3}
    output.sort(
        key=lambda item: (
            status_rank.get(item["入库状态"], 9),
            int(item["质量分"]),
            item["发布日期"],
            item["文件标题"],
        ),
        reverse=False,
    )
    output.sort(key=lambda item: int(item["质量分"]), reverse=True)
    output.sort(key=lambda item: status_rank.get(item["入库状态"], 9))
    return output


def write_summary(path: Path, rows: list[dict[str, str]], errors: list[str], source_count: int) -> Path:
    summary_path = path.with_suffix(".summary.json")
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        by_status[row["入库状态"]] = by_status.get(row["入库状态"], 0) + 1
        by_source[row["来源栏目"]] = by_source.get(row["来源栏目"], 0) + 1
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "candidate_count": len(rows),
        "source_count": source_count,
        "error_count": len(errors),
        "by_status": by_status,
        "top_sources": sorted(by_source.items(), key=lambda item: item[1], reverse=True)[:20],
        "errors": errors[:20],
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary_path


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Build a large candidate pool from whitelisted official sources.")
    parser.add_argument("--pages", type=int, default=20, help="HTML pages to try for each source.")
    parser.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds.")
    parser.add_argument("--min-score", type=int, default=0, help="Drop candidates below this quality score.")
    parser.add_argument("--include-existing", action="store_true", help="Keep candidates already in the ledger.")
    parser.add_argument("--limit", type=int, default=20, help="Print first N rows.")
    parser.add_argument("--source-file", default=str(SOURCE_CSV), help="Source whitelist CSV path.")
    parser.add_argument("--keywords", nargs="*", default=BROAD_KEYWORDS, help="Title keywords.")
    args = parser.parse_args()

    collector = load_collector()
    rows, errors, source_count = collector.collect_candidates(
        keywords=args.keywords,
        timeout=args.timeout,
        source_file=Path(args.source_file),
        pages=args.pages,
        max_page_errors=3,
    )
    output_rows = enrich_rows(rows, min_score=args.min_score, include_existing=args.include_existing)
    output_path = OUTPUT_DIR / f"大规模候选池_{dt.date.today():%Y%m%d}.csv"
    write_csv(output_path, output_rows)
    summary_path = write_summary(output_path, output_rows, errors, source_count)

    for row in output_rows[: args.limit]:
        print(
            f"{row['入库状态']} | {row['质量分']} | {row['发布日期']} | "
            f"{row['地区']} | {row['来源栏目']} | {row['文件标题']}"
        )
    if errors:
        print("---- failed sources ----")
        for error in errors[:10]:
            print(error)

    print(f"候选池：{output_path}")
    print(f"摘要：{summary_path}")
    print(f"候选总数：{len(output_rows)}，扫描来源：{source_count}，失败来源：{len(errors)}")

    append_log(
        action_type="大规模候选池采集",
        content=f"按来源清单进行大规模候选采集，生成候选 {len(output_rows)} 条",
        files=f"{output_path.relative_to(ROOT)}; {summary_path.relative_to(ROOT)}",
        command=" ".join([str(Path(sys.executable)), *sys.argv]),
        result="完成" if not errors else "部分完成",
        note=f"pages={args.pages}; min_score={args.min_score}; 扫描来源={source_count}; 失败来源={len(errors)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
