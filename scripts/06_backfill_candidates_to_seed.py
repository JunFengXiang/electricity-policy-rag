from __future__ import annotations

import argparse
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "02_元数据" / "待采集链接.csv"
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"

SEED_FIELDS = [
    "url",
    "来源名称",
    "来源类型",
    "发布部门",
    "发布日期",
    "适用地区",
    "市场主题",
    "关键词",
    "权威等级",
    "时间敏感类型",
    "有效状态",
    "是否原文",
    "备注",
]


def read_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_dicts(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def latest_candidates() -> Path:
    files = sorted(OUTPUT_DIR.glob("来源清单候选链接_*.csv"), key=lambda p: p.name, reverse=True)
    if not files:
        raise FileNotFoundError("未找到来源清单候选链接文件")
    return files[0]


def infer_source_type(row: dict[str, str]) -> str:
    source_level = row.get("来源层级", "")
    source_name = row.get("来源栏目", "")
    if "交易" in source_level or "交易中心" in source_name:
        return "交易规则"
    if "监管" in source_level or "监管" in source_name:
        return "监管规则"
    return "官方政策"


def infer_authority(row: dict[str, str], source_type: str) -> str:
    source_level = row.get("来源层级", "")
    if "国家级" in source_level or source_type == "官方政策":
        return "A"
    if source_type in {"监管规则", "交易规则"}:
        return "B"
    return "C"


def infer_time_type(title: str, source_type: str) -> str:
    if "征求意见" in title or "公开征求" in title:
        return "中期参考型"
    if source_type in {"监管规则", "交易规则"}:
        return "中期参考型"
    return "长期有效型"


def infer_status(title: str) -> str:
    if "征求意见" in title or "公开征求" in title:
        return "征求意见"
    if "试行" in title:
        return "试行"
    return "现行有效"


def infer_topics(title: str, hits: str) -> str:
    text = f"{title};{hits}"
    topics: list[str] = []
    rules = [
        ("电力市场", ["电力市场", "市场规则", "市场监管"]),
        ("中长期", ["中长期"]),
        ("现货", ["现货"]),
        ("辅助服务", ["辅助服务"]),
        ("省间交易", ["省间"]),
        ("省内交易", ["省内"]),
        ("绿电", ["绿电", "绿证"]),
        ("容量电价", ["容量电价"]),
        ("需求响应", ["需求响应"]),
        ("储能", ["储能"]),
        ("新能源", ["新能源"]),
        ("计量结算", ["计量结算", "结算"]),
    ]
    for topic, keys in rules:
        if any(key in text for key in keys):
            topics.append(topic)
    return ";".join(topics or ["电力市场"])


def candidate_to_seed(row: dict[str, str]) -> dict[str, str]:
    title = row.get("文件标题", "").strip()
    source_type = infer_source_type(row)
    return {
        "url": row.get("原文链接", "").strip(),
        "来源名称": row.get("来源栏目", "").strip(),
        "来源类型": source_type,
        "发布部门": row.get("发布部门", "").strip(),
        "发布日期": row.get("发布日期", "").strip(),
        "适用地区": row.get("地区", "").strip(),
        "市场主题": infer_topics(title, row.get("命中关键词", "")),
        "关键词": row.get("命中关键词", "").strip() or title,
        "权威等级": infer_authority(row, source_type),
        "时间敏感类型": infer_time_type(title, source_type),
        "有效状态": infer_status(title),
        "是否原文": "是",
        "备注": title,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill candidate official links into 待采集链接.csv.")
    parser.add_argument("--candidate", default="", help="Candidate csv path. Defaults to latest 来源清单候选链接 file.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum new rows to append. 0 means all.")
    parser.add_argument("--write", action="store_true", help="Write changes. Without it only previews.")
    args = parser.parse_args()

    candidate_path = Path(args.candidate) if args.candidate else latest_candidates()
    candidates = read_dicts(candidate_path)
    seeds = read_dicts(SEED_CSV)
    ledger = read_dicts(LEDGER_CSV)

    existing_urls = {
        *(row.get("url", "").strip() for row in seeds),
        *(row.get("原文链接", "").strip() for row in ledger),
    }

    new_rows: list[dict[str, str]] = []
    for row in candidates:
        url = row.get("原文链接", "").strip()
        if not url or url in existing_urls:
            continue
        seed = candidate_to_seed(row)
        new_rows.append(seed)
        existing_urls.add(url)
        if args.limit and len(new_rows) >= args.limit:
            break

    print(f"候选文件：{candidate_path}")
    print(f"可新增链接：{len(new_rows)}")
    for row in new_rows[:10]:
        print(f"- {row['适用地区']} | {row['来源名称']} | {row['备注']}")

    if not args.write:
        print("当前为dry-run，未写入。需要写入时追加 --write")
        return 0

    write_dicts(SEED_CSV, seeds + new_rows, SEED_FIELDS)
    print(f"已写入 {len(new_rows)} 条到 {SEED_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
