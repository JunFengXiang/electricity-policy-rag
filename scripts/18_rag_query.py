"""查询本地 RAG 索引并生成带引用的回答草稿。

脚本负责把用户问题转成检索词、筛选切片、排序打分，并把命中的政策来源整理成
可追溯的 Markdown 回答。
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import pickle
import re
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "05_输出成果"
INDEX_PATH = OUTPUT_DIR / "rag_index.pkl.gz"

KNOWN_TERMS = [
    "电力市场",
    "电力交易",
    "交易规则",
    "规则体系",
    "实施细则",
    "中长期",
    "现货",
    "辅助服务",
    "调峰",
    "调频",
    "备用",
    "省内",
    "省间",
    "跨省",
    "跨区",
    "绿电",
    "绿证",
    "结算",
    "计量结算",
    "注册",
    "信息披露",
    "容量电价",
    "需求响应",
    "储能",
    "新能源",
    "山西",
    "山东",
    "江苏",
    "浙江",
    "四川",
    "新疆",
    "广东",
    "上海",
    "安徽",
    "福建",
    "内蒙古",
    "华东",
    "华中",
    "南方",
    "西北",
]


def load_index(path: Path) -> dict:
    with gzip.open(path, "rb") as f:
        return pickle.load(f)


def to_float(value: str, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def query_terms(query: str) -> list[str]:
    """合并领域词表和粗分词结果，用于给向量召回结果增加可解释的精确命中分。"""
    terms = [term for term in KNOWN_TERMS if term in query]
    rough_tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]{2,}", query)
    terms.extend(token for token in rough_tokens if len(token) >= 2)
    # Long terms first helps exact matching explainability.
    return sorted(set(terms), key=lambda item: (len(item), item), reverse=True)


def authority_bonus(value: str) -> float:
    return {"A": 8.0, "B": 5.0, "C": 2.0}.get((value or "").strip(), 0.0)


def source_bonus(value: str) -> float:
    text = value or ""
    if "官方政策" in text:
        return 5.0
    if "监管" in text:
        return 4.0
    if "交易规则" in text:
        return 4.0
    if "解读" in text or "新闻" in text:
        return -3.0
    return 0.0


def row_blob(row: dict[str, str]) -> str:
    return "\n".join(
        [
            row.get("文件标题", ""),
            row.get("章节标题", ""),
            row.get("适用地区", ""),
            row.get("市场主题", ""),
            row.get("发布机构", ""),
            row.get("采集来源机构", ""),
            row.get("正文片段", ""),
        ]
    )


def exact_hit_score(row: dict[str, str], terms: list[str]) -> tuple[float, list[str]]:
    """统计查询词在切片字段中的直接命中，弥补短政策术语在 TF-IDF 中的波动。"""
    blob = row_blob(row)
    hits = [term for term in terms if term and term in blob]
    score = min(16.0, sum(min(5, len(term)) for term in hits) * 0.8)
    return score, hits[:8]


def passes_filters(row: dict[str, str], region: str, source_type: str, official_only: bool) -> bool:
    if region and region not in row.get("适用地区", "") and region not in row.get("文件标题", ""):
        return False
    if source_type and source_type not in row.get("来源类型", ""):
        return False
    if official_only and any(token in row.get("来源类型", "") for token in ["解读", "新闻", "咨询"]):
        return False
    return True


def search(payload: dict, query: str, top_k: int, candidate_pool: int, region: str, source_type: str, official_only: bool):
    """先用向量召回候选，再叠加权威、来源和精确命中分排序。"""
    vectorizer = payload["vectorizer"]
    matrix = payload["matrix"]
    rows = payload["rows"]
    query_vec = vectorizer.transform([query])
    scores = (matrix @ query_vec.T).toarray().ravel()

    candidate_count = min(candidate_pool, len(rows))
    if candidate_count == 0:
        return []
    top_indices = scores.argsort()[-candidate_count:][::-1]
    terms = query_terms(query)

    ranked = []
    for idx in top_indices:
        row = rows[int(idx)]
        if not passes_filters(row, region=region, source_type=source_type, official_only=official_only):
            continue
        vector_score = float(scores[int(idx)]) * 100
        metadata_score = to_float(row.get("检索权重", ""), 0.0)
        exact_score, hits = exact_hit_score(row, terms)
        final_score = (
            vector_score * 0.68
            + metadata_score * 0.18
            + exact_score
            + authority_bonus(row.get("权威等级", ""))
            + source_bonus(row.get("来源类型", ""))
        )
        ranked.append(
            {
                "rank": 0,
                "score": round(final_score, 3),
                "vector_score": round(vector_score, 3),
                "metadata_score": round(metadata_score, 3),
                "exact_hits": hits,
                "row": row,
            }
        )

    ranked.sort(key=lambda item: item["score"], reverse=True)
    for rank, item in enumerate(ranked[:top_k], start=1):
        item["rank"] = rank
    return ranked[:top_k]


def citation(row: dict[str, str]) -> str:
    parts = [
        row.get("文件标题", ""),
        row.get("发布机构", "") or row.get("采集来源机构", ""),
        row.get("发布日期", ""),
        row.get("文号", ""),
    ]
    return " | ".join(part for part in parts if part)


def compact_snippet(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def build_answer(query: str, results: list[dict]) -> str:
    """把检索结果整理成回答草稿；最终结论仍需要人工或更强模型润色。"""
    lines = [
        f"# RAG检索回答草稿",
        "",
        f"问题：{query}",
        "",
    ]
    if not results:
        lines.extend(["未检索到足够相关的知识切片。"])
        return "\n".join(lines)

    docs: dict[str, dict] = {}
    for item in results:
        row = item["row"]
        doc_id = row.get("资料编号", "")
        if doc_id not in docs:
            docs[doc_id] = item

    lines.extend(
        [
            "结论：下面是本地知识库检索出的主要依据。当前版本是“检索增强草稿”，还没有调用大模型做自由生成，因此内容以证据摘录和来源排序为主。",
            "",
            "## 主要依据",
        ]
    )
    for index, item in enumerate(list(docs.values())[:5], start=1):
        row = item["row"]
        lines.append(
            f"{index}. {citation(row)}  \n"
            f"   分数：{item['score']}；命中：{';'.join(item['exact_hits']) or '向量相似'}  \n"
            f"   摘录：{compact_snippet(row.get('正文片段', ''))}"
        )

    lines.extend(["", "## 切片明细"])
    for item in results:
        row = item["row"]
        lines.append(
            f"- [{item['rank']}] {citation(row)}  \n"
            f"  章节：{row.get('章节标题', '') or '未标注'}  \n"
            f"  链接：{row.get('原文链接', '')}  \n"
            f"  摘录：{compact_snippet(row.get('正文片段', ''), 220)}"
        )
    return "\n".join(lines)


def write_result(query: str, answer: str) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", query).strip()[:32] or "query"
    path = OUTPUT_DIR / f"rag回答_{timestamp}_{safe}.md"
    path.write_text(answer, encoding="utf-8")
    return path


def self_check(index_path: Path) -> int:
    payload = load_index(index_path)
    checks = [
        "四川电力辅助服务市场交易实施细则",
        "山西电力市场规则体系 V16.0",
        "江苏电力中长期市场实施细则 2026",
    ]
    failures: list[str] = []
    for query in checks:
        results = search(payload, query, top_k=5, candidate_pool=200, region="", source_type="", official_only=True)
        if not results:
            failures.append(f"{query}: no results")
            continue
        top_title = results[0]["row"].get("文件标题", "")
        if not top_title:
            failures.append(f"{query}: missing title")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print(f"RAG查询自检通过：{len(checks)} 个问题")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the local RAG index and produce cited evidence.")
    parser.add_argument("--query", default="", help="User question.")
    parser.add_argument("--index", default=str(INDEX_PATH), help="RAG index path.")
    parser.add_argument("--top-k", type=int, default=8, help="Number of chunks to return.")
    parser.add_argument("--candidate-pool", type=int, default=300, help="Initial vector candidates before rerank.")
    parser.add_argument("--region", default="", help="Optional region filter, e.g. 四川.")
    parser.add_argument("--source-type", default="", help="Optional source type filter.")
    parser.add_argument("--include-interpretation", action="store_true", help="Allow interpretation/news sources.")
    parser.add_argument("--write", action="store_true", help="Write markdown answer to 05_输出成果.")
    parser.add_argument("--self-check", action="store_true", help="Run built-in checks.")
    args = parser.parse_args()

    index_path = Path(args.index)
    if args.self_check:
        return self_check(index_path)
    if not args.query:
        print("ERROR: --query is required unless --self-check is used")
        return 2

    payload = load_index(index_path)
    results = search(
        payload,
        args.query,
        top_k=args.top_k,
        candidate_pool=args.candidate_pool,
        region=args.region,
        source_type=args.source_type,
        official_only=not args.include_interpretation,
    )
    answer = build_answer(args.query, results)
    print(answer)

    output_path = None
    if args.write:
        output_path = write_result(args.query, answer)
        print(f"\n已写入：{output_path}")

    append_log(
        action_type="RAG查询测试",
        content=f"运行本地RAG查询：{args.query}",
        files=str(output_path.relative_to(ROOT)) if output_path else str(index_path.relative_to(ROOT)),
        command=f"{Path(__file__).name} --query \"{args.query}\" --top-k {args.top_k}",
        result="完成" if results else "无结果",
        note=f"返回切片={len(results)}; 本地检索增强草稿，未调用大模型",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
