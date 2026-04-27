from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import html
import io
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from log_action import append_log


ROOT = Path(__file__).resolve().parents[1]
SEED_CSV = ROOT / "02_元数据" / "待采集链接.csv"
LEDGER_CSV = ROOT / "02_元数据" / "政策资料台账.csv"
RAW_ROOT = ROOT / "01_原始资料"
TEXT_ROOT = ROOT / "03_处理后文本"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

LEDGER_FIELDS = [
    "资料编号",
    "文件标题",
    "发布部门",
    "采集来源机构",
    "发布日期",
    "文号",
    "适用地区",
    "来源类型",
    "政策层级",
    "市场主题",
    "关键词",
    "权威等级",
    "时间敏感类型",
    "有效状态",
    "是否原文",
    "原文链接",
    "本地文件路径",
    "关联官方文件",
    "摘要",
    "备注",
    "入库日期",
    "审核状态",
]

DOC_NO_PATTERNS = [
    re.compile(r"[一-龥A-Za-z]{1,18}〔20\d{2}〕\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}\[20\d{2}\]\s*\d+\s*号"),
    re.compile(r"[一-龥A-Za-z]{1,18}【20\d{2}】\s*\d+\s*号"),
    re.compile(r"[一-龥]{2,18}令第\s*\d+\s*号"),
    re.compile(r"第\s*\d+\s*号令"),
    re.compile(r"[一-龥A-Za-z]{1,18}\s*\(\s*20\d{2}\s*\)\s*\d+\s*号"),
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"\s+", " ", html.unescape(data)).strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def ensure_ledger() -> None:
    if LEDGER_CSV.exists() and LEDGER_CSV.stat().st_size > 0:
        return
    LEDGER_CSV.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS)
        writer.writeheader()


def append_ledger(row: dict[str, str]) -> None:
    ensure_ledger()
    with LEDGER_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        writer.writerow(row)


def existing_urls() -> set[str]:
    rows = read_csv(LEDGER_CSV)
    return {row.get("原文链接", "").strip() for row in rows if row.get("原文链接")}


def decode_bytes(data: bytes, content_type: str) -> str:
    candidates: list[str] = []
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.I)
    if match:
        candidates.append(match.group(1))
    candidates.extend(["utf-8", "gb18030"])
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="ignore")


def fetch(url: str, timeout: int) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "policy-knowledge-base-crawler/0.1 (+manual-seed)",
            "Accept": "text/html,application/pdf,application/msword,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if not isinstance(reason, ssl.SSLCertVerificationError):
            return fetch_with_curl(url, timeout)
        try:
            insecure_ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=insecure_ctx) as resp:
                return resp.read(), resp.headers.get("Content-Type", "")
        except Exception:
            return fetch_with_curl(url, timeout)
    except Exception:
        return fetch_with_curl(url, timeout)


def fetch_with_curl(url: str, timeout: int) -> tuple[bytes, str]:
    if not shutil.which("curl.exe"):
        raise OSError("curl.exe not found")
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
        raise OSError(f"curl fallback failed: {stderr or result.returncode}")
    return result.stdout, ""


def guess_extension(url: str, content_type: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    suffix = Path(path).suffix
    if suffix in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".html", ".htm"}:
        return suffix
    if "pdf" in content_type:
        return ".pdf"
    if "msword" in content_type:
        return ".doc"
    if "officedocument" in content_type and "word" in content_type:
        return ".docx"
    return ".html"


def sanitize_filename(text: str, fallback: str) -> str:
    text = text.strip() or fallback
    text = re.sub(r"[\\/:*?\"<>|;；]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:120].strip("._") or fallback


def extract_title(raw_html: str, fallback: str) -> str:
    patterns = [
        r"<meta[^>]+name=[\"']ArticleTitle[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']ArticleTitle[\"']",
        r"<h1[^>]*>(.*?)</h1>",
        r"<title[^>]*>(.*?)</title>",
        r"公开事项名称[:：]\s*([^<\n]+)",
    ]
    generic_titles = {
        "山西省能源局",
        "云南省能源局",
        "国家能源局江苏监管办公室",
        "国家能源局浙江监管办公室",
        "国家能源局四川监管办公室",
        "国家能源局福建监管办公室",
        "国家能源局新疆监管办公室",
    }
    for pattern in patterns:
        match = re.search(pattern, raw_html, flags=re.I | re.S)
        if match:
            title = re.sub(r"<[^>]+>", "", match.group(1))
            title = html.unescape(re.sub(r"\s+", " ", title)).strip()
            if title and title not in generic_titles:
                return title
    return fallback


def extract_publish_date(raw_html: str, seed_row: dict[str, str]) -> str:
    for value in seed_row.values():
        if value:
            match = re.search(r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})", value)
            if match:
                return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    text = re.sub(r"<[^>]+>", " ", raw_html)
    patterns = [
        r"制发日期[:：]?\s*(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
        r"发布日期[:：]?\s*(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
        r"(20\d{2})[-年./](\d{1,2})[-月./](\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


def source_folder(source_type: str) -> Path:
    if "交易" in source_type:
        return RAW_ROOT / "交易规则"
    if "咨询" in source_type or "报告" in source_type:
        return RAW_ROOT / "咨询报告"
    if "公众号" in source_type or "解读" in source_type or "新闻" in source_type:
        return RAW_ROOT / "解读资料"
    return RAW_ROOT / "官方政策"


def normalize_source_org(value: str) -> str:
    value = (value or "").strip()
    column_suffixes = {
        "通知公告",
        "通知公示",
        "交易规则",
        "政策文件",
        "规范性文件",
        "行政规范性文件",
        "政策法规",
        "委局文件",
        "本委其他文件",
        "部门文件",
        "政府信息公开",
        "价格管理",
    }
    for delimiter in ["-", "－", "—"]:
        if delimiter in value:
            left, right = value.rsplit(delimiter, 1)
            if right.strip() in column_suffixes:
                return left.strip()
    return value


def clean_doc_no(value: str) -> str:
    value = re.sub(r"\s+", "", value or "")
    value = value.replace("[", "〔").replace("]", "〕").replace("【", "〔").replace("】", "〕")
    value = re.sub(r"^(发文字号|文号|文件编号)[:：]?", "", value)
    value = re.sub(r"^[年月日]+(?=国家|国务院|中华人民共和国)", "", value)
    return value.strip("()（）[]【】《》,，;；。")


def extract_doc_number(title: str, text: str) -> str:
    candidates = [title]
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines[:80]):
        if len(line) > 180:
            continue
        if not any(pattern.search(line) for pattern in DOC_NO_PATTERNS):
            continue
        if index <= 30 or any(key in line for key in ["发文字号", "文号", "文件编号", "令第", "公布"]):
            candidates.append(line)

    for block in candidates:
        for pattern in DOC_NO_PATTERNS:
            for match in pattern.findall(block):
                doc_no = clean_doc_no(match)
                if doc_no:
                    return doc_no
    return ""


def make_id(url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10].upper()
    return f"AUTO-{dt.date.today():%Y%m%d}-{digest}"


def text_from_html(raw_html: str) -> str:
    parser = TextExtractor()
    parser.feed(raw_html)
    lines = [line.strip() for line in parser.text().splitlines() if line.strip()]
    compact: list[str] = []
    for line in lines:
        if not compact or compact[-1] != line:
            compact.append(line)
    return "\n".join(compact)


def text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return ""

    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def collect_one(seed_row: dict[str, str], timeout: int, dry_run: bool) -> dict[str, str]:
    url = seed_row["url"].strip()
    if not url:
        raise ValueError("empty url")

    data, content_type = fetch(url, timeout=timeout)
    ext = guess_extension(url, content_type)
    fallback = Path(urllib.parse.urlparse(url).path).stem or "policy_document"

    raw_html = ""
    text = ""
    if ext in {".html", ".htm"}:
        raw_html = decode_bytes(data, content_type)
        title = extract_title(raw_html, seed_row.get("备注", "").strip() or fallback)
        publish_date = extract_publish_date(raw_html, seed_row)
        text = text_from_html(raw_html)
    elif ext == ".pdf":
        title = seed_row.get("备注", "").strip() or fallback
        publish_date = extract_publish_date("", seed_row)
        text = text_from_pdf(data)
    else:
        title = seed_row.get("备注", "").strip() or fallback
        publish_date = extract_publish_date("", seed_row)
    document_number = extract_doc_number(title, text)

    file_stem = sanitize_filename(
        "_".join(
            part
            for part in [
                publish_date,
                seed_row.get("适用地区", ""),
                seed_row.get("发布部门", ""),
                title,
            ]
            if part
        ),
        fallback,
    )

    raw_dir = source_folder(seed_row.get("来源类型", ""))
    raw_path = raw_dir / f"{file_stem}{ext}"
    text_path = TEXT_ROOT / f"{file_stem}.txt"

    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)
        TEXT_ROOT.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(data)
        if text:
            text_path.write_text(text, encoding="utf-8")

    local_path = str(raw_path.relative_to(ROOT))
    if text:
        local_path = f"{local_path}; {text_path.relative_to(ROOT)}"

    return {
        "资料编号": make_id(url),
        "文件标题": title,
        "发布部门": seed_row.get("发布部门", ""),
        "采集来源机构": normalize_source_org(seed_row.get("来源名称", "") or seed_row.get("发布部门", "")),
        "发布日期": publish_date,
        "文号": document_number,
        "适用地区": seed_row.get("适用地区", ""),
        "来源类型": seed_row.get("来源类型", ""),
        "政策层级": "国家级" if "国家" in seed_row.get("发布部门", "") else "",
        "市场主题": seed_row.get("市场主题", ""),
        "关键词": seed_row.get("关键词", ""),
        "权威等级": seed_row.get("权威等级", ""),
        "时间敏感类型": seed_row.get("时间敏感类型", ""),
        "有效状态": seed_row.get("有效状态", "状态未知"),
        "是否原文": seed_row.get("是否原文", ""),
        "原文链接": url,
        "本地文件路径": local_path,
        "关联官方文件": "",
        "摘要": "",
        "备注": seed_row.get("备注", ""),
        "入库日期": f"{dt.date.today():%Y-%m-%d}",
        "审核状态": "待人工复核",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch manually curated policy URLs.")
    parser.add_argument("--seed", default=str(SEED_CSV), help="Seed URL csv path")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to fetch; 0 means all")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay seconds between requests")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="Fetch only, do not write files")
    parser.add_argument("--force", action="store_true", help="Fetch URLs already in ledger")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    rows = read_csv(seed_path)
    if not rows:
        print(f"No seed rows found: {seed_path}", file=sys.stderr)
        append_fetch_log(args, fetched=0, failed=0, result="失败", note=f"No seed rows found: {seed_path}")
        return 1

    seen = existing_urls()
    fetched = 0
    failed = 0

    for row in rows:
        url = row.get("url", "").strip()
        if not url:
            continue
        if url in seen and not args.force:
            print(f"SKIP existing: {url}")
            continue
        if args.limit and fetched >= args.limit:
            break

        try:
            result = collect_one(row, timeout=args.timeout, dry_run=args.dry_run)
            if not args.dry_run:
                append_ledger(result)
            fetched += 1
            print(f"OK {result['资料编号']} {result['文件标题']}")
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            failed += 1
            print(f"FAIL {url} :: {exc}", file=sys.stderr)

        if args.delay and not args.dry_run:
            time.sleep(args.delay)

    exit_code = 0 if failed == 0 else 2
    result_text = "成功" if exit_code == 0 else "部分失败"
    print(f"Done. fetched={fetched}, failed={failed}, dry_run={args.dry_run}")
    append_fetch_log(args, fetched=fetched, failed=failed, result=result_text)
    return exit_code


def append_fetch_log(args: argparse.Namespace, fetched: int, failed: int, result: str, note: str = "") -> None:
    mode = "采集测试" if args.dry_run else "数据采集"
    command = " ".join([str(Path(sys.executable)), *sys.argv])
    files = "02_元数据/待采集链接.csv; 02_元数据/政策资料台账.csv; 01_原始资料; 03_处理后文本"
    content = f"运行种子链接采集脚本，成功{fetched}条，失败{failed}条"
    if args.dry_run:
        note = (note + "；" if note else "") + "dry-run模式，未写入原始资料和台账"
    try:
        append_log(
            action_type=mode,
            content=content,
            files=files,
            command=command,
            result=result,
            note=note,
        )
    except OSError as exc:
        print(f"WARN failed to append operation log: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
