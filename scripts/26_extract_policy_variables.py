"""Extract structured policy variables without using an LLM.

The output is intentionally conservative: every extracted value is based on
keyword hits from the title, metadata and local text chunks, and the quality
status remains machine-level unless existing review metadata says otherwise.
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
CHUNKS_CSV = META_DIR / "知识切片表.csv"
VARIABLES_CSV = META_DIR / "政策变量表.csv"
QUALITY_CSV = META_DIR / "政策质量状态表.csv"
SUMMARY_JSON = OUTPUT_DIR / "policy_variables.summary.json"

CSV_FIELDS = [
    "资料编号",
    "文件标题",
    "发布日期",
    "文号",
    "适用地区",
    "来源类型",
    "有效状态",
    "质量状态",
    "政策工具",
    "约束机制",
    "激励机制",
    "适用主体",
    "市场环节",
    "价格机制",
    "交易品种",
    "结算机制",
    "考核机制",
    "低碳目标",
    "规划场景",
    "投资影响",
    "商业模式影响",
    "风险约束",
    "证据摘录",
    "抽取方法",
    "人工复核状态",
    "更新时间",
]

QUALITY_FIELDS = [
    "资料编号",
    "文件标题",
    "质量状态",
    "状态原因",
    "是否可引用",
    "缺失字段",
    "人工复核状态",
    "更新时间",
]

TAXONOMY: dict[str, dict[str, list[str]]] = {
    "政策工具": {
        "命令控制": ["必须", "应当", "不得", "禁止", "监管", "考核", "处罚", "监管办法"],
        "市场机制": ["市场化", "交易", "竞价", "现货", "中长期", "价格形成", "绿电"],
        "规划引导": ["规划", "实施方案", "建设方案", "发展方案", "试点", "示范"],
        "财政金融": ["补贴", "补偿", "奖励", "基金", "资金", "财政", "金融"],
        "技术标准": ["技术规范", "标准", "并网标准", "计量", "信息系统"],
        "信息披露": ["信息披露", "信息报送", "公开", "披露", "公告"],
        "监管执法": ["监管", "稽查", "信用", "评价", "约谈", "处罚"],
    },
    "约束机制": {
        "准入条件": ["准入", "注册", "备案", "条件", "资质", "市场主体", "经营主体"],
        "并网约束": ["并网", "接入电网", "接网", "调度", "安全稳定", "电网承载"],
        "履约约束": ["履约", "合同", "偏差", "违约", "保证金", "信用"],
        "信息约束": ["信息报送", "信息披露", "公开", "数据", "计量"],
        "消纳约束": ["消纳", "弃风", "弃光", "承载能力", "调峰"],
    },
    "激励机制": {
        "补偿收益": ["补偿", "收益", "辅助服务费用", "容量补偿", "调峰补偿"],
        "价格激励": ["上网电价", "分时电价", "峰谷", "价格机制", "市场价格"],
        "绿色激励": ["绿电", "绿证", "可再生能源", "绿色低碳", "消纳责任"],
        "试点示范": ["试点", "示范", "优先", "鼓励", "支持"],
    },
    "适用主体": {
        "电网企业": ["电网企业", "供电企业", "输配电", "国家电网", "南方电网"],
        "发电企业": ["发电企业", "发电侧", "电源企业", "燃煤机组", "新能源发电"],
        "新能源企业": ["新能源企业", "风电", "光伏", "分布式光伏", "可再生能源"],
        "储能企业": ["储能", "新型储能", "独立储能", "抽水蓄能"],
        "售电公司": ["售电公司", "售电企业", "零售"],
        "电力用户": ["电力用户", "工商业用户", "用户侧", "负荷"],
        "交易机构": ["交易机构", "电力交易中心", "交易中心"],
        "虚拟电厂": ["虚拟电厂", "负荷聚合商", "聚合商", "需求响应"],
        "微电网主体": ["微电网", "增量配电网", "园区", "源网荷储"],
        "数据中心": ["数据中心", "算力", "算力负荷", "电力算力协同"],
    },
    "市场环节": {
        "发电": ["发电", "电源", "机组", "新能源发电"],
        "输配电": ["输配电", "电网", "配电网", "接入电网", "电网规划"],
        "售电": ["售电", "零售", "代理购电"],
        "用电": ["用电", "电力用户", "负荷", "需求响应"],
        "调度运行": ["调度", "运行", "并网运行", "安全稳定"],
        "辅助服务": ["辅助服务", "调峰", "调频", "备用", "爬坡", "黑启动"],
        "现货市场": ["现货", "日前", "实时", "出清"],
        "中长期市场": ["中长期", "年度交易", "月度交易", "合同电量"],
        "绿电绿证": ["绿电", "绿证", "绿色电力"],
    },
    "价格机制": {
        "上网电价": ["上网电价", "新能源上网", "燃煤基准价"],
        "输配电价": ["输配电价", "输配电", "准许收入", "监管周期"],
        "分时电价": ["分时电价", "峰谷", "尖峰", "谷段"],
        "市场形成价格": ["市场形成", "市场化价格", "竞价", "出清价格", "交易价格"],
        "容量价格": ["容量电价", "容量补偿", "容量市场", "容量费用"],
        "辅助服务价格": ["辅助服务费用", "补偿标准", "调频价格", "调峰价格"],
        "偏差价格": ["偏差", "偏差电量", "偏差考核", "偏差结算"],
    },
    "交易品种": {
        "中长期交易": ["中长期交易", "年度交易", "月度交易", "合同交易"],
        "现货交易": ["现货交易", "日前市场", "实时市场"],
        "绿电交易": ["绿电交易", "绿色电力交易", "绿证"],
        "辅助服务交易": ["辅助服务交易", "调峰", "调频", "备用"],
        "容量交易": ["容量市场", "容量补偿", "容量交易"],
        "需求响应": ["需求响应", "可调节负荷", "负荷聚合"],
        "跨省跨区交易": ["跨省", "跨区", "省间", "区域市场"],
    },
    "结算机制": {
        "电费结算": ["电费结算", "结算办法", "电费", "清算"],
        "偏差结算": ["偏差结算", "偏差电量", "偏差考核"],
        "辅助服务结算": ["辅助服务费用", "补偿分摊", "调频补偿", "调峰补偿"],
        "分摊机制": ["分摊", "承担", "费用分摊", "疏导"],
        "周期结算": ["日清月结", "月度结算", "年度清算", "滚动结算"],
    },
    "考核机制": {
        "运行考核": ["并网运行考核", "两个细则", "运行结果", "考核补偿"],
        "偏差考核": ["偏差考核", "偏差电量", "履约考核"],
        "信用评价": ["信用评价", "信用管理", "失信", "评价及评定"],
        "信息披露考核": ["信息披露", "信息报送", "报送责任"],
        "监管处罚": ["处罚", "约谈", "监管措施", "稽查"],
    },
    "低碳目标": {
        "双碳目标": ["碳达峰", "碳中和", "双碳"],
        "绿色低碳": ["绿色低碳", "绿色发展", "低碳转型"],
        "新能源消纳": ["新能源消纳", "可再生能源消纳", "弃风", "弃光"],
        "能源安全": ["能源安全", "电力保供", "安全保供"],
    },
    "规划场景": {
        "新型电力系统": ["新型电力系统", "现代能源体系"],
        "源网荷储": ["源网荷储", "一体化"],
        "微电网": ["微电网", "智能微电网"],
        "储能": ["储能", "新型储能", "抽水蓄能"],
        "电网规划": ["电网规划", "配电网", "坚强局部电网", "电网建设"],
        "电力算力协同": ["算力", "数据中心", "电力算力协同"],
        "分布式光伏": ["分布式光伏", "屋顶光伏", "农光互补"],
        "虚拟电厂": ["虚拟电厂", "负荷聚合"],
    },
    "投资影响": {
        "收益机制明确": ["收益", "补偿", "价格机制", "容量补偿", "辅助服务"],
        "成本疏导": ["成本", "疏导", "输配电价", "分摊", "电费"],
        "并网条件影响": ["并网", "接入", "消纳", "承载能力"],
        "市场风险暴露": ["市场化", "现货", "价格波动", "偏差"],
        "项目准入影响": ["申报", "备案", "准入", "条件", "试点"],
    },
    "商业模式影响": {
        "自发自用余电上网": ["自发自用", "余电上网", "分布式光伏"],
        "聚合交易": ["聚合", "虚拟电厂", "负荷聚合", "需求响应"],
        "绿电消费": ["绿电交易", "绿证", "绿色电力消费"],
        "辅助服务收益": ["辅助服务", "调频", "调峰", "备用", "补偿"],
        "园区微电网": ["园区", "微电网", "源网荷储", "增量配电"],
    },
    "风险约束": {
        "价格波动": ["价格波动", "现货价格", "市场风险", "出清价格"],
        "偏差风险": ["偏差", "偏差考核", "违约", "履约"],
        "并网消纳风险": ["并网", "消纳", "弃风", "弃光", "接入受限"],
        "政策状态风险": ["征求意见", "试行", "暂行", "废止", "失效"],
        "合规监管风险": ["信用", "监管", "处罚", "信息披露", "报送"],
    },
}

REQUIRED_CITATION_FIELDS = ["文件标题", "发布日期", "原文链接", "本地文件路径"]


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


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    return [item.strip() for item in re.split(r"(?<=[。；;])|\n+", text) if len(item.strip()) >= 16]


def collect_chunk_text(limit_per_doc: int = 26000) -> dict[str, str]:
    texts: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    if not CHUNKS_CSV.exists():
        return {}
    with CHUNKS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            doc_id = row.get("资料编号", "")
            if not doc_id:
                continue
            current = sizes.get(doc_id, 0)
            if current >= limit_per_doc:
                continue
            piece = row.get("章节标题", "") + " " + row.get("正文片段", "")
            piece = normalize_space(piece)
            if not piece:
                continue
            texts.setdefault(doc_id, []).append(piece)
            sizes[doc_id] = current + len(piece)
    return {doc_id: " ".join(items)[:limit_per_doc] for doc_id, items in texts.items()}


def joined(values: list[str]) -> str:
    return ";".join(dict.fromkeys(value for value in values if value))


def match_dimension(text: str, dimension: str) -> list[str]:
    hits: list[str] = []
    for label, keywords in TAXONOMY[dimension].items():
        if any(keyword in text for keyword in keywords):
            hits.append(label)
    return hits


def evidence_snippets(text: str, hit_values: list[str], limit: int = 3) -> str:
    keywords: list[str] = []
    for dimension in TAXONOMY.values():
        for label, terms in dimension.items():
            if label in hit_values:
                keywords.extend(terms)
    snippets: list[str] = []
    seen: set[str] = set()
    for sentence in split_sentences(text):
        if any(term in sentence for term in keywords):
            snippet = sentence[:180]
            key = snippet[:80]
            if key not in seen:
                snippets.append(snippet)
                seen.add(key)
        if len(snippets) >= limit:
            break
    return " | ".join(snippets)


def quality_status(row: dict[str, str]) -> tuple[str, str, str]:
    review = row.get("审核状态", "")
    source_type = row.get("来源类型", "")
    status = row.get("有效状态", "")
    missing = [field for field in REQUIRED_CITATION_FIELDS if not row.get(field, "").strip()]
    if "公众号" in source_type or "第三方" in source_type or "媒体" in source_type:
        return "需复核", "第三方或公众号资料默认不进入可引用状态", ";".join(missing)
    if missing:
        return "需复核", "缺少可引用必备字段", ";".join(missing)
    if "废止" in status or "失效" in status:
        return "需复核", "政策状态非现行有效，需要人工确认引用场景", ";".join(missing)
    if "待" in review or "需复核" in review or "待人工复核" in review:
        return "机器抽取", "由规则词典抽取，尚未人工校验", ";".join(missing)
    if any(flag in review for flag in ["可引用", "人工校验", "已复核", "通过"]):
        return "可引用", "已有人工复核或可引用标记", ";".join(missing)
    if "人工" in review:
        return "人工校验", "已有人工处理标记但未明确可引用", ";".join(missing)
    return "机器抽取", "由规则词典抽取，尚未人工校验", ";".join(missing)


def extract_variables(row: dict[str, str], chunk_text: str, timestamp: str) -> tuple[dict[str, str], dict[str, str]]:
    doc_id = row.get("资料编号", "")
    text = normalize_space(
        " ".join(
            [
                row.get("文件标题", ""),
                row.get("发布部门", ""),
                row.get("采集来源机构", ""),
                row.get("适用地区", ""),
                row.get("来源类型", ""),
                row.get("市场主题", ""),
                row.get("关键词", ""),
                row.get("摘要", ""),
                row.get("备注", ""),
                chunk_text,
            ]
        )
    )
    status, reason, missing = quality_status(row)
    extracted: dict[str, list[str]] = {dimension: match_dimension(text, dimension) for dimension in TAXONOMY}
    all_hits = [value for values in extracted.values() for value in values]

    variable_row = {
        "资料编号": doc_id,
        "文件标题": row.get("文件标题", ""),
        "发布日期": row.get("发布日期", ""),
        "文号": row.get("文号", ""),
        "适用地区": row.get("适用地区", ""),
        "来源类型": row.get("来源类型", ""),
        "有效状态": row.get("有效状态", ""),
        "质量状态": status,
        "证据摘录": evidence_snippets(text, all_hits),
        "抽取方法": "规则词典_v1",
        "人工复核状态": row.get("审核状态", "") or "待人工复核",
        "更新时间": timestamp,
    }
    for dimension in TAXONOMY:
        variable_row[dimension] = joined(extracted[dimension])

    quality_row = {
        "资料编号": doc_id,
        "文件标题": row.get("文件标题", ""),
        "质量状态": status,
        "状态原因": reason,
        "是否可引用": "是" if status == "可引用" else "否",
        "缺失字段": missing,
        "人工复核状态": row.get("审核状态", "") or "待人工复核",
        "更新时间": timestamp,
    }
    return variable_row, quality_row


def build_variables(limit: int | None = None) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, object]]:
    rows = read_csv(LEDGER_CSV)
    if limit:
        rows = rows[:limit]
    chunk_texts = collect_chunk_text()
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    variables: list[dict[str, str]] = []
    qualities: list[dict[str, str]] = []
    for row in rows:
        variable_row, quality_row = extract_variables(row, chunk_texts.get(row.get("资料编号", ""), ""), timestamp)
        variables.append(variable_row)
        qualities.append(quality_row)

    quality_counts: dict[str, int] = {}
    for row in variables:
        quality_counts[row["质量状态"]] = quality_counts.get(row["质量状态"], 0) + 1
    filled_core = sum(
        1
        for row in variables
        if row["政策工具"] and row["适用主体"] and (row["市场环节"] or row["规划场景"])
    )
    summary = {
        "generated_at": timestamp,
        "document_count": len(variables),
        "quality_counts": quality_counts,
        "core_variable_coverage": round(filled_core / len(variables), 4) if variables else 0,
        "output_csv": str(VARIABLES_CSV.relative_to(ROOT)),
        "quality_csv": str(QUALITY_CSV.relative_to(ROOT)),
    }
    return variables, qualities, summary


def self_check(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if not rows:
        errors.append("政策变量表为空")
        return errors
    for field in ["政策工具", "适用主体", "市场环节", "质量状态"]:
        filled = sum(1 for row in rows if row.get(field))
        if filled / len(rows) < 0.45:
            errors.append(f"{field} 覆盖率过低：{filled}/{len(rows)}")
    statuses = {row.get("质量状态", "") for row in rows}
    if not {"机器抽取", "需复核"} & statuses:
        errors.append("质量状态没有体现机器抽取或需复核")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract rule-based policy variables.")
    parser.add_argument("--limit", type=int, default=0, help="Only process first N ledger rows.")
    parser.add_argument("--self-check-only", action="store_true", help="Validate existing output.")
    args = parser.parse_args()

    if args.self_check_only:
        rows = read_csv(VARIABLES_CSV)
        errors = self_check(rows)
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print(f"政策变量自检通过：{len(rows)} 条")
        return 0

    variables, qualities, summary = build_variables(limit=args.limit or None)
    write_csv(VARIABLES_CSV, variables, CSV_FIELDS)
    write_csv(QUALITY_CSV, qualities, QUALITY_FIELDS)
    SUMMARY_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    append_log(
        action_type="政策变量抽取",
        content=f"Extracted rule-based policy variables for {summary['document_count']} policies.",
        files="02_元数据/政策变量表.csv;02_元数据/政策质量状态表.csv",
        command="python scripts/26_extract_policy_variables.py",
        result=f"core coverage={summary['core_variable_coverage']}",
        note="No LLM used; outputs remain machine-extracted unless review metadata says otherwise.",
    )
    errors = self_check(variables)
    if errors:
        print("政策变量自检失败：")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"已生成：{VARIABLES_CSV}")
    print(f"已生成：{QUALITY_CSV}")
    print(f"核心变量覆盖率：{summary['core_variable_coverage']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
