from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import json
import pickle
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
CHUNK_CSV = ROOT / "02_元数据" / "知识切片表.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
INDEX_PATH = OUTPUT_DIR / "rag_index.pkl.gz"
SUMMARY_PATH = OUTPUT_DIR / "rag_index.summary.json"


def read_chunks(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def chunk_text(row: dict[str, str]) -> str:
    weighted_parts = [
        row.get("文件标题", ""),
        row.get("文件标题", ""),
        row.get("章节标题", ""),
        row.get("适用地区", ""),
        row.get("市场主题", ""),
        row.get("发布机构", ""),
        row.get("采集来源机构", ""),
        row.get("文号", ""),
        row.get("正文片段", ""),
    ]
    return "\n".join(part for part in weighted_parts if part).strip()


def compact_row(row: dict[str, str]) -> dict[str, str]:
    fields = [
        "切片编号",
        "资料编号",
        "文件标题",
        "发布机构",
        "采集来源机构",
        "发布日期",
        "文号",
        "适用地区",
        "市场主题",
        "来源类型",
        "权威等级",
        "有效状态",
        "时间权重等级",
        "检索权重",
        "切片类型",
        "章节标题",
        "正文片段",
        "原文链接",
        "本地文本路径",
    ]
    return {field: row.get(field, "") for field in fields}


def build_index(chunks: list[dict[str, str]], max_features: int):
    import numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer

    rows: list[dict[str, str]] = []
    corpus: list[str] = []
    for row in chunks:
        text = chunk_text(row)
        if len(text) < 20:
            continue
        rows.append(compact_row(row))
        corpus.append(text)

    vectorizer = TfidfVectorizer(
        analyzer="char",
        ngram_range=(2, 4),
        max_features=max_features,
        min_df=1,
        max_df=0.95,
        sublinear_tf=True,
        norm="l2",
        dtype=np.float32,
    )
    matrix = vectorizer.fit_transform(corpus)
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_csv": str(CHUNK_CSV.relative_to(ROOT)),
        "row_count": len(rows),
        "feature_count": len(vectorizer.vocabulary_),
        "vectorizer": vectorizer,
        "matrix": matrix,
        "rows": rows,
    }
    return payload


def write_index(payload: dict, index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(index_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)


def write_summary(payload: dict, summary_path: Path) -> None:
    by_source_type: dict[str, int] = {}
    by_authority: dict[str, int] = {}
    for row in payload["rows"]:
        source_type = row.get("来源类型", "") or "未标注"
        authority = row.get("权威等级", "") or "未标注"
        by_source_type[source_type] = by_source_type.get(source_type, 0) + 1
        by_authority[authority] = by_authority.get(authority, 0) + 1

    summary = {
        "generated_at": payload["generated_at"],
        "source_csv": payload["source_csv"],
        "row_count": payload["row_count"],
        "feature_count": payload["feature_count"],
        "index_path": str(INDEX_PATH.relative_to(ROOT)),
        "by_source_type": dict(sorted(by_source_type.items(), key=lambda item: item[1], reverse=True)),
        "by_authority": dict(sorted(by_authority.items())),
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a local RAG retrieval index from 知识切片表.csv.")
    parser.add_argument("--chunk-csv", default=str(CHUNK_CSV), help="Knowledge chunk CSV path.")
    parser.add_argument("--output", default=str(INDEX_PATH), help="Output index path.")
    parser.add_argument("--max-features", type=int, default=80000, help="TF-IDF max feature count.")
    args = parser.parse_args()

    chunk_csv = Path(args.chunk_csv)
    index_path = Path(args.output)
    chunks = read_chunks(chunk_csv)
    payload = build_index(chunks, max_features=args.max_features)
    write_index(payload, index_path)
    write_summary(payload, SUMMARY_PATH)

    print(f"RAG索引：{index_path}")
    print(f"摘要：{SUMMARY_PATH}")
    print(f"切片数：{payload['row_count']}，特征数：{payload['feature_count']}")

    append_log(
        action_type="RAG索引构建",
        content=f"基于知识切片表构建本地RAG检索索引，切片 {payload['row_count']} 条",
        files=f"{chunk_csv.relative_to(ROOT)}; {index_path.relative_to(ROOT)}; {SUMMARY_PATH.relative_to(ROOT)}",
        command=f"{Path(__file__).name} --max-features={args.max_features}",
        result="完成",
        note="TF-IDF char 2-4gram，本地离线检索索引",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
