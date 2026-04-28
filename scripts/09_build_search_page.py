"""生成本地搜索页面和前端检索索引。

该脚本把政策台账、处理后文本和政策关联关系打包成一个静态 HTML，方便像搜索引擎
一样筛选政策，同时保留原文链接、本地文本和引用关系追溯。
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
OUTPUT_DIR = ROOT / "05_输出成果"
INDEX_PATH = OUTPUT_DIR / "search_index.json"
HTML_PATH = OUTPUT_DIR / "search.html"

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


def build_index(text_limit: int) -> dict:
    rows = read_csv(LEDGER_CSV)
    docs = []
    region_set: set[str] = set()
    province_set: set[str] = set()
    topic_set: set[str] = set()
    source_set: set[str] = set()
    authority_set: set[str] = set()
    status_set: set[str] = set()

    for row in rows:
        full_text, text_path = read_text_file(row, limit=text_limit)
        regions = split_values(row.get("适用地区", ""))
        provinces = derive_provinces(row, regions)
        topics = split_values(row.get("市场主题", ""))
        source_type = row.get("来源类型", "").strip()
        authority = row.get("权威等级", "").strip()
        status = row.get("有效状态", "").strip()

        region_set.update(regions)
        province_set.update(provinces)
        topic_set.update(topics)
        if source_type:
            source_set.add(source_type)
        if authority:
            authority_set.add(authority)
        if status:
            status_set.add(status)

        docs.append(
            {
                "id": row.get("资料编号", ""),
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
                "summary": row.get("摘要", ""),
                "note": row.get("备注", ""),
                "ingested_at": row.get("入库日期", ""),
                "review_status": row.get("审核状态", ""),
                "snippet": text_snippet(full_text),
                "text": normalize_space(full_text),
            }
        )

    valid_ids = {doc["id"] for doc in docs if doc.get("id")}
    outgoing, incoming = relation_maps(valid_ids)
    for doc in docs:
        doc["outgoing_relations"] = outgoing.get(doc["id"], [])[:8]
        doc["incoming_relations"] = incoming.get(doc["id"], [])[:8]

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
  <title>电力政策知识库搜索</title>
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
    input, select, button {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 6px;
      min-height: 38px;
      padding: 8px 10px;
      font: inherit;
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
    }}
  </style>
</head>
<body>
  <header>
    <h1>电力政策知识库搜索</h1>
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
      <div class="filter">
        <label for="authorityFilter">权威等级</label>
        <select id="authorityFilter"></select>
      </div>
      <div class="filter">
        <label for="statusFilter">有效状态</label>
        <select id="statusFilter"></select>
      </div>
      <button class="secondary" id="resetBtn" type="button">重置筛选</button>
    </aside>
    <section class="content">
      <div class="toolbar">
        <input id="queryInput" type="search" placeholder="输入关键词，例如：山东 辅助服务 结算" autocomplete="off">
        <select id="sortMode">
          <option value="score">综合排序</option>
          <option value="date">最新优先</option>
          <option value="authority">权威优先</option>
        </select>
        <button id="searchBtn" type="button">搜索</button>
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

    const els = {{
      query: document.getElementById('queryInput'),
      province: document.getElementById('provinceFilter'),
      topic: document.getElementById('topicFilter'),
      source: document.getElementById('sourceFilter'),
      authority: document.getElementById('authorityFilter'),
      status: document.getElementById('statusFilter'),
      sort: document.getElementById('sortMode'),
      results: document.getElementById('results'),
      count: document.getElementById('resultCount'),
      hint: document.getElementById('activeHint'),
      search: document.getElementById('searchBtn'),
      reset: document.getElementById('resetBtn')
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

    fillSelect(els.province, data.filters.provinces || [], '省份');
    fillSelect(els.topic, data.filters.topics || [], '主题');
    fillSelect(els.source, data.filters.source_types || [], '来源');
    fillSelect(els.authority, data.filters.authorities || [], '等级');
    fillSelect(els.status, data.filters.statuses || [], '状态');

    function norm(value) {{
      return String(value || '').toLowerCase().replace(/[\\s《》〈〉“”"'()（）\\[\\]【】,，。.;；:：、/\\\\_-]+/g, '');
    }}

    function terms(value) {{
      return String(value || '').split(/[;；,，、\\s]+/).map(v => v.trim()).filter(Boolean);
    }}

    function containsAny(values, selected) {{
      if (!selected) return true;
      const s = norm(selected);
      return values.some(value => {{
        const v = norm(value);
        return v.includes(s) || s.includes(v);
      }});
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
      const base = authorityScore(doc) + recencyBoost(doc.publish_date);
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

    function statusClass(value) {{
      if ((value || '').includes('征求')) return 'status-warn';
      if ((value || '').includes('废止')) return 'status-bad';
      return '';
    }}

    // search.html 位于 05_输出成果，处理后文本在上一级目录下，所以这里生成相对链接。
    function localTextHref(path) {{
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

      let rows = docs.map(doc => {{
          const match = scoreDoc(doc, queryTerms);
          return {{ doc, score: match.score, hits: match.hits, hitCount: match.hitCount }};
        }})
        .filter(item => queryTerms.length ? item.score > 0 : true)
        .filter(item => containsAny(item.doc.provinces || [], province))
        .filter(item => containsAny(item.doc.topics || [], topic))
        .filter(item => !source || item.doc.source_type === source)
        .filter(item => !authority || item.doc.authority === authority)
        .filter(item => !status || item.doc.status === status);

      if (els.sort.value === 'date') {{
        rows.sort((a, b) => dateScore(b.doc.publish_date) - dateScore(a.doc.publish_date) || b.score - a.score || b.hitCount - a.hitCount);
      }} else if (els.sort.value === 'authority') {{
        rows.sort((a, b) => authorityScore(b.doc) - authorityScore(a.doc) || b.score - a.score || b.hitCount - a.hitCount);
      }} else {{
        rows.sort((a, b) => b.score - a.score || b.hitCount - a.hitCount || dateScore(b.doc.publish_date) - dateScore(a.doc.publish_date));
      }}

      els.count.textContent = `${{rows.length}} 条结果`;
      const filters = [province, topic, source, authority, status].filter(Boolean);
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
      const localText = doc.text_path ? `<a href="${{escapeHtml(localTextHref(doc.text_path))}}" target="_blank" rel="noreferrer" title="${{escapeHtml(doc.text_path)}}">本地文本</a>` : '';
      const note = doc.note ? `<span class="tag">${{escapeHtml(doc.note).slice(0, 60)}}</span>` : '';
      const sourceLine = [doc.department, doc.collection_source].filter(Boolean).filter((v, i, arr) => arr.indexOf(v) === i).join(' / ') || '发布部门未知';
      const docNo = doc.document_number ? `<span class="mini-action">文号：${{escapeHtml(doc.document_number)}}</span>` : '';
      const relationBlock = relationHtml(doc);
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
            ${{provinceTags}}${{scopeTags}}${{topicTags}}${{sourceTag}}${{authTag}}${{statusTag}}${{note}}
          </div>
          <div class="snippet">${{highlight(doc.snippet || doc.summary || doc.department || '', queryTerms)}}</div>
          ${{relationBlock}}
          <div class="links">
            <a href="${{escapeHtml(doc.url)}}" target="_blank" rel="noreferrer">原文链接</a>
            ${{localText}}
            ${{docNo}}
            <span class="mini-action">${{escapeHtml(sourceLine)}} · ${{escapeHtml(doc.publish_date || '日期未知')}}</span>
          </div>
        </article>
      `;
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

    [els.province, els.topic, els.source, els.authority, els.status, els.sort].forEach(el => el.addEventListener('change', render));
    els.search.addEventListener('click', render);
    els.query.addEventListener('input', () => render());
    els.query.addEventListener('keydown', event => {{
      if (event.key === 'Enter') render();
    }});
    els.reset.addEventListener('click', () => {{
      els.query.value = '';
      [els.province, els.topic, els.source, els.authority, els.status].forEach(el => el.value = '');
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
      [els.province, els.topic, els.source, els.authority, els.status].forEach(el => el.value = '');
      els.sort.value = 'score';
      render();
      window.setTimeout(() => {{
        const target = document.getElementById(`doc-${{targetId}}`);
        if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
      }}, 0);
    }});

    const params = new URLSearchParams(window.location.search);
    if (params.has('q')) els.query.value = params.get('q') || '';
    if (params.has('province')) els.province.value = params.get('province') || '';
    if (params.has('region')) els.province.value = params.get('region') || '';
    if (params.has('topic')) els.topic.value = params.get('topic') || '';
    if (params.has('source')) els.source.value = params.get('source') || '';
    if (params.has('authority')) els.authority.value = params.get('authority') || '';
    if (params.has('status')) els.status.value = params.get('status') || '';
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
    for field in ["provinces", "regions", "topics", "source_types", "authorities", "statuses"]:
        if field not in index.get("filters", {}):
            errors.append(f"索引缺少筛选字段：{field}")
    if not html_path.exists() or html_path.stat().st_size < 10000:
        errors.append(f"HTML页面生成异常：{html_path}")
    if not json_path.exists() or json_path.stat().st_size < 1000:
        errors.append(f"JSON索引生成异常：{json_path}")
    probe = [doc for doc in index["documents"] if "辅助服务" in doc.get("title", "") or "辅助服务" in ";".join(doc.get("topics", []))]
    if not probe:
        errors.append("索引自检未找到辅助服务相关文档")
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
