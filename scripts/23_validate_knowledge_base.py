"""知识库数据验收脚本。

检查台账规模、本地路径、PDF/DOCX 文本抽取、知识切片覆盖和附件失败项。
默认用于 500-600 条目标验收；更新周期中可配合 --warn-only 生成非致命报告。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
CHUNK_CSV = META_DIR / "知识切片表.csv"
ATTACHMENT_CSV = META_DIR / "PDF附件清单.csv"
PDF_ATTACHMENT_JSON = OUTPUT_DIR / "pdf_attachments.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def split_paths(value: str) -> list[str]:
    return [item.strip().strip('"') for item in (value or "").split(";") if item.strip()]


def is_url(value: str) -> bool:
    return bool(re.match(r"^https?://", value or "", flags=re.I))


def local_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def path_exists(value: str) -> bool:
    if not value or is_url(value):
        return True
    return local_path(value).exists()


def suffix(value: str) -> str:
    return Path(value.replace("\\", "/")).suffix.lower()


def has_text_path(row: dict[str, str]) -> bool:
    for item in split_paths(row.get("本地文件路径", "")):
        if suffix(item) == ".txt" and path_exists(item):
            return True
    return False


def has_source_file(row: dict[str, str], suffixes: set[str]) -> bool:
    return any(suffix(item) in suffixes for item in split_paths(row.get("本地文件路径", "")))


def latest_candidate_pool() -> Path | None:
    files = sorted(OUTPUT_DIR.glob("大规模候选池_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def count_candidate_statuses(path: Path | None) -> dict[str, int]:
    if not path:
        return {}
    counts: dict[str, int] = {}
    for row in read_csv(path):
        status = row.get("入库状态", "") or "未知"
        counts[status] = counts.get(status, 0) + 1
    return counts


def read_pdf_failures() -> tuple[int, list[dict[str, str]]]:
    if PDF_ATTACHMENT_JSON.exists():
        try:
            data = json.loads(PDF_ATTACHMENT_JSON.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0, []
        failures = data.get("failures", [])
        return int(data.get("failure_count", len(failures)) or 0), failures

    failures = [
        row
        for row in read_csv(ATTACHMENT_CSV)
        if row.get("下载状态", "") and row.get("下载状态", "") not in {"downloaded", "existing_local_pdf", "local_pdf"}
    ]
    return len(failures), failures


def validate(min_count: int, max_count: int, min_chunk_coverage: float) -> tuple[list[str], list[str], dict[str, object]]:
    errors: list[str] = []
    warnings: list[str] = []
    summary: dict[str, object] = {}

    if not LEDGER_CSV.exists():
        return [f"缺少台账：{LEDGER_CSV}"], warnings, summary

    ledger = read_csv(LEDGER_CSV)
    chunks = read_csv(CHUNK_CSV)
    candidate_path = latest_candidate_pool()
    candidate_counts = count_candidate_statuses(candidate_path)
    pdf_failure_count, pdf_failures = read_pdf_failures()

    ledger_count = len(ledger)
    summary["台账条数"] = ledger_count
    summary["切片条数"] = len(chunks)
    summary["候选池"] = str(candidate_path.relative_to(ROOT)) if candidate_path else ""
    summary["候选分流"] = candidate_counts
    summary["PDF失败附件数"] = pdf_failure_count

    if ledger_count < min_count:
        warnings.append(f"台账 {ledger_count} 条，尚未达到目标下限 {min_count} 条")
    if max_count and ledger_count > max_count:
        warnings.append(f"台账 {ledger_count} 条，超过目标上限 {max_count} 条，需检查是否混入低质资料")

    missing_paths: list[str] = []
    docs_with_text: set[str] = set()
    docs_with_file: set[str] = set()
    pdf_docx_without_text: list[str] = []

    for row in ledger:
        doc_id = row.get("资料编号", "")
        title = row.get("文件标题", "")
        local_items = split_paths(row.get("本地文件路径", ""))
        if local_items:
            docs_with_file.add(doc_id)
        for item in local_items:
            if not path_exists(item):
                missing_paths.append(f"{doc_id} {title} -> {item}")
        if has_text_path(row):
            docs_with_text.add(doc_id)
        if has_source_file(row, {".pdf", ".doc", ".docx"}) and not has_text_path(row):
            pdf_docx_without_text.append(f"{doc_id} {title}")

    chunk_doc_ids = {row.get("资料编号", "") for row in chunks if row.get("资料编号", "")}
    docs_with_text_count = len(docs_with_text)
    chunk_coverage = len(chunk_doc_ids & docs_with_text) / docs_with_text_count if docs_with_text_count else 0.0

    summary["有本地文件资料数"] = len(docs_with_file)
    summary["已抽文本资料数"] = docs_with_text_count
    summary["切片覆盖资料数"] = len(chunk_doc_ids)
    summary["切片覆盖率"] = chunk_coverage
    summary["缺失本地路径数"] = len(missing_paths)
    summary["PDF或DOCX无文本数"] = len(pdf_docx_without_text)

    if missing_paths:
        errors.append(f"发现 {len(missing_paths)} 个台账本地路径不存在")
    if pdf_docx_without_text:
        warnings.append(f"发现 {len(pdf_docx_without_text)} 条含 PDF/DOCX 但未挂接文本路径")
    if docs_with_text_count and chunk_coverage < min_chunk_coverage:
        warnings.append(f"切片覆盖率 {chunk_coverage:.1%} 低于阈值 {min_chunk_coverage:.1%}")
    if pdf_failure_count:
        warnings.append(f"PDF 附件下载失败 {pdf_failure_count} 个，需单独复核")

    summary["缺失本地路径样例"] = missing_paths[:20]
    summary["PDF或DOCX无文本样例"] = pdf_docx_without_text[:20]
    summary["PDF失败样例"] = pdf_failures[:10]
    return errors, warnings, summary


def write_report(errors: list[str], warnings: list[str], summary: dict[str, object]) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"数据验收报告_{timestamp}.md"
    lines = [
        f"# 知识库数据验收报告 {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "## 汇总",
        "",
    ]
    for key in ["台账条数", "切片条数", "有本地文件资料数", "已抽文本资料数", "切片覆盖资料数", "切片覆盖率", "缺失本地路径数", "PDF或DOCX无文本数", "PDF失败附件数", "候选池"]:
        value = summary.get(key, "")
        if key == "切片覆盖率" and isinstance(value, float):
            value = f"{value:.1%}"
        lines.append(f"- {key}：{value}")

    candidate_counts = summary.get("候选分流", {})
    lines.extend(["", "## 候选分流", ""])
    if isinstance(candidate_counts, dict) and candidate_counts:
        lines.extend(f"- {key}：{value}" for key, value in sorted(candidate_counts.items()))
    else:
        lines.append("- 未发现候选池")

    lines.extend(["", "## 错误", ""])
    lines.extend(f"- {item}" for item in errors) if errors else lines.append("- 无")
    lines.extend(["", "## 提醒", ""])
    lines.extend(f"- {item}" for item in warnings) if warnings else lines.append("- 无")

    for title, key in [
        ("缺失本地路径样例", "缺失本地路径样例"),
        ("PDF/DOCX 未挂文本样例", "PDF或DOCX无文本样例"),
        ("PDF 失败样例", "PDF失败样例"),
    ]:
        samples = summary.get(key, [])
        lines.extend(["", f"## {title}", ""])
        if samples:
            for item in samples:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('资料编号', '')} {item.get('附件标题', '')} {item.get('下载状态', '')} {item.get('备注', '')}".strip())
                else:
                    lines.append(f"- {item}")
        else:
            lines.append("- 无")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def print_summary(errors: list[str], warnings: list[str], summary: dict[str, object]) -> None:
    print("数据验收汇总：")
    for key in ["台账条数", "切片条数", "已抽文本资料数", "切片覆盖资料数", "切片覆盖率", "缺失本地路径数", "PDF或DOCX无文本数", "PDF失败附件数"]:
        value = summary.get(key, "")
        if key == "切片覆盖率" and isinstance(value, float):
            value = f"{value:.1%}"
        print(f"- {key}: {value}")
    if errors:
        print("错误：")
        for item in errors:
            print(f"- {item}")
    if warnings:
        print("提醒：")
        for item in warnings:
            print(f"- {item}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate policy knowledge base data quality.")
    parser.add_argument("--min-ledger-count", type=int, default=500, help="Expected minimum ledger rows.")
    parser.add_argument("--max-ledger-count", type=int, default=600, help="Expected maximum ledger rows; 0 disables the check.")
    parser.add_argument("--min-chunk-coverage", type=float, default=0.95, help="Minimum chunk coverage for docs with text.")
    parser.add_argument("--write-report", action="store_true", help="Write a markdown validation report.")
    parser.add_argument("--self-check-only", action="store_true", help="Run checks without writing a report or log.")
    parser.add_argument("--warn-only", action="store_true", help="Return success even when errors/warnings are present.")
    args = parser.parse_args()

    errors, warnings, summary = validate(args.min_ledger_count, args.max_ledger_count, args.min_chunk_coverage)
    print_summary(errors, warnings, summary)

    report_path: Path | None = None
    if args.write_report and not args.self_check_only:
        report_path = write_report(errors, warnings, summary)
        print(f"验收报告：{report_path}")

    if not args.self_check_only:
        append_log(
            action_type="数据验收",
            content=f"检查台账、路径、附件和切片覆盖，台账 {summary.get('台账条数', 0)} 条",
            files=str(report_path.relative_to(ROOT)) if report_path else "02_元数据;05_输出成果",
            command="python scripts/23_validate_knowledge_base.py",
            result="通过" if not errors and not warnings else "有提醒" if not errors else "有错误",
            note=f"errors={len(errors)}; warnings={len(warnings)}",
        )

    if args.warn_only:
        return 0
    return 1 if errors or warnings else 0


if __name__ == "__main__":
    raise SystemExit(main())
