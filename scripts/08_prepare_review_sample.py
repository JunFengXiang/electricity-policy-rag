from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
REVIEW_CSV = ROOT / "02_元数据" / "人工核验表.csv"

REVIEW_FIELDS = [
    "核验编号",
    "资料编号",
    "文件标题",
    "来源名称",
    "来源类型",
    "适用地区",
    "市场主题",
    "核验类型",
    "标题是否准确",
    "发布部门是否准确",
    "发布日期是否准确",
    "地区是否准确",
    "主题标签是否准确",
    "权威等级是否准确",
    "有效状态是否准确",
    "原文链接是否可访问",
    "正文是否完整",
    "附件是否下载",
    "是否重复",
    "新旧版本关系",
    "问题描述",
    "修正建议",
    "核验人",
    "核验日期",
    "核验结论",
    "备注",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def local_paths(row: dict[str, str]) -> list[Path]:
    paths = []
    for item in row.get("本地文件路径", "").split(";"):
        item = item.strip()
        if item:
            paths.append(ROOT / item)
    return paths


def has_text(row: dict[str, str]) -> bool:
    return any(path.suffix.lower() == ".txt" and path.exists() and path.stat().st_size > 0 for path in local_paths(row))


def raw_file_state(row: dict[str, str]) -> str:
    paths = local_paths(row)
    if not paths:
        return "未记录本地路径"
    if all(path.exists() for path in paths):
        return "本地文件存在"
    return "部分本地文件缺失"


def build_review_row(index: int, row: dict[str, str], existing_ids: set[str]) -> dict[str, str]:
    doc_id = row.get("资料编号", "")
    review_id = f"V-{index:03d}"
    while review_id in existing_ids:
        index += 1
        review_id = f"V-{index:03d}"
    existing_ids.add(review_id)

    text_state = "已生成文本，待人工抽查完整性" if has_text(row) else "未生成文本或文本为空"
    link_state = "已采集成功，待人工复测" if row.get("原文链接") else "缺少原文链接"
    duplicate_hint = "待核验"
    title = row.get("文件标题", "")
    note = row.get("备注", "")
    if note and title and note != title:
        duplicate_hint = "标题与备注不完全一致，需核验"

    return {
        "核验编号": review_id,
        "资料编号": doc_id,
        "文件标题": title,
        "来源名称": row.get("采集来源机构", "") or row.get("发布部门", ""),
        "来源类型": row.get("来源类型", ""),
        "适用地区": row.get("适用地区", ""),
        "市场主题": row.get("市场主题", ""),
        "核验类型": "自动预核验",
        "标题是否准确": "待人工确认",
        "发布部门是否准确": "待人工确认",
        "发布日期是否准确": "待人工确认",
        "地区是否准确": "待人工确认",
        "主题标签是否准确": "待人工确认",
        "权威等级是否准确": "待人工确认",
        "有效状态是否准确": "待人工确认",
        "原文链接是否可访问": link_state,
        "正文是否完整": text_state,
        "附件是否下载": raw_file_state(row),
        "是否重复": duplicate_hint,
        "新旧版本关系": "待人工确认",
        "问题描述": "",
        "修正建议": "",
        "核验人": "Codex自动预核验",
        "核验日期": dt.date.today().strftime("%Y-%m-%d"),
        "核验结论": "待人工确认",
        "备注": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare first review sample rows from ledger.")
    parser.add_argument("--limit", type=int, default=20, help="Total number of review rows to prepare.")
    parser.add_argument("--write", action="store_true", help="Write review csv. Without this flag only previews.")
    args = parser.parse_args()

    ledger_rows = read_csv(LEDGER_CSV)
    review_rows = read_csv(REVIEW_CSV)
    existing_doc_ids = {row.get("资料编号", "") for row in review_rows if row.get("资料编号")}
    existing_review_ids = {row.get("核验编号", "") for row in review_rows if row.get("核验编号")}

    next_index = len(review_rows) + 1
    new_rows: list[dict[str, str]] = []
    for row in ledger_rows:
        if len(review_rows) + len(new_rows) >= args.limit:
            break
        doc_id = row.get("资料编号", "")
        if not doc_id or doc_id in existing_doc_ids:
            continue
        new_rows.append(build_review_row(next_index, row, existing_review_ids))
        existing_doc_ids.add(doc_id)
        next_index += 1

    print(f"当前核验表：{len(review_rows)} 行")
    print(f"计划新增：{len(new_rows)} 行")
    for row in new_rows[:10]:
        print(f"- {row['核验编号']} {row['资料编号']} {row['文件标题']}")

    if not args.write:
        print("当前为dry-run，未写入。需要写入时追加 --write")
        return 0

    write_csv(REVIEW_CSV, review_rows + new_rows)
    print(f"已写入人工核验表，总行数：{len(review_rows) + len(new_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
