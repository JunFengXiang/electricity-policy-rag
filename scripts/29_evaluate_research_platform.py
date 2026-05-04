"""Evaluate the research-platform layer."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
VARIABLES_CSV = META_DIR / "政策变量表.csv"
QUALITY_CSV = META_DIR / "政策质量状态表.csv"
EVOLUTION_CSV = META_DIR / "政策演化关系表.csv"
RESEARCH_INDEX_JSON = OUTPUT_DIR / "research_platform_index.json"
REPORT_JSON = OUTPUT_DIR / "research_platform_evaluation.summary.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ratio(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def evaluate() -> dict[str, object]:
    variables = read_csv(VARIABLES_CSV)
    qualities = read_csv(QUALITY_CSV)
    evolutions = read_csv(EVOLUTION_CSV)
    variable_count = len(variables)
    required_fields = ["政策工具", "适用主体", "市场环节", "质量状态"]
    coverage = {field: ratio(sum(1 for row in variables if row.get(field)), variable_count) for field in required_fields}
    core_variable_coverage = ratio(
        sum(1 for row in variables if row.get("政策工具") and row.get("适用主体") and (row.get("市场环节") or row.get("规划场景"))),
        variable_count,
    )
    quality_complete_rate = ratio(sum(1 for row in qualities if row.get("质量状态")), len(qualities))
    citation_ready_count = sum(1 for row in qualities if row.get("是否可引用") == "是")
    relation_types = sorted({row.get("关系类型", "") for row in evolutions if row.get("关系类型")})
    focus_topics = ["电力市场", "辅助服务", "绿电", "新能源上网电价", "储能", "微电网", "新型电力系统", "电网规划"]
    topic_hits = {
        topic: sum(1 for row in evolutions if topic in row.get("主题链条", "") or topic in row.get("源标题", "") or topic in row.get("目标标题", ""))
        for topic in focus_topics
    }
    research_index_ok = RESEARCH_INDEX_JSON.exists() and RESEARCH_INDEX_JSON.stat().st_size > 500
    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "variable_count": variable_count,
        "evolution_relation_count": len(evolutions),
        "field_coverage": coverage,
        "core_variable_coverage": core_variable_coverage,
        "quality_complete_rate": quality_complete_rate,
        "citation_ready_count": citation_ready_count,
        "relation_types": relation_types,
        "focus_topic_relation_counts": topic_hits,
        "research_index_ok": research_index_ok,
        "pass": (
            variable_count > 0
            and len(evolutions) > 0
            and core_variable_coverage >= 0.6
            and quality_complete_rate == 1.0
            and research_index_ok
            and sum(1 for count in topic_hits.values() if count > 0) >= 5
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate research-platform outputs.")
    parser.add_argument("--self-check-only", action="store_true")
    args = parser.parse_args()

    report = evaluate()
    if not args.self_check_only:
        REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        append_log(
            action_type="研究平台验收",
            content="Evaluated policy variables, quality status, evolution chains and research exports.",
            files="05_输出成果/research_platform_evaluation.summary.json",
            command="python scripts/29_evaluate_research_platform.py",
            result=f"pass={report['pass']}; core_coverage={report['core_variable_coverage']}",
            note="Acceptance is rule-based and should be followed by manual sampling.",
        )
    if not report["pass"]:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
