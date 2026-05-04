"""生成本地搜索页面和前端检索索引。

该脚本把政策台账、处理后文本、网页快照和政策关联关系打包成一个静态 HTML，
方便像搜索引擎一样筛选政策，同时保留原文链接、本地快照和引用关系追溯。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
RELATION_CSV = ROOT / "02_元数据" / "政策关联关系表.csv"
VARIABLES_CSV = ROOT / "02_元数据" / "政策变量表.csv"
EVOLUTION_CSV = ROOT / "02_元数据" / "政策演化关系表.csv"
OUTPUT_DIR = ROOT / "05_输出成果"
INDEX_PATH = OUTPUT_DIR / "search_index.json"
HTML_PATH = OUTPUT_DIR / "search.html"
SNAPSHOT_DIR = OUTPUT_DIR / "网页快照"
SNAPSHOT_MANIFEST = SNAPSHOT_DIR / "snapshot_manifest.json"
PDF_ATTACHMENT_JSON = OUTPUT_DIR / "pdf_attachments.json"

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


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def split_values(value: str) -> list[str]:
    parts = re.split(r"[;；,，、]+", value or "")
    return [part.strip() for part in parts if part.strip()]


def order_provinces(values: set[str]) -> list[str]:
    rank = {name: index for index, name in enumerate(PROVINCE_ORDER)}
    return sorted(values, key=lambda name: rank.get(name, 999))


def derive_provinces(row: dict[str, str], regions: list[str]) -> list[str]:
    text = " ".join(
        [
            row.get("文件标题", ""),
            row.get("备注", ""),
            row.get("关键词", ""),
            row.get("市场主题", ""),
            row.get("适用地区", ""),
        ]
    )
    provinces: set[str] = set()
    if "全国" in regions:
        provinces.add("全国")

    for province, aliases in PROVINCE_ALIASES.items():
        if any(alias in text for alias in aliases):
            provinces.add(province)

    if provinces:
        return order_provinces(provinces)

    for region in regions:
        for province in REGIONAL_PROVINCES.get(region, []):
            provinces.add(province)

    return order_provinces(provinces)


def read_text_file(row: dict[str, str], limit: int) -> tuple[str, str]:
    text_path = ""
    for item in row.get("本地文件路径", "").split(";"):
        item = item.strip()
        if item.lower().endswith(".txt"):
            candidate = ROOT / item
            if candidate.exists():
                text_path = item
                try:
                    return candidate.read_text(encoding="utf-8", errors="ignore")[:limit], text_path
                except OSError:
                    return "", text_path
    return "", text_path


def snapshot_path(doc_id: str) -> str:
    if not doc_id:
        return ""
    safe_doc_id = re.sub(r'[\\/:*?"<>|]+', "_", doc_id)
    path = SNAPSHOT_DIR / f"{safe_doc_id}.html"
    if not path.exists():
        return ""
    return path.relative_to(OUTPUT_DIR).as_posix()


def read_snapshot_entries() -> dict[str, dict[str, str]]:
    if not SNAPSHOT_MANIFEST.exists():
        return {}
    try:
        data = json.loads(SNAPSHOT_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries: dict[str, dict[str, str]] = {}
    for entry in data.get("entries", []):
        doc_id = entry.get("doc_id", "")
        if doc_id:
            entries[doc_id] = entry
    return entries


def read_pdf_attachments() -> dict[str, list[dict[str, str]]]:
    if not PDF_ATTACHMENT_JSON.exists():
        return {}
    try:
        data = json.loads(PDF_ATTACHMENT_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    attachments: dict[str, list[dict[str, str]]] = {}
    for entry in data.get("entries", []):
        doc_id = entry.get("资料编号", "")
        path = entry.get("本地PDF路径", "")
        if doc_id and path and (ROOT / path).exists():
            attachments.setdefault(doc_id, []).append(
                {
                    "title": entry.get("附件标题", "") or "PDF附件",
                    "path": path,
                    "url": entry.get("附件URL", ""),
                    "status": entry.get("下载状态", ""),
                    "size": entry.get("文件大小", ""),
                }
            )
    return attachments


def local_pdf_attachments(row: dict[str, str]) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for item in row.get("本地文件路径", "").split(";"):
        path = item.strip()
        if path.lower().endswith(".pdf"):
            attachments.append(
                {
                    "title": Path(path).name,
                    "path": path,
                    "url": row.get("原文链接", "") if row.get("原文链接", "").lower().endswith(".pdf") else "",
                    "status": "local_pdf",
                    "size": "",
                }
            )
    return attachments


def merge_pdf_attachments(row: dict[str, str], manifest_items: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in [*manifest_items, *local_pdf_attachments(row)]:
        path = item.get("path", "").replace("\\", "/")
        if not path or path in seen:
            continue
        seen.add(path)
        item = dict(item)
        item["path"] = path
        merged.append(item)
    return merged


def snapshot_label(method: str) -> tuple[str, str]:
    if method == "online_full_page":
        return "官网长截图", "official"
    if method.startswith("simulated_page"):
        return "模拟截图", "simulated"
    if method.startswith("local_html"):
        return "本地长截图", "local"
    if method.startswith("pdf_render"):
        return "PDF截图", "pdf"
    if method:
        return "网页快照", "local"
    return "", ""


def text_snippet(text: str, limit: int = 220) -> str:
    compact = normalize_space(text)
    return compact[:limit] + ("..." if len(compact) > limit else "")


def relation_maps(valid_ids: set[str]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    outgoing: dict[str, list[dict]] = {}
    incoming: dict[str, list[dict]] = {}
    if not RELATION_CSV.exists():
        return outgoing, incoming

    for row in read_csv(RELATION_CSV):
        new_id = row.get("新政策资料编号", "")
        old_id = row.get("旧政策资料编号", "")
        if not new_id or not old_id or new_id not in valid_ids or old_id not in valid_ids:
            continue
        outgoing.setdefault(new_id, []).append(
            {
                "target_id": old_id,
                "target_title": row.get("旧政策标题", ""),
                "target_date": row.get("旧政策发布日期", ""),
                "relation_type": row.get("关联类型", ""),
                "basis": row.get("匹配依据", ""),
                "evidence": row.get("证据文本", ""),
                "confidence": row.get("置信度", ""),
            }
        )
        incoming.setdefault(old_id, []).append(
            {
                "target_id": new_id,
                "target_title": row.get("新政策标题", ""),
                "target_date": row.get("新政策发布日期", ""),
                "relation_type": row.get("关联类型", ""),
                "basis": row.get("匹配依据", ""),
                "evidence": row.get("证据文本", ""),
                "confidence": row.get("置信度", ""),
            }
        )
    return outgoing, incoming


def variable_maps() -> dict[str, dict[str, str]]:
    if not VARIABLES_CSV.exists():
        return {}
    return {row.get("资料编号", ""): row for row in read_csv(VARIABLES_CSV) if row.get("资料编号")}


def evolution_maps(valid_ids: set[str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    if not EVOLUTION_CSV.exists():
        return grouped
    for row in read_csv(EVOLUTION_CSV):
        source_id = row.get("源资料编号", "")
        target_id = row.get("目标资料编号", "")
        if source_id not in valid_ids and target_id not in valid_ids:
            continue
        relation = {
            "source_id": source_id,
            "source_title": row.get("源标题", ""),
            "source_date": row.get("源发布日期", ""),
            "target_id": target_id,
            "target_title": row.get("目标标题", ""),
            "target_date": row.get("目标发布日期", ""),
            "relation_type": row.get("关系类型", ""),
            "chain": row.get("主题链条", ""),
            "region_path": row.get("区域路径", ""),
            "basis": row.get("匹配依据", ""),
            "confidence": row.get("置信度", ""),
        }
        if source_id in valid_ids:
            grouped.setdefault(source_id, []).append(relation)
        if target_id in valid_ids:
            grouped.setdefault(target_id, []).append(relation)
    return grouped


def build_index(text_limit: int) -> dict:
    rows = read_csv(LEDGER_CSV)
    variable_entries = variable_maps()
    snapshot_entries = read_snapshot_entries()
    pdf_attachment_entries = read_pdf_attachments()
    docs = []
    region_set: set[str] = set()
    province_set: set[str] = set()
    topic_set: set[str] = set()
    source_set: set[str] = set()
    authority_set: set[str] = set()
    status_set: set[str] = set()
    review_status_set: set[str] = set()
    quality_status_set: set[str] = set()
    policy_tool_set: set[str] = set()
    subject_set: set[str] = set()
    market_segment_set: set[str] = set()
    pricing_set: set[str] = set()
    trading_product_set: set[str] = set()
    planning_scenario_set: set[str] = set()

    for row in rows:
        full_text, text_path = read_text_file(row, limit=text_limit)
        doc_id = row.get("资料编号", "")
        variable = variable_entries.get(doc_id, {})
        regions = split_values(row.get("适用地区", ""))
        provinces = derive_provinces(row, regions)
        topics = split_values(row.get("市场主题", ""))
        source_type = row.get("来源类型", "").strip()
        authority = row.get("权威等级", "").strip()
        status = row.get("有效状态", "").strip()
        review_status = row.get("审核状态", "").strip()
        snapshot = snapshot_entries.get(doc_id, {})
        snapshot_method = snapshot.get("method", "")
        snapshot_text, snapshot_kind = snapshot_label(snapshot_method)
        pdf_attachments = merge_pdf_attachments(row, pdf_attachment_entries.get(doc_id, []))
        quality_status = variable.get("质量状态", "未生成")
        policy_tools = split_values(variable.get("政策工具", ""))
        subjects = split_values(variable.get("适用主体", ""))
        market_segments = split_values(variable.get("市场环节", ""))
        pricing_mechanisms = split_values(variable.get("价格机制", ""))
        trading_products = split_values(variable.get("交易品种", ""))
        planning_scenarios = split_values(variable.get("规划场景", ""))

        region_set.update(regions)
        province_set.update(provinces)
        topic_set.update(topics)
        quality_status_set.add(quality_status)
        policy_tool_set.update(policy_tools)
        subject_set.update(subjects)
        market_segment_set.update(market_segments)
        pricing_set.update(pricing_mechanisms)
        trading_product_set.update(trading_products)
        planning_scenario_set.update(planning_scenarios)
        if source_type:
            source_set.add(source_type)
        if authority:
            authority_set.add(authority)
        if status:
            status_set.add(status)
        if review_status:
            review_status_set.add(review_status)

        docs.append(
            {
                "id": doc_id,
                "title": row.get("文件标题", ""),
                "department": row.get("发布部门", ""),
                "collection_source": row.get("采集来源机构", ""),
                "publish_date": row.get("发布日期", ""),
                "document_number": row.get("文号", ""),
                "regions": regions,
                "provinces": provinces,
                "source_type": source_type,
                "policy_level": row.get("政策层级", ""),
                "topics": topics,
                "keywords": split_values(row.get("关键词", "")),
                "authority": authority,
                "time_sensitivity": row.get("时间敏感类型", ""),
                "status": status,
                "is_original": row.get("是否原文", ""),
                "url": row.get("原文链接", ""),
                "local_paths": row.get("本地文件路径", ""),
                "text_path": text_path,
                "snapshot_path": snapshot_path(doc_id),
                "snapshot_method": snapshot_method,
                "snapshot_label": snapshot_text,
                "snapshot_kind": snapshot_kind,
                "pdf_attachments": pdf_attachments,
                "has_pdf": bool(pdf_attachments),
                "has_text": bool(text_path),
                "summary": row.get("摘要", ""),
                "note": row.get("备注", ""),
                "ingested_at": row.get("入库日期", ""),
                "review_status": review_status,
                "quality_status": quality_status,
                "policy_variables": {
                    "policy_tools": policy_tools,
                    "constraints": split_values(variable.get("约束机制", "")),
                    "incentives": split_values(variable.get("激励机制", "")),
                    "subjects": subjects,
                    "market_segments": market_segments,
                    "pricing_mechanisms": pricing_mechanisms,
                    "trading_products": trading_products,
                    "settlement_mechanisms": split_values(variable.get("结算机制", "")),
                    "assessment_mechanisms": split_values(variable.get("考核机制", "")),
                    "low_carbon_targets": split_values(variable.get("低碳目标", "")),
                    "planning_scenarios": planning_scenarios,
                    "investment_impacts": split_values(variable.get("投资影响", "")),
                    "business_model_impacts": split_values(variable.get("商业模式影响", "")),
                    "risk_constraints": split_values(variable.get("风险约束", "")),
                    "evidence": variable.get("证据摘录", ""),
                    "review_status": variable.get("人工复核状态", ""),
                },
                "snippet": text_snippet(full_text),
                "text": normalize_space(full_text),
            }
        )

    valid_ids = {doc["id"] for doc in docs if doc.get("id")}
    outgoing, incoming = relation_maps(valid_ids)
    evolutions = evolution_maps(valid_ids)
    for doc in docs:
        doc["outgoing_relations"] = outgoing.get(doc["id"], [])[:8]
        doc["incoming_relations"] = incoming.get(doc["id"], [])[:8]
        doc["evolution_relations"] = evolutions.get(doc["id"], [])[:12]

    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "document_count": len(docs),
        "relation_count": sum(len(items) for items in outgoing.values()),
        "filters": {
            "regions": sorted(region_set),
            "provinces": order_provinces(province_set),
            "topics": sorted(topic_set),
            "source_types": sorted(source_set),
            "authorities": sorted(authority_set),
            "statuses": sorted(status_set),
            "review_statuses": sorted(review_status_set),
            "quality_statuses": sorted(quality_status_set),
            "policy_tools": sorted(policy_tool_set),
            "subjects": sorted(subject_set),
            "market_segments": sorted(market_segment_set),
            "pricing_mechanisms": sorted(pricing_set),
            "trading_products": sorted(trading_product_set),
            "planning_scenarios": sorted(planning_scenario_set),
        },
        "documents": docs,
    }


def write_json(index: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def html_template(index: dict) -> str:
    embedded = json.dumps(index, ensure_ascii=False).replace("</", "<\\/")
    generated = html.escape(index.get("generated_at", ""))
    count = index.get("document_count", 0)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>电力政策研究与决策分析平台</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #5f6b7a;
      --line: #d8dee7;
      --blue: #1f6feb;
      --green: #287d4f;
      --amber: #9a6700;
      --red: #b42318;
      --tag: #edf2f7;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      color: var(--ink);
      font-size: 14px;
      line-height: 1.45;
    }}
    header {{
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    h1 {{
      margin: 0;
      font-size: 20px;
      font-weight: 700;
    }}
    .meta {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    main {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: calc(100vh - 57px);
    }}
    aside {{
      border-right: 1px solid var(--line);
      background: #fbfcfd;
      padding: 16px;
    }}
    .content {{
      padding: 16px 20px 28px;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(260px, 1fr) 132px 120px;
      gap: 10px;
      margin-bottom: 12px;
    }}
    input, select, button, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      min-height: 38px;
      padding: 8px 10px;
      font: inherit;
    }}
    textarea {{
      min-height: 92px;
      resize: vertical;
      line-height: 1.5;
    }}
    button {{
      background: var(--blue);
      color: white;
      border-color: var(--blue);
      cursor: pointer;
      font-weight: 600;
    }}
    button.secondary {{
      background: #fff;
      color: var(--ink);
      border-color: var(--line);
    }}
    label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin: 0 0 5px;
    }}
    .filter {{
      margin-bottom: 13px;
    }}
    .quick-filters {{
      display: grid;
      gap: 8px;
      margin: 2px 0 14px;
    }}
    .check-row {{
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      gap: 8px;
      align-items: center;
      color: #334155;
      font-size: 13px;
    }}
    .check-row input {{
      width: 18px;
      min-height: 18px;
      margin: 0;
      padding: 0;
    }}
    .counts {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border-bottom: 1px solid var(--line);
      padding: 0 0 10px;
      margin-bottom: 12px;
      color: var(--muted);
    }}
    .qa-panel {{
      background: #f8fbff;
      border: 1px solid #dbe8ff;
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 12px;
    }}
    .qa-actions {{
      display: grid;
      grid-template-columns: 120px minmax(220px, 1fr) minmax(0, 1fr);
      gap: 10px;
      align-items: center;
      margin-top: 8px;
    }}
    .qa-status {{
      color: var(--muted);
      font-size: 12px;
    }}
    .qa-answer {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 10px;
      padding: 10px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      display: none;
    }}
    .results {{
      display: grid;
      gap: 10px;
    }}
    .result {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 13px 14px;
    }}
    .result-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      margin-bottom: 8px;
    }}
    .title {{
      font-size: 16px;
      font-weight: 700;
      margin: 0;
      overflow-wrap: anywhere;
    }}
    .score {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .hit-detail {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 4px 0 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .hit-detail span {{
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 2px 6px;
    }}
    .fields {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 8px 0;
    }}
    .tag {{
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      background: var(--tag);
      border: 1px solid #e1e8f0;
      border-radius: 6px;
      color: #334155;
      font-size: 12px;
    }}
    .authority-a {{ color: var(--green); font-weight: 700; }}
    .status-warn {{ color: var(--amber); font-weight: 700; }}
    .status-bad {{ color: var(--red); font-weight: 700; }}
    .snapshot-official {{
      color: #0f766e;
      border-color: #9ad5cb;
      background: #ecfdf5;
      font-weight: 700;
    }}
    .snapshot-simulated {{
      color: #a16207;
      border-color: #f3cf7a;
      background: #fffbeb;
      font-weight: 700;
    }}
    .snapshot-local, .snapshot-pdf {{
      color: #1d4ed8;
      border-color: #bfdbfe;
      background: #eff6ff;
      font-weight: 700;
    }}
    .snippet {{
      color: #334155;
      margin: 8px 0 10px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    .relations {{
      display: grid;
      gap: 5px;
      margin: 8px 0 10px;
      font-size: 12px;
    }}
    .relation-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
    }}
    .relation-label {{
      color: var(--muted);
      min-width: 84px;
    }}
    .relation-link {{
      color: var(--blue);
      text-decoration: none;
      background: #f8fbff;
      border: 1px solid #dbe8ff;
      border-radius: 6px;
      padding: 4px 7px;
      max-width: 100%;
      overflow-wrap: anywhere;
    }}
    .links {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .links a, .mini-action {{
      color: var(--blue);
      text-decoration: none;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      padding: 5px 8px;
      font-size: 12px;
    }}
    .empty {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 28px;
      color: var(--muted);
      text-align: center;
    }}
    .lens-bar, .research-panel, .export-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      margin-bottom: 10px;
    }}
    .lens-btn {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      padding: 7px 10px;
      cursor: pointer;
      font-size: 13px;
    }}
    .lens-btn:hover {{
      border-color: var(--blue);
      color: var(--blue);
    }}
    .metric {{
      min-width: 126px;
      border-right: 1px solid var(--line);
      padding-right: 12px;
    }}
    .metric:last-child {{
      border-right: 0;
    }}
    .metric strong {{
      display: block;
      font-size: 18px;
      color: var(--ink);
    }}
    .metric span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .var-block, .evolution-block {{
      border-top: 1px dashed var(--line);
      padding-top: 8px;
      margin-top: 8px;
      color: var(--muted);
      font-size: 13px;
      display: grid;
      gap: 6px;
    }}
    .var-line, .evolution-line {{
      overflow-wrap: anywhere;
    }}
    mark {{
      background: #fff0a6;
      padding: 0 1px;
      border-radius: 2px;
    }}
    @media (max-width: 860px) {{
      header {{
        align-items: flex-start;
        flex-direction: column;
      }}
      main {{
        grid-template-columns: 1fr;
      }}
      aside {{
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      .toolbar {{
        grid-template-columns: 1fr;
      }}
      .qa-actions {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>电力政策研究与决策分析平台</h1>
    <div class="meta">资料 {count} 条 · 生成时间 {generated}</div>
  </header>
  <main>
    <aside>
      <div class="filter">
        <label for="provinceFilter">省份</label>
        <select id="provinceFilter"></select>
      </div>
      <div class="filter">
        <label for="topicFilter">主题</label>
        <select id="topicFilter"></select>
      </div>
      <div class="filter">
        <label for="sourceFilter">来源类型</label>
        <select id="sourceFilter"></select>
      </div>
      <div class="quick-filters" aria-label="实用筛选">
        <label class="check-row"><input id="hasPdfFilter" type="checkbox">有 PDF</label>
        <label class="check-row"><input id="officialOnlyFilter" type="checkbox">官方政策</label>
        <label class="check-row"><input id="regulatoryOnlyFilter" type="checkbox">监管规则</label>
        <label class="check-row"><input id="tradingOnlyFilter" type="checkbox">交易规则</label>
        <label class="check-row"><input id="hasTextFilter" type="checkbox">已抽文本</label>
        <label class="check-row"><input id="reviewPendingFilter" type="checkbox">待人工复核</label>
      </div>
      <div class="filter">
        <label for="authorityFilter">权威等级</label>
        <select id="authorityFilter"></select>
      </div>
      <div class="filter">
        <label for="statusFilter">有效状态</label>
        <select id="statusFilter"></select>
      </div>
      <div class="filter">
        <label for="reviewFilter">复核状态</label>
        <select id="reviewFilter"></select>
      </div>
      <div class="filter">
        <label for="qualityFilter">质量状态</label>
        <select id="qualityFilter"></select>
      </div>
      <div class="filter">
        <label for="toolFilter">政策工具</label>
        <select id="toolFilter"></select>
      </div>
      <div class="filter">
        <label for="subjectFilter">适用主体</label>
        <select id="subjectFilter"></select>
      </div>
      <div class="filter">
        <label for="segmentFilter">市场环节</label>
        <select id="segmentFilter"></select>
      </div>
      <div class="filter">
        <label for="pricingFilter">价格机制</label>
        <select id="pricingFilter"></select>
      </div>
      <div class="filter">
        <label for="tradingProductFilter">交易品种</label>
        <select id="tradingProductFilter"></select>
      </div>
      <div class="filter">
        <label for="scenarioFilter">规划场景</label>
        <select id="scenarioFilter"></select>
      </div>
      <button class="secondary" id="resetBtn" type="button">重置筛选</button>
    </aside>
    <section class="content">
      <div class="lens-bar" aria-label="研究视角">
        <button class="lens-btn" data-lens="national" type="button">全国视角</button>
        <button class="lens-btn" data-lens="regional" type="button">区域视角</button>
        <button class="lens-btn" data-lens="province" type="button">省份视角</button>
        <button class="lens-btn" data-lens="topic" type="button">专题视角</button>
      </div>
      <div class="research-panel">
        <div class="metric"><strong>{count}</strong><span>政策资料</span></div>
        <div class="metric"><strong>{index.get("relation_count", 0)}</strong><span>明文引用关系</span></div>
        <div class="metric"><strong>{sum(1 for doc in index.get("documents", []) if doc.get("policy_variables", {}).get("policy_tools"))}</strong><span>已抽政策变量</span></div>
        <div class="metric"><strong>{sum(1 for doc in index.get("documents", []) if doc.get("evolution_relations"))}</strong><span>有演化链资料</span></div>
      </div>
      <div class="export-links">
        <a href="政策工具分类表.csv" target="_blank" rel="noreferrer">政策工具分类表</a>
        <a href="区域比较表.csv" target="_blank" rel="noreferrer">区域比较表</a>
        <a href="政策强度时间趋势.csv" target="_blank" rel="noreferrer">政策强度时间趋势</a>
        <a href="引用清单.csv" target="_blank" rel="noreferrer">引用清单</a>
      </div>
      <div class="toolbar">
        <input id="queryInput" type="search" placeholder="输入关键词，例如：高质量发展 电网规划 新型电力系统" autocomplete="off">
        <select id="sortMode">
          <option value="score">综合排序</option>
          <option value="date">最新优先</option>
          <option value="authority">权威优先</option>
        </select>
        <button id="searchBtn" type="button">搜索</button>
      </div>
      <div class="qa-panel">
        <label for="qaInput">问答</label>
        <textarea id="qaInput" placeholder="输入政策问题，例如：高质量发展相关电力政策有哪些？"></textarea>
        <div class="qa-actions">
          <button id="askBtn" type="button">问答</button>
          <input id="apiBaseInput" type="url" value="http://127.0.0.1:8000" aria-label="问答 API 地址">
          <span class="qa-status" id="qaStatus">本地问答服务默认地址：http://127.0.0.1:8000</span>
        </div>
        <div class="qa-answer" id="qaAnswer"></div>
      </div>
      <div class="counts">
        <span id="resultCount">0 条结果</span>
        <span id="activeHint"></span>
      </div>
      <div class="results" id="results"></div>
    </section>
  </main>
  <script id="searchData" type="application/json">{embedded}</script>
  <script>
    const data = JSON.parse(document.getElementById('searchData').textContent);
    const docs = data.documents || [];
    const knownTerms = [
      '全国', '四川', '山东', '浙江', '山西', '广东', '南方区域', '西北区域',
      '华中区域', '华东区域', '电力市场', '交易规则', '实施细则', '市场规则',
      '规则体系', '高质量发展', '电网', '新型电力系统', '源网荷储', '电力保供',
      '电网规划', '输配电价', '新能源消纳', '新能源上网电价', '辅助服务', '现货',
      '中长期', '绿电', '信息披露', '山东电力市场规则', '广东电力市场规则', '四川辅助服务',
      '南方区域新能源', '跨电网经营区'
    ];

    const els = {{
      query: document.getElementById('queryInput'),
      province: document.getElementById('provinceFilter'),
      topic: document.getElementById('topicFilter'),
      source: document.getElementById('sourceFilter'),
      hasPdf: document.getElementById('hasPdfFilter'),
      officialOnly: document.getElementById('officialOnlyFilter'),
      regulatoryOnly: document.getElementById('regulatoryOnlyFilter'),
      tradingOnly: document.getElementById('tradingOnlyFilter'),
      hasText: document.getElementById('hasTextFilter'),
      reviewPending: document.getElementById('reviewPendingFilter'),
      authority: document.getElementById('authorityFilter'),
      status: document.getElementById('statusFilter'),
      review: document.getElementById('reviewFilter'),
      quality: document.getElementById('qualityFilter'),
      tool: document.getElementById('toolFilter'),
      subject: document.getElementById('subjectFilter'),
      segment: document.getElementById('segmentFilter'),
      pricing: document.getElementById('pricingFilter'),
      tradingProduct: document.getElementById('tradingProductFilter'),
      scenario: document.getElementById('scenarioFilter'),
      sort: document.getElementById('sortMode'),
      results: document.getElementById('results'),
      count: document.getElementById('resultCount'),
      hint: document.getElementById('activeHint'),
      search: document.getElementById('searchBtn'),
      reset: document.getElementById('resetBtn'),
      qaInput: document.getElementById('qaInput'),
      ask: document.getElementById('askBtn'),
      apiBase: document.getElementById('apiBaseInput'),
      qaStatus: document.getElementById('qaStatus'),
      qaAnswer: document.getElementById('qaAnswer')
    }};

    function fillSelect(select, values, label) {{
      select.innerHTML = '';
      const all = document.createElement('option');
      all.value = '';
      all.textContent = `全部${{label}}`;
      select.appendChild(all);
      values.forEach(value => {{
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      }});
    }}

    fillSelect(els.province, Array.from(new Set([...(data.filters.provinces || []), ...(data.filters.regions || [])])), '省份/区域');
    fillSelect(els.topic, data.filters.topics || [], '主题');
    fillSelect(els.source, data.filters.source_types || [], '来源');
    fillSelect(els.authority, data.filters.authorities || [], '等级');
    fillSelect(els.status, data.filters.statuses || [], '状态');
    fillSelect(els.review, data.filters.review_statuses || [], '复核状态');
    fillSelect(els.quality, data.filters.quality_statuses || [], '质量状态');
    fillSelect(els.tool, data.filters.policy_tools || [], '政策工具');
    fillSelect(els.subject, data.filters.subjects || [], '主体');
    fillSelect(els.segment, data.filters.market_segments || [], '市场环节');
    fillSelect(els.pricing, data.filters.pricing_mechanisms || [], '价格机制');
    fillSelect(els.tradingProduct, data.filters.trading_products || [], '交易品种');
    fillSelect(els.scenario, data.filters.planning_scenarios || [], '规划场景');

    function norm(value) {{
      return String(value || '').toLowerCase().replace(/[\\s《》〈〉“”"'()（）\\[\\]【】,，。.;；:：、/\\\\_-]+/g, '');
    }}

    function terms(value) {{
      const raw = String(value || '');
      const parts = raw.split(/[;；,，、\\s]+/).map(v => v.trim()).filter(Boolean);
      knownTerms.forEach(term => {{
        if (norm(raw).includes(norm(term))) parts.push(term);
      }});
      const seen = new Set();
      return parts.filter(item => {{
        const key = norm(item);
        if (!key || seen.has(key)) return false;
        seen.add(key);
        return true;
      }});
    }}

    function containsAny(values, selected) {{
      if (!selected) return true;
      const s = norm(selected);
      return values.some(value => {{
        const v = norm(value);
        return v.includes(s) || s.includes(v);
      }});
    }}

    function isOfficialPolicy(doc) {{
      const value = doc.source_type || '';
      return value.includes('官方') && !value.includes('解读');
    }}

    function isRegulatoryRule(doc) {{
      return (doc.source_type || '').includes('监管');
    }}

    function isTradingRule(doc) {{
      const value = doc.source_type || '';
      return value.includes('交易规则') || value.includes('交易中心') || value.includes('交易');
    }}

    function isPendingReview(doc) {{
      const value = `${{doc.review_status || ''}} ${{doc.note || ''}} ${{doc.status || ''}}`;
      return value.includes('待') || value.includes('复核') || value.includes('低分') || value.includes('征求');
    }}

    function dateScore(raw) {{
      if (!raw) return 0;
      const time = Date.parse(raw);
      return Number.isFinite(time) ? time / 86400000 : 0;
    }}

    // 权威分是搜索页的底座：先保证官方、监管、现行有效资料有合理优先级。
    function authorityScore(doc) {{
      let score = 0;
      if ((doc.authority || '').includes('A')) score += 7;
      else if ((doc.authority || '').includes('B')) score += 4;
      else if ((doc.authority || '').includes('C')) score += 2;
      if ((doc.source_type || '').includes('官方')) score += 3;
      if ((doc.source_type || '').includes('监管')) score += 3;
      if ((doc.source_type || '').includes('交易')) score += 2;
      if ((doc.status || '').includes('现行有效')) score += 2;
      return score;
    }}

    function countOccurrences(value, key) {{
      if (!key) return 0;
      const text = norm(value);
      let count = 0;
      let pos = 0;
      while ((pos = text.indexOf(key, pos)) !== -1) {{
        count += 1;
        pos += Math.max(1, key.length);
        if (count >= 20) break;
      }}
      return count;
    }}

    function recencyBoost(raw) {{
      if (!raw) return 0;
      const time = Date.parse(raw);
      if (!Number.isFinite(time)) return 0;
      const days = Math.max(0, (Date.now() - time) / 86400000);
      if (days <= 30) return 3.5;
      if (days <= 90) return 2.8;
      if (days <= 365) return 2.0;
      if (days <= 365 * 3) return 1.0;
      return 0.3;
    }}

    // 查询分在权威分之上叠加字段命中、出现次数和短语长度，用于减少大量同分结果。
    function scoreDoc(doc, queryTerms) {{
      const asksForInterpretation = queryTerms.some(term => term.includes('解读'));
      const interpretationPenalty = !asksForInterpretation && ((doc.title || '').includes('解读') || (doc.source_type || '').includes('解读')) ? -20 : 0;
      const base = authorityScore(doc) + recencyBoost(doc.publish_date) + interpretationPenalty;
      if (!queryTerms.length) return {{ score: base, hits: [], hitCount: 0 }};
      const fields = {{
        id: {{ label: '编号', value: doc.id, weight: 20 }},
        title: {{ label: '标题', value: doc.title, weight: 14 }},
        document_number: {{ label: '文号', value: doc.document_number, weight: 10 }},
        keywords: {{ label: '关键词', value: (doc.keywords || []).join(';'), weight: 8 }},
        topics: {{ label: '主题', value: (doc.topics || []).join(';'), weight: 7 }},
        provinces: {{ label: '省份', value: (doc.provinces || []).join(';'), weight: 6 }},
        regions: {{ label: '范围', value: (doc.regions || []).join(';'), weight: 3 }},
        department: {{ label: '发布部门', value: doc.department, weight: 4 }},
        collection_source: {{ label: '来源机构', value: doc.collection_source, weight: 4 }},
        policy_tools: {{ label: '政策工具', value: (doc.policy_variables?.policy_tools || []).join(';'), weight: 8 }},
        subjects: {{ label: '适用主体', value: (doc.policy_variables?.subjects || []).join(';'), weight: 7 }},
        market_segments: {{ label: '市场环节', value: (doc.policy_variables?.market_segments || []).join(';'), weight: 7 }},
        pricing: {{ label: '价格机制', value: (doc.policy_variables?.pricing_mechanisms || []).join(';'), weight: 7 }},
        trading_products: {{ label: '交易品种', value: (doc.policy_variables?.trading_products || []).join(';'), weight: 7 }},
        planning_scenarios: {{ label: '规划场景', value: (doc.policy_variables?.planning_scenarios || []).join(';'), weight: 7 }},
        investment: {{ label: '投资影响', value: (doc.policy_variables?.investment_impacts || []).join(';'), weight: 5 }},
        business: {{ label: '商业模式', value: (doc.policy_variables?.business_model_impacts || []).join(';'), weight: 5 }},
        risks: {{ label: '风险约束', value: (doc.policy_variables?.risk_constraints || []).join(';'), weight: 5 }},
        pdf_attachments: {{ label: 'PDF附件', value: (doc.pdf_attachments || []).map(item => item.title || item.path || '').join(';'), weight: 4 }},
        note: {{ label: '备注', value: doc.note, weight: 4 }},
        snippet: {{ label: '摘要', value: doc.snippet, weight: 3 }},
        text: {{ label: '正文', value: doc.text, weight: 1.2 }}
      }};
      let score = base;
      let hitCount = 0;
      const hitMap = new Map();
      queryTerms.forEach(term => {{
        const key = norm(term);
        if (!key) return;
        Object.values(fields).forEach(field => {{
          const occurrences = countOccurrences(field.value, key);
          if (occurrences) {{
            const phraseBonus = key.length >= 6 ? 1.8 : key.length >= 4 ? 1.2 : 0;
            const repeatBonus = Math.min(4, Math.log2(occurrences + 1) * 1.3);
            score += field.weight + phraseBonus + repeatBonus;
            hitCount += occurrences;
            const existing = hitMap.get(field.label) || {{ label: field.label, terms: new Set(), count: 0 }};
            existing.terms.add(term);
            existing.count += occurrences;
            hitMap.set(field.label, existing);
          }}
        }});
      }});
      if (!hitMap.size) return {{ score: 0, hits: [], hitCount: 0 }};
      const titlePhrase = queryTerms.some(term => norm(doc.title).includes(norm(term)) && norm(term).length >= 4);
      if (titlePhrase) score += 4;
      const docNoHit = queryTerms.some(term => norm(doc.document_number).includes(norm(term)) && norm(term).length >= 4);
      if (docNoHit) score += 3;
      const parsedDate = Date.parse(doc.publish_date || '');
      const dateTiebreaker = Number.isFinite(parsedDate) ? (parsedDate % 997) / 1000 : 0;
      score += Math.min(2.5, hitCount * 0.08) + dateTiebreaker;
      const hits = Array.from(hitMap.values()).map(item => ({{
        label: item.label,
        terms: Array.from(item.terms).slice(0, 4),
        count: item.count
      }}));
      return {{ score, hits, hitCount }};
    }}

    function highlight(text, queryTerms) {{
      let safe = escapeHtml(text || '');
      queryTerms.filter(t => t.length >= 2).forEach(term => {{
        const escaped = term.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
        safe = safe.replace(new RegExp(escaped, 'gi'), match => `<mark>${{match}}</mark>`);
      }});
      return safe;
    }}

    function escapeHtml(value) {{
      return String(value || '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    }}

    function renderQaPayload(payload) {{
      const lines = [];
      lines.push(payload.answer || '未返回答案');
      if (payload.citations && payload.citations.length) {{
        lines.push('', '引用来源：');
        payload.citations.slice(0, 8).forEach(item => {{
          const docNo = item.document_number ? `，${{item.document_number}}` : '';
          lines.push(`[${{item.rank}}] ${{item.title}}（${{item.department || '机构未知'}}，${{item.publish_date || '日期未知'}}${{docNo}}）`);
          if (item.url) lines.push(`    ${{item.url}}`);
        }});
      }}
      if (payload.warnings && payload.warnings.length) {{
        lines.push('', `提示：${{payload.warnings.join('；')}}`);
      }}
      return lines.join('\\n');
    }}

    async function askQuestion() {{
      const query = els.qaInput.value.trim() || els.query.value.trim();
      if (!query) {{
        els.qaStatus.textContent = '请先输入问题';
        return;
      }}
      els.qaStatus.textContent = '正在检索并生成回答...';
      els.qaAnswer.style.display = 'block';
      els.qaAnswer.textContent = '';
      try {{
        const apiBase = (els.apiBase.value || 'http://127.0.0.1:8000').replace(/\\/+$/, '');
        const response = await fetch(`${{apiBase}}/api/ask`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            query,
            region: els.province.value,
            source_type: els.source.value,
            official_only: true,
            top_k: 8,
            answer_mode: 'research'
          }})
        }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const payload = await response.json();
        els.qaAnswer.textContent = renderQaPayload(payload);
        els.qaStatus.textContent = payload.warnings && payload.warnings.length ? '已返回检索草稿或降级答案' : '已返回问答结果';
      }} catch (error) {{
        els.qaAnswer.textContent = `无法连接问答服务：${{els.apiBase.value}}\\n错误：${{error}}`;
        els.qaStatus.textContent = '本地问答服务不可用';
      }}
    }}

    function statusClass(value) {{
      if ((value || '').includes('征求')) return 'status-warn';
      if ((value || '').includes('废止')) return 'status-bad';
      return '';
    }}

    function snapshotClass(kind) {{
      if (kind === 'official') return 'snapshot-official';
      if (kind === 'simulated') return 'snapshot-simulated';
      if (kind === 'pdf') return 'snapshot-pdf';
      if (kind === 'local') return 'snapshot-local';
      return '';
    }}

    // search.html 与网页快照都位于 05_输出成果 下；处理后文本则在上一级目录。
    function outputHref(path) {{
      if (!path) return '';
      const normalized = String(path).replace(/\\\\/g, '/');
      return encodeURI(normalized);
    }}

    function rootFileHref(path) {{
      if (!path) return '';
      const normalized = String(path).replace(/\\\\/g, '/');
      return encodeURI(`../${{normalized}}`);
    }}

    function render() {{
      const queryTerms = terms(els.query.value);
      const province = els.province.value;
      const topic = els.topic.value;
      const source = els.source.value;
      const authority = els.authority.value;
      const status = els.status.value;
      const review = els.review.value;
      const quality = els.quality.value;
      const tool = els.tool.value;
      const subject = els.subject.value;
      const segment = els.segment.value;
      const pricing = els.pricing.value;
      const tradingProduct = els.tradingProduct.value;
      const scenario = els.scenario.value;

      let rows = docs.map(doc => {{
          const match = scoreDoc(doc, queryTerms);
          return {{ doc, score: match.score, hits: match.hits, hitCount: match.hitCount }};
        }})
        .filter(item => queryTerms.length ? item.score > 0 : true)
        .filter(item => containsAny(item.doc.provinces || [], province))
        .filter(item => containsAny(item.doc.topics || [], topic))
        .filter(item => !source || item.doc.source_type === source)
        .filter(item => !els.hasPdf.checked || item.doc.has_pdf)
        .filter(item => !els.officialOnly.checked || isOfficialPolicy(item.doc))
        .filter(item => !els.regulatoryOnly.checked || isRegulatoryRule(item.doc))
        .filter(item => !els.tradingOnly.checked || isTradingRule(item.doc))
        .filter(item => !els.hasText.checked || item.doc.has_text)
        .filter(item => !els.reviewPending.checked || isPendingReview(item.doc))
        .filter(item => !authority || item.doc.authority === authority)
        .filter(item => !status || item.doc.status === status)
        .filter(item => !review || item.doc.review_status === review)
        .filter(item => !quality || item.doc.quality_status === quality)
        .filter(item => containsAny(item.doc.policy_variables?.policy_tools || [], tool))
        .filter(item => containsAny(item.doc.policy_variables?.subjects || [], subject))
        .filter(item => containsAny(item.doc.policy_variables?.market_segments || [], segment))
        .filter(item => containsAny(item.doc.policy_variables?.pricing_mechanisms || [], pricing))
        .filter(item => containsAny(item.doc.policy_variables?.trading_products || [], tradingProduct))
        .filter(item => containsAny(item.doc.policy_variables?.planning_scenarios || [], scenario));

      if (els.sort.value === 'date') {{
        rows.sort((a, b) => dateScore(b.doc.publish_date) - dateScore(a.doc.publish_date) || b.score - a.score || b.hitCount - a.hitCount);
      }} else if (els.sort.value === 'authority') {{
        rows.sort((a, b) => authorityScore(b.doc) - authorityScore(a.doc) || b.score - a.score || b.hitCount - a.hitCount);
      }} else {{
        rows.sort((a, b) => b.score - a.score || b.hitCount - a.hitCount || dateScore(b.doc.publish_date) - dateScore(a.doc.publish_date));
      }}

      els.count.textContent = `${{rows.length}} 条结果`;
      const quickFilters = [
        els.hasPdf.checked ? '有 PDF' : '',
        els.officialOnly.checked ? '官方政策' : '',
        els.regulatoryOnly.checked ? '监管规则' : '',
        els.tradingOnly.checked ? '交易规则' : '',
        els.hasText.checked ? '已抽文本' : '',
        els.reviewPending.checked ? '待人工复核' : ''
      ];
      const filters = [province, topic, source, authority, status, review, quality, tool, subject, segment, pricing, tradingProduct, scenario, ...quickFilters].filter(Boolean);
      els.hint.textContent = filters.length ? filters.join(' / ') : '';

      const limited = rows.slice(0, 80);
      els.results.innerHTML = limited.length ? limited.map((item, index) => resultHtml(item.doc, item.score, index + 1, queryTerms)).join('') : '<div class="empty">没有匹配结果</div>';
    }}

    // 结果卡片只展示面向用户的短标签，长路径等追溯信息放入链接或悬停提示。
    function resultHtml(doc, score, rank, queryTerms) {{
      const provinceTags = (doc.provinces || []).map(v => `<span class="tag">${{escapeHtml(v)}}</span>`).join('');
      const scopeTags = (doc.regions || []).filter(v => !(doc.provinces || []).includes(v)).map(v => `<span class="tag">适用范围：${{escapeHtml(v)}}</span>`).join('');
      const topicTags = (doc.topics || []).map(v => `<span class="tag">${{escapeHtml(v)}}</span>`).join('');
      const sourceTag = `<span class="tag">${{escapeHtml(doc.source_type || '来源未知')}}</span>`;
      const authTag = `<span class="tag authority-a">等级 ${{escapeHtml(doc.authority || '未标')}}</span>`;
      const statusTag = `<span class="tag ${{statusClass(doc.status)}}">${{escapeHtml(doc.status || '状态未知')}}</span>`;
      const pdfTag = doc.has_pdf ? '<span class="tag">有 PDF</span>' : '';
      const textTag = doc.has_text ? '<span class="tag">已抽文本</span>' : '';
      const reviewTag = doc.review_status ? `<span class="tag">复核：${{escapeHtml(doc.review_status)}}</span>` : '';
      const qualityTag = doc.quality_status ? `<span class="tag">质量：${{escapeHtml(doc.quality_status)}}</span>` : '';
      const snapshotTitle = [doc.snapshot_path, doc.snapshot_label, doc.snapshot_method].filter(Boolean).join(' · ');
      const snapshotLink = doc.snapshot_path ? `<a href="${{escapeHtml(outputHref(doc.snapshot_path))}}" target="_blank" rel="noreferrer" title="${{escapeHtml(snapshotTitle)}}">网页快照</a>` : '';
      const textFallback = !snapshotLink && doc.text_path ? `<a href="${{escapeHtml(rootFileHref(doc.text_path))}}" target="_blank" rel="noreferrer" title="${{escapeHtml(doc.text_path)}}">处理后文本</a>` : '';
      const archiveLink = snapshotLink || textFallback;
      const pdfLinks = (doc.pdf_attachments || []).map((item, idx) => {{
        const label = (doc.pdf_attachments || []).length > 1 ? `PDF附件${{idx + 1}}` : 'PDF附件';
        const title = [item.title, item.path, item.url].filter(Boolean).join(' · ');
        const href = item.path ? rootFileHref(item.path) : item.url;
        return href ? `<a href="${{escapeHtml(href)}}" target="_blank" rel="noreferrer" title="${{escapeHtml(title)}}">${{label}}</a>` : '';
      }}).filter(Boolean).join('');
      const snapshotTag = doc.snapshot_label ? `<span class="tag ${{snapshotClass(doc.snapshot_kind)}}">快照：${{escapeHtml(doc.snapshot_label)}}</span>` : '';
      const note = doc.note ? `<span class="tag">${{escapeHtml(doc.note).slice(0, 60)}}</span>` : '';
      const sourceLine = [doc.department, doc.collection_source].filter(Boolean).filter((v, i, arr) => arr.indexOf(v) === i).join(' / ') || '发布部门未知';
      const docNo = doc.document_number ? `<span class="mini-action">文号：${{escapeHtml(doc.document_number)}}</span>` : '';
      const relationBlock = relationHtml(doc);
      const variableBlock = variableHtml(doc);
      const evolutionBlock = evolutionHtml(doc);
      const hitDetails = scoreDoc(doc, queryTerms).hits;
      const hitDetail = hitDetails && hitDetails.length
        ? `<div class="hit-detail">${{hitDetails.slice(0, 5).map(hit => `<span>${{escapeHtml(hit.label)}}：${{escapeHtml(hit.terms.join('、'))}}${{hit.count > 1 ? ' x' + hit.count : ''}}</span>`).join('')}}</div>`
        : '';
      return `
        <article class="result" id="doc-${{escapeHtml(doc.id)}}">
          <div class="result-head">
            <h2 class="title">${{rank}}. ${{highlight(doc.title, queryTerms)}}</h2>
            <div class="score">得分 ${{score.toFixed(1)}}</div>
          </div>
          ${{hitDetail}}
          <div class="fields">
            ${{provinceTags}}${{scopeTags}}${{topicTags}}${{sourceTag}}${{authTag}}${{statusTag}}${{qualityTag}}${{pdfTag}}${{textTag}}${{reviewTag}}${{snapshotTag}}${{note}}
          </div>
          <div class="snippet">${{highlight(doc.snippet || doc.summary || doc.department || '', queryTerms)}}</div>
          ${{variableBlock}}
          ${{relationBlock}}
          ${{evolutionBlock}}
          <div class="links">
            <a href="${{escapeHtml(doc.url)}}" target="_blank" rel="noreferrer">原文链接</a>
            ${{archiveLink}}
            ${{pdfLinks}}
            ${{docNo}}
            <span class="mini-action">${{escapeHtml(sourceLine)}} · ${{escapeHtml(doc.publish_date || '日期未知')}}</span>
          </div>
        </article>
      `;
    }}

    function line(label, values) {{
      const items = (values || []).filter(Boolean).slice(0, 6);
      return items.length ? `<div class="var-line"><strong>${{escapeHtml(label)}}：</strong>${{items.map(escapeHtml).join('；')}}</div>` : '';
    }}

    function variableHtml(doc) {{
      const vars = doc.policy_variables || {{}};
      const rows = [
        line('政策工具', vars.policy_tools),
        line('适用主体', vars.subjects),
        line('市场环节', vars.market_segments),
        line('价格机制', vars.pricing_mechanisms),
        line('规划场景', vars.planning_scenarios),
        line('投资影响', vars.investment_impacts),
        line('商业模式', vars.business_model_impacts),
        line('风险约束', vars.risk_constraints)
      ].filter(Boolean);
      if (!rows.length) return '';
      return `<div class="var-block">${{rows.join('')}}</div>`;
    }}

    function evolutionHtml(doc) {{
      const rows = (doc.evolution_relations || []).slice(0, 4).map(rel => {{
        const otherId = rel.source_id === doc.id ? rel.target_id : rel.source_id;
        const otherTitle = rel.source_id === doc.id ? rel.target_title : rel.source_title;
        const text = `${{rel.relation_type || '关联'}}：${{otherTitle || otherId}}${{rel.region_path ? ' · ' + rel.region_path : ''}}`;
        const title = [rel.chain, rel.basis, rel.confidence ? '置信度：' + rel.confidence : ''].filter(Boolean).join('；');
        return `<div class="evolution-line"><a class="relation-link" href="#doc-${{escapeHtml(otherId)}}" data-jump-doc="${{escapeHtml(otherId)}}" title="${{escapeHtml(title)}}">${{escapeHtml(text)}}</a></div>`;
      }});
      return rows.length ? `<div class="evolution-block">${{rows.join('')}}</div>` : '';
    }}

    // 政策关联只展示已入库且能定位到资料编号的关系，点击后回到对应结果卡片。
    function relationHtml(doc) {{
      const rows = [];
      if ((doc.outgoing_relations || []).length) {{
        rows.push(relationRow('明文引用', doc.outgoing_relations));
      }}
      if ((doc.incoming_relations || []).length) {{
        rows.push(relationRow('被后续引用', doc.incoming_relations));
      }}
      return rows.length ? `<div class="relations">${{rows.join('')}}</div>` : '';
    }}

    function relationRow(label, relations) {{
      const links = relations.map(rel => {{
        const text = `${{rel.relation_type || '关联'}}：${{rel.target_title || rel.target_id}}${{rel.target_date ? '（' + rel.target_date + '）' : ''}}`;
        const title = [rel.basis, rel.evidence].filter(Boolean).join('；');
        return `<a class="relation-link" href="#doc-${{escapeHtml(rel.target_id)}}" data-jump-doc="${{escapeHtml(rel.target_id)}}" title="${{escapeHtml(title)}}">${{escapeHtml(text)}}</a>`;
      }}).join('');
      return `<div class="relation-row"><span class="relation-label">${{escapeHtml(label)}}</span>${{links}}</div>`;
    }}

    [els.province, els.topic, els.source, els.authority, els.status, els.review, els.quality, els.tool, els.subject, els.segment, els.pricing, els.tradingProduct, els.scenario, els.sort].forEach(el => el.addEventListener('change', render));
    [els.hasPdf, els.officialOnly, els.regulatoryOnly, els.tradingOnly, els.hasText, els.reviewPending].forEach(el => el.addEventListener('change', render));
    els.search.addEventListener('click', render);
    els.ask.addEventListener('click', askQuestion);
    els.query.addEventListener('input', () => render());
    els.query.addEventListener('keydown', event => {{
      if (event.key === 'Enter') render();
    }});
    els.reset.addEventListener('click', () => {{
      els.query.value = '';
      [els.province, els.topic, els.source, els.authority, els.status, els.review, els.quality, els.tool, els.subject, els.segment, els.pricing, els.tradingProduct, els.scenario].forEach(el => el.value = '');
      [els.hasPdf, els.officialOnly, els.regulatoryOnly, els.tradingOnly, els.hasText, els.reviewPending].forEach(el => el.checked = false);
      els.sort.value = 'score';
      render();
    }});
    document.addEventListener('click', event => {{
      const link = event.target.closest('[data-jump-doc]');
      if (!link) return;
      event.preventDefault();
      const targetId = link.getAttribute('data-jump-doc') || '';
      if (!targetId) return;
      els.query.value = targetId;
      [els.province, els.topic, els.source, els.authority, els.status, els.review, els.quality, els.tool, els.subject, els.segment, els.pricing, els.tradingProduct, els.scenario].forEach(el => el.value = '');
      [els.hasPdf, els.officialOnly, els.regulatoryOnly, els.tradingOnly, els.hasText, els.reviewPending].forEach(el => el.checked = false);
      els.sort.value = 'score';
      render();
      window.setTimeout(() => {{
        const target = document.getElementById(`doc-${{targetId}}`);
        if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}, 0);
    }});
    document.querySelectorAll('[data-lens]').forEach(button => {{
      button.addEventListener('click', () => {{
        const lens = button.getAttribute('data-lens');
        [els.province, els.topic, els.source, els.authority, els.status, els.review, els.quality, els.tool, els.subject, els.segment, els.pricing, els.tradingProduct, els.scenario].forEach(el => el.value = '');
        if (lens === 'national') {{
          els.province.value = '全国';
          els.query.value = els.query.value || '高质量发展 新型电力系统 电网规划';
        }} else if (lens === 'regional') {{
          els.province.value = '南方区域';
          els.query.value = els.query.value || '区域市场 中长期 结算';
        }} else if (lens === 'province') {{
          els.query.value = els.query.value || '电力市场 分布式光伏 储能';
        }} else if (lens === 'topic') {{
          els.scenario.value = els.scenario.value || '新型电力系统';
          els.query.value = els.query.value || '源网荷储 微电网 储能';
        }}
        render();
      }});
    }});

    const params = new URLSearchParams(window.location.search);
    if (params.has('q')) els.query.value = params.get('q') || '';
    if (params.has('province')) els.province.value = params.get('province') || '';
    if (params.has('region')) els.province.value = params.get('region') || '';
    if (params.has('topic')) els.topic.value = params.get('topic') || '';
    if (params.has('source')) els.source.value = params.get('source') || '';
    if (params.has('authority')) els.authority.value = params.get('authority') || '';
    if (params.has('status')) els.status.value = params.get('status') || '';
    if (params.has('review')) els.review.value = params.get('review') || '';
    if (params.has('api')) els.apiBase.value = params.get('api') || els.apiBase.value;
    if (params.has('quality')) els.quality.value = params.get('quality') || '';
    if (params.has('tool')) els.tool.value = params.get('tool') || '';
    if (params.has('subject')) els.subject.value = params.get('subject') || '';
    if (params.has('segment')) els.segment.value = params.get('segment') || '';
    if (params.has('pricing')) els.pricing.value = params.get('pricing') || '';
    if (params.has('trading_product')) els.tradingProduct.value = params.get('trading_product') || '';
    if (params.has('scenario')) els.scenario.value = params.get('scenario') || '';
    if (params.has('has_pdf')) els.hasPdf.checked = params.get('has_pdf') === '1';
    if (params.has('official')) els.officialOnly.checked = params.get('official') === '1';
    if (params.has('regulatory')) els.regulatoryOnly.checked = params.get('regulatory') === '1';
    if (params.has('trading')) els.tradingOnly.checked = params.get('trading') === '1';
    if (params.has('has_text')) els.hasText.checked = params.get('has_text') === '1';
    if (params.has('pending_review')) els.reviewPending.checked = params.get('pending_review') === '1';
    if (params.has('sort')) els.sort.value = params.get('sort') || 'score';

    render();
  </script>
</body>
</html>
"""


def write_html(index: dict, path: Path) -> None:
    path.write_text(html_template(index), encoding="utf-8")


def self_check(index: dict, html_path: Path, json_path: Path) -> list[str]:
    errors: list[str] = []
    if not index.get("documents"):
        errors.append("索引没有文档")
    for field in [
        "provinces",
        "regions",
        "topics",
        "source_types",
        "authorities",
        "statuses",
        "review_statuses",
        "quality_statuses",
        "policy_tools",
        "subjects",
        "market_segments",
        "pricing_mechanisms",
        "trading_products",
        "planning_scenarios",
    ]:
        if field not in index.get("filters", {}):
            errors.append(f"索引缺少筛选字段：{field}")
    if not html_path.exists() or html_path.stat().st_size < 10000:
        errors.append(f"HTML页面生成异常：{html_path}")
    if not json_path.exists() or json_path.stat().st_size < 1000:
        errors.append(f"JSON索引生成异常：{json_path}")
    probe = [doc for doc in index["documents"] if "辅助服务" in doc.get("title", "") or "辅助服务" in ";".join(doc.get("topics", []))]
    if not probe:
        errors.append("索引自检未找到辅助服务相关文档")
    if not any(doc.get("has_pdf") for doc in index["documents"]):
        errors.append("索引自检未找到带 PDF 的文档")
    if not any(doc.get("has_text") for doc in index["documents"]):
        errors.append("索引自检未找到已抽文本的文档")
    if VARIABLES_CSV.exists() and not any(doc.get("policy_variables", {}).get("policy_tools") for doc in index["documents"]):
        errors.append("索引自检未找到政策变量")
    if EVOLUTION_CSV.exists() and not any(doc.get("evolution_relations") for doc in index["documents"]):
        errors.append("索引自检未找到政策演化关系")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Build search_index.json and local search.html.")
    parser.add_argument("--text-limit", type=int, default=50000, help="Max processed text characters per document.")
    args = parser.parse_args()

    index = build_index(text_limit=args.text_limit)
    write_json(index, INDEX_PATH)
    write_html(index, HTML_PATH)

    errors = self_check(index, HTML_PATH, INDEX_PATH)
    if errors:
        print("搜索页面生成自检失败：")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"已生成索引：{INDEX_PATH}")
    print(f"已生成页面：{HTML_PATH}")
    print(f"文档数：{index['document_count']}")
    print("自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
