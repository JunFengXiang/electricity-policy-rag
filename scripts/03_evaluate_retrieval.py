from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUESTION_FILE = ROOT / "02_元数据" / "问题评测表.csv"
LEDGER_FILE = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"

QUESTION_FIELDS = [
    "评测编号",
    "问题",
    "问题类型",
    "地区",
    "市场主题",
    "时间范围",
    "检索关键词",
    "期望Top3结果",
    "标准依据文件",
    "标准答案要点",
    "权威性要求",
    "是否允许解读资料",
    "评测阶段",
    "人工评分_相关性",
    "人工评分_引用准确性",
    "人工评分_答案正确性",
    "问题状态",
    "备注",
]

LEDGER_FIELDS = [
    "资料编号",
    "文件标题",
    "发布部门",
    "采集来源机构",
    "发布日期",
    "文号",
    "适用地区",
    "来源类型",
    "政策层级",
    "市场主题",
    "关键词",
    "权威等级",
    "时间敏感类型",
    "有效状态",
    "是否原文",
    "原文链接",
    "本地文件路径",
    "关联官方文件",
    "摘要",
    "备注",
    "入库日期",
    "审核状态",
]

RESULT_FIELDS = [
    "评测编号",
    "问题",
    "标准依据文件",
    "Top1资料编号",
    "Top1标题",
    "Top1得分",
    "Top3标题",
    "Top3命中",
    "标准文件是否已入库",
    "说明",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    return re.sub(r"[\s《》〈〉“”\"'()（）\[\]【】,，。.;；:：、/\\_-]+", "", value)


def split_terms(*values: str) -> list[str]:
    raw = ";".join(v or "" for v in values)
    parts = re.split(r"[;；,，、\s]+", raw)
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = part.strip()
        if not term:
            continue
        key = normalize_text(term)
        if not key or key in seen:
            continue
        seen.add(key)
        terms.append(term)
    return terms


def title_matches(title: str, expected: str) -> bool:
    title_norm = normalize_text(title)
    for item in split_terms(expected):
        item_norm = normalize_text(item)
        if item_norm and (item_norm in title_norm or title_norm in item_norm):
            return True
    return False


def load_processed_text(row: dict[str, str], include_text: bool) -> str:
    if not include_text:
        return ""
    paths = row.get("本地文件路径", "")
    for raw_path in paths.split(";"):
        raw_path = raw_path.strip()
        if not raw_path or not raw_path.lower().endswith(".txt"):
            continue
        text_path = ROOT / raw_path
        if not text_path.exists():
            continue
        try:
            return text_path.read_text(encoding="utf-8", errors="ignore")[:80000]
        except OSError:
            return ""
    return ""


def document_text(row: dict[str, str], include_text: bool) -> str:
    fields = [
        "文件标题",
        "发布部门",
        "采集来源机构",
        "文号",
        "适用地区",
        "来源类型",
        "政策层级",
        "市场主题",
        "关键词",
        "权威等级",
        "有效状态",
        "摘要",
        "备注",
    ]
    base = "\n".join(row.get(field, "") for field in fields)
    body = load_processed_text(row, include_text)
    return f"{base}\n{body}"


def authority_score(row: dict[str, str]) -> float:
    authority = row.get("权威等级", "")
    source_type = row.get("来源类型", "")
    score = 0.0
    if "A" in authority:
        score += 4.0
    elif "B" in authority:
        score += 2.5
    elif "C" in authority:
        score += 1.0
    if "官方" in source_type or "监管" in source_type:
        score += 2.0
    elif "交易规则" in source_type:
        score += 1.5
    return score


def score_document(question: dict[str, str], doc: dict[str, str], include_text: bool) -> float:
    question_terms = split_terms(
        question.get("问题", ""),
        question.get("检索关键词", ""),
        question.get("地区", ""),
        question.get("市场主题", ""),
    )
    text = normalize_text(document_text(doc, include_text))
    title = normalize_text(doc.get("文件标题", ""))
    score = authority_score(doc)

    for term in question_terms:
        term_norm = normalize_text(term)
        if not term_norm:
            continue
        if term_norm in title:
            score += 8.0
        elif term_norm in text:
            score += 2.0

    region_terms = split_terms(question.get("地区", ""))
    region_text = normalize_text(doc.get("适用地区", ""))
    for term in region_terms:
        term_norm = normalize_text(term)
        if term_norm and (term_norm in region_text or region_text in term_norm):
            score += 5.0

    topic_terms = split_terms(question.get("市场主题", ""))
    topic_text = normalize_text(doc.get("市场主题", "") + ";" + doc.get("关键词", ""))
    for term in topic_terms:
        term_norm = normalize_text(term)
        if term_norm and term_norm in topic_text:
            score += 4.0

    if "现行有效" in doc.get("有效状态", ""):
        score += 2.0
    return score


def rank_documents(
    question: dict[str, str],
    docs: list[dict[str, str]],
    include_text: bool,
    top_k: int,
) -> list[tuple[float, dict[str, str]]]:
    region_terms = split_terms(question.get("地区", ""))
    should_filter_region = bool(region_terms) and not any(term == "全国" for term in region_terms)
    if should_filter_region:
        filtered_docs = [
            doc
            for doc in docs
            if any(
                normalize_text(term) in normalize_text(doc.get("适用地区", ""))
                or normalize_text(doc.get("适用地区", "")) in normalize_text(term)
                for term in region_terms
            )
        ]
        docs = filtered_docs or docs

    scored = [(score_document(question, doc, include_text), doc) for doc in docs]
    scored.sort(key=lambda item: (item[0], item[1].get("发布日期", "")), reverse=True)
    return scored[:top_k]


def check_headers(path: Path, expected_fields: list[str]) -> list[str]:
    rows = read_csv(path)
    if not rows:
        return [f"{path.name} 为空"]
    actual = list(rows[0].keys())
    missing = [field for field in expected_fields if field not in actual]
    extra = [field for field in actual if field not in expected_fields]
    errors = []
    if missing:
        errors.append(f"{path.name} 缺少字段：{'、'.join(missing)}")
    if extra:
        errors.append(f"{path.name} 存在未登记字段：{'、'.join(extra)}")
    return errors


def self_check(limit: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for path in [QUESTION_FILE, LEDGER_FILE]:
        if not path.exists():
            errors.append(f"缺少文件：{path}")

    if errors:
        return errors, warnings

    errors.extend(check_headers(QUESTION_FILE, QUESTION_FIELDS))
    errors.extend(check_headers(LEDGER_FILE, LEDGER_FIELDS))

    questions = read_csv(QUESTION_FILE)
    docs = read_csv(LEDGER_FILE)
    selected = questions[:limit]

    seen: set[str] = set()
    for row in questions:
        qid = row.get("评测编号", "")
        if qid in seen:
            errors.append(f"问题评测表存在重复评测编号：{qid}")
        seen.add(qid)

    for row in selected:
        qid = row.get("评测编号", "")
        if not row.get("标准依据文件"):
            errors.append(f"{qid} 缺少标准依据文件")
        if not row.get("标准答案要点"):
            errors.append(f"{qid} 缺少标准答案要点")
        if not row.get("期望Top3结果"):
            warnings.append(f"{qid} 缺少期望Top3结果")

    for row in selected:
        expected = row.get("标准依据文件", "")
        if expected and not any(title_matches(doc.get("文件标题", "") + ";" + doc.get("备注", ""), expected) for doc in docs):
            warnings.append(f"{row.get('评测编号')} 的标准依据文件暂未在台账中命中：{expected}")

    probe_question = {
        "问题": "山东电力市场规则试行文件",
        "检索关键词": "山东电力市场规则;试行",
        "地区": "山东",
        "市场主题": "电力市场;现货;中长期",
    }
    probe_docs = [
        {"文件标题": "山东电力市场规则(试行)", "适用地区": "山东", "市场主题": "电力市场;现货;中长期", "关键词": "山东;电力市场", "权威等级": "B", "来源类型": "监管规则", "有效状态": "现行有效", "发布日期": "2025-01-01"},
        {"文件标题": "湖北省电力中长期市场实施细则", "适用地区": "湖北", "市场主题": "中长期", "关键词": "湖北", "权威等级": "B", "来源类型": "监管规则", "有效状态": "现行有效", "发布日期": "2025-01-01"},
    ]
    probe_top = rank_documents(probe_question, probe_docs, include_text=False, top_k=1)[0][1]["文件标题"]
    if probe_top != "山东电力市场规则(试行)":
        errors.append("内置排序自检失败：山东规则未排在第一")

    return errors, warnings


def evaluate(limit: int, top_k: int, include_text: bool) -> tuple[Path, list[dict[str, str]]]:
    questions = read_csv(QUESTION_FILE)[:limit]
    docs = read_csv(LEDGER_FILE)
    rows: list[dict[str, str]] = []

    for question in questions:
        ranked = rank_documents(question, docs, include_text=include_text, top_k=top_k)
        top_docs = [doc for _, doc in ranked]
        expected = question.get("标准依据文件", "")
        standard_in_ledger = any(
            title_matches(doc.get("文件标题", "") + ";" + doc.get("备注", ""), expected)
            for doc in docs
        )
        hit_top3 = any(
            title_matches(doc.get("文件标题", "") + ";" + doc.get("备注", ""), expected)
            for doc in top_docs[:3]
        )
        top1_score = f"{ranked[0][0]:.2f}" if ranked else ""
        rows.append(
            {
                "评测编号": question.get("评测编号", ""),
                "问题": question.get("问题", ""),
                "标准依据文件": expected,
                "Top1资料编号": top_docs[0].get("资料编号", "") if top_docs else "",
                "Top1标题": top_docs[0].get("文件标题", "") if top_docs else "",
                "Top1得分": top1_score,
                "Top3标题": " | ".join(doc.get("文件标题", "") for doc in top_docs[:3]),
                "Top3命中": "是" if hit_top3 else "否",
                "标准文件是否已入库": "是" if standard_in_ledger else "否",
                "说明": "" if hit_top3 else "需检查检索词、标题抽取或补充标准文件",
            }
        )

    today = dt.datetime.now().strftime("%Y%m%d")
    output_path = OUTPUT_DIR / f"检索评测结果_{today}.csv"
    write_csv(output_path, rows, RESULT_FIELDS)
    return output_path, rows


def print_check(errors: list[str], warnings: list[str]) -> None:
    if errors:
        print("自检错误：")
        for item in errors:
            print(f"- {item}")
    if warnings:
        print("自检提醒：")
        for item in warnings:
            print(f"- {item}")
    if not errors and not warnings:
        print("自检通过：未发现错误或提醒")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval Top3 hit rate for the policy knowledge base.")
    parser.add_argument("--limit", type=int, default=10, help="Number of questions to evaluate from the top of the table.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of retrieval results to keep for each question.")
    parser.add_argument("--no-text", action="store_true", help="Only use metadata fields, do not read processed text.")
    parser.add_argument("--self-check-only", action="store_true", help="Run checks without writing evaluation output.")
    args = parser.parse_args()

    errors, warnings = self_check(limit=args.limit)
    print_check(errors, warnings)
    if errors:
        return 1
    if args.self_check_only:
        return 0

    output_path, rows = evaluate(limit=args.limit, top_k=args.top_k, include_text=not args.no_text)
    total = len(rows)
    hits = sum(1 for row in rows if row["Top3命中"] == "是")
    in_ledger = sum(1 for row in rows if row["标准文件是否已入库"] == "是")
    hit_rate = hits / total if total else 0
    print(f"评测完成：{total} 个问题")
    print(f"标准文件已入库：{in_ledger}/{total}")
    print(f"Top3命中：{hits}/{total} ({hit_rate:.0%})")
    print(f"结果文件：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
