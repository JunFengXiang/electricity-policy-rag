"""评测无大模型 RAG v2 的答案质量。

与 03_evaluate_retrieval.py 只看检索命中不同，本脚本检查结构化答案、引用字段、
标准依据文件命中、地区跑偏和重复资料比例。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
QUESTION_FILE = ROOT / "02_元数据" / "问题评测表.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
RAG_SCRIPT = ROOT / "scripts" / "18_rag_query.py"

RESULT_FIELDS = [
    "评测编号",
    "问题",
    "地区",
    "标准依据文件",
    "置信度",
    "Top3标准文件命中",
    "命中排名",
    "引用字段完整",
    "答案结构完整",
    "标准要点命中率",
    "地区跑偏",
    "返回切片数",
    "返回资料数",
    "重复资料比例",
    "通过",
    "说明",
]

REQUIRED_SECTIONS = ["direct_conclusion", "applicable_scope", "rule_points", "cautions", "citations"]


def load_rag_module():
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("rag_query_module", RAG_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载 RAG 脚本：{RAG_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag = load_rag_module()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    return re.sub(r"[\s《》〈〉“”\"'()（）\[\]【】,，。.;；:：、/\\_-]+", "", value)


def split_terms(*values: str) -> list[str]:
    raw = ";".join(value or "" for value in values)
    parts = re.split(r"[;；,，、\s]+", raw)
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        term = part.strip()
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


def expected_titles(question: dict[str, str]) -> str:
    return ";".join(
        value
        for value in [
            question.get("标准依据文件", ""),
            question.get("期望Top3结果", ""),
        ]
        if value
    )


def keypoint_terms(question: dict[str, str]) -> list[str]:
    raw_terms = split_terms(
        question.get("标准答案要点", ""),
        question.get("检索关键词", ""),
        question.get("市场主题", ""),
    )
    terms: list[str] = []
    for term in raw_terms:
        if len(normalize_text(term)) < 2:
            continue
        if term in {"应", "和", "及", "等", "围绕", "说明", "覆盖", "优先", "引用", "定位"}:
            continue
        terms.append(term)
    return terms[:12]


def section_text(response: dict[str, Any]) -> str:
    sections = response.get("answer_sections", {})
    return json.dumps(sections, ensure_ascii=False) + "\n" + response.get("answer", "")


def keypoint_hit_rate(question: dict[str, str], response: dict[str, Any]) -> float:
    terms = keypoint_terms(question)
    if not terms:
        return 1.0
    text = normalize_text(section_text(response))
    hits = sum(1 for term in terms if normalize_text(term) in text)
    return hits / len(terms)


def citation_fields_complete(response: dict[str, Any]) -> bool:
    citations = response.get("citations", [])
    if not citations:
        return False
    required = ["doc_id", "title", "publish_date", "document_number", "url"]
    for citation in citations:
        for field in required:
            value = str(citation.get(field, "")).strip()
            if not value:
                return False
    return True


def answer_sections_complete(response: dict[str, Any]) -> bool:
    sections = response.get("answer_sections", {})
    return all(key in sections for key in REQUIRED_SECTIONS)


def hit_rank(results: list[dict[str, Any]], expected: str, top_n: int) -> int:
    for index, item in enumerate(results[:top_n], start=1):
        title = item["row"].get("文件标题", "")
        if title_matches(title, expected):
            return index
    return 0


def has_region_drift(results: list[dict[str, Any]], region: str) -> bool:
    region = (region or "").strip()
    if not region or region == "全国":
        return False
    for item in results[:3]:
        if rag.region_match_kind(item["row"], region) == "miss":
            return True
    return False


def evaluate_question(
    payload: dict[str, Any],
    question: dict[str, str],
    top_k: int,
    candidate_pool: int,
    per_doc_limit: int,
) -> dict[str, Any]:
    allow_interpretation = "可" in question.get("是否允许解读资料", "")
    results, diagnostics = rag.search_with_diagnostics(
        payload,
        query=question.get("问题", ""),
        top_k=top_k,
        candidate_pool=candidate_pool,
        region=question.get("地区", ""),
        source_type="",
        official_only=not allow_interpretation,
        per_doc_limit=per_doc_limit,
    )
    response = rag.build_no_llm_response(question.get("问题", ""), results, diagnostics)
    expected = expected_titles(question)
    rank = hit_rank(results, expected, top_n=3)
    citation_ok = citation_fields_complete(response)
    sections_ok = answer_sections_complete(response)
    key_rate = keypoint_hit_rate(question, response)
    drift = has_region_drift(results, question.get("地区", ""))
    stats = diagnostics.get("dedup_stats", {})
    passed = bool(rank and citation_ok and sections_ok and key_rate >= 0.25 and not drift and response["confidence"] != "low")
    notes: list[str] = []
    if not rank:
        notes.append("Top3未命中标准依据")
    if not citation_ok:
        notes.append("引用字段不完整")
    if not sections_ok:
        notes.append("答案结构不完整")
    if key_rate < 0.25:
        notes.append("标准要点命中过低")
    if drift:
        notes.append("地区跑偏")
    if response["confidence"] == "low":
        notes.append("低置信")

    return {
        "评测编号": question.get("评测编号", ""),
        "问题": question.get("问题", ""),
        "地区": question.get("地区", ""),
        "标准依据文件": question.get("标准依据文件", ""),
        "置信度": response["confidence"],
        "Top3标准文件命中": "是" if rank else "否",
        "命中排名": str(rank) if rank else "",
        "引用字段完整": "是" if citation_ok else "否",
        "答案结构完整": "是" if sections_ok else "否",
        "标准要点命中率": f"{key_rate:.0%}",
        "地区跑偏": "是" if drift else "否",
        "返回切片数": str(stats.get("returned_chunk_count", len(results))),
        "返回资料数": str(stats.get("returned_doc_count", len({item["row"].get("资料编号", "") for item in results}))),
        "重复资料比例": f"{float(stats.get('duplicate_ratio', 0.0)):.0%}",
        "通过": "是" if passed else "否",
        "说明": "；".join(notes),
    }


def self_check(limit: int) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not QUESTION_FILE.exists():
        errors.append(f"缺少问题评测表：{QUESTION_FILE}")
        return errors, warnings
    if not (OUTPUT_DIR / "rag_index.pkl.gz").exists():
        errors.append("缺少 rag_index.pkl.gz")
    questions = read_csv(QUESTION_FILE)
    if len(questions) < 50:
        errors.append(f"问题评测表仅 {len(questions)} 题，未达到 50 题下限")
    qids = [row.get("评测编号", "") for row in questions]
    duplicates = sorted({qid for qid in qids if qids.count(qid) > 1})
    if duplicates:
        errors.append(f"评测编号重复：{'、'.join(duplicates)}")
    for row in questions[:limit]:
        if not row.get("标准依据文件"):
            errors.append(f"{row.get('评测编号')} 缺少标准依据文件")
        if not row.get("标准答案要点"):
            errors.append(f"{row.get('评测编号')} 缺少标准答案要点")
    if limit < len(questions):
        warnings.append(f"本次只评测前 {limit} 题，共 {len(questions)} 题")
    return errors, warnings


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


def evaluate(limit: int, top_k: int, candidate_pool: int, per_doc_limit: int) -> tuple[Path, Path, list[dict[str, Any]], dict[str, Any]]:
    payload = rag.load_index(OUTPUT_DIR / "rag_index.pkl.gz")
    questions = read_csv(QUESTION_FILE)[:limit]
    rows = [evaluate_question(payload, question, top_k, candidate_pool, per_doc_limit) for question in questions]
    today = dt.datetime.now().strftime("%Y%m%d")
    output_path = OUTPUT_DIR / f"RAG答案评测结果_{today}.csv"
    summary_path = OUTPUT_DIR / f"RAG答案评测结果_{today}.summary.json"
    write_csv(output_path, rows, RESULT_FIELDS)

    total = len(rows)
    summary = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "question_count": total,
        "top3_standard_hit_rate": sum(1 for row in rows if row["Top3标准文件命中"] == "是") / total if total else 0,
        "citation_complete_rate": sum(1 for row in rows if row["引用字段完整"] == "是") / total if total else 0,
        "answer_section_complete_rate": sum(1 for row in rows if row["答案结构完整"] == "是") / total if total else 0,
        "region_drift_count": sum(1 for row in rows if row["地区跑偏"] == "是"),
        "pass_rate": sum(1 for row in rows if row["通过"] == "是") / total if total else 0,
        "target": {
            "top3_standard_hit_rate": 0.90,
            "citation_complete_rate": 1.00,
            "region_drift_count": 0,
        },
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path, summary_path, rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate no-LLM RAG v2 answer quality.")
    parser.add_argument("--limit", type=int, default=50, help="Number of questions to evaluate.")
    parser.add_argument("--top-k", type=int, default=8, help="Returned chunks per question.")
    parser.add_argument("--candidate-pool", type=int, default=800, help="Initial retrieval candidates.")
    parser.add_argument("--per-doc-limit", type=int, default=2, help="Maximum returned chunks per document.")
    parser.add_argument("--self-check-only", action="store_true", help="Check inputs without writing reports.")
    args = parser.parse_args()

    errors, warnings = self_check(limit=args.limit)
    print_check(errors, warnings)
    if errors:
        return 1
    if args.self_check_only:
        return 0

    output_path, summary_path, rows, summary = evaluate(args.limit, args.top_k, args.candidate_pool, args.per_doc_limit)
    total = len(rows)
    print(f"RAG答案评测完成：{total} 个问题")
    print(f"Top3标准文件命中率：{summary['top3_standard_hit_rate']:.0%}")
    print(f"引用字段完整率：{summary['citation_complete_rate']:.0%}")
    print(f"地区跑偏数量：{summary['region_drift_count']}")
    print(f"通过率：{summary['pass_rate']:.0%}")
    print(f"结果文件：{output_path}")
    print(f"摘要文件：{summary_path}")

    append_log(
        action_type="RAG答案评测",
        content=f"运行无大模型 RAG v2 答案级评测，共 {total} 题",
        files=f"{output_path.relative_to(ROOT)}; {summary_path.relative_to(ROOT)}",
        command=f"{Path(__file__).name} --limit {args.limit} --top-k {args.top_k}",
        result="完成",
        note=(
            f"Top3={summary['top3_standard_hit_rate']:.0%}; "
            f"引用完整={summary['citation_complete_rate']:.0%}; "
            f"地区跑偏={summary['region_drift_count']}"
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
