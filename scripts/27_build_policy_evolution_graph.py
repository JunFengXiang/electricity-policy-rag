"""Build a policy evolution graph for research use.

The graph combines existing explicit citation relations with conservative
metadata heuristics for revision, consultation-to-final, local implementation
and same-topic continuation chains.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
RELATION_CSV = META_DIR / "政策关联关系表.csv"
EVOLUTION_CSV = META_DIR / "政策演化关系表.csv"
GRAPH_JSON = OUTPUT_DIR / "policy_evolution_graph.json"

FIELDS = [
    "关系编号",
    "源资料编号",
    "源标题",
    "源发布日期",
    "目标资料编号",
    "目标标题",
    "目标发布日期",
    "关系类型",
    "主题链条",
    "区域路径",
    "匹配依据",
    "证据文本",
    "置信度",
    "是否人工确认",
    "备注",
]

FOCUS_TOPICS = [
    "电力市场",
    "辅助服务",
    "绿电",
    "新能源上网电价",
    "储能",
    "微电网",
    "新型电力系统",
    "电网规划",
    "输配电价",
    "源网荷储",
]

RELATION_TYPES = {
    "上位法",
    "配套文件",
    "修订替代",
    "地方承接",
    "试行转正式",
    "征求意见到发布稿",
    "引用依据",
    "同主题延续",
}

NATIONAL_ORGS = ["国家发展改革委", "国家能源局", "国务院", "财政部", "市场监管总局"]
REGIONAL_TERMS = ["区域", "南方", "华中", "华北", "东北", "西北", "华东"]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def normalize(value: str) -> str:
    value = (value or "").lower()
    value = re.sub(r"（[^）]*(征求意见稿|试行|暂行|修订版|全文)[^）]*）", "", value)
    value = re.sub(r"\([^)]*(征求意见稿|试行|暂行|修订版|全文)[^)]*\)", "", value)
    value = re.sub(r"[《》〈〉“”\"'()（）\[\]【】,，。.;；:：、/\\_\s-]+", "", value)
    for token in ["关于印发", "的通知", "通知", "实施细则", "管理办法", "交易规则", "基本规则"]:
        value = value.replace(token, "")
    return value


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;；,，、]+", value or "") if item.strip()]


def topic_chain(row: dict[str, str]) -> str:
    blob = " ".join([row.get("文件标题", ""), row.get("市场主题", ""), row.get("关键词", ""), row.get("备注", "")])
    hits = [topic for topic in FOCUS_TOPICS if topic in blob]
    return ";".join(hits[:3]) or (split_values(row.get("市场主题", "")) or ["综合"])[0]


def region_path(source: dict[str, str], target: dict[str, str]) -> str:
    a = source.get("适用地区", "") or "未标"
    b = target.get("适用地区", "") or "未标"
    return f"{a}->{b}"


def is_national(row: dict[str, str]) -> bool:
    blob = " ".join([row.get("政策层级", ""), row.get("适用地区", ""), row.get("发布部门", ""), row.get("采集来源机构", "")])
    return "国家级" in blob or "全国" in blob or any(org in blob for org in NATIONAL_ORGS)


def is_local_or_regional(row: dict[str, str]) -> bool:
    blob = " ".join([row.get("政策层级", ""), row.get("适用地区", ""), row.get("文件标题", ""), row.get("发布部门", "")])
    if any(term in blob for term in REGIONAL_TERMS):
        return True
    return not is_national(row)


def add_relation(
    rows: list[dict[str, str]],
    seen: set[tuple[str, str, str]],
    source: dict[str, str],
    target: dict[str, str],
    relation_type: str,
    basis: str,
    evidence: str,
    confidence: str,
    note: str = "",
) -> None:
    if source.get("资料编号") == target.get("资料编号"):
        return
    if relation_type not in RELATION_TYPES:
        relation_type = "同主题延续"
    key = (source.get("资料编号", ""), target.get("资料编号", ""), relation_type)
    if key in seen:
        return
    seen.add(key)
    rows.append(
        {
            "关系编号": f"EVO-{len(rows) + 1:05d}",
            "源资料编号": source.get("资料编号", ""),
            "源标题": source.get("文件标题", ""),
            "源发布日期": source.get("发布日期", ""),
            "目标资料编号": target.get("资料编号", ""),
            "目标标题": target.get("文件标题", ""),
            "目标发布日期": target.get("发布日期", ""),
            "关系类型": relation_type,
            "主题链条": topic_chain(source) if topic_chain(source) != "综合" else topic_chain(target),
            "区域路径": region_path(source, target),
            "匹配依据": basis,
            "证据文本": evidence[:260],
            "置信度": confidence,
            "是否人工确认": "否",
            "备注": note,
        }
    )


def ledger_by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("资料编号", ""): row for row in rows if row.get("资料编号")}


def existing_relations(ledger: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in read_csv(RELATION_CSV):
        source = ledger.get(row.get("新政策资料编号", ""))
        target = ledger.get(row.get("旧政策资料编号", ""))
        if not source or not target:
            continue
        relation_type = row.get("关联类型", "") or "引用依据"
        if relation_type not in RELATION_TYPES:
            relation_type = "引用依据"
        add_relation(
            output,
            seen,
            source,
            target,
            relation_type,
            row.get("匹配依据", ""),
            row.get("证据文本", ""),
            row.get("置信度", "") or "中",
            row.get("备注", ""),
        )
    return output


def build_heuristic_relations(ledger_rows: list[dict[str, str]], base_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output = list(base_rows)
    seen = {(row["源资料编号"], row["目标资料编号"], row["关系类型"]) for row in output}
    rows = sorted(ledger_rows, key=lambda row: row.get("发布日期", ""))

    title_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = normalize(row.get("文件标题", ""))
        if len(key) >= 8:
            title_groups[key].append(row)

    for group in title_groups.values():
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda row: row.get("发布日期", ""))
        for earlier, later in zip(group, group[1:]):
            earlier_title = earlier.get("文件标题", "")
            later_title = later.get("文件标题", "")
            if "征求意见" in earlier_title and "征求意见" not in later_title:
                relation = "征求意见到发布稿"
            elif "试行" in earlier_title and "试行" not in later_title:
                relation = "试行转正式"
            elif "修订" in later_title or "补充" in later_title:
                relation = "修订替代"
            else:
                relation = "同主题延续"
            add_relation(output, seen, later, earlier, relation, "标题规范化后高度一致", earlier_title, "中")

    by_topic: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        chain = topic_chain(row)
        if chain != "综合":
            by_topic[chain.split(";")[0]].append(row)

    for topic, group in by_topic.items():
        national = [row for row in group if is_national(row)]
        local = [row for row in group if is_local_or_regional(row)]
        for child in local[:120]:
            child_date = parse_date(child.get("发布日期", ""))
            candidates = []
            for parent in national:
                parent_date = parse_date(parent.get("发布日期", ""))
                if parent.get("资料编号") == child.get("资料编号"):
                    continue
                if parent_date and child_date and parent_date > child_date:
                    continue
                title_blob = normalize(parent.get("文件标题", "") + child.get("文件标题", ""))
                score = 0
                for token in split_values(parent.get("市场主题", "")) + [topic]:
                    if token and token in child.get("市场主题", "") + child.get("文件标题", ""):
                        score += 1
                if "基本规则" in parent.get("文件标题", "") and ("实施细则" in child.get("文件标题", "") or "交易规则" in child.get("文件标题", "")):
                    score += 2
                if score > 0:
                    candidates.append((score, parent_date or dt.date.min, parent, title_blob))
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            for score, _, parent, _ in candidates[:2]:
                relation = "配套文件" if "实施细则" in child.get("文件标题", "") else "地方承接"
                add_relation(
                    output,
                    seen,
                    child,
                    parent,
                    relation,
                    f"主题链条：{topic}；国家/区域/地方层级承接",
                    f"{child.get('文件标题', '')} 与 {parent.get('文件标题', '')} 属于同一研究链条",
                    "中" if score >= 2 else "低",
                )

    for topic, group in by_topic.items():
        group = sorted(group, key=lambda row: row.get("发布日期", ""))
        for earlier, later in zip(group, group[1:]):
            if topic not in later.get("文件标题", "") and topic not in later.get("市场主题", ""):
                continue
            if earlier.get("适用地区", "") != later.get("适用地区", ""):
                continue
            add_relation(
                output,
                seen,
                later,
                earlier,
                "同主题延续",
                f"同地区同主题时间延续：{topic}",
                later.get("文件标题", ""),
                "低",
            )
    return output


def build_graph(rows: list[dict[str, str]]) -> dict[str, object]:
    chains: dict[str, dict[str, object]] = {}
    for row in rows:
        topic = (row.get("主题链条", "") or "综合").split(";")[0]
        chain = chains.setdefault(topic, {"nodes": {}, "edges": []})
        nodes: dict[str, dict[str, str]] = chain["nodes"]  # type: ignore[assignment]
        for prefix in ["源", "目标"]:
            doc_id = row[f"{prefix}资料编号"]
            nodes.setdefault(
                doc_id,
                {
                    "doc_id": doc_id,
                    "title": row[f"{prefix}标题"],
                    "publish_date": row[f"{prefix}发布日期"],
                },
            )
        chain["edges"].append(
            {
                "source": row["源资料编号"],
                "target": row["目标资料编号"],
                "relation_type": row["关系类型"],
                "confidence": row["置信度"],
                "region_path": row["区域路径"],
            }
        )
    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "relation_count": len(rows),
        "chains": {topic: {"nodes": list(value["nodes"].values()), "edges": value["edges"]} for topic, value in chains.items()},
    }


def build_evolution(limit: int | None = None) -> tuple[list[dict[str, str]], dict[str, object]]:
    ledger_rows = read_csv(LEDGER_CSV)
    if limit:
        ledger_rows = ledger_rows[:limit]
    ledger = ledger_by_id(ledger_rows)
    base = existing_relations(ledger)
    rows = build_heuristic_relations(ledger_rows, base)
    graph = build_graph(rows)
    return rows, graph


def self_check(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if not rows:
        return ["政策演化关系表为空"]
    relation_types = {row.get("关系类型", "") for row in rows}
    required = {"引用依据", "地方承接", "同主题延续"}
    missing = required - relation_types
    if missing:
        errors.append(f"缺少核心关系类型：{';'.join(sorted(missing))}")
    focus_hits = {topic for row in rows for topic in FOCUS_TOPICS if topic in row.get("主题链条", "") or topic in row.get("源标题", "")}
    if len(focus_hits) < 5:
        errors.append("重点主题链条覆盖不足")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build policy evolution relation table and graph JSON.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--self-check-only", action="store_true")
    args = parser.parse_args()

    if args.self_check_only:
        rows = read_csv(EVOLUTION_CSV)
        errors = self_check(rows)
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print(f"政策演化自检通过：{len(rows)} 条关系")
        return 0

    rows, graph = build_evolution(limit=args.limit or None)
    write_csv(EVOLUTION_CSV, rows)
    GRAPH_JSON.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    append_log(
        action_type="政策演化链生成",
        content=f"Built policy evolution graph with {len(rows)} relations.",
        files="02_元数据/政策演化关系表.csv;05_输出成果/policy_evolution_graph.json",
        command="python scripts/27_build_policy_evolution_graph.py",
        result=f"relation_count={len(rows)}",
        note="Relation confidence is heuristic unless explicitly confirmed.",
    )
    errors = self_check(rows)
    if errors:
        print("政策演化自检失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"已生成：{EVOLUTION_CSV}")
    print(f"已生成：{GRAPH_JSON}")
    print(f"关系数：{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
