"""Generate research-oriented exports from policy variables and evolution data."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
VARIABLES_CSV = META_DIR / "政策变量表.csv"
EVOLUTION_CSV = META_DIR / "政策演化关系表.csv"
RESEARCH_INDEX_JSON = OUTPUT_DIR / "research_platform_index.json"
TOOL_TABLE_CSV = OUTPUT_DIR / "政策工具分类表.csv"
REGION_COMPARE_CSV = OUTPUT_DIR / "区域比较表.csv"
TREND_CSV = OUTPUT_DIR / "政策强度时间趋势.csv"
CITATION_CSV = OUTPUT_DIR / "引用清单.csv"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def split_values(value: str) -> list[str]:
    return [item.strip() for item in (value or "").replace("；", ";").replace("、", ";").replace(",", ";").split(";") if item.strip()]


def year_of(value: str) -> str:
    return (value or "")[:4] if len(value or "") >= 4 else "未标"


def build_policy_tool_table(variables: list[dict[str, str]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in variables:
        tools = split_values(row.get("政策工具", "")) or ["未抽取"]
        for tool in tools:
            rows.append(
                {
                    "政策工具类型": tool,
                    "资料编号": row.get("资料编号", ""),
                    "文件标题": row.get("文件标题", ""),
                    "发布日期": row.get("发布日期", ""),
                    "文号": row.get("文号", ""),
                    "适用地区": row.get("适用地区", ""),
                    "市场环节": row.get("市场环节", ""),
                    "价格机制": row.get("价格机制", ""),
                    "质量状态": row.get("质量状态", ""),
                    "证据摘录": row.get("证据摘录", ""),
                }
            )
    return rows


def build_region_compare(variables: list[dict[str, str]]) -> list[dict[str, str]]:
    bucket: dict[str, dict[str, Counter[str]]] = defaultdict(lambda: defaultdict(Counter))
    counts: Counter[str] = Counter()
    for row in variables:
        regions = split_values(row.get("适用地区", "")) or ["未标"]
        for region in regions:
            counts[region] += 1
            for field in ["政策工具", "适用主体", "市场环节", "价格机制", "交易品种", "规划场景", "风险约束"]:
                for value in split_values(row.get(field, "")):
                    bucket[region][field][value] += 1
    output: list[dict[str, str]] = []
    for region, total in counts.most_common():
        item = {"区域": region, "政策数量": str(total)}
        for field in ["政策工具", "适用主体", "市场环节", "价格机制", "交易品种", "规划场景", "风险约束"]:
            item[field] = ";".join(f"{name}({count})" for name, count in bucket[region][field].most_common(6))
        output.append(item)
    return output


def build_trend(variables: list[dict[str, str]]) -> list[dict[str, str]]:
    bucket: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in variables:
        year = year_of(row.get("发布日期", ""))
        topics = split_values(row.get("规划场景", "")) + split_values(row.get("市场环节", ""))
        if not topics:
            topics = ["综合"]
        for topic in topics[:4]:
            bucket[(year, topic)]["政策数量"] += 1
            for tool in split_values(row.get("政策工具", "")):
                bucket[(year, topic)][tool] += 1
    rows: list[dict[str, str]] = []
    for (year, topic), counter in sorted(bucket.items()):
        rows.append(
            {
                "年份": year,
                "主题": topic,
                "政策数量": str(counter["政策数量"]),
                "主要政策工具": ";".join(f"{name}({count})" for name, count in counter.most_common(6) if name != "政策数量"),
                "政策强度分": str(counter["政策数量"] + sum(count for name, count in counter.items() if name != "政策数量")),
            }
        )
    return rows


def build_citations(ledger_rows: list[dict[str, str]], variables: list[dict[str, str]]) -> list[dict[str, str]]:
    var_by_id = {row.get("资料编号", ""): row for row in variables}
    rows: list[dict[str, str]] = []
    for row in ledger_rows:
        doc_id = row.get("资料编号", "")
        variable = var_by_id.get(doc_id, {})
        rows.append(
            {
                "资料编号": doc_id,
                "文件标题": row.get("文件标题", ""),
                "发布部门": row.get("发布部门", ""),
                "发布日期": row.get("发布日期", ""),
                "文号": row.get("文号", ""),
                "来源类型": row.get("来源类型", ""),
                "有效状态": row.get("有效状态", ""),
                "质量状态": variable.get("质量状态", "未生成"),
                "原文链接": row.get("原文链接", ""),
                "本地文件路径": row.get("本地文件路径", ""),
                "建议引用格式": f"{row.get('发布部门', '')}：《{row.get('文件标题', '')}》，{row.get('发布日期', '')}，{row.get('文号', '')}。",
            }
        )
    return rows


def build_index(
    ledger_rows: list[dict[str, str]],
    variables: list[dict[str, str]],
    evolutions: list[dict[str, str]],
    generated_at: str,
) -> dict[str, object]:
    quality_counts = Counter(row.get("质量状态", "未标") for row in variables)
    tool_counts = Counter(value for row in variables for value in split_values(row.get("政策工具", "")))
    scenario_counts = Counter(value for row in variables for value in split_values(row.get("规划场景", "")))
    relation_counts = Counter(row.get("关系类型", "未标") for row in evolutions)
    return {
        "generated_at": generated_at,
        "document_count": len(ledger_rows),
        "variable_count": len(variables),
        "evolution_relation_count": len(evolutions),
        "quality_counts": dict(quality_counts),
        "top_policy_tools": dict(tool_counts.most_common(12)),
        "top_planning_scenarios": dict(scenario_counts.most_common(12)),
        "relation_type_counts": dict(relation_counts),
        "exports": {
            "政策工具分类表": str(TOOL_TABLE_CSV.relative_to(ROOT)),
            "区域比较表": str(REGION_COMPARE_CSV.relative_to(ROOT)),
            "政策强度时间趋势": str(TREND_CSV.relative_to(ROOT)),
            "引用清单": str(CITATION_CSV.relative_to(ROOT)),
        },
    }


def generate_outputs() -> dict[str, object]:
    ledger_rows = read_csv(LEDGER_CSV)
    variables = read_csv(VARIABLES_CSV)
    evolutions = read_csv(EVOLUTION_CSV)
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    tool_rows = build_policy_tool_table(variables)
    compare_rows = build_region_compare(variables)
    trend_rows = build_trend(variables)
    citation_rows = build_citations(ledger_rows, variables)
    index = build_index(ledger_rows, variables, evolutions, generated_at)

    write_csv(
        TOOL_TABLE_CSV,
        tool_rows,
        ["政策工具类型", "资料编号", "文件标题", "发布日期", "文号", "适用地区", "市场环节", "价格机制", "质量状态", "证据摘录"],
    )
    write_csv(
        REGION_COMPARE_CSV,
        compare_rows,
        ["区域", "政策数量", "政策工具", "适用主体", "市场环节", "价格机制", "交易品种", "规划场景", "风险约束"],
    )
    write_csv(TREND_CSV, trend_rows, ["年份", "主题", "政策数量", "主要政策工具", "政策强度分"])
    write_csv(
        CITATION_CSV,
        citation_rows,
        ["资料编号", "文件标题", "发布部门", "发布日期", "文号", "来源类型", "有效状态", "质量状态", "原文链接", "本地文件路径", "建议引用格式"],
    )
    RESEARCH_INDEX_JSON.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    return index


def self_check(index: dict[str, object] | None = None) -> list[str]:
    errors: list[str] = []
    if index is None:
        if not RESEARCH_INDEX_JSON.exists():
            return ["研究平台索引不存在"]
        index = json.loads(RESEARCH_INDEX_JSON.read_text(encoding="utf-8"))
    if int(index.get("variable_count", 0)) <= 0:
        errors.append("政策变量数量为 0")
    if int(index.get("evolution_relation_count", 0)) <= 0:
        errors.append("政策演化关系数量为 0")
    for path in [TOOL_TABLE_CSV, REGION_COMPARE_CSV, TREND_CSV, CITATION_CSV]:
        if not path.exists() or path.stat().st_size < 200:
            errors.append(f"研究导出文件异常：{path}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate research-oriented CSV/JSON outputs.")
    parser.add_argument("--self-check-only", action="store_true")
    args = parser.parse_args()

    if args.self_check_only:
        errors = self_check()
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print("研究平台导出自检通过")
        return 0

    index = generate_outputs()
    append_log(
        action_type="研究导出生成",
        content="Generated research exports for policy tools, regional comparison, trend and citations.",
        files="05_输出成果/research_platform_index.json;05_输出成果/政策工具分类表.csv;05_输出成果/区域比较表.csv;05_输出成果/政策强度时间趋势.csv;05_输出成果/引用清单.csv",
        command="python scripts/28_generate_research_outputs.py",
        result=f"documents={index.get('document_count')}; variables={index.get('variable_count')}",
        note="Exports are derived from official ledger and rule-based policy variables.",
    )
    errors = self_check(index)
    if errors:
        print("研究平台导出自检失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"已生成研究平台索引：{RESEARCH_INDEX_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
