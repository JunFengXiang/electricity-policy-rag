"""Local QA API for the electricity policy knowledge base."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from llm_client import LlmSettings, chat_completion, load_env_file


ROOT = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT / "05_输出成果" / "rag_index.pkl.gz"
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
app = FastAPI(title="电力政策知识库问答服务", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

_payload_cache: dict[str, Any] = {"mtime": None, "payload": None}


class AskRequest(BaseModel):
    query: str = Field(..., min_length=2)
    region: str = ""
    source_type: str = ""
    official_only: bool = True
    top_k: int = Field(default=DEFAULT_MAX_CONTEXT_CHUNKS, ge=1, le=20)
    candidate_pool: int = Field(default=300, ge=20, le=2000)


def load_payload() -> dict:
    mtime = INDEX_PATH.stat().st_mtime if INDEX_PATH.exists() else 0
    if _payload_cache["payload"] is None or _payload_cache["mtime"] != mtime:
        _payload_cache["payload"] = rag.load_index(INDEX_PATH)
        _payload_cache["mtime"] = mtime
    return _payload_cache["payload"]


def compact(text: str, max_len: int = 360) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def citation_payload(row: dict[str, str], rank: int) -> dict[str, str | int]:
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
        "llm_configured": bool(os.getenv("LLM_API_KEY")),
        "max_context_chunks": DEFAULT_MAX_CONTEXT_CHUNKS,
    }


@app.post("/api/ask")
def ask(request: AskRequest) -> dict[str, Any]:
    payload = load_payload()
    results = rag.search(
        payload,
        request.query,
        top_k=request.top_k,
        candidate_pool=request.candidate_pool,
        region=request.region,
        source_type=request.source_type,
        official_only=request.official_only,
    )
    warnings: list[str] = []
    if not results:
        return {
            "answer": "未找到足够依据。请尝试换用更具体的地区、主题、文号或政策名称。",
            "citations": [],
            "retrieval_hits": [],
            "warnings": ["no_retrieval_results"],
        }

    settings = LlmSettings.from_env()
    answer_results = results[: settings.max_context_chunks]
    citations = build_citations(answer_results)
    try:
        answer = chat_completion(build_prompt(request.query, answer_results), settings)
    except Exception as exc:
        warnings.append(f"llm_fallback: {type(exc).__name__}: {exc}")
        answer = fallback_answer(request.query, answer_results, str(exc))

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
    }


def main() -> None:
    import uvicorn

    host = os.getenv("QA_HOST", "127.0.0.1")
    port = int(os.getenv("QA_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
