from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
OUTPUT_DIR = ROOT / "05_输出成果"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
CHUNK_CSV = META_DIR / "知识切片表.csv"
CHUNK_JSONL = OUTPUT_DIR / "knowledge_chunks.jsonl"

FIELDS = [
    "切片编号",
    "资料编号",
    "切片序号",
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
    "字符数",
    "原文链接",
    "本地文本路径",
    "向量入库状态",
]

SECTION_RE = re.compile(
    r"^(第[一二三四五六七八九十百\d]+[章节条款]|[一二三四五六七八九十]+[、.．]|[(（][一二三四五六七八九十\d]+[)）]|[0-9]+[.．、])"
)

BOILERPLATE_TERMS = [
    "当前位置",
    "网站地图",
    "主办单位",
    "版权所有",
    "ICP备案",
    "联系我们",
    "分享到",
    "打印本页",
    "关闭窗口",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def split_paths(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def text_path(row: dict[str, str]) -> Path | None:
    for item in split_paths(row.get("本地文件路径", "")):
        if item.lower().endswith(".txt"):
            candidate = ROOT / item
            if candidate.exists():
                return candidate
    return None


def normalize_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line or "").strip()
    return line


def clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    seen_nearby: set[str] = set()
    for raw_line in text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            seen_nearby.clear()
            continue
        if any(term in line for term in BOILERPLATE_TERMS):
            continue
        if len(line) <= 2:
            continue
        key = re.sub(r"\W+", "", line)
        if key in seen_nearby:
            continue
        seen_nearby.add(key)
        if len(seen_nearby) > 40:
            seen_nearby.clear()
        lines.append(line)
    return lines


def split_long_text(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            boundary = max(text.rfind("。", start, end), text.rfind("；", start, end), text.rfind("\n", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        piece = text[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return pieces


def paragraph_chunks(text: str, max_chars: int, overlap: int, min_chars: int) -> list[tuple[str, str, str]]:
    lines = clean_lines(text)
    chunks: list[tuple[str, str, str]] = []
    buffer: list[str] = []
    section = ""

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        body = "\n".join(buffer).strip()
        buffer = []
        if len(body) < min_chars:
            return
        for piece in split_long_text(body, max_chars=max_chars, overlap=overlap):
            chunk_type = "条款" if "第" in piece[:12] and "条" in piece[:12] else "正文"
            chunks.append((section, chunk_type, piece))

    for line in lines:
        is_section = bool(SECTION_RE.match(line)) or (len(line) <= 36 and any(term in line for term in ["章", "规则", "细则", "办法", "方案"]))
        if is_section and buffer and sum(len(item) for item in buffer) >= min_chars:
            flush()
        if is_section:
            section = line[:80]
        if sum(len(item) for item in buffer) + len(line) + 1 > max_chars:
            flush()
        buffer.append(line)
    flush()
    return chunks


def parse_date(value: str) -> dt.date | None:
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def time_weight_level(publish_date: str) -> str:
    parsed = parse_date(publish_date)
    if not parsed:
        return "未知"
    days = max(0, (dt.date.today() - parsed).days)
    return str(min(days // 15 + 1, 99))


def retrieval_weight(row: dict[str, str], level: str) -> str:
    score = 50
    authority = row.get("权威等级", "")
    if authority == "A":
        score += 30
    elif authority == "B":
        score += 20
    elif authority == "C":
        score += 10
    if row.get("有效状态") == "现行有效":
        score += 10
    elif "征求意见" in row.get("有效状态", ""):
        score -= 8
    if level.isdigit():
        score += max(0, 15 - int(level))
    return str(max(0, min(score, 100)))


def chunk_id(doc_id: str, index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8].upper()
    return f"CHUNK-{doc_id}-{index:04d}-{digest}"


def build_chunks(max_chars: int, overlap: int, min_chars: int, per_doc_limit: int) -> list[dict[str, str]]:
    rows = read_csv(LEDGER_CSV)
    chunks: list[dict[str, str]] = []
    for row in rows:
        path = text_path(row)
        if not path:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        pieces = paragraph_chunks(text, max_chars=max_chars, overlap=overlap, min_chars=min_chars)
        if per_doc_limit:
            pieces = pieces[:per_doc_limit]
        doc_id = row.get("资料编号", "")
        level = time_weight_level(row.get("发布日期", ""))
        weight = retrieval_weight(row, level)
        rel_path = str(path.relative_to(ROOT))

        for index, (section, chunk_type, body) in enumerate(pieces, start=1):
            chunks.append(
                {
                    "切片编号": chunk_id(doc_id, index, body),
                    "资料编号": doc_id,
                    "切片序号": str(index),
                    "文件标题": row.get("文件标题", ""),
                    "发布机构": row.get("发布部门", ""),
                    "采集来源机构": row.get("采集来源机构", ""),
                    "发布日期": row.get("发布日期", ""),
                    "文号": row.get("文号", ""),
                    "适用地区": row.get("适用地区", ""),
                    "市场主题": row.get("市场主题", ""),
                    "来源类型": row.get("来源类型", ""),
                    "权威等级": row.get("权威等级", ""),
                    "有效状态": row.get("有效状态", ""),
                    "时间权重等级": level,
                    "检索权重": weight,
                    "切片类型": chunk_type,
                    "章节标题": section,
                    "正文片段": body,
                    "字符数": str(len(body)),
                    "原文链接": row.get("原文链接", ""),
                    "本地文本路径": rel_path,
                    "向量入库状态": "待入库",
                }
            )
    return chunks


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def self_check(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    ids = [row["切片编号"] for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("切片编号存在重复")
    if not rows:
        errors.append("未生成任何知识切片")
    short_count = sum(1 for row in rows if int(row.get("字符数", "0") or 0) < 20)
    if short_count:
        errors.append(f"存在过短切片：{short_count} 条")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build RAG-ready knowledge chunks from processed policy texts.")
    parser.add_argument("--max-chars", type=int, default=450, help="Target maximum characters per chunk.")
    parser.add_argument("--overlap", type=int, default=60, help="Character overlap when splitting long text.")
    parser.add_argument("--min-chars", type=int, default=80, help="Minimum chunk length.")
    parser.add_argument("--per-doc-limit", type=int, default=0, help="Maximum chunks per document; 0 means unlimited.")
    args = parser.parse_args()

    rows = build_chunks(
        max_chars=args.max_chars,
        overlap=args.overlap,
        min_chars=args.min_chars,
        per_doc_limit=args.per_doc_limit,
    )
    errors = self_check(rows)
    write_csv(CHUNK_CSV, rows)
    write_jsonl(CHUNK_JSONL, rows)

    by_doc: dict[str, int] = {}
    for row in rows:
        by_doc[row["资料编号"]] = by_doc.get(row["资料编号"], 0) + 1
    top_docs = sorted(by_doc.items(), key=lambda item: item[1], reverse=True)[:10]

    print(f"知识切片表：{CHUNK_CSV}")
    print(f"JSONL：{CHUNK_JSONL}")
    print(f"切片总数：{len(rows)}，覆盖文件：{len(by_doc)}")
    print("切片最多的文件：")
    for doc_id, count in top_docs:
        print(f"- {doc_id}: {count}")

    append_log(
        action_type="知识切片生成",
        content=f"从处理后文本生成 RAG 知识切片 {len(rows)} 条，覆盖文件 {len(by_doc)} 个",
        files=f"{CHUNK_CSV.relative_to(ROOT)}; {CHUNK_JSONL.relative_to(ROOT)}",
        command=" ".join([str(Path(sys.executable)), *sys.argv]),
        result="完成" if not errors else "完成但有提醒",
        note="；".join(errors) if errors else f"max_chars={args.max_chars}; overlap={args.overlap}; min_chars={args.min_chars}",
    )

    if errors:
        print("自检提醒：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
