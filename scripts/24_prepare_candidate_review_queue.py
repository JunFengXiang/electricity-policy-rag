"""从候选池生成 50-54 分人工复核队列。

低于自动入库线但接近门槛的候选，不直接进入主台账和主问答索引。
本脚本把这些候选单独输出，供人工确认来源、标题、有效状态和附件后再回填。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "05_输出成果"

FIELDS = [
    "复核优先级",
    "复核原因",
    "建议动作",
    "质量分",
    "地区",
    "来源栏目",
    "发布部门",
    "文件标题",
    "发布日期",
    "命中关键词",
    "文档类型",
    "原文链接",
    "来源列表页",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def latest_candidate_pool() -> Path:
    files = sorted(OUTPUT_DIR.glob("大规模候选池_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError("未找到大规模候选池")
    return files[0]


def review_reason(title: str) -> tuple[str, str, str]:
    if "征求" in title:
        return "P2", "征求意见类，需确认是否已有正式稿", "人工确认有效状态后再入库"
    if "公示" in title or "公告" in title:
        return "P2", "公告/公示类，需确认是否为规则正文或仅为过程信息", "人工确认正文性质后再入库"
    if "解读" in title or "说明" in title:
        return "P3", "解读/说明类，只能作为辅助资料", "放入解读资料或辅助索引"
    if any(term in title for term in ["规则", "细则", "办法", "方案", "输配电价", "现货", "中长期", "绿电", "电网"]):
        return "P1", "接近自动线的规则/政策类候选", "人工核验来源和有效状态后优先回填"
    return "P2", "接近自动线但主题需确认", "人工核验后决定是否入库"


def build_queue(candidate_path: Path, min_score: int, max_score: int) -> list[dict[str, str]]:
    rows = []
    for row in read_csv(candidate_path):
        try:
            score = int(row.get("质量分", "0"))
        except ValueError:
            continue
        if row.get("入库状态") != "低分候选":
            continue
        if score < min_score or score > max_score:
            continue
        title = row.get("文件标题", "")
        priority, reason, action = review_reason(title)
        rows.append(
            {
                "复核优先级": priority,
                "复核原因": reason,
                "建议动作": action,
                "质量分": row.get("质量分", ""),
                "地区": row.get("地区", ""),
                "来源栏目": row.get("来源栏目", ""),
                "发布部门": row.get("发布部门", ""),
                "文件标题": title,
                "发布日期": row.get("发布日期", ""),
                "命中关键词": row.get("命中关键词", ""),
                "文档类型": row.get("文档类型", ""),
                "原文链接": row.get("原文链接", ""),
                "来源列表页": row.get("来源列表页", ""),
            }
        )
    priority_rank = {"P1": 0, "P2": 1, "P3": 2}
    rows.sort(key=lambda item: (priority_rank.get(item["复核优先级"], 9), -int(item["质量分"]), item["地区"], item["文件标题"]))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare manual review queue for low-score candidates.")
    parser.add_argument("--candidate", default="", help="Candidate pool CSV. Defaults to latest 大规模候选池.")
    parser.add_argument("--min-score", type=int, default=50)
    parser.add_argument("--max-score", type=int, default=54)
    args = parser.parse_args()

    candidate_path = Path(args.candidate) if args.candidate else latest_candidate_pool()
    if not candidate_path.is_absolute():
        candidate_path = ROOT / candidate_path
    rows = build_queue(candidate_path, args.min_score, args.max_score)
    output_path = OUTPUT_DIR / f"候选人工复核队列_{dt.date.today():%Y%m%d}.csv"
    write_csv(output_path, rows)
    print(f"复核队列：{output_path}")
    print(f"候选数量：{len(rows)}")

    append_log(
        action_type="候选人工复核队列",
        content=f"从候选池生成 {args.min_score}-{args.max_score} 分人工复核队列 {len(rows)} 条",
        files=f"{candidate_path.relative_to(ROOT)}; {output_path.relative_to(ROOT)}",
        command="python scripts/24_prepare_candidate_review_queue.py",
        result="完成",
        note="低分候选不进入主索引，人工复核后再回填",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
