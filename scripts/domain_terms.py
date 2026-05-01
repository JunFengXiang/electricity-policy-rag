"""Shared domain terms for candidate discovery, tagging, and RAG retrieval."""

from __future__ import annotations


CORE_POLICY_KEYWORDS = [
    "电力市场",
    "电力交易",
    "交易规则",
    "规则体系",
    "实施细则",
    "运营规则",
    "市场监管",
    "市场化交易",
    "年度交易",
    "月度交易",
    "中长期",
    "现货",
    "辅助服务",
    "调峰",
    "调频",
    "备用",
    "省内",
    "省间",
    "跨省",
    "跨区",
    "电能量",
    "容量电价",
    "机制电价",
    "上网电价",
    "输配电价",
    "计量结算",
    "结算",
    "绿电",
    "绿证",
    "源网荷储",
    "新型储能",
    "新能源上网",
    "新能源消纳",
    "高比例新能源",
    "注册",
]

STRATEGIC_KEYWORDS = [
    "高质量发展",
    "新型电力系统",
    "电网",
    "电网规划",
    "电网投资",
    "配电网",
    "主网",
    "微电网",
    "智能电网",
    "电力保供",
    "迎峰度夏",
    "迎峰度冬",
    "电力安全",
    "能源安全",
    "新型能源体系",
    "绿色低碳",
    "双碳",
    "碳达峰",
    "碳中和",
]

INTERPRETATION_KEYWORDS = [
    "政策解读",
    "官方解读",
    "答记者问",
    "答疑",
    "问答",
    "一图读懂",
    "新闻发布",
]

DEFAULT_CANDIDATE_KEYWORDS = CORE_POLICY_KEYWORDS + STRATEGIC_KEYWORDS + INTERPRETATION_KEYWORDS

HIGH_VALUE_TERMS = [
    "规则",
    "细则",
    "办法",
    "通知",
    "实施方案",
    "市场",
    "交易",
    "现货",
    "中长期",
    "辅助服务",
    "省间",
    "跨省",
    "容量电价",
    "输配电价",
    "绿电",
    "绿色电力",
    "结算",
    "需求响应",
    "新型储能",
    "配电网",
    "源网荷储",
    "新能源消纳",
    "市场化交易",
    "规则体系",
    "高质量发展",
    "新型电力系统",
    "电网",
    "电力保供",
]

POWER_RELEVANCE_TERMS = [
    "电力",
    "电价",
    "发电",
    "电网",
    "电能量",
    "新能源",
    "储能",
    "绿电",
    "绿证",
    "辅助服务",
    "调峰",
    "调频",
    "容量",
    "需求响应",
    "源网荷储",
    "售电",
    "购电",
    "用电",
    "输配电",
    "新型电力系统",
    "电力保供",
]

TOPIC_RULES = [
    ("高质量发展", ["高质量发展", "高质量", "新质生产力"]),
    ("电网", ["电网", "配电网", "主网", "微电网", "智能电网", "电网规划", "电网投资"]),
    ("新型电力系统", ["新型电力系统", "新型能源体系", "源网荷储", "高比例新能源"]),
    ("电力保供", ["电力保供", "迎峰度夏", "迎峰度冬", "电力安全", "能源安全"]),
    ("电力市场", ["电力市场", "市场规则", "市场监管", "市场化交易"]),
    ("中长期", ["中长期", "年度交易", "月度交易", "月内交易"]),
    ("现货", ["现货", "日前", "实时", "出清"]),
    ("辅助服务", ["辅助服务", "调峰", "调频", "备用", "爬坡", "黑启动"]),
    ("省间交易", ["省间", "跨省", "跨区"]),
    ("省内交易", ["省内"]),
    ("绿电", ["绿电", "绿证", "绿色电力"]),
    ("容量电价", ["容量电价", "容量补偿", "容量机制"]),
    ("需求响应", ["需求响应", "负荷聚合", "虚拟电厂"]),
    ("储能", ["储能", "新型储能", "独立储能", "共享储能"]),
    ("新能源", ["新能源", "风电", "光伏", "新能源上网", "新能源消纳"]),
    ("计量结算", ["计量结算", "结算", "偏差考核"]),
    ("输配电价", ["输配电价", "配电价格", "输电价格"]),
]

RAG_KNOWN_TERMS = list(dict.fromkeys(DEFAULT_CANDIDATE_KEYWORDS + [topic for topic, _ in TOPIC_RULES]))


def infer_topics_from_text(*values: str) -> str:
    text = ";".join(value or "" for value in values)
    topics: list[str] = []
    for topic, keys in TOPIC_RULES:
        if any(key in text for key in keys):
            topics.append(topic)
    return ";".join(topics or ["电力市场"])


def is_official_interpretation(title: str, source_name: str = "") -> bool:
    text = f"{title};{source_name}"
    return any(keyword in text for keyword in INTERPRETATION_KEYWORDS)
