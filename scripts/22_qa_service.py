"""Local QA API for the electricity policy knowledge base."""

from __future__ import annotations

import importlib.util
import csv
import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from llm_client import LlmSettings, chat_completion, load_env_file


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "05_输出成果" / "rag_index.pkl.gz"
SEARCH_INDEX_PATH = ROOT / "05_输出成果" / "search_index.json"
RESEARCH_INDEX_PATH = ROOT / "05_输出成果" / "research_platform_index.json"
VARIABLES_CSV = ROOT / "02_元数据" / "政策变量表.csv"
EVOLUTION_CSV = ROOT / "02_元数据" / "政策演化关系表.csv"
CITATION_CSV = ROOT / "05_输出成果" / "引用清单.csv"
REGION_COMPARE_CSV = ROOT / "05_输出成果" / "区域比较表.csv"
RAG_SCRIPT = Path(__file__).with_name("18_rag_query.py")


def load_rag_module():
    spec = importlib.util.spec_from_file_location("rag_query_module", RAG_SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"无法加载 RAG 脚本：{RAG_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rag = load_rag_module()
load_env_file()
DEFAULT_MAX_CONTEXT_CHUNKS = max(1, int(os.getenv("LLM_MAX_CONTEXT_CHUNKS", "8") or "8"))
app = FastAPI(title="电力政策知识库问答服务", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_payload_cache: dict[str, Any] = {"mtime": None, "payload": None}
_research_cache: dict[str, Any] = {"key": None, "assets": None}


class AskRequest(BaseModel):
    query: str = Field(..., min_length=2)
    region: str = ""
    source_type: str = ""
    official_only: bool = True
    top_k: int = Field(default=DEFAULT_MAX_CONTEXT_CHUNKS, ge=1, le=20)
    candidate_pool: int = Field(default=300, ge=20, le=2000)
    per_doc_limit: int = Field(default=2, ge=1, le=5)
    use_llm: bool = False
    answer_mode: str = "research"


class CompareRequest(BaseModel):
    topic: str = ""
    regions: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=200)


class ExportRequest(BaseModel):
    export_type: str = "citations"
    query: str = ""
    doc_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=80, ge=1, le=500)


def load_payload() -> dict:
    mtime = INDEX_PATH.stat().st_mtime if INDEX_PATH.exists() else 0
    if _payload_cache["payload"] is None or _payload_cache["mtime"] != mtime:
        _payload_cache["payload"] = rag.load_index(INDEX_PATH)
        _payload_cache["mtime"] = mtime
    return _payload_cache["payload"]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_research_assets() -> dict[str, Any]:
    mtimes = tuple(
        path.stat().st_mtime if path.exists() else 0
        for path in [SEARCH_INDEX_PATH, RESEARCH_INDEX_PATH, VARIABLES_CSV, EVOLUTION_CSV, CITATION_CSV, REGION_COMPARE_CSV]
    )
    if _research_cache["key"] != mtimes:
        search_index = json.loads(SEARCH_INDEX_PATH.read_text(encoding="utf-8")) if SEARCH_INDEX_PATH.exists() else {"documents": []}
        research_index = json.loads(RESEARCH_INDEX_PATH.read_text(encoding="utf-8")) if RESEARCH_INDEX_PATH.exists() else {}
        variables = read_csv_rows(VARIABLES_CSV)
        evolution = read_csv_rows(EVOLUTION_CSV)
        citations = read_csv_rows(CITATION_CSV)
        region_compare = read_csv_rows(REGION_COMPARE_CSV)
        _research_cache["assets"] = {
            "search_index": search_index,
            "research_index": research_index,
            "documents": {doc.get("id", ""): doc for doc in search_index.get("documents", []) if doc.get("id")},
            "variables": {row.get("资料编号", ""): row for row in variables if row.get("资料编号")},
            "evolution_by_doc": group_evolution(evolution),
            "citations": citations,
            "region_compare": region_compare,
        }
        _research_cache["key"] = mtimes
    return _research_cache["assets"]


def group_evolution(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        for doc_id in [row.get("源资料编号", ""), row.get("目标资料编号", "")]:
            if doc_id:
                grouped.setdefault(doc_id, []).append(row)
    return grouped


def row_matches(row: dict[str, str], query: str) -> bool:
    if not query:
        return True
    blob = " ".join(str(value) for value in row.values())
    return all(term in blob for term in re.split(r"\s+", query.strip()) if term)


def split_values(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[;；,，、]+", value or "") if item.strip()]


def markdown_table(rows: list[dict[str, str]], fields: list[str]) -> str:
    if not rows:
        return ""
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join(["---"] * len(fields)) + " |"]
    for row in rows:
        lines.append("| " + " | ".join((row.get(field, "") or "").replace("|", "｜") for field in fields) + " |")
    return "\n".join(lines)


def compact(text: str, max_len: int = 360) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def citation_payload(row: dict[str, str], rank: int) -> dict[str, str | int]:
    variable = load_research_assets().get("variables", {}).get(row.get("资料编号", ""), {})
    return {
        "rank": rank,
        "doc_id": row.get("资料编号", ""),
        "chunk_id": row.get("切片编号", ""),
        "title": row.get("文件标题", ""),
        "department": row.get("发布部门", "") or row.get("发布机构", "") or row.get("采集来源机构", ""),
        "publish_date": row.get("发布日期", ""),
        "document_number": row.get("文号", ""),
        "source_type": row.get("来源类型", ""),
        "authority": row.get("权威等级", ""),
        "status": row.get("有效状态", ""),
        "quality_status": variable.get("质量状态", "未生成"),
        "url": row.get("原文链接", ""),
        "snippet": compact(row.get("正文片段", "")),
    }


def build_citations(results: list[dict]) -> list[dict[str, str | int]]:
    citations: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for item in results:
        row = item["row"]
        doc_id = row.get("资料编号", "")
        if doc_id in seen:
            continue
        seen.add(doc_id)
        citations.append(citation_payload(row, len(citations) + 1))
    return citations


def build_prompt(query: str, results: list[dict]) -> list[dict[str, str]]:
    evidence_lines: list[str] = []
    for index, item in enumerate(results, start=1):
        row = item["row"]
        evidence_lines.append(
            "\n".join(
                [
                    f"[{index}] 资料编号：{row.get('资料编号', '')}",
                    f"标题：{row.get('文件标题', '')}",
                    f"来源类型：{row.get('来源类型', '')}；权威等级：{row.get('权威等级', '')}",
                    f"发布机构：{row.get('发布部门', '') or row.get('发布机构', '') or row.get('采集来源机构', '')}",
                    f"发布日期：{row.get('发布日期', '')}；文号：{row.get('文号', '')}",
                    f"链接：{row.get('原文链接', '')}",
                    f"证据摘录：{compact(row.get('正文片段', ''), 700)}",
                ]
            )
        )
    system = (
        "你是电力政策知识库问答助手。只能根据给定证据回答。"
        "官方政策、监管规则、交易规则优先；官方解读只能辅助解释。"
        "如果证据不足，必须明确说未找到足够依据。"
        "回答要列出引用编号，不能编造文号、日期或机构。"
    )
    user = f"问题：{query}\n\n证据：\n\n" + "\n\n".join(evidence_lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def fallback_answer(query: str, results: list[dict], reason: str) -> str:
    draft = rag.build_answer(query, results)
    return f"{draft}\n\n> 大模型未调用：{reason}"


@app.get("/api/health")
def health() -> dict[str, Any]:
    payload = load_payload()
    assets = load_research_assets()
    summary_path = ROOT / "05_输出成果" / "rag_index.summary.json"
    summary = {}
    if summary_path.exists():
        import json

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "index_path": str(INDEX_PATH.relative_to(ROOT)),
        "index_mtime": _payload_cache["mtime"],
        "chunk_count": len(payload.get("rows", [])),
        "document_count": len({row.get("资料编号", "") for row in payload.get("rows", [])}),
        "summary": summary,
        "rag_mode": getattr(rag, "MODE", "no_llm_rag"),
        "llm_configured": bool(os.getenv("LLM_API_KEY")),
        "max_context_chunks": DEFAULT_MAX_CONTEXT_CHUNKS,
        "research_platform": {
            "variable_count": len(assets.get("variables", {})),
            "document_index_count": len(assets.get("documents", {})),
            "research_index_ready": bool(assets.get("research_index")),
        },
    }


@app.post("/api/ask")
def ask(request: AskRequest) -> dict[str, Any]:
    payload = load_payload()
    results, diagnostics = rag.search_with_diagnostics(
        payload,
        query=request.query,
        top_k=request.top_k,
        candidate_pool=request.candidate_pool,
        region=request.region,
        source_type=request.source_type,
        official_only=request.official_only,
        per_doc_limit=request.per_doc_limit,
    )
    warnings: list[str] = []
    answer_mode = request.answer_mode if request.answer_mode in {"research", "evidence", "comparison"} else "research"
    no_llm_response = rag.build_no_llm_response(request.query, results, diagnostics, answer_mode=answer_mode)
    if not results:
        return {
            "answer": no_llm_response["answer"],
            "citations": no_llm_response["citations"],
            "retrieval_hits": [],
            "warnings": ["no_retrieval_results"],
            "mode": no_llm_response["mode"],
            "confidence": no_llm_response["confidence"],
            "answer_sections": no_llm_response["answer_sections"],
            "missing_evidence": no_llm_response["missing_evidence"],
            "dedup_stats": no_llm_response["dedup_stats"],
            "answer_mode": no_llm_response.get("answer_mode", answer_mode),
        }

    answer_results = results
    citations = no_llm_response["citations"]
    answer = no_llm_response["answer"]
    mode = no_llm_response["mode"]
    if request.use_llm:
        settings = LlmSettings.from_env()
        answer_results = results[: settings.max_context_chunks]
        try:
            answer = chat_completion(build_prompt(request.query, answer_results), settings)
            mode = "llm_with_rag_v2"
        except Exception as exc:
            warnings.append(f"llm_fallback: {type(exc).__name__}: {exc}")
            answer = no_llm_response["answer"]
            mode = no_llm_response["mode"]

    return {
        "answer": answer,
        "citations": citations,
        "retrieval_hits": [
            {
                "rank": item["rank"],
                "score": item["score"],
                "exact_hits": item["exact_hits"],
                "doc_id": item["row"].get("资料编号", ""),
                "title": item["row"].get("文件标题", ""),
            }
            for item in results
        ],
        "warnings": warnings,
        "mode": mode,
        "confidence": no_llm_response["confidence"],
        "answer_sections": no_llm_response["answer_sections"],
        "missing_evidence": no_llm_response["missing_evidence"],
        "dedup_stats": no_llm_response["dedup_stats"],
        "answer_mode": no_llm_response.get("answer_mode", answer_mode),
    }


@app.get("/api/policy/{doc_id}")
def policy_detail(doc_id: str) -> dict[str, Any]:
    assets = load_research_assets()
    doc = assets.get("documents", {}).get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Policy not found: {doc_id}")
    return {
        "policy": doc,
        "variables": assets.get("variables", {}).get(doc_id, {}),
        "evolution": assets.get("evolution_by_doc", {}).get(doc_id, []),
    }


@app.get("/api/policy/{doc_id}/variables")
def policy_variables(doc_id: str) -> dict[str, Any]:
    variable = load_research_assets().get("variables", {}).get(doc_id)
    if not variable:
        raise HTTPException(status_code=404, detail=f"Policy variables not found: {doc_id}")
    return {"doc_id": doc_id, "variables": variable}


@app.get("/api/policy/{doc_id}/evolution")
def policy_evolution(doc_id: str) -> dict[str, Any]:
    rows = load_research_assets().get("evolution_by_doc", {}).get(doc_id, [])
    return {"doc_id": doc_id, "relations": rows, "relation_count": len(rows)}


@app.post("/api/research/compare")
def research_compare(request: CompareRequest) -> dict[str, Any]:
    assets = load_research_assets()
    rows = assets.get("region_compare", [])
    if request.topic:
        grouped: dict[str, dict[str, Any]] = {}
        for row in assets.get("variables", {}).values():
            blob = " ".join(str(value) for value in row.values())
            if request.topic not in blob:
                continue
            for region in split_values(row.get("适用地区", "")) or ["未标"]:
                if request.regions and region not in set(request.regions):
                    continue
                item = grouped.setdefault(
                    region,
                    {
                        "区域": region,
                        "政策数量": 0,
                        "政策工具": {},
                        "适用主体": {},
                        "市场环节": {},
                        "价格机制": {},
                        "交易品种": {},
                        "规划场景": {},
                        "代表文件": [],
                    },
                )
                item["政策数量"] += 1
                if len(item["代表文件"]) < 5:
                    item["代表文件"].append(row.get("文件标题", ""))
                for field in ["政策工具", "适用主体", "市场环节", "价格机制", "交易品种", "规划场景"]:
                    for value in split_values(row.get(field, "")):
                        item[field][value] = item[field].get(value, 0) + 1
        rows = []
        for item in sorted(grouped.values(), key=lambda value: value["政策数量"], reverse=True):
            rows.append(
                {
                    "区域": item["区域"],
                    "政策数量": str(item["政策数量"]),
                    "政策工具": ";".join(f"{name}({count})" for name, count in sorted(item["政策工具"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "适用主体": ";".join(f"{name}({count})" for name, count in sorted(item["适用主体"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "市场环节": ";".join(f"{name}({count})" for name, count in sorted(item["市场环节"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "价格机制": ";".join(f"{name}({count})" for name, count in sorted(item["价格机制"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "交易品种": ";".join(f"{name}({count})" for name, count in sorted(item["交易品种"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "规划场景": ";".join(f"{name}({count})" for name, count in sorted(item["规划场景"].items(), key=lambda pair: pair[1], reverse=True)[:6]),
                    "代表文件": "；".join(item["代表文件"]),
                }
            )
    else:
        if request.regions:
            rows = [row for row in rows if row.get("区域") in set(request.regions)]
    rows = rows[: request.limit]
    return {
        "topic": request.topic,
        "regions": request.regions,
        "rows": rows,
        "markdown": markdown_table(rows, ["区域", "政策数量", "政策工具", "适用主体", "市场环节", "价格机制", "规划场景"]),
    }


@app.post("/api/research/export")
def research_export(request: ExportRequest) -> dict[str, Any]:
    assets = load_research_assets()
    export_type = request.export_type
    if export_type in {"citation", "citations", "引用清单"}:
        rows = assets.get("citations", [])
        fields = ["资料编号", "文件标题", "发布部门", "发布日期", "文号", "有效状态", "质量状态", "原文链接", "建议引用格式"]
    elif export_type in {"compare", "region_compare", "区域比较"}:
        rows = assets.get("region_compare", [])
        fields = ["区域", "政策数量", "政策工具", "适用主体", "市场环节", "价格机制", "规划场景"]
    elif export_type in {"variables", "政策变量"}:
        rows = list(assets.get("variables", {}).values())
        fields = ["资料编号", "文件标题", "政策工具", "适用主体", "市场环节", "价格机制", "规划场景", "质量状态"]
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported export_type: {request.export_type}")

    if request.doc_ids:
        wanted = set(request.doc_ids)
        rows = [row for row in rows if row.get("资料编号") in wanted]
    if request.query:
        rows = [row for row in rows if row_matches(row, request.query)]
    rows = rows[: request.limit]
    return {
        "export_type": export_type,
        "row_count": len(rows),
        "rows": rows,
        "markdown": markdown_table(rows, fields),
    }


def main() -> None:
    import uvicorn

    host = os.getenv("QA_HOST", "127.0.0.1")
    port = int(os.getenv("QA_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
