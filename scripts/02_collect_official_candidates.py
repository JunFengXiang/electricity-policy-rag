"""从来源清单中的官方栏目页发现候选政策链接。

这个脚本不直接入库，而是生成候选池，供后续去重、筛选后再回填到
待采集链接表。它承担“扩大来源覆盖面”的探索任务。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import shutil
import subprocess
import json
import re
import ssl
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "05_输出成果"
SOURCE_CSV = ROOT / "02_元数据" / "来源清单.csv"

# 默认关键词只用于“候选发现”，不是最终分类依据；最终主题仍由后续清洗脚本确认。
DEFAULT_KEYWORDS = [
    "电力市场",
    "交易规则",
    "中长期",
    "现货",
    "辅助服务",
    "省内",
    "省间",
    "容量电价",
    "计量结算",
    "结算",
    "绿电",
    "源网荷储",
    "新能源",
    "获得电力",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

OUTPUT_FIELDS = [
    "抓取日期",
    "来源编号",
    "来源层级",
    "地区",
    "来源栏目",
    "发布部门",
    "文件标题",
    "发布日期",
    "命中关键词",
    "原文链接",
    "来源列表页",
]

NAV_TITLES = {
    "首页",
    "政务公开",
    "政策",
    "通知",
    "公告",
    "规范性文件",
    "政策文件",
    "政策法规",
    "工作动态",
    "通知公告",
    "政策解读",
    "信息公开",
}

COMMON_TITLE_SKIP = [
    "答记者问",
    "一图读懂",
    "专家解读",
    "新闻发言人",
    "媒体解读",
    "媒体聚焦",
    "工作动态",
    "领导活动",
    "巡视",
    "整改",
    "招聘",
    "招标",
    "结果公示",
    "事故",
    "调查报告",
    "依法公开",
    "风机",
    "节能报告",
    "审查意见",
    "终止",
    "示范项目",
    "外债",
    "天然气",
    "专项资金",
    "资金目录",
    "奖励项目",
    "新能源汽车",
    "获得电力",
    "营商环境",
    "许可公告",
    "许可证",
    "行政处罚",
    "承装",
    "名单",
    "公示新增",
    "活动",
    "会议",
    "数据清单",
    "结算数据清单",
]

CUSTOM_SOURCE_FILTERS = {
    "SRC-N-001": {
        "url_must_not_contain": ["/xxgk/jd/", "/xwdt/"],
    },
    "SRC-N-002": {
        "url_must_not_contain": ["/xxgk/jd/", "/xwdt/"],
    },
    "SRC-R-001": {
        "title_must_not_contain": ["巡视", "整改进展"],
    },
    "SRC-R-002": {
        "title_must_not_contain": ["巡视", "整改进展"],
    },
    "SRC-R-003": {
        "title_must_not_contain": ["巡视", "整改进展"],
    },
}

TITLE_KEYS = [
    "title",
    "gk_doctitle",
    "zc_doctitle",
    "informationNewsMainTitle",
    "bt",
    "name",
    "articleTitle",
    "docTitle",
    "policyTitle",
    "xxbt",
    "cname",
]
URL_KEYS = [
    "url",
    "docpuburl",
    "staticHtmlUrl",
    "informationNewsLink",
    "link",
    "href",
    "articleUrl",
    "xxnr_url",
    "wjgl",
    "wjlj",
    "xxlj",
]
DATE_KEYS = [
    "scrq",
    "docpubtime",
    "informationNewsPublishTime",
    "publishTime",
    "pubDate",
    "fbrq",
    "date",
    "riqi",
    "sj",
    "publishDate",
    "releaseTime",
    "gxsj",
]


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_a = False
        self.current_href = ""
        self.current_parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "a":
            return
        self.in_a = True
        self.current_href = dict(attrs).get("href", "")
        self.current_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self.in_a:
            return
        text = " ".join("".join(self.current_parts).split())
        if text:
            self.links.append((text, self.current_href))
        self.in_a = False
        self.current_href = ""
        self.current_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_a:
            self.current_parts.append(data)


def fetch_bytes_with_curl(url: str, timeout: int) -> tuple[bytes, str]:
    if not shutil.which("curl.exe"):
        raise RuntimeError("curl.exe not found")
    result = subprocess.run(
        [
            "curl.exe",
            "-k",
            "-L",
            "--compressed",
            "--max-time",
            str(max(timeout, 1)),
            "-A",
            USER_AGENT,
            url,
        ],
        check=False,
        capture_output=True,
        timeout=timeout + 5,
    )
    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", errors="ignore").strip()
        raise RuntimeError(f"curl fallback failed: {stderr or result.returncode}")
    return result.stdout, ""


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def canonical_domain(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def build_allowed_domains(*urls: str) -> set[str]:
    domains = set()
    for url in urls:
        if url:
            domains.add(canonical_domain(url))
    return domains


def detect_fetch_mode(raw_mode: str) -> str | None:
    """把来源清单中的采集方式文字折算成脚本可执行的抓取模式。"""
    if "JSON列表" in raw_mode:
        return "json"
    if "HTML列表" in raw_mode or "公开平台" in raw_mode:
        return "html"
    return None


def load_source_configs(source_file: Path) -> list[dict[str, str]]:
    configs: list[dict[str, str]] = []
    for row in read_csv_rows(source_file):
        if row.get("是否纳入自动采集") != "是":
            continue
        if row.get("状态") != "启用":
            continue
        fetch_mode = detect_fetch_mode(row.get("采集方式", ""))
        if not fetch_mode:
            continue

        config = {
            "source_id": row["来源编号"],
            "source_level": row["来源层级"],
            "region": row["地区"],
            "source_name": row["来源名称"],
            "source_type": row["来源类型"],
            "department": row["主管/发布主体"],
            "list_url": row["优先入口URL"],
            "main_url": row["主站URL"],
            "fetch_mode": fetch_mode,
            "allowed_domains": build_allowed_domains(row["优先入口URL"], row["主站URL"]),
            "title_must_not_contain": [],
            "url_must_not_contain": [],
            "title_must_contain_any": [],
        }
        config.update(CUSTOM_SOURCE_FILTERS.get(row["来源编号"], {}))
        configs.append(config)
    return configs


def fetch_text(url: str, timeout: int) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": urllib.parse.urlsplit(url).scheme + "://" + urllib.parse.urlsplit(url).netloc,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if not isinstance(reason, ssl.SSLCertVerificationError):
            data, content_type = fetch_bytes_with_curl(url, timeout)
        else:
            try:
                insecure_ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=timeout, context=insecure_ctx) as resp:
                    data = resp.read()
                    content_type = resp.headers.get("Content-Type", "")
            except Exception:
                data, content_type = fetch_bytes_with_curl(url, timeout)
    except Exception:
        data, content_type = fetch_bytes_with_curl(url, timeout)

    encodings = []
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.I)
    if match:
        encodings.append(match.group(1))
    encodings.extend(["utf-8", "gb18030", "gbk"])

    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="ignore")


def clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", "", value or "")
    return " ".join(html.unescape(without_tags).split())


def parse_html_links(base_url: str, html_text: str) -> list[dict[str, str]]:
    """从栏目 HTML 中抽取候选链接，并把相对链接转成绝对链接。"""
    parser = LinkParser()
    parser.feed(html_text)
    items: list[dict[str, str]] = []
    for title, href in parser.links:
        items.append(
            {
                "title": title,
                "url": urllib.parse.urljoin(base_url, href),
                "publish_date": "",
            }
        )
    return items


def strip_jsonp(text: str) -> str:
    raw = text.strip()
    if raw.startswith("{") or raw.startswith("["):
        return raw
    match = re.match(r"^[^(]+\((.*)\)\s*;?\s*$", raw, flags=re.S)
    if match:
        return match.group(1)
    return raw


def first_value(obj: dict, keys: list[str]) -> str:
    for key in keys:
        value = obj.get(key)
        if value:
            return str(value).strip()
    return ""


def walk_json(obj, base_url: str, items: list[dict[str, str]]) -> None:
    if isinstance(obj, dict):
        title = clean_text(first_value(obj, TITLE_KEYS))
        url = clean_text(first_value(obj, URL_KEYS))
        publish_date = clean_text(first_value(obj, DATE_KEYS))
        if title and url:
            items.append(
                {
                    "title": title,
                    "url": urllib.parse.urljoin(base_url, url),
                    "publish_date": publish_date,
                }
            )
        for value in obj.values():
            walk_json(value, base_url, items)
    elif isinstance(obj, list):
        for value in obj:
            walk_json(value, base_url, items)


def parse_json_links(base_url: str, raw_text: str) -> list[dict[str, str]]:
    payload = json.loads(strip_jsonp(raw_text))
    items: list[dict[str, str]] = []
    walk_json(payload, base_url, items)
    return items


def normalize_date(raw_text: str) -> str:
    if not raw_text:
        return ""
    match = re.search(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})", raw_text)
    if not match:
        return ""
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def extract_date(title: str, url: str, explicit_date: str = "") -> str:
    for text in (explicit_date, url, title):
        normalized = normalize_date(text)
        if normalized:
            return normalized
    return ""


def matched_keywords(title: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in title]


def domain_allowed(url: str, allowed_domains: set[str]) -> bool:
    if not allowed_domains:
        return True
    return canonical_domain(url) in allowed_domains


def is_candidate(item: dict[str, str], config: dict[str, str], keywords: list[str]) -> bool:
    """过滤导航链接、非政策链接和关键词无关链接，保留值得进入候选池的条目。"""
    title = item["title"].strip()
    url = item["url"].strip()
    if not title or len(title) < 6:
        return False
    if title in NAV_TITLES:
        return False
    if any(skip in title for skip in COMMON_TITLE_SKIP):
        return False
    if any(skip in title for skip in config.get("title_must_not_contain", [])):
        return False
    if any(skip in url for skip in config.get("url_must_not_contain", [])):
        return False
    if not domain_allowed(url, config.get("allowed_domains", set())):
        return False
    if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
        return False
    if url.rstrip("/") == config["list_url"].rstrip("/"):
        return False
    must_contain_any = config.get("title_must_contain_any", [])
    if must_contain_any and not any(token in title for token in must_contain_any):
        return False
    return bool(matched_keywords(title, keywords))


def deduplicate(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        key = row["原文链接"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def paginated_urls(list_url: str, fetch_mode: str, pages: int) -> list[str]:
    if pages <= 1:
        return [list_url]

    if "{page}" in list_url:
        return [list_url.replace("{page}", str(page)) for page in range(1, pages + 1)]

    parsed = urllib.parse.urlsplit(list_url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    page_keys = [key for key in ("pageNum", "page", "pageNo", "currentPage") if key in query]
    if page_keys:
        urls = []
        for page in range(1, pages + 1):
            page_query = dict(query)
            for key in page_keys:
                page_query[key] = [str(page)]
            urls.append(
                urllib.parse.urlunsplit(
                    parsed._replace(query=urllib.parse.urlencode(page_query, doseq=True))
                )
            )
        return list(dict.fromkeys(urls))

    if fetch_mode != "html":
        return [list_url]

    urls = [list_url]
    path = parsed.path

    for page_index in range(1, pages):
        if path.endswith("/"):
            page_path = f"{path}index_{page_index}.html"
        else:
            suffix = Path(path).suffix
            stem = path[: -len(suffix)] if suffix else path.rstrip("/")
            if path.endswith("index.html"):
                page_path = path.removesuffix("index.html") + f"index_{page_index}.html"
            elif suffix in {".html", ".htm", ".shtml"}:
                page_path = f"{stem}_{page_index}{suffix}"
            else:
                page_path = f"{path.rstrip('/')}/index_{page_index}.html"
        urls.append(urllib.parse.urlunsplit(parsed._replace(path=page_path)))

    # Preserve order while dropping duplicate URL patterns.
    return list(dict.fromkeys(urls))


def collect_candidates(
    keywords: list[str],
    timeout: int,
    source_file: Path,
    pages: int = 1,
    max_page_errors: int = 3,
) -> tuple[list[dict[str, str]], list[str], int]:
    today = f"{dt.date.today():%Y-%m-%d}"
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    configs = load_source_configs(source_file)

    for config in configs:
        page_urls = paginated_urls(config["list_url"], config["fetch_mode"], pages)
        source_had_success = False
        source_errors: list[str] = []
        consecutive_page_errors = 0

        for page_url in page_urls:
            try:
                raw_text = fetch_text(page_url, timeout=timeout)
                if config["fetch_mode"] == "json":
                    items = parse_json_links(page_url, raw_text)
                else:
                    items = parse_html_links(page_url, raw_text)
                source_had_success = True
                consecutive_page_errors = 0
            except Exception as exc:
                source_errors.append(f"{page_url} :: {exc}")
                consecutive_page_errors += 1
                if max_page_errors and source_had_success and consecutive_page_errors >= max_page_errors:
                    break
                continue

            page_config = dict(config)
            page_config["list_url"] = page_url

            for item in items:
                if not is_candidate(item, page_config, keywords):
                    continue
                hits = matched_keywords(item["title"], keywords)
                rows.append(
                    {
                        "抓取日期": today,
                        "来源编号": config["source_id"],
                        "来源层级": config["source_level"],
                        "地区": config["region"],
                        "来源栏目": config["source_name"],
                        "发布部门": config["department"],
                        "文件标题": item["title"].strip(),
                        "发布日期": extract_date(item["title"], item["url"], item.get("publish_date", "")),
                        "命中关键词": ";".join(hits),
                        "原文链接": item["url"].strip(),
                        "来源列表页": page_url,
                    }
                )

        if source_errors and not source_had_success:
            errors.append(f"{config['source_id']} {config['source_name']} :: {'；'.join(source_errors[:2])}")

    rows = deduplicate(rows)
    rows.sort(key=lambda row: (row["发布日期"], row["文件标题"]), reverse=True)
    return rows, errors, len(configs)


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def append_collection_log(
    output_path: Path,
    count: int,
    keywords: list[str],
    result: str,
    source_count: int,
    error_count: int,
    note: str = "",
) -> None:
    command = " ".join([str(Path(sys.executable)), *sys.argv])
    content = f"按来源清单抓取候选链接，共命中 {count} 条，扫描来源 {source_count} 个"
    files = str(output_path.relative_to(ROOT))
    extra = f"关键词：{'、'.join(keywords)}；失败来源 {error_count} 个"
    if note:
        extra = f"{extra}；{note}"
    append_log(
        action_type="来源清单采集",
        content=content,
        files=files,
        command=command,
        result=result,
        note=extra,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect candidate links from whitelisted sources.")
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="Keywords used to filter policy titles.",
    )
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds.")
    parser.add_argument("--limit", type=int, default=20, help="Print top N rows to stdout.")
    parser.add_argument("--pages", type=int, default=1, help="HTML list pages to try per source.")
    parser.add_argument("--max-page-errors", type=int, default=3, help="Stop one source after N consecutive page errors.")
    parser.add_argument("--source-file", default=str(SOURCE_CSV), help="Source whitelist csv path.")
    args = parser.parse_args()

    output_path = OUTPUT_DIR / f"来源清单候选链接_{dt.date.today():%Y%m%d}.csv"
    source_file = Path(args.source_file)

    try:
        rows, errors, source_count = collect_candidates(
            keywords=args.keywords,
            timeout=args.timeout,
            source_file=source_file,
            pages=args.pages,
            max_page_errors=args.max_page_errors,
        )
    except Exception as exc:
        append_collection_log(output_path, 0, args.keywords, result="失败", source_count=0, error_count=1, note=str(exc))
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    write_csv(rows, output_path)

    for row in rows[: args.limit]:
        print(
            f"{row['发布日期']} | {row['来源编号']} | {row['地区']} | "
            f"{row['来源栏目']} | {row['文件标题']}"
        )
        print(row["原文链接"])

    if errors:
        print("---- failed sources ----")
        for error in errors[:10]:
            print(error)

    print(f"Saved {len(rows)} rows to {output_path}")

    note = ""
    if errors:
        note = "；".join(errors[:5])
    append_collection_log(
        output_path,
        len(rows),
        args.keywords,
        result="完成" if not errors else "部分完成",
        source_count=source_count,
        error_count=len(errors),
        note=note,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
