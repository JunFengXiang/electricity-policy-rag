from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
META_DIR = ROOT / "02_元数据"
LEDGER_CSV = META_DIR / "政策资料台账.csv"
RULE_LIST_CSV = META_DIR / "规则清单.csv"
BACKUP_DIR = META_DIR / "备份"

DOC_NO_FIELD = "文号"
AFTER_FIELD = "发布日期"

RULE_FIELDS = [
    "序号",
    "资料编号",
    "规则名称",
    "文件标题",
    "发布机构",
    "采集来源机构",
    "发布日期",
    "文号",
    "省份",
    "原始适用范围",
    "市场主题",
    "来源类型",
    "权威等级",
    "有效状态",
    "是否原文",
    "原文链接",
    "本地文本路径",
    "备注",
]

PROVINCE_ORDER = [
    "全国",
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "内蒙古",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "广西",
    "海南",
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

PROVINCE_ALIASES = {
    "北京": ["北京", "北京市"],
    "天津": ["天津", "天津市"],
    "上海": ["上海", "上海市"],
    "重庆": ["重庆", "重庆市"],
    "河北": ["河北", "河北省"],
    "山西": ["山西", "山西省"],
    "内蒙古": ["内蒙古", "内蒙古自治区"],
    "辽宁": ["辽宁", "辽宁省"],
    "吉林": ["吉林", "吉林省"],
    "黑龙江": ["黑龙江", "黑龙江省"],
    "江苏": ["江苏", "江苏省"],
    "浙江": ["浙江", "浙江省"],
    "安徽": ["安徽", "安徽省"],
    "福建": ["福建", "福建省"],
    "江西": ["江西", "江西省"],
    "山东": ["山东", "山东省"],
    "河南": ["河南", "河南省"],
    "湖北": ["湖北", "湖北省"],
    "湖南": ["湖南", "湖南省"],
    "广东": ["广东", "广东省"],
    "广西": ["广西", "广西壮族自治区"],
    "海南": ["海南", "海南省"],
    "四川": ["四川", "四川省"],
    "贵州": ["贵州", "贵州省"],
    "云南": ["云南", "云南省"],
    "西藏": ["西藏", "西藏自治区"],
    "陕西": ["陕西", "陕西省"],
    "甘肃": ["甘肃", "甘肃省"],
    "青海": ["青海", "青海省"],
    "宁夏": ["宁夏", "宁夏回族自治区"],
    "新疆": ["新疆", "新疆维吾尔自治区"],
}

REGIONAL_PROVINCES = {
    "南方区域": ["广东", "广西", "云南", "贵州", "海南"],
    "华中区域": ["湖北", "湖南", "河南", "江西", "重庆", "四川", "西藏"],
    "华北区域": ["北京", "天津", "河北", "山西", "内蒙古"],
    "东北区域": ["辽宁", "吉林", "黑龙江", "内蒙古"],
    "西北区域": ["陕西", "甘肃", "青海", "宁夏", "新疆"],
    "华东区域": ["上海", "江苏", "浙江", "安徽", "福建", "山东"],
}

DOC_NO_PATTERNS = [
    re.compile(r"[一-龥A-Za-z]{1,18}〔20\d{2}〕\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}\[20\d{2}\]\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}【20\d{2}】\s*\d+\s*号"),
    re.compile(r"[一-龥]{2,18}令第\s*\d+\s*号"),
    re.compile(r"第\s*\d+\s*号令"),
    re.compile(r"[一-龥A-Za-z]{1,18}\s*\(\s*20\d{2}\s*\)\s*\d+\s*号"),
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def backup(path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = BACKUP_DIR / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / path.name
    shutil.copy2(path, target_path)
    return target_path


def insert_field(fields: list[str]) -> list[str]:
    if DOC_NO_FIELD in fields:
        return fields
    if AFTER_FIELD not in fields:
        return [*fields, DOC_NO_FIELD]
    index = fields.index(AFTER_FIELD) + 1
    return [*fields[:index], DOC_NO_FIELD, *fields[index:]]


def text_path(row: dict[str, str]) -> Path | None:
    for item in row.get("本地文件路径", "").split(";"):
        item = item.strip()
        if item.lower().endswith(".txt"):
            path = ROOT / item
            if path.exists():
                return path
    return None


def read_text_prefix(row: dict[str, str], limit: int = 50000) -> str:
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
    value = value.strip("()（）[]【】《》,，;；。")
    return value


def collect_doc_numbers(text: str, found: list[str], seen: set[str], max_count: int) -> None:
    if len(found) >= max_count:
        return
    for pattern in DOC_NO_PATTERNS:
        for match in pattern.findall(text):
            doc_no = clean_doc_no(match)
            if doc_no and doc_no not in seen:
                seen.add(doc_no)
                found.append(doc_no)
                if len(found) >= max_count:
                    return


def relevant_text_lines(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    selected: list[str] = []
    for index, line in enumerate(lines[:80]):
        if len(line) > 180:
            continue
        has_doc_no = any(pattern.search(line) for pattern in DOC_NO_PATTERNS)
        if not has_doc_no:
            continue
        if index <= 30:
            selected.append(line)
            continue
        if any(key in line for key in ["发文字号", "文号", "文件编号", "令第", "公布"]):
            selected.append(line)
    return "\n".join(selected)


def extract_doc_numbers(row: dict[str, str], max_count: int = 1) -> str:
    found: list[str] = []
    seen: set[str] = set()

    metadata_text = "\n".join(
        [
            row.get("文件标题", ""),
            row.get("备注", ""),
            row.get("摘要", ""),
        ]
    )
    collect_doc_numbers(metadata_text, found, seen, max_count)
    if len(found) < max_count:
        collect_doc_numbers(relevant_text_lines(read_text_prefix(row, limit=12000)), found, seen, max_count)
    return ";".join(found[:max_count])


def extract_rule_name(title: str) -> str:
    title = re.sub(r"^【|】.*$", "", title or "").strip()
    names = re.findall(r"《([^》]+)》", title)
    if names:
        return ";".join(dict.fromkeys(name.strip() for name in names if name.strip()))
    title = re.sub(r"^关于印发", "", title)
    title = re.sub(r"^关于发布", "", title)
    title = re.sub(r"^关于修订", "", title)
    title = re.sub(r"的通知.*$", "", title)
    title = re.sub(r"的复函.*$", "", title)
    title = title.strip(" _-—")
    return title or ""


def order_provinces(values: set[str]) -> list[str]:
    rank = {name: index for index, name in enumerate(PROVINCE_ORDER)}
    return sorted(values, key=lambda item: rank.get(item, 999))


def derive_provinces(row: dict[str, str]) -> str:
    raw_regions = [part.strip() for part in re.split(r"[;；,，、]+", row.get("适用地区", "")) if part.strip()]
    text = " ".join([row.get("文件标题", ""), row.get("备注", ""), row.get("适用地区", ""), row.get("市场主题", "")])
    provinces: set[str] = set()
    if "全国" in raw_regions:
        provinces.add("全国")
    for province, aliases in PROVINCE_ALIASES.items():
        if any(alias in text for alias in aliases):
            provinces.add(province)
    if not provinces:
        for region in raw_regions:
            provinces.update(REGIONAL_PROVINCES.get(region, []))
    return ";".join(order_provinces(provinces))


def text_path_value(row: dict[str, str]) -> str:
    path = text_path(row)
    return str(path.relative_to(ROOT)) if path else ""


def build_rule_list(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        output.append(
            {
                "序号": str(index),
                "资料编号": row.get("资料编号", ""),
                "规则名称": extract_rule_name(row.get("文件标题", "")),
                "文件标题": row.get("文件标题", ""),
                "发布机构": row.get("发布部门", ""),
                "采集来源机构": row.get("采集来源机构", ""),
                "发布日期": row.get("发布日期", ""),
                "文号": row.get(DOC_NO_FIELD, ""),
                "省份": derive_provinces(row),
                "原始适用范围": row.get("适用地区", ""),
                "市场主题": row.get("市场主题", ""),
                "来源类型": row.get("来源类型", ""),
                "权威等级": row.get("权威等级", ""),
                "有效状态": row.get("有效状态", ""),
                "是否原文": row.get("是否原文", ""),
                "原文链接": row.get("原文链接", ""),
                "本地文本路径": text_path_value(row),
                "备注": row.get("备注", ""),
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract document numbers and build 规则清单.csv.")
    parser.add_argument("--no-write-ledger", action="store_true", help="Only generate rule list, do not rewrite ledger.")
    parser.add_argument("--rebuild-doc-no", action="store_true", help="Recalculate 文号 even when the field already has a value.")
    parser.add_argument("--max-doc-no", type=int, default=1, help="Maximum document numbers stored per row.")
    args = parser.parse_args()

    rows = read_rows(LEDGER_CSV)
    fields = insert_field(read_header(LEDGER_CSV))

    changed = 0
    for row in rows:
        current = row.get(DOC_NO_FIELD, "").strip()
        if current and not args.rebuild_doc_no:
            continue
        doc_no = extract_doc_numbers(row, max_count=max(1, args.max_doc_no))
        row[DOC_NO_FIELD] = doc_no
        if doc_no != current:
            changed += 1

    if not args.no_write_ledger:
        backup_path = backup(LEDGER_CSV)
        write_rows(LEDGER_CSV, rows, fields)
        print(f"已回填文号：{changed} 条")
        print(f"备份文件：{backup_path}")
    else:
        print(f"预览：可回填文号 {changed} 条，未写回台账")

    rule_rows = build_rule_list(rows)
    write_rows(RULE_LIST_CSV, rule_rows, RULE_FIELDS)
    with_doc_no = sum(1 for row in rows if row.get(DOC_NO_FIELD, "").strip())
    print(f"规则清单：{RULE_LIST_CSV}")
    print(f"台账总数：{len(rows)}，已有文号：{with_doc_no}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
