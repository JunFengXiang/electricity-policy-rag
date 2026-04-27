from __future__ import annotations

import csv
import datetime as dt
import re
import shutil
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
TEXT_ROOT = ROOT / "03_处理后文本"
BACKUP_DIR = ROOT / "02_元数据" / "备份"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def backup(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = BACKUP_DIR / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    shutil.copy2(path, target)
    return target


def split_paths(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def pdf_path(row: dict[str, str]) -> Path | None:
    for item in split_paths(row.get("本地文件路径", "")):
        if item.lower().endswith(".pdf"):
            path = ROOT / item
            if path.exists():
                return path
    return None


def has_text_path(row: dict[str, str]) -> bool:
    return any(item.lower().endswith(".txt") for item in split_paths(row.get("本地文件路径", "")))


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def infer_title(text: str) -> str:
    compact = re.sub(r"\s+", "", text or "")
    patterns = [
        r"(国家能源局[\u4e00-\u9fa5]{0,12}监管局关于印发《[^》]{8,80}》的通知)",
        r"(关于印发《[^》]{8,80}》的通知)",
        r"(关于发布《[^》]{8,80}》的通知)",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact)
        if match:
            return match.group(1)
    return ""


def main() -> int:
    rows = read_rows(LEDGER_CSV)
    fields = list(rows[0].keys()) if rows else []
    TEXT_ROOT.mkdir(parents=True, exist_ok=True)

    extracted = 0
    title_fixed = 0
    changed = False

    for row in rows:
        path = pdf_path(row)
        if not path:
            continue

        text = extract_pdf_text(path)
        if not text.strip():
            continue

        text_path = TEXT_ROOT / f"{path.stem}.txt"
        text_path.write_text(text, encoding="utf-8")
        extracted += 1

        rel_text = str(text_path.relative_to(ROOT))
        existing_paths = split_paths(row.get("本地文件路径", ""))
        if not has_text_path(row):
            row["本地文件路径"] = "; ".join([*existing_paths, rel_text])
            changed = True

        suggested_title = infer_title(text)
        current_title = row.get("文件标题", "")
        if suggested_title and ("..." in current_title or len(current_title.strip()) < 12):
            row["文件标题"] = suggested_title
            title_fixed += 1
            changed = True

    if changed:
        backup_path = backup(LEDGER_CSV)
        write_rows(LEDGER_CSV, rows, fields)
        print(f"备份文件：{backup_path}")

    print(f"PDF抽取：{extracted} 个")
    print(f"标题修正：{title_fixed} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
