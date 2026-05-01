"""Run the 15-day knowledge-base update cycle.

The cycle is intentionally conservative: high-confidence official candidates can be
ingested automatically, while low-score/duplicate/uncertain candidates remain in
the candidate pool for review and do not enter the main QA index.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
LOG_DIR = ROOT / "06_实验日志"
LEDGER_CSV = META_DIR / "政策资料台账.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def run_step(
    name: str,
    args: list[str],
    dry_run: bool,
    execute_in_dry_run: bool = False,
    fatal: bool = True,
) -> dict[str, str | int | bool]:
    command = [sys.executable, *args]
    if dry_run and not execute_in_dry_run:
        print(f"[dry-run] {name}: {' '.join(command)}")
        return {"name": name, "status": "skipped", "returncode": 0, "command": " ".join(command), "fatal": fatal}

    print(f"[run] {name}: {' '.join(command)}")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, cwd=ROOT, text=True, encoding="utf-8", errors="replace", env=env)
    if completed.returncode == 0:
        status = "ok"
    elif fatal:
        status = "failed"
    else:
        status = "warning"
    return {"name": name, "status": status, "returncode": completed.returncode, "command": " ".join(command), "fatal": fatal}


def latest_candidate_pool() -> Path | None:
    files = sorted(OUTPUT_DIR.glob("大规模候选池_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def summarize_candidates(path: Path | None) -> dict[str, int]:
    if not path:
        return {}
    rows = read_csv(path)
    counts: dict[str, int] = {}
    for row in rows:
        status = row.get("入库状态", "未知") or "未知"
        counts[status] = counts.get(status, 0) + 1
    return counts


def count_rows(path: Path) -> int:
    return len(read_csv(path))


def write_report(
    started_at: dt.datetime,
    before_count: int,
    after_count: int,
    candidate_path: Path | None,
    steps: list[dict[str, str | int | bool]],
    strict_gate_score: int,
) -> Path:
    report_path = LOG_DIR / f"更新报告_{dt.date.today():%Y%m%d}.md"
    if report_path.exists():
        report_path = LOG_DIR / f"更新报告_{dt.datetime.now():%Y%m%d_%H%M%S}.md"
    candidate_counts = summarize_candidates(candidate_path)
    failed = [step for step in steps if step["status"] == "failed"]
    warnings = [step for step in steps if step["status"] == "warning"]
    lines = [
        f"# 知识库15天更新报告 {dt.date.today():%Y-%m-%d}",
        "",
        f"- 开始时间：{started_at.isoformat(timespec='seconds')}",
        f"- 结束时间：{dt.datetime.now().isoformat(timespec='seconds')}",
        f"- 更新前台账：{before_count} 条",
        f"- 更新后台账：{after_count} 条",
        f"- 新增台账：{after_count - before_count} 条",
        f"- 严格入库分数线：{strict_gate_score}",
        f"- 候选池：{candidate_path.relative_to(ROOT) if candidate_path else '未生成'}",
        "",
        "## 候选分流",
        "",
    ]
    if candidate_counts:
        lines.extend(f"- {key}：{value}" for key, value in sorted(candidate_counts.items()))
    else:
        lines.append("- 无候选统计")
    lines.extend(["", "## 执行步骤", ""])
    for step in steps:
        lines.append(f"- {step['status']} | {step['name']} | returncode={step['returncode']}")
    lines.extend(["", "## 风险提示", ""])
    if failed:
        lines.extend(f"- 失败步骤：{step['name']}" for step in failed)
    if warnings:
        lines.extend(f"- 非致命告警：{step['name']} returncode={step['returncode']}" for step in warnings)
    else:
        lines.append("- 未发现非致命告警")
    if not failed:
        lines.append("- 未发现致命失败步骤")
    if after_count < 500:
        lines.append("- 台账尚未达到 500 条，下一轮需继续扩源或降低严格分流阈值后人工复核。")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the 15-day full update cycle.")
    parser.add_argument("--full-auto", action="store_true", help="Actually write back and fetch new documents.")
    parser.add_argument("--strict-gate", action="store_true", help="Use strict high-confidence backfill gate.")
    parser.add_argument("--target-count", type=int, default=600, help="Target ledger row count for expansion.")
    parser.add_argument("--pages", type=int, default=25, help="Candidate pages per source.")
    parser.add_argument("--timeout", type=int, default=20, help="Network timeout seconds.")
    parser.add_argument("--delay", type=float, default=0.5, help="Fetch delay seconds.")
    parser.add_argument("--min-score", type=int, default=65, help="Backfill score threshold in strict mode.")
    parser.add_argument("--skip-snapshots", action="store_true", help="Skip rebuilding web snapshots.")
    parser.add_argument("--skip-ocr", action="store_true", help="Skip OCR and only extract embedded/DOCX text.")
    args = parser.parse_args()

    started_at = dt.datetime.now()
    before_count = count_rows(LEDGER_CSV)
    needed = max(0, args.target_count - before_count)
    dry_run = not args.full_auto
    strict_score = args.min_score if args.strict_gate else 55
    steps: list[dict[str, str | int | bool]] = []

    steps.append(
        run_step(
            "构建大规模候选池",
            [
                "scripts/15_mass_crawl_candidates.py",
                "--pages",
                str(args.pages),
                "--timeout",
                str(args.timeout),
                "--min-score",
                "0",
                "--limit",
                "20",
            ],
            dry_run=False,
            execute_in_dry_run=True,
        )
    )
    candidate_path = latest_candidate_pool()

    if needed > 0:
        backfill_args = [
            "scripts/06_backfill_candidates_to_seed.py",
            "--candidate",
            str(candidate_path or ""),
            "--min-score",
            str(strict_score),
            "--limit",
            str(needed),
        ]
        if args.full_auto:
            backfill_args.append("--write")
        steps.append(run_step("候选回填待采集", backfill_args, dry_run=False, execute_in_dry_run=True))

        fetch_args = [
            "scripts/01_fetch_seed_urls.py",
            "--limit",
            str(needed),
            "--delay",
            str(args.delay),
            "--timeout",
            str(args.timeout),
        ]
        if not args.full_auto:
            fetch_args.append("--dry-run")
        steps.append(run_step("正式采集入库", fetch_args, dry_run=False, execute_in_dry_run=True))

    steps.append(
        run_step(
            "下载PDF附件",
            ["scripts/20_download_pdf_attachments.py", "--delay", "0", "--timeout", str(args.timeout)],
            dry_run,
            fatal=False,
        )
    )
    extract_args = ["scripts/14_extract_pdf_texts.py"]
    if not args.skip_ocr:
        extract_args.append("--ocr")
    steps.append(run_step("抽取PDF和DOCX文本", extract_args, dry_run))
    steps.append(run_step("回填来源机构", ["scripts/11_backfill_source_org.py"], dry_run))
    steps.append(run_step("重建文号和规则清单", ["scripts/12_doc_number_and_rule_list.py", "--rebuild-doc-no"], dry_run))
    steps.append(run_step("重建政策关联关系", ["scripts/13_build_policy_relations.py"], dry_run))
    steps.append(run_step("重建知识切片", ["scripts/16_build_knowledge_chunks.py"], dry_run))
    steps.append(run_step("重建RAG索引", ["scripts/17_build_rag_index.py"], dry_run))
    if not args.skip_snapshots:
        steps.append(run_step("重建网页快照", ["scripts/19_build_web_snapshots.py", "--offline-only"], dry_run))
    steps.append(run_step("重建搜索页面", ["scripts/09_build_search_page.py"], dry_run))
    steps.append(run_step("检索自检", ["scripts/07_search.py", "--self-check-only"], dry_run=False, execute_in_dry_run=True))
    steps.append(run_step("RAG自检", ["scripts/18_rag_query.py", "--self-check"], dry_run=False, execute_in_dry_run=True))
    steps.append(
        run_step(
            "数据验收",
            [
                "scripts/23_validate_knowledge_base.py",
                "--min-ledger-count",
                str(args.target_count),
                "--max-ledger-count",
                "600",
                "--write-report",
                "--warn-only",
            ],
            dry_run=False,
            execute_in_dry_run=True,
            fatal=False,
        )
    )

    after_count = count_rows(LEDGER_CSV)
    report_path = write_report(started_at, before_count, after_count, candidate_path, steps, strict_score)
    print(f"更新报告：{report_path}")

    failed = [step for step in steps if step["status"] == "failed"]
    append_log(
        action_type="15天自动更新",
        content=f"执行知识库15天更新周期，台账 {before_count} -> {after_count}",
        files=str(report_path.relative_to(ROOT)),
        command=" ".join([str(Path(sys.executable)), *sys.argv]),
        result="完成" if not failed else "部分失败",
        note=f"full_auto={args.full_auto}; strict_gate={args.strict_gate}; failed_steps={len(failed)}",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
