from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
RULE_LIST_CSV = META_DIR / "规则清单.csv"
RELATION_CSV = META_DIR / "政策关联关系表.csv"

RELATION_FIELDS = [
    "关系编号",
    "新政策资料编号",
    "新政策标题",
    "新政策发布日期",
    "旧政策资料编号",
    "旧政策标题",
    "旧政策发布日期",
    "关联类型",
    "匹配依据",
    "证据文本",
    "置信度",
    "是否人工确认",
    "备注",
]

DOC_NO_PATTERNS = [
    re.compile(r"[一-龥A-Za-z]{1,18}〔20\d{2}〕\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}\[20\d{2}\]\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}【20\d{2}】\s*\d+\s*号"),
    re.compile(r"[一-龥]{2,18}令第\s*\d+\s*号"),
    re.compile(r"第\s*\d+\s*号令"),
]

RELATION_KEYWORDS = [
    ("废止", ["废止", "停止执行", "不再执行"]),
    ("替代", ["替代", "代替", "取代"]),
    ("修订", ["修订", "修正", "修改"]),
    ("延续", ["延续", "继续执行", "仍按"]),
    ("引用依据", ["根据", "依据", "按照", "参照", "贯彻", "落实", "结合"]),
]


def read_dicts(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_dicts(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_date(value: str) -> dt.date | None:
    match = re.match(r"(20\d{2})-(\d{2})-(\d{2})", value or "")
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def text_path(row: dict[str, str]) -> Path | None:
    for item in row.get("本地文件路径", "").split(";"):
        item = item.strip()
        if item.lower().endswith(".txt"):
            path = ROOT / item
            if path.exists():
                return path
    return None


def read_policy_text(row: dict[str, str], limit: int) -> str:
    path = text_path(row)
    if not path:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def clean_doc_no(value: str) -> str:
    value = re.sub(r"\s+", "", value or "")
    value = value.replace("[", "〔").replace("]", "〕").replace("【", "〔").replace("】", "〕")
    value = re.sub(r"^(发文字号|文号|文件编号)[:：]?", "", value)
    value = re.sub(r"^[年月日]+(?=国家|国务院|中华人民共和国)", "", value)
    return value.strip("()（）[]【】《》,，;；。")


def split_doc_numbers(value: str) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[;；,，、\s]+", value or ""):
        doc_no = clean_doc_no(part)
        if doc_no and doc_no not in seen:
            seen.add(doc_no)
            output.append(doc_no)
    return output


def extract_doc_numbers(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in DOC_NO_PATTERNS:
        for match in pattern.findall(text or ""):
            doc_no = clean_doc_no(match)
            if doc_no:
                found.add(doc_no)
    return found


def extract_quoted_names(text: str) -> set[str]:
    names: set[str] = set()
    for name in re.findall(r"《([^》]+)》", text or ""):
        cleaned = normalize_name(name)
        if is_meaningful_name(cleaned):
            names.add(cleaned)
    return names


def normalize_name(value: str) -> str:
    value = re.sub(r"\s+", "", value or "")
    value = value.strip("《》〈〉“”\"'()（）[]【】,，;；。")
    value = re.sub(r"[（(]征求意见稿[)）]$", "", value)
    value = re.sub(r"[（(]试行[)）]$", "（试行）", value)
    return value


def is_meaningful_name(value: str) -> bool:
    if len(value) < 6:
        return False
    if value in {"实施细则", "交易规则", "市场规则", "管理办法", "基本规则"}:
        return False
    return True


def clean_title_as_name(title: str) -> str:
    value = title or ""
    value = re.sub(r"_.*$", "", value)
    value = re.sub(r"-国家发展和改革委员会.*$", "", value)
    value = re.sub(r"_国家能源局.*$", "", value)
    value = re.sub(r"^【", "", value)
    value = re.sub(r"】.*$", "", value)
    value = re.sub(r"^关于印发", "", value)
    value = re.sub(r"^关于发布", "", value)
    value = re.sub(r"^关于修订", "", value)
    value = re.sub(r"^关于公开征求", "", value)
    value = re.sub(r"的通知.*$", "", value)
    value = re.sub(r"的复函.*$", "", value)
    value = re.sub(r"意见的通知.*$", "", value)
    return normalize_name(value)


def rule_name_map() -> dict[str, str]:
    output: dict[str, str] = {}
    for row in read_dicts(RULE_LIST_CSV):
        doc_id = row.get("资料编号", "")
        name = row.get("规则名称", "")
        if doc_id and name:
            output[doc_id] = name
    return output


def candidate_names(row: dict[str, str], names_by_id: dict[str, str]) -> list[str]:
    raw_names: list[str] = []
    raw_names.extend(re.findall(r"《([^》]+)》", row.get("文件标题", "")))
    raw_names.extend((names_by_id.get(row.get("资料编号", ""), "") or "").split(";"))
    raw_names.append(clean_title_as_name(row.get("文件标题", "")))

    output: list[str] = []
    seen: set[str] = set()
    for name in raw_names:
        cleaned = normalize_name(name)
        if not is_meaningful_name(cleaned) or cleaned in seen:
            continue
        seen.add(cleaned)
        output.append(cleaned)
    return output


def context_window(text: str, term: str, radius: int = 80) -> str:
    index = text.find(term)
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(text), index + len(term) + radius)
    snippet = text[start:end]
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet.strip()


def relation_type(evidence: str) -> str:
    for keyword in ["根据", "依据", "按照", "参照", "贯彻", "落实", "结合"]:
        if keyword in evidence:
            return "引用依据"
    for label, keywords in RELATION_KEYWORDS:
        if label == "引用依据":
            continue
        if any(keyword in evidence for keyword in keywords):
            return label
    return "明文引用"


def has_relation_context(evidence: str) -> bool:
    return any(keyword in evidence for _, keywords in RELATION_KEYWORDS for keyword in keywords)


def has_explicit_title_context(title: str, name: str) -> bool:
    if name not in title:
        return False
    if normalize_name(title) == normalize_name(name):
        return False
    strong_keywords = ["修订", "修正", "修改", "废止", "替代", "代替", "取代", "停止执行", "不再执行"]
    return any(keyword in title for keyword in strong_keywords)


def can_be_old_policy(new_row: dict[str, str], old_row: dict[str, str]) -> bool:
    if new_row.get("资料编号") == old_row.get("资料编号"):
        return False
    new_date = parse_date(new_row.get("发布日期", ""))
    old_date = parse_date(old_row.get("发布日期", ""))
    if new_date and old_date and old_date > new_date:
        return False
    return True


def build_policy_text(row: dict[str, str], text_limit: int) -> str:
    parts = [
        row.get("文件标题", ""),
        row.get("摘要", ""),
        row.get("备注", ""),
        read_policy_text(row, text_limit),
    ]
    return "\n".join(part for part in parts if part)


def find_relations(text_limit: int) -> list[dict[str, str]]:
    rows = read_dicts(LEDGER_CSV)
    names_by_id = rule_name_map()
    names_cache = {row.get("资料编号", ""): candidate_names(row, names_by_id) for row in rows}
    doc_no_cache = {row.get("资料编号", ""): split_doc_numbers(row.get("文号", "")) for row in rows}

    output: dict[tuple[str, str], dict[str, str]] = {}
    for new_row in rows:
        new_id = new_row.get("资料编号", "")
        policy_text = build_policy_text(new_row, text_limit)
        if not policy_text:
            continue
        quoted_names = extract_quoted_names(policy_text)
        cited_doc_numbers = extract_doc_numbers(policy_text)
        own_doc_numbers = set(doc_no_cache.get(new_id, []))

        for old_row in rows:
            old_id = old_row.get("资料编号", "")
            if not can_be_old_policy(new_row, old_row):
                continue

            match_basis = ""
            evidence_term = ""
            confidence = ""

            for doc_no in doc_no_cache.get(old_id, []):
                if doc_no in own_doc_numbers:
                    continue
                if doc_no and doc_no in cited_doc_numbers:
                    match_basis = f"文号：{doc_no}"
                    evidence_term = doc_no
                    confidence = "高"
                    break

            if not match_basis:
                for name in names_cache.get(old_id, []):
                    evidence_term = f"《{name}》" if f"《{name}》" in policy_text else name
                    evidence = context_window(policy_text, evidence_term)
                    if name in quoted_names:
                        if not has_relation_context(evidence):
                            continue
                        match_basis = f"规则名称：{name}"
                        confidence = "中"
                        break
                    if has_explicit_title_context(new_row.get("文件标题", ""), name):
                        match_basis = f"标题明文：{name}"
                        evidence_term = name
                        confidence = "中"
                        break

            if not match_basis:
                continue

            evidence = context_window(policy_text, evidence_term)
            key = (new_id, old_id)
            current = output.get(key)
            row = {
                "关系编号": "",
                "新政策资料编号": new_id,
                "新政策标题": new_row.get("文件标题", ""),
                "新政策发布日期": new_row.get("发布日期", ""),
                "旧政策资料编号": old_id,
                "旧政策标题": old_row.get("文件标题", ""),
                "旧政策发布日期": old_row.get("发布日期", ""),
                "关联类型": relation_type(evidence),
                "匹配依据": match_basis,
                "证据文本": evidence,
                "置信度": confidence,
                "是否人工确认": "否",
                "备注": "仅由新政策明文引用旧政策时生成",
            }
            if not current or (current.get("置信度") == "中" and confidence == "高"):
                output[key] = row

    rows_out = sorted(
        output.values(),
        key=lambda row: (
            row.get("新政策发布日期", ""),
            row.get("新政策资料编号", ""),
            row.get("旧政策发布日期", ""),
            row.get("旧政策资料编号", ""),
        ),
        reverse=True,
    )
    for index, row in enumerate(rows_out, start=1):
        row["关系编号"] = f"REL-{index:04d}"
    return rows_out


def main() -> int:
    parser = argparse.ArgumentParser(description="Build explicit policy citation relations.")
    parser.add_argument("--text-limit", type=int, default=160000, help="Max text characters read from each policy.")
    args = parser.parse_args()

    rows = find_relations(text_limit=args.text_limit)
    write_dicts(RELATION_CSV, rows, RELATION_FIELDS)
    high_count = sum(1 for row in rows if row.get("置信度") == "高")
    print(f"政策关联关系表：{RELATION_CSV}")
    print(f"关系总数：{len(rows)}，文号高置信：{high_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
