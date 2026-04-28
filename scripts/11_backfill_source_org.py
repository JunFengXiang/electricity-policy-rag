"""回填并规范化“采集来源机构”字段。

发布部门和采集来源机构不总是同一个概念：前者是政策署名，后者是我们从哪里抓到。
本脚本用于补齐来源机构，方便后续统计来源覆盖和排查采集链路。
"""

from __future__ import annotations

import csv
import datetime as dt
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
SEED_CSV = META_DIR / "待采集链接.csv"
BACKUP_DIR = META_DIR / "备份"

NEW_FIELD = "采集来源机构"
AFTER_FIELD = "发布部门"

COLUMN_SUFFIXES = {
    "通知公告",
    "通知公示",
    "交易规则",
    "政策文件",
    "规范性文件",
    "行政规范性文件",
    "政策法规",
    "委局文件",
    "本委其他文件",
    "部门文件",
    "政府信息公开",
    "价格管理",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def backup(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = BACKUP_DIR / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / path.name
    shutil.copy2(path, target_path)
    return target_path


def insert_field(fields: list[str]) -> list[str]:
    """把新字段插到发布部门后面，保持台账列顺序便于人工阅读。"""
    if NEW_FIELD in fields:
        return fields
    if AFTER_FIELD not in fields:
        return [*fields, NEW_FIELD]
    index = fields.index(AFTER_FIELD) + 1
    return [*fields[:index], NEW_FIELD, *fields[index:]]


def normalize_source_org(value: str) -> str:
    """去掉栏目后缀，只保留真正的来源机构名称。"""
    value = (value or "").strip()
    for delimiter in ["-", "－", "—"]:
        if delimiter in value:
            left, right = value.rsplit(delimiter, 1)
            if right.strip() in COLUMN_SUFFIXES:
                return left.strip()
    return value


def main() -> int:
    """依据待采集链接和原有字段补齐政策台账中的采集来源机构。"""
    ledger_rows = read_csv(LEDGER_CSV)
    seed_rows = read_csv(SEED_CSV)
    fields = insert_field(read_header(LEDGER_CSV))

    source_by_url = {
        row.get("url", "").strip(): row.get("来源名称", "").strip()
        for row in seed_rows
        if row.get("url")
    }

    changed = 0
    for row in ledger_rows:
        old_value = row.get(NEW_FIELD, "").strip()
        source_org = old_value or source_by_url.get(row.get("原文链接", "").strip()) or row.get("发布部门", "").strip()
        source_org = normalize_source_org(source_org)
        row[NEW_FIELD] = source_org
        if source_org != old_value:
            changed += 1

    backup_path = backup(LEDGER_CSV)
    write_csv(LEDGER_CSV, ledger_rows, fields)
    print(f"已回填 {NEW_FIELD}：{changed} 条")
    print(f"备份文件：{backup_path}")
    print(f"台账文件：{LEDGER_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
