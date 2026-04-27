from __future__ import annotations

import csv
import datetime as dt
import re
import shutil
from pathlib import Path

from pypdf import PdfReader

from log_action import append_log


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


def has_path(row: dict[str, str], rel_path: str) -> bool:
    normalized = rel_path.replace("/", "\\")
    return any(item.replace("/", "\\") == normalized for item in split_paths(row.get("本地文件路径", "")))


def existing_text_for_pdf(path: Path) -> Path | None:
    for suffix in [".ocr.txt", ".txt"]:
        text_path = TEXT_ROOT / f"{path.stem}{suffix}"
        if text_path.exists() and text_path.stat().st_size > 0:
            return text_path
    return None


def extract_pdf_text(path: Path, max_pages: int | None = None) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    pages = reader.pages[:max_pages] if max_pages else reader.pages
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def ocr_pdf_text(path: Path, dpi: int = 160, max_pages: int | None = None) -> tuple[str, int]:
    import fitz
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    zoom = dpi / 72
    parts: list[str] = []
    page_count = 0

    with fitz.open(path) as doc:
        total = min(len(doc), max_pages) if max_pages else len(doc)
        for page_index in range(total):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            result, _ = engine(image)
            lines: list[str] = []
            if result:
                for item in result:
                    if len(item) >= 2 and item[1]:
                        lines.append(str(item[1]).strip())
            if lines:
                page_count += 1
                parts.append(f"--- 第 {page_index + 1} 页 OCR ---\n" + "\n".join(lines))

    return "\n\n".join(parts), page_count


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


def append_note(row: dict[str, str], note: str) -> None:
    current = row.get("备注", "")
    if note in current:
        return
    row["备注"] = f"{current}；{note}" if current else note


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Extract text from local PDF files and optionally OCR scanned PDFs.")
    parser.add_argument("--ocr", action="store_true", help="Run OCR when embedded PDF text is missing or too short.")
    parser.add_argument("--force-ocr", action="store_true", help="Run OCR even when a text path already exists.")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit pages per PDF for testing. 0 means all pages.")
    parser.add_argument("--ocr-dpi", type=int, default=160, help="OCR render DPI.")
    parser.add_argument("--min-text-chars", type=int, default=120, help="OCR threshold for embedded text length.")
    parser.add_argument("--sync-existing", action="store_true", help="Only attach existing .txt/.ocr.txt files to ledger.")
    args = parser.parse_args()

    rows = read_rows(LEDGER_CSV)
    fields = list(rows[0].keys()) if rows else []
    TEXT_ROOT.mkdir(parents=True, exist_ok=True)

    extracted = 0
    ocr_extracted = 0
    ocr_pages = 0
    title_fixed = 0
    changed = False
    max_pages = args.max_pages or None

    for row in rows:
        path = pdf_path(row)
        if not path:
            continue
        existing_text = existing_text_for_pdf(path)
        if existing_text and not args.force_ocr:
            rel_text = str(existing_text.relative_to(ROOT))
            if not has_path(row, rel_text):
                row["本地文件路径"] = "; ".join([*split_paths(row.get("本地文件路径", "")), rel_text])
                changed = True
            if existing_text.name.endswith(".ocr.txt"):
                append_note(row, "OCR文本待人工复核")
                changed = True
            if args.sync_existing or has_text_path(row):
                continue
        if args.sync_existing:
            continue
        if has_text_path(row) and not args.force_ocr:
            continue

        text = extract_pdf_text(path, max_pages=max_pages)
        used_ocr = False
        if args.ocr and (args.force_ocr or len(text.strip()) < args.min_text_chars):
            ocr_text, page_count = ocr_pdf_text(path, dpi=args.ocr_dpi, max_pages=max_pages)
            if ocr_text.strip():
                text = ocr_text
                used_ocr = True
                ocr_pages += page_count

        if not text.strip():
            continue

        suffix = ".ocr.txt" if used_ocr else ".txt"
        text_path = TEXT_ROOT / f"{path.stem}{suffix}"
        text_path.write_text(text, encoding="utf-8")
        extracted += 1
        if used_ocr:
            ocr_extracted += 1

        rel_text = str(text_path.relative_to(ROOT))
        existing_paths = split_paths(row.get("本地文件路径", ""))
        if used_ocr:
            if not has_path(row, rel_text):
                row["本地文件路径"] = "; ".join([*existing_paths, rel_text])
                changed = True
            append_note(row, "OCR文本待人工复核")
            changed = True
        elif not has_text_path(row):
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
    print(f"OCR抽取：{ocr_extracted} 个，OCR页数：{ocr_pages}")
    print(f"标题修正：{title_fixed} 条")
    append_log(
        action_type="PDF文本抽取",
        content=f"抽取PDF文本 {extracted} 个，其中OCR {ocr_extracted} 个",
        files=f"{TEXT_ROOT.relative_to(ROOT)}; {LEDGER_CSV.relative_to(ROOT)}",
        command=f"{Path(__file__).name} --ocr={args.ocr} --force-ocr={args.force_ocr} --max-pages={args.max_pages}",
        result="完成",
        note=f"OCR页数={ocr_pages}; dpi={args.ocr_dpi}; min_text_chars={args.min_text_chars}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
