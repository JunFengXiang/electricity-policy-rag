"""查询本地 RAG 索引并生成无大模型结构化回答。

RAG v2 使用“切片召回 -> 文档级重排 -> 每份资料限量切片 -> 相邻切片补上下文”
的流程，默认不调用大模型，直接产出带引用的政策问答草稿。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import pickle
import re
from pathlib import Path
from typing import Any

from domain_terms import RAG_KNOWN_TERMS
from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
INDEX_PATH = OUTPUT_DIR / "rag_index.pkl.gz"
VARIABLES_CSV = META_DIR / "政策变量表.csv"
EVOLUTION_CSV = META_DIR / "政策演化关系表.csv"
MODE = "no_llm_rag_v2"

REGIONAL_PROVINCES = {
    "南方区域": ["广东", "广西", "云南", "贵州", "海南"],
    "华中区域": ["湖北", "湖南", "河南", "江西", "重庆", "四川", "西藏"],
    "华北区域": ["北京", "天津", "河北", "山西", "内蒙古"],
    "东北区域": ["辽宁", "吉林", "黑龙江", "内蒙古"],
    "西北区域": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
    "华东区域": ["上海", "江苏", "浙江", "安徽", "福建", "山东"],
}

REGION_TERMS = [
    "全国",
    "南方区域",
    "华中区域",
    "华北区域",
    "东北区域",
    "西北区域",
    "华东区域",
    "南方",
    "华中",
    "华北",
    "东北",
    "西北",
    "华东",
    "北京",
    "天津",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "上海",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
    "重庆",
    "四川",
    "贵州",
    "云南",
    "西藏",
    "陕西",
    "甘肃",
    "青海",
    "宁夏",
    "新疆",
]

EXTRA_QUERY_TERMS = [
    "代理购电",
    "电网企业代理购电",
    "工商业用户",
    "售电公司",
    "售电公司管理办法",
    "信息披露管理细则",
    "信息披露基本规则",
    "中长期市场实施细则",
    "中长期交易实施细则",
    "交易组织",
    "准入",
    "退出",
    "应急调度",
    "跨省跨区",
    "省内交易",
    "省间交易",
    "复函",
    "市场主体",
    "经营主体",
]

KNOWN_TERMS = list(dict.fromkeys(RAG_KNOWN_TERMS + REGION_TERMS + EXTRA_QUERY_TERMS))
QUESTION_STOPWORDS = {
    "哪些",
    "什么",
    "如何",
    "有关",
    "相关",
    "主要",
    "政策",
    "规则",
    "文件",
    "要求",
    "应当",
    "是否",
    "怎么",
    "有何",
    "有什么",
    "是什么",
}

BROAD_TOPIC_TERMS = {
    "高质量发展",
    "新型电力系统",
    "电网",
    "电网规划",
    "电力保供",
    "新能源消纳",
    "能源安全",
    "绿色低碳",
}

RULE_POINT_HINTS = [
    "应",
    "应当",
    "明确",
    "包括",
    "按照",
    "组织",
    "执行",
    "交易",
    "结算",
    "补偿",
    "考核",
    "分摊",
    "价格",
    "电价",
    "申报",
    "注册",
    "披露",
]

SCOPE_HINTS = ["适用", "范围", "区域", "主体", "对象", "经营主体", "市场成员", "交易品种", "项目"]
CAUTION_HINTS = ["不得", "禁止", "未", "不予", "风险", "考核", "偏差", "有效", "征求意见", "试行", "废止"]


def load_index(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rb") as f:
        return pickle.load(f)


_research_cache: dict[str, Any] = {"variables_mtime": None, "variables": {}, "evolution_mtime": None, "evolution": {}}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_policy_variables() -> dict[str, dict[str, str]]:
    mtime = VARIABLES_CSV.stat().st_mtime if VARIABLES_CSV.exists() else 0
    if _research_cache["variables_mtime"] != mtime:
        rows = read_csv_rows(VARIABLES_CSV)
        _research_cache["variables"] = {row.get("资料编号", ""): row for row in rows if row.get("资料编号")}
        _research_cache["variables_mtime"] = mtime
    return _research_cache["variables"]


def load_evolution_relations() -> dict[str, list[dict[str, str]]]:
    mtime = EVOLUTION_CSV.stat().st_mtime if EVOLUTION_CSV.exists() else 0
    if _research_cache["evolution_mtime"] != mtime:
        grouped: dict[str, list[dict[str, str]]] = {}
        for row in read_csv_rows(EVOLUTION_CSV):
            for key in [row.get("源资料编号", ""), row.get("目标资料编号", "")]:
                if key:
                    grouped.setdefault(key, []).append(row)
        _research_cache["evolution"] = grouped
        _research_cache["evolution_mtime"] = mtime
    return _research_cache["evolution"]


def to_float(value: str, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def query_terms(query: str) -> list[str]:
    """合并领域词、地区词和粗分词，供精确命中和答案抽取使用。"""
    terms = [term for term in KNOWN_TERMS if term in query]
    rough_tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fa5]{2,}", query)
    for token in rough_tokens:
        if len(token) >= 2 and token not in QUESTION_STOPWORDS:
            terms.append(token)
    return sorted(set(terms), key=lambda item: (len(item), item), reverse=True)


def canonical_region(value: str) -> str:
    value = (value or "").strip()
    aliases = {
        "南方": "南方区域",
        "华中": "华中区域",
        "华北": "华北区域",
        "东北": "东北区域",
        "西北": "西北区域",
        "华东": "华东区域",
    }
    return aliases.get(value, value)


def infer_region(query: str, explicit_region: str = "") -> str:
    if explicit_region:
        return canonical_region(explicit_region)
    for region in sorted(REGION_TERMS, key=len, reverse=True):
        if region != "全国" and region in query:
            return canonical_region(region)
    if "全国" in query or "国家级" in query or "国家层面" in query:
        return "全国"
    return ""


def extract_doc_numbers(query: str) -> list[str]:
    patterns = [
        r"[\u4e00-\u9fa5]{0,8}〔\d{4}〕\d+号",
        r"[\u4e00-\u9fa5]{0,8}\[\d{4}\]\d+号",
        r"\d{4}年?\s*\d+号",
        r"\d+号",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, query or ""))
    return sorted(set(item.strip() for item in found if item.strip()), key=len, reverse=True)


def extract_title_phrases(query: str) -> list[str]:
    phrases = re.findall(r"《([^》]{4,120})》", query or "")
    if not phrases:
        for suffix in ["有哪些", "是什么", "如何", "主要", "规定", "要求"]:
            if suffix in query and len(query) > 8:
                phrases.append(query.split(suffix, 1)[0])
                break
    for marker in ["对", "应", "如何", "主要"]:
        if marker in query:
            prefix = query.split(marker, 1)[0].strip("，,；;。")
            if 4 <= len(prefix) <= 40:
                phrases.append(prefix)
                break
    return [phrase.strip() for phrase in phrases if phrase.strip()]


def query_profile(query: str, region: str = "") -> dict[str, Any]:
    inferred_region = infer_region(query, region)
    terms = query_terms(query)
    doc_numbers = extract_doc_numbers(query)
    title_phrases = extract_title_phrases(query)
    comparison = any(term in query for term in ["区别", "对比", "相比", "差异", "存量", "增量", "全国", "地方"])
    recent = any(term in query for term in ["近期", "最近", "十五天", "15天", "近15天"])
    broad_topic = not inferred_region and any(term in query for term in BROAD_TOPIC_TERMS) and len(terms) <= 4
    return {
        "region": inferred_region,
        "terms": terms,
        "doc_numbers": doc_numbers,
        "title_phrases": title_phrases,
        "comparison": comparison,
        "recent": recent,
        "broad_topic": broad_topic,
    }


def row_blob(row: dict[str, str]) -> str:
    return "\n".join(
        [
            row.get("文件标题", ""),
            row.get("章节标题", ""),
            row.get("适用地区", ""),
            row.get("市场主题", ""),
            row.get("发布机构", ""),
            row.get("采集来源机构", ""),
            row.get("文号", ""),
            row.get("正文片段", ""),
        ]
    )


def region_match_kind(row: dict[str, str], target_region: str) -> str:
    """返回 target/regional/national/miss/none，供过滤、重排和评测复用。"""
    target_region = canonical_region(target_region)
    if not target_region or target_region == "全国":
        return "none"
    doc_region = row.get("适用地区", "") or ""
    blob = ";".join(
        [
            doc_region,
            row.get("文件标题", ""),
            row.get("发布机构", ""),
            row.get("采集来源机构", ""),
        ]
    )
    if target_region in blob:
        return "target"
    if "全国" in doc_region:
        return "national"

    for area, provinces in REGIONAL_PROVINCES.items():
        if target_region == area and any(province in blob for province in provinces):
            return "target"
        if target_region in provinces and (area in blob or canonical_region(area) in blob):
            return "regional"
    return "miss"


def exact_hit_score(row: dict[str, str], terms: list[str]) -> tuple[float, list[str]]:
    blob = row_blob(row)
    hits = [term for term in terms if term and term in blob]
    score = min(20.0, sum(min(6, len(term)) for term in hits) * 0.9)
    return score, hits[:10]


def authority_bonus(value: str) -> float:
    return {"A": 8.0, "B": 5.0, "C": 2.0}.get((value or "").strip(), 0.0)


def source_bonus(value: str) -> float:
    text = value or ""
    if "官方政策" in text:
        return 6.0
    if "监管" in text:
        return 5.0
    if "交易规则" in text:
        return 5.0
    if "解读" in text or "新闻" in text:
        return -5.0
    return 0.0


def status_penalty(row: dict[str, str]) -> float:
    text = ";".join([row.get("文件标题", ""), row.get("有效状态", ""), row.get("来源类型", "")])
    penalty = 0.0
    if "征求" in text or "意见稿" in text:
        penalty -= 10.0
    if "废止" in text or "失效" in text:
        penalty -= 20.0
    if "现行有效" in text:
        penalty += 3.0
    return penalty


def parse_date(value: str) -> dt.date | None:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def recency_bonus(row: dict[str, str], recent: bool) -> float:
    if not recent:
        return 0.0
    parsed = parse_date(row.get("发布日期", ""))
    if not parsed:
        return 0.0
    today = dt.date.today()
    if parsed > today:
        return -4.0
    days = (today - parsed).days
    if days <= 15:
        return 14.0
    if days <= 30:
        return 9.0
    if days <= 90:
        return 5.0
    return 0.0


def title_match_bonus(row: dict[str, str], profile: dict[str, Any]) -> float:
    title = normalize_text(row.get("文件标题", ""))
    query_title_hits = 0.0
    for phrase in profile["title_phrases"]:
        phrase_norm = normalize_text(phrase)
        if len(phrase_norm) >= 6 and phrase_norm in title:
            query_title_hits += 24.0
    for term in profile["terms"]:
        term_norm = normalize_text(term)
        if len(term_norm) >= 4 and term_norm in title:
            query_title_hits += 7.0
    return min(query_title_hits, 34.0)


def doc_number_bonus(row: dict[str, str], profile: dict[str, Any]) -> float:
    doc_no_blob = row.get("文号", "") + ";" + row.get("文件标题", "")
    score = 0.0
    for doc_no in profile["doc_numbers"]:
        if normalize_text(doc_no) and normalize_text(doc_no) in normalize_text(doc_no_blob):
            score += 28.0
    return min(score, 32.0)


def region_bonus(row: dict[str, str], profile: dict[str, Any]) -> float:
    kind = region_match_kind(row, profile.get("region", ""))
    if profile.get("broad_topic"):
        region_text = row.get("适用地区", "")
        source_org = row.get("发布机构", "") + row.get("采集来源机构", "")
        if "全国" in region_text or any(org in source_org for org in ["国家发展改革委", "国家能源局", "国务院"]):
            return 16.0
        if any(area in region_text for area in REGIONAL_PROVINCES):
            return 6.0
        return -8.0
    if kind == "target":
        return 18.0
    if kind == "regional":
        return 11.0
    if kind == "national":
        return 3.0
    if kind == "miss":
        return -100.0
    return 0.0


def passes_filters(row: dict[str, str], profile: dict[str, Any], source_type: str, official_only: bool) -> bool:
    if source_type and source_type not in row.get("来源类型", ""):
        return False
    if official_only and any(token in row.get("来源类型", "") for token in ["解读", "新闻", "咨询"]):
        return False
    if region_match_kind(row, profile.get("region", "")) == "miss":
        return False
    return True


def chunk_order(row: dict[str, str]) -> int:
    raw = row.get("切片序号", "")
    if raw.isdigit():
        return int(raw)
    chunk_id = row.get("切片编号", "")
    match = re.search(r"-(\d{3,6})-[0-9A-Fa-f]+$", chunk_id)
    if match:
        return int(match.group(1))
    return 0


def doc_maps(payload: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    cached = payload.get("_doc_maps")
    if cached is not None:
        return cached
    maps: dict[str, list[dict[str, str]]] = {}
    for row in payload.get("rows", []):
        doc_id = row.get("资料编号", "")
        if not doc_id:
            continue
        maps.setdefault(doc_id, []).append(row)
    for rows in maps.values():
        rows.sort(key=chunk_order)
    payload["_doc_maps"] = maps
    return maps


def neighbor_context(payload: dict[str, Any], row: dict[str, str], max_len: int = 900) -> str:
    rows = doc_maps(payload).get(row.get("资料编号", ""), [])
    if not rows:
        return row.get("正文片段", "")
    chunk_id = row.get("切片编号", "")
    index = next((i for i, item in enumerate(rows) if item.get("切片编号", "") == chunk_id), -1)
    if index < 0:
        return row.get("正文片段", "")
    selected = rows[max(0, index - 1) : min(len(rows), index + 2)]
    text = "\n".join(item.get("正文片段", "") for item in selected if item.get("正文片段", ""))
    return compact_snippet(text, max_len=max_len)


def rank_chunks(
    payload: dict[str, Any],
    query: str,
    candidate_pool: int,
    profile: dict[str, Any],
    source_type: str,
    official_only: bool,
) -> list[dict[str, Any]]:
    vectorizer = payload["vectorizer"]
    matrix = payload["matrix"]
    rows = payload["rows"]
    query_vec = vectorizer.transform([query])
    scores = (matrix @ query_vec.T).toarray().ravel()

    candidate_count = min(candidate_pool, len(rows))
    if candidate_count == 0:
        return []
    top_indices = scores.argsort()[-candidate_count:][::-1]

    ranked: list[dict[str, Any]] = []
    for idx in top_indices:
        row = rows[int(idx)]
        if not passes_filters(row, profile=profile, source_type=source_type, official_only=official_only):
            continue
        vector_score = float(scores[int(idx)]) * 100
        metadata_score = to_float(row.get("检索权重", ""), 0.0)
        exact_score, hits = exact_hit_score(row, profile["terms"])
        final_score = (
            vector_score * 0.60
            + metadata_score * 0.12
            + exact_score
            + title_match_bonus(row, profile)
            + doc_number_bonus(row, profile)
            + authority_bonus(row.get("权威等级", ""))
            + source_bonus(row.get("来源类型", ""))
            + region_bonus(row, profile)
            + status_penalty(row)
            + recency_bonus(row, profile["recent"])
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
    return ranked


def document_rerank(
    payload: dict[str, Any],
    ranked_chunks: list[dict[str, Any]],
    top_k: int,
    per_doc_limit: int,
    profile: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = profile or {}
    effective_per_doc_limit = 1 if profile.get("broad_topic") else per_doc_limit
    doc_groups: dict[str, list[dict[str, Any]]] = {}
    for item in ranked_chunks:
        doc_id = item["row"].get("资料编号", "")
        if not doc_id:
            continue
        doc_groups.setdefault(doc_id, []).append(item)

    doc_scores: list[tuple[float, str]] = []
    for doc_id, items in doc_groups.items():
        top_scores = [item["score"] for item in items[:4]]
        score = top_scores[0] + sum(top_scores[1:]) * 0.08
        row = items[0]["row"]
        if profile.get("broad_topic"):
            region_text = row.get("适用地区", "")
            source_org = row.get("发布机构", "") + row.get("采集来源机构", "")
            if "全国" in region_text or any(org in source_org for org in ["国家发展改革委", "国家能源局", "国务院"]):
                score += 10.0
            elif any(area in region_text for area in REGIONAL_PROVINCES):
                score += 3.0
            else:
                score -= 6.0
        doc_scores.append((score, doc_id))
    doc_scores.sort(reverse=True)

    selected: list[dict[str, Any]] = []
    raw_count = len(ranked_chunks)
    for _, doc_id in doc_scores:
        items = sorted(doc_groups[doc_id], key=lambda item: item["score"], reverse=True)[:effective_per_doc_limit]
        for item in items:
            item = dict(item)
            item["context"] = neighbor_context(payload, item["row"])
            selected.append(item)
            if len(selected) >= top_k:
                break
        if len(selected) >= top_k:
            break

    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank

    unique_docs = len({item["row"].get("资料编号", "") for item in selected})
    duplicate_ratio = 0.0 if not selected else 1 - unique_docs / len(selected)
    stats = {
        "raw_chunk_count": raw_count,
        "raw_doc_count": len(doc_groups),
        "returned_chunk_count": len(selected),
        "returned_doc_count": unique_docs,
        "per_doc_limit": effective_per_doc_limit,
        "duplicate_ratio": round(duplicate_ratio, 3),
    }
    return selected, stats


def search_with_diagnostics(
    payload: dict[str, Any],
    query: str,
    top_k: int = 8,
    candidate_pool: int = 300,
    region: str = "",
    source_type: str = "",
    official_only: bool = True,
    per_doc_limit: int = 2,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = query_profile(query, region)
    ranked_chunks = rank_chunks(
        payload,
        query=query,
        candidate_pool=candidate_pool,
        profile=profile,
        source_type=source_type,
        official_only=official_only,
    )
    results, dedup_stats = document_rerank(payload, ranked_chunks, top_k=top_k, per_doc_limit=per_doc_limit, profile=profile)
    confidence = confidence_level(results, profile)
    diagnostics = {
        "mode": MODE,
        "profile": profile,
        "confidence": confidence,
        "dedup_stats": dedup_stats,
    }
    return results, diagnostics


def search(
    payload: dict[str, Any],
    query: str,
    top_k: int,
    candidate_pool: int,
    region: str,
    source_type: str,
    official_only: bool,
) -> list[dict[str, Any]]:
    results, _ = search_with_diagnostics(
        payload,
        query=query,
        top_k=top_k,
        candidate_pool=candidate_pool,
        region=region,
        source_type=source_type,
        official_only=official_only,
    )
    return results


def confidence_level(results: list[dict[str, Any]], profile: dict[str, Any]) -> str:
    if not results:
        return "low"
    top_score = results[0]["score"]
    unique_docs = len({item["row"].get("资料编号", "") for item in results})
    top_hits = len(results[0].get("exact_hits", []))
    if profile.get("region") and profile.get("region") != "全国":
        if not any(region_match_kind(item["row"], profile["region"]) in {"target", "regional"} for item in results[:3]):
            return "low"
    if top_score >= 62 and unique_docs >= 2 and top_hits >= 2:
        return "high"
    if top_score >= 42 and (unique_docs >= 1 or top_hits >= 1):
        return "medium"
    return "low"


def citation(row: dict[str, str]) -> str:
    parts = [
        row.get("资料编号", ""),
        row.get("文件标题", ""),
        row.get("发布机构", "") or row.get("采集来源机构", ""),
        row.get("发布日期", "") or "未标注日期",
        row.get("文号", "") or "未标注文号",
    ]
    return " | ".join(part for part in parts if part)


def citation_payload(row: dict[str, str], rank: int) -> dict[str, str | int]:
    variable = load_policy_variables().get(row.get("资料编号", ""), {})
    return {
        "rank": rank,
        "doc_id": row.get("资料编号", ""),
        "chunk_id": row.get("切片编号", ""),
        "title": row.get("文件标题", ""),
        "department": row.get("发布部门", "") or row.get("发布机构", "") or row.get("采集来源机构", ""),
        "publish_date": row.get("发布日期", "") or "未标注",
        "document_number": row.get("文号", "") or "未标注",
        "source_type": row.get("来源类型", ""),
        "authority": row.get("权威等级", ""),
        "status": row.get("有效状态", ""),
        "quality_status": variable.get("质量状态", "未生成"),
        "url": row.get("原文链接", "") or "未标注",
        "snippet": compact_snippet(row.get("正文片段", ""), 260),
    }


def build_citations(results: list[dict[str, Any]], limit: int = 8) -> list[dict[str, str | int]]:
    citations: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for item in results:
        row = item["row"]
        doc_id = row.get("资料编号", "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        citations.append(citation_payload(row, len(citations) + 1))
        if len(citations) >= limit:
            break
    return citations


def compact_snippet(text: str, max_len: int = 180) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    pieces = re.split(r"(?<=[。；;])|\n+", text)
    sentences: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        piece = piece.strip(" ；;。")
        if len(piece) < 12:
            continue
        key = normalize_text(piece[:120])
        if key in seen:
            continue
        seen.add(key)
        sentences.append(piece)
    return sentences


def score_sentence(sentence: str, terms: list[str], hints: list[str]) -> float:
    score = 0.0
    for term in terms:
        if term and term in sentence:
            score += min(8, len(term))
    for hint in hints:
        if hint in sentence:
            score += 3.0
    return score


def select_sentences(
    results: list[dict[str, Any]],
    terms: list[str],
    hints: list[str],
    limit: int,
) -> list[str]:
    candidates: list[tuple[float, str, str]] = []
    for item in results:
        row = item["row"]
        source = f"[{row.get('资料编号', '')}]"
        context = item.get("context") or row.get("正文片段", "")
        for sentence in split_sentences(context):
            score = score_sentence(sentence, terms, hints)
            if score > 0:
                candidates.append((score + item["score"] * 0.03, source, sentence))
    candidates.sort(key=lambda item: item[0], reverse=True)

    output: list[str] = []
    seen: set[str] = set()
    for _, source, sentence in candidates:
        cleaned = compact_snippet(sentence, 180)
        key = normalize_text(cleaned)
        if key in seen:
            continue
        seen.add(key)
        output.append(f"{source} {cleaned}")
        if len(output) >= limit:
            break
    return output


def fallback_terms(profile: dict[str, Any]) -> list[str]:
    terms = [term for term in profile.get("terms", []) if term not in REGION_TERMS and term not in QUESTION_STOPWORDS]
    return terms[:8] or ["文号", "地区", "交易规则", "实施细则"]


def missing_evidence(results: list[dict[str, Any]], profile: dict[str, Any], confidence: str) -> list[str]:
    missing: list[str] = []
    if not results:
        missing.append("未检索到可引用资料。")
    if confidence == "low":
        missing.append("当前命中分或精确命中不足，不能形成可靠政策判断。")
    if profile.get("region") and profile.get("region") != "全国":
        if not any(region_match_kind(item["row"], profile["region"]) in {"target", "regional"} for item in results[:3]):
            missing.append(f"Top3 未命中明确适用于 {profile['region']} 的资料。")
    return missing


def comparison_table(results: list[dict[str, Any]]) -> list[str]:
    docs = build_citations(results, limit=4)
    if len(docs) < 2:
        return []
    lines = ["| 对比项 | 资料 | 可直接依据 |", "| --- | --- | --- |"]
    for doc in docs:
        lines.append(f"| {doc['source_type'] or '政策资料'} | [{doc['rank']}] {doc['title']} | {doc['snippet']} |")
    return lines


def doc_ids_from_results(results: list[dict[str, Any]], limit: int = 6) -> list[str]:
    doc_ids: list[str] = []
    seen: set[str] = set()
    for item in results:
        doc_id = item["row"].get("资料编号", "")
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            doc_ids.append(doc_id)
        if len(doc_ids) >= limit:
            break
    return doc_ids


def variable_lines(doc_ids: list[str]) -> list[str]:
    variables = load_policy_variables()
    fields = ["政策工具", "适用主体", "市场环节", "价格机制", "交易品种", "规划场景", "投资影响", "商业模式影响", "风险约束"]
    lines: list[str] = []
    for doc_id in doc_ids[:5]:
        row = variables.get(doc_id)
        if not row:
            continue
        parts = [f"{field}：{row.get(field, '')}" for field in fields if row.get(field, "")]
        if parts:
            lines.append(f"[{doc_id}] " + "；".join(parts[:7]) + f"；质量状态：{row.get('质量状态', '未生成')}")
    return lines


def evolution_lines(doc_ids: list[str]) -> list[str]:
    relations = load_evolution_relations()
    lines: list[str] = []
    seen: set[str] = set()
    for doc_id in doc_ids[:6]:
        for rel in relations.get(doc_id, [])[:4]:
            if rel.get("源资料编号") == doc_id:
                other_id = rel.get("目标资料编号", "")
                other_title = rel.get("目标标题", "")
                direction = "指向"
            else:
                other_id = rel.get("源资料编号", "")
                other_title = rel.get("源标题", "")
                direction = "来自"
            key = f"{doc_id}-{other_id}-{rel.get('关系类型', '')}"
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"[{doc_id}] {rel.get('关系类型', '关联')}：{direction} [{other_id}] {compact_snippet(other_title, 80)}；"
                f"区域路径：{rel.get('区域路径', '')}；置信度：{rel.get('置信度', '')}"
            )
            if len(lines) >= 8:
                return lines
    return lines


def management_implications(doc_ids: list[str]) -> list[str]:
    variables = load_policy_variables()
    lines: list[str] = []
    for doc_id in doc_ids[:5]:
        row = variables.get(doc_id)
        if not row:
            continue
        investment = row.get("投资影响", "")
        business = row.get("商业模式影响", "")
        risk = row.get("风险约束", "")
        pricing = row.get("价格机制", "")
        parts = []
        if investment:
            parts.append(f"投资决策：{investment}")
        if business:
            parts.append(f"商业模式：{business}")
        if pricing:
            parts.append(f"成本收益：{pricing}")
        if risk:
            parts.append(f"市场与合规风险：{risk}")
        if parts:
            lines.append(f"[{doc_id}] " + "；".join(parts))
    return lines


def quality_caution_lines(doc_ids: list[str]) -> list[str]:
    variables = load_policy_variables()
    lines: list[str] = []
    for doc_id in doc_ids[:6]:
        row = variables.get(doc_id)
        if not row:
            lines.append(f"[{doc_id}] 尚未生成政策变量和质量状态，引用前应复核。")
            continue
        quality = row.get("质量状态", "未生成")
        review = row.get("人工复核状态", "")
        if quality != "可引用":
            lines.append(f"[{doc_id}] 质量状态为“{quality}”，人工复核状态为“{review or '未标'}”，不能等同人工校验结论。")
    return lines


def build_answer_sections(
    query: str,
    results: list[dict[str, Any]],
    diagnostics: dict[str, Any],
    answer_mode: str = "research",
) -> dict[str, Any]:
    profile = diagnostics.get("profile", query_profile(query))
    confidence = diagnostics.get("confidence", confidence_level(results, profile))
    terms = fallback_terms(profile)
    miss = missing_evidence(results, profile, confidence)

    if not results or confidence == "low":
        return {
            "direct_conclusion": "依据不足：当前知识库没有检索到足够稳定的官方、监管或交易规则证据，不能给出确定政策判断。",
            "applicable_scope": [],
            "rule_points": [],
            "policy_variables": [],
            "evolution_relations": [],
            "management_implications": [],
            "cautions": miss,
            "quality_cautions": miss,
            "comparison": [],
            "citations": [],
        }

    citations = build_citations(results, limit=6)
    doc_ids = doc_ids_from_results(results, limit=6)
    scope = select_sentences(results, terms, SCOPE_HINTS, limit=3)
    points = select_sentences(results, terms, RULE_POINT_HINTS, limit=6)
    cautions = select_sentences(results, terms, CAUTION_HINTS, limit=3)
    comparison = comparison_table(results) if profile.get("comparison") or answer_mode == "comparison" else []

    if not points:
        points = [
            f"[{item['row'].get('资料编号', '')}] {compact_snippet(item.get('context') or item['row'].get('正文片段', ''), 180)}"
            for item in results[:3]
        ]

    doc_titles = "、".join(f"[{item['rank']}] {item['title']}" for item in citations[:3])
    return {
        "direct_conclusion": f"根据当前命中的 {len(citations)} 份资料，优先依据 {doc_titles}。以下结论均来自本地知识库切片，未调用大模型自由生成。",
        "applicable_scope": scope,
        "rule_points": points,
        "policy_variables": variable_lines(doc_ids),
        "evolution_relations": evolution_lines(doc_ids),
        "management_implications": management_implications(doc_ids),
        "cautions": cautions or ["未从命中证据中抽取到额外限制条件；仍应以引用文件原文为准。"],
        "quality_cautions": quality_caution_lines(doc_ids),
        "comparison": comparison,
        "citations": citations,
    }


def format_answer_markdown(query: str, sections: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    lines = [
        "# RAG结构化回答",
        "",
        f"问题：{query}",
        f"模式：{MODE}",
        f"置信度：{diagnostics.get('confidence', 'low')}",
        "",
        "## 直接结论",
        sections.get("direct_conclusion", ""),
        "",
        "## 适用范围",
    ]
    scope = sections.get("applicable_scope") or ["未从命中证据中抽取到明确适用范围。"]
    lines.extend(f"- {item}" for item in scope)
    if sections.get("comparison"):
        lines.extend(["", "## 对比信息"])
        lines.extend(sections["comparison"])
    lines.extend(["", "## 规则要点"])
    points = sections.get("rule_points") or ["依据不足，未生成规则要点。"]
    lines.extend(f"- {item}" for item in points)
    lines.extend(["", "## 政策变量"])
    variables = sections.get("policy_variables") or ["尚未从命中资料中生成政策变量。"]
    lines.extend(f"- {item}" for item in variables)
    lines.extend(["", "## 政策演化关系"])
    evolutions = sections.get("evolution_relations") or ["尚未识别到可展示的上位法、配套文件、地方承接或修订替代关系。"]
    lines.extend(f"- {item}" for item in evolutions)
    lines.extend(["", "## 管理学与技术经济含义"])
    implications = sections.get("management_implications") or ["当前证据不足以形成投资、成本收益、商业模式或风险层面的结构化解释。"]
    lines.extend(f"- {item}" for item in implications)
    lines.extend(["", "## 注意事项"])
    cautions = sections.get("cautions") or ["请以引用文件原文为准。"]
    lines.extend(f"- {item}" for item in cautions)
    quality_cautions = sections.get("quality_cautions") or []
    if quality_cautions:
        lines.extend(["", "## 可信度提示"])
        lines.extend(f"- {item}" for item in quality_cautions)
    lines.extend(["", "## 引用资料"])
    citations = sections.get("citations") or []
    if not citations:
        lines.append("- 无可靠引用资料。")
    for item in citations:
        lines.append(
            f"- [{item['rank']}] 资料编号：{item['doc_id']}；标题：{item['title']}；"
            f"日期：{item['publish_date']}；文号：{item['document_number']}；"
            f"有效状态：{item.get('status', '')}；质量状态：{item.get('quality_status', '未生成')}；链接：{item['url']}"
        )
    return "\n".join(lines)


def build_no_llm_response(
    query: str,
    results: list[dict[str, Any]],
    diagnostics: dict[str, Any] | None = None,
    answer_mode: str = "research",
) -> dict[str, Any]:
    diagnostics = diagnostics or {"profile": query_profile(query), "confidence": confidence_level(results, query_profile(query)), "dedup_stats": {}}
    sections = build_answer_sections(query, results, diagnostics, answer_mode=answer_mode)
    answer = format_answer_markdown(query, sections, diagnostics)
    miss = missing_evidence(results, diagnostics.get("profile", {}), diagnostics.get("confidence", "low"))
    return {
        "mode": MODE,
        "answer_mode": answer_mode,
        "confidence": diagnostics.get("confidence", "low"),
        "answer": answer,
        "answer_sections": sections,
        "citations": sections.get("citations", []),
        "missing_evidence": miss,
        "dedup_stats": diagnostics.get("dedup_stats", {}),
    }


def build_answer(query: str, results: list[dict[str, Any]]) -> str:
    diagnostics = {
        "mode": MODE,
        "profile": query_profile(query),
        "confidence": confidence_level(results, query_profile(query)),
        "dedup_stats": {
            "returned_chunk_count": len(results),
            "returned_doc_count": len({item["row"].get("资料编号", "") for item in results}),
        },
    }
    return build_no_llm_response(query, results, diagnostics)["answer"]


def write_result(query: str, answer: str) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[\\/:*?\"<>|]+", "_", query).strip()[:32] or "query"
    path = OUTPUT_DIR / f"rag回答_{timestamp}_{safe}.md"
    path.write_text(answer, encoding="utf-8")
    return path


def self_check(index_path: Path) -> int:
    payload = load_index(index_path)
    checks = [
        ("四川电力辅助服务管理实施细则有哪些考核和补偿规则？", "四川"),
        ("新能源上网电价市场化改革对存量项目和增量项目有什么区别？", ""),
        ("高质量发展和电网规划相关政策有哪些？", ""),
    ]
    failures: list[str] = []
    for query, region in checks:
        results, diagnostics = search_with_diagnostics(payload, query, top_k=6, candidate_pool=500, region=region)
        response = build_no_llm_response(query, results, diagnostics)
        if not results:
            failures.append(f"{query}: no results")
            continue
        if not response["citations"]:
            failures.append(f"{query}: missing citations")
        if "规则要点" not in response["answer"]:
            failures.append(f"{query}: missing structured sections")
        if region and any(region_match_kind(item["row"], region) == "miss" for item in results[:3]):
            failures.append(f"{query}: region drift in top3")
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    print(f"RAG v2 自检通过：{len(checks)} 个问题")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Query the local RAG v2 index and produce cited evidence.")
    parser.add_argument("--query", default="", help="User question.")
    parser.add_argument("--index", default=str(INDEX_PATH), help="RAG index path.")
    parser.add_argument("--top-k", type=int, default=8, help="Number of chunks to return.")
    parser.add_argument("--candidate-pool", type=int, default=500, help="Initial vector candidates before rerank.")
    parser.add_argument("--per-doc-limit", type=int, default=2, help="Maximum chunks returned per document.")
    parser.add_argument("--region", default="", help="Optional region filter, e.g. 四川.")
    parser.add_argument("--source-type", default="", help="Optional source type filter.")
    parser.add_argument("--answer-mode", default="research", choices=["research", "evidence", "comparison"], help="Structured answer mode.")
    parser.add_argument("--include-interpretation", action="store_true", help="Allow interpretation/news sources.")
    parser.add_argument("--json", action="store_true", help="Print JSON response instead of Markdown.")
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
    results, diagnostics = search_with_diagnostics(
        payload,
        args.query,
        top_k=args.top_k,
        candidate_pool=args.candidate_pool,
        region=args.region,
        source_type=args.source_type,
        official_only=not args.include_interpretation,
        per_doc_limit=args.per_doc_limit,
    )
    response = build_no_llm_response(args.query, results, diagnostics, answer_mode=args.answer_mode)
    if args.json:
        print(json.dumps(response, ensure_ascii=False, indent=2))
    else:
        print(response["answer"])

    output_path = None
    if args.write:
        output_path = write_result(args.query, response["answer"])
        print(f"\n已写入：{output_path}")

    append_log(
        action_type="RAG查询测试",
        content=f"运行本地RAG v2查询：{args.query}",
        files=str(output_path.relative_to(ROOT)) if output_path else str(index_path.relative_to(ROOT)),
        command=f"{Path(__file__).name} --query \"{args.query}\" --top-k {args.top_k}",
        result="完成" if results else "无结果",
        note=f"返回切片={len(results)}; 模式={MODE}; answer_mode={args.answer_mode}; 置信度={response['confidence']}; 未调用大模型",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
