"""检查政策标题质量，并对明显异常标题给出修正建议。

标题是搜索、去重和规则清单的核心字段；本脚本会识别栏目名、空标题、过短标题等
问题，必要时从正文或本地文件名中提取更可靠的候选标题。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
BACKUP_DIR = ROOT / "02_元数据" / "备份"

OUTPUT_FIELDS = [
    "生成日期",
    "资料编号",
    "当前标题",
    "建议标题",
    "问题类型",
    "置信度",
    "建议动作",
    "建议来源",
    "发布部门",
    "采集来源机构",
    "发布日期",
    "适用地区",
    "来源类型",
    "市场主题",
    "原文链接",
    "本地文本路径",
    "说明",
]

TITLE_MARKERS = [
    "关于",
    "通知",
    "规则",
    "细则",
    "办法",
    "方案",
    "意见",
    "规定",
    "复函",
    "公告",
    "市场",
]

GENERIC_EXACT = {
    "广州电力交易中心",
    "山西省能源局",
    "云南省能源局",
    "四川省发展和改革委员会",
    "重庆市经济和信息化委员会行政规范性文件",
    "通知公告_ 内蒙古自治区能源局",
}

NAV_LINES = {
    "首页",
    "新闻资讯",
    "公司动态",
    "媒体关注",
    "市场研究",
    "信息发布",
    "最新通知",
    "公示公告",
    "市场服务",
    "市场培训",
    "服务承诺",
    "常见问题解答",
    "政策法规",
    "法律法规",
    "政策文件",
    "交易规则",
    "业务指引",
    "收费标准",
    "关于我们",
    "通知公告",
    "返回首页",
    "政府信息公开",
    "政务服务",
    "互动交流",
    "当前位置：",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def normalize(value: str) -> str:
    return re.sub(r"[\s《》〈〉“”\"'()（）\[\]【】,，。.;；:：、/\\_-]+", "", value or "")


def is_meaningful_title(value: str) -> bool:
    """判断标题是否像正式政策标题，而不是栏目名、导航名或过短噪声。"""
    value = clean_candidate(value)
    if len(value) < 8 or len(value) > 160:
        return False
    if value in NAV_LINES or value in GENERIC_EXACT:
        return False
    if re.fullmatch(r"W\d{10,}", value):
        return False
    return any(marker in value for marker in TITLE_MARKERS)


def clean_candidate(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "").strip()
    value = value.strip("_-—| ")
    value = re.sub(r"\.(docx?|pdf|html?|xlsx?)$", "", value, flags=re.I)
    value = re.sub(r"\s+\d{4}-\d{1,2}-\d{1,2}$", "", value)
    value = re.sub(r"\(发文字号：.*$", "", value).strip()
    value = re.sub(r"（发文字号：.*$", "", value).strip()
    value = re.sub(r"_国家能源局.*$", "", value).strip()
    value = re.sub(r"_国务院.*$", "", value).strip()
    value = re.sub(r"_中国政府网.*$", "", value).strip()
    if "】-" in value:
        value = value.split("】-", 1)[0] + "】"
    if value.startswith("【") and value.endswith("】"):
        value = value[1:-1].strip()
    return value


def note_candidate(note: str) -> str:
    note = clean_candidate(note)
    if not note:
        return ""
    # Some rows store 文号_部门_标题 in remarks. Prefer the real title starting at "关于".
    about_index = note.find("关于")
    if about_index > 0:
        trimmed = note[about_index:]
        if is_meaningful_title(trimmed):
            return trimmed
    return note if is_meaningful_title(note) else ""


def text_path(row: dict[str, str]) -> Path | None:
    for item in row.get("本地文件路径", "").split(";"):
        item = item.strip()
        if item.lower().endswith(".txt"):
            path = ROOT / item
            if path.exists():
                return path
    return None


def text_candidate(row: dict[str, str], max_lines: int = 100) -> tuple[str, str]:
    path = text_path(row)
    if not path:
        return "", ""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return "", str(path.relative_to(ROOT))

    for line in lines[:max_lines]:
        candidate = clean_candidate(line)
        if is_meaningful_title(candidate):
            return candidate, str(path.relative_to(ROOT))
    return "", str(path.relative_to(ROOT))


def title_issue_type(title: str) -> str:
    original = title or ""
    title = clean_candidate(title)
    issues: list[str] = []
    if title != original.strip():
        issues.append("标题可规则清洗")
    if title in GENERIC_EXACT:
        issues.append("机构名或栏目名标题")
    if re.fullmatch(r"W\d{10,}", title):
        issues.append("附件编号标题")
    if len(title) <= 8 and not is_meaningful_title(title):
        issues.append("标题过短")
    if re.match(r"^[一二三四五六七八九十]+[、.．]", title):
        issues.append("疑似正文小标题")
    if any(token in title for token in ["_中国政府网", "_国务院", "_国家能源局", "-国家发展和改革委员会"]):
        issues.append("标题含网站或栏目后缀")
    if "首页" in title or "当前位置" in title:
        issues.append("标题含导航信息")
    return ";".join(issues)


def find_issue(row: dict[str, str]) -> dict[str, str] | None:
    """定位单条台账记录的标题问题，并给出可人工复核的候选标题。"""
    current = row.get("文件标题", "").strip()
    current_clean = clean_candidate(current)
    issue_type = title_issue_type(current)

    if not issue_type:
        return None

    note_title = note_candidate(row.get("备注", ""))
    text_title, local_text = text_candidate(row)

    suggestion = ""
    source = ""
    confidence = "中"
    action = "人工确认后修正"
    explanation = ""

    if current_clean != current:
        suggestion = current_clean
        source = "标题规则清洗"
        confidence = "高"
        action = "建议自动修正"
        explanation = "当前标题可去除网站或栏目后缀等噪声"
    elif note_title and normalize(note_title) != normalize(current_clean):
        suggestion = note_title
        source = "备注字段"
        confidence = "高"
        action = "建议自动修正"
        explanation = "备注字段中存在更像正式文件标题的内容"
    elif text_title and normalize(text_title) != normalize(current_clean):
        suggestion = text_title
        source = "处理后文本"
        confidence = "中"
        explanation = "正文前部存在更像正式文件标题的内容"
    else:
        suggestion = ""
        source = "未找到"
        confidence = "低"
        explanation = "需要人工打开原文确认"

    return {
        "生成日期": dt.date.today().strftime("%Y-%m-%d"),
        "资料编号": row.get("资料编号", ""),
        "当前标题": current,
        "建议标题": suggestion,
        "问题类型": issue_type,
        "置信度": confidence,
        "建议动作": action,
        "建议来源": source,
        "发布部门": row.get("发布部门", ""),
        "采集来源机构": row.get("采集来源机构", ""),
        "发布日期": row.get("发布日期", ""),
        "适用地区": row.get("适用地区", ""),
        "来源类型": row.get("来源类型", ""),
        "市场主题": row.get("市场主题", ""),
        "原文链接": row.get("原文链接", ""),
        "本地文本路径": local_text,
        "说明": explanation,
    }


def audit_titles() -> list[dict[str, str]]:
    rows = read_csv(LEDGER_CSV)
    issues = [issue for row in rows if (issue := find_issue(row))]
    issues.sort(key=lambda row: (row["置信度"], row["发布日期"]), reverse=True)
    return issues


def backup_ledger() -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = BACKUP_DIR / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)
    backup_path = target_dir / LEDGER_CSV.name
    shutil.copy2(LEDGER_CSV, backup_path)
    return backup_path


def apply_safe_fixes(issues: list[dict[str, str]]) -> int:
    """只应用高置信、低风险的标题修正，其余保留给人工核验。"""
    rows = read_csv(LEDGER_CSV)
    by_id = {
        row["资料编号"]: row
        for row in issues
        if row.get("置信度") == "高" and row.get("建议标题") and row.get("建议动作") == "建议自动修正"
    }
    if not by_id:
        return 0

    changed = 0
    for row in rows:
        doc_id = row.get("资料编号", "")
        if doc_id in by_id:
            row["文件标题"] = by_id[doc_id]["建议标题"]
            changed += 1

    backup_ledger()
    write_csv(LEDGER_CSV, rows, list(rows[0].keys()))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit generic or noisy titles in 政策资料台账.csv.")
    parser.add_argument("--apply-safe", action="store_true", help="Apply high-confidence title fixes to ledger.")
    args = parser.parse_args()

    issues = audit_titles()
    output_path = OUTPUT_DIR / f"标题清洗建议_{dt.date.today():%Y%m%d}.csv"
    write_csv(output_path, issues, OUTPUT_FIELDS)

    high = sum(1 for row in issues if row["置信度"] == "高")
    medium = sum(1 for row in issues if row["置信度"] == "中")
    low = sum(1 for row in issues if row["置信度"] == "低")
    print(f"标题问题：{len(issues)} 条，高置信 {high}，中置信 {medium}，低置信 {low}")
    print(f"输出文件：{output_path}")
    for row in issues[:12]:
        print(f"- {row['资料编号']} | {row['问题类型']} | {row['当前标题']} -> {row['建议标题']}")

    if args.apply_safe:
        changed = apply_safe_fixes(issues)
        print(f"已应用高置信修正：{changed} 条")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
